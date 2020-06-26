from collections import namedtuple
import base64
import json
import pickle
import zlib
import asyncio
from uuid import uuid4
import time
import re

import cryptography
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes

PAD_ENCRYPT = padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
)
PAD_SIGN = padding.PSS(
    mgf=padding.MGF1(hashes.SHA256()),
    salt_length=padding.PSS.MAX_LENGTH
)
MARKER_NONJSON_SERIALIZATION = 'TELEKINESIS_BASE85_ZLIB_PICKLE_DUMP.'

Token = namedtuple('Token', ['created_by', 'signature', 'payload'])

encode = lambda x, enc=base64.b64encode: str(enc(x), 'utf-8')
decode = lambda y, enc=base64.b64decode: enc(bytes(y, 'utf-8'))

def deserialize_private_key(private_key_serial, password=None):
    return serialization.load_pem_private_key(
        bytes(private_key_serial, 'utf-8'),
        password=None if password is None else bytes(password, 'utf-8'),
        backend=default_backend()
    )

def create_private_key():
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=3072,
        backend=default_backend()
    )

def serialize_private_key(private_key, password=None):
    serial_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption() if password is None
                             else serialization.BestAvailableEncryption(bytes(password, 'utf-8'))
    )
    return str(serial_private, 'utf-8')

def generate_public_serial(private_key):
    serial_pub = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return encode(serial_pub)

def deserialize_public_key(public_serial):
    public_key = serialization.load_pem_public_key(
        decode(public_serial),
        backend=default_backend()
    )
    return public_key

def encrypt(message, public_serial):
    return encode(deserialize_public_key(public_serial).encrypt(bytes(message, 'utf-8'), PAD_ENCRYPT))

def decrypt(encrypted, private_key):
    return str(private_key.decrypt(decode(encrypted), PAD_ENCRYPT), 'utf-8')

def sign(message, private_key):
    return encode(private_key.sign(bytes(message, 'utf-8'), PAD_SIGN, hashes.SHA3_256()), base64.b32encode)

def verify(signature, message, public_serial):
    return deserialize_public_key(public_serial)\
             .verify(decode(signature, base64.b32decode), bytes(message, 'utf-8'), PAD_SIGN, hashes.SHA3_256())

def extend_role(from_private_key, from_role, recipient, sub_role):
    payload = json.dumps({
        'from_role': from_role,
        'recipient': recipient,
        'sub_role': sub_role,
    })
    return Token(generate_public_serial(from_private_key),
                 sign(payload, from_private_key),
                 payload)

def check_extension(token):
    try:
        verify(token.signature, token.payload, token.created_by)
        return True
    except:
        pass
    return False

def list_roles(root_serial, public_serial, *tokens):
    def add_sub(d, i, v):
        if i not in d:
            d[i] = set()
        d[i].add(v)            
    
    validated_serials = {}
    add_sub(validated_serials, root_serial, ('', 0))
    
    validated_roles = {}
    
    remaining_tokens = set([Token(*t) for t in tokens])
    while True:
        for token in remaining_tokens:
            if token.created_by in validated_serials:
                payload = get_token_payload(token)
                if payload['from_role'] in validated_serials[token.created_by]:
                    if check_extension(token):
                        new_role = get_payload_role(payload)
                        if len(payload['recipient']) < len(public_serial): # Recipient is role
                            add_sub(validated_roles, payload['recipient'], new_role)
                            for serial in validated_serials:
                                if payload['recipient'] in validated_serials[serial]:
                                    add_sub(validated_serials, serial, new_role)
                        else:
                            add_sub(validated_serials, payload['recipient'], new_role)
                            if new_role in validated_roles:
                                for sub_role in validated_roles[new_role]:
                                    add_sub(validated_serials, payload['recipient'], sub_role)
                    remaining_tokens.remove(token)
                    break
        else:
            return validated_serials.get(public_serial) or set()

def check_min_role(min_roles, roles):
    if '*' in [m[0] for m in min_roles]:
        return True

    for min_role in min_roles:
        for role in roles:

            role_path = ('/' if role[0].strip('/') else '') + role[0].strip('/') + '/'
            min_role_path = ('/' if min_role[0].strip('/*') else '') + min_role[0].strip('/*') + '/'

            if min_role and min_role[0] and min_role[0][-1] == '*':
                nchars = min(len(min_role_path), len(role_path))
            else:
                nchars = len(role_path)

            if min_role_path[:nchars] == role_path[:nchars]:
                if role[1] <= min_role[1]:
                    return True
    return False

def get_payload_role(payload):
    sep = '' if '' in [payload['from_role'][0], payload['sub_role'][0]] else '/'
    new_role = (payload['from_role'][0] + sep + payload['sub_role'][0],
                max(payload['from_role'][1], payload['sub_role'][1]))

    return new_role

def get_token_payload(token):
    payload = json.loads(token.payload)
    for k in payload:
        if isinstance(payload[k], list):
            payload[k] = tuple(payload[k])

    return payload

def get_certificate_dependencies(role_certificates, final_role):
    remaining_roles = set([tuple(final_role)])
    seen_roles = set()
    certificate_dependencies = set()
    while remaining_roles:
        role = remaining_roles.pop()
        seen_roles.add(role)
        for rc in role_certificates:
            cert = Token(*rc)
            if check_extension(cert):
                payload = get_token_payload(cert)
                if get_payload_role(payload) == role:
                    certificate_dependencies.add(cert)
                    if len(payload['recipient']) < len(cert.created_by):
                        if payload['recipient'] not in seen_roles:
                            remaining_roles.add(payload['recipient'])
                    if payload['from_role'] not in seen_roles:
                        remaining_roles.add(payload['from_role'])
    return list(certificate_dependencies)

async def decode_message(raw_message, connection, deserialize_pickle=True):
    def postprocess(data):
        if data is None:
            return data
        elif isinstance(data, (bool, int, float)):
            return data
        elif isinstance(data, (tuple, list)):
            return [postprocess(x) for x in data]
        elif isinstance(data, dict):
            return {postprocess(k): postprocess(v) for k, v in data.items()}
        elif isinstance(data, str):
            if deserialize_pickle and (data[:len(MARKER_NONJSON_SERIALIZATION)] == MARKER_NONJSON_SERIALIZATION):
                return pickle.loads(zlib.decompress(base64.b85decode(bytes(data[len(MARKER_NONJSON_SERIALIZATION):], 'utf-8'))))
            return data
        raise Exception('Telekinesis: message decode error')

    thread_id, message_id = raw_message[:32], raw_message[32:64]
    chunk_i = None if (len(raw_message) == 64) or (raw_message[64] != '.') else int(raw_message[65:68])

    if (len(raw_message) == 64) or ((len(raw_message) == 72) and raw_message[64] == '.'):
        # print('ack recieved', thread_id, message_id, chunk_i)
        event = connection.pending_messages.get((thread_id, message_id, chunk_i))
        if event is not None:
            event.set()
            return thread_id, None
        raise Exception('there should be an event here', thread_id, message_id, chunk_i)

    # print('sending ack', raw_message[:(64 if chunk_i is None else 72)])
    await connection.websocket.send(raw_message[:(64 if chunk_i is None else 72)])
    
    connection.seen_messages = (set(), set())

    tid = int(time.time()/30)
    if tid not in connection.seen_messages[tid%2]:
        connection.seen_messages[tid%2].clear()
        connection.seen_messages[tid%2].add(tid)
    mid = (thread_id, message_id)

    if mid not in connection.seen_messages[0] and mid not in connection.seen_messages[1]:
        if raw_message[64] != '.':
            message_in = json.loads(raw_message[64:])
        else:
            chunk_n = int(raw_message[68:71])

            chunk = raw_message[72:]
            chunks = connection._chunks

            if (thread_id, message_id) not in chunks:
                chunks[(thread_id, message_id)] = {}
            
            chunks[(thread_id, message_id)][chunk_i] = chunk

            if len(chunks[(thread_id, message_id)]) < chunk_n:
                return thread_id, None
            
            d = chunks.pop((thread_id, message_id))
            message_in = json.loads(''.join([d[i] for i in range(chunk_n)]))
    else:
        # print('duplicate message', thread_id, message_id)
        message_in = None
    
    connection.seen_messages[tid%2].add(mid)
    return thread_id, postprocess(message_in)

async def encode_message(thread_id, message, connection):
    def prepare(data):
        if data is None:
            return data
        elif isinstance(data, (bool, int, float, str)):
            return data
        elif isinstance(data, (tuple, list)):
            return [prepare(x) for x in data]
        elif isinstance(data, dict):
            return {prepare(k): prepare(v) for k, v in data.items()}
        return MARKER_NONJSON_SERIALIZATION+str(base64.b85encode(zlib.compress(pickle.dumps(data, 4))), 'utf-8')

    dump = json.dumps(prepare(message))
    message_id = uuid4().hex

    # print('SEND>', thread_id, message_id, message.get('method') if 'get' in dir(message) else None)

    if len(dump) >= (2**20-32):
        n = (len(dump)-1)//(2**20-72) + 1
        for i in range(n):
            asyncio.get_event_loop().create_task(
                ensure_delivery(connection, thread_id+message_id+'.%03d'%i+'%03d'%n+'.'+\
                dump[(i*(2**20-72)):((i+1)*(2**20-72))]))
    else:
        asyncio.get_event_loop().create_task(ensure_delivery(connection, thread_id + message_id + dump))

async def event_wait(event):
    await event.wait()
    return

async def ensure_delivery(connection, raw_message):
    thread_id, message_id = raw_message[:32], raw_message[32:64]
    if raw_message[64] != '.':
        chunk_i = None
    else:
        chunk_i = int(raw_message[65:68])
    
    # print('START', thread_id, message_id, chunk_i)

    
    event = asyncio.Event()
    connection.pending_messages[(thread_id, message_id, chunk_i)] = event

    for i in range(10):
        try:
            await connection.websocket.send(raw_message)
            await asyncio.wait_for(event_wait(event), 3)
            connection.pending_messages.pop((thread_id, message_id, chunk_i))

            # print('<ACK>', thread_id, message_id, chunk_i)
            return
        except asyncio.TimeoutError:
            await asyncio.sleep(3)
            # print('RET%02d'%i, thread_id, message_id, chunk_i)
        except Exception as e:
            print('retrying message delivery', thread_id, message_id, chunk_i, e)
    raise Exception('Max retries sending message exceeded', thread_id, message_id, chunk_i)

def check_function_signature(signature):
    if '\n' in signature or (signature != re.sub(r'(?:[^A-Za-z0-9_])lambda(?=[\)\s\:])', '', signature)):
        return 'Unsafe signature'

    