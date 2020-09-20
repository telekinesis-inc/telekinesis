import os
import inspect
import types
import asyncio
from functools import partial

import makefun
import bson

from .client import Channel

class RemoteController:
    def __init__(self, target, channel, mask=None, logger=None):
        self._target = target
        self._channel = channel
        self._istype = isinstance(target, type)
        self._mask = mask or set()
        self._state = {}
        if '__call__' in dir(target):
#             try:
            self._signature = str(inspect.signature(target))  
#             except:
#                 self._signature = None
        elif self._istype or isinstance(target, types.FunctionType):
            self._signature = '(self, '+ str(inspect.signature(target))[1:]
        else:
            self._signature = None
        self._update_state()
        self._task = asyncio.create_task(self._listen())
        self._children = set()
        self._logger = logger or print

    def _update_state(self):
        self._state = {}
        
        target = self._target
        
        for attribute_name in dir(target):
            if attribute_name[0] != '_' and attribute_name not in self._mask:
                if self._istype:
                    target_attribute = target.__getattribute__(target, attribute_name)
                else:
                    target_attribute = target.__getattribute__(attribute_name)
                
                if type(target_attribute) in (int, float, str, bool, bytes, range, type(None)):
                    attribute_type = 'value'
                    attribute_value = target_attribute
                elif '__call__' in dir(target_attribute):
                    attribute_type = 'method'
                    try:
                        attribute_value = str(inspect.signature(target_attribute))
                    except:
                        attribute_value = None
                else:
                    attribute_type = 'on_demand'
                    attribute_value = None
                self._state[attribute_name] = (attribute_type, attribute_value)
        
        if self._istype:
            self._state['_repr'] = ('value', (str(type(target))))
        else:
            self._state['_repr'] = ('value', target.__repr__())
    
    def _call_method(self, method, args, kwargs):
        self._logger('calling', method, 'args', args, 'kwargs', kwargs)
        
        if method[0] != '_' \
        or method in ('__call__', '__getitem__', '__setitem__', '__add__', 
                      '__mul__', '__repr__', '__iter__', '__next__', '_get_on_demand_attribute') \
        and method not in self._mask:
            if method == '_get_on_demand_attribute' and args[0][0] != '_':
                call_return = self._target.__getattribute__(args[0])
            else:
                if self._istype:
                    call_return = self._target.__getattribute__(self._target, method)(*args, **kwargs)
                else:
                    call_return = self._target.__getattribute__(method)(*args, **kwargs)
            
            self._update_state()
            
            if call_return is self._target:
                return self
            return call_return
        
        raise PermissionError('Calling a private method', method,'of a RemoteObject is not permitted')
    
    async def _listen(self):
        await self._channel.listen()
        while True:
            await self._onmessage(*(await self._channel.recv()))
        
    
    async def _close(self):
        await self._channel.close()
        
    async def _onmessage(self, reply, payload_raw):
        try:
            if payload := bson.loads(payload_raw):
                call_return = self._call_method(**payload)

                if type(call_return) in (int, float, str, bool, bytes, range, type(None)):
                    output_type = 'value'
                    output_value = call_return
                elif call_return is self:
                    output_type = 'self'
                    output_value = None
                else:
                    receiver = RemoteController(call_return, 
                                                Channel(self._channel.session), 
                                                mask=self._mask,
                                                logger=self._logger)
                    self._children.add(receiver)
                    output_type = 'remote_object'
                    output_value = {
                        'call_signature': receiver._signature,
                        'initial_state': receiver._state,
                        'channel': receiver._channel.route_dict()
                    }
            else:
                output_type = 'remote_object'
                output_value = {
                    'call_signature': self._signature,
                    'initial_state': self._state,
                    'channel': self._channel.route_dict()
                }
            await self._channel.send(reply, bson.dumps({'return_type': output_type, 
                                                        'return_value': output_value, 
                                                        'attributes': self._state}))
        except Exception as e:
            self._logger(e, type(e))

async def spawn(session, destination):
    new_channel = Channel(session)
    
    await new_channel.send(destination, bson.dumps({}), True)
    _, out = await new_channel.recv()
    
    await new_channel.close()
    
    return RemoteObjectBase(session, destination)._spawn(**bson.loads(out)['return_value'])

class RemoteObjectBase:
    def __init__(self, session, destination):
        self._session = session
        self._destination = destination
        self._repr = ''
        self._tasks = set()
      
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
        ds, dc = self._destination['session'][:4], self._destination['channel'][:4]
        return f'Remote Object -> ({ds} {dc}): {self._repr}'
    
    def _update_state(self, attributes):
        if attributes:
            for d in dir(self):
                if d[0] != '_':
                    att = self.__getattribute__(d)
                    if asyncio.iscoroutine(att):
                        try:
                            att.close()
                        except:
                            pass
                    self.__delattr__(d)

            for attribute_name in attributes:
                attribute_type, attribute_value = attributes[attribute_name]
                    
                if attribute_type == 'value':
                    self.__setattr__(attribute_name, attribute_value)

                if attribute_type == 'method':
                    attribute_value = attribute_value or '(*args, **kwargs)'
                    method = makefun.create_function(attribute_value, 
                                                     partial(self._call_method, attribute_name),
                                                     func_name=attribute_name,
                                                     module_name='telekinesis.client.RemoteObject')
                    self.__setattr__(attribute_name, method)

                if attribute_type == 'on_demand':
                    self.__setattr__(attribute_name, self._get_on_demand_attribute(attribute_name))
    
    def _spawn(self, call_signature, initial_state, channel):
        
        class RemoteObject(RemoteObjectBase):
            @makefun.with_signature(call_signature)
            async def __call__(self, *args, **kwargs):
                return await super().__call__(*args, **kwargs)
        
        new_remote_obj = RemoteObject(self._session, channel)
        new_remote_obj._update_state(initial_state)
        
        return new_remote_obj
    
    async def _get_on_demand_attribute(self, attribute_name):
        out = await self._call_method('_get_on_demand_attribute', attribute_name)
        
        self.__getattribute__(attribute_name).close()
        self.__setattr__(attribute_name, self._get_on_demand_attribute(attribute_name))
        
        return out
        
    async def _call_method(self, method, *args, **kwargs):
        new_channel = Channel(self._session)
        await new_channel.send(self._destination,
                                     bson.dumps({'method': method,
                                                   'args': args,
                                                   'kwargs': kwargs}), True)
        _, out = await new_channel.recv()
        await new_channel.close()
        
        o = bson.loads(out)
        
        self._update_state(o['attributes'])
        
        if o['return_type'] == 'value':
            return o['return_value']
        if o['return_type'] == 'remote_object':
            return self._spawn(**o['return_value'])
        if o['return_type'] == 'self':
            return self
