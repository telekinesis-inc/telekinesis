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
        self.attributes = attributes or []
        self.methods = methods or {}
        self.pipeline = pipeline or []
        self.repr = repr or ""
        self.doc = doc
        self.last_change = last_change

    def to_dict(self, mask=None):
        mask = mask or set()
        return {
            "attributes": [x for x in self.attributes if x not in mask],
            "methods": {k: v for k, v in self.methods.items() if k not in mask},
            "pipeline": self.pipeline,
            "repr": self.repr,
            "doc": self.doc,
            "last_change": self.last_change,
        }

    def clone(self):
        return State(**self.to_dict())

    @staticmethod
    def from_object(target):
        logger = logging.getLogger(__name__)

        attributes, methods, repr_ = [], {}, ""

        for attribute_name in dir(target):
            if attribute_name[0] != "_" or attribute_name in [
                "__call__",
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
                        if attribute_name == "__call__":
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

                        methods[attribute_name] = (signature, target_attribute.__doc__)
                    else:
                        attributes.append(attribute_name)

                except Exception as e:
                    logger.error("Could not obtain handle for %s.%s: %s", target, attribute_name, e)

        if isinstance(target, type):
            repr_ = str(type(target))
            methods["__call__"] = (str(inspect.signature(target)), target.__init__.__doc__)
            doc = target.__doc__
        else:
            doc = None
            repr_ = target.__repr__()

        return State(attributes, methods, repr_, doc, None, time.time())


class Listener:
    def __init__(self, channel):
        self.channel = channel
        self.coro_callback = None
        self.listen_task = None
        self.current_tasks = set()

    def set_callback(self, coro_callback):
        self.coro_callback = coro_callback
        self.listen_task = asyncio.get_event_loop().create_task(self.listen())
        return self

    async def listen(self):
        while True:
            try:
                await self.channel.listen()
                while True:
                    message = await self.channel.recv()

                    self.current_tasks.add(
                        asyncio.get_event_loop().create_task(self.coro_callback(self, *message))
                    )

                    await asyncio.gather(*(x for x in self.current_tasks if x.done()))
                    self.current_tasks = set(x for x in self.current_tasks if not x.done())
            except Exception:
                logging.getLogger(__name__).error("Listener error", exc_info=True)

    async def close(self, close_public=False):
        if close_public or not self.channel.is_public:
            self.listen_task and self.listen_task.cancel()
            await asyncio.gather(*self.current_tasks)
            await self.channel.close()


class Telekinesis:
    def __init__(
        self, target, session, mask=None, expose_tb=True, max_delegation_depth=None, compile_signatures=True, parent=None,
    ):

        self._logger = logging.getLogger(__name__)
        self._target = target
        self._session = session
        self._mask = mask or []
        self._expose_tb = expose_tb
        self._max_delegation_depth = max_delegation_depth
        self._compile_signatures = compile_signatures
        self._parent = parent
        self._listeners = {}
        if isinstance(target, Route):
            self._state = State()
        else:
            self._update_state(State.from_object(target))

    def __getattribute__(self, attr):
        if attr[0] == "_":  # and (attr != '__call__'):
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
        )

    def _get_root_state(self):
        if self._parent:
            return self._parent._get_root_state()
        return self._state

    def _update_state(self, state):
        for d in dir(self):
            if d[0] != "_":
                self.__delattr__(d)

        for method_name in state.methods:
            if method_name[0] != "_":
                self.__setattr__(method_name, None)

        for attribute_name in state.attributes:
            if attribute_name[0] != "_":
                self.__setattr__(attribute_name, None)

        self._state = state

        return self

    def _add_listener(self, channel):
        route = channel.route

        channel.telekinesis = self

        self._listeners[route] = Listener(channel).set_callback(self._handle_request)

        return route

    def _delegate(self, receiver_id, parent_channel=None):
        if isinstance(self._target, Route):
            route = self._target.clone()
            max_delegation_depth = None

        else:
            route = self._add_listener(Channel(self._session))
            listener = self._listeners[route]
            max_delegation_depth = self._max_delegation_depth

            if not parent_channel:
                parent_channel = listener.channel

        token_header = self._session.extend_route(route, receiver_id, max_delegation_depth)
        parent_channel.header_buffer.append(token_header)

        return route

    async def _handle_request(self, listener, reply, payload):
        try:
            if "close" in payload:
                await listener.close()
            elif "ping" in payload:
                await listener.channel.send(reply, {"repr": self._state.repr, "timestamp": self._state.last_change})
            elif "pipeline" in payload:
                pipeline = self._decode(payload.get("pipeline"), reply.session)
                self._logger.info("%s called %s", reply.session[:4], len(pipeline))
                ret = await self._execute(listener, reply, pipeline)

                await listener.channel.send(reply, {
                    "return": self._encode(ret, reply.session, listener),
                    "repr": self._state.repr,
                    "timestamp": self._state.last_change})

        except Exception:
            self._logger.error("Telekinesis request error with payload %s", payload, exc_info=True)

            self._state.pipeline.clear()
            try:
                await listener.channel.send(reply, {"error": traceback.format_exc() if self._expose_tb else ""})
            finally:
                pass

    def _call(self, *args, **kwargs):
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
        )

    async def _execute(self, listener=None, reply=None, pipeline=None):
        if not pipeline:
            pipeline = []

        pipeline = self._state.pipeline + pipeline
        self._state.pipeline.clear()

        if isinstance(self._target, Route):
            async with Channel(self._session) as new_channel:
                return await self._send_request(
                    new_channel,
                    pipeline=self._encode(pipeline, self._target.session, Listener(new_channel))
                )

        async def exc(x):
            if isinstance(x, Telekinesis) and x._state.pipeline:
                return await x._execute(listener, reply)
            return x

        target = self._target
        for action, arg in pipeline:
            if action == "get":
                self._logger.info("%s %s %s", action, arg, target)
                if (arg[0] == "_" and arg not in ["__getitem__", "__setitem__", "__add__", "__mul__"]) or arg in (
                    self._mask or []
                ):
                    raise Exception("Unauthorized!")
                target = target.__getattribute__(arg)
            if action == "call":
                self._logger.info("%s %s", action, target)
                ar, kw = arg
                args, kwargs = [await exc(x) for x in ar], {x: await exc(kw[x]) for x in kw}

                if "_tk_inject_first_arg" in dir(target) and target._tk_inject_first_arg:
                    target = target(reply, *args, **kwargs)
                else:
                    target = target(*args, **kwargs)
                if asyncio.iscoroutine(target):
                    target = await target

        self._update_state(State.from_object(self._target))
        self._state.last_change = time.time()
        return target

    async def _send_request(self, channel, **kwargs):
        response = {}
        await channel.send(self._target, kwargs)

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
            return self._decode(response["return"], self._target.session)

        if "repr" not in response:
            raise Exception("Telekinesis communication error: received unrecognized message schema %s" % response)

    async def _close(self):
        try:
            if isinstance(self._target, Route):
                async with Channel(self._session) as new_channel:
                    await new_channel.send(self._target, {"close": True})
            else:
                for listener in self._listeners:
                    await listener.close(True)
        except Exception:
            self._session.logger('Error closing Telekinesis Object: %s', self._target, exc_info=True)

    def __await__(self):
        return self._execute().__await__()

    def __repr__(self):
        return "\033[92m\u2248\033[0m " + str(self._state.repr)

    def __del__(self):
        if self._parent is None:
            self._session.pending_tasks.add(
                asyncio.get_event_loop().create_task(self._close())
            )

    def _encode(self, arg, receiver_id=None, listener=None, traversal_stack=None):
        i = str(id(arg))
        if traversal_stack is None:
            traversal_stack = {}
            output_stack = True
        else:
            output_stack = False
            if i in traversal_stack:
                return i

        traversal_stack[i] = None

        if type(arg) in (int, float, str, bytes, bool, type(None)):
            tup = (type(arg).__name__, arg)
        elif type(arg) in (range, slice):
            tup = (type(arg).__name__, (arg.start, arg.stop, arg.step))
        elif type(arg) in (list, tuple, set):
            tup = (type(arg).__name__, [self._encode(v, receiver_id, listener, traversal_stack) for v in arg])
        elif isinstance(arg, dict):
            tup = ("dict", {x: self._encode(arg[x], receiver_id, listener, traversal_stack) for x in arg})
        else:
            if isinstance(arg, Telekinesis):
                obj = arg
            else:
                obj = Telekinesis(
                    arg, self._session, self._mask, self._expose_tb, self._max_delegation_depth, self._compile_signatures,
                )

            route = obj._delegate(receiver_id, listener.channel)
            tup = (
                "obj",
                (route.to_dict(), self._encode(obj._state.to_dict(self._mask), receiver_id, listener, traversal_stack)),
            )

        traversal_stack[i] = tup

        if output_stack:
            traversal_stack["root"] = i
            return traversal_stack
        return i

    def _decode(self, input_stack, caller_id=None, root=None, output_stack=None):
        out = None
        if root is None:
            root = input_stack["root"]
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
                        )
                    out = channel.telekinesis._target
                else:
                    raise Exception(f"Unauthorized! {caller_id} {route.tokens}")
            else:
                out = Telekinesis._from_state(
                    state,
                    route,
                    self._session,
                    self._mask,
                    self._expose_tb,
                    self._max_delegation_depth,
                    self._compile_signatures,
                )

        output_stack[root] = out
        return out

    @staticmethod
    def _from_state(
        state, target, session, mask=None, expose_tb=True, max_delegation_depth=None, compile_signatures=True, parent=None,
    ):
        def callable_subclass(signature, method_name, docstring):
            class Telekinesis_(Telekinesis):
                @makefun.with_signature(
                    signature,
                    func_name=method_name,
                    doc=docstring if method_name == "__call__" else None,
                    module_name="telekinesis.telekinesis",
                )
                def __call__(self, *args, **kwargs):
                    return self._call(*args, **kwargs)

            return Telekinesis_

        method_name = state.pipeline[-1][1] if state.pipeline and state.pipeline[-1][0] == "get" else "__call__"

        reset = "call" in [x[0] for x in state.pipeline[:-1]]
        if method_name in state.methods or reset:
            signature, docstring = (not reset and state.methods.get(method_name)) or (None, None)
            signature = signature or "(*args, **kwargs)"
            # if not isinstance(target, type):
            signature = signature.replace("(", "(self, ")

            stderr = sys.stderr
            sys.stderr = io.StringIO()

            try:
                if compile_signatures and check_signature(signature):
                    Telekinesis_ = callable_subclass(signature, method_name, docstring)
                else:
                    Telekinesis_ = callable_subclass("(self, *args, **kwargs)", method_name, docstring)
            except Exception:
                Telekinesis_ = callable_subclass("(self, *args, **kwargs)", method_name, docstring)

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
                )

            if "__getitem__" in state.methods:
                Telekinesis_.__getitem__ = partialmethod(dundermethod, "__getitem__")
            if "__add__" in state.methods:
                Telekinesis_.__add__ = partialmethod(dundermethod, "__add__")
            if "__mul__" in state.methods:
                Telekinesis_.__mul__ = partialmethod(dundermethod, "__mul__")
            if "__setitem__" in state.methods:
                Telekinesis_.__setitem__ = setitem

        out = Telekinesis_(target, session, mask, expose_tb, max_delegation_depth, compile_signatures, parent)
        out._update_state(state)
        out.__doc__ = state.doc if method_name == "__call__" else docstring

        return out


def check_signature(signature):
    return not ("\n" in signature or (signature != re.sub(r"(?:[^A-Za-z0-9_])lambda(?=[\)\s\:])", "", signature)))


def inject_first_arg(func):
    func._tk_inject_first_arg = True
    return func
