from collections import namedtuple
import base64
import json
from uuid import uuid4

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
                payload = json.loads(token.payload)
                for k in payload:
                    if isinstance(payload[k], list):
                        payload[k] = tuple(payload[k])

                if payload['from_role'] in validated_serials[token.created_by]:
                    if check_extension(token):
                        sep = '' if '' in [payload['from_role'][0], payload['sub_role'][0]] else '/'
                        new_role = (payload['from_role'][0] + sep + payload['sub_role'][0],
                                    max(payload['from_role'][1], payload['sub_role'][1]))

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

def decode_message(raw_message, threads):
    thread_id = raw_message[:32]
    if threads is not None and thread_id not in threads:
        threads[thread_id] = {
            'thread_id': thread_id,
            'chunks': {},
            'services': set(),
            'call_requests': {},
            'calls_processing': {}}

    if raw_message[32] != '.':
        message_in = json.loads(raw_message[32:])
    else:
        chunk_id = raw_message[33:65]
        chunk_x = int(raw_message[65:68])
        chunk_n = int(raw_message[68:71])

        chunk = raw_message[72:]

        chunks = threads[thread_id]['chunks']

        if chunk_id not in chunks:
            chunks[chunk_id] = {}
        
        chunks[chunk_id][chunk_x] = chunk

        if len(chunks[chunk_id]) < chunk_n:
            return thread_id, None
        
        d = chunks.pop(chunk_id)
        message_in = json.loads(''.join([d[i] for i in range(chunk_n)]))

    return thread_id, message_in

async def encode_message(thread_id, message, websocket):
    dump = json.dumps(message)
    if len(dump) >= (2**20-32):
        n = (len(dump)-1)//(2**20-72) + 1
        chunk_id = uuid4().hex
        for i in range(n):
            await websocket.send(thread_id+'.'+chunk_id+'%03d'%i+'%03d'%n+'.'+\
                            dump[(i*(2**20-72)):((i+1)*(2**20-72))])
    else:
        await websocket.send(thread_id+dump)
