"""Keychain-backed key storage.

On macOS, `keyring` uses Security.framework to read/write the system Keychain.
Generates a fresh random key on first access and persists it for reuse.

Keys are memoized in-process after first read — the Keychain prompt only happens
once per server lifetime, and subsequent encrypt/decrypt calls don't pay Keychain
RPC latency. Restarting uvicorn re-fetches.
"""

from __future__ import annotations

import logging
import secrets
import threading

import keyring

logger = logging.getLogger(__name__)

SERVICE = "mypocket"
COOKIE_KEY_ID = "cookie_signing_key"
MASTER_KEY_ID = "master_encryption_key"

_cache: dict[str, bytes] = {}
_cache_lock = threading.Lock()


def _get_or_create(key_id: str, length: int = 32) -> bytes:
    cached = _cache.get(key_id)
    if cached is not None:
        return cached
    with _cache_lock:
        # Re-check inside the lock in case another thread populated it.
        cached = _cache.get(key_id)
        if cached is not None:
            return cached
        stored = keyring.get_password(SERVICE, key_id)
        if stored:
            key = bytes.fromhex(stored)
        else:
            key = secrets.token_bytes(length)
            keyring.set_password(SERVICE, key_id, key.hex())
            logger.info("security: generated new %s in Keychain", key_id)
        _cache[key_id] = key
        return key


def cookie_signing_key() -> bytes:
    """HMAC key used to sign session cookies (32 bytes)."""
    return _get_or_create(COOKIE_KEY_ID)


def master_encryption_key() -> bytes:
    """AES-256-GCM key used to encrypt access tokens at rest (32 bytes)."""
    return _get_or_create(MASTER_KEY_ID)


def invalidate_cache() -> None:
    """Drop the in-process key cache. Use after rotating a key in Keychain."""
    with _cache_lock:
        _cache.clear()
