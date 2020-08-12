import os
import inspect
from functools import partial
import re
import types
import warnings

import json
import asyncio
import websockets
import time
import hashlib

from getpass import getpass
import makefun
from uuid import uuid4
from collections import deque
from makefun import create_function
from .common import sign, generate_public_serial, encode_message, decode_message, create_private_key, \
                    serialize_private_key, deserialize_private_key, extend_role, get_certificate_dependencies, \
                    event_wait, check_function_signature

class Node:
    def __init__(self, url='ws://localhost:3388', auth_file_path=None, key_password=None, connection=None):
        if connection is None:
            self.connection = Connection(url, auth_file_path, key_password)
        else:
            self.connection = connection

    async def connect(self, **kwargs):
        await self.connection.connect(**kwargs)
        return self
    
    async def close(self):
        return await self.connection.close()
    
    async def get(self, function_name):
        async with Thread(self.connection) as thread:
            await thread.send({
                'method': 'GET_SERVICE', 
                'function': function_name})
            
            message = await thread.recv()

        return self._create_service_instance(message['signature'],
                                             function_name,
                                             can_serve=message['can_serve'],
                                             docstring=message['docstring'],
                                             service_type=message['type'])

    async def publish(self, function_name, function_impl, n_workers=1, foreground=False, replace=False, static_page=None, can_call=None, inject_first_arg=False):

        signature = str(inspect.signature(function_impl))

        if inject_first_arg:
            signature = re.sub(r'[a-zA-Z0-9=\_\s]+(?=[\)\,])', '', signature, 1)\
                          .replace('(,','(',1).replace('( ','(',1)

        message = {
            'method': 'PUBLISH', 
            'function': function_name,
            'type': 'function' if isinstance(function_impl, types.FunctionType) else 'object',
            'replace': replace,
            'signature': signature, 
            'docstring': function_impl.__doc__,
            'can_call': can_call if can_call is None or isinstance(can_call[0], list) or isinstance(can_call[0], tuple) else [can_call]
        }

        if static_page is not None:
            message['static'] = static_page

        async with Thread(self.connection) as thread:
            await thread.send(message)
            ret_message = await thread.recv()
            # print(ret_message)

        service = self._create_service_instance(signature, function_name, function_impl, True, inject_first_arg, message['docstring'],
                                             message['type'])
        service.start(n_workers)

        if foreground:
            await asyncio.gather(*service.workers)

        return service

    def _create_service_instance(self, signature, function_name, function_impl=None, can_serve=False, inject_first_arg=False,
                                       docstring=None, service_type='function'):
        def create_call():
            return Call(self.connection, function_name)

        async def await_request():
            return await Request(self.connection, function_name)._await_request()

        async def call_service(*args, **kwargs):
            if service_type == 'function':
                call = create_call()
                return await call.call(*args, **kwargs)
            else:
                ro = RemoteObject(self.connection, function_name, args, kwargs)
                return await ro._start()

        async def run_service(function_impl=function_impl, inject_first_arg=inject_first_arg):
            def get_object_state(function_impl):
                props = {}
                meths = {}

                for d in dir(function_impl):
                    if d[0] != '_':
                        sub = function_impl.__getattribute__(d)
                        if isinstance(sub, types.MethodType):
                            meths[d] = {'signature': str(inspect.signature(sub)),
                                        'docstring': sub.__doc__}
                        else:
                            props[d] = sub
                return props, meths

            if function_impl is None:
                raise Exception('Function to be served has to be specified somewhere.')

            while True:
                async with Request(self.connection, function_name) as req:
                    try:
                        if service_type == 'function':
                            if inject_first_arg:
                                output = function_impl(req, *req.args, **req.kwargs)
                            else:
                                output = function_impl(*req.args, **req.kwargs)

                            if asyncio.iscoroutinefunction(function_impl):
                                output = await output
                            await req.send_return(output)
                        else:
                            if inject_first_arg:
                                obj = function_impl(req, *req.args, **req.kwargs)
                            else:
                                obj = function_impl(*req.args, **req.kwargs)
                            res = None
                            while True:
                                props, meths = get_object_state(obj)
                                await req.send_update(props=props, meths=meths, response=res)
                                m = await req._thread.recv()
                                if 'obj_method' in m:
                                    args = m.get('args') or []
                                    kwargs = m.get('kwargs') or {}
                                    res = obj.__getattribute__(m['obj_method'])(*args, **kwargs)

                                    if asyncio.iscoroutinefunction(obj.__getattribute__(m['obj_method'])):
                                        res = await res
                                if '_close' in m:
                                    # await req.send_update(props=props, meths=meths, response=res, call_return=None)
                                    break
                    except asyncio.exceptions.CancelledError:
                        break
                    except Exception as e:
                        await req.send_update(error=str(e))

        def start(workers, n_workers=1, function_impl=function_impl, inject_first_arg=inject_first_arg):
            for _ in range(n_workers):
                workers.append(asyncio.get_event_loop().create_task(run_service(function_impl, inject_first_arg)))

        async def remove():
            async with Thread(self.connection) as thread:
                await thread.send({'method': 'REMOVE', 'function': function_name})

                await asyncio.sleep(2)

                return await thread.recv()
        
        if check_function_signature(signature):
            raise check_function_signature(signature)
        service = makefun.create_function(signature, call_service, doc=docstring)

        if service_type == 'function':
            service.create_call = create_call

        if can_serve:
            service.run = run_service
            service.workers = []
            service.start = partial(start, service.workers)
            service.stop_all = lambda: [t.cancel() for t in service.workers] and None
            service.remove = remove
            service.await_request = await_request

        return service

    async def list(self):
        async with Thread(self.connection) as thread:
            await thread.send({'method': 'LIST'})

            return await thread.recv()

    async def __aenter__(self):
        return await self.connect()

    async def __aexit__(self, _, __, ___):
        return await self.close()

class Thread:
    def __init__(self, connection):
        while True:
            thread_id = uuid4().hex
            if thread_id not in connection.threads:
                connection.threads[thread_id] = self
                break

        self.event = asyncio.Event()
        self.queue = deque()
        self.chunks = {}
        self.thread_id = thread_id
        self.connection = connection

    async def send(self, message):
        if not self.connection.is_connected():
            await self.connection.connect()
        
        await encode_message(self.thread_id, message, self.connection)

    async def recv(self):
        await self.event.wait()

        if len(self.queue) == 1:
            self.event.clear()

        message = self.queue.pop()

        if 'error' in message:
            raise Exception(message['error'])

        if 'warning' in message:
            warnings.warn(message['warning'])
        
        return message
    
    async def close(self):
        await self.send({'method': 'CLOSE_THREAD'})
        self.connection.threads.pop(self.thread_id, None)
    
    async def __aenter__(self):
        # if not self.connection.is_connected():
        #     await self.connection.connect()
        return self
    
    async def __aexit__(self, _, __, ___):
        await self.close()

class Connection:
    def __init__(self, url='ws://localhost:3388', auth_file=None, key_password=None):
        self.url = url
        self.is_connected = lambda: False
        self.threads = {}
        self._chunks = {}
        self.role_certificates = []
        self.roles = []
        self.auth_file = None
        self.hashed_key_password = None
        self._retry_connect = False

        self.pending_messages = {}
        self.seen_messages = (set(), set())

        self.update_auth_file_params(auth_file, key_password)

        if auth_file is None or not os.path.exists(auth_file):
            self.private_key = create_private_key()
            if auth_file is not None:
                self.save_auth_file()
        else:
            self.load_auth_file()

        self.public_key = generate_public_serial(self.private_key)

        self.websocket = None
        self.listener = None

    async def connect(self, **kwargs):
        for i_retry in range(3):
            try:
                # if self.is_connected():
                    # await self.websocket.close()
                self.websocket = await websockets.connect(self.url+'/ws/', **kwargs)
                self.is_connected = lambda: not self.websocket.closed
                self._retry_connect = True
                break
            except Exception as e:
                await asyncio.sleep(2)
        else:
            raise Exception('Max connection retries reached')

        self.listener = asyncio.get_event_loop().create_task(self._listen())

        await self.authenticate()

        if self.threads:
            async with Thread(self) as thread:
                await thread.send({
                    'method': 'RECOVER_THREADS',
                    'threads': list(self.threads.keys())
                })
                print(await thread.recv())
            # TODO close non recovered threads
        return self
    
    async def authenticate(self):
        async with Thread(self) as thread:
            pubkey = generate_public_serial(self.private_key)
            timestamp = str(int(time.time()))
            signature = sign(timestamp, self.private_key)

            await thread.send({
                'method': 'AUTHENTICATE',
                'pubkey': pubkey,
                'timestamp': timestamp,
                'signature': signature,
                'role_certificates': self.role_certificates
            })
            self.roles = await thread.recv()

    async def add_role_certificate(self, *role_certificates):
        [self.role_certificates.append(rc) for rc in role_certificates]
        await self.authenticate()        

        self.save_auth_file()
    
    def save_auth_file(self, auth_file=None, key_password=None):
        self.update_auth_file_params(auth_file, key_password)

        if self.auth_file is not None:
            with open(self.auth_file, 'w') as fp:
                json.dump({'private_key': serialize_private_key(self.private_key, self.hashed_key_password),
                           'role_certificates': self.role_certificates}, fp)

    def load_auth_file(self, auth_file=None, key_password=None):
        self.update_auth_file_params(auth_file, key_password)

        if self.auth_file is not None:
            with open(self.auth_file, 'r') as fp:
                auth_data = json.load(fp) 

            self.private_key = deserialize_private_key(auth_data['private_key'], self.hashed_key_password)
            self.role_certificates = auth_data['role_certificates']

    def update_auth_file_params(self, auth_file, key_password):
        if auth_file is not None:
            self.auth_file = auth_file

        if key_password is not None:
            if key_password == True:
                key_password = getpass('Private Key Password: ')
            self.hashed_key_password = hashlib.sha256(bytes(key_password, 'utf-8')).hexdigest()

    async def _listen(self):
        i = 0
        try:
            while True:
                raw_message = await self.websocket.recv()
                i += 1

                thread_id, message = await decode_message(raw_message, self)

                if message is None:
                    continue

                if thread_id in self.threads:
                    self.threads[thread_id].queue.appendleft(message)
                    self.threads[thread_id].event.set()
                else:
                    print(self.public_key[102:107], thread_id, message)
        except Exception as e:
            print(self.public_key[102:107], 'connection listen error:', e)
            if self._retry_connect:
                await asyncio.sleep(5)
                asyncio.get_event_loop().create_task(self.connect())
    
    async def close(self):
        for t in self.threads.copy():
            await self.threads[t].close()
            await asyncio.sleep(0.001)

        self._retry_connect = False
        self.listener.cancel()

        # for m in self.pending_messages.copy():
        #     await asyncio.wait_for(self.pending_messages[m].wait(), 3)
        #     # await asyncio.sleep(0.001)

        await self.websocket.close()

class Request:
    def __init__(self, connection, function_name):
        self._function_name = function_name
        self._thread = Thread(connection)
        self.call_id = None
        self.args = None
        self.kwargs = None
        self.caller_pubkey = None

    async def _await_request(self):
        await self._thread.send({'method': 'SERVE', 'function': self._function_name})
        while True:
            message = await self._thread.recv()

            if 'call' in message:
                call = message['call']

                self.call_id = call['call_id']
                self.args = call['args']
                self.kwargs = call['kwargs']
                self.caller_pubkey = call['caller_pubkey']

                return self
            elif 'service_removed' in message:
                print(message['service_removed'])
                return None
            else:
                print('Unexpected message:', message)

    async def send_return(self, output):
        await self.send_update(call_return=output)
    
    async def send_update(self, **kwargs):
        message = kwargs

        message.update({
            'method': 'RETURN',
            'call_id': self.call_id,
        })
        return await self._thread.send(message)

    async def send_role_extension(self, from_role, sub_role, recipient=None):
        if recipient is None:
            recipient = self.caller_pubkey
        await self.send_update(role_extension=[
            *get_certificate_dependencies(self._thread.connection.role_certificates, from_role),
            extend_role(self._thread.connection.private_key, from_role, recipient, sub_role)
        ])

    async def send_input_request(self, prompt=None, hashed=False, salt=None):
        await self.send_update(input_request=prompt, hashed=hashed, salt=salt)

        response = await self._thread.recv()

        if '_close' in response:
            await self.close()
            raise Exception('Client closed call')

        if 'input_response' in response:
            return response['input_response']

    async def close(self):
        return await self._thread.close()

    async def __aenter__(self):
        return await self._await_request()

    async def __aexit__(self, _, __, ___):
        await self.close()

class RemoteObject:
    def __init__(self, connection, function_name, args, kwargs):
        self._connection = connection
        self._function_name = function_name
        self._args = args
        self._kwargs = kwargs
        self._thread = None
        self._call_id = None

    async def _start(self):
        self._thread = Thread(self._connection)
        await self._thread.send({
            'method': 'CALL', 
            'function': self._function_name, 
            'args': self._args, 
            'kwargs': self._kwargs})

        self._call_id = (await self._thread.recv())['call_id']

        await self._await_state_update()
        
        return self
    
    async def _call_method(self, method, *args, **kwargs):
        await self._thread.send({
            'method': 'SEND',
            'call_id': self._call_id,
            'obj_method': method,
            'args': args,
            'kwargs': kwargs
        })

        return await self._await_state_update()

    async def _await_state_update(self):
        while True:
            message = await self._thread.recv()

            if 'call_return' in message:
                await self._close(False)

            if 'print' in message:
                print(message['print'])

            if 'props' in message or 'meths' in message:
                for d in dir(self):
                    if d[0] != '_':
                        self.__delattr__(d)

                for p in message['props']:
                    self.__setattr__(p, message['props'][p])
                
                for m in message['meths']:
                    method = message['meths'][m]

                    func = makefun.create_function(method['signature'], 
                                                partial(self._call_method, m),
                                                func_name=m,
                                                module_name='camarere.client.RemoteObject',
                                                doc=method['docstring'] if method['docstring'] is not None else "")
                    self.__setattr__(m, func)

            if 'response' in message:
                return message['response']
            
            if 'call_return' in message:
                break

            if 'reset' in message:
                await self._start()
                break
    
    async def __aenter__(self):
        return await self._start()
    
    async def __aexit__(self, _, __, ___):
        return await self._close()

    async def _close(self, send_close_signal=True):
        if send_close_signal:
            await self._thread.send({
                'method': 'SEND',
                'call_id': self._call_id,
                '_close': True
            })
        await self._thread.close()

class Call:
    def __init__(self, connection, function_name, accept_role_extensions=True):
        self.connection = connection
        self.function_name = function_name
        self.accept_role_extensions = accept_role_extensions
        self.thread = None
        self.call_id = None

    async def call(self, *args, **kwargs):
        async with Thread(self.connection) as thread:
            self.thread = thread
            await thread.send({
                'method': 'CALL', 
                'function': self.function_name, 
                'args': args, 
                'kwargs': kwargs})
        
            while True:
                try:
                    message = await thread.recv()
                    if 'call_id' in message:
                        self.call_id = message['call_id']
                    if 'input_request' in message:
                        if message['hashed']:
                            await self.on_secret_request(message['input_request'], message['salt'])
                        else:
                            await self.on_input_request(message['input_request'])
                    if 'role_extension' in message:
                        if self.accept_role_extensions:
                            await self.connection.add_role_certificate(*message['role_extension'])
                    if 'print' in message:
                        await self.on_update(message['print'])
                    if 'call_return' in message:
                        return message['call_return']
                except asyncio.CancelledError as e:
                    await self._send(error=str(e))
                    raise e

    async def _send(self, **kwargs):
        kwargs.update({'method': 'SEND', 'call_id': self.call_id})

        return await self.thread.send(kwargs)

    async def on_input_request(self, prompt):
        x = input(prompt)
        await self._send(input_response=x)

    async def on_secret_request(self, prompt, salt=None):
        secret = getpass(prompt)

        x = hashlib.sha256(bytes(secret + (str(salt) if salt is not None else ''), 'utf-8')).hexdigest()
        
        await self._send(input_response=x)

    async def on_update(self, content):
        print(content)
