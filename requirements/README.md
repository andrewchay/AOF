# AOF Dependency Management (W07.01)

Single source of truth for all dependency declarations.

## Structure

- `locks/reference-local.lock` — Frozen lock for the reference-local profile
  (SQLite, local RDF, no external services). Generated from a verified working
  environment. Install with: `pip install -r requirements/locks/reference-local.lock`

- `core.in` — Core runtime dependencies (FastAPI, Pydantic, rdflib, etc.)
- `dev.in` — Development-only dependencies (pytest, mypy, ruff, etc.)
- `cognee.in` — Cognee integration dependencies
- `observability.in` — OpenTelemetry and monitoring

## Profiles

| Profile | Lock file | External deps |
|---------|-----------|---------------|
| reference-local | `locks/reference-local.lock` | None (SQLite, local files) |
| enterprise | `locks/enterprise.lock` (TODO) | PostgreSQL, IdP, KMS, MQ |

## Rules

1. All version pins use `==` (exact) in lock files
2. `.in` files use `>=` with upper bounds where known
3. Lock files are regenerated from a verified working environment
4. Fresh install must work from lock file alone (no .pth, no editable)
