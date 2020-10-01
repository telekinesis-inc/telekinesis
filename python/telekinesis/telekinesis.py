import sys
import io
import asyncio
import inspect
import traceback
import logging

import re
import makefun

from .client import Route, Channel

class State:
    def __init__(self, attributes=None, methods=None, repr=None, pipeline=None):
        self.attributes = attributes
        self.methods = methods
        self.pipeline = pipeline or []
        self.repr = repr

    def to_dict(self):
        return {
            'attributes': self.attributes,
            'methods': self.methods,
            'pipeline': self.pipeline,
            'repr': self.repr
        }
    
    def clone(self):
        return State(**self.to_dict())
    
    @staticmethod
    def from_object(target):
        logger = logging.getLogger(__name__)
        
        attributes, methods, repr_ = [], {}, ''
        
        for attribute_name in dir(target):
            if attribute_name[0] != '_' :
                try:
                    if isinstance(target, type):
                        target_attribute = target.__getattribute__(target, attribute_name)
                    else:
                        target_attribute = target.__getattribute__(attribute_name)

                    if '__call__' in dir(target_attribute):
                        if attribute_name == '__call__':
                            target_attribute = target
                        signature = None
                        try:
                            signature = str(inspect.signature(target_attribute))
                            
                            if '_tk_inject_first_arg' in dir(target_attribute) \
                            and target_attribute._tk_inject_first_arg:
                                signature = re.sub(r'[a-zA-Z0-9=\_\s]+(?=[\)\,])', '', signature, 1)\
                                                .replace('(,','(',1).replace('( ','(',1)
                        except:
                            logger.debug('Cound not obtain signature from %s.%s', 
                                        target, attribute_name)

                        methods[attribute_name] = (signature, target_attribute.__doc__)
                    else:
                        attributes.append(attribute_name)

                except Exception as e:
                    logger.error('Could not obtain handle for %s.%s: %s', target, 
                                 attribute_name, e)

        if isinstance(target, type):
            repr_ =  str(type(target))
            methods['__call__'] = (str(inspect.signature(target)), target.__init__.__doc__)
        else:
            repr_ = target.__repr__()

        return State(attributes, methods, repr_)

class Listener:
    def __init__(self, channel, coro_callback, expose_tb, max_depth, mask):
        self.channel = channel
        self.coro_callback = coro_callback
        self.expose_tb = expose_tb
        self.max_depth = max_depth
        self.mask = mask
        self.current_tasks = set()
        self.listen_task = asyncio.get_event_loop().create_task(self.listen())
    
    async def listen(self):
        try:
            await self.channel.listen()
            while True:
                message = await self.channel.recv()

                self.current_tasks.add(asyncio.get_event_loop().create_task(
                    self.coro_callback(self, self.channel, *message)))

                await asyncio.gather(*(x for x in self.current_tasks if x.done()))
                self.current_tasks = set(x for x in self.current_tasks if not x.done())
        except:
            logging.getLogger(__name__).error('', exc_info=True)

class Telekinesis():
    def __init__(self, target, session):
        self._logger = logging.getLogger(__name__)
        self._target = target
        self._session = session
        self._listeners = {}
        if isinstance(target, Route):
            self._state = State()
        else:
            self._update_state(State.from_object(target))
       
    def __call__(self, *args, **kwargs):
        self._state.pipeline.append(('call', (args, kwargs)))
        return self
    
    def __getattribute__(self, attr):
        if (attr[0] == '_') and (attr != '__call__'):
            return super().__getattribute__(attr)
        
        state = self._state.clone()
        state.pipeline.append(('get', attr))

        return Telekinesis._from_state(self._target, self._session, state)
    
    def _update_state(self, state):
        for d in dir(self):
            if d[0] != '_':
                self.__delattr__(d)

        for method_name in state.methods:
            self.__setattr__(method_name, None)

        for attribute_name in state.attributes:
            self.__setattr__(attribute_name, None)

        self._state = state
        return self

    def _add_listener(self, channel, expose_tb=True, max_depth=None, mask=None):
        route = channel.route
        
        self._listeners[route] = Listener(channel, self._handle, expose_tb, max_depth, mask)
        
        return route

    async def _handle(self, listener, channel, reply, payload):
        try:
            pipeline = payload.get('pipeline')
            ret = await self._execute(reply, pipeline)
            
            await channel.send(reply, {
                'return': self._encode(ret, reply.session, listener)})
        except:
            self._logger.error(payload, exc_info=True)

            self._state.pipeline.clear()
            await channel.send(reply, {
                'error': traceback.format_exc() if listener.expose_tb else ''})
    
    def _delegate(self, recepient_id, listener):
        if isinstance(self._target, Route):
            route = self._target.clone()
        else:
            if isinstance(listener, Listener):
                route = self._add_listener(Channel(self._session), listener.expose_tb, 
                                        listener.max_depth, listener.mask)
            else:
                route = self._add_listener(Channel(self._session))
                listener = self._listeners[route]
        
        token_header = self._session.extend_route(route, recepient_id, listener.max_depth)
        listener.channel.header_buffer.append(token_header)
        
        return route

    async def _execute(self, route=None, pipeline=None):
        if pipeline:
            pipeline = self._decode(pipeline)
        else:
            pipeline = []

        pipeline = self._state.pipeline + pipeline
        self._state.pipeline.clear()
        
        if isinstance(self._target, Route):
            new_channel = await Channel(self._session).send(
                self._target,
                {'pipeline': self._encode(pipeline, self._target.session)}, True)

            _, out = await new_channel.recv()
            await new_channel.close()
            
            
            if 'error' in out:
                raise Exception(out['error'])

            if 'return' in out:
                return self._decode(out['return'])
            raise Exception
        
        target = self._target
        for action, arg in pipeline:
            self._logger.info('%s %s %s', action, arg, target)
            if action == 'get':
                if arg[0] == '_':
                    raise Exception('Unauthorized!')
                target = target.__getattribute__(arg)
            if action == 'call':
                ar, kw = arg
                args, kwargs = [None]*len(ar), {}
                for i, x in enumerate(ar):
                    args[i] = await x._execute(route) if isinstance(x, Telekinesis) else x
                for i, x in kw.items():
                    kwargs[i] = await x._execute(route) if isinstance(x, Telekinesis) else x
                
                if '_tk_inject_first_arg' in dir(target) \
                and target._tk_inject_first_arg:
                    target = target(route, *args, **kwargs)
                else:
                    target = target(*args, **kwargs)
                if asyncio.iscoroutine(target):
                    target = await target
        
        return target

    def __await__(self):
        return self._execute().__await__()

    def __repr__(self):
        return '\033[92m\u2248\033[0m ' + str(self._state.repr)

    def _encode(self, arg, receiver_id=None, listener=None):
        if type(arg) in (int, float, str, bytes, bool, type(None)):
            return (type(arg).__name__, arg)
        if type(arg) in (range, slice):
            return (type(arg).__name__, (arg.start, arg.stop, arg.step))
        if type(arg) in (list, tuple, set):
            return (type(arg).__name__, [self._encode(v, receiver_id, listener) for v in arg])
        if isinstance(arg, dict):
            return ('dict', {x: self._encode(arg[x], receiver_id, listener) for x in arg})
        if isinstance(arg, Telekinesis):
            obj = arg
        else:
            obj = Telekinesis(arg, self._session)
            
        route = obj._delegate(receiver_id, listener)
        return ('obj', (route.to_dict(), obj._state.to_dict()))

    def _decode(self, tup):
        typ, obj = tup
        if typ in ('int', 'float', 'str', 'bytes', 'bool', 'NoneType'):
            return obj
        if typ in ('range', 'slice'):
            return {'range': range, 'slice': slice}[typ](*obj)
        if typ in ('list', 'tuple', 'set'):
            return {'list':list, 'tuple':tuple, 'set':set}[typ]([self._decode(v) 
                                                                 for v in obj])
        if typ == 'dict':
            return {x: self._decode(obj[x]) for x in obj}
        return Telekinesis._from_state(Route(**obj[0]), self._session, State(**obj[1]))

    @staticmethod
    def _from_state(target, session, state):
        method_name = state.pipeline[-1][1] if state.pipeline and state.pipeline[-1][0] == 'get' \
                                            else '__call__'

        signature, docstring = state.methods.get(method_name) or (None, None)
        signature = signature or '(*args, **kwargs)'
        if not isinstance(target, type):
            signature = signature.replace('(', '(self, ')


            stderr = sys.stderr
            sys.stderr = io.StringIO()

            try:
                if session.compile_signatures and check_signature(signature):
                    class Telekinesis_(Telekinesis):
                        @makefun.with_signature(signature,
                                                func_name=method_name,
                                                doc=docstring,
                                                module_name='telekinesis.telekinesis')
                        def __call__(self, *args, **kwargs):
                            return super().__call__(*args, **kwargs)
                else:
                    raise Exception
            except:
                if session.compile_signatures and check_signature(signature):
                    logging.getLogger(__name__).info('Could not compile signature %s for %s', signature, method_name)
                
                class Telekinesis_(Telekinesis):
                    @makefun.with_signature('(self, *args, **kwargs)',
                                            func_name=method_name,
                                            doc=docstring,
                                            module_name='telekinesis.telekinesis')
                    def __call__(self, *args, **kwargs):
                        return super().__call__(*args, **kwargs)

            sys.stderr = stderr

        return Telekinesis_(target, session)._update_state(state)

def check_signature(signature):
    return not ('\n' in signature or \
                (signature != re.sub(r'(?:[^A-Za-z0-9_])lambda(?=[\)\s\:])', '', signature)))

def inject_first_arg(func):
    func._tk_inject_first_arg = True
    return func