import base64
import ujson
import os
import time

from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.exceptions import InvalidSignature


class PrivateKey:
    def __init__(self, key_file=None, password=None):
        self._public_serial_cache = None
        if key_file and os.path.exists(key_file):
            with open(key_file, "rb") as kf:
                data = kf.read()
            self.key = PrivateKey.from_private_serial(data, password).key
            return

        self.key = ec.generate_private_key(curve=ec.SECP256R1, backend=default_backend())

        if key_file:
            self.save_key_file(key_file, password)

    def sign(self, m):
        signature = self.key.sign(m, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(signature)
        return b"".join([x.to_bytes(32, "big") for x in (r, s)])

    def public_serial(self):
        if not self._public_serial_cache:
            Q = self.key.public_key().public_numbers()
            self._public_serial_cache = base64.b64encode(b"".join([x.to_bytes(32, "big") for x in (Q.x, Q.y)])).decode()
        return self._public_serial_cache

    def _private_serial(self, password=None):
        enc = (
            serialization.NoEncryption()
            if password is None
            else serialization.BestAvailableEncryption(password.encode())
        )
        return self.key.private_bytes(
            encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=enc,
        )
    def save_key_file(self, key_file, password=None):
        with open(key_file, "wb") as kf:
            kf.write(self._private_serial(password))

    @staticmethod
    def from_private_serial(data, password=None):
        out = PrivateKey()
        out.key = serialization.load_pem_private_key(
            data, None if password is None else password.encode(), default_backend()
        )
        return out

    def __getstate__(self):
        return {'private_serial': self._private_serial()}

    def __setstate__(self, state):
        self.key = PrivateKey.from_private_serial(state['private_serial']).key


class PublicKey:
    def __init__(self, public_serial):
        self.key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + base64.b64decode(public_serial))

    def verify(self, raw_signature, message):
        r = int.from_bytes(raw_signature[:32], "big")
        s = int.from_bytes(raw_signature[32:], "big")

        signature = utils.encode_dss_signature(r, s)

        self.key.verify(signature, message, ec.ECDSA(hashes.SHA256()))


class SharedKey:
    def __init__(self, private_key, public_key):
        self.key = private_key.key.exchange(ec.ECDH(), public_key.key)

    def encrypt(self, message, nonce):
        encryptor = Cipher(algorithms.AES(self.key), modes.CTR(nonce), default_backend()).encryptor()

        return encryptor.update(message) + encryptor.finalize()

    def decrypt(self, ciphertext, nonce):
        decryptor = Cipher(algorithms.AES(self.key), modes.CTR(nonce), default_backend()).decryptor()

        return decryptor.update(ciphertext) + decryptor.finalize()


class Token:
    def __init__(
        self,
        issuer,
        brokers,
        receiver,
        asset,
        token_type,
        max_depth=None,
        valid_from=None,
        valid_until=None,
        metadata=None,
    ):
        self.issuer = issuer
        self.brokers = brokers
        self.receiver = receiver
        self.asset = asset
        self.token_type = token_type
        self.max_depth = max_depth
        self.valid_from = valid_from or time.time()
        self.valid_until = valid_until
        self.metadata = metadata or {}
        self.signature = None

    def verify(self, signature):
        try:
            PublicKey(self.issuer).verify(base64.b64decode(signature.encode()), self._to_string().encode())
            self.signature = signature
            return True
        except InvalidSignature:
            return False

    def _to_dict(self):
        return {
            x: self.__getattribute__(x)
            for x in [
                "issuer",
                "brokers",
                "receiver",
                "asset",
                "token_type",
                "max_depth",
                "valid_from",
                "valid_until",
                "metadata",
            ]
        }

    def _to_string(self):
        return ujson.dumps(self._to_dict(), escape_forward_slashes=False)

    def encode(self):
        if not self.signature:
            raise RuntimeError("You need to sign the token before encoding it")
        return self.signature + "." + self._to_string()

    def sign(self, signing_key):
        self.signature = base64.b64encode(signing_key.sign(self._to_string().encode())).decode()
        return self.signature

    def __repr__(self):
        return f"{self.issuer[:4]} - {self.signature and self.signature[:4]}: {self.receiver[:4]} ({self.asset[:4]})"

    @staticmethod
    def decode(string, verify=True):
        token = Token(**ujson.loads(string[string.find(".") + 1 :]))
        if not verify:
            token.signature = string.split(".")[0]
            return token
        if token.verify(string.split(".")[0]):
            return token
        raise InvalidSignature
