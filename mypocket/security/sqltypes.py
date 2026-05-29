"""SQLAlchemy TypeDecorator that transparently encrypts string columns at rest."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


class EncryptedString(TypeDecorator):
    """Stores as TEXT; encrypts on write, decrypts on read.

    Legacy plaintext rows are returned unchanged on read, so an in-place migration
    can detect and encrypt them without breaking existing data.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        from mypocket.security.crypto import encrypt

        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        from mypocket.security.crypto import decrypt

        return decrypt(value)
