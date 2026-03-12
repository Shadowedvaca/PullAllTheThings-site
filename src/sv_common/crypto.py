"""Fernet symmetric encryption using JWT_SECRET_KEY as seed."""
import base64
import hashlib
from cryptography.fernet import Fernet


def _derive_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(value: str, jwt_secret: str) -> str:
    """Encrypt a string value using Fernet. Returns base64-encoded ciphertext."""
    f = Fernet(_derive_key(jwt_secret))
    return f.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, jwt_secret: str) -> str:
    """Decrypt a Fernet-encrypted token. Raises cryptography.fernet.InvalidToken on failure."""
    f = Fernet(_derive_key(jwt_secret))
    return f.decrypt(token.encode("ascii")).decode("utf-8")
