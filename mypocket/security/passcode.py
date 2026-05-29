"""Passcode hashing using scrypt (stdlib, no external deps).

Stored format: scrypt$N$r$p$salt_hex$hash_hex
"""

from __future__ import annotations

import hashlib
import secrets

# Cost parameters: N=16384 r=8 p=1 is the OWASP-recommended baseline for scrypt.
# Takes ~50ms on a modern Mac — fast enough for interactive login, slow enough
# to make offline brute force expensive.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
DK_LEN = 64


def hash_passcode(passcode: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        passcode.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=DK_LEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_passcode(passcode: str, stored: str) -> bool:
    """Constant-time verify against a hash produced by `hash_passcode`."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = bytes.fromhex(parts[4])
        expected_hex = parts[5]
    except ValueError:
        return False
    derived = hashlib.scrypt(
        passcode.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected_hex) // 2,
    )
    return secrets.compare_digest(derived.hex(), expected_hex)
