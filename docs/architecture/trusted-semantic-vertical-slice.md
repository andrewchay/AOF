# Trusted semantic P0-P2 baseline

## P0: one governed runtime baseline

`bridge.semantic_core` is the canonical runtime contract. It owns immutable
semantic resources, release manifests, signed principals, compilation runs,
query plans and query receipts. `bridge.decision_provenance` supplies an
append-only, hash-chained explanation of why a release or decision exists.

The former `/v1/semantic/compile` endpoint is deliberately retired. A raw LLM
SQL response is not a governed semantic query. The public boundary returns HTTP
410 and directs callers to the release-pinned semantic query capability.

## P1: source evidence to semantic resources

`bridge.document_parser` accepts local text and Office/PDF parser outputs only
through a path allowlist, normalizes parser output, retains source context, and
has an async parse queue. `KnowledgeSource`, `SourceBatch`, and
`ContinuousIngestionService` bind each source snapshot, cursor, change set and
ingestion run to a tenant and a deterministic digest.

Adapters convert approved mapping libraries, OKF bundles, ontology documents,
and rule sets into `SemanticResource` revisions. A revision preserves its
evidence references and cannot be silently changed after a release is built.

## P2: governed validation, release, and consumption

```text
source snapshot -> evidence-bearing SemanticResource -> proposal
  -> SHACL/OWL/SKOS validation + impact -> review/waiver -> compile
  -> signed immutable release -> release-pinned query or projection
```

`bridge.ontology_governance` implements the bounded SHACL Core gate, OWL/SKOS
validation and versioned Datalog baseline. Unsupported constraints fail the
gate rather than being ignored. `SemanticGovernanceService` requires role
separation for create, validate, review, compile and publish. The REST and MCP
control planes derive actor, tenant and roles from a signed principal header;
body fields are never authoritative.

Consumers use `/v1/semantic/query` and compiler endpoints. They receive only
release-pinned artifacts and record a query run, evidence package and digest.
Graph, vector, SQL and Skills are projections/capabilities of the same release,
not independent truth sources.

## Verified repository example

`tests/test_semantic_release_e2e.py` publishes a signed release from the
repository's mapping library, Genshin ontology, Datalog rule, and OKF bundle;
then compiles, independently replays, approves, and promotes all six targets:
semantic JSON, OWL, SHACL, Datalog, RAG and MCP.
