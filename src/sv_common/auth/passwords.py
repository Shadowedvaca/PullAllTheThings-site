"""Password hashing and verification using bcrypt."""

import bcrypt


def hash_password(plain: str) -> str:
    """Hash a plain-text password. Returns bcrypt hash string."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode(), salt).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the stored bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())
