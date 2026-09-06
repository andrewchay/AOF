"""W06.04 — Data classification, log minimization, versioned retention.

- every field classifies (restricted/confidential/internal)
- logs carry the minimum: restricted masked, confidential digested
- sensitive sentinels must never leak into a log projection
- retention: dry-run plan first, apply returns a receipt bound to the
  policy revision; versioned policies take effect for new registrations
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bridge.access.data_governance import (
    DataClass,
    DataGovernanceError,
    RetentionEngine,
    RetentionPolicy,
    RetentionRecordStore,
    classify_field,
    digest_confidential,
    ensure_no_sentinel,
    make_sentinel,
    mask_restricted,
    minimize_for_log,
    ttl_for,
)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_field_classification_rules():
    assert classify_field("password_hash") == DataClass.RESTRICTED
    assert classify_field("api_key") == DataClass.RESTRICTED
    assert classify_field("user_question") == DataClass.CONFIDENTIAL
    assert classify_field("conclusion") == DataClass.INTERNAL
    assert classify_field("dataset_name") == DataClass.INTERNAL


def test_nested_payload_minimization():
    payload = {
        "run_id": "run-1",
        "operator": {"password": "hunter2secret", "name": "alice"},
        "question": "公司的收入偏差原因是什么？",
        "api_key": "sk-abcdef123456",
        "tags": ["finance"],
    }
    minimized = minimize_for_log(payload)

    assert minimized["run_id"] == "run-1"
    assert minimized["operator"]["name"] == "alice"
    assert minimized["operator"]["password"] != "hunter2secret"
    assert "hunter2secret" not in str(minimized)
    assert minimized["question"].startswith("sha256:")
    assert "收入偏差" not in str(minimized)
    assert minimized["api_key"] != "sk-abcdef123456"
    assert "sk-abcdef" not in str(minimized)
    assert minimized["tags"] == ["finance"]


def test_mask_and_digest_helpers():
    assert mask_restricted("ab") == "****"
    assert mask_restricted("secret-value")[2:6] == "****"
    assert digest_confidential("x").startswith("sha256:")
    assert digest_confidential("x") != digest_confidential("y")


# ---------------------------------------------------------------------------
# Sentinels — sensitive canaries never leak
# ---------------------------------------------------------------------------


def test_sensitive_sentinel_never_leaks_into_log_projection():
    sentinel_password = make_sentinel("db-password-9f2")
    sentinel_question = make_sentinel("trade-secret-question")
    payload = {
        "password": sentinel_password,
        "question": sentinel_question,
        "run_id": "run-2",
    }

    minimized = minimize_for_log(payload)

    # no exception == no sentinel anywhere in the projection
    ensure_no_sentinel(minimized)
    ensure_no_sentinel(str(minimized))


def test_sentinel_check_detects_leak():
    leaked = {"note": f"oops {make_sentinel('leak-1')}"}
    with pytest.raises(DataGovernanceError):
        ensure_no_sentinel(leaked)


# ---------------------------------------------------------------------------
# Versioned retention: dry-run → apply → receipt
# ---------------------------------------------------------------------------


def _policy(ttl_by_class: dict[str, int], revision: int = 1) -> RetentionPolicy:
    return RetentionPolicy(
        policy_id="fin-default",
        revision=revision,
        ttl_days=ttl_by_class,
    )


def test_dry_run_does_not_delete(tmp_path):
    store = RetentionRecordStore(tmp_path / "retention.sqlite")
    policy = _policy({"restricted": 30, "confidential": 90})
    store.register(data_key="k-1", tenant_id="t1", data_class=DataClass.RESTRICTED, policy=policy)

    engine = RetentionEngine(store)
    plan = engine.plan(policy=policy, now=datetime.now(timezone.utc) + timedelta(days=31))

    assert plan.dry_run is True
    assert len(plan.items) == 1
    assert plan.items[0].data_key == "k-1"
    # dry-run leaves the record in place
    assert store.record_count() == 1


def test_apply_deletes_and_returns_policy_bound_receipt(tmp_path):
    store = RetentionRecordStore(tmp_path / "retention.sqlite")
    policy = _policy({"confidential": 7})
    now = datetime.now(timezone.utc)
    store.register(data_key="k-old", tenant_id="t1", data_class=DataClass.CONFIDENTIAL,
                   policy=policy, created_at=now - timedelta(days=10))
    store.register(data_key="k-new", tenant_id="t1", data_class=DataClass.CONFIDENTIAL,
                   policy=policy, created_at=now - timedelta(days=1))

    engine = RetentionEngine(store)
    plan = engine.plan(policy=policy, now=now)
    assert {item.data_key for item in plan.items} == {"k-old"}

    receipt = engine.apply(plan)
    assert receipt["deleted"] == 1
    assert receipt["policy_id"] == "fin-default"
    assert receipt["policy_revision"] == policy.revision
    assert store.record_count() == 1


def test_ttl_none_means_keep(tmp_path):
    store = RetentionRecordStore(tmp_path / "retention.sqlite")
    policy = _policy({"restricted": 30})  # internal has NO ttl -> keep forever
    store.register(data_key="k-keep", tenant_id="t1", data_class=DataClass.INTERNAL, policy=policy)

    engine = RetentionEngine(store)
    plan = engine.plan(policy=policy, now=datetime.now(timezone.utc) + timedelta(days=3650))
    assert plan.items == ()
    assert store.record_count() == 1


def test_policy_revision_is_versioned(tmp_path):
    """新 revision 生效：plan 的回执绑定提交时记录的 revision。"""
    store = RetentionRecordStore(tmp_path / "retention.sqlite")
    v1 = _policy({"confidential": 90}, revision=1)
    v2 = _policy({"confidential": 7}, revision=2)  # tightened

    store.register(data_key="k-a", tenant_id="t1", data_class=DataClass.CONFIDENTIAL, policy=v1)
    # register v2 record: policy version changed between registrations
    store.register(data_key="k-b", tenant_id="t1", data_class=DataClass.CONFIDENTIAL, policy=v2)

    engine = RetentionEngine(store)
    now = datetime.now(timezone.utc) + timedelta(days=8)

    # v2 policy (current): k-a (90-day class at registration) — plan is policy-driven,
    # not registration-driven: BOTH records are confidential; v2 ttl=7 → both expire
    plan_v2 = engine.plan(policy=v2, now=now)
    assert {item.data_key for item in plan_v2.items} == {"k-a", "k-b"}

    # the receipt keeps the policy revision that justified the deletion
    receipt = engine.apply(plan_v2)
    assert receipt["policy_revision"] == 2
    # per-item record kept the revision in force at registration time
    assert {item.policy_revision for item in plan_v2.items} == {1, 2}


def test_ttl_lookup_helpers():
    policy = _policy({"restricted": 30})
    assert ttl_for(policy, DataClass.RESTRICTED) == 30
    assert ttl_for(policy, DataClass.CONFIDENTIAL) is None
