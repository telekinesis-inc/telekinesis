import json
import asyncio
import websockets
import time
from uuid import uuid4
from collections import deque
from .encryption import generate_public_serial, read_private_key, sign

class Client:
    def __init__(self, url='ws://localhost:3388', private_key_path=None, key_password=None, connection=None):
        if connection is None:
            self.c = Connection(url, private_key_path, key_password)
        else:
            self.c = connection

    async def connect(self):
        await self.c.connect()
        return self
    
    async def close(self):
        return await self.c.close()
    
    async def call(self, function_name, *args, **kwargs):
        async with Thread(self.c) as thread:
            await thread.send({
                'method': 'CALL', 
                'function': function_name, 
                'args': args, 
                'kwargs': kwargs})
            
            message = await thread.recv()
            print(message)
            if message == 'SERVICE NOT FOUND':
                return None

            return await thread.recv()

    async def list(self):
        async with Thread(self.c) as thread:
            await thread.send({'method': 'LIST'})

            return await thread.recv()

class Server:
    def __init__(self, url='ws://localhost:3388', private_key_path=None, key_password=None, connection=None):
        if connection is None:
            self.c = Connection(url, private_key_path, key_password)
        else:
            self.c = connection

    async def connect(self):
        await self.c.connect()
        return self
    
    async def close(self):
        return await self.c.close()
    
    async def publish(self, function_name, static_page=None):
        message = {'method': 'PUBLISH', 'function': function_name} 
        if static_page is not None:
            message['static'] = static_page
        async with Thread(self.c) as thread:
            await thread.send(message)
            message = await thread.recv()
            print(message)

    async def serve(self, function, function_name):
        async with Thread(self.c) as thread:
            await thread.send({'method': 'SERVE', 'function': function_name})
            message = await thread.recv()
            print(message)
            if not isinstance(message, str):
                while True:
                    call = await thread.recv()
                    print(call)
                    if isinstance(call, dict):
                        call['method'] = 'RETURN'
                        if asyncio.iscoroutinefunction(function):
                            call['return'] = await function(*call['args'], **call['kwargs'])
                        else:
                            call['return'] = function(*call['args'], **call['kwargs'])
                        await thread.send(call)
                    elif isinstance(call, str):
                        break

    async def remove(self, function_name):
        async with Thread(self.c) as thread:
            await thread.send({'method': 'REMOVE', 'function': function_name})

            await asyncio.sleep(2)

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
        dump = json.dumps(message)
        ws = self.connection.hub
        thread_id = self.thread_id

        if len(dump) >= (2**20-32):
            chunk_id = uuid4().hex
            n = (len(dump)-1)//(2**20-72) + 1
            for i in range(n):
                await ws.send(thread_id+'.'+chunk_id+'%03d'%i+'%03d'%n+'.'+\
                              dump[(i*(2**20-72)):((i+1)*(2**20-72))])
        else:
            await ws.send(thread_id+dump)

    async def recv(self):
        await self.event.wait()

        if len(self.queue) == 1:
            self.event.clear()

        return self.queue.pop()
    
    async def close(self):
        await self.send({'method': 'CLOSE_THREAD'})
        self.connection.threads.pop(self.thread_id)
    
    async def __aenter__(self):
        if not self.connection.is_connected():
            await self.connection.connect()
        return self
    
    async def __aexit__(self, _, __, ___):
        await self.close()

class Connection:
    def __init__(self, url='ws://localhost:3388', private_key_path=None, key_password=None):
        self.url = url
        self.is_connected = lambda: False
        self.threads = {}

        if private_key_path is None:
            self.private_key = None
        else:
            self.private_key = read_private_key(private_key_path, key_password)
        self.hub = None
        self.listener = None

    async def connect(self):
        self.hub = await websockets.connect(self.url+'/ws/')
        self.is_connected = lambda: not self.hub.closed

        self.listener = asyncio.create_task(self._listen())

        async with Thread(self) as thread:
            if self.private_key is not None: # Authenticate
                pubkey = generate_public_serial(self.private_key)
                timestamp = str(int(time.time()))
                signature = sign(timestamp, self.private_key)

                await thread.send({
                    'method': 'AUTHENTICATE',
                    'pubkey': pubkey,
                    'timestamp': timestamp,
                    'signature': signature
                })
            else:
                await thread.send({'method': 'SKIP_AUTH'})
            print(await thread.recv())
        return self
    
    async def _listen(self):
        while True:
            raw_message = await self.hub.recv()
            thread_id = raw_message[:32]

            if raw_message[32] != '.':
                message = json.loads(raw_message[32:])
            else:
                chunk_id = raw_message[33:65]
                chunk_x = int(raw_message[65:68])
                chunk_n = int(raw_message[68:71])

                chunk = raw_message[72:]

                chunks = self.threads[thread_id].chunks

                if chunk_id not in chunks:
                    chunks[chunk_id] = {}
                
                chunks[chunk_id][chunk_x] = chunk

                if len(chunks[chunk_id]) < chunk_n:
                    continue
                
                d = chunks.pop(chunk_id)
                message = json.loads(''.join([d[i] for i in range(chunk_n)]))

            if thread_id in self.threads:
                self.threads[thread_id].queue.appendleft(message)
                self.threads[thread_id].event.set()
            else:
                print(thread_id, message)
    
    def close(self):
        self.listener.cancel()
        return self.hub.close()
