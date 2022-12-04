import logging
import os
import asyncio
import time
from packaging import version
import re
from pkg_resources import get_distribution

import ujson
import websockets

from .cryptography import PrivateKey, PublicKey, Token, InvalidSignature
from .client import Route


class Connection:
    def __init__(self, websocket):
        self.MIN_CLIENT_VERSION = "0.1.59"
        self.websocket = websocket
        self.logger = logging.getLogger(__name__)
        self.session = None
        self.channels = set()
        self.tasks = set()

    async def handshake(self, sessions, broker_key, entrypoint):
        challenge = os.urandom(32) + int(time.time()).to_bytes(4, "big")

        await self.websocket.send(challenge)
        m = await asyncio.wait_for(self.websocket.recv(), 15)

        signature, session_id, client_challenge, metadata_raw = m[:64], m[64:152].decode(), m[152:184], m[184:]
        metadata = ujson.loads(metadata_raw.decode())

        if version.parse(metadata["version"]) < version.parse(self.MIN_CLIENT_VERSION):
            err_message = f'Incompatible version {metadata["version"]} < {self.MIN_CLIENT_VERSION}'
            await self.websocket.send(err_message.encode())
            raise Exception(err_message)

        PublicKey(session_id).verify(signature, challenge)

        await self.websocket.send(
            broker_key.sign(client_challenge)
            + broker_key.public_serial().encode()
            + ujson.dumps({"entrypoint": entrypoint and entrypoint.to_dict()}, escape_forward_slashes=False).encode()
        )

        if session_id not in sessions:
            sessions[session_id] = Session(session_id)

        self.session = sessions[session_id]
        self.session.connections.add(self)

        return self

    async def close(self, sessions, remove=True):
        try:
            await self.websocket.close()
            for channel in self.channels:
                if self in channel.connections:
                    channel.connections.remove(self)
                if not channel.connections:
                    self.session.channels.pop(channel.channel_id, None)

            if remove:
                if self in self.session.connections:
                    self.session.connections.remove(self)

                self.session.broker_connections.pop(self, None)

                if not self.session.channels and not self.session.broker_connections and not self.session.connections:
                    sessions.pop(self.session.session_id, None)

                await asyncio.gather(*(x for x in self.tasks if x.done()))
                # print([x for x in self.tasks if not x.done()])
                [x.cancel() for x in self.tasks if not x.done()]
        except Exception:
            self.logger.error("Exception when closing %s", self.session.session_id[:4], exc_info=True)


class Session:
    def __init__(self, session_id):
        self.session_id = session_id
        self.channels = {}
        self.expecting_channels = {}
        self.connections = set()
        self.broker_connections = {}
        self.cached_tokens = {}

    async def expect_channel(self, channel_id):
        if channel_id not in self.channels:
            list(self.connections)[0].logger.info("awaiting channel %s", channel_id[:4])
            event = asyncio.Event()
            self.expecting_channels[channel_id] = event
            try:
                await asyncio.wait_for(event.wait(), 2)
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
            broker.logger.info("||| no tokens")
            return False

        asset = self.channel_id
        last_receiver = self.session.session_id
        max_depth = None

        for depth, enc_token in enumerate(tokens):
            token = Token.decode(enc_token, False)
            if await broker.check_token(token):
                if (token.asset == asset) and (token.issuer == last_receiver):
                    max_depth = (max_depth and min(max_depth, (token.max_depth or max_depth) + depth)) or token.max_depth
                    if depth >= (max_depth or (depth + 1)):
                        return False
                    if token.receiver == source_id:
                        return True
                    last_receiver = token.receiver
                    asset = token.signature

            else:
                broker.logger.info("||| token failed broker.check_token %s", token.signature[:4])
        return False


class Broker:
    def __init__(self, broker_key_file=None, broker_key_pass=None):
        self.sessions = {}
        self.servers = {}
        self.entrypoint = None
        self.broker_key = PrivateKey(broker_key_file, broker_key_pass)
        self.logger = logging.getLogger(__name__)
        self.seen_messages = [set(), set(), 0]
        self.topology_cache = {0: {}}

    async def handle_connection(self, websocket, _):
        connection = None
        try:
            connection = await Connection(websocket).handshake(self.sessions, self.broker_key, self.entrypoint)
            self.logger.info("%s: new connection %s", self.broker_key.public_serial()[:4], connection.session.session_id[:4])

            async for message in websocket:
                if self.check_no_repeat(message):
                    await asyncio.gather(*(x for x in connection.tasks if x.done()))
                    connection.tasks = set(x for x in connection.tasks if not x.done())
                    connection.tasks.add(asyncio.get_event_loop().create_task(self.handle_message(connection, message)))

        except Exception:
            if connection:
                self.logger.error(
                    "%s: %s .handle_connection",
                    self.broker_key.public_serial()[:4],
                    connection.session.session_id[:4],
                    exc_info=True,
                )
            else:
                self.logger.error("%s: Handshake error", self.broker_key.public_serial(), exc_info=True)

        finally:
            if connection:
                self.logger.info("%s: %s disconnected", self.broker_key.public_serial()[:4], connection.session.session_id[:4])
                await connection.close(self.sessions)

    async def handle_message(self, connection, message):
        try:
            headers = self.decode_header(message)
            for action, args in headers:
                if action == "listen":
                    self.handle_listen(connection, **args)
                if action == "broker":
                    await self.handle_broker_action(connection, *args)
                if action == "send":
                    await self.handle_send(connection, message, **args)
                if action == "close":
                    self.handle_close(connection, **args)

        except Exception:
            self.logger.error(
                "%s: %s .handle_message",
                self.broker_key.public_serial()[:4],
                connection.session.session_id[:4],
                exc_info=True,
            )
            self.logger.info("%s: %s disconnected", self.broker_key.public_serial()[:4], connection.session.session_id[:4])
            await connection.close(self.sessions)

    async def handle_send(self, connection, message, source, destination):
        # self.logger.info(
        #     "%s: send %s %s ??? %s ??? %s %s",
        #     self.broker_key.public_serial()[:4],
        #     source["session"][0][:4],
        #     source["channel"][:4],
        #     str(len(message) // 2 ** 10),
        #     destination["session"][0][:4],
        #     destination["channel"][:4],
        # )
        s = Route(**source)
        d = Route(**destination)

        if d.brokers and self.broker_key.public_serial() in d.brokers:
            dest_session = self.sessions.get(d.session[0])
            if dest_session:
                dest_channel = await dest_session.expect_channel(d.channel)

                if dest_session.channels.get(d.channel):
                    if await dest_channel.validate_token_chain(s.session[0], d.tokens, self):
                        if connection.session.session_id != s.session[0]:
                            try:
                                len_h = int.from_bytes(message[68:70], "big")
                                PublicKey(s.session[0]).verify(message[:64], message[64 : 73 + len_h + 65 + 32])
                            except InvalidSignature:
                                self.logger.info(
                                    'Invalid forwarded message signature - source: %s connection: %s',
                                    s.session[0][:4],
                                    connection.session.session_id[:4]
                                )
                                return
                        self.logger.info(
                            "%s: send %s %s >>> %s >>> %s %s",
                            self.broker_key.public_serial()[:4],
                            source["session"][0][:4],
                            source["channel"][:4],
                            str(len(message) // 2 ** 10),
                            destination["session"][0][:4],
                            destination["channel"][:4],
                        )

                        for connection in dest_channel.connections:
                            await connection.websocket.send(message)
                        return
                    else:
                        # print('|||||', [Token.decode(t) for t in s.tokens], [Token.decode(t) for t in d.tokens])
                        self.logger.info(
                            "%s: send %s %s ||| %s ||| %s %s",
                            self.broker_key.public_serial()[:4],
                            source["session"][0][:4],
                            source["channel"][:4],
                            str(len(message) // 2 ** 10),
                            destination["session"][0][:4],
                            destination["channel"][:4],
                        )
                else:
                    if self.broker_key.public_serial() in (d.brokers or []):
                        return

        if d.brokers:
            for depth in sorted(self.topology_cache):
                brokers = set(d.brokers).intersection(set(self.topology_cache[depth].keys()))
                if brokers:
                    for broker_id in brokers:
                        for broker_connection in self.topology_cache[depth][broker_id]:
                            if broker_connection.websocket.closed == False:
                                await broker_connection.websocket.send(message)
                                self.logger.info(
                                    "%s: send %s %s ))) %s ))) %s %s (%s)",
                                    self.broker_key.public_serial()[:4],
                                    source["session"][0][:4],
                                    source["channel"][:4],
                                    str(len(message) // 2 ** 10),
                                    destination["session"][0][:4],
                                    destination["channel"][:4],
                                    broker_id[:4],
                                )
                    return
            for peer in set().union(*self.topology_cache[0].values()):
                await peer.send(
                    [
                        (
                            "broker",
                            (
                                "topology_update",
                                {"source": self.broker_key.public_serial(), "destinations": d.brokers, "counter": 0},
                            ),
                        )
                    ]
                )

    def handle_listen(self, connection, session, channel, brokers, is_public=False):
        if session[0] == connection.session.session_id:
            self.logger.info(
                "%s: listen %s %s %s",
                self.broker_key.public_serial()[:4],
                str(connection.session.session_id[:4]),
                str(channel[:4]),
                str(is_public),
            )

            if channel not in connection.session.channels:
                connection.session.channels[channel] = Channel(connection.session, channel, is_public)

            channel_obj = connection.session.channels[channel]
            channel_obj.connections.add(connection)
            connection.channels.add(channel_obj)

            event = connection.session.expecting_channels.pop(channel, None)
            if event:
                event.set()

    def handle_close(self, connection, session, channel, **kwargs):
        if session[0] == connection.session.session_id:
            self.logger.info(
                "%s: close %s %s",
                self.broker_key.public_serial()[:4],
                str(connection.session.session_id[:4]),
                str(channel[:4]),
            )

            if connection.session.channels.get(channel):
                channel_obj = connection.session.channels.get(channel)
                connection.channels.remove(channel_obj)
                channel_obj.connections.remove(connection)
                if not channel_obj.connections:
                    channel_obj.close()

    async def handle_broker_action(self, connection, action, params=None):
        if action == "open":
            peer = Peer(connection.websocket, self)
            session_id = connection.session.session_id
            connection.session.broker_connections[connection] = peer
            self.topology_cache[0][session_id] = (self.topology_cache[0].get(session_id) or set()).union([peer])
        if action == "close":
            connection.session.broker_sessions.pop(connection, None)
        if action == "topology_update" and params:
            self.logger.info("%s: topology %s", self.broker_key.public_serial()[:4], str(params))
            # if self.check_no_repeat...
            peer = connection.session.broker_connections[connection]
            if self.broker_key.public_serial() == params["source"]:
                # if check signature
                for reply in params["replies"]:
                    peer_set = self.topology_cache[params["replies"][reply]].get(reply) or set()
                    peer_set = peer_set.union([peer])
                    self.topology_cache[params["replies"][reply]][reply] = peer_set
            elif "replies" in params:
                for reply in params["replies"]:
                    params["replies"][reply] += 1
                for depth in self.topology_cache:
                    source_set = self.topology_cache[depth].get(params["source"])
                    if source_set:
                        for conn in source_set:
                            await conn.send([("broker", ("topology_update", params))])
                        return
            else:
                destination_set = set(params["destinations"]).intersection(set(self.topology_cache[0].keys()))
                if destination_set:
                    params["replies"] = {d: 0 for d in destination_set}
                    await peer.send([("broker", ("topology_update", params))])
                    return
                params["counter"] += 1
                source_set = self.topology_cache[params["counter"]].get(params["source"]) or set()
                self.topology_cache[params["counter"]][params["source"]] = source_set.union([peer])

                for forwarded in set().union(self.topology_cache[0].values()):
                    await peer.send([("broker", ("topology_update", params))])

    async def add_broker(self, url, inherit_entrypoint=False):
        peer = Peer(None, self)
        if re.sub(r"(?![\w\d]+:\/\/[\w\d.]+):[\d]+", "", url) == url:
            i = len(re.findall(r"[\w\d]+:\/\/[\w\d.]+", url)[0])
            url = url[:i] + ":8776" + url[i:]

        await peer.connect(url, inherit_entrypoint)  # This already adds the peer to self.sessions

    async def check_token(self, token):
        session = self.sessions.get(token.issuer)
        if session:
            if token.signature in session.cached_tokens:
                return token.encode() == session.cached_tokens.get(token.signature).encode()
            else:
                if token.verify(token.signature):
                    session.cached_tokens[token.signature] = token
                    return True
                else:
                    return False

        else:
            return token.verify(token.signature)

    def check_no_repeat(self, message):
        signature, timestamp = message[:64], int.from_bytes(message[64:68], "big")
        now = int(time.time())

        lead = now // 60
        if self.seen_messages[2] != lead:
            self.seen_messages[lead % 2].clear()
            self.seen_messages[2] = lead

        if (now - 60 + 4) <= timestamp <= now + 4:
            if signature not in self.seen_messages[0].union(self.seen_messages[1]):
                self.seen_messages[lead % 2].add(signature)
                return True

        return False

    def decode_header(self, m):
        header = ujson.loads(m[73 : 73 + int.from_bytes(m[68:70], "big")])
        return header

    async def serve(self, host="127.0.0.1", port=8776, **kwargs):
        if "compression" not in kwargs:
            kwargs["compression"] = None
        server = await websockets.serve(self.handle_connection, host, port, **kwargs)
        self.servers[(host, port)] = server

        return self

    async def close(self, host=None, port=None):
        keys = set(self.servers.keys())
        for server_host, server_port in keys:
            if not host or server_host == host:
                if not port or server_port == port:
                    self.servers.pop((server_host, server_port)).close()


class Peer(Connection):
    def __init__(self, websocket, broker):
        super().__init__(websocket)
        self.broker = broker
        self.t_offset = 0
        self.listener = None
        self.url = None
        self.lock = asyncio.Event()
        self.exception = None

    def connect(self, url, inherit_entrypoint):
        self.url = url
        self.listener = asyncio.get_event_loop().create_task(self.listen(inherit_entrypoint))

        return self

    async def reconnect(self):
        if self.websocket:
            await self.websocket.close()

        self.websocket = await websockets.connect(self.url)

        challenge = await self.websocket.recv()
        t_broker = int.from_bytes(challenge[-4:], "big")

        self.t_offset = int(time.time()) - t_broker
        signature = self.broker.broker_key.sign(challenge)

        pk = self.broker.broker_key.public_serial().encode()

        sent_challenge = os.urandom(32)
        sent_metadata = {"version": get_distribution(__name__.split(".")[0]).version}
        await self.websocket.send(signature + pk + sent_challenge + ujson.dumps(sent_metadata).encode())

        m = await asyncio.wait_for(self.websocket.recv(), 15)

        if m[: len("Incompatible")] == b"Incompatible":
            raise Exception(m.decode())

        signature, session_id, metadata = m[:64], m[64:152].decode(), ujson.loads(m[152:].decode())
        PublicKey(session_id).verify(signature, sent_challenge)

        entrypoint = Route(**metadata.get("entrypoint")) if metadata.get("entrypoint") else None

        await self.send([("broker", ("open",))])

        return session_id, entrypoint

    async def send(self, header):
        h = ujson.dumps(header).encode()
        m = len(h).to_bytes(2, "big") + (0).to_bytes(3, "big") + h
        t = int(time.time() - self.t_offset - 4).to_bytes(4, "big")
        s = self.broker.broker_key.sign(
            t + m,
        )

        await self.websocket.send(s + t + m)

    async def listen(self, inherit_entrypoint):
        n_tries = 0
        while True:
            try:
                self.lock.clear()
                session_id, entrypoint = await self.reconnect()

                if not (-15 < self.t_offset < 2):
                    raise IncompatibleBrokerException(f"Unix time difference too large: {self.t_offset} seconds.")
                self.lock.set()

                if session_id not in self.broker.sessions:
                    self.broker.sessions[session_id] = Session(session_id)

                if inherit_entrypoint:
                    self.broker.entrypoint = entrypoint

                self.session = self.broker.sessions[session_id]
                self.session.connections.add(self)
                self.session.broker_connections[self] = self
                self.broker.topology_cache[0][session_id] = (self.broker.topology_cache[0].get(session_id) or set()).union(
                    [self]
                )

                n_tries = 0
                while True:
                    message = await self.websocket.recv()
                    self.tasks.add(asyncio.get_event_loop().create_task(self.broker.handle_message(self, message)))

                    # await asyncio.gather(*(x for x in self.tasks if x.done() and not x.exception()), return_exceptions=True)
                    self.tasks = set(x for x in self.tasks if not x.done())

            except IncompatibleBrokerException as e:
                self.logger.error("Peer.listen", exc_info=True)
                self.exception = e
                await self.close(self.broker.sessions, False)
                self.lock.set()
                return

            except Exception:
                self.logger.error("Peer.listen", exc_info=True)
                await self.close(self.broker.sessions, False)
                self.lock.set()

                if n_tries > 10:
                    self.logger.error("Peer.listen: Max connection retries reached", exc_info=True)
                    return

                await asyncio.sleep(1)
                n_tries += 1
                continue

    def __await__(self):
        async def wait_lock():
            await self.lock.wait()
            if self.exception:
                raise self.exception

        return wait_lock().__await__()


class IncompatibleBrokerException(Exception):
    pass
