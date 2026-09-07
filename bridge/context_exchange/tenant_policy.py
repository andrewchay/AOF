"""Tenant-owned routing policy for local and share-eligible context.

The policy is evaluated by AOF, not trusted to a desktop client.  A source
that has no matching route is private by default and cannot be exported.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .contracts import ContextExchangeError
from .mycontext_exporter import MyContextExportBundle


class ContextRouteDisposition(str, Enum):
    PRIVATE = "private"
    SHARE_ELIGIBLE = "share-eligible"
    DENY = "deny"


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ContextExchangeError(f"{name} must be a non-empty list of strings")
    result = tuple(sorted({item.strip() for item in value}))
    if not result:
        raise ContextExchangeError(f"{name} must not be empty")
    return result


@dataclass(frozen=True)
class ContextSpacePolicy:
    draft_space_id: str
    allowed_purposes: tuple[str, ...]


@dataclass(frozen=True)
class ContextSourceRoute:
    source_prefix: str
    disposition: ContextRouteDisposition
    draft_space_id: str | None = None
    allowed_purposes: tuple[str, ...] = ()
    sensitivity_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutedContextSubmission:
    draft_space_id: str
    allowed_purposes: tuple[str, ...]
    matched_source_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class TenantContextPolicy:
    tenant_id: str
    spaces: Mapping[str, ContextSpacePolicy]
    source_routes: tuple[ContextSourceRoute, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TenantContextPolicy":
        if set(value) != {"tenant_id", "spaces", "source_routes"}:
            raise ContextExchangeError("tenant context policy fields do not match v1 contract")
        tenant_id = value["tenant_id"]
        raw_spaces = value["spaces"]
        raw_routes = value["source_routes"]
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ContextExchangeError("tenant_id must be a non-empty string")
        if not isinstance(raw_spaces, Mapping) or not isinstance(raw_routes, list):
            raise ContextExchangeError("spaces must be an object and source_routes must be a list")
        spaces: dict[str, ContextSpacePolicy] = {}
        for key, raw in raw_spaces.items():
            if not isinstance(key, str) or not isinstance(raw, Mapping) or set(raw) != {"draft_space_id", "allowed_purposes"}:
                raise ContextExchangeError("context space policy has invalid fields")
            draft_space_id = raw["draft_space_id"]
            if not isinstance(draft_space_id, str) or not draft_space_id.strip():
                raise ContextExchangeError("draft_space_id must be a non-empty string")
            spaces[key] = ContextSpacePolicy(draft_space_id.strip(), _strings(raw["allowed_purposes"], "allowed_purposes"))
        routes: list[ContextSourceRoute] = []
        for raw in raw_routes:
            if not isinstance(raw, Mapping) or set(raw) != {"source_prefix", "disposition", "draft_space", "allowed_purposes", "sensitivity_labels"}:
                raise ContextExchangeError("source route fields do not match v1 contract")
            prefix = raw["source_prefix"]
            if not isinstance(prefix, str) or not prefix.strip():
                raise ContextExchangeError("source_prefix must be a non-empty string")
            try:
                disposition = ContextRouteDisposition(raw["disposition"])
            except ValueError as exc:
                raise ContextExchangeError("source route disposition is invalid") from exc
            draft_space = raw["draft_space"]
            purposes = _strings(raw["allowed_purposes"], "allowed_purposes") if raw["allowed_purposes"] else ()
            labels = _strings(raw["sensitivity_labels"], "sensitivity_labels") if raw["sensitivity_labels"] else ()
            if disposition is ContextRouteDisposition.SHARE_ELIGIBLE:
                if not isinstance(draft_space, str) or draft_space not in spaces:
                    raise ContextExchangeError("share-eligible source route requires a known draft_space")
                if not purposes or not set(purposes).issubset(spaces[draft_space].allowed_purposes):
                    raise ContextExchangeError("source route purposes must be allowed by its draft space")
            elif draft_space is not None or purposes:
                raise ContextExchangeError("private and deny source routes cannot nominate a draft space or purposes")
            routes.append(ContextSourceRoute(prefix.strip(), disposition, draft_space, purposes, labels))
        return cls(tenant_id.strip(), dict(spaces), tuple(routes))

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> "TenantContextPolicy":
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - deployment dependency error
            raise ContextExchangeError("PyYAML is required to load a tenant context policy") from exc
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ContextExchangeError("tenant context policy must be a YAML object")
        return cls.from_dict(value)

    def route_export(self, bundle: MyContextExportBundle) -> RoutedContextSubmission:
        routes = [self._route(evidence.source_ref) for assertion in bundle.assertions for evidence in assertion.evidence]
        blocked = [route for route in routes if route.disposition is not ContextRouteDisposition.SHARE_ELIGIBLE]
        if blocked:
            raise ContextExchangeError("one or more selected sources are private or denied by tenant policy")
        draft_names = {route.draft_space_id for route in routes}
        if len(draft_names) != 1:
            raise ContextExchangeError("selected sources must route to one shared draft space")
        shared_purposes = set.intersection(*(set(route.allowed_purposes) for route in routes))
        if not set(bundle.consented_purpose).issubset(shared_purposes):
            raise ContextExchangeError("consented purpose is not allowed for every selected source")
        draft_name = next(iter(draft_names))
        assert draft_name is not None
        return RoutedContextSubmission(
            draft_space_id=self.spaces[draft_name].draft_space_id,
            allowed_purposes=tuple(sorted(shared_purposes)),
            matched_source_prefixes=tuple(sorted({route.source_prefix for route in routes})),
        )

    def _route(self, source_ref: str) -> ContextSourceRoute:
        matches = [route for route in self.source_routes if source_ref.startswith(route.source_prefix)]
        if not matches:
            return ContextSourceRoute("<default-private>", ContextRouteDisposition.PRIVATE)
        return max(matches, key=lambda route: len(route.source_prefix))
