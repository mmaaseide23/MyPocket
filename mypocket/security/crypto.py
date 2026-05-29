"""AES-256-GCM authenticated encryption for at-rest token storage.

Stored format: PREFIX + base64url(nonce || ciphertext || tag)
The PREFIX makes legacy plaintext rows distinguishable, so migration is cheap
and accidental double-encryption is impossible.
"""

from __future__ import annotations

import base64
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mypocket.security.keys import master_encryption_key

PREFIX = "enc-v1:"
NONCE_BYTES = 12


def encrypt(plaintext: str) -> str:
    if not isinstance(plaintext, str):
        raise TypeError(f"encrypt expects str, got {type(plaintext).__name__}")
    aesgcm = AESGCM(master_encryption_key())
    nonce = secrets.token_bytes(NONCE_BYTES)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt(stored: str) -> str:
    """Decrypt a previously-encrypted value, or return as-is if it's legacy plaintext."""
    if not stored.startswith(PREFIX):
        return stored  # legacy plaintext; will be re-encrypted on next write
    blob = base64.urlsafe_b64decode(stored[len(PREFIX) :])
    nonce, ct = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    aesgcm = AESGCM(master_encryption_key())
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")


def is_encrypted(stored: str | None) -> bool:
    return bool(stored and stored.startswith(PREFIX))
