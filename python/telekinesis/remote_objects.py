import os
import inspect
import types
import asyncio
from functools import partial
import traceback

import makefun
import bson

from .client import Channel

class RemoteController:
    def __init__(self, target, channel, mask=None, logger=None):
        self._target = target
        self._channel = channel
        self._istype = isinstance(target, type)
        self._mask = mask or set()
        self._task = asyncio.create_task(self._listen())
        self._logger = logger or print

        self._state = {}

        if id(target) in channel.session.remote_controllers:
            channel.session.remote_controllers.add(self)
        else:
            channel.session.remote_controllers[id(target)] = set([self])

        self._update_state()

    def _update_state(self):
        self._state = {
            'attributes': [],
            'methods': {}
        }
        
        target = self._target
        
        for attribute_name in dir(target):
            if attribute_name[0] != '_' \
            or attribute_name in ('__call__', '__getitem__', '__setitem__', '__add__', 
                                  '__mul__', '__iter__', '__next__') \
            and attribute_name not in self._mask:
                if self._istype:
                    target_attribute = target.__getattribute__(target, attribute_name)
                else:
                    target_attribute = target.__getattribute__(attribute_name)
                
                if '__call__' in dir(target_attribute):
                    try:
                        self._state['methods'][attribute_name] = str(inspect.signature(target_attribute))
                    except:
                        self._state['methods'][attribute_name] = None
                else:
                    self._state['attributes'].append(attribute_name)
        
        if self._istype:
            self._state['_repr'] =  str(type(target))
        else:
            self._state['_repr'] = target.__repr__()

        self._state['_channel'] = self._channel.route_dict()
    
    def _call_method(self, method, args, kwargs):
        dargs = RemoteObjectBase._decode(args, self._channel.session)
        dkwargs = RemoteObjectBase._decode(kwargs, self._channel.session)
        self._logger('calling', method, 'args', dargs, 'kwargs', dkwargs)
        
        if method[0] != '_' \
        or method in ('__call__', '__getitem__', '__setitem__', '__add__', 
                      '__mul__', '__repr__', '__iter__', '__next__', '_get_attribute') \
        and method not in self._mask:
            if method == '_get_attribute' and dargs[0][0] != '_' and dargs[0] not in self._mask:
                call_return = self._target.__getattribute__(dargs[0])
            else:
                if self._istype:
                    call_return = self._target.__getattribute__(self._target, method)(*dargs, **dkwargs)
                else:
                    call_return = self._target.__getattribute__(method)(*dargs, **dkwargs)
                
                self._update_state()
            return call_return
        
        raise PermissionError('Calling a private method', method,'of a RemoteObject is not permitted')
    
    async def _listen(self):
        await self._channel.listen()
        while True:
            await self._onmessage(*(await self._channel.recv()))
        
    async def _close(self):
        await self._channel.close()
        
    async def _onmessage(self, reply, payload):
        try:
            if payload:
                call_return = self._call_method(**payload)
            else:
                call_return = self
            await self._channel.send(reply, {'return': await self._encode(call_return, self._channel.session, self)})
        except Exception:
            self._logger(traceback.format_exc())
    
    @staticmethod
    async def _encode(target, session, remote_session_id, mask=None, logger=None):
        mask = mask or set()
        logger = logger or print
        encode = lambda v: RemoteController._encode(v, Channel(session), remote_session_id, mask, logger)

        if type(target) in (int, float, str, bool, bytes, type(None)):
            return (type(target).__name__, target)
        if type(target) in (range, slice):
            return (type(target).__name__, (target.start, target.stop, target.step))
        elif isinstance(target, dict):
            return ('dict', {k: await encode(v) for k, v in target.items()})
        elif type(target) in [list, tuple, set]:
            return (type(target).__name__, [await encode(v) for v in target])
        elif isinstance(target, RemoteObjectBase) or isinstance(target, RemoteController):
            # TODO extend persmissions to remote_session_id
            return ('object', target._state)
        else:
            if rcs := session.remote_controllers.get(id(target)):
                for rc in rcs:
                    if rc._mask == mask and rc._logger == logger:
                        return ('object', rc._state)
            return ('object', RemoteController(target, Channel(session), mask, logger)._state)

async def spawn(session, destination):
    new_channel = Channel(session)
    
    await new_channel.send(destination, {}, True)
    _, out = await new_channel.recv()
    
    await new_channel.close()
    
    return RemoteObjectBase._decode(out['return'], session)

class RemoteObjectBase:
    def __init__(self, session, state):
        self._session = session
        self._repr = ''
        self._state = None
        self._tasks = set()

        session.remote_objects[str(state['_channel'])] = self

        self._update_state(state)
      
    async def __call__(self, *args, **kwargs):
        return await self._call_method('__call__', *args, **kwargs)
    
    async def __getitem__(self, *args, **kwargs):
        return await self._call_method('__getitem__', *args, **kwargs)
    
    def __setitem__(self, *args, **kwargs):
        self._tasks.add(asyncio.create_task(self._call_method('__setitem__', *args, **kwargs)))
        #TO DO... wait for them somewhere
    
    async def __mul__(self, *args, **kwargs):
        return await self._call_method('__mul__', *args, **kwargs)
    
    async def __add__(self, *args, **kwargs):
        return await self._call_method('__add__', *args, **kwargs)
    
    # __aiter__ __anext__ ...

    def __repr__(self):
        ds, dc = self._state['_channel']['session'][:4], self._state['_channel']['channel'][:4]
        return f'Remote Object -> ({ds} {dc}): {self._repr}'
    
    def _update_state(self, state):
        self._state = state
        if state:
            for d in dir(self):
                if d[0] != '_':
                    att = self.__getattribute__(d)
                    if asyncio.iscoroutine(att):
                        try:
                            att.close()
                        except:
                            pass
                    self.__delattr__(d)

            for method_name, signature in state['methods'].items():
                signature = signature or '(*args, **kwargs)'
                method = makefun.create_function(signature, 
                                                 partial(self._call_method, method_name),
                                                 func_name=method_name,
                                                 module_name='telekinesis.client.RemoteObject')
                self.__setattr__(method_name, method)

            for attribute_name in state['attributes']:
                    self.__setattr__(attribute_name, self._get_attribute(attribute_name))

    async def _get_attribute(self, attribute_name):
        out = await self._call_method('_get_attribute', attribute_name)
        
        # self.__getattribute__(attribute_name).close()
        self.__setattr__(attribute_name, self._get_attribute(attribute_name))
        
        return out
    
    async def _call_method(self, method, *args, **kwargs):
        new_channel = Channel(self._session)

        encode = lambda x: RemoteController._encode(x, self._session, self._state['_channel']['session'])
        await new_channel.send(self._state['_channel'],
                               {'method': method,
                                'args': await encode(args),
                                'kwargs': await encode(kwargs)}, True)
        
        _, out = await new_channel.recv()
        await new_channel.close()
        
        return self._decode(out['return'], self._session)
    
    @staticmethod
    def _decode(obj_tuple, session):
        obj_type, obj = obj_tuple
        decode = lambda v: RemoteObjectBase._decode(v, session)
        if obj_type in ['int', 'float', 'str', 'bool', 'bytes', 'NoneType']:
            return obj
        if obj_type == 'range':
            return range(*obj)
        if obj_type == 'slice':
            return slice(*obj)
        if obj_type == 'dict':
            return {k: decode(v) for k,v in obj.items()}
        if obj_type == 'list':
            return [decode(v) for v in obj]
        if obj_type == 'tuple':
            return tuple([decode(v) for v in obj])
        if obj_type == 'set':
            return set([decode(v) for v in obj])
        if obj_type == 'object':
            if ro := session.remote_objects.get(str(obj['_channel'])):
                return ro._update_state(obj)
            if channel := session.channels.get(obj['_channel']['channel']):
                pass  # TODO Patch potential security hole -> Un-authorized can interact with target
                # if channel.validate_token_chain()
                for rcs in session.remote_controllers.values():
                    for rc in rcs:
                        if rc._channel is channel:
                            return rc._target 
            return RemoteObjectBase(session, obj)