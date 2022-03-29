import base64
import logging
import time
import os
import asyncio
import bson
import zlib
from collections import deque, OrderedDict
from pkg_resources import get_distribution
import hashlib

import websockets
import ujson

from .cryptography import PrivateKey, PublicKey, SharedKey, Token, InvalidSignature


class Connection:
    def __init__(self, session, url="ws://localhost:8776"):
        self.RESEND_TIMEOUT = 2  # sec
        self.MAX_SEND_RETRIES = 3

        self.session = session
        self.url = url
        self.logger = logging.getLogger(__name__)
        self.websocket = None
        self.t_offset = 0
        self.broker_id = None
        self.entrypoint = None

        self.is_connecting_lock = asyncio.Event()
        self.awaiting_ack = OrderedDict()

        if 'connections' not in dir(session):
            session.connections = set()

        session.connections.add(self)

        self.listener = asyncio.get_event_loop().create_task(self.listen())

    async def reconnect(self):
        if self.is_connecting_lock.is_set():
            self.is_connecting_lock.clear()
            if self.listener:
                self.listener.cancel()

            self.listener = asyncio.get_event_loop().create_task(self.listen())

        await self.is_connecting_lock.wait()

    async def _connect(self):
        self.logger.info("%s connecting", self.session.session_key.public_serial()[:4])
        if self.websocket:
            await self.websocket.close()

        self.websocket = await websockets.connect(self.url)

        challenge = await self.websocket.recv()
        t_broker = int.from_bytes(challenge[-4:], "big")

        self.t_offset = int(time.time()) - t_broker
        signature = self.session.session_key.sign(challenge)

        pk = self.session.session_key.public_serial().encode()

        sent_challenge = os.urandom(32)
        sent_metadata = {"version": get_distribution(__name__.split(".")[0]).version}
        await self.websocket.send(
            signature + pk + sent_challenge + ujson.dumps(sent_metadata, escape_forward_slashes=False).encode()
        )

        m = await asyncio.wait_for(self.websocket.recv(), 15)

        if m[: len("Incompatible")] == b"Incompatible":
            raise Exception(m.decode())

        broker_signature, broker_id, metadata = m[:64], m[64:152].decode(), ujson.loads(m[152:].decode())
        PublicKey(broker_id).verify(broker_signature, sent_challenge)

        self.broker_id = broker_id
        self.entrypoint = Route(**metadata.get("entrypoint")) if metadata.get("entrypoint") else None

        headers = []
        for channel in self.session.channels.values():
            listen_dict = channel.route.to_dict()
            listen_dict["is_public"] = channel.is_public
            listen_dict.pop("tokens")
            headers.append(("listen", listen_dict))

        lens = tuple((len(ujson.dumps(h, escape_forward_slashes=False)), h) for h in headers)

        groups = [[]]
        acc_len = 1
        for l, h in lens:
            if acc_len + l + 1 < 256**2:
                groups[-1].append(h)
                acc_len += l + 1
            else:
                groups.append([h])
                acc_len = l + 2

        for group in groups:
            await self.send(group)

        self.is_connecting_lock.set()

        return self

    async def send(self, header, payload=b"", bundle_id=None, ack_message_id=None):
        if not ack_message_id:
            self.logger.info(
                "%s sending: %s %s",
                self.session.session_key.public_serial()[:4],
                " ".join(h[0] for h in header),
                len(payload),
            )

        def encode(header, payload, message_id, retry):
            h = ujson.dumps(header, escape_forward_slashes=False).encode()
            r = (retry).to_bytes(1, "big") + (message_id or b"0" * 64)
            p = hashlib.sha256(payload).digest()
            m = len(h).to_bytes(2, "big") + len(r + p + payload).to_bytes(3, "big") + h + r + p
            t = int(time.time() - self.t_offset).to_bytes(4, "big")
            s = self.session.session_key.sign(t + m)
            return s, t + m + payload

        s, mm = encode(header, payload, ack_message_id, 255 if ack_message_id else 0)
        message_id = s

        expect_ack = "send" in set(a for a, _ in header) and not ack_message_id
        if expect_ack:
            while True:  # Clearing old messages
                if self.awaiting_ack:
                    target = list(self.awaiting_ack.values())[0]
                    if target[3] < (time.time() - (1 + self.MAX_SEND_RETRIES) * self.RESEND_TIMEOUT):
                        self.clear(target[1])
                        continue
                break
            lock = asyncio.Event()
            if not self.awaiting_ack:
                lock.set()
            self.awaiting_ack[message_id or s] = (header, bundle_id, lock, time.time())

        for retry in range(self.MAX_SEND_RETRIES + 1):
            if not self.websocket or self.websocket.closed:
                self.logger.info("%s reconnecting during send retry %d", self.session.session_key.public_serial()[:4], retry)
                if self.is_connecting_lock.is_set():
                    await self.reconnect()
                else:
                    await self.is_connecting_lock.wait()

            try:
                await self.websocket.send(s + mm)
            except Exception:
                self.logger.info("%s Connection.send", self.session.session_key.public_serial()[:4], exc_info=True)
                continue

            if not expect_ack or await self.expect_ack(message_id, lock):
                return

            if retry < (self.MAX_SEND_RETRIES):
                s, mm = encode(header, payload, message_id, retry + 1)
                self.logger.info("%s retrying send %d", self.session.session_key.public_serial()[:4], retry)

        raise ConnectionError(["%s send %s %s - %s max retries reached" % (
            self.session.session_key.public_serial()[:4],
            h['destination']['session'][0][:4], 
            h['destination']['session'][1][:2],
            h['destination']['channel'][:4],
            ) for a, h in header if a == 'send'][0])

    async def expect_ack(self, message_id, lock):
        await lock.wait()
        if message_id not in self.awaiting_ack:
            return True
        lock.clear()
        try:
            await asyncio.wait_for(lock.wait(), self.RESEND_TIMEOUT)
        except asyncio.TimeoutError:
            lock.set()
        if message_id not in self.awaiting_ack:
            return True

        return False

    def clear(self, bundle_id):
        if bundle_id:
            self.logger.info("%s clearing %s", self.session.session_key.public_serial()[:4], bundle_id[:4])
            ks = []
            for k, v in self.awaiting_ack.items():
                if v[1] == bundle_id:
                    ks.append(k)
            for k in ks:
                self.awaiting_ack.pop(k)

    async def listen(self):
        n_tries = 0
        while True:
            try:
                await self._connect()
                while True:
                    await self.recv()
                    n_tries = 0
            except Exception:
                self.logger.warning("%s Connection.listen", self.session.session_key.public_serial()[:4], exc_info=True)

            self.is_connecting_lock.clear()
            await asyncio.sleep(1)

            if n_tries > self.MAX_SEND_RETRIES:
                self.is_connecting_lock.set()
                raise ConnectionError("%s Max tries reached" % self.session.session_key.public_serial()[:4])
            n_tries += 1

    async def recv(self):
        if not self.websocket or self.websocket.closed:
            if self.is_connecting_lock.is_set():
                await self.reconnect()
            else:
                await self.is_connecting_lock.wait()

        message = await self.websocket.recv()

        signature, timestamp = message[:64], int.from_bytes(message[64:68], "big")

        if self.session.check_no_repeat(signature, timestamp + self.t_offset):

            len_h, len_p = [int.from_bytes(x, "big") for x in [message[68:70], message[70:73]]]
            header = ujson.loads(message[73 : 73 + len_h])
            full_payload = message[73 + len_h : 73 + len_h + len_p]
            self.logger.info(
                "%s received: %s %s",
                self.session.session_key.public_serial()[:4],
                " ".join(h[0] for h in header),
                len(full_payload),
            )
            for action, content in header:
                if action == "send":
                    source, destination = Route(**content["source"]), Route(**content["destination"])
                    PublicKey(source.session[0]).verify(signature, message[64 : 73 + len_h + 65 + 32])
                    if self.session.channels.get(destination.channel):
                        channel = self.session.channels.get(destination.channel)
                        if full_payload[0] == 255:
                            self.ack(source.session[0], full_payload[1:65])
                        else:
                            ret_signature = signature if (full_payload[0] == 0) else full_payload[1:65]
                            payload = full_payload[65 + 32 :]
                            await self.send(
                                (("send", {"destination": content["source"], "source": content["destination"]}),),
                                b"",
                                None,
                                ret_signature,
                            )
                            # print(self.session.session_key.public_serial()[:4], 'sent ack', ret_signature[:4])
                            if hashlib.sha256(payload).digest() == full_payload[65 : 65 + 32]:
                                if (ret_signature == signature) or self.session.check_no_repeat(
                                    ret_signature, timestamp + self.t_offset
                                ):
                                    channel.handle_message(source, destination, payload, message[: 73 + len_h + 65 + 32])
                            else:
                                raise AssertionError("Message payload does not match signed hash")

    def ack(self, source_id, message_id):
        # print(self.session.session_key.public_serial()[:4], 'received ack', message_id[:4], 'from', source_id[:4])
        header = self.awaiting_ack.get(message_id, [[]])[0]
        for action, content in header:
            if action == "send":
                if content["destination"]["session"][0] == source_id:
                    t = self.awaiting_ack.pop(message_id)
                    t[2].set()
                    if self.awaiting_ack:
                        self.awaiting_ack[list(self.awaiting_ack.keys())[0]][2].set()

    def __await__(self):
        async def await_lock():
            if self.listener:
                await self.is_connecting_lock.wait()
                if self.listener.done():
                    old_listener = self.listener
                    self.listener = None
                    await old_listener
            else:
                await self.reconnect()

            return self

        return await_lock().__await__()


class Session:
    def __init__(self, session_key=None, session_key_pass=None):
        self.session_key = session_key if isinstance(session_key, PrivateKey) else PrivateKey(session_key, session_key_pass)
        self.instance_id = base64.b64encode(os.urandom(6)).decode()
        self.channels = {}
        self.targets = {}
        self.routes = {}
        self.connections = set()
        self.seen_messages = [set(), set(), 0]
        self.issued_tokens = {}
        self.pending_tasks = set()
        self.message_listener = None
        self.logger = logging.getLogger(__name__)

    def check_no_repeat(self, signature, timestamp):
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

    def issue_token(self, target, receiver, max_depth=None):
        if isinstance(target, Token):
            token_type = "extension"
            prev_token = target
            asset = target.signature
        else:
            token_type = "root"
            prev_token = None
            asset = target

        for token, prev_token_tmp in self.issued_tokens.values():
            if (
                token.asset == asset
                and token.receiver == receiver
                and token.token_type == token_type
                and token.max_depth == max_depth
                and all([x.broker_id in token.brokers for x in self.connections])
            ):
                prev_token = prev_token_tmp
                break
        else:
            token = Token(
                self.session_key.public_serial(),
                [x.broker_id for x in self.connections],
                receiver,
                asset,
                token_type,
                max_depth,
            )
            signature = token.sign(self.session_key)

            self.issued_tokens[signature] = token, prev_token

        return ("token", ("issue", token.encode(), prev_token and prev_token.encode()))

    def revoke_tokens(self, asset):
        children = [tid for tid, t in self.issued_tokens.items() if t[0].asset == asset]
        [self.revoke_tokens(tid) for tid in children]
        self.issued_tokens.pop(asset, None)

    def extend_route(self, route, receiver, max_depth=None):

        if route.session[0] == self.session_key.public_serial():
            token_header = self.issue_token(route.channel, receiver, max_depth)
            route.tokens = [token_header[1][1]]
            return token_header

        for i, enc_token in enumerate(route.tokens):
            token = Token.decode(enc_token)
            if token.receiver == self.session_key.public_serial():
                route.tokens = route.tokens[: i + 1]

        token = Token.decode(route.tokens[-1], False)
        token_header = self.issue_token(token, receiver, max_depth)
        route.tokens.append(token_header[1][1])

    def clear(self, bundle_id):
        for connection in self.connections:
            connection.clear(bundle_id)

    async def send(self, header, payload=b"", bundle_id=None):
        for connection in self.connections:
            await connection.send(header, payload, bundle_id)

    def __repr__(self):
        return "Session %s %s" % (
            self.session_key.public_serial()[:4],
            self.instance_id[:2],
        )


class Channel:
    def __init__(self, session, channel_key=None, channel_key_pass=None, is_public=False):
        self.MAX_PAYLOAD_LEN = 2 ** 19
        self.MAX_COMPRESSION_LEN = 2 ** 19
        self.MAX_OUTBOX = 2 ** 4

        self.session = session
        self.channel_key = channel_key if isinstance(channel_key, PrivateKey) else PrivateKey(channel_key, channel_key_pass)
        self.is_public = is_public
        self.route = Route(
            list(set(c.broker_id for c in self.session.connections)),
            (self.session.session_key.public_serial(), self.session.instance_id),
            self.channel_key.public_serial(),
        )
        self.header_buffer = []
        self.chunks = {}
        self.messages = deque()
        self.lock = asyncio.Event()
        session.channels[self.channel_key.public_serial()] = self

        self.telekinesis = None

    def handle_message(self, source, destination, raw_payload, proof):
        if self.validate_token_chain(source.session[0], destination.tokens):
            message_tuple = None
            shared_key = SharedKey(self.channel_key, PublicKey(source.channel))
            payload = shared_key.decrypt(raw_payload[16:], raw_payload[:16])
            metadata = RequestMetadata(
                self.session, source, [{"raw_payload": raw_payload, "shared_key": shared_key.key, "proof": proof}]
            )

            if payload[:4] == b"\x00" * 4:
                if payload[4] == 0:
                    message_tuple = (metadata, bson.loads(payload[5:]))
                elif payload[4] == 255:
                    message_tuple = (metadata, bson.loads(zlib.decompress(payload[5:])))
                else:
                    raise BufferError("Received message with different encoding")
            else:
                ir, nr, mmid, chunk = payload[:2], payload[2:4], payload[4:8], payload[8:]
                i, n = int.from_bytes(ir, "big"), int.from_bytes(nr, "big")

                mid = source.session[0].encode() + mmid
                if mid not in self.chunks:
                    self.chunks[mid] = {}
                self.chunks[mid][i] = (chunk, metadata)

                if len(self.chunks[mid]) == n:
                    chunks = self.chunks.pop(mid)
                    payload = b"".join(chunks[ii][0] for ii in range(n))

                    combined_metadata = RequestMetadata(
                        metadata._session, metadata.caller, [chunks[ii][1].raw_messages[0] for ii in range(n)]
                    )
                    if payload[0] == 0:
                        message_tuple = (combined_metadata, bson.loads(payload[1:]))
                    elif payload[0] == 255:
                        message_tuple = (combined_metadata, bson.loads(zlib.decompress(payload[1:])))
                    else:
                        raise BufferError("Received message with different encoding")

            if message_tuple:
                if self.session.message_listener:
                    self.session.message_listener(message_tuple[0])
                # print('<<<', source, destination, {k: v is None for k, v in message_tuple[1].items()})
                if not self.telekinesis:
                    self.messages.appendleft(message_tuple)
                    self.lock.set()
                else:
                    asyncio.get_event_loop().create_task(self.telekinesis._handle_request(self, *message_tuple))
        else:
            self.session.logger.error(
                "Invalid Tokens: %s %s -> %s %s [%s]",
                source.session[0][:4],
                source.channel[:4],
                destination.session[0][:4],
                destination.channel[:4],
                destination.tokens,
            )

    async def recv(self):
        if not self.messages:
            self.lock.clear()
            await self.lock.wait()

        return self.messages.pop()

    def listen(self):
        listen_dict = self.route.to_dict()
        listen_dict["is_public"] = self.is_public
        listen_dict.pop("tokens")
        self.header_buffer.append(("listen", listen_dict))

        return self

    async def send(self, destination, payload_obj):
        # if payload_obj:
        # print('>>>', self.route, destination, {k: v is None for k, v in payload_obj.items()})
        def encrypt_slice(payload, max_payload, shared_key, message_id, n, i):
            if i < n:
                if n == 1:
                    chunk = b"\x00" * 4 + payload
                else:
                    if n > 2 ** 16:
                        raise MemoryError(f"Payload size {len(payload)/2**20} MiB is too large")
                    chunk = i.to_bytes(2, "big") + n.to_bytes(2, "big") + message_id + payload[i * max_payload : (i + 1) * max_payload]

                nonce = os.urandom(16)
                yield nonce + shared_key.encrypt(chunk, nonce)
                yield from encrypt_slice(payload, max_payload, shared_key, message_id, n, i + 1)

        async def execute(header, encrypted_slice_generator, message_id):
            for encrypted_slice in encrypted_slice_generator:
                await self.execute(header, encrypted_slice, message_id)

        source_route = self.route.clone()
        self.session.extend_route(source_route, destination.session[0])
        self.listen()

        payload = bson.dumps(payload_obj)

        if len(payload) < self.MAX_COMPRESSION_LEN:
            payload = b"\xff" + zlib.compress(payload)
        else:
            payload = b"\x00" + payload

        message_id = os.urandom(4)
        shared_key = SharedKey(self.channel_key, PublicKey(destination.channel))

        header = ("send", {"source": source_route.to_dict(), "destination": destination.to_dict()})

        n = (len(payload) - 1) // self.MAX_PAYLOAD_LEN + 1
        n_tasks = min(n, self.MAX_OUTBOX)
        gen = encrypt_slice(payload, self.MAX_PAYLOAD_LEN, shared_key, message_id, n, 0)

        try:
            await asyncio.gather(*(execute(header, gen, message_id) for _ in range(n_tasks)))
        except asyncio.CancelledError:
            self.session.clear(message_id)
            raise asyncio.CancelledError
        finally:
            self.session.clear(message_id)

        return self

    async def execute(self, header=None, payload=b"", bundle_id=None):
        all_headers = [h for h in self.header_buffer + [header] if h]
        lens = tuple((len(ujson.dumps(h, escape_forward_slashes=False)), h) for h in all_headers)

        groups = [[]]
        acc_len = 1
        for l, h in lens:
            if acc_len + l + 1 < 256**2:
                groups[-1].append(h)
                acc_len += l + 1
            else:
                groups.append([h])
                acc_len = l + 2

        for group in groups:
            await self.session.send(group, payload if 'send' in set(h[0] for h in group) else b'', bundle_id)
        self.header_buffer = []

        return self

    def __await__(self):
        return self.execute().__await__()

    def close(self):
        self.header_buffer.append(("close", self.route.to_dict()))
        self.session.revoke_tokens(self.channel_key.public_serial())

        self.session.channels.pop(self.channel_key.public_serial(), None)

        return self

    def validate_token_chain(self, source_id, tokens):
        if self.is_public or (source_id == self.session.session_key.public_serial()):
            return True
        if not tokens:
            return False

        asset = self.channel_key.public_serial()
        last_receiver = self.session.session_key.public_serial()
        max_depth = None

        for depth, token_string in enumerate(tokens):
            try:
                token = Token.decode(token_string)
            except InvalidSignature:
                return False
            if (token.asset == asset) and (token.issuer == last_receiver):
                if token.issuer == self.session.session_key.public_serial():
                    if token.signature not in self.session.issued_tokens:
                        return False
                if token.max_depth:
                    if not max_depth or (token.max_depth + depth) < max_depth:
                        max_depth = token.max_depth + depth
                if not max_depth or depth < max_depth:
                    last_receiver = token.receiver
                    asset = token.signature
                    if last_receiver == source_id:
                        return True
                    continue
            return False
        return False

    def __repr__(self):
        return "Channel %s %s - %s: %s" % (
            self.session.session_key.public_serial()[:4],
            self.session.instance_id[:2],
            self.channel_key.public_serial()[:4],
            self.telekinesis,
        )

    async def __aenter__(self):
        return self#.listen()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

        return isinstance(exc_type, Exception)

    def __getstate__(self):
        out = self.__dict__
        out['lock'] = None
        return out

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.lock = asyncio.Event()
        if self.telekinesis:
            self.telekinesis._session = self.session


class Route:
    def __init__(self, brokers, session, channel, tokens=None, parent_channel=None):
        self.brokers = brokers
        self.session = tuple(session)
        self.channel = channel
        self.tokens = tokens or []
        self._parent_channel = parent_channel

    def to_dict(self):
        return {"brokers": self.brokers, "session": self.session, "channel": self.channel, "tokens": self.tokens}

    def clone(self):
        return Route(**self.to_dict())

    def validate_token_chain(self, receiver):
        if self.session[0] != receiver:
            assert len(self.tokens) > 0
            prev_token = None
            for i, raw_token in enumerate(self.tokens):
                token = Token.decode(raw_token)
                if i == 0:
                    assert token.asset == self.channel
                    assert token.issuer == self.session[0]
                    assert token.token_type == "root"
                else:
                    assert token.asset == prev_token.signature
                    assert token.issuer == prev_token.receiver
                    assert token.token_type == "extension"
                if token.receiver == receiver:
                    break
                prev_token = token
            else:
                return False
        return True

    def __repr__(self):
        return f"Route {self.session[0][:4]} {self.session[1][:2]} - {self.channel[:4]}"

    def __eq__(self, route):
        return self.to_dict() == route.to_dict()

    def __await__(self):
        async def await_parent_channel():
            await self._parent_channel
            return self

        return self._parent_channel and await_parent_channel().__await__()


class RequestMetadata:
    def __init__(self, session, caller, raw_messages):
        self._session = session
        self.session_public_key = session.session_key.public_serial()
        self.caller = caller
        self.raw_messages = raw_messages
        self.reply_to = None
