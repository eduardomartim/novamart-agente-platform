"""The one place that knows Redis exists.

Everything else in the platform keeps talking to the interfaces it already had
-- ``ProviderBudget``, a pending registry, a rate limiter. This module owns the
connection and the handful of atomic operations those interfaces need, so
swapping the backend never means touching the policy engine, the gateway, or an
agent.

Why the atomicity lives here rather than in the callers
------------------------------------------------------
Every operation below is one of the classic read-modify-write races:

* "is there room, and if so store it" (pending registry)
* "is the budget spent, and if not charge it" (provider ledger)
* "how many requests in the window, and may I add one" (rate limiter)

Written as ``GET`` then ``SET`` from Python, each of those is correct on one
replica and wrong on two: both callers read "one left" and both proceed. They
are therefore expressed as Lua scripts, which Redis runs to completion without
interleaving. A caller cannot get this wrong because a caller cannot see the
pieces.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis import Redis


class SharedStateUnavailable(RuntimeError):
    """The configured shared backend could not be reached.

    Deliberately *not* silently downgraded to process-local state. A platform
    that quietly stops sharing its rate limit is a platform whose limit is
    suddenly N times larger than the operator configured, and nothing in the
    logs says so. Callers decide what to do; the default is to fail closed.
    """


class Backend(Protocol):
    """The operations shared state actually needs. Nothing more."""

    @property
    def kind(self) -> str:
        """``"local"`` or ``"redis"`` -- surfaced so the mode is never guessed."""
        ...

    def ping(self) -> bool: ...

    # -- bounded, expiring entries (pending confirmations) -------------------
    def entry_put(
        self, namespace: str, key: str, payload: str, *, ttl_seconds: float,
        max_entries: int, grace_seconds: float, now: float,
    ) -> bool: ...

    def entry_peek(self, namespace: str, key: str, *, now: float) -> str | None: ...

    def entry_take(self, namespace: str, key: str, *, now: float) -> str | None: ...

    def entry_count(self, namespace: str, *, now: float) -> int: ...

    # -- capped counter (provider-call ledger) -------------------------------
    def counter_charge(self, key: str, *, limit: int, ttl_seconds: float) -> bool: ...

    def counter_value(self, key: str) -> int: ...

    # -- sliding window (rate limiter) ---------------------------------------
    def window_allow(
        self, key: str, *, windows: tuple[tuple[float, int], ...], now: float
    ) -> bool: ...

    def window_check(
        self, key: str, *, windows: tuple[tuple[float, int], ...], now: float
    ) -> bool: ...


# --------------------------------------------------------------------- Lua --

#: Store an entry only if the namespace has room. Membership is tracked in a
#: sorted set scored by expiry so the count is maintained without scanning
#: keys, and stale members are dropped on every write.
#:
#: The value is stored with a TTL *longer* than the logical one (``grace``).
#: That is what preserves an existing behaviour: the platform distinguishes
#: "this confirmation aged out" (409) from "no such confirmation" (404), and a
#: key that Redis has already evicted cannot tell those apart. The surviving
#: entry acts as a tombstone until the grace period ends.
_PUT = """
local idx, now, key, payload = KEYS[1], tonumber(ARGV[1]), ARGV[2], ARGV[3]
local ttl, max_entries, grace = tonumber(ARGV[4]), tonumber(ARGV[5]), tonumber(ARGV[6])
redis.call('ZREMRANGEBYSCORE', idx, '-inf', now)
if redis.call('ZSCORE', idx, key) == false then
  if redis.call('ZCARD', idx) >= max_entries then return 0 end
end
redis.call('ZADD', idx, now + ttl, key)
redis.call('SET', KEYS[2], payload, 'PX', math.floor((ttl + grace) * 1000))
redis.call('PEXPIRE', idx, math.floor((ttl + grace) * 1000 * 4))
return 1
"""

#: Single-use consumption. ``GETDEL`` is the whole point: two replicas racing to
#: approve the same confirmation both call this, and Redis serialises them, so
#: exactly one receives the payload and the other receives nil.
_TAKE = """
local idx, key = KEYS[1], ARGV[1]
local value = redis.call('GET', KEYS[2])
redis.call('DEL', KEYS[2])
redis.call('ZREM', idx, key)
return value
"""

#: Charge one unit if the cap allows it. The increment and the test are one
#: operation, so "both saw one left" cannot happen.
_CHARGE = """
local key, limit, ttl = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2])
local used = tonumber(redis.call('GET', key) or '0')
if used >= limit then return 0 end
local now = redis.call('INCR', key)
if now == 1 then redis.call('PEXPIRE', key, math.floor(ttl * 1000)) end
return 1
"""

#: Read-only counterpart of ``_WINDOW``. The policy engine asks whether quota
#: remains *without* consuming it, and a check that charged for asking would
#: make the limit tighter than configured every time a decision is evaluated.
_WINDOW_CHECK = """
local key, now = KEYS[1], tonumber(ARGV[1])
for i = 2, #ARGV, 2 do
  local span = tonumber(ARGV[i])
  local limit = tonumber(ARGV[i + 1])
  if redis.call('ZCOUNT', key, now - span, '+inf') >= limit then return 0 end
end
return 1
"""

#: Sliding window across several (span, limit) pairs at once. Members are
#: timestamped, so pruning is a range delete rather than a scan. Nothing is
#: recorded unless *every* window has room, which keeps a rejected request from
#: consuming quota it was never granted.
_WINDOW = """
local key, now = KEYS[1], tonumber(ARGV[1])
local longest = tonumber(ARGV[2])
local member = ARGV[3]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - longest)
for i = 4, #ARGV, 2 do
  local span = tonumber(ARGV[i])
  local limit = tonumber(ARGV[i + 1])
  if redis.call('ZCOUNT', key, now - span, '+inf') >= limit then return 0 end
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, math.floor(longest * 1000) + 1000)
return 1
"""


class RedisBackend:
    """Shared state on Redis. Constructed only when ``REDIS_URL`` is set."""

    def __init__(self, client: Redis, *, prefix: str = "agent-platform") -> None:
        self._r = client
        self._prefix = prefix
        self._put = client.register_script(_PUT)
        self._take = client.register_script(_TAKE)
        self._charge = client.register_script(_CHARGE)
        self._window = client.register_script(_WINDOW)
        self._window_check = client.register_script(_WINDOW_CHECK)

    @property
    def kind(self) -> str:
        return "redis"

    # ------------------------------------------------------------- helpers
    def _idx(self, namespace: str) -> str:
        return f"{self._prefix}:{namespace}:index"

    def _entry(self, namespace: str, key: str) -> str:
        return f"{self._prefix}:{namespace}:entry:{key}"

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception as exc:
            raise SharedStateUnavailable(f"redis ping failed: {type(exc).__name__}") from exc

    # -------------------------------------------------------------- entries
    def entry_put(
        self, namespace: str, key: str, payload: str, *, ttl_seconds: float,
        max_entries: int, grace_seconds: float, now: float,
    ) -> bool:
        # `now` comes from the caller rather than from Redis or from
        # ``time.time`` here. The registry owns one clock; a backend that
        # consulted a second one would disagree with it about which entries are
        # still occupying capacity, which is exactly the bug this parameter
        # removes.
        result = self._put(
            keys=[self._idx(namespace), self._entry(namespace, key)],
            args=[now, key, payload, ttl_seconds, max_entries, grace_seconds],
        )
        return bool(int(result))

    def entry_peek(self, namespace: str, key: str, *, now: float) -> str | None:
        # `now` is unused against Redis, which expires keys itself. It is in the
        # signature because the local backend cannot, and one interface that
        # takes the clock in every implementation is easier to reason about than
        # two that disagree about who owns it.
        del now
        raw = self._r.get(self._entry(namespace, key))
        return _text(raw)

    def entry_take(self, namespace: str, key: str, *, now: float) -> str | None:
        del now
        raw = self._take(
            keys=[self._idx(namespace), self._entry(namespace, key)], args=[key]
        )
        return _text(raw)

    def entry_count(self, namespace: str, *, now: float) -> int:
        idx = self._idx(namespace)
        self._r.zremrangebyscore(idx, "-inf", now)
        # The sync client is typed as possibly-awaitable because it shares
        # stubs with the async one; this class only ever holds the sync one.
        return int(cast("int", self._r.zcard(idx)))

    # -------------------------------------------------------------- counter
    def counter_charge(self, key: str, *, limit: int, ttl_seconds: float) -> bool:
        return bool(int(self._charge(keys=[self._key(key)], args=[limit, ttl_seconds])))

    def counter_value(self, key: str) -> int:
        raw = self._r.get(self._key(key))
        return int(cast("bytes", raw)) if raw is not None else 0

    # --------------------------------------------------------------- window
    def window_allow(
        self, key: str, *, windows: tuple[tuple[float, int], ...], now: float
    ) -> bool:
        longest = max(span for span, _ in windows)
        # A unique member per call. Keyed on the timestamp alone, two requests
        # arriving in the same instant -- or in the same tick of an injected
        # test clock -- would be one sorted-set member, and ZADD would overwrite
        # rather than add. The limit would then silently admit more than it
        # should, which is the failure a rate limiter exists to prevent.
        args: list[Any] = [now, longest, uuid.uuid4().hex]
        for span, limit in windows:
            args.extend([span, limit])
        return bool(int(self._window(keys=[self._key(key)], args=args)))

    def window_check(
        self, key: str, *, windows: tuple[tuple[float, int], ...], now: float
    ) -> bool:
        args: list[Any] = [now]
        for span, limit in windows:
            args.extend([span, limit])
        return bool(int(self._window_check(keys=[self._key(key)], args=args)))


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


def redis_backend_from_url(url: str, *, prefix: str = "agent-platform") -> RedisBackend:
    """Open a Redis connection, or refuse loudly.

    ``decode_responses`` is left off so the Lua scripts and the client agree on
    bytes; :func:`_text` does the decoding at the one boundary that needs it.
    """
    try:
        from redis import Redis
    except ImportError as exc:  # pragma: no cover - depends on the redis extra
        raise SharedStateUnavailable(
            'REDIS_URL is set but the redis client is not installed. '
            'Install the redis extra: pip install -e ".[redis]"'
        ) from exc

    try:
        client = Redis.from_url(url, socket_timeout=5.0, socket_connect_timeout=5.0)
    except Exception as exc:
        raise SharedStateUnavailable(
            f"could not construct a redis client: {type(exc).__name__}"
        ) from exc

    backend = RedisBackend(client, prefix=prefix)
    backend.ping()
    return backend
