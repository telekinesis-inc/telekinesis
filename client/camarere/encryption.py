import cryptography
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
import base64



PAD_ENCRYPT = padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
)
PAD_SIGN = padding.PSS(
    mgf=padding.MGF1(hashes.SHA256()),
    salt_length=padding.PSS.MAX_LENGTH
)

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


# public_key.encrypt(b'test', PAD_ENCRYPT), PAD_ENCRYPT)

# public_key.verify(, b'test2', PAD_SIGN, hashes.SHA3_256())

# read_data = []
# private_key = read_private()
# with open('test.txt', "rb") as f:
#     for encrypted in f:
#         print(encrypted)
#         read_data.append(
#             private_key.decrypt(
#                 encrypted,
#             padding.OAEP(
#                 mgf=padding.MGF1(algorithm=hashes.SHA256()),
#                 algorithm=hashes.SHA256(),
#                 label=None
#             )))