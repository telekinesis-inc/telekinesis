import json
import asyncio
import websockets
import time
import uuid
from .encryption import generate_public_serial, read_private_key, sign

class Connection:
    def __init__(self, url='ws://localhost:3388', private_key_path=None, key_password=None):
        self.url = url
        self.is_connected = lambda: False
        if private_key_path is None:
            self.private_key = None
        else:
            self.private_key = read_private_key(private_key_path, key_password)
        self.hub = None

    async def connect(self):
        self.hub = await websockets.connect(self.url+'/ws/')
        self.is_connected = lambda: not self.hub.closed

        if self.private_key is not None: # Authenticate
            pubkey = generate_public_serial(self.private_key)
            timestamp = str(int(time.time()))
            signature = sign(timestamp, self.private_key)

            await self.send({
                'method': 'AUTHENTICATE',
                'pubkey': pubkey,
                'timestamp': timestamp,
                'signature': signature
            })
        else:
            await self.send({'method': 'SKIP_AUTH'})
        print(await self.recv())
        return self
    
    async def send(self, message, thread_id=None):
        if not self.is_connected():
            await self.connect()
        return await self.hub.send(json.dumps(message))
    
    async def recv(self, thread_id=None):
        return json.loads(await self.hub.recv())
    
    def close(self):
        return self.hub.close()

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
        await self.c.send({'method': 'CALL', 'function': function_name, 'args': args, 'kwargs': kwargs})
        
        message = await self.c.recv()
        print(message)
        if message == 'SERVICE NOT FOUND':
            return None

        return await self.c.recv()

    async def list(self):
        await self.c.send({'method': 'LIST'})

        return await self.c.recv()

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
    
    async def publish(self, function, function_name, static_page=None):
        message = {'method': 'PUBLISH', 'function': function_name} 
        if static_page is not None:
            message['static'] = static_page
        await self.c.send(message)
        message = await self.c.recv()
        print(message)

    async def serve(self, function, function_name):
        await self.c.send({'method': 'SERVE', 'function': function_name})
        message = await self.c.recv()
        print(message)
        if not isinstance(message, str):
            while True:
                call = await self.c.recv()
                print(call)
                if isinstance(call, dict):
                    call['method'] = 'RETURN'
                    if asyncio.iscoroutinefunction(function):
                        call['return'] = await function(*call['args'], **call['kwargs'])
                    else:
                        call['return'] = function(*call['args'], **call['kwargs'])
                    await self.c.send(call)
                elif isinstance(call, str):
                    break

    async def remove(self, function_name):
        await self.c.send({'method': 'REMOVE', 'function': function_name})

        await asyncio.sleep(2)

        return await self.c.recv()
