import logging
import os
import asyncio
import time
import traceback
import base64

import ujson
import websockets

from .cryptography import PrivateKey, PublicKey, Token
from .client import Route

class Connection:
    def __init__(self, websocket):
        self.websocket = websocket
        self.logger = logging.getLogger(__name__)
        self.session = None
        self.channels = set()
        self.tasks = set()

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

    async def close(self, sessions):
        try:
            for channel in self.channels:
                channel.connections.remove(self)
                if not channel.connections:
                    self.session.channels.pop(channel.channel_id)

            self.session.connections.remove(self)

            if self in self.session.broker_connections:
                self.session.broker_connections.remove(self)

            if not self.session.channels and not self.session.broker_connections:
                sessions.pop(self.session.session_id)
        
            await asyncio.gather(*(x for x in self.tasks if x.done()))
            [x.cancel() for x in self.tasks if not x.done()]
        except Exception:
            self.logger.error('Closing Connection', exc_info=True)
class Session:
    def __init__(self, session_id):
        self.session_id = session_id
        self.channels = {}
        self.expecting_channels = {}
        self.connections = set()
        self.broker_connections = {}
        self.active_tokens = {}
        self.cached_tokens = {}
        self.expecting_tokens = {}
        self.tasks = set()
    
    async def validate_peer_token(self, token, event):
        if token.signature in self.cached_tokens \
        and token.encode() == self.cached_tokens[token.signature].encode():
            event.set()
            return True
        self.expecting_tokens[token.signature] = (event, token)
        for broker in self.broker_connections.values():
            await broker.send((('token', ('validate', token.encode())),))
        self.tasks.add(asyncio.get_event_loop().create_task(self.clean_expecting_token(token)))
        
        return False

    async def clean_expecting_token(self, token):
        await asyncio.sleep(15)
        self.expecting_tokens.pop(token.signature, None)
    
    async def timeout_cached_token(self, token):
        await asyncio.sleep(15)
        self.cached_tokens.pop(token.signature, None)

    def approve_token(self, token):
        self.tasks = set(x for x in self.tasks if not x.done())
        if token.issuer == self.session_id:
            self.active_tokens[token.signature] = token
        else:
            self.cached_tokens[token.signature] = token
            self.tasks.add(asyncio.get_event_loop().create_task(self.timeout_cached_token(token)))

        event, expected_token = self.expecting_tokens.get(token.signature, (None, None))
        if expected_token and expected_token.encode() == token.encode():
            self.expecting_tokens.pop(token.signature, None)
            event.set()

    async def expect_channel(self, channel_id):
        if channel_id not in self.channels:
            event = asyncio.Event()
            self.expecting_channels[channel_id] = event
            try:
                await asyncio.wait_for(event.wait(), 15)
                self.expecting_channels.pop(channel_id, None)
            except asyncio.exceptions.TimeoutError:
                pass
        return self.channels.get(channel_id)

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
    
    async def validate_token_chain(self, source_id, tokens, broker):
        if (source_id == self.session.session_id) or self.is_public:
            return True
        
        if not tokens:
            return False

        asset = self.channel_id
        last_receiver = self.session.session_id
        max_depth = None
        
        for depth, enc_token in enumerate(tokens):
            token = Token.decode(enc_token, False)
            if await broker.check_token(token):
                if (token.asset == asset) and (token.issuer == last_receiver):
                    max_depth = (max_depth and min(max_depth, token.max_depth or max_depth)) or token.max_depth
                    if depth > (max_depth or depth):
                        return False
                    if token.receiver == source_id:
                        return True
                    last_receiver = token.receiver
                    asset = token.signature
            
        return False

class Broker:
    def __init__(self, broker_key_file=None):
        self.sessions = {}
        self.servers = {}
        self.broker_key = PrivateKey(broker_key_file)
        self.logger = logging.getLogger(__name__)  
        self.seen_messages = (set(), set(), 0)

    async def handle_connection(self, websocket, _):
        connection = None
        try:
            connection = await Connection(websocket).handshake(self.sessions, self.broker_key)
            self.logger.info ('new connection %s', connection.session.session_id[:4])

            async for message in websocket:
                if self.check_no_repeat(message):
                    await asyncio.gather(*(x for x in connection.tasks if x.done()))
                    connection.tasks = set(x for x in connection.tasks if not x.done())
                    connection.tasks.add(asyncio.get_event_loop().create_task(self.handle_message(connection, message)))

        except Exception:
            self.logger.error('Broker.handle_connection', exc_info=True)

        finally:
            self.logger.info ('%s disconnected', connection.session.session_id[:4])
            if connection:
                await connection.close(self.sessions)
    async def handle_message(self, connection, message):
        headers = self.decode_header(message)
        for action, args in headers:
            if action == 'listen':
                self.handle_listen(connection, **args)
            if action == 'token':
                await self.handle_tokens(connection, *args)
            if action == 'broker':
                self.handle_broker_action(connection, args)
            if action == 'send':
                await self.handle_send(connection, message, **args)
            if action == 'close':
                self.handle_close(connection, **args)

    async def handle_send(self, connection, message, source, destination):
        self.logger.info('send %s %s ??? %s ??? %s %s', source['session'][:4], source['channel'][:4],
                        str(len(message)//2**10),
                        destination['session'][:4], destination['channel'][:4])
        s = Route(**source)
        d = Route(**destination)
        
        dest_session = self.sessions.get(d.session)
        if dest_session:
            dest_channel = await dest_session.expect_channel(d.channel)

            if dest_session.channels.get(d.channel):
                if await dest_channel.validate_token_chain(s.session, d.tokens, self):
                    self.logger.info('send %s %s >>> %s >>> %s %s', source['session'][:4], source['channel'][:4],
                        str(len(message)//2**10),
                        destination['session'][:4], destination['channel'][:4])

                    for connection in dest_channel.connections:
                        await connection.websocket.send(message)
                    return
                else:
                    self.logger.info('send %s %s ||| %s ||| %s %s', source['session'][:4], source['channel'][:4],
                            str(len(message)//2**10),
                            destination['session'][:4], destination['channel'][:4])

        if d.brokers:
            for broker_id in d.brokers:
                if broker_id in self.sessions:
                    for broker_connection in self.sessions[broker_id].broker_connections.values():
                        for enc_token in d.tokens:
                            token = Token.decode(enc_token, False)
                            if self.broker_key.public_serial() in token.brokers \
                            and token.issuer in self.sessions \
                            and token.signature in self.sessions[token.issuer].active_tokens \
                            and enc_token == self.sessions[token.issuer].active_tokens.get(token.signature).encode():
                                await broker_connection.send((('token', ('approve', enc_token)),))
                        await broker_connection.websocket.send(message)

    def handle_listen(self, connection, session, channel, brokers, is_public=False):
        if session == connection.session.session_id:
            self.logger.info('listen %s %s %s', str(connection.session.session_id[:4]), str(channel[:4]), str(is_public))
            
            if channel not in connection.session.channels:
                connection.session.channels[channel] = Channel(connection.session, channel, is_public)

            channel_obj = connection.session.channels[channel]
            channel_obj.connections.add(connection)
            connection.channels.add(channel_obj)

            event = connection.session.expecting_channels.pop(channel, None)
            if event:
                event.set()
    
    def handle_close(self, connection, session, channel, **kwargs):
        if session == connection.session.session_id:
            self.logger.info('close %s %s', str(connection.session.session_id[:4]), str(channel[:4]))

            if connection.session.channels.get(channel):
                channel_obj = connection.session.channels.get(channel)
                connection.channels.remove(channel_obj)
                channel_obj.connections.remove(connection)
                if not channel_obj.connections:
                    channel_obj.close()

    async def handle_tokens(self, connection, action, *args):
        if action == 'issue':
            tokens = [Token.decode(x) for x in args if x]
            token = tokens[0]
            if connection.session.session_id == token.issuer:
                self.logger.info('tokens %s %s %s', str(token.issuer[:4]), action, str(token.signature[:4]))
                if token.token_type == 'root':
                    connection.session.active_tokens[token.signature] = token
                elif token.token_type == 'extension':
                    prev_token = tokens[1]
                    if await self.check_token(prev_token):
                        connection.session.approve_token(token)

        if action == 'revoke':
            token = connection.session.active_tokens.get(args[0])
            if token:
                self.logger.info('tokens %s %s %s', str(token.issuer[:4]), action, str(token.signature[:4]))
                connection.session.active_tokens.pop(token.signature, None)

        if action == 'validate':
            token = Token.decode(args[0], False)
            if token.issuer in self.sessions:
                expected_token = self.sessions[token.issuer].active_tokens.get(token.signature)
                if expected_token and token.encode() == expected_token.encode():
                    broker_connection = connection.session.broker_connections.get(connection)
                    if broker_connection:
                        self.logger.info('tokens %s %s %s', str(token.issuer[:4]), action, str(token.signature[:4]))
                        await broker_connection.send((('token', ('approve', token.encode())),))

        if action == 'approve':
            token = Token.decode(args[0])
            self.logger.info('tokens %s %s %s', str(token.issuer[:4]), action, str(token.signature[:4]))
            connection.session.approve_token(token)

    def handle_broker_action(self, connection, action):
        if action == 'open':
            connection.session.broker_connections[connection] = Peer(connection.websocket, self.broker_key)
        if action == 'close':
            connection.session.broker_sessions.pop(connection, None)

    async def add_broker(self, url):
        peer = Peer(None, self.broker_key)
        peer.connect(url, self.sessions, self.handle_message) # This already adds the peer to self.sessions

    async def check_token(self, token):
        session = self.sessions.get(token.issuer)
        if session:
            if token.signature in session.active_tokens:
                return token.encode() == session.active_tokens.get(token.signature).encode()
            else:
                event = asyncio.Event()
                session.expecting_tokens[token.signature] = (event, token)
                session.tasks.add(asyncio.get_event_loop().create_task(session.clean_expecting_token(token)))
                await asyncio.wait_for(event.wait(), 15)

                return True
        else:
            event = asyncio.Event()
            for broker in token.brokers:
                if broker in self.sessions \
                and await self.sessions[broker].validate_peer_token(token, event):
                    break
            else:
                try:
                    await asyncio.wait_for(event.wait(), 15)
                except asyncio.exceptions.TimeoutError:
                    return False
            return True

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

    async def serve(self, host='127.0.0.1', port=8776, **kwargs):
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
    def __init__(self, websocket, broker_key):
        super().__init__(websocket)
        self.broker_key = broker_key
        self.t_offset = 0

        self.broker_sessions = None
        self.callback = None
        self.listener = None

    def connect(self, url, sessions, callback):
        self.url = url
        self.broker_sessions = sessions
        self.callback = callback
        self.listener = asyncio.get_event_loop().create_task(self.listen())


    async def reconnect(self):
        if self.websocket:
            await self.websocket.close()
        
        self.websocket = await websockets.connect(self.url)
        
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


        await self.send([('broker', 'open')])

        return session_id

    async def send(self, header):
        h = ujson.dumps(header).encode()
        m = len(h).to_bytes(2, 'big') + (0).to_bytes(3, 'big') + h 
        t = int(time.time() - self.t_offset - 4).to_bytes(4, 'big')
        s = self.broker_key.sign(t + m,)
        
        await self.websocket.send(s + t + m)

    async def listen(self):
        n_tries = 0
        while True:
            try:
                session_id = await self.reconnect()

                if session_id not in self.broker_sessions:
                    self.broker_sessions[session_id] = Session(session_id)
                
                self.session = self.broker_sessions[session_id]
                self.session.connections.add(self)
                self.session.broker_connections[self] = self

                while True:
                    message = await self.websocket.recv()
                    await asyncio.gather(*(x for x in self.tasks if x.done()))
                    self.tasks = set(x for x in self.tasks if not x.done())
                    self.tasks.add(asyncio.get_event_loop().create_task(self.callback(self, message)))
                    n_tries = 0

            except Exception:
                self.logger.error('Peer.listen', exc_info=True)
            finally:
                self.close(self.broker_sessions)
            
            await asyncio.sleep(1)
            if n_tries > 10:
                raise Exception('Max tries reached')
            n_tries += 1
