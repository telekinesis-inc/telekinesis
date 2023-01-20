import asyncio
from cmath import isfinite
import inspect
import types
import traceback
import logging
import re

from .client import Route, Channel


class State:
    def __init__(self, attributes=None, methods=None, repr=None, doc=None, name=None, pipeline=None):
        self.attributes = {} if attributes is None else (set(attributes) if isinstance(attributes, list) else attributes) 
        self.methods = methods or {}
        self.pipeline = pipeline or []
        self.repr = repr or ""
        self.doc = doc
        self.name = name
        self._pending_changes = {}
        self._history_offset = 0
        self._history = []

    def to_dict(self, mask=None, cache_attributes=False):
        mask = mask or set()
        return {
            "attributes": {k: v for k, v in self.attributes.items() if k not in mask}
            if cache_attributes and isinstance(self.attributes, dict)
            else [k for k in self.attributes if k not in mask],
            "methods": {k: v for k, v in self.methods.items() if k not in mask},
            "pipeline": self.pipeline,
            "repr": self.repr,
            "doc": self.doc,
            "name": self.name,
        }

    def clone(self):
        out = State(**self.to_dict(None, True))
        out._history = self._history.copy()
        out._history_offset = self._history_offset
        return out

    def get_diffs(self, last_version=0, mask=None, cache_attributes=False):
        if last_version < self._history_offset:
            return self._history_offset + len(self._history), self.to_dict(mask, cache_attributes)
        out = {}
        for i, diff in enumerate(self._history[last_version - self._history_offset :]):
            filtered = {}
            if "attributes" in diff:
                if cache_attributes:
                    x = {k: v for k, v in diff["attributes"][1].items() if k not in (mask or set())}
                else:
                    x = {
                        k: v[0] for k, v in diff["attributes"][1].items() if k not in (mask or set()) and v[0] in ["c", "d"]
                    }
                if x:
                    filtered["attributes"] = (diff["attributes"][0], x)
            if "methods" in diff:
                x = {k: v for k, v in diff["methods"][1].items() if k not in (mask or set())}
                if x:
                    filtered["methods"] = (diff['methods'][0], x)
            if "repr" in diff:
                filtered["repr"] = diff["repr"]
            if "doc" in diff:
                filtered["doc"] = diff["doc"]
            if "name" in diff:
                filtered["name"] = diff["name"]
            out[i + last_version + 1] = filtered
        out["pipeline"] = self.pipeline
        return 0, out

    def update_from_target(self, target):
        logger = logging.getLogger(__name__)

        attributes, methods, repr_ = {}, {}, ""

        for attribute_name in dir(target):
            if attribute_name[0] != "_" or attribute_name in [
                "__call__",
                "__init__",
                "__getitem__",
                "__setitem__",
                "__aiter__",
                "__anext__",
                "__add__",
                "__mul__",
            ]:
                try:
                    if isinstance(target, type):
                        try:
                            target_attribute = target.__getattribute__(target, attribute_name)
                        except AttributeError:
                            target_attribute = target.__getattr__(target, attribute_name)
                    else:
                        try:
                            try:
                                if attribute_name in dir(type(target)) and isinstance(type(target).__getattribute__(type(target), attribute_name), types.GetSetDescriptorType):
                                    # Hack to avoid duplicating memory usage with numpy arrays
                                    target_attribute = None
                                else:
                                    target_attribute = target.__getattribute__(attribute_name)
                            except (AttributeError, TypeError):
                                target_attribute = target.__getattribute__(attribute_name)
                        except AttributeError:
                            target_attribute = target.__getattr__(attribute_name)

                    if "__call__" in dir(target_attribute) and not isinstance(target_attribute, Telekinesis):
                        if attribute_name == "__call__" or (isinstance(target, type) and attribute_name == "__init__"):
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
                        except Exception as e:
                            logger.debug("Could not obtain signature from %s.%s: %s", target, attribute_name, e)

                        if attribute_name == "__init__":
                            if isinstance(target, type):
                                methods["__call__"] = (signature, target.__init__.__doc__)
                        elif attribute_name == '__call__':
                            methods[attribute_name] = (
                                signature, 
                                (target_attribute.__call__.__doc__ or '')
                            )
                        else:
                            methods[attribute_name] = (signature, str(target_attribute.__doc__))
                    else:
                        if asyncio.iscoroutine(target_attribute):
                            target_attribute.close()
                            target_attribute = None
                        attributes[attribute_name] = target_attribute.copy() if type(target_attribute) in [dict, list, set] else target_attribute 

                except Exception as e:
                    logger.error("Could not obtain handle for %s.%s: %s", target, attribute_name, e)
        try:
            name = target.__name__
        except:
            name = None

        if isinstance(target, type):
            repr_ = str(type(target))
            doc = target.__doc__
        else:
            doc = target.__doc__
            repr_ = target.__repr__()

        if self._history_offset == 0:
            self._history_offset = 1
            self.attributes = attributes
            self.methods = methods
            self.repr = repr_
            self.doc = doc
            self.name = name
            return self
        diff = {}
        cmp = {
            "attributes": (self.attributes, attributes),
            "methods": (self.methods, methods),
            "repr": (self.repr, repr_),
            "doc": (self.doc, doc),
            "name": (self.name, name)
        }
        for k in cmp:
            x = self.calc_diff(*cmp[k])
            if x:
                diff[k] = x

        if diff:
            return self.update_from_diffs(0, {self._history_offset + len(self._history) + 1: diff})
        return self

    def update_from_diffs(self, last_version, diffs):
        ks = ["attributes", "methods", "repr", "doc", "name"]
        if last_version:
            self._history = []
            self._history_offset = last_version
            self._pending_changes = {}
            new_state = State(**diffs)
            for k in ks:
                self.__dict__[k] = new_state.__dict__[k]
        else:
            next_version = self._history_offset + len(self._history) + 1
            diffs_int = {int(k): v for k, v in diffs.items() if k != "pipeline"}
            if next_version in diffs_int:
                for i in range(len(diffs_int)-next_version+min(diffs_int.keys())):
                    self._history.append(diffs_int[i + next_version])
                    for k in ks:
                        if k in diffs_int[i + next_version]:
                            self.__dict__[k] = self.apply_diff(self.__dict__[k], diffs_int[i + next_version][k])

                for i in sorted(self._pending_changes):
                    if i < (self._history_offset + len(self._history) + 1):
                        self._pending_changes.pop(i)
                    elif i == (self._history_offset + len(self._history) + 1):
                        self._history.append(self._pending_changes[i])
                        for k in ks:
                            if k in self._pending_changes[i]:
                                self.__dict__[k] = self.apply_diff(self.__dict__[k], self._pending_changes[i][k])
                    else:
                        break
            else:
                self._pending_changes.update(diffs_int)

        if "pipeline" in diffs:
            self.pipeline = diffs["pipeline"]

        return self

    def __repr__(self):
        return "State<attributes: %s |methods: %s>" % (
            ", ".join(x for x in self.attributes or {}),
            ", ".join(x for x in self.methods or {}),
        )

    @staticmethod
    def calc_diff(obj0, obj1, max_depth=10):
        try:
            if obj0 == obj1:
                return
        except Exception: pass
        if max_depth > 0:
            if type(obj0) == type(obj1) == dict:
                changes = {}
                for key in obj0:
                    if key in obj1:
                        changes[key] = State.calc_diff(obj0[key], obj1[key], max_depth - 1)
                    else:
                        changes[key] = ("d",)
                for key in obj1:
                    if key not in obj0:
                        changes[key] = ("c", obj1[key])
                return ("u", {k: c for k, c in changes.items() if c})
            if type(obj0) == type(obj1) == set:
                changes = {}
                for key in obj0.difference(obj1):
                    changes[key] = "d"
                for key in obj1.difference(obj0):
                    changes[key] = "c"
                return ("u", changes)

        return ("r", obj1)

    @staticmethod
    def apply_diff(obj0, diff):
        if not diff:
            return obj0
        if diff[0] in {"c", "r"}:
            return diff[1]
        if diff[0] == "u":
            if type(obj0) == dict:
                obj1 = obj0.copy()
                for key, (code, *changes) in diff[1].items():
                    if code in {"c", "r"}:
                        obj1[key] = changes[0]
                    if code == "d" and key in obj1:
                        del obj1[key]
                    if code == "u":
                        obj1[key] = State.apply_diff(obj1[key], (code, changes[0]))
                return obj1
            if type(obj0) == set:
                obj1 = obj0.copy()
                for key, code in diff[1].items():
                    if code == "c":
                        obj1.add(key)
                    if code == "d":
                        obj1.discard(key)
                return obj1

    def __getstate__(self):
        return {'channel': self.channel, 'coro_callback': self.coro_callback}

    def __setstate__(self, state):
        self.channel = state['channel']
        self.set_callback(state['coro_callback'])


class Telekinesis:
    def __init__(
        self,
        target,
        session,
        mask=None,
        expose_tb=True,
        max_delegation_depth=None,
        block_gc=False,
        parent=None,
    ):
        self._logger = logging.getLogger(__name__)
        self._target = target
        self._session = session
        self._mask = [mask] if isinstance(mask, str) else mask or []
        self._expose_tb = expose_tb
        self._max_delegation_depth = max_delegation_depth
        self._block_gc = block_gc
        self._parent = parent
        self._channel = None
        self._clients = {}
        self._requests = {}
        self._on_update_callback = None
        self._subscription = None
        self._subscribers = set()
        self._state = State()
        self._last_magic = None
        self._magic_history = []

        if isinstance(target, Route):
            if parent is None:
                if (target.session, target.channel) not in session.routes:
                    session.routes[(target.session, target.channel)] = {"refcount": 0, "delegations": set(), "state": State()}
                session.routes[(target.session, target.channel)]["refcount"] += 1

        elif not asyncio.iscoroutine(target) and not parent:
            session.targets[id(target)] = (session.targets.get(id(target)) or set()).union(set((self,)))
            self._state.update_from_target(target)
            self._update_state()

    def __getattribute__(self, attr, override=False):
        if attr[0] == "_" and not override:
            return super().__getattribute__(attr)

        if isinstance(self._state.attributes, dict) and isinstance(self._state.attributes.get(attr), Telekinesis) and not self._state.pipeline:
            state = self._state.attributes[attr]._state.clone()
            target = self._state.attributes[attr]
        else: 
            state = self._state.clone()
            state.pipeline.append(("get", attr))
            target = self

        return Telekinesis._from_state(
            state,
            target._target,
            target._session,
            target._mask,
            target._expose_tb,
            target._max_delegation_depth,
            target._block_gc,
            target,
        )

    def _get_root_parent(self):
        if self._parent:
            return self._parent._get_root_parent()
        return self

    @property
    def _last_value(self):
        if (
            (len(self._state.pipeline) == 1)
            and (self._state.pipeline[0][0] == "get")
            and (isinstance(self._state.attributes, dict))
        ):
            return self._state.attributes[self._state.pipeline[0][1]]

    def _update_state(self, diffs=None):
        if diffs:
            self._state.update_from_diffs(*diffs)
        for d in dir(self):
            if d[0] != "_":
                self.__delattr__(d)

        for method_name in self._state.methods:
            if method_name[0] != "_":
                super().__setattr__(method_name, None)

        for attribute_name in self._state.attributes:
            if attribute_name[0] != "_":
                super().__setattr__(attribute_name, None)

        if self._on_update_callback:
            self._on_update_callback(self)

        gets = [g for g in self._state.pipeline if g[0] == 'get']
        if gets:
            self.__doc__ = '\n'.join([s or '' for s in self._state.methods.get(gets[-1][1]) or ['', '']])
        else:
            self.__doc__ = ((self._state.doc and (self._state.doc + "\n")) or "") + '\n'.join([s or '' for s in self._state.methods.get('__call__') or ['', '']])



        return self

    def _delegate(self, receiver, parent_channel=None):
        token_header = []
        extend_route = True

        if isinstance(self._target, Route):
            route = self._target.clone()
            max_delegation_depth = None
            if isinstance(receiver, str) and receiver == "*":
                raise PermissionError("Cannot delegate remote Channel to public.")
            if not route.tokens:
                extend_route = False
            if receiver == self._session.session_key.public_serial():
                extend_route = False

        else:
            if not self._channel:
                self._channel = Channel(self._session, is_public=isinstance(receiver, str) and receiver == "*")
                self._channel.telekinesis = self
                self._channel.listen()
                token_header += [self._channel.header_buffer.pop()]
            if self._channel.is_public:
                extend_route = False

            max_delegation_depth = self._max_delegation_depth
            route = self._channel.route
            if isinstance(receiver, str) and receiver == "*" and not self._channel.is_public:
                self._channel.is_public = True
                self._channel.listen()
                token_header += [self._channel.header_buffer.pop()]

        if extend_route:
            self._session.extend_route(route, receiver[0] if isinstance(receiver, tuple) else receiver, max_delegation_depth)
            if (route.session, route.channel) in self._session.routes:
                self._session.routes[(route.session, route.channel)]["delegations"].add(
                    receiver if isinstance(receiver, tuple) else (receiver, None)
                )

        route._parent_channel = parent_channel or self._channel
        [route._parent_channel.header_buffer.append(th) for th in token_header]

        return route

    def _subscribe(self, callback=None):
        def subscription_callback(tk):
            pipeline = self._state.pipeline
            self._update_state(tk._state.get_diffs(0, None, True))
            self._state.pipeline = pipeline

        self._on_update_callback = callback
        self._subscription = Telekinesis(
            subscription_callback,
            self._session,
            None,
            self._expose_tb,
            self._max_delegation_depth,
            self._block_gc,
        )
        self._state.pipeline.append(("subscribe", self._subscription))

        return self

    async def _handle_request(self, channel, metadata, payload):
        pipeline = None
        reply_to = None
        try:
            if metadata.caller.session not in self._clients:
                self._clients[metadata.caller.session] = {"last_state": 0, "cache_attributes": None}
                self._clients.pop((metadata.caller.session[0], None), None)

            if "close" in payload:
                self._clients.pop(metadata.caller.session, None)
                for delegation_lst in payload["close"]:
                    delegation = tuple(delegation_lst)
                    if delegation not in self._clients:
                        self._clients[delegation] = {"last_state": 0, "cache_attributes": None}
                        if delegation[1] is not None:
                            self._clients.pop((delegation[0], None), None)
                if not self._clients and not self._channel.is_public:
                    await self._close()
            elif "ping" in payload:
                await channel.channel.send(metadata.caller, {"pong": True})
            elif "pipeline" in payload:
                self._requests[channel] = {'metadata': metadata, 'payload': payload}
                pipeline = self._decode(payload.get("pipeline"), metadata.caller.session[0])
                self._logger.info("%s %s called %s %s", metadata.caller.session[0][:4], metadata.caller.session[1][:2], self, len(pipeline))
                # print('...', self._requests.keys())
                if payload.get("reply_to"):
                    reply_to = Route(**payload["reply_to"])
                    reply_to.validate_token_chain(self._session.session_key.public_serial())
                    metadata.reply_to = reply_to
                self._requests[channel]['reply_to'] = reply_to
                ret = await self._execute(metadata, pipeline, True)
                await self._respond_request(channel, ret, False)

        except (Exception, KeyboardInterrupt) as e:
            if not isinstance(e, StopAsyncIteration):
                if pipeline is None:
                    self._logger.error("Telekinesis request error with payload %s", payload, exc_info=True)
                else:
                    self._logger.error("Telekinesis request error with pipeline %s", pipeline, exc_info=True)

            self._state.pipeline.clear()
            err_message = {
                "error": traceback.format_exc() if self._expose_tb else "", 
                "error_type": type(e).__name__
            }
            await self._respond_request(channel, err_message, True)

    async def _respond_request(self, channel, return_object, error):
        # print('here')
        if channel in self._requests:
            # print('here 1')
            reply_to = self._requests[channel].get('reply_to')
            metadata = self._requests[channel]['metadata']
            payload = self._requests[channel]['payload']
            self._requests.pop(channel)
            if not error:
                if (
                    isinstance(return_object, Telekinesis)
                    and isinstance(return_object._target, Route)
                    and return_object._state.pipeline
                    and return_object._target.session != (self._session.session_key.public_serial(), self._session.instance_id)
                ):
                    await return_object._forward(
                        return_object._state.pipeline,
                        reply_to or metadata.caller,
                        root_parent=payload.get("root_parent") if reply_to else (self, metadata.caller.session),
                    )
                else:
                    try:
                        if reply_to:
                            async with Channel(self._session) as new_channel:
                                await new_channel.send(
                                    reply_to,
                                    {
                                        "return": self._encode(return_object, reply_to.session, new_channel),
                                        "root_parent": payload.get("root_parent"),
                                    },
                                )
                        else:
                            await channel.send(
                                metadata.caller,
                                {
                                    "return": self._encode(return_object, metadata.caller.session),
                                    "root_parent": None if return_object is self else self._encode(self, metadata.caller.session, channel),
                                },
                            )
                    except ConnectionError:
                        pass
            else:
                try:
                    if reply_to:
                        async with Channel(self._session) as new_channel:
                            await new_channel.send(reply_to, return_object)
                    else:
                        await channel.send(metadata.caller, return_object)
                except Exception as e:
                    print(e)
                finally:
                    pass

    async def _execute(self, metadata=None, pipeline=None, break_on_telekinesis=False):
        if asyncio.iscoroutine(self._target):
            target = await self._target
            x = self
            while x:
                x._target = target
                x = x._parent

            if isinstance(self._target, Route):
                if (self._target.session, self._target.channel) not in self._session.routes:
                    self._session.routes[(self._target.session, self._target.channel)] = {
                        "refcount": 0,
                        "delegations": set(),
                        "state": State(),
                    }
                self._session.routes[(self._target.session, self._target.channel)]["refcount"] += 1
            else:
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
                break_on_telekinesis
                and isinstance(target, Telekinesis)
                and isinstance(target._target, Route)
                and (
                    target._target.session != (self._session.session_key.public_serial(), self._session.instance_id)
                    or target._target.channel not in self._session.channels
                )
            ):
                new_state = target._state.clone()
                target = Telekinesis(
                    target._target,
                    target._session,
                    target._mask,
                    target._expose_tb,
                    target._max_delegation_depth,
                    target._block_gc,
                    target._parent,
                )
                target._state = new_state
                target._state.pipeline += pipeline[i:]
                break

            if action == "get":
                self._logger.info("%s %s %s", action, arg, target)
                if (
                    (arg[0] == "_" and arg not in ["__getitem__", "__setitem__", "__aiter__", "__anext__", "__add__", "__mul__"])
                    or arg in (self._mask or [])
                ):
                    raise PermissionError("Private attributes and methods (those starting with _) cannot be accessed remotely")
                elif type(target) in (dict, list, set) and arg not in (
                    'get', 'index', 'count', '__getitem__', 'copy', 'difference', 'intersection', 'isdisjoint', 'issubset', 'issuperset', 
                    'symmetric_difference', 'union' 
                ):
                    raise PermissionError("dicts, lists and sets cannot be modified remotely")
                if isinstance(target, type):
                    try:
                        target = target.__getattribute__(target, arg)
                    except AttributeError as ae:
                        try:
                            target = target.__getattr__(target, arg)
                        except AttributeError:
                            raise ae
                else:
                    try:
                        target = target.__getattribute__(arg)
                    except AttributeError as ae:
                        try:
                            target = target.__getattr__(arg)
                        except AttributeError:
                            raise ae
                if asyncio.iscoroutine(target):
                    target = await target
                
            if action == "call":
                self._logger.info("%s %s", action, target)
                ar, kw = arg
                if "_tk_block_arg_evaluation" in dir(target) and target._tk_block_arg_evaluation:
                    args, kwargs = [ar, kw]
                else:
                    args, kwargs = [await exc(x) for x in ar], {x: await exc(kw[x]) for x in kw}

                if (
                    "_tk_inject_first_arg" in dir(target) and target._tk_inject_first_arg or
                    isinstance(target, type) and "_tk_inject_first_arg" in dir(target.__init__) and target.__init__._tk_inject_first_arg
                ):
                    target = target(metadata, *args, **kwargs)
                else:
                    target = target(*args, **kwargs)
                if asyncio.iscoroutine(target):
                    target = await target
                if (
                    isinstance(target, Telekinesis)
                    and isinstance(target._target, Route)
                    and target._state.pipeline
                    and not break_on_telekinesis
                ):
                    target = await target._execute()
            if action == "subscribe":
                if arg._target.validate_token_chain(metadata.caller.session[0]):
                    tk = Telekinesis._reuse(
                        target,
                        self._session,
                        self._mask,
                        self._expose_tb,
                        self._max_delegation_depth,
                        self._block_gc,
                        None,
                    )
                    if arg._target.session not in tk._clients:
                        tk._clients[arg._target.session] = {"last_state": 0, "cache_attributes": True}
                        tk._clients.pop((arg._target.session[0], None), None)
                    if tk._clients[arg._target.session]["cache_attributes"] != True:
                        tk._clients[arg._target.session]["last_state"] = 0
                    tk._clients[arg._target.session]["cache_attributes"] = True
                    tk._subscribers.add(arg)
            touched = touched.union((self._session.targets.get(id(target))) or set())

        for tk in touched:
            tk._state.update_from_target(tk._target)
            tk._update_state()
            if tk and tk._subscribers:
                for s in tk._subscribers:
                    asyncio.create_task(s(tk)._execute())

        if target is self._target:
            return self

        return target

    async def _timeout(self, seconds):
        return await asyncio.wait_for(self._execute(), seconds)

    async def _send_request(self, channel, **kwargs):
        response = {}
        await channel.send(self._target, kwargs)

        if not kwargs.get("reply_to"):
            metadata, response = await channel.recv()

            if response.get("root_parent"):
                root = self._get_root_parent()
                diffs = root._decode(response["root_parent"], metadata.caller.session[0])._state.get_diffs(0, None, True)
                root._update_state(diffs)

            if "error" in response:
                if response.get("error_type") == "PermissionError":
                    raise PermissionError(response["error"])
                if response.get("error_type") == "StopAsyncIteration":
                    raise StopAsyncIteration(response["error"])
                else:
                    raise Exception(response["error"])

            if "return" in response:
                ret = self._decode(response["return"], metadata.caller.session[0])
                return ret

    async def _close(self):
        try:
            if isinstance(self._target, Route):
                self._session.routes[(self._target.session, self._target.channel)]["refcount"] -= 1
                if self._session.routes[(self._target.session, self._target.channel)]["refcount"] <= 0:
                    o = self._session.routes.pop((self._target.session, self._target.channel))
                    async with Channel(self._session) as new_channel:
                        await new_channel.send(self._target, {"close": list(o["delegations"])})
            else:
                # print('closing', id(self._target), self._session.targets.get(id(self._target)))
                if id(self._target) in self._session.targets:
                    self._session.targets[id(self._target)].discard(self)
                    if not self._session.targets[id(self._target)]:
                        self._session.targets.pop(id(self._target))
                    self._channel and await self._channel.close()
        except ConnectionError:
            self._session.logger.info("Error closing Telekinesis Object: %s", self._target, exc_info=True)
        except Exception:
            self._session.logger.error("Error closing Telekinesis Object: %s", self._target, exc_info=True)

    async def _forward(self, pipeline, reply_to=None, **kwargs):
        async with Channel(self._session) as new_channel:
            if reply_to:
                self._session.extend_route(reply_to, self._target.session[0])
            for key, value in kwargs.items():
                if isinstance(value, tuple) and isinstance(value[0], Telekinesis):
                    kwargs[key] = value[0]._encode(value[0], value[1], new_channel)
            return await self._send_request(
                new_channel,
                reply_to=reply_to and reply_to.to_dict(),
                pipeline=self._encode(pipeline, self._target.session, new_channel),
                **kwargs,
            )

    def _register_magic(self, ipython, name=None):
        self_name =[ k for k,v in ipython.ns_table['user_global'].items() 
            if v is self and k != '_'][0]
        
        name = name or self_name

        def inject_code(line, cell):
            args, kwargs = [], {}
            method, *rest = line.split('(')
            eval(
                "(lambda *a, **kw: _tkj_args.extend(a) or _tkj_kwargs.update(kw))"+'('.join(['', *rest]),
                {**ipython.ns_table['user_global'], '_tkj_args': args, '_tkj_kwargs': kwargs}
            )
            f = eval(self_name+method.strip(), ipython.ns_table['user_global'])
            t = asyncio.create_task(f(cell, *args, **kwargs)._execute())
            
            self._last_magic = t
            self._magic_history.append(t)
        
        ipython.register_magic_function(inject_code, 'cell', name)

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
            self._block_gc,
            self,
        )

    def __setattr__(self, attribute, value):
        if attribute[0] != "_":
            raise PermissionError("Attributes for Telekinesis objects cannot be set directly")
        super().__setattr__(attribute, value)

    def __getitem__(self, key):
        return self.__getattribute__('__getitem__', True)(key)

    def __setitem__(self, key, val):
        return asyncio.create_task(self.__getattribute__('__setitem__', True)(key, val)._execute())
 
    def __aiter__(self):
        return self.__getattribute__('__aiter__', True)()

    def __anext__(self):
        return self.__getattribute__('__anext__', True)()

    def __add__(self, val):
        return self.__getattribute__('__add__', True)(val)

    def __mul__(self, val):
        return self.__getattribute__('__mul__', True)(val)

    def __await__(self):
        return self._execute().__await__()

    def __repr__(self):
        return "\033[92m\u2248\033[0m " + str(self._state.repr)

    def __del__(self):
        if self._parent is None and not self._block_gc:
            self._session.pending_tasks.add(asyncio.get_event_loop().create_task(self._close()))

    def _encode(self, target, receiver=None, channel=None, traversal_stack=None, block_recursion=False):
        if traversal_stack is None:
            i = 0
            traversal_stack = {}
        else:
            if id(target) in traversal_stack:
                return str(traversal_stack[id(target)][0])
            i = len(traversal_stack)

        traversal_stack[id(target)] = [i, None, target]
        # ..  keep a copy of the target so it doesn't get garbage collected - which messes up the id()

        if receiver is None:
            receiver = (self._session.session_key.public_serial(), self._session.instance_id)


        if type(target) in (str, bytes, bool, type(None)):
            tup = (type(target).__name__, target)
        elif isinstance(target, float):
            tup = ('float', target)
        elif isinstance(target, int) or re.match(r'<class \'numpy\.[\w]*int', str(type(target))):
            tup = ('int', int(target))
        elif type(target) in (range, slice):
            tup = (type(target).__name__, (target.start, target.stop, target.step))
        elif type(target) in (list, dict) and is_bson_encodable(target):
            tup = ('raw_'+type(target).__name__, target)
        elif type(target) in (list, tuple, set):
            tup = (
                type(target).__name__,
                [self._encode(v, receiver, channel, traversal_stack, block_recursion) for v in target],
            )
        elif type(target) == dict:
            tup = (
                "dict",
                [(
                    self._encode(x, receiver, channel, traversal_stack, block_recursion),
                    self._encode(target[x], receiver, channel, traversal_stack, block_recursion) 
                ) for x in target],
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
                    self._block_gc,
                )
            if not isinstance(obj._target, Route):
                if receiver not in obj._clients:
                    obj._clients[receiver] = {"last_state": 0, "cache_attributes": None}
                    if receiver[1] is not None:
                        obj._clients.pop((receiver[0], None), None)

            route = obj._delegate(receiver[0], channel or self._channel)
            diffs = obj._state.get_diffs(
                obj._clients[receiver]["last_state"] if receiver in obj._clients else 0,
                self._mask,
                obj._clients.get(receiver) and obj._clients[receiver]["cache_attributes"] and not block_recursion,
            ) if receiver != route.session else [0, {"pipeline": obj._state.pipeline}]
            tup = (
                "obj",
                (
                    route.to_dict(),
                    self._encode(
                        diffs,
                        receiver,
                        channel,
                        traversal_stack,
                        block_recursion=True,
                    ),
                ),
            )
            if receiver in obj._clients:
                obj._clients[receiver]["last_state"] = obj._state._history_offset + len(obj._state._history)

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
        if typ in ("int", "float", "str", "bytes", "bool", "NoneType", "raw_list", "raw_dict"):
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
            for k, v in obj:
                out[self._decode(input_stack, caller_id, k, output_stack)] = self._decode(input_stack, caller_id, v, output_stack)
        elif typ == "route":
            out = Route(**obj)
        else:
            route = Route(**obj[0])
            state_diff = self._decode(input_stack, caller_id, obj[1], output_stack)

            if route.session == (self._session.session_key.public_serial(), self._session.instance_id) and route.channel in self._session.channels:
                channel = self._session.channels.get(route.channel)
                if channel.validate_token_chain(caller_id, route.tokens):

                    if state_diff[1].get('pipeline'):
                        
                        return Telekinesis._from_state(
                            channel.telekinesis._state.clone().update_from_diffs(*state_diff),
                            channel.telekinesis._target,
                            self._session,
                            self._mask,
                            self._expose_tb,
                            self._max_delegation_depth,
                            self._block_gc,
                            channel.telekinesis,
                        )
                    out = channel.telekinesis._target
                else:
                    raise PermissionError(f"Unauthorized! {caller_id} {route} {route.tokens}")
            # elif self._parent and (
            #     ("__call__" not in self._state.methods)
            #     or ("methods" not in state_diff[1])
            #     or ("__call__" not in state_diff[1]["methods"])
            # ):
            elif self._parent and root == '0':
                self._target = route
                self._parent = None
                if (self._target.session, self._target.channel) not in self._session.routes:
                    self._session.routes[(self._target.session, self._target.channel)] = {
                        "refcount": 0,
                        "delegations": set(),
                        "state": State(),
                    }
                self._session.routes[(self._target.session, self._target.channel)]["state"].update_from_diffs(*state_diff)
                self._update_state(
                    self._session.routes[(self._target.session, self._target.channel)]["state"].get_diffs(0, None, True)
                )
                self._session.routes[(self._target.session, self._target.channel)]["refcount"] += 1
                out = self
            elif (isinstance(self._target, Route) and (route == self._target)) and (
                ("__call__" not in self._state.methods)
                or ("methods" not in state_diff[1])
                or ("__call__" not in state_diff[1]["methods"])
            ):
                self._session.routes[(self._target.session, self._target.channel)]["state"].update_from_diffs(*state_diff)
                self._update_state(
                    self._session.routes[(self._target.session, self._target.channel)]["state"].get_diffs(0, None, True)
                )
                out = self
            else:
                if (route.session, route.channel) not in self._session.routes:
                    self._session.routes[(route.session, route.channel)] = {
                        "refcount": 0,
                        "delegations": set(),
                        "state": State(),
                    }
                self._session.routes[(route.session, route.channel)]["state"].update_from_diffs(*state_diff)
                out = Telekinesis._from_state(
                    self._session.routes[(route.session, route.channel)]["state"].clone(),
                    route,
                    self._session,
                    self._mask,
                    self._expose_tb,
                    self._max_delegation_depth,
                    self._block_gc,
                )

        output_stack[root] = out
        return out

    @property
    def __signature__(self):
        gets = [g for g in self._state.pipeline if g[0] == 'get']
        if gets:
            return (self._state.methods.get(gets[-1][1]) or [None])[0]
        return (self._state.methods.get('__call__') or [None])[0]

    # @property
    # def __doc__(self):
    #     gets = [g for g in self._state.pipeline if g[0] == 'get']
    #     if gets:
    #         return '\n'.join([s or '' for s in self._state.methods.get(gets[-1][1]) or ['', '']])
    #     return ((self._state.doc and self._state.doc + "\n") or "") + '\n'.join([s or '' for s in self._state.methods.get('__call__') or ['', '']])

    @staticmethod
    def _from_state(
        state,
        target,
        session,
        mask=None,
        expose_tb=True,
        max_delegation_depth=None,
        block_gc=True,
        parent=None,
    ):
        out = Telekinesis(target, session, mask, expose_tb, max_delegation_depth, block_gc, parent)
        out._state = state
        out._update_state()

        return out

    @staticmethod
    def _reuse(
        target,
        session,
        mask=None,
        expose_tb=True,
        max_delegation_depth=None,
        block_gc=True,
        parent=None,
    ):
        kwargs = locals()

        for tk in session.targets.get(id(target)) or []:
            if all((kwargs[x] == tk.__getattribute__("_" + x) for x in kwargs)):
                return tk
        return Telekinesis(**kwargs)


def inject_first_arg(func):
    func._tk_inject_first_arg = True
    return func


def block_arg_evaluation(func):
    func._tk_block_arg_evaluation = True
    return func

def is_bson_encodable(target, depth=0):
    if depth > 300:
        return False
    elif type(target) in (str, bytes, bool, type(None), float, int):
        return True
    elif type(target) == list:
        return all(is_bson_encodable(x, depth+1) for x in target)
    elif type(target) == dict:
        return all(isinstance(k, str) and is_bson_encodable(x, depth+1) for k, x in target.items())
    return False