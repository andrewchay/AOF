"""Release-pinned Agentic System for governed ontology consumption.

The runtime persists explicit plans, evidence and receipts. It never persists or
requires private chain-of-thought; rationale codes and tool evidence are the
auditable explanation surface.
"""

from __future__ import annotations
from bridge.persistence.sqlite_support import managed_sqlite_connection

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore

from .canonical import canonical_data, content_digest


class AgenticSystemError(ValueError):
    """Raised when an Agentic request cannot be executed without guessing."""


CAPABILITIES = (
    "graph_search",
    "skill_search",
    "vector_search",
    "semantic_sql",
    "rag_retrieve",
    "llm_fallback",
    "rule_search",
    "action_submit",
    "skill_execute",
)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgenticSystemError(f"{field} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class AgenticRequest:
    run_id: str
    tenant_id: str
    session_id: str
    query: str
    purpose: str
    channel: str
    release_id: str
    release_digest: str
    policy_resource_id: str
    allowed_capabilities: tuple[str, ...]
    max_steps: int
    allow_rag_fallback: bool
    allow_llm_fallback: bool
    query_contract_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        tenant_id: str,
        session_id: str,
        query: str,
        purpose: str,
        channel: str,
        release_id: str,
        release_digest: str,
        policy_resource_id: str,
        allowed_capabilities: Iterable[str],
        max_steps: int = 4,
        allow_rag_fallback: bool = True,
        allow_llm_fallback: bool = False,
        query_contract_id: str | None = None,
    ) -> "AgenticRequest":
        allowed = tuple(sorted(set(allowed_capabilities)))
        unknown = sorted(set(allowed) - set(CAPABILITIES))
        if unknown:
            raise AgenticSystemError(
                f"unsupported Agentic capabilities: {', '.join(unknown)}"
            )
        if not allowed:
            raise AgenticSystemError("allowed_capabilities must not be empty")
        if (
            isinstance(max_steps, bool)
            or not isinstance(max_steps, int)
            or max_steps < 1
        ):
            raise AgenticSystemError("max_steps must be a positive integer")
        if max_steps > 8 or len(query) > 8000:
            raise AgenticSystemError("request exceeds Agentic step or query budget")
        return cls(
            run_id=_text(run_id, "run_id"),
            tenant_id=_text(tenant_id, "tenant_id"),
            session_id=_text(session_id, "session_id"),
            query=_text(query, "query"),
            purpose=_text(purpose, "purpose"),
            channel=_text(channel, "channel"),
            release_id=_text(release_id, "release_id"),
            release_digest=_text(release_digest, "release_digest"),
            policy_resource_id=_text(policy_resource_id, "policy_resource_id"),
            allowed_capabilities=allowed,
            max_steps=max_steps,
            allow_rag_fallback=bool(allow_rag_fallback),
            allow_llm_fallback=bool(allow_llm_fallback),
            query_contract_id=query_contract_id,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgenticRequest":
        return cls.create(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(self.__dict__)


@dataclass(frozen=True)
class IntentRoute:
    query_digest: str
    intents: tuple[str, ...]
    capabilities: tuple[str, ...]
    rationale_codes: tuple[str, ...]
    fallback: str | None

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(self.__dict__)


class OntologyIntentRouter:
    """Deterministic, explainable router over published capability names."""

    _PATTERNS = {
        "semantic_sql": (
            "多少",
            "总数",
            "平均",
            "增长率",
            "趋势",
            "count",
            "average",
            "sum",
            "revenue",
            "metric",
            "kpi",
            "指标",
        ),
        "graph_search": (
            "关系",
            "关联",
            "有关",
            "依赖",
            "上下游",
            "影响",
            "路径",
            "relationship",
            "related",
            "dependency",
            "path",
            "impact",
        ),
        "skill_search": (
            "如何",
            "怎么",
            "流程",
            "步骤",
            "操作",
            "执行",
            "how to",
            "procedure",
            "workflow",
            "skill",
        ),
        "vector_search": (
            "相似",
            "类似",
            "相关文档",
            "语义",
            "similar",
            "relevant document",
            "semantic similarity",
        ),
    }

    def route(self, request: AgenticRequest) -> IntentRoute:
        query = request.query.casefold()
        matched: list[str] = []
        rationale: list[str] = []
        for capability, patterns in self._PATTERNS.items():
            from .natural_query import mentions

            if any(mentions(query, pattern) for pattern in patterns):
                if capability not in request.allowed_capabilities:
                    raise AgenticSystemError(
                        f"required capability is not allowed: {capability}"
                    )
                matched.append(capability)
                rationale.append(f"intent:{capability}:lexical-evidence")

        fallback = None
        if not matched:
            if (
                request.allow_rag_fallback
                and "rag_retrieve" in request.allowed_capabilities
            ):
                matched.append("rag_retrieve")
                fallback = "rag_retrieve"
                rationale.append("fallback:ontology-intent-unresolved")
            elif (
                request.allow_llm_fallback
                and "llm_fallback" in request.allowed_capabilities
            ):
                matched.append("llm_fallback")
                fallback = "llm_fallback"
                rationale.append("fallback:ontology-and-rag-unavailable")
            else:
                raise AgenticSystemError(
                    "ontology intent is unresolved and no approved fallback is available"
                )
        if len(matched) > request.max_steps:
            raise AgenticSystemError("routed capabilities exceed max_steps")
        return IntentRoute(
            query_digest=content_digest({"query": request.query}),
            intents=tuple(
                item.removesuffix("_search").removesuffix("_retrieve")
                for item in matched
            ),
            capabilities=tuple(matched),
            rationale_codes=tuple(rationale),
            fallback=fallback,
        )


@dataclass(frozen=True)
class AgenticPlan:
    release_id: str
    release_digest: str
    memory_digest: str
    memory_count: int
    steps: tuple[Mapping[str, Any], ...]
    plan_digest: str

    @classmethod
    def build(
        cls,
        request: AgenticRequest,
        route: IntentRoute,
        *,
        memory_digest: str,
        memory_count: int,
        published_steps: Iterable[Mapping[str, Any]] | None = None,
    ) -> "AgenticPlan":
        steps = tuple(
            {
                "step_id": f"retrieve-{index + 1}",
                "kind": "capability",
                "capability": capability,
                "depends_on": [],
                "query_digest": route.query_digest,
            }
            for index, capability in enumerate(route.capabilities)
        )
        if published_steps is not None:
            steps = tuple(canonical_data(step) for step in published_steps)
        steps += (
            {
                "step_id": "summarize",
                "kind": "summary",
                "capability": "evidence_summary",
                "depends_on": [step["step_id"] for step in steps],
                "query_digest": route.query_digest,
            },
        )
        payload = {
            "api_version": "aof.agentic-plan/v1",
            "release_id": request.release_id,
            "release_digest": request.release_digest,
            "memory_digest": memory_digest,
            "memory_count": memory_count,
            "steps": steps,
        }
        return cls(
            release_id=request.release_id,
            release_digest=request.release_digest,
            memory_digest=memory_digest,
            memory_count=memory_count,
            steps=steps,
            plan_digest=content_digest(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(
            {
                "api_version": "aof.agentic-plan/v1",
                "release_id": self.release_id,
                "release_digest": self.release_digest,
                "memory_digest": self.memory_digest,
                "memory_count": self.memory_count,
                "steps": self.steps,
                "plan_digest": self.plan_digest,
            }
        )


@dataclass(frozen=True)
class CapabilityResult:
    result_id: str
    capability: str
    output: Mapping[str, Any]
    evidence: tuple[Mapping[str, Any], ...]
    result_digest: str
    receipt: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls, *, result_id: str, capability: str, value: Mapping[str, Any]
    ) -> "CapabilityResult":
        evidence = value.get("evidence")
        if not isinstance(evidence, list | tuple) or not evidence:
            raise AgenticSystemError(
                f"capability {capability} returned no evidence; factual output is blocked"
            )
        if any(
            not isinstance(item, Mapping)
            or not any(
                item.get(key) for key in ("id", "evidence_id", "source", "resource_id")
            )
            for item in evidence
        ):
            raise AgenticSystemError("capability evidence must identify a source")
        output = value.get("output")
        if not isinstance(output, Mapping):
            raise AgenticSystemError(
                f"capability {capability} output must be an object"
            )
        payload = {
            "result_id": _text(result_id, "result_id"),
            "capability": capability,
            "output": canonical_data(output),
            "evidence": canonical_data(evidence),
        }
        return cls(
            result_id=payload["result_id"],
            capability=capability,
            output=payload["output"],
            evidence=tuple(payload["evidence"]),
            result_digest=content_digest(payload),
            receipt=canonical_data(value.get("receipt", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(self.__dict__)


CapabilityExecutor = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class AgenticSummaryValidator:
    """Build and validate claims whose citations resolve to tool receipts."""

    def summarize(self, results: Iterable[CapabilityResult]) -> dict[str, Any]:
        values = tuple(results)
        claims = []
        for result in values:
            text = result.output.get("summary")
            if not isinstance(text, str) or not text.strip():
                text = json.dumps(result.output, ensure_ascii=False, sort_keys=True)
            claims.append({"text": text.strip(), "citations": [result.result_id]})
        summary = {
            "claims": claims,
            "text": "\n".join(
                f"{claim['text']} [{claim['citations'][0]}]" for claim in claims
            ),
        }
        self.validate(summary, values)
        return {**summary, "summary_digest": content_digest(summary)}

    def validate(
        self, summary: Mapping[str, Any], results: Iterable[CapabilityResult]
    ) -> None:
        values = {result.result_id: result for result in results}
        result_ids = set(values)
        claims = summary.get("claims")
        if not isinstance(claims, list) or not claims:
            raise AgenticSystemError("summary must contain at least one claim")
        for claim in claims:
            if not isinstance(claim, Mapping) or not _text(
                claim.get("text"), "claim text"
            ):
                raise AgenticSystemError("summary claim is invalid")
            citations = claim.get("citations")
            if not isinstance(citations, list) or not citations:
                raise AgenticSystemError("every summary claim requires citations")
            if not set(citations).issubset(result_ids):
                raise AgenticSystemError("summary cites an unknown capability result")
            supported = [
                values[citation].output.get("summary")
                or json.dumps(
                    values[citation].output, ensure_ascii=False, sort_keys=True
                )
                for citation in citations
            ]
            if claim["text"].strip() not in supported:
                raise AgenticSystemError(
                    "summary claim is not supported by its cited output"
                )


class SqliteAgenticRunRepository:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS agentic_runs "
                "(run_id TEXT NOT NULL, tenant_id TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(tenant_id, run_id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS agentic_memory "
                "(sequence INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, "
                "session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, digest TEXT NOT NULL)"
            )

    def save(self, run: Mapping[str, Any]) -> None:
        payload = canonical_data(run)
        digest = content_digest(payload)
        try:
            with self._connection() as connection:
                connection.execute(
                    "INSERT INTO agentic_runs(run_id, tenant_id, payload, digest) VALUES (?, ?, ?, ?)",
                    (
                        payload["run_id"],
                        payload["tenant_id"],
                        json.dumps(payload, sort_keys=True),
                        digest,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise AgenticSystemError(
                f"agentic run already exists: {payload['run_id']}"
            ) from exc

    def reserve(self, request, actor):
        payload = {
            "run_id": request.run_id,
            "tenant_id": request.tenant_id,
            "session_id": request.session_id,
            "actor": actor,
            "request": request.to_dict(),
            "status": "running",
        }
        self.save(payload)

    def finish(self, run, memory=()):
        payload = canonical_data(run)
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE agentic_runs SET payload = ?, digest = ? WHERE run_id = ? AND tenant_id = ?",
                (
                    json.dumps(payload, sort_keys=True),
                    content_digest(payload),
                    payload["run_id"],
                    payload["tenant_id"],
                ),
            )
            if cursor.rowcount != 1:
                raise AgenticSystemError("agentic reservation missing")
            for role, content in memory:
                content = canonical_data(content)
                connection.execute(
                    "INSERT INTO agentic_memory(tenant_id, session_id, role, content, digest) VALUES (?, ?, ?, ?, ?)",
                    (
                        payload["tenant_id"],
                        payload["session_id"],
                        role,
                        json.dumps(content, sort_keys=True),
                        content_digest(
                            {
                                "tenant_id": payload["tenant_id"],
                                "session_id": payload["session_id"],
                                "role": role,
                                "content": content,
                            }
                        ),
                    ),
                )

    def get(self, run_id: str, *, tenant_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload, digest FROM agentic_runs WHERE run_id = ? AND tenant_id = ?",
                (run_id, tenant_id),
            ).fetchone()
        if row is None:
            raise AgenticSystemError(f"agentic run not found: {run_id}")
        payload = json.loads(row[0])
        if content_digest(payload) != row[1]:
            raise AgenticSystemError("agentic run digest mismatch")
        if payload.get("tenant_id") != tenant_id or payload.get("run_id") != run_id:
            raise AgenticSystemError("agentic run identity mismatch")
        return payload

    def append_memory(
        self, *, tenant_id: str, session_id: str, role: str, content: Mapping[str, Any]
    ) -> None:
        payload = canonical_data(content)
        digest = content_digest(
            {
                "tenant_id": tenant_id,
                "session_id": session_id,
                "role": role,
                "content": payload,
            }
        )
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO agentic_memory(tenant_id, session_id, role, content, digest) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    session_id,
                    role,
                    json.dumps(payload, sort_keys=True),
                    digest,
                ),
            )

    def memory(self, *, tenant_id: str, session_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT role, content, digest FROM agentic_memory "
                "WHERE tenant_id = ? AND session_id = ? ORDER BY sequence DESC LIMIT 20",
                (tenant_id, session_id),
            ).fetchall()
        result = []
        for role, raw, digest in reversed(rows):
            content = json.loads(raw)
            if (
                content_digest(
                    {
                        "tenant_id": tenant_id,
                        "session_id": session_id,
                        "role": role,
                        "content": content,
                    }
                )
                != digest
            ):
                raise AgenticSystemError("agentic memory digest mismatch")
            result.append({"role": role, "content": content, "digest": digest})
        return result

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection
    def _connection(self):
        """Transaction + close context manager (W09.01: no leaked connections)."""
        return managed_sqlite_connection(self._connect)


    def backup_to(self, destination: str | Path) -> None:
        destination = Path(destination)
        if destination.exists():
            raise AgenticSystemError("backup destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as source, sqlite3.connect(destination) as target:
            source.backup(target)


class AgenticSystemService:
    """Plan, execute, summarize, persist and replay read-only ontology consumption."""

    def __init__(
        self,
        repository: SqliteAgenticRunRepository,
        *,
        executor: CapabilityExecutor,
        router: OntologyIntentRouter | None = None,
        summary_validator: AgenticSummaryValidator | None = None,
        decision_store: DecisionProvenanceStore | None = None,
        planner: Callable | None = None,
        action_reader: Callable | None = None,
    ) -> None:
        self.repository = repository
        self.executor = executor
        self.router = router or OntologyIntentRouter()
        self.summary_validator = summary_validator or AgenticSummaryValidator()
        self.decision_store = decision_store
        self.planner = planner
        self.action_reader = action_reader

    def run(
        self,
        request: AgenticRequest,
        *,
        actor: str,
        memory_snapshot: Iterable[Mapping[str, Any]] | None = None,
        replay_source: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor = _text(actor, "actor")
        self.repository.reserve(request, actor)
        try:
            return self._run(
                request,
                actor=actor,
                memory_snapshot=memory_snapshot,
                replay_source=replay_source,
            )
        except Exception as exc:
            failed = self.repository.get(request.run_id, tenant_id=request.tenant_id)
            failed.update(status="failed", error_type=type(exc).__name__)
            if self.decision_store is not None:
                failed["decision_id"] = self.decision_store.record(
                    agent_id=actor,
                    decision_type="agentic_query_orchestration",
                    conclusion="failed",
                    rationale="The Agentic plan did not complete; no final answer was published.",
                    tenant_id=request.tenant_id,
                    session_id=request.session_id,
                    parent_decision_ids=[
                        item["receipt"]["decision_id"]
                        for item in failed.get("results", [])
                        if item.get("receipt", {}).get("decision_id")
                    ],
                    metadata={
                        "run_id": request.run_id,
                        "error_type": type(exc).__name__,
                    },
                )["decision"]["id"]
            self.repository.finish(failed)
            raise

    def _run(self, request, *, actor, memory_snapshot, replay_source):
        memory = canonical_data(
            list(memory_snapshot)
            if memory_snapshot is not None
            else self.repository.memory(
                tenant_id=request.tenant_id, session_id=request.session_id
            )[-20:]
        )
        while (
            memory
            and len(json.dumps(memory, ensure_ascii=False).encode("utf-8")) > 65536
        ):
            memory.pop(0)
        memory_digest = content_digest(memory)
        effective_query = request.query
        if any(
            word in effective_query
            for word in ("它", "这个", "该指标", "that metric", "its ")
        ):
            previous = next(
                (
                    item["content"].get("query")
                    for item in reversed(memory)
                    if item["role"] == "user" and item["content"].get("query")
                ),
                None,
            )
            if previous:
                effective_query = previous + "\n" + effective_query
        published_steps = None
        if request.query_contract_id:
            if self.planner is None:
                raise AgenticSystemError("published query planner is unavailable")
            published_steps = self.planner(request)
            capabilities = tuple(step["capability"] for step in published_steps)
            route = IntentRoute(
                content_digest({"query": request.query}),
                capabilities,
                capabilities,
                ("published-query-contract",),
                None,
            )
        else:
            route = self.router.route(replace(request, query=effective_query))
        plan = AgenticPlan.build(
            request,
            route,
            memory_digest=memory_digest,
            memory_count=len(memory),
            published_steps=published_steps,
        )
        context = {
            "tenant_id": request.tenant_id,
            "session_id": request.session_id,
            "query": effective_query,
            "purpose": request.purpose,
            "channel": request.channel,
            "release_id": request.release_id,
            "release_digest": request.release_digest,
            "policy_resource_id": request.policy_resource_id,
            "memory": memory,
            "memory_digest": memory_digest,
            "query_contract_id": request.query_contract_id,
        }
        results_list = []
        for step in plan.steps:
            if step["kind"] != "capability":
                continue
            step_context = {
                **context,
                "step": step,
                "run_id": request.run_id,
                "previous_results": {
                    item.result_id.rsplit(":", 1)[-1]: item.to_dict()
                    for item in results_list
                },
            }
            result = CapabilityResult.create(
                result_id=f"{request.run_id}:{step['step_id']}",
                capability=step["capability"],
                value=self.executor(step["capability"], step_context),
            )
            for evidence in result.evidence:
                if (
                    evidence.get("release_digest", request.release_digest)
                    != request.release_digest
                ):
                    raise AgenticSystemError(
                        "capability evidence release digest mismatch"
                    )
            results_list.append(result)
            self.repository.finish(
                {
                    "run_id": request.run_id,
                    "tenant_id": request.tenant_id,
                    "session_id": request.session_id,
                    "actor": actor,
                    "status": "running",
                    "request": request.to_dict(),
                    "plan": plan.to_dict(),
                    "results": [item.to_dict() for item in results_list],
                }
            )
        results = tuple(results_list)
        summary = self.summary_validator.summarize(results)
        outcome = {
            "memory_context": {
                "items": memory,
                "count": len(memory),
                "memory_digest": memory_digest,
            },
            "route": route.to_dict(),
            "plan": plan.to_dict(),
            "results": [result.to_dict() for result in results],
            "summary": summary,
        }
        decision_id = None
        if self.decision_store is not None:
            decision_id = self.decision_store.record(
                agent_id=_text(actor, "actor"),
                decision_type="agentic_query_orchestration",
                conclusion="awaiting_approval"
                if any(result.capability == "action_submit" for result in results)
                else "succeeded",
                rationale="Execute the explicit release-pinned Agentic plan.",
                evidence=[
                    {
                        "id": result.result_id,
                        "type": "capability_result",
                        "content_hash": result.result_digest,
                    }
                    for result in results
                ],
                output_entities=[
                    {
                        "id": request.run_id,
                        "type": "agentic_run",
                        "content_hash": content_digest(outcome),
                    }
                ],
                tags=[*route.capabilities, request.purpose, "agentic-system"],
                policies=[request.policy_resource_id],
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                parent_decision_ids=[
                    result.receipt["decision_id"]
                    for result in results
                    if result.receipt.get("decision_id")
                ],
                metadata={
                    "release_id": request.release_id,
                    "release_digest": request.release_digest,
                    "plan_digest": plan.plan_digest,
                    "summary_digest": summary["summary_digest"],
                    "fallback": route.fallback,
                },
            )["decision"]["id"]
        run = {
            "api_version": "aof.agentic-run/v1",
            "run_id": request.run_id,
            "tenant_id": request.tenant_id,
            "session_id": request.session_id,
            "actor": _text(actor, "actor"),
            "status": "awaiting_approval"
            if any(result.capability == "action_submit" for result in results)
            else "succeeded",
            "decision_id": decision_id,
            "request": request.to_dict(),
            **outcome,
            "outcome_digest": content_digest(outcome),
            "replay_digest": self._replay_digest(outcome),
        }
        memory_entries = [
            ("user", {"query": request.query, "query_digest": route.query_digest}),
            (
                "assistant",
                {
                    "run_id": request.run_id,
                    "release_id": request.release_id,
                    "summary": summary,
                },
            ),
        ]
        if replay_source is not None:
            run["source_run_id"] = replay_source["run_id"]
            run["reproducible"] = run["replay_digest"] == replay_source["replay_digest"]
            memory_entries = []
        self.repository.finish(run, memory_entries)
        return run

    def replay(
        self, source_run_id: str, *, run_id: str, tenant_id: str, actor: str
    ) -> dict[str, Any]:
        source = self.repository.get(source_run_id, tenant_id=tenant_id)
        if any(
            item["capability"] == "action_submit" for item in source.get("results", [])
        ):
            raise AgenticSystemError(
                "action-bearing runs cannot be replayed; replay query runs separately"
            )
        if source["status"] != "succeeded":
            raise AgenticSystemError("only succeeded runs can be replayed")
        request_payload = dict(source["request"])
        request_payload["run_id"] = run_id
        replay = self.run(
            AgenticRequest.from_dict(request_payload),
            actor=actor,
            memory_snapshot=source["memory_context"]["items"],
            replay_source=source,
        )
        reproducible = replay["replay_digest"] == source["replay_digest"]
        return {**replay, "source_run_id": source_run_id, "reproducible": reproducible}

    def evaluate(self, run_id: str, *, tenant_id: str) -> dict[str, Any]:
        run = self.repository.get(run_id, tenant_id=tenant_id)
        if run["status"] != "succeeded":
            return {
                "run_id": run_id,
                "status": "failed",
                "checks": {"run_succeeded": False},
                "score": 0.0,
            }
        claims = run["summary"]["claims"]
        results = run["results"]
        cited = {citation for claim in claims for citation in claim["citations"]}
        result_ids = {result["result_id"] for result in results}
        checks = {
            "release_pinned": bool(run["plan"]["release_digest"]),
            "route_covered": len(results) == len(run["route"]["capabilities"]),
            "evidence_complete": all(result["evidence"] for result in results),
            "citation_complete": result_ids.issubset(cited),
            "plan_integrity": content_digest(
                {
                    "api_version": "aof.agentic-plan/v1",
                    "release_id": run["plan"]["release_id"],
                    "release_digest": run["plan"]["release_digest"],
                    "memory_digest": run["memory_context"]["memory_digest"],
                    "memory_count": run["memory_context"]["count"],
                    "steps": run["plan"]["steps"],
                }
            )
            == run["plan"]["plan_digest"],
        }
        return {
            "run_id": run_id,
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "score": sum(checks.values()) / len(checks),
        }

    def refresh_actions(self, run_id: str, *, tenant_id: str) -> dict[str, Any]:
        run = self.repository.get(run_id, tenant_id=tenant_id)
        ids = [
            item["output"]["action_run_id"]
            for item in run.get("results", [])
            if item["capability"] == "action_submit"
        ]
        if not ids or self.action_reader is None:
            raise AgenticSystemError("run has no action receipts")
        states = [self.action_reader(action_id) for action_id in ids]
        statuses = {state["status"] for state in states}
        run["action_receipts"] = states
        run["status"] = (
            "succeeded"
            if statuses == {"succeeded"}
            else "reconciliation_required"
            if "reconciliation_required" in statuses
            else "failed"
            if "failed" in statuses
            else "awaiting_approval"
        )
        self.repository.finish(run)
        return run

    @staticmethod
    def _replay_digest(outcome: Mapping[str, Any]) -> str:
        """Compare semantic outputs while excluding run-scoped receipt identifiers."""
        return content_digest(
            {
                "route": outcome["route"],
                "plan": outcome["plan"],
                "results": [
                    {
                        "capability": result["capability"],
                        "output": result["output"],
                        "evidence": result["evidence"],
                    }
                    for result in outcome["results"]
                ],
                "summary_claims": [
                    claim["text"] for claim in outcome["summary"]["claims"]
                ],
            }
        )
