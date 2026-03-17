"""
One-way privacy token generation.

The token is a SHA-256 hash of (FEEDBACK_PRIVACY_SALT + normalized_contact_info).
It cannot be reversed without the salt. Different apps should use different salts.

Returns None when contact_info is absent or when is_anonymous is True.
"""
import hashlib
import os
from typing import Optional


def make_privacy_token(contact_info: Optional[str], is_anonymous: bool) -> Optional[str]:
    """
    Generate a one-way privacy token from contact info.

    Returns None if:
    - is_anonymous is True
    - contact_info is None or empty
    - FEEDBACK_PRIVACY_SALT env var is not set
    """
    if is_anonymous or not contact_info or not contact_info.strip():
        return None

    salt = os.environ.get("FEEDBACK_PRIVACY_SALT", "")
    if not salt:
        return None

    normalized = contact_info.strip().lower()
    return hashlib.sha256(f"{salt}{normalized}".encode()).hexdigest()
