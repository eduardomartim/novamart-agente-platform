"""Process-local shared state: the default, and the reference behaviour.

This is not a stub. It is what the platform has always done, expressed behind
the same interface the Redis backend implements, so that:

* the offline suite never needs a server to run;
* single-process deployments keep working unchanged;
* the two implementations can be tested against the *same* assertions, which is
  the only way to know they actually agree.

Its limits are the honest ones: state dies with the process, and two processes
using it share nothing. The platform reports which backend is active rather
than leaving that to be inferred.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class LocalBackend:
    """In-memory implementation of the shared-state operations.

    Wall-clock (``time.time``) rather than ``time.monotonic``, deliberately:
    the Redis backend has no choice but wall-clock, and two implementations
    that disagree about what "expired" means are two different behaviours
    wearing one interface.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        #: namespace -> key -> (expires_at, hard_expires_at, payload)
        self._entries: dict[str, dict[str, tuple[float, float, str]]] = defaultdict(dict)
        #: key -> (value, expires_at)
        self._counters: dict[str, tuple[int, float]] = {}
        #: key -> timestamps
        self._windows: defaultdict[str, deque[float]] = defaultdict(deque)

    @property
    def kind(self) -> str:
        return "local"

    def ping(self) -> bool:
        return True

    # -------------------------------------------------------------- entries

    def _purge(self, namespace: str, now: float) -> dict[str, tuple[float, float, str]]:
        """Drop entries past their *hard* expiry.

        Logical expiry does not remove anything: an aged-out entry has to stay
        readable for a while so the caller can tell "expired" from "never
        existed". The hard expiry is what finally reclaims it.
        """
        bucket = self._entries[namespace]
        for key in [k for k, (_, hard, _) in bucket.items() if now >= hard]:
            del bucket[key]
        return bucket

    def entry_put(
        self, namespace: str, key: str, payload: str, *, ttl_seconds: float,
        max_entries: int, grace_seconds: float, now: float,
    ) -> bool:
        with self._lock:
            bucket = self._purge(namespace, now)
            live = sum(1 for expires, _, _ in bucket.values() if now < expires)
            if key not in bucket and live >= max_entries:
                return False
            bucket[key] = (now + ttl_seconds, now + ttl_seconds + grace_seconds, payload)
            return True

    def entry_peek(self, namespace: str, key: str, *, now: float) -> str | None:
        with self._lock:
            bucket = self._purge(namespace, now)
            entry = bucket.get(key)
            return entry[2] if entry else None

    def entry_take(self, namespace: str, key: str, *, now: float) -> str | None:
        with self._lock:
            bucket = self._purge(namespace, now)
            entry = bucket.pop(key, None)
            return entry[2] if entry else None

    def entry_count(self, namespace: str, *, now: float) -> int:
        with self._lock:
            bucket = self._purge(namespace, now)
            return sum(1 for expires, _, _ in bucket.values() if now < expires)

    # -------------------------------------------------------------- counter

    def counter_charge(self, key: str, *, limit: int, ttl_seconds: float) -> bool:
        with self._lock:
            now = time.time()
            value, expires = self._counters.get(key, (0, 0.0))
            if now >= expires:
                value, expires = 0, now + ttl_seconds
            if value >= limit:
                self._counters[key] = (value, expires)
                return False
            self._counters[key] = (value + 1, expires)
            return True

    def counter_value(self, key: str) -> int:
        with self._lock:
            value, expires = self._counters.get(key, (0, 0.0))
            return value if time.time() < expires else 0

    # --------------------------------------------------------------- window

    def window_allow(
        self, key: str, *, windows: tuple[tuple[float, int], ...], now: float
    ) -> bool:
        with self._lock:
            longest = max(span for span, _ in windows)
            events = self._windows[key]
            while events and events[0] <= now - longest:
                events.popleft()
            for span, limit in windows:
                cutoff = now - span
                if sum(1 for t in events if t >= cutoff) >= limit:
                    return False
            events.append(now)
            return True

    def window_check(
        self, key: str, *, windows: tuple[tuple[float, int], ...], now: float
    ) -> bool:
        """Report room without taking any. See ``_WINDOW_CHECK``."""
        with self._lock:
            events = self._windows[key]
            for span, limit in windows:
                cutoff = now - span
                if sum(1 for t in events if t >= cutoff) >= limit:
                    return False
            return True
