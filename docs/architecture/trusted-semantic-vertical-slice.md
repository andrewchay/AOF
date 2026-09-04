# Trusted semantic vertical slice

This slice is the system-of-record boundary for enterprise knowledge.  It does
not replace the graph, vector store, relational warehouse, or Skill runtime.
Those systems may only consume a published semantic release.

## Lifecycle

```text
SourceAsset -> Evidence -> candidate SemanticFact -> proposal review
  -> approved SemanticFact -> immutable Release -> consumer projections
```

`SourceAsset` records the observed source version and access classification.
`Evidence` points to an exact source locator and excerpt.  A fact without
evidence cannot be proposed.  Evidence from another tenant cannot support a
fact.  A proposer cannot approve their own proposal.

Only approved facts can be added to a release.  A release has a deterministic
digest over its tenant and ordered fact identifiers; reads verify that digest
and fail closed when its stored record has been altered.

## Boundary for consumers

Graph, vector, SQL, and Skill projections must include both `tenant_id` and
`release_id`.  A query plan must refuse a projection that does not match the
requested release digest.  Candidate facts may be displayed in a review UI,
but must not be used by production query or Agent execution paths.

## API exposure is deliberately deferred

The current public FastAPI service accepts tenant and reviewer fields in request
bodies and does not yet establish them from a verified principal.  Exposing
approval or publish endpoints there would allow caller-controlled identity.
Until the authentication middleware supplies a signed principal and tenant
context, `SemanticService` is a trusted in-process interface only.

## First domain acceptance example

For a revenue metric, register the warehouse snapshot as a `SourceAsset`, bind
the precise SQL definition or data-dictionary excerpt as `Evidence`, propose
the metric fact, approve it as a distinct reviewer, and publish a release.
The resulting release query must return the fact, source, and evidence together.
