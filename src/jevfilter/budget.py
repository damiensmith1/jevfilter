"""Spend and rate ceilings that refuse rather than block.

A rate cap stops a loop bug within seconds; a spend cap stops a slow
bleed. Both are checked before every request, so overshoot is bounded to
one request. One `Budget` can be shared by many filters (thread-safe).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from .errors import BudgetExceeded

DEFAULT_PRICE_PER_MTOK = 0.042
"""Jev's published input price (USD per million tokens) when this was written."""


class Budget:
    """`Budget(usd=1.00, per_minute=60)`. Either limit may be None (off)."""

    def __init__(
        self,
        usd: float | None = None,
        per_minute: int | None = None,
        *,
        price_per_mtok: float = DEFAULT_PRICE_PER_MTOK,
        clock: Callable[[], float] = time.monotonic,
    ):
        if usd is not None and usd < 0:
            raise ValueError("usd must be ≥ 0")
        if per_minute is not None and per_minute < 1:
            raise ValueError("per_minute must be ≥ 1")
        self.usd = usd
        self.per_minute = per_minute
        self.price_per_mtok = price_per_mtok
        self._clock = clock
        self._lock = threading.Lock()
        self._calls: deque[float] = deque()
        self.spent_usd = 0.0
        self.input_tokens = 0
        self.requests = 0

    def check(self) -> None:
        """Raise `BudgetExceeded` if another request would break a ceiling.

        Passing reserves a slot in the rate window, so concurrent callers
        can't all slip through at once.
        """
        with self._lock:
            if self.usd is not None and self.spent_usd >= self.usd:
                raise BudgetExceeded(f"spend cap reached: ${self.spent_usd:.6f} of ${self.usd:.6f}")
            if self.per_minute is not None:
                now = self._clock()
                while self._calls and now - self._calls[0] >= 60:
                    self._calls.popleft()
                if len(self._calls) >= self.per_minute:
                    raise BudgetExceeded(f"rate cap reached: {self.per_minute} requests per minute")
                self._calls.append(now)

    def record(self, input_tokens: int | None) -> float:
        """Record a finished request; returns its cost in USD."""
        cost = (input_tokens or 0) * self.price_per_mtok / 1e6
        with self._lock:
            self.spent_usd += cost
            self.input_tokens += input_tokens or 0
            self.requests += 1
        return cost

    @property
    def remaining_usd(self) -> float | None:
        return None if self.usd is None else max(self.usd - self.spent_usd, 0.0)

    def __repr__(self) -> str:
        return (
            f"Budget(usd={self.usd}, per_minute={self.per_minute}, "
            f"spent_usd={self.spent_usd:.6f}, requests={self.requests})"
        )
