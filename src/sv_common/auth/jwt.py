"""JWT token creation and validation."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt

from guild_portal.config import get_settings

_ALGORITHM = "HS256"


def create_access_token(
    user_id: int,
    member_id: int,
    rank_level: int,
    expires_minutes: int | None = None,
    session_id: str | None = None,
) -> str:
    """Create a signed JWT bound to an independently revocable session id."""
    settings = get_settings()
    if expires_minutes is not None:
        exp_minutes = expires_minutes
    elif rank_level >= settings.jwt_privileged_rank_level:
        exp_minutes = settings.jwt_privileged_expire_minutes
    else:
        exp_minutes = settings.jwt_member_expire_minutes
    payload = {
        "user_id": user_id,
        "member_id": member_id,
        "rank_level": rank_level,
        "jti": session_id or str(uuid4()),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=exp_minutes),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Returns payload dict.

    Raises jwt.ExpiredSignatureError if expired.
    Raises jwt.InvalidTokenError for any other validation failure.
    """
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
