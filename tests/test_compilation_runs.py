"""Behavioral contracts for immutable governed compilation runs."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import KnowledgeRelease, ResourceKind, SemanticResource
from bridge.semantic_core.compilers import (
    CompilationRunError,
    CompilationRunRepository,
    SqliteCompilationRunRepository,
    CompilationRunService,
    CompilerPolicy,
    default_compiler_registry,
)


def _inputs():
    concept = SemanticResource.create(
        resource_id="aof://acme/sales/concept/customer",
        kind=ResourceKind.CONCEPT,
        name="customer",
        domain="sales",
        owner="knowledge-team",
    )
    release = KnowledgeRelease.build(
        release_id="sales-knowledge@1.0.0", resources=[concept], scope={"tenant_id": "acme"}
    )
    registry = default_compiler_registry()
    plan = registry.plan(release, resources=[concept], targets=["mcp"])
    policy = CompilerPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/compiler-production",
            kind=ResourceKind.POLICY,
            name="compiler-production",
            domain="platform",
            owner="platform-governance",
            spec={
                "policy_type": "compiler",
                "allowed_compilers": {
                    "semantic-json": ["semantic-json@1"],
                    "mcp": ["mcp@1"],
                },
                "required_targets": ["semantic-json"],
            },
        )
    )
    return concept, release, registry, plan, policy


def test_compilation_runs_require_replay_before_promotion_and_support_rollback(tmp_path) -> None:
    concept, release, registry, plan, policy = _inputs()
    repository = CompilationRunRepository(tmp_path / "compiler-state")
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    service = CompilationRunService(repository, registry=registry, decision_store=decisions)

    first = service.execute(
        run_id="sales-compile-001",
        plan=plan,
        policy=policy,
        release=release,
        resources=(item for item in [concept]),
        actor="compiler:ci",
        rationale="Compile the approved release candidate.",
    )

    assert first.reproducible is False
    assert repository.get(first.run_id) == first
    assert [artifact["target"] for artifact in first.artifacts] == ["semantic-json", "mcp"]
    with pytest.raises(CompilationRunError, match="independent replay"):
        service.promote(
            first.run_id,
            channel="production",
            actor="publisher:bob",
            approved_by="reviewer:alice",
            rationale="Premature promotion must fail.",
        )

    replay = service.replay(
        first.run_id,
        run_id="sales-compile-002",
        policy=policy,
        release=release,
        resources=(item for item in [concept]),
        actor="compiler:replay-ci",
        rationale="Independent reproducibility check.",
    )

    assert replay.reproducible is True
    assert replay.replay_of == first.run_id
    assert [item["content_hash"] for item in replay.artifacts] == [
        item["content_hash"] for item in first.artifacts
    ]
    approval = service.approve_promotion(
        replay.run_id,
        channel="production",
        actor="reviewer:alice",
        rationale="The reproduced artifacts satisfy the production policy.",
    )
    promoted = service.promote_with_approval(
        replay.run_id,
        channel="production",
        actor="publisher:bob",
        approval_decision_id=approval["decision"]["id"],
        rationale="Promote the independently reproduced run.",
    )
    assert promoted["run_id"] == replay.run_id
    assert promoted["version"] == 1

    next_replay = service.replay(
        replay.run_id,
        run_id="sales-compile-003",
        policy=policy,
        release=release,
        resources=[concept],
        actor="compiler:replay-ci",
        rationale="Reproduce the promoted run again.",
    )
    service.promote(
        next_replay.run_id,
        channel="production",
        actor="publisher:carol",
        approved_by="reviewer:alice",
        rationale="Move the pointer to the newer immutable run.",
    )
    rolled_back = service.rollback(
        channel="production",
        to_run_id=replay.run_id,
        actor="publisher:dave",
        rationale="Rollback after a downstream incident.",
    )

    assert rolled_back["run_id"] == replay.run_id
    assert rolled_back["version"] == 3
    assert [event["action"] for event in rolled_back["history"]] == [
        "promote",
        "promote",
        "rollback",
    ]
    assert decisions.verify_integrity()["valid"] is True


def test_compilation_run_identity_is_immutable(tmp_path) -> None:
    concept, release, registry, plan, policy = _inputs()
    service = CompilationRunService(
        CompilationRunRepository(tmp_path / "compiler-state"),
        registry=registry,
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )
    service.execute(
        run_id="sales-compile-001",
        plan=plan,
        policy=policy,
        release=release,
        resources=[concept],
        actor="compiler:ci",
        rationale="First immutable run.",
    )

    with pytest.raises(CompilationRunError, match="already exists"):
        service.execute(
            run_id="sales-compile-001",
            plan=plan,
            policy=policy,
            release=release,
            resources=[concept],
            actor="compiler:ci",
            rationale="The same identity cannot execute twice.",
        )

    run_path = tmp_path / "compiler-state" / "runs" / "sales-compile-001.json"
    tampered = json.loads(run_path.read_text(encoding="utf-8"))
    tampered["reproducible"] = True
    run_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(CompilationRunError, match="run_digest"):
        service.repository.get("sales-compile-001")


def test_sqlite_compilation_state_survives_restart_and_full_backup(tmp_path) -> None:
    concept, release, registry, plan, policy = _inputs()
    root = tmp_path / "compiler-state"
    repository = SqliteCompilationRunRepository(root)
    service = CompilationRunService(
        repository,
        registry=registry,
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )
    first = service.execute(
        run_id="sales-sqlite-001",
        plan=plan,
        policy=policy,
        release=release,
        resources=[concept],
        actor="compiler:ci",
        rationale="Create transactional compiler state.",
    )
    replay = service.replay(
        first.run_id,
        run_id="sales-sqlite-002",
        policy=policy,
        release=release,
        resources=[concept],
        actor="compiler:replay-ci",
        rationale="Verify reproducibility before promotion.",
    )
    service.promote(
        replay.run_id,
        channel="production",
        actor="publisher:bob",
        approved_by="reviewer:alice",
        rationale="Promote the transactional state.",
    )

    restarted = SqliteCompilationRunRepository(root)
    assert restarted.get(replay.run_id) == replay
    assert restarted.get_channel("production")["run_id"] == replay.run_id
    assert restarted.schema_version() == 1
    assert restarted.verify_all() == {
        "valid": True,
        "run_count": 2,
        "channel_count": 1,
        "errors": [],
    }

    backup_root = restarted.backup_to(tmp_path / "backup")
    restored = SqliteCompilationRunRepository(backup_root)
    assert restored.get(replay.run_id) == replay
    assert restored.get_channel("production")["pointer_digest"] == (
        restarted.get_channel("production")["pointer_digest"]
    )
    assert restored.verify_all()["valid"] is True


def test_sqlite_compilation_repository_serializes_channel_updates_and_detects_tampering(
    tmp_path,
) -> None:
    concept, release, registry, plan, policy = _inputs()
    root = tmp_path / "compiler-state"
    repository = SqliteCompilationRunRepository(root)
    service = CompilationRunService(
        repository,
        registry=registry,
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )
    run = service.execute(
        run_id="sales-sqlite-001",
        plan=plan,
        policy=policy,
        release=release,
        resources=[concept],
        actor="compiler:ci",
        rationale="Create a run for concurrent pointer updates.",
    )
    event = {
        "action": "promote",
        "run_id": run.run_id,
        "actor": "publisher:test",
        "rationale": "Exercise transactional pointer history.",
    }

    with ThreadPoolExecutor(max_workers=8) as pool:
        pointers = list(
            pool.map(lambda _: repository.advance_channel("canary", event), range(24))
        )

    assert sorted(pointer["version"] for pointer in pointers) == list(range(1, 25))
    assert repository.get_channel("canary")["version"] == 24
    assert repository.verify_all()["valid"] is True

    artifact = next((root / "artifacts" / run.run_id).rglob("*.json"))
    artifact.write_text("{}", encoding="utf-8")
    report = repository.verify_all()
    assert report["valid"] is False
    assert any("artifact digest mismatch" in error for error in report["errors"])
