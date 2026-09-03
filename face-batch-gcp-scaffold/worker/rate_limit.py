from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class FixedWindowRateLimiter:
    """Small per-instance abuse guard; Cloud Armor remains the deployment edge limit."""

    def __init__(self, monotonic=time.monotonic):
        self.monotonic = monotonic
        self.events = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(
        self, key: tuple[str, str], limit: int, window_seconds: float = 60
    ) -> bool:
        now = self.monotonic()
        with self.lock:
            events = self.events[key]
            while events and events[0] <= now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True
