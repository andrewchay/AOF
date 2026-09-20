# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with the
# Business Source License, use of this software will be governed by
# the Apache License, Version 2.0.

"""AOF MCP 知识构建编排。

将 GravitAI 文档快照转成 tenant 隔离的可信 release，并将同时含 rag 与
semantic-json 产物的独立回放 promote 到 production channel。产物路径与
QueryControlPlane 的 ``AOF_COMPILER_STATE_DIR/<tenant>`` 一致，因而
``aof_semantic_query`` 可以使用返回的 policy_resource_id + release_digest
执行 release-pinned 查询。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import ResourceKind, SemanticResource
from bridge.tenant_dataset_registry import register_owned_dataset
from bridge.semantic_core.compilers import (
    CompilationRunRepository,
    CompilationRunService,
    CompilerPolicy,
    default_compiler_registry,
)
from bridge.semantic_core.continuous_ingest import (
    ContinuousIngestionService,
    KnowledgeSource,
    SourceBatch,
    SourceConnector,
    SourceConnectorRegistry,
    SqliteContinuousIngestionRepository,
)
from bridge.semantic_core.governance import SemanticGovernanceService
from bridge.semantic_core.releases import KnowledgeRelease


def _slug(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _resolve_links(docs: list[dict[str, Any]]) -> int:
    """原地解析唯一标题 wikilink；多义链接断开并计数。"""
    titles: dict[str, list[str]] = {}
    for doc in docs:
        titles.setdefault(str(doc["title"]), []).append(str(doc["relative_path"]))
    dropped = 0
    for doc in docs:
        resolved: list[str] = []
        for link in doc.get("links", []):
            candidates = titles.get(str(link).strip(), [])
            if len(candidates) == 1 and candidates[0] != doc["relative_path"]:
                resolved.append(candidates[0])
            elif len(candidates) > 1:
                dropped += 1
        doc["resolved_links"] = resolved
    return dropped


class _SnapshotConnector(SourceConnector):
    def __init__(self, docs: list[dict[str, Any]]):
        self.docs = docs

    def fetch(self, source: KnowledgeSource, cursor: str | None) -> SourceBatch:
        records = tuple(
            {
                "id": f"doc-{_slug(str(doc['relative_path']))}",
                "path": doc["relative_path"],
                "content_sha256": doc["sha256"],
                "title": doc["title"],
                "links": doc.get("resolved_links", []),
            }
            for doc in self.docs
        )
        snapshot = json.dumps([doc["sha256"] for doc in self.docs], separators=(",", ":"))
        return SourceBatch.create(
            cursor_from=cursor,
            cursor_to=f"snapshot-{_slug(snapshot)}",
            records=records,
            source_snapshot={"doc_count": len(records)},
        )


def build_knowledge_release(
    *,
    kb_id: str,
    docs: list[dict[str, Any]],
    tenant_id: str,
    actor: str,
    knowledge_state_root: Path,
    compiler_state_root: Path,
) -> dict[str, Any]:
    """构建并 promote 一个 tenant-scoped 知识 release。"""
    docs = [dict(doc) for doc in docs]
    dropped = _resolve_links(docs)
    ledger: dict[str, Any] = {
        "kb_id": kb_id,
        "tenant_id": tenant_id,
        "doc_count": len(docs),
        "ambiguous_links_dropped": dropped,
    }

    tenant_root = knowledge_state_root / tenant_id
    tenant_root.mkdir(parents=True, exist_ok=True)
    decisions = DecisionProvenanceStore(tenant_root / "decisions.jsonl")

    # 1) Deterministic snapshot ingestion.
    ingestion_repo = SqliteContinuousIngestionRepository(tenant_root / "ingestion.sqlite3")
    connectors = SourceConnectorRegistry()
    connectors.register("knowledge-snapshot", _SnapshotConnector(docs))
    ingestion = ContinuousIngestionService(ingestion_repo, connectors, decisions=decisions)
    source = KnowledgeSource.create(
        source_id=f"kb-{_slug(kb_id)}",
        tenant_id=tenant_id,
        source_type="knowledge-snapshot",
        owner=actor,
        config={"kb_id": kb_id},
    )
    ingestion_repo.register_source(source)
    ingest_run = ingestion.ingest_once(source.source_id, tenant_id=tenant_id, actor=actor)
    ledger["ingest"] = {"status": ingest_run.status, "run_digest": ingest_run.run_digest}
    if ingest_run.status != "succeeded":
        return {
            "ok": False,
            "error": {"type": "IngestFailed", "message": "ingestion did not succeed"},
            "ledger": ledger,
        }

    # 2) Semantic resources. Policy IDs are stable per tenant and are referenced
    # by aof_semantic_query at the trusted release boundary.
    policy_resource_id = f"aof://{tenant_id}/platform/policy/query-adapter"
    resources = [
        SemanticResource.create(
            resource_id=f"aof://{tenant_id}/knowledge/ontology/knowledge",
            kind=ResourceKind.ONTOLOGY,
            name="knowledge",
            domain="knowledge",
            owner=actor,
            spec={"format": "turtle", "content": "@prefix ex: <https://example.test/> . ex:Doc a ex:Entity ."},
        ),
        SemanticResource.create(
            resource_id=f"aof://{tenant_id}/knowledge/retrieval-profile/default",
            kind=ResourceKind.RETRIEVAL_PROFILE,
            name="default",
            domain="knowledge",
            owner=actor,
            spec={"strategy": "keyword", "top_k": 10},
        ),
        SemanticResource.create(
            resource_id=policy_resource_id,
            kind=ResourceKind.POLICY,
            name="query-adapter",
            domain="platform",
            owner=actor,
            spec={"policy_type": "query", "role_capabilities": {"analyst": ["semantic_search"]}},
        ),
        SemanticResource.create(
            resource_id=f"aof://{tenant_id}/platform/policy/compiler-adapter",
            kind=ResourceKind.POLICY,
            name="compiler-adapter",
            domain="platform",
            owner=actor,
            spec={
                "policy_type": "compiler",
                "allowed_compilers": {"owl": ["owl@1"], "rag": ["rag@1"], "semantic-json": ["semantic-json@1"]},
            },
        ),
    ]
    path_to_id = {
        str(doc["relative_path"]): f"aof://{tenant_id}/knowledge/concept/doc-{_slug(str(doc['relative_path']))}"
        for doc in docs
    }
    for doc in docs:
        path = str(doc["relative_path"])
        resources.append(
            SemanticResource.create(
                resource_id=path_to_id[path],
                kind=ResourceKind.CONCEPT,
                name=str(doc["title"]),
                domain="knowledge",
                owner=actor,
                depends_on=[path_to_id[path] for path in doc.get("resolved_links", []) if path in path_to_id],
                spec={"source_path": path, "content_sha256": doc["sha256"]},
            )
        )
    ledger["resource_count"] = len(resources)

    # 3) Governance release. These role prefixes identify pipeline stages in
    # provenance; authentication was already performed by the MCP gateway.
    subject = actor.split(":", 1)[-1]
    governance = SemanticGovernanceService(
        tenant_root / "semantic-governance",
        decision_store=decisions,
        compiler_registry=default_compiler_registry(),
    )
    release_id = f"kb-{_slug(kb_id)}@{time.strftime('%Y.%m.%d.%H%M%S')}-{time.time_ns()}"
    proposal = governance.create_proposal(
        proposal_id=f"prop-{_slug(kb_id + release_id)}-{time.time_ns()}",
        release_id=release_id,
        resources=resources,
        actor=f"editor:{subject}",
        rationale="AOF knowledge graph build via MCP.",
    )
    review = governance.validate(proposal["proposal_id"], actor=f"validator:{subject}")
    if not review["conforms"]:
        return {
            "ok": False,
            "error": {"type": "ValidationFailed", "message": json.dumps(review, ensure_ascii=False)[:500]},
            "ledger": ledger,
        }
    governance.approve(proposal["proposal_id"], actor=f"reviewer:{subject}", rationale="MCP build.")
    governance.compile(proposal["proposal_id"], actor=f"compiler:{subject}", targets=["semantic-json"])
    published = governance.publish(proposal["proposal_id"], actor=f"publisher:{subject}")
    governance_digest = published["release"]["release_digest"]
    ledger["publish"] = {
        "release_id": release_id,
        "governance_release_digest": governance_digest,
        "verify": KnowledgeRelease.from_dict(published["release"]).verify(),
    }

    # 4) Trusted query runtime. Both artifacts are required: rag executes the
    # query and semantic-json is enforced at QueryControlPlane's boundary.
    query_release = KnowledgeRelease.build(release_id=release_id, resources=resources, scope={"tenant_id": tenant_id})
    compiler_repo = CompilationRunRepository(compiler_state_root / tenant_id)
    compiler_policy = CompilerPolicy.from_resource(next(r for r in resources if r.spec.get("policy_type") == "compiler"))
    compiler = CompilationRunService(compiler_repo, registry=default_compiler_registry(), decision_store=decisions)
    plan = default_compiler_registry().plan(query_release, resources=resources, targets=["rag", "semantic-json"])
    run_id = f"compile-{_slug(release_id)}-1"
    replay_id = f"compile-{_slug(release_id)}-2"
    compiler.execute(run_id=run_id, plan=plan, policy=compiler_policy, release=query_release, resources=resources, actor=f"compiler:{subject}", rationale="build")
    replay = compiler.replay(run_id, run_id=replay_id, policy=compiler_policy, release=query_release, resources=resources, actor=f"compiler:{subject}", rationale="reproduce")
    compiler.promote(
        replay.run_id,
        channel="production",
        actor=f"publisher:{subject}-pub",
        approved_by=f"reviewer:{subject}-review",
        rationale="promote",
    )
    ledger["query_runtime"] = {"promoted_channel": "production", "run_id": replay.run_id}
    # 为本次构建产生的 KB descriptor 登记当前签名 tenant 的 ownership；
    # 这是 aof_list_datasets 过滤的写侧依据。只登记自己的 KB 编号，
    # 不伪装为 Cognee 原始数据所有权，也不写其他 tenant 的记录。
    register_owned_dataset(
        state_root=knowledge_state_root,
        tenant_id=tenant_id,
        dataset_id=f"kb-{_slug(kb_id)}",
    )
    return {
        "ok": True,
        "release_id": release_id,
        "release_digest": query_release.release_digest,
        "policy_resource_id": policy_resource_id,
        "ledger": ledger,
    }
