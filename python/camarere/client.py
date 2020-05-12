import json
import asyncio
import websockets
import time
from .encryption import generate_public_serial, read_private_key, sign

class Client:
    def __init__(self, url='ws://localhost:3388', email=None, private_key_path=None, key_password=None):
        self.url = url
        self.is_connected = lambda: False
        self.server = None
        self.private_key = None
        self.email = email
        if private_key_path is not None:
            self.private_key = read_private_key(private_key_path, key_password)

    async def connect(self):
        self.server = await websockets.connect(self.url+'/client')

        self.is_connected = lambda: not self.server.closed

        if self.private_key is not None: # Authenticate
            pubkey = generate_public_serial(self.private_key)
            timestamp = str(int(time.time()))
            signature = sign(timestamp, self.private_key)

            await self._send({
                'method': 'AUTHENTICATE',
                'email': self.email,
                'pubkey': pubkey,
                'timestamp': timestamp,
                'signature': signature
            })
        else:
            await self._send({'method': 'SKIP_AUTH'})
        print(await self._recv())
        return self
    
    async def close(self):
        return await self.server.close()
    
    async def _send(self, message):
        if not self.is_connected():
            await self.connect()
        return await self.server.send(json.dumps(message))

    async def _recv(self):
        return json.loads(await self.server.recv())
        
    async def __aenter__(self):
        await self.connect()

    async def __aexit__(self, _, __, ___):
        await self.close()
    
    async def publish(self, function, function_name, static_page=None):
        message = {'method': 'PUBLISH', 'function': function_name} 
        if static_page is not None:
            message['static'] = static_page
        await self._send(message)
        message = await self._recv()
        print(message)

    async def supply(self, function, function_name):
        await self._send({'method': 'SUPPLY', 'function': function_name})
        message = await self._recv()
        print(message)
        if not isinstance(message, str):
            while True:
                call = await self._recv()
                print(call)
                if isinstance(call, dict):
                    call['method'] = 'RETURN'
                    if asyncio.iscoroutinefunction(function):
                        call['return'] = await function(*call['args'], **call['kwargs'])
                    else:
                        call['return'] = function(*call['args'], **call['kwargs'])
                    await self._send(call)
                elif isinstance(call, str):
                    break

    async def call(self, function_name, *args, **kwargs):
        await self._send({'method': 'CALL', 'function': function_name, 'args': args, 'kwargs': kwargs})
        
        message = await self._recv()
        print(message)
        if message == 'SERVICE NOT FOUND':
            return None

        return await self._recv()

    async def list(self):
        await self._send({'method': 'LIST'})

        return await self._recv()

    async def remove(self, function_name):
        await self._send({'method': 'REMOVE', 'function': function_name})

        await asyncio.sleep(2)

        return await self._recv()