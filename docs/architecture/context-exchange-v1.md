# Context Exchange v1

## Scope

This contract transfers a user-selected, minimum-disclosure context proposal from a local-first producer into AOF. It does **not** grant AOF access to a producer vault, provide a MyContext database adapter, or make unreviewed content queryable.

## Spaces and transitions

| Space | Meaning | Queryable in AOF | Entry condition |
| --- | --- | --- | --- |
| `private` | Local-only personal context | No | Never uploaded by this contract |
| `shared-draft` | AOF quarantine for consented proposals | No | Explicit local consent |
| `tenant-governed` | Published tenant knowledge | Yes, under tenant policy | Privacy + domain + independent publisher approval |
| `public-governed` | Published public knowledge | Yes, under public policy | Fresh public consent + privacy + domain + public + publisher approval |

The `tenant_id` remains the publishing organization even for a public release. `public` is a visibility and must not be modeled as a tenant, otherwise tenant policy could be bypassed.

## Packet contract

`aof.context-packet/v1` contains a target `shared-draft` ContextSpace, one or more evidence-backed assertions, a consent decision identifier, the consented purpose, an expiry, and a deterministic SHA-256 packet digest.

Evidence is either a source reference only or an explicitly redacted excerpt. A reference may not carry raw source content. Version one permits only `decision`, `document`, `event`, `fact`, and `public-source` assertion categories; persona, relationship, and raw-conversation categories are default-denied.

## Approval matrix

| Promotion | Required approved roles | Additional rule |
| --- | --- | --- |
| `shared-draft` → `tenant-governed` | `privacy-reviewer`, `domain-approver`, `publisher` | Submitter cannot be final publisher |
| `tenant-governed` → `public-governed` | Above plus `public-reviewer` | A newly granted public consent is mandatory; draft content cannot bypass tenant governance |

The gateway creates an immutable AOF `DecisionProvenance` record for quarantine receipt. Subsequent iterations must add records for local-consent receipt, each validation finding, each approval, publication, expiry, and revocation.

## Iteration 1: quarantine gateway

`ContextGateway` now persists a validated `ContextPacket` in a tenant-isolated SQLite quarantine repository and writes a `context_packet_quarantined` DecisionProvenance receipt. The repository has no query-executor integration: `list_for_review()` is the only read path and is intended for the next validation/review workflow. Repeated delivery of an identical digest is idempotent; a changed packet under the same tenant and packet ID is rejected.

## Iteration 2: approval and release

`ContextPromotionService` records each required Context Exchange review as a chained DecisionProvenance event, maps reviewed assertions to `ContextAssertion` semantic resources, and runs the established AOF proposal → validate → approve → compile → publish lifecycle. Tenant promotion starts at `shared-draft`. A public release requires a prior tenant-governed ContextPublication, fresh public consent, and public-reviewer approval; it becomes a new release with the tenant release as parent.

## Iteration 3: MyContext read-only export adapter

The adapter accepts `mycontext.context-export/v1` JSON, produced locally after source selection and redaction, and transforms it into `aof.context-packet/v1`. It does not accept a vault path, execute SQL, or carry raw records. Evidence is limited to source references plus SHA-256 hashes, optionally with a user-selected redacted excerpt. MyContext persona, relationships, raw conversations, and any unsupported assertion category remain rejected by the ContextPacket contract.

## Iteration 4: public-source ingest foundation

`PublicSourceIngestor` accepts connector-produced batches of public documents and persists each distinct source-content revision immutably. The source watermark moves only when a batch is complete; a truncated or failed batch remains explicitly `incomplete`. Source URL, observation time, raw content hash, ETag/last-modified metadata, and redistribution rights are retained so parsing can be replayed without re-fetching. Ingest does not publish knowledge: `restricted` and `unknown` redistribution sources may be retained for analysis, but `require_redistributable()` blocks their public release.

## Iteration 5: public assertion governance

`PublicAssertionService` creates an immutable, evidence-bound candidate from a specific public-source revision. It records the candidate intake, validates that redistribution is explicitly allowed, records privacy/domain/public/publisher review decisions, and then uses the standard AOF governance lifecycle to produce a `public-governed` release. This direct route is only for public-source material: private-origin context still follows `shared-draft → tenant-governed → public-governed`.

## Iteration 6: released-public knowledge query and conflict gate

Only a successfully published public assertion enters `SqlitePublicKnowledgeRepository`. Its query response includes the statement, source URL and content hash, valid-time metadata, and exact release digest; unreleased candidates remain invisible. Before a new public assertion is published, the repository checks for a different statement on the same explicit `subject_key`. Such a conflict creates an immutable conflict decision and blocks automatic publication, preserving both sources for human resolution rather than silently overwriting a prior conclusion.

## Iteration 7: controlled public knowledge query

`PublicKnowledgeQueryControl` requires a signed AOF principal and an allowed purpose, queries only the released-public catalog within that principal's tenant, and persists a signed `QueryRun`. The run binds the request, catalog snapshot digest, query plan digest, policy report, released-record evidence, and decision-provenance trail. A policy failure after principal verification also creates a signed failed QueryRun; invalid identities cannot be attributed and are rejected before execution.

## Iteration 8: revocation and strict replay

Public assertion revocation is append-only: a revocation decision is stored separately from the published assertion, so new catalog queries exclude it while historical releases and QueryRuns remain intact. `PublicKnowledgeQueryControl.replay()` requires the source run ID, its expected digest, and a valid attestation; it only succeeds if the current public catalog produces the identical result digest. A revocation or other catalog change therefore produces a signed failed replay run rather than a misleading successful replay.

## Iteration-0 acceptance

- Packets cannot target governed spaces directly.
- Assertions require evidence and support deterministic packet digests.
- Unconsented purposes and default-denied personal categories are rejected.
- Tenant promotion has separation of duties.
- Public promotion has an additional reviewer and fresh public consent.
