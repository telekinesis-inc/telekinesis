from collections import namedtuple
import base64
import json

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

def read_private_key(path, password=None):
    with open(path, "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None if password is None else bytes(password, 'utf-8'),
            backend=default_backend()
        )
    return private_key

def create_private_key(path, password=None):
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=3072,
        backend=default_backend()
    )
    serial_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption() if password is None
                             else serialization.BestAvailableEncryption(bytes(password, 'utf-8'))
    )
    with open(path, 'wb') as f: f.write(serial_private)

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

def extend_permissions(from_private_key, from_role, recipient, sub_role, recipient_is_role=False):
    payload = json.dumps({
        'from_role': from_role,
        'recipient': recipient,
        'recipient_is_role': recipient_is_role,
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
    add_sub(validated_serials, root_serial, ())
    
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
                        new_role = tuple(payload['from_role']) + tuple(payload['sub_role'])
                        if payload['recipient_is_role']:
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
            return validated_serials.get(public_serial)