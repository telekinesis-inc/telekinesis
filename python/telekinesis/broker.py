import os
import asyncio
import time
import traceback
import base64

import ujson
import websockets

from .cryptography import PrivateKey, PublicKey, Token

class Connection:
    def __init__(self, websocket):
        self.websocket = websocket
        self.session = None
        self.channels = set()

    async def handshake(self, sessions, broker_key):
        challenge = os.urandom(32) + int(time.time()).to_bytes(4, 'big')
        
        await self.websocket.send(challenge)
        m = await asyncio.wait_for(self.websocket.recv(), 15)
        
        signature, session_id, client_challenge = m[:64], m[64:152].decode(), m[152:184]
        PublicKey(session_id).verify(signature, challenge)

        await self.websocket.send(broker_key.sign(client_challenge) + broker_key.public_serial().encode())

        if session_id not in sessions:
            sessions[session_id] = Session(session_id)
        
        self.session = sessions[session_id]
        self.session.connections.add(self)

        return self

class Session:
    def __init__(self, session_id):
        self.session_id = session_id
        self.channels = {}
        self.connections = set()
        self.broker_connections = set()
        self.active_tokens = set()

class Channel:
    def __init__(self, session, channel_id, is_public):
        self.session = session
        self.channel_id = channel_id
        self.is_public = is_public
        self.connections = set()

    def close(self):
        for connection in self.connections:
            connection.channels.remove(self)
        
        self.session.channels.pop(self.channel_id)
    
    async def validate_token_chain(self, source_id, tokens, active_tokens):
        if (source_id == self.session.session_id) or self.is_public:
            return True
        
        if not tokens:
            return False

        asset = self.channel_id
        last_receiver = self.session.session_id
        max_depth = None
        
        for depth, token_tuple in enumerate(tokens):
            if active_tokens.get(token_tuple[0]):
                token = active_tokens.get(token_tuple[0])
                if (token.asset == asset) and (token.issuer == last_receiver):
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

class Broker:
    def __init__(self, broker_key_file=None, logger=None):
        self.tokens = {}
        self.sessions = {}
        self.brokers = {}
        self.servers = {}
        self.broker_key = PrivateKey(broker_key_file)
        self.logger = logger or print
        self.seen_messages = (set(), set(), 0)

    async def handle_connection(self, websocket, _):
        connection = None
        try:
            connection = await Connection(websocket).handshake(self.sessions, self.broker_key)

            async for message in websocket:
                if self.check_no_repeat(message):
                    await self.handle_message(connection, message)

        except Exception:
            self.logger(traceback.format_exc())

        finally:
            if connection:
                for channel in connection.channels:
                    channel.connections.remove(connection)
                    if not channel.connections:
                        connection.session.channels.pop(channel.channel_id)

                connection.session.connections.remove(connection)

                if connection in connection.session.broker_connections:
                    connection.session.broker_connections.remove(connection)

                if not connection.session.channels and not connection.session.broker_connections:
                    session = self.sessions.pop(connection.session.session_id)
                    for token_id in session.active_tokens:
                        self.tokens.pop(token_id)

    async def handle_message(self, connection, message):
        headers = self.decode_header(message)
        if headers.get('listen'):
            self.handle_listen(connection, **headers.get('listen'))
        if headers.get('tokens'):
            await self.handle_tokens(connection, headers.get('tokens'))
        if headers.get('broker'):
            self.handle_broker_action(connection, headers.get('broker'))
        if headers.get('send'):
            await self.handle_send(connection, message, **headers.get('send'))
        if headers.get('close'):
            self.handle_close(connection, **headers.get('close'))

    async def handle_send(self, connection, message, source, destination):
        if self.sessions.get(destination['session']):
            dest_session = self.sessions.get(destination['session'])
            if dest_session.channels.get(destination['channel']):
                dest_channel = dest_session.channels.get(destination['channel'])
                if await dest_channel.validate_token_chain(source['session'], destination.get('tokens'), self.tokens):
                    self.logger(source['session'][:4], source['channel'][:4],
                        '>>>', len(message)//2**10, '>>>',
                        destination['session'][:4], destination['channel'][:4])

                    for connection in dest_channel.connections:
                        await connection.websocket.send(message)
                    return
                else:
                    self.logger(source['session'][:4], source['channel'][:4],
                            '|||', len(message)//2**10, '|||',
                            destination['session'][:4], destination['channel'][:4])

        if destination.get('brokers'):
            brokers = destination.get('brokers')
            for broker_id in brokers:
                if broker_id in self.sessions:
                    for broker_connection in self.sessions[broker_id].broker_connections:
                        await broker_connection.websocket.send(message)

    def handle_listen(self, connection, session, channel, brokers, is_public=False):
        if session == connection.session.session_id:
            self.logger('listen', connection.session.session_id[:4], channel[:4], is_public)
            
            if channel not in connection.session.channels:
                connection.session.channels[channel] = Channel(connection.session, channel, is_public)

            channel_obj = connection.session.channels[channel]
            channel_obj.connections.add(connection)
            connection.channels.add(channel_obj)
    
    def handle_close(self, connection, session, channel, **kwargs):
        if session == connection.session.session_id:
            self.logger('close', connection.session.session_id[:4], channel[:4])

            if connection.session.channels.get(channel):
                channel_obj = connection.session.channels.get(channel)
                connection.channels.remove(channel_obj)
                channel_obj.connections.remove(connection)
                if not channel_obj.connections:
                    channel_obj.close()

    async def handle_tokens(self, connection, *tokens):
        for action, token_tuple in tokens:
            if action == 'issue':
                token = Token(**token_tuple[1])
                if connection.session.session_id == token.issuer:
                    self.logger('tokens', token.issuer[:4], action, token_tuple[0][:4])
                    if token.verify_signature(token_tuple[0]):
                        if token.token_type == 'root':
                            connection.session.active_tokens.add(token_tuple[0])
                            self.tokens[token_tuple[0]] = token
                        elif token.token_type == 'extension':
                            if self.tokens.get(token.asset):
                                prev_token = self.tokens.get(token.asset)
                                if prev_token.receiver == token.issuer:
                                    connection.session.active_tokens.add(token_tuple[0])
                                    self.tokens[token_tuple[0]] = token
            if action == 'revoke':
                token = self.tokens.get(token_tuple)
                if token and token.issuer == connection.session.session_id:
                    self.logger('tokens', token.issuer[:4], action, token_tuple[:4])
                    self.tokens.pop(token_tuple)
                    connection.session.active_tokens.remove(token_tuple)

    def handle_broker_action(self, connection, action):
        if action == 'open':
            connection.session.broker_connections.add(connection)
        if action == 'close':
            connection.session.broker_sessions.remove(connection)

    async def add_broker(self, url):
        websocket = await websockets.connect(url)
        await Peer(websocket, self.broker_key).connect(self.sessions, self.handle_message)

    def check_no_repeat(self, message):
        signature, timestamp = message[:64], int.from_bytes(message[64:68], 'big')
        now = int(time.time())

        lead = now//60%2
        if self.seen_messages[2] != lead:
            self.seen_messages[lead].clear()

        if (now - 60) <= timestamp <= now:
            if signature not in self.seen_messages[0].union(self.seen_messages[1]):
                self.seen_messages[lead].add(signature)
                return True

        return False

    def decode_header(self, m):
        header = ujson.loads(m[73:73+int.from_bytes(m[68:70], 'big')])
        return header

    async def serve(self, host='127.0.0.1', port=2020, **kwargs):
        if 'compression' not in kwargs:
            kwargs['compression'] = None
        server = await websockets.serve(self.handle_connection, host, port, **kwargs)
        self.servers[(host, port)] = server

        return self

    async def close(self, host=None, port=None):
        for server_host, server_port in self.servers:
            if not host or server_host == host:
                if not port or server_port == port:
                    await self.servers.pop((server_host, server_port)).close()

class Peer(Connection):
    def __init__(self, websocket, broker_key, logger=None):
        super().__init__(websocket)
        self.broker_key = broker_key
        self.t_offset = 0
        self.listener = None
        self.logger = logger or print

    async def connect(self, sessions, callback):
        challenge = await self.websocket.recv()
        t_broker = int.from_bytes(challenge[-4:], 'big')
        
        self.t_offset = int(time.time()) - t_broker
        signature = self.broker_key.sign(challenge)

        pk = self.broker_key.public_serial().encode()

        sent_challenge = os.urandom(32)
        await self.websocket.send(signature + pk + sent_challenge)

        m = await asyncio.wait_for(self.websocket.recv(), 15)

        signature, session_id = m[:64], m[64:152].decode()
        PublicKey(session_id).verify(signature, sent_challenge)

        if session_id not in sessions:
            sessions[session_id] = Session(session_id)
        
        self.session = sessions[session_id]
        self.session.connections.add(self)
        self.session.broker_connections.add(self)

        await self.send({'broker': 'open'})

        self.listener = asyncio.create_task(self.listen(callback))
        
        return self

    async def send(self, header):
        h = ujson.dumps(header).encode()
        m = len(h).to_bytes(2, 'big') + (0).to_bytes(3, 'big') + h 
        t = int(time.time() - self.t_offset - 4).to_bytes(4, 'big')
        s = self.broker_key.sign(t + m,)
        
        await self.websocket.send(s + t + m)

    async def listen(self, callback):
        n_tries = 0
        while True:
            try:
                while True:
                    await callback(self, await self.websocket.recv())
                    n_tries = 0
            except Exception:
                self.logger(traceback.format_exc())
            
            await asyncio.sleep(1)
            if n_tries > 10:
                raise Exception('Max tries reached')
            n_tries += 1