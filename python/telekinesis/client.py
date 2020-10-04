import logging
import time
import traceback
import os
import asyncio
import bson
from collections import deque

import websockets
import ujson

from .cryptography import PrivateKey, PublicKey, SharedKey, Token

class Connection:
    def __init__(self, session, url='ws://localhost:8776'):
        self.session = session
        self.url = url
        self.logger = logging.getLogger(__name__)
        self.websocket = None
        self.t_offset = 0
        self.MAX_PAYLOAD_LEN = 2**19
        self.broker_id = None
        self.lock = asyncio.Event()
        session.connections.add(self)

        self.listener = asyncio.get_event_loop().create_task(self.listen())

    async def reconnect(self):
        if self.lock.is_set():
            self.lock.clear()
            if self.listener:
                self.listener.cancel()

            self.listener = asyncio.get_event_loop().create_task(self.listen())

        await self.lock.wait()

    async def _connect(self):
        if self.websocket:
            await self.websocket.close()

        self.websocket = await websockets.connect(self.url)
        
        challenge = await self.websocket.recv()
        t_broker = int.from_bytes(challenge[-4:], 'big')
        
        self.t_offset = int(time.time()) - t_broker
        signature = self.session.session_key.sign(challenge)

        pk = self.session.session_key.public_serial().encode()

        sent_challenge = os.urandom(32)
        await self.websocket.send(signature + pk + sent_challenge)

        m = await asyncio.wait_for(self.websocket.recv(), 15)

        broker_signature, broker_id = m[:64], m[64:152].decode()
        PublicKey(broker_id).verify(broker_signature, sent_challenge)

        self.broker_id = broker_id
        
        self.lock.set()

        return self
        
    async def send(self, header, payload=b''):
        for action, _ in header:
            self.logger.info('%s Sending %s: %s', self.session.session_key.public_serial()[:4],
                             action, len(payload) if action == 'send' else 0)
        if not self.websocket or self.websocket.closed:
            if self.lock.is_set():
                await self.reconnect()
            else:
                await self.lock.wait()

        h = ujson.dumps(header).encode()
        m = len(h).to_bytes(2, 'big') + len(payload).to_bytes(3, 'big') + h + payload
        t = int(time.time() - self.t_offset - 4).to_bytes(4, 'big')
        s = self.session.session_key.sign(t + m,)
        
        await self.websocket.send(s + t + m)

    async def listen(self):
        n_tries = 0
        while True:
            try:
                await self._connect()
                while True:
                    await self.recv()
                    n_tries = 0
            except Exception:
                self.logger.error('Connection.listen', exc_info=True)
            
            self.lock.clear()
            await asyncio.sleep(1)

            if n_tries > 10:
                raise Exception('Max tries reached')
            n_tries += 1
            
    async def recv(self):
        if not self.websocket or self.websocket.closed:
            if self.lock.is_set():
                await self.reconnect()
            else:
                await self.lock.wait()

        message = await self.websocket.recv()

        signature, timestamp = message[:64], int.from_bytes(message[64:68], 'big')
        
        if self.session.check_no_repeat(signature, timestamp + self.t_offset):

            len_h, len_p = [int.from_bytes(x, 'big') for x in [message[68:70], message[70:73]]]
            header = ujson.loads(message[73:73+len_h])
            payload = message[73+len_h:73+len_h+len_p]
            for action, content in header:
                self.logger.info('%s Received %s: %s', self.session.session_key.public_serial()[:4], 
                                 action, len(payload) if action == 'send' else 0)
                if action == 'send':
                    source, destination = Route(**content['source']), Route(**content['destination'])
                    PublicKey(source.session).verify(signature, message[64:])
                    if self.session.channels.get(destination.channel):
                        channel = self.session.channels.get(destination.channel)
                        channel.handle_message(source, destination, payload)

class Session:
    def __init__(self, session_key_file=None, compile_signatures=True):
        self.session_key = PrivateKey(session_key_file)
        self.channels = {}
        self.connections = set()
        self.compile_signatures = compile_signatures
        self.seen_messages = (set(), set(), 0)
        self.issued_tokens = {}

    def check_no_repeat(self, signature, timestamp):
        now = int(time.time())

        lead = now//60%2
        if self.seen_messages[2] != lead:
            self.seen_messages[lead].clear()

        if (now - 60) <= timestamp <= now:
            if signature not in self.seen_messages[0].union(self.seen_messages[1]):
                self.seen_messages[lead].add(signature)
                return True
        return False

    def issue_token(self, asset, receiver, token_type, max_depth=None):
        token = Token(self.session_key.public_serial(), receiver, asset, token_type, max_depth)

        token_tuple = (token.sign(self.session_key), token.to_dict())
        
        self.issued_tokens[token_tuple[0]] = token
        return ('token', ('issue', token_tuple))

    def revoke_token(self, token_id):
        self.issued_tokens.pop(token_id)
        return ('token', ('revoke', token_id))
    
    def extend_route(self, route, receiver, max_depth=None):

        if route.session == self.session_key.public_serial():
            token_header = self.issue_token(route.channel, receiver, 'root', max_depth)
            route.tokens = [token_header[1][1]]
            return token_header

        for i, (_, token) in enumerate(route.tokens):
            if token['receiver'] == self.session_key.public_serial():
                route.tokens = route.tokens[:i+1]

        token_header = self.issue_token(route.tokens[-1][0], receiver, 'extension', max_depth)
        route.tokens.append(token_header[1][1])
        return token_header
    
    async def send(self, header, payload=b''):
        for connection in self.connections:
            await connection.send(header, payload)

class Channel:
    def __init__(self, session, channel_key_file=None, is_public=False):
        self.session = session
        self.channel_key = PrivateKey(channel_key_file)
        self.is_public = is_public
        self.route = Route(list(set(c.broker_id for c in self.session.connections)),
                           self.session.session_key.public_serial(),
                           self.channel_key.public_serial())
        self.header_buffer = []
        self.chunks = {}
        self.messages = deque()
        self.lock = asyncio.Event()
        session.channels[self.channel_key.public_serial()] = self

        self.telekinesis = None
    
    def route_dict(self):
        return self.route.to_dict()

    def handle_message(self, source, destination, raw_payload):
        if self.validate_token_chain(source.session, destination.tokens):

            shared_key = SharedKey(self.channel_key, PublicKey(source.channel))
            payload = shared_key.decrypt(raw_payload[16:], raw_payload[:16])


            if payload[:4] == b'\x00'*4:
                self.messages.appendleft((source, bson.loads(payload[4:])))
                self.lock.set()
            else:
                ir, nr, mid, chunk = payload[:2], payload[2:4], payload[4:8], payload[8:]
                i, n = int.from_bytes(ir, 'big'), int.from_bytes(nr, 'big')
                if mid not in self.chunks:
                    self.chunks[mid] = {}
                self.chunks[mid][i] = chunk

                if len(self.chunks[mid]) == n:
                    chunks = self.chunks.pop(mid)
                    self.messages.appendleft((source, bson.loads(b''.join(chunks[ii] for ii in range(n)))))
                    self.lock.set()
    
    async def recv(self):
        if not self.messages:
            self.lock.clear()
            await self.lock.wait()
        
        return self.messages.pop()
    
    def listen(self):
        listen_dict = self.route.to_dict()
        listen_dict['is_public'] = self.is_public
        listen_dict.pop('tokens')
        self.header_buffer.append(('listen', listen_dict))

        return self
    
    async def send(self, destination, payload_obj, allow_reply=False):
        source_route = self.route.clone()
        if allow_reply:
            self.header_buffer.append(self.session.extend_route(source_route, destination.session))
            self.listen()
        
        payload = bson.dumps(payload_obj)

        max_payload = list(self.session.connections)[0].MAX_PAYLOAD_LEN

        if len(payload) < max_payload:
            chunks = [b'\x00'*4 + payload]
        else:
            mid = os.urandom(4)
            n = (len(payload) - 1) // max_payload + 1
            if n > 2**16:
                raise Exception(f'Payload size {len(payload)/2**20} MiB too large')
            chunks = [i.to_bytes(2, 'big') + n.to_bytes(2, 'big') + mid + payload[i*max_payload: (i+1)*max_payload]
                      for i in range(n)]

        tasks = []
        shared_key = SharedKey(self.channel_key, PublicKey(destination.channel))

        header = ('send', {'source': source_route.to_dict(), 'destination': destination.to_dict()})
        for chunk in chunks:
            nonce = os.urandom(16)
            raw_payload = nonce + shared_key.encrypt(chunk, nonce)

            tasks.append(self.execute(header, raw_payload))
        
        await asyncio.gather(*tasks)

        return self

    async def execute(self, header=None, payload=b''):
        await self.session.send([h for h in (self.header_buffer + [header]) if h], payload)
        self.header_buffer = []

        return self

    def __await__(self):
        return self.execute().__await__()

    def close(self):
        self.header_buffer.append(('close', self.route.to_dict()))

        return self

    def validate_token_chain(self, source_id, tokens):
        if self.is_public or (source_id == self.session.session_key.public_serial()):
            return True
        if not tokens:
            return False

        asset = self.channel_key.public_serial()
        last_receiver = self.session.session_key.public_serial()
        max_depth = None
        
        for depth, token_tuple in enumerate(tokens):
            token = Token(**token_tuple[1])
            if token.verify_signature(token_tuple[0]):
                if (token.asset == asset) and (token.issuer == last_receiver):
                    if token.issuer == self.session.session_key.public_serial():
                        if token_tuple[0] not in self.session.issued_tokens:
                            return False
                    if token.max_depth:
                        if not max_depth or (token.max_depth + depth) < max_depth:
                            max_depth = token.max_depth + depth
                    if not max_depth or depth <= max_depth:
                        last_receiver = token.receiver
                        asset = token_tuple[0]
                        if last_receiver == source_id:
                            return True
                        continue
            return False
        return False

class Route:
    def __init__(self, brokers, session, channel, tokens=None):
        self.brokers = brokers
        self.session = session
        self.channel = channel
        self.tokens = tokens or []

    def to_dict(self):
        return {
            'brokers': self.brokers,
            'session': self.session,
            'channel': self.channel,
            'tokens': self.tokens
        }

    def clone(self):
        return Route(**self.to_dict())
