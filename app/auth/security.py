"""SAA-137: Password hashing and session token generation."""
from __future__ import annotations

import hashlib
import secrets

_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
    return f"{salt}${digest}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, digest = hashed.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
    return secrets.compare_digest(candidate, digest)


def generate_token() -> str:
    return secrets.token_urlsafe(32)
