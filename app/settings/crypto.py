"""SAA-132: Symmetric encryption for provider credentials at rest."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet

_KEY_FILE = Path(__file__).resolve().parent / ".encryption_key"


def _load_or_create_key() -> bytes:
    env_key = os.getenv("SETTINGS_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode()
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    return key


_fernet = Fernet(_load_or_create_key())


def encrypt_value(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()


def mask_value(plaintext: str) -> str:
    """Return a display-safe masked form, e.g. '••••••cd12'."""
    if len(plaintext) <= 4:
        return "•" * len(plaintext)
    return "•" * 6 + plaintext[-4:]
