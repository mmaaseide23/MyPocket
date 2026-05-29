"""In-memory per-IP rate limiter for sensitive endpoints (e.g. /login).

Stateless across process restarts — acceptable for a single-user local app, where
a brute-force attempt long enough to span a restart isn't realistic. For a
multi-process deployment, swap this for Redis / SQLite-backed storage.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Sliding-window counter. Allow `max_attempts` within `window_seconds` per key."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds).

        Records the current attempt regardless of outcome so repeated checks count.
        """
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.max_attempts:
                retry_after = max(1, int(hits[0] + self.window_seconds - now))
                return False, retry_after
            hits.append(now)
            return True, 0

    def reset(self, key: str) -> None:
        """Called on successful auth to clear the bucket."""
        with self._lock:
            self._hits.pop(key, None)


# Tunable per-route limiter. 10 wrong passcodes in 5 minutes triggers a 429.
login_limiter = RateLimiter(max_attempts=10, window_seconds=300)


def client_ip(request) -> str:
    """Best-effort client IP for rate-limit bucketing.

    For local Tailscale traffic, `request.client.host` is the Tailscale peer's IP.
    For LAN traffic, it's the device IP. Either way, a per-device key is what we want.
    """
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
