import json
import asyncio
import websockets
import time
from .encryption import generate_public_serial, read_private_key, sign

class Client:
    def __init__(self, url='ws://localhost:3388', private_key_path=None, password=None):
        self.url = url
        self.is_connected = lambda: False
        self.server = None
        self.private_key = None
        if private_key_path is not None:
            self.private_key = read_private_key(private_key_path, password)

    async def connect(self, user='', password=''):
        if not user and not password and self.private_key is not None:
            user = generate_public_serial(self.private_key)[:-2] #remove padding
            timestamp = str(int(time.time()))
            signature = sign(timestamp, self.private_key)[:-1]
            password = timestamp + signature
        url = self.url.split('//')[0]+'//'+user+':'+password+'@'+'//'.join(self.url.split('//')[1:])
        self.server = await websockets.connect(url)
        self.is_connected = lambda: not self.server.closed
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
    
    async def publish(self, function, function_name, offer=True):
        await self._send({'method': 'PUBLISH', 'function': function_name})
        message = await self._recv()
        print(message)
        # if message == 'OK' and offer:
        #     print('hi')
        #     return await self.offer(function, function_name)

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


    async def call(self, function_name, *args, **kwargs):
        await self._send({'method': 'CALL', 'function': function_name, 'args': args, 'kwargs': kwargs})
        print(await self._recv())

        return await self._recv()