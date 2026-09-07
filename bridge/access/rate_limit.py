"""W09.03 — Tenant-scoped token-bucket rate limiting with bounded keys.

- One bucket per (tenant, subject-bucket): a burst capacity plus a refill
  rate. Tenants are isolated: exhausting one tenant's budget never
  affects another's.
- Non-blocking check() for request gates; acquire() for backpressure
  waits with a bounded timeout.
- The bucket key space is bounded: at most max_tenants buckets are kept,
  evicting the least-recently-refilled one (the W09.02 cardinality
  lesson applied to rate-limit state).

Reference-local scope: budgets are per-process. A shared multi-instance
budget requires the PostgreSQL state store (W08) and is intentionally
out of scope here.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


class RateLimitConfig:
    def __init__(
        self,
        *,
        capacity: int | None = None,
        refill_per_sec: float | None = None,
        max_tenants: int = 10_000,
    ) -> None:
        self.capacity = int(
            capacity if capacity is not None
            else os.environ.get("AOF_RATE_LIMIT_CAPACITY", "120")
        )
        self.refill_per_sec = float(
            refill_per_sec if refill_per_sec is not None
            else os.environ.get("AOF_RATE_LIMIT_REFILL_PER_SEC", "20")
        )
        self.max_tenants = max_tenants
        if self.capacity <= 0 or self.refill_per_sec <= 0:
            raise ValueError("rate limit capacity and refill_per_sec must be positive")


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class TenantRateLimiter:
    """Thread-safe token buckets keyed by tenant."""

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    def _refill(self, bucket: _Bucket, now: float) -> None:
        elapsed = now - bucket.last_refill
        if elapsed > 0:
            bucket.tokens = min(
                float(self.config.capacity),
                bucket.tokens + elapsed * self.config.refill_per_sec,
            )
            bucket.last_refill = now

    def _bucket_for(self, key: str, now: float) -> _Bucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self.config.max_tenants:
                # bounded key space: evict least-recently-refilled
                self._buckets.popitem(last=False)
            bucket = _Bucket(tokens=float(self.config.capacity), last_refill=now)
            self._buckets[key] = bucket
        self._buckets.move_to_end(key)
        return bucket

    def check(self, key: str, *, cost: float = 1.0) -> bool:
        """Non-blocking: take ``cost`` tokens if available, else return False."""
        now = time.monotonic()
        with self._lock:
            bucket = self._bucket_for(key, now)
            self._refill(bucket, now)
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True
            return False

    def retry_after(self, key: str, *, cost: float = 1.0) -> float:
        """Seconds until ``cost`` tokens could be available (best effort)."""
        now = time.monotonic()
        with self._lock:
            bucket = self._bucket_for(key, now)
            self._refill(bucket, now)
            missing = max(0.0, cost - bucket.tokens)
        if missing <= 0:
            return 0.0
        return missing / self.config.refill_per_sec

    def acquire(self, key: str, *, cost: float = 1.0, timeout: float = 5.0) -> bool:
        """Backpressure wait: poll until tokens are available or timeout."""
        deadline = time.monotonic() + timeout
        while True:
            if self.check(key, cost=cost):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))

    def bucket_count(self) -> int:
        with self._lock:
            return len(self._buckets)
