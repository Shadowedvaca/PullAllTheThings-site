"""Password hashing and verification using bcrypt."""

import secrets

import bcrypt

# Unambiguous characters — no 0/O, 1/I/L
_TEMP_PW_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789abcdefghjkmnpqrstuvwxyz"


def generate_temp_password(length: int = 12) -> str:
    """Generate a random temporary password using an unambiguous character set."""
    return "".join(secrets.choice(_TEMP_PW_ALPHABET) for _ in range(length))


def hash_password(plain: str) -> str:
    """Hash a plain-text password. Returns bcrypt hash string."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode(), salt).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the stored bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())
