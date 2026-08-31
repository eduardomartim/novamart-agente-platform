"""A daily ceiling on physical calls to a paid provider.

The problem this solves is narrow and concrete. The configured free tier allows
roughly 500 calls per day, per project. Nothing in the platform counted
*physical* calls against that ceiling:

* the cost tracker counts money, and at the measured $0.00051 per call its
  $1.00 daily budget binds at about 1 960 calls -- nearly four times past the
  point where the quota is already gone;
* the rate limiter counts inbound requests, not the two-to-three provider calls
  each one becomes;
* the resource guard bounds a *single* request, not the day;
* the circuit breaker reacts to failures, not to success.

Each of those is doing its own job correctly. None of them is this job.

Where this lives, and why
-------------------------
The check sits immediately before the SDK call inside the provider's retry
loop, because that is the only line every *physical* attempt passes through.
A wrapper around ``generate()`` would see one logical call and miss the retries
inside it -- which is precisely when the platform burns quota fastest.

It can only prevent a call. It has no "allow" verb, and it takes no argument
from a caller asserting that it should be exempt: an exemption is a bypass with
better manners. Live verification runs draw on the same allowance as everything
else, which is why the ceiling is set below the free tier rather than at it.

Why SQLite
----------
The budget has to hold across processes -- a CLI invocation, a long-running
dashboard and a test run are three processes sharing one provider quota. SQLite
is already a dependency and already the project's durable store, and its
transactions give atomic read-modify-write across both threads and processes
without inventing a lock file.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

from .provider import LLMError

#: Observed free-tier allowance for the configured model, per project per day.
FREE_TIER_DAILY_CALLS: Final[int] = 500

#: The global ceiling. Deliberately below the free tier: the quota is shared
#: with anything else using the same Google Cloud project, and a budget set at
#: the ceiling protects nothing -- it merely predicts the failure it should
#: have prevented. 400 leaves a fifth of the day's allowance in reserve.
DEFAULT_DAILY_LIMIT: Final[int] = 400


class ProviderBudgetExhausted(LLMError):
    """The daily provider-call budget is spent.

    A subclass of :class:`LLMError` so existing callers keep handling it, but a
    distinct type so it is never reported as a provider outage. The provider is
    fine; we chose not to call it.
    """


class ProviderBudget(Protocol):
    """One verb: *stop*. Structurally incapable of granting permission."""

    def try_consume(self) -> bool:
        """Charge one physical call. False means: do not make the call."""
        ...


def _utc_day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class SqliteProviderBudget:
    """A durable, atomic, per-day counter of physical provider calls."""

    def __init__(
        self,
        path: Path | str,
        *,
        daily_limit: int = DEFAULT_DAILY_LIMIT,
        today: Callable[[], str] = _utc_day,
    ) -> None:
        if daily_limit < 0:
            raise ValueError("daily_limit must be >= 0")
        self._path = Path(path)
        self._limit = daily_limit
        self._today = today
        # Guards this process's own threads. Cross-process safety comes from
        # SQLite's transaction, not from here.
        self._lock = threading.Lock()
        # Created on first use, not here: constructing a provider that is
        # never called should not leave a file behind.
        self._ready = False

    # ------------------------------------------------------------- internals

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure(self) -> None:
        if self._ready:
            return
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS provider_budget ("
                "  day TEXT PRIMARY KEY,"
                "  calls INTEGER NOT NULL"
                ")"
            )
        self._ready = True

    # ---------------------------------------------------------------- public

    @property
    def daily_limit(self) -> int:
        return self._limit

    def try_consume(self) -> bool:
        """Charge one physical call if the day's allowance permits it.

        The read and the write happen inside one ``BEGIN IMMEDIATE``
        transaction, so two callers who both see "one left" cannot both be
        granted it -- the second blocks until the first commits and then reads
        the updated count.
        """
        self._ensure()
        day = self._today()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT calls FROM provider_budget WHERE day = ?", (day,)
                ).fetchone()
                used = int(row["calls"]) if row else 0
                if used >= self._limit:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute(
                    "INSERT INTO provider_budget (day, calls) VALUES (?, 1) "
                    "ON CONFLICT(day) DO UPDATE SET calls = calls + 1",
                    (day,),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def used(self) -> int:
        self._ensure()
        day = self._today()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT calls FROM provider_budget WHERE day = ?", (day,)
            ).fetchone()
        return int(row["calls"]) if row else 0

    def remaining(self) -> int:
        return max(0, self._limit - self.used())
