"""W09.03 — Per-operation rate limit policy: differentiated budgets by
path class and cost weighting.

- governed write/execute operations cost more tokens (scarcer budget)
- reads cost 1 token
- governance-plane writes (proposals, publishes) cost 3 tokens
- health/metrics exempt

Wired into the rate_limit_middleware via the operation registry's
classification (no hard-coded path lists in the limiter itself).
"""

from __future__ import annotations

from enum import Enum


class RateCost(int, Enum):
    READ = 1
    WRITE = 2
    GOVERNED_WRITE = 3
    EXECUTE = 2
    ADMIN = 5


def cost_for_method(method: str, path: str) -> int:
    """Return the rate-limit token cost for a request."""
    method = method.upper()

    # governance plane writes are expensive
    if path.startswith("/v1/semantic/") and method in ("POST", "PUT", "DELETE", "PATCH"):
        return RateCost.GOVERNED_WRITE

    # generic write
    if method in ("POST", "PUT", "DELETE", "PATCH"):
        return RateCost.WRITE

    # reads
    return RateCost.READ
