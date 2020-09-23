import base64
import ujson
import os

from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.exceptions import InvalidSignature

class PrivateKey:
    def __init__(self, key_file=None, password=None):
        if key_file and os.path.exists(key_file):
            with open(key_file, 'rb') as kf:
                data = kf.read()
            self.key = serialization.load_pem_private_key(
                data,
                None if password is None else password.encode(),
                default_backend()
            )
            return 

        self.key = ec.generate_private_key(curve=ec.SECP256R1, backend=default_backend())
        
        if key_file:
            with open(key_file, 'wb') as kf:
                enc = serialization.NoEncryption() if password is None else \
                      serialization.BestAvailableEncryption(password.encode())
                data = self.key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=enc
                )
                kf.write(data)

    def sign(self, m):
        signature = self.key.sign(m, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(signature)
        return b''.join([x.to_bytes(32, 'big') for x in (r, s)])

    def public_serial(self):
        Q = self.key.public_key().public_numbers()
        return base64.b64encode(b''.join([x.to_bytes(32, 'big') 
                                          for x in (Q.x, Q.y)])).decode()

class PublicKey:
    def __init__(self, public_serial):
        self.key = ec.EllipticCurvePublicKey\
                     .from_encoded_point(ec.SECP256R1(), b'\x04'+base64.b64decode(public_serial))
    
    def verify(self, raw_signature, message):
        r = int.from_bytes(raw_signature[:32], 'big')
        s = int.from_bytes(raw_signature[32:], 'big')

        signature = utils.encode_dss_signature(r, s)

        self.key .verify(signature, 
                         message, 
                         ec.ECDSA(hashes.SHA256()))
    
class SharedKey:
    def __init__(self, private_key, public_key):
        self.key = private_key.key.exchange(
                    ec.ECDH(), public_key.key)

    def encrypt(self, message, nonce):
        encryptor = Cipher(algorithms.AES(self.key), 
                           modes.CTR(nonce), 
                           default_backend()).encryptor()
         

        return encryptor.update(message) + encryptor.finalize()

    def decrypt(self, ciphertext, nonce):
        decryptor = Cipher(algorithms.AES(self.key),
                           modes.CTR(nonce),
                           default_backend()).decryptor()
        

        return decryptor.update(ciphertext) + decryptor.finalize()

class Token:
    def __init__(self, issuer, receiver, asset, token_type, max_depth=None):
        self.issuer = issuer
        self.receiver = receiver
        self.asset = asset
        self.token_type = token_type
        self.max_depth = max_depth

    def verify_signature(self, signature):
        try:
            PublicKey(self.issuer).verify(base64.b64decode(signature.encode()), self.to_string().encode()) 
            return True
        except InvalidSignature:
            return False

    def to_dict(self):
        return {x: self.__getattribute__(x) for x in ['issuer', 'receiver', 'asset', 'token_type', 'max_depth']}

    def to_string(self):
        return ujson.dumps(self.to_dict())

    def sign(self, signing_key):
        return base64.b64encode(signing_key.sign(self.to_string().encode())).decode()
