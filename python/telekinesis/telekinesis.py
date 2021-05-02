import sys
import io
import time
import asyncio
import inspect
import traceback
import logging
from functools import partialmethod

import re
import makefun

from .client import Route, Channel


class State:
    def __init__(self, attributes=None, methods=None, repr=None, doc=None, pipeline=None, last_change=None):
        self.attributes = attributes or {}
        self.methods = methods or {}
        self.pipeline = pipeline or []
        self.repr = repr or ""
        self.doc = doc
        self.last_change = last_change

    def to_dict(self, mask=None):
        mask = mask or set()
        return {
            "attributes": {k: v for k, v in self.attributes.items() if k not in mask}
            if isinstance(self.attributes, dict)
            else [k for k in self.attributes if k not in mask],
            "methods": {k: v for k, v in self.methods.items() if k not in mask},
            "pipeline": self.pipeline,
            "repr": self.repr,
            "doc": self.doc,
            "last_change": self.last_change,
        }

    def clone(self):
        return State(**self.to_dict())

    @staticmethod
    def from_object(target, cache_attributes):
        logger = logging.getLogger(__name__)

        attributes, methods, repr_ = {} if cache_attributes else [], {}, ""

        for attribute_name in dir(target):
            if attribute_name[0] != "_" or attribute_name in [
                "__call__",
                "__init__",
                "__getitem__",
                "__setitem__",
                "__add__",
                "__mul__",
            ]:
                try:
                    if isinstance(target, type):
                        target_attribute = target.__getattribute__(target, attribute_name)
                    else:
                        target_attribute = target.__getattribute__(attribute_name)

                    if "__call__" in dir(target_attribute):
                        if attribute_name in ["__call__", "__init__"]:
                            target_attribute = target
                        signature = None
                        try:
                            signature = str(inspect.signature(target_attribute))

                            if "_tk_inject_first_arg" in dir(target_attribute) and target_attribute._tk_inject_first_arg:
                                signature = (
                                    re.sub(r"[a-zA-Z0-9=\_\s]+(?=[\)\,])", "", signature, 1)
                                    .replace("(,", "(", 1)
                                    .replace("( ", "(", 1)
                                )
                        except Exception:
                            logger.debug("Could not obtain signature from %s.%s", target, attribute_name)

                        if attribute_name == "__init__":
                            methods["__call__"] = (signature, target.__init__.__doc__)
                        else:
                            methods[attribute_name] = (signature, target_attribute.__doc__)
                    else:
                        if cache_attributes:
                            attributes[attribute_name] = target_attribute
                        else:
                            attributes.append(attribute_name)

                except Exception as e:
                    logger.error("Could not obtain handle for %s.%s: %s", target, attribute_name, e)

        if isinstance(target, type):
            repr_ = str(type(target))
            doc = target.__doc__
        else:
            doc = None
            repr_ = target.__repr__()

        return State(attributes, methods, repr_, doc, None, time.time())


class Telekinesis:
    def __init__(
        self,
        target,
        session,
        mask=None,
        expose_tb=True,
        max_delegation_depth=None,
        compile_signatures=True,
        parent=None,
        cache_attributes=False,
    ):

        self._logger = logging.getLogger(__name__)
        self._target = target
        self._session = session
        self._mask = mask or []
        self._expose_tb = expose_tb
        self._max_delegation_depth = max_delegation_depth
        self._compile_signatures = compile_signatures
        self._parent = parent
        self._cache_attributes = cache_attributes
        self._channel = None
        self._on_update_callback = None
        self._subscription = None
        self._subscribers = set()
        self._state = None

        if isinstance(target, Route):
            self._state = State()
        else:
            session.targets[id(target)] = (session.targets.get(id(target)) or set()).union(set((self,)))
            self._update_state(State.from_object(target, cache_attributes))

    def __getattribute__(self, attr):
        if attr[0] == "_":
            return super().__getattribute__(attr)

        state = self._state.clone()
        state.pipeline.append(("get", attr))

        return Telekinesis._from_state(
            state,
            self._target,
            self._session,
            self._mask,
            self._expose_tb,
            self._max_delegation_depth,
            self._compile_signatures,
            self,
            self._cache_attributes,
        )

    def _get_root_state(self):
        if self._parent:
            return self._parent._get_root_state()
        return self._state

    def _last(self):
        if (
            (len(self._state.pipeline) == 1)
            and (self._state.pipeline[0][0] == "get")
            and (isinstance(self._state.attributes, dict))
        ):
            return self._state.attributes[self._state.pipeline[0][1]]

    def _update_state(self, state):
        if not self._state or not self._state.last_change or (state.last_change > self._state.last_change):
            for d in dir(self):
                if d[0] != "_":
                    self.__delattr__(d)

            for method_name in state.methods:
                if method_name[0] != "_":
                    super().__setattr__(method_name, None)

            for attribute_name in state.attributes:
                if attribute_name[0] != "_":
                    super().__setattr__(attribute_name, None)

            self._state = state

        return self

    def _delegate(self, receiver_id, parent_channel=None):
        token_header = []
        extend_route = True

        if isinstance(self._target, Route):
            route = self._target.clone()
            max_delegation_depth = None
            if receiver_id == "*":
                raise Exception("Cannot delegate remote Channel to public.")

        else:
            if not self._channel:
                self._channel = Channel(self._session, is_public=receiver_id == "*")
                self._channel.telekinesis = self
                self._channel.listen()
                token_header += [self._channel.header_buffer.pop()]
            if self._channel.is_public:
                extend_route = False

            max_delegation_depth = self._max_delegation_depth
            route = self._channel.route
            if receiver_id == "*" and not self._channel.is_public:
                self._channel.is_public = True
                self._channel.listen()
                token_header += [self._channel.header_buffer.pop()]

        if extend_route:
            token_header += [self._session.extend_route(route, receiver_id, max_delegation_depth)]

        route._parent_channel = parent_channel or self._channel
        [route._parent_channel.header_buffer.append(th) for th in token_header]

        return route

    def _subscribe(self, callback=None):
        self._on_update_callback = callback
        self._subscription = Telekinesis(
            lambda s: self._update_state(State(**s)) and self._on_update_callback and self._on_update_callback(self),
            self._session,
            None,
            self._expose_tb,
            self._max_delegation_depth,
            self._compile_signatures,
        )
        self._state.pipeline.append(("subscribe", self._subscription))

        return self

    async def _handle_request(self, channel, metadata, payload):
        pipeline = None
        reply_to = None
        try:
            if "close" in payload:
                # await channel.close()
                pass
            elif "ping" in payload:
                await channel.channel.send(metadata.caller, {"repr": self._state.repr, "timestamp": self._state.last_change})
            elif "pipeline" in payload:
                pipeline = self._decode(payload.get("pipeline"), metadata.caller.session)
                self._logger.info("%s called %s", metadata.caller.session, len(pipeline))
                if payload.get("reply_to"):
                    reply_to = Route(**payload["reply_to"])
                    reply_to.validate_token_chain(self._session.session_key.public_serial())

                ret = await self._execute(metadata, pipeline)

                if (
                    isinstance(ret, Telekinesis)
                    and isinstance(ret._target, Route)
                    and ret._state.pipeline
                    and (
                        ret._target.session != self._session.session_key.public_serial()
                        or ret._target._channel not in self._session.channels
                    )
                ):
                    await ret._forward(ret._state.pipeline, reply_to or metadata.caller)
                else:
                    if reply_to:
                        async with Channel(self._session) as new_channel:
                            await new_channel.send(
                                reply_to,
                                {
                                    "return": self._encode(ret, reply_to.session, new_channel),
                                    "repr": self._state.repr,
                                    "timestamp": self._state.last_change,
                                },
                            )
                    else:
                        await channel.send(
                            metadata.caller,
                            {
                                "return": self._encode(ret, metadata.caller.session),
                                "repr": self._state.repr,
                                "timestamp": self._state.last_change,
                            },
                        )

        except Exception:
            if pipeline is None:
                self._logger.error("Telekinesis request error with payload %s", payload, exc_info=True)
            else:
                self._logger.error("Telekinesis request error with pipeline %s", pipeline, exc_info=True)

            self._state.pipeline.clear()
            try:
                err_message = {"error": traceback.format_exc() if self._expose_tb else ""}
                if reply_to:
                    async with Channel(self._session) as new_channel:
                        await new_channel.send(reply_to, err_message)
                else:
                    await channel.send(metadata.caller, err_message)
            finally:
                pass

    async def _execute(self, metadata=None, pipeline=None):
        if asyncio.iscoroutine(self._target):
            old_id = id(self._target)
            self._target = await self._target
            self._session.targets[old_id].remove(self)
            if not self._session.targets[old_id]:
                self._session.targets.pop(old_id)
            if not isinstance(self._target, Route):
                self._session.targets[id(self._target)] = (self._session.targets.get(id(self._target)) or set()).union(
                    set((self,))
                )

        if not pipeline:
            pipeline = []

        pipeline = self._state.pipeline + pipeline
        self._state.pipeline.clear()

        if isinstance(self._target, Route):
            return await self._forward(pipeline)

        async def exc(x):
            if isinstance(x, Telekinesis) and x._state.pipeline:
                return await x._execute(metadata)
            return x

        target = self._target

        touched = self._session.targets.get(id(target))
        for i, (action, arg) in enumerate(pipeline):
            if (
                isinstance(target, Telekinesis)
                and isinstance(target._target, Route)
                and (
                    target._target.session != self._session.session_key.public_serial()
                    or target._target.channel not in self._session.channels
                )
            ):
                target._state.pipeline += pipeline[i:]
                break

            touched = touched.union((self._session.targets.get(id(target))) or set())

            if action == "get":
                self._logger.info("%s %s %s", action, arg, target)
                if (
                    (arg[0] == "_" and arg not in ["__getitem__", "__setitem__", "__add__", "__mul__"])
                    or arg in (self._mask or [])
                    or (type(target) == dict)
                ):
                    raise Exception("Unauthorized!")
                target = target.__getattribute__(arg)
            if action == "call":
                self._logger.info("%s %s", action, target)
                ar, kw = arg
                args, kwargs = [await exc(x) for x in ar], {x: await exc(kw[x]) for x in kw}

                if "_tk_inject_first_arg" in dir(target) and target._tk_inject_first_arg:
                    target = target(metadata, *args, **kwargs)
                else:
                    target = target(*args, **kwargs)
                if asyncio.iscoroutine(target):
                    target = await target
            if action == "subscribe":
                tk = Telekinesis._reuse(
                    target,
                    self._session,
                    self._mask,
                    self._expose_tb,
                    self._max_delegation_depth,
                    self._compile_signatures,
                    None,
                    self._cache_attributes,
                )
                tk._subscribers.add(arg)

        for tk in touched:
            tk._update_state(State.from_object(tk._target, tk._cache_attributes))
            if tk and tk._subscribers:
                state_obj = State.from_object(tk._target, True).to_dict(tk._mask)
                for s in tk._subscribers:
                    asyncio.create_task(s(state_obj)._execute())

        return target

    async def _timeout(self, seconds):
        return await asyncio.wait_for(self._execute(), seconds)

    async def _send_request(self, channel, **kwargs):
        response = {}
        await channel.send(self._target, kwargs)

        if not kwargs.get("reply_to"):
            _, response = await channel.recv()

            state = self._get_root_state()
            if "repr" in response and ((response.get("timestamp") or 1e99) >= (state.last_change or 0)):
                state.last_change = response.get("timestamp") or time.time()
                state.repr = response["repr"]

            if "repr" in response:
                state = self._get_root_state()

            if "error" in response:
                raise Exception(response["error"])

            if "return" in response:
                ret = self._decode(response["return"], self._target.session)
                return ret

            if "repr" not in response:
                raise Exception("Telekinesis communication error: received unrecognized message schema %s" % response)

    async def _close(self):
        try:
            if isinstance(self._target, Route):
                async with Channel(self._session) as new_channel:
                    await new_channel.send(self._target, {"close": True})
            else:
                self._channel and await self._channel.close()
        except Exception:
            self._session.logger("Error closing Telekinesis Object: %s", self._target, exc_info=True)

    async def _forward(self, pipeline, reply_to=None):
        async with Channel(self._session) as new_channel:
            if reply_to:
                token_header = self._session.extend_route(reply_to, self._target.session)
                new_channel.header_buffer.append(token_header)
            return await self._send_request(
                new_channel,
                reply_to=reply_to and reply_to.to_dict(),
                pipeline=self._encode(pipeline, self._target.session, new_channel),
            )

    def __call__(self, *args, **kwargs):
        state = self._state.clone()
        state.pipeline.append(("call", (args, kwargs)))

        return Telekinesis._from_state(
            state,
            self._target,
            self._session,
            self._mask,
            self._expose_tb,
            self._max_delegation_depth,
            self._compile_signatures,
            self,
            self._cache_attributes,
        )

    def __setattr__(self, attribute, value):
        if attribute[0] != "_":
            raise Exception("Attributes for Telekinesis objects cannot be set directy")
        super().__setattr__(attribute, value)

    def __await__(self):
        return self._execute().__await__()

    def __repr__(self):
        return "\033[92m\u2248\033[0m " + str(self._state.repr)

    def __del__(self):
        if self._parent is None:
            self._session.pending_tasks.add(asyncio.get_event_loop().create_task(self._close()))

    def _encode(self, target, receiver_id=None, channel=None, traversal_stack=None, block_recursion=False):
        if traversal_stack is None:
            i = 0
            traversal_stack = {}
        else:
            if id(target) in traversal_stack:
                return str(traversal_stack[id(target)][0])
            i = len(traversal_stack)

        traversal_stack[id(target)] = [i, None, target]
        # ..  keep a copy of the target so it doesn't get garbage collected - which messes up the id()

        if receiver_id is None:
            receiver_id = self._session.session_key.public_serial()

        if type(target) in (int, float, str, bytes, bool, type(None)):
            tup = (type(target).__name__, target)
        elif type(target) in (range, slice):
            tup = (type(target).__name__, (target.start, target.stop, target.step))
        elif type(target) in (list, tuple, set):
            tup = (
                type(target).__name__,
                [self._encode(v, receiver_id, channel, traversal_stack, block_recursion) for v in target],
            )
        elif type(target) == dict:
            tup = (
                "dict",
                {x: self._encode(target[x], receiver_id, channel, traversal_stack, block_recursion) for x in target},
            )
        elif isinstance(target, Route):
            tup = ("route", target.to_dict())
        else:
            if isinstance(target, Telekinesis):
                obj = target
            else:
                obj = Telekinesis._reuse(
                    target,
                    self._session,
                    self._mask,
                    self._expose_tb,
                    self._max_delegation_depth,
                    self._compile_signatures,
                    cache_attributes=not block_recursion and self._cache_attributes,
                )

            route = obj._delegate(receiver_id, channel or self._channel)
            tup = (
                "obj",
                (
                    route.to_dict(),
                    self._encode(obj._state.to_dict(self._mask), receiver_id, channel, traversal_stack, block_recursion=True),
                ),
            )

        traversal_stack[id(target)] = [i, tup, target]

        if i == 0:
            return {str(v[0]): v[1] for v in traversal_stack.values()}
        return str(i)

    def _decode(self, input_stack, caller_id=None, root=None, output_stack=None):
        out = None
        if root is None:
            root = "0"
            output_stack = {}
        if root in output_stack:
            return output_stack[root]

        typ, obj = input_stack[root]
        if typ in ("int", "float", "str", "bytes", "bool", "NoneType"):
            out = obj
        elif typ in ("range", "slice"):
            out = {"range": range, "slice": slice}[typ](*obj)
        elif typ == "list":
            out = [None] * len(obj)
            output_stack[root] = out
            for k, v in enumerate(obj):
                out[k] = self._decode(input_stack, caller_id, v, output_stack)
        elif typ == "set":
            out = set()
            output_stack[root] = out
            for v in obj:
                out.add(self._decode(input_stack, caller_id, v, output_stack))
        elif typ == "tuple":
            out = tuple(self._decode(input_stack, caller_id, v, output_stack) for v in obj)
        elif typ == "dict":
            out = {}
            output_stack[root] = out
            for k, v in obj.items():
                out[k] = self._decode(input_stack, caller_id, v, output_stack)
        elif typ == "route":
            out = Route(**obj)
        else:
            route = Route(**obj[0])
            state = State(**self._decode(input_stack, caller_id, obj[1], output_stack))

            if route.session == self._session.session_key.public_serial() and route.channel in self._session.channels:
                channel = self._session.channels.get(route.channel)
                if channel.validate_token_chain(caller_id, route.tokens):
                    if state.pipeline:
                        return Telekinesis._from_state(
                            state,
                            channel.telekinesis._target,
                            self._session,
                            self._mask,
                            self._expose_tb,
                            self._max_delegation_depth,
                            self._compile_signatures,
                            channel.telekinesis,
                            self._cache_attributes,
                        )
                    out = channel.telekinesis._target
                else:
                    raise Exception(f"Unauthorized! {caller_id} {route.tokens}")
            elif self._parent and (
                ("__call__" not in self._state.methods) or (self._state.methods["__call__"] == state.methods.get("__call__"))
            ):
                self._target = route
                self._update_state(state)
                out = self
            else:
                out = Telekinesis._from_state(
                    state,
                    route,
                    self._session,
                    self._mask,
                    self._expose_tb,
                    self._max_delegation_depth,
                    self._compile_signatures,
                    cache_attributes=self._cache_attributes,
                )

        output_stack[root] = out
        return out

    @staticmethod
    def _from_state(
        state,
        target,
        session,
        mask=None,
        expose_tb=True,
        max_delegation_depth=None,
        compile_signatures=True,
        parent=None,
        cache_attributes=False,
    ):
        def signatured_subclass(signature, method_name, docstring):
            class Telekinesis_(Telekinesis):
                @makefun.with_signature(
                    signature,
                    func_name=method_name,
                    doc=docstring if method_name == "__call__" else None,
                    module_name="telekinesis.telekinesis",
                )
                def __call__(self, *args, **kwargs):
                    return Telekinesis.__call__(self, *args, **kwargs)

            return Telekinesis_

        method_name = state.pipeline[-1][1] if state.pipeline and state.pipeline[-1][0] == "get" else "__call__"

        reset = "call" in [x[0] for x in state.pipeline[:-1]]
        if method_name in state.methods or reset:
            signature, docstring = (not reset and state.methods.get(method_name)) or (None, None)
            signature = signature or "(*args, **kwargs)"
            signature = signature.replace("(", "(self, ")

            stderr = sys.stderr
            sys.stderr = io.StringIO()

            try:
                if compile_signatures and check_signature(signature):
                    Telekinesis_ = signatured_subclass(signature, method_name, docstring)
                else:
                    Telekinesis_ = signatured_subclass("(self, *args, **kwargs)", method_name, docstring)
            except Exception:
                Telekinesis_ = signatured_subclass("(self, *args, **kwargs)", method_name, docstring)

            sys.stderr = stderr

        else:
            Telekinesis_ = Telekinesis
            docstring = None

        if method_name == "__call__":

            def dundermethod(self, method, key):
                state = self._state.clone()
                state.pipeline.append(("get", method))
                state.pipeline.append(("call", ((key,), {})))
                return Telekinesis._from_state(
                    state,
                    self._target,
                    self._session,
                    self._mask,
                    self._expose_tb,
                    self._max_delegation_depth,
                    self._compile_signatures,
                    self,
                    self._cache_attributes,
                )

            def setitem(self, key, value):
                state = self._state
                state.pipeline.append(("get", "__setitem__"))
                state.pipeline.append(("call", ((key, value), {})))
                return Telekinesis._from_state(
                    state,
                    self._target,
                    self._session,
                    self._mask,
                    self._expose_tb,
                    self._max_delegation_depth,
                    self._compile_signatures,
                    self,
                    self._cache_attributes,
                )

            if "__getitem__" in state.methods:
                Telekinesis_.__getitem__ = partialmethod(dundermethod, "__getitem__")
            if "__add__" in state.methods:
                Telekinesis_.__add__ = partialmethod(dundermethod, "__add__")
            if "__mul__" in state.methods:
                Telekinesis_.__mul__ = partialmethod(dundermethod, "__mul__")
            if "__setitem__" in state.methods:
                Telekinesis_.__setitem__ = setitem

        out = Telekinesis_(
            target, session, mask, expose_tb, max_delegation_depth, compile_signatures, parent, cache_attributes
        )
        out._update_state(state)
        out.__doc__ = state.doc if method_name == "__call__" else docstring

        return out

    @staticmethod
    def _reuse(
        target,
        session,
        mask=None,
        expose_tb=True,
        max_delegation_depth=None,
        compile_signatures=True,
        parent=None,
        cache_attributes=False,
    ):
        kwargs = locals()

        for tk in session.targets.get(id(target)) or []:
            if all((kwargs[x] == tk.__getattribute__("_" + x) for x in kwargs)):
                return tk
        return Telekinesis(**kwargs)


def check_signature(signature):
    return not ("\n" in signature or (signature != re.sub(r"(?:[^A-Za-z0-9_])lambda(?=[\)\s\:])", "", signature)))


def inject_first_arg(func):
    func._tk_inject_first_arg = True
    return func
