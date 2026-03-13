"""Fernet symmetric encryption utilities.

Provides two independent encryption contexts:
- JWT-derived key (jwt_secret): for Discord bot tokens, Blizzard secrets stored in DB
- BNET_TOKEN_ENCRYPTION_KEY (env var): dedicated key for Battle.net OAuth tokens
"""
import base64
import hashlib
import os
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


# ---------------------------------------------------------------------------
# Battle.net OAuth token encryption (dedicated BNET_TOKEN_ENCRYPTION_KEY)
# ---------------------------------------------------------------------------


def get_bnet_fernet() -> Fernet:
    """Return a Fernet instance keyed with BNET_TOKEN_ENCRYPTION_KEY.

    The key must be a valid Fernet key (URL-safe base64-encoded 32-byte key).
    Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """
    key = os.environ.get("BNET_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("BNET_TOKEN_ENCRYPTION_KEY is not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_bnet_token(token: str) -> str:
    """Encrypt a Battle.net OAuth token with the dedicated BNET key."""
    return get_bnet_fernet().encrypt(token.encode()).decode()


def decrypt_bnet_token(encrypted: str) -> str:
    """Decrypt a Battle.net OAuth token. Raises InvalidToken on failure."""
    return get_bnet_fernet().decrypt(encrypted.encode()).decode()
