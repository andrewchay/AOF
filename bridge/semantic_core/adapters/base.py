"""Shared helpers for legacy AOF asset adapters."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


class AdapterError(ValueError):
    """Raised when a legacy asset cannot be converted without losing integrity."""


@dataclass(frozen=True)
class AdapterContext:
    tenant: str
    domain: str
    owner: str

    def resource_id(self, kind_segment: str, name: str) -> str:
        return f"aof://{self.tenant}/{self.domain}/{kind_segment}/{safe_segment(name)}"


def safe_segment(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", str(value).strip().lower()).strip("-._")
    if normalized:
        return normalized
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
    return f"legacy-{digest}"
