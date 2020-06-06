import os
import inspect
import re

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
                    serialize_private_key, deserialize_private_key, extend_role

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
    
    async def get_service(self, function_name):
        async with Thread(self.connection) as thread:
            await thread.send({
                'method': 'GET_SERVICE', 
                'function': function_name})
            
            message = await thread.recv()

        return self._create_service_instance(message['signature'], function_name, can_serve=message['can_serve'])

    async def publish_service(self, function_name, function_impl, static_page=None, can_call=None, inject_first_arg=False):
        signature = str(inspect.signature(function_impl))
        if inject_first_arg:
            signature = re.sub(r'[a-zA-Z0-9=\_\s]+(?=[\)\,])', '', signature, 1)\
                          .replace('(,','(',1).replace('( ','(',1)
        message = {'method': 'PUBLISH', 'function': function_name, 'signature': signature, 'can_call': can_call} 
        if static_page is not None:
            message['static'] = static_page

        async with Thread(self.connection) as thread:
            await thread.send(message)
            message = await thread.recv()
            # print(message)

        return self._create_service_instance(signature, function_name, function_impl, True, inject_first_arg)

    def _create_service_instance(self, signature, function_name, function_impl=None, can_serve=False, inject_first_arg=False):
        def create_call():
            return Call(self.connection, function_name)

        async def await_request():
            return await Request(self.connection, function_name)._await_request()

        async def call_service(*args, **kwargs):
            call = create_call()
            return await call.call(*args, **kwargs)

        async def run_service(function_impl=function_impl, inject_first_arg=inject_first_arg):
            if function_impl is None:
                raise Exception('Function to be served has to be specified somewhere.')

            async with Request(self.connection, function_name) as req:
                while True:
                    if inject_first_arg:
                        output = function_impl(req, *req.args, **req.kwargs)
                    else:
                        output = function_impl(*req.args, **req.kwargs)

                    if asyncio.iscoroutinefunction(function_impl):
                        output = await output

                    await req.send_return(output, True)

        async def remove():
            async with Thread(self.connection) as thread:
                await thread.send({'method': 'REMOVE', 'function': function_name})

                await asyncio.sleep(2)

                return await thread.recv()
        
        service = makefun.create_function(signature, call_service)
        service.create_call = create_call

        if can_serve:
            service.run = run_service
            service.remove = remove
            service.await_request = await_request

        return service

    async def list_services(self):
        async with Thread(self.connection) as thread:
            await thread.send({'method': 'LIST'})

            return await thread.recv()

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
        
        return message
    
    async def close(self):
        await self.send({'method': 'CLOSE_THREAD'})
        self.connection.threads.pop(self.thread_id)
    
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

    async def add_role_certificate(self, role_certificate):
        self.role_certificates.append(role_certificate)
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

                thread_id, message = await decode_message(raw_message, lambda tid: self.threads[tid].chunks, self)

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
    
    def close(self):
        # TODO close threads
        self._retry_connect = False
        self.listener.cancel()
        return self.websocket.close()

class Request:
    def __init__(self, connection, function_name):
        self._connection = connection
        self._function_name = function_name
        self._thread = None
        self._reset()
    
    def _reset(self):
        self.call_id = None
        self.args = None
        self.caller_pubkey = None
        self.kwargs = None

    async def _expect_call(self):
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

    async def _await_request(self):
        self._thread = Thread(self._connection)

        await self._thread.send({'method': 'SERVE', 'function': self._function_name})

        return await self._expect_call()

    async def send_return(self, output, get_next_call=False):
        await self.send_update(output, 'return', get_next_call=get_next_call)
        if get_next_call:
            await self._thread.send({'method': 'SERVE', 'function': self._function_name})
            self._reset()
            return await self._expect_call()
    
    async def send_update(self, content, update_type='print', **kwargs):
        message = kwargs

        message.update({
            'method': 'RETURN',
            'call_id': self.call_id,
            update_type: content,
        })
        return await self._thread.send(message)

    async def send_role_extension(self, from_role, sub_role, recipient=None):
        if recipient is None:
            recipient = self.caller_pubkey
        await self.send_update(extend_role(self._connection.private_key, from_role, recipient, sub_role),
                                     'role_extension')

    async def send_input_request(self, prompt=None, hashed=False, salt=None):
        await self.send_update(prompt, 'input_request', hashed=hashed, salt=salt)

        return (await self._thread.recv())['input_response']

    async def close(self):
        return await self._thread.close()

    async def __aenter__(self):
        return await self._await_request()

    async def __aexit__(self, _, __, ___):
        await self.close()

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
                        await self.connection.add_role_certificate(message['role_extension'])
                if 'print' in message:
                    await self.on_update(message['print'])
                if 'return' in message:
                    return message['return']

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
