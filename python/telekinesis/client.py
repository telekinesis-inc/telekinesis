import ujson
import time
from collections import deque

import websockets
import traceback
import os
import asyncio
import bson

from .cryptography import PrivateKey, PublicKey, SharedKey, Token

class Connection:
    def __init__(self, session, url='ws://localhost:2020', logger=None):
        self.session = session
        self.url = url
        self.logger = logger or print
        self.websocket = None
        self.t_offset = 0
        self.listener = None
        self.MAX_PAYLOAD_LEN = 2**19
        self.broker_id = None
        session.connections.add(self)

    async def connect(self):
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
        self.listener = asyncio.create_task(self.listen())
        
        return self
        
    async def send(self, header, payload=b''):
        if not self.websocket:
            await self.connect()

        h = ujson.dumps(header).encode()
        m = len(h).to_bytes(2, 'big') + len(payload).to_bytes(3, 'big') + h + payload
        t = int(time.time() - self.t_offset - 4).to_bytes(4, 'big')
        s = self.session.session_key.sign(t + m,)
        
        await self.websocket.send(s + t + m)

    async def listen(self):
        n_tries = 0
        while True:
            try:
                while True:
                    await self.recv()
                    n_tries = 0
            except Exception:
                self.logger(traceback.format_exc())
            
            await asyncio.sleep(1)
            if n_tries > 10:
                raise Exception('Max tries reached')
            n_tries += 1
            
    async def recv(self):
        message = await self.websocket.recv()

        signature, timestamp = message[:64], int.from_bytes(message[64:68], 'big')
        
        if self.session.check_no_repeat(signature, timestamp + self.t_offset):

            len_h, len_p = [int.from_bytes(x, 'big') for x in [message[68:70], message[70:73]]]
            header = ujson.loads(message[73:73+len_h])
            payload = message[73+len_h:73+len_h+len_p]
            if (action := header.get('send')):
                PublicKey(action['source']['session']).verify(signature, message[64:])
                if (channel := self.session.channels.get(action['destination']['channel'])):
                    await channel.handle_message(action['source'], action['destination'], payload)

class Session:
    def __init__(self, session_key_file=None):
        self.session_key = PrivateKey(session_key_file)
        self.channels = {}
        self.connections = set()
        self.seen_messages = (set(), set(), 0)
        self.issued_tokens = {}
        self.remote_controllers = {}
        self.remote_objects = {}
    
    def check_no_repeat(self, signature, timestamp):
        now = int(time.time())

        if self.seen_messages[2] != (lead := now//60%2):
            self.seen_messages[lead].clear()

        if (now - 60) <= timestamp <= now:
            if signature not in self.seen_messages[0].union(self.seen_messages[1]):
                self.seen_messages[lead].add(signature)
                return True
        return False

    async def issue_token(self, asset, receiver, token_type, max_depth=None, send=True):
        token = Token(self.session_key.public_serial(), receiver, asset, token_type, max_depth)

        token_tuple = (token.sign(self.session_key), token.to_dict())
        
        self.issued_tokens[token_tuple[0]] = token

        if send:
            for connection in self.connections:
                await connection.send({'tokens': ['issue', token_tuple]})
        return token_tuple

    async def revoke_token(self, token_id, send=True):
        token = self.issued_tokens.pop(token_id)
        if send:
            for connection in self.connections:
                await connection.send({'tokens': ['revoke', token_id]})
        return token
    
    async def extend_route(self, route, receiver):
        token_tuple = await self.issue_token(route['tokens'][-1][0], receiver, 'extension')
        route['tokens'].append(token_tuple)
        return route

class Channel:
    def __init__(self, session, channel_key_file=None, is_public=False):
        self.session = session
        self.channel_key = PrivateKey(channel_key_file)
        self.is_public = is_public
        self.chunks = {}
        self.messages = deque()
        self.lock = asyncio.Event()
        session.channels[self.channel_key.public_serial()] = self
    
    def route_dict(self):
        return {
            'brokers': list(set(c.broker_id for c in self.session.connections)),
            'session': self.session.session_key.public_serial(),
            'channel': self.channel_key.public_serial()}

    async def handle_message(self, source, destination, raw_payload):
        if self.validate_token_chain(source['session'], destination.get('tokens')):

            shared_key = SharedKey(self.channel_key, PublicKey(source['channel']))
            payload = shared_key.decrypt(raw_payload[16:], raw_payload[:16])

            if payload[:4] == b'\x00'*4:
                self.messages.appendleft((source, bson.loads(payload[4:])))
                self.lock.set()
            else:
                raise NotImplementedError
    
    async def recv(self):
        if not self.messages:
            self.lock.clear()
            await self.lock.wait()
        
        return self.messages.pop()
    
    async def listen(self):
        for connection in self.session.connections:
            listen_dict = self.route_dict()
            listen_dict['is_public'] = self.is_public
            await connection.send({'listen': listen_dict})

        return self
    
    async def send(self, destination, payload_obj, allow_reply=False):
        headers = {
            'send': {
                'source': self.route_dict(),
                'destination': destination}}

        if allow_reply:
            token_tuple = await self.session.issue_token(self.channel_key.public_serial(), 
                                                         destination['session'], 
                                                         'root', send=False)
            headers['listen'] = self.route_dict() 
            headers['tokens'] = ['issue', token_tuple]
            headers['send']['source']['tokens'] = [token_tuple]
        
        payload = bson.dumps(payload_obj)

        if len(payload) > list(self.session.connections)[0].MAX_PAYLOAD_LEN:
            raise NotImplementedError('Payload too large')
        
        shared_key = SharedKey(self.channel_key, PublicKey(destination['channel']))
        nonce = os.urandom(16)
        raw_payload = nonce + shared_key.encrypt(b'\x00'*4 + payload, nonce)
        
        for connection in self.session.connections:
            await connection.send(headers, raw_payload)
        
        return self

    async def close(self):
        for connection in self.session.connections:
            await connection.send({'close': self.route_dict()})

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
                        continue
            return False
        if last_receiver == source_id:
            return True
        return False
