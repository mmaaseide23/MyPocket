"""Serialize bank sync and cutover in the supported single-process local server."""

from threading import RLock

bank_lock = RLock()
