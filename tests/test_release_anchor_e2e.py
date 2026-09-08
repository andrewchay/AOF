"""W02.03 — Release publish anchored with OpenBao; tamper detection."""

from __future__ import annotations

import pytest

from bridge.semantic_core.attestations import HmacReleaseAttestor
from bridge.semantic_core.compilers import default_compiler_registry
from bridge.semantic_core.governance import SemanticGovernancePolicy, SemanticGovernanceService
from bridge.semantic_core.models import ResourceKind, SemanticResource
from bridge.semantic_core.release_anchor import GovernedReleaseAnchor
from bridge.semantic_core.releases import SqliteReleaseRepository


def _openbao_up() -> bool:
    try:
        import socket
        s = socket.create_connection(("127.0.0.1", 8200), timeout=1)
        s.close()
        return True
    except OSError:
        return False

pytestmark = pytest.mark.skipif(
    not _openbao_up(), reason="OpenBao not running (deploy/docker-compose.infra.yml)"
)



def _make_resource():
    return SemanticResource.create(
        resource_id="aof://acme/test/ontology/test-onto",
        kind=ResourceKind.ONTOLOGY,
        name="test-onto",
        domain="test",
        owner="team",
    )


def _make_service(tmp_path, *, with_anchor: bool = True):
    repo = SqliteReleaseRepository(tmp_path / "releases.sqlite3")
    attestor = HmacReleaseAttestor(key_id="hmac-key", secret=b"test-hmac")
    anchor = GovernedReleaseAnchor() if with_anchor else None
    service = SemanticGovernanceService(
        tmp_path / "governance",
        compiler_registry=default_compiler_registry(),
        release_repository=repo,
        access_policy=SemanticGovernancePolicy(),
        release_attestor=attestor,
        release_anchor=anchor,
    )
    return service, anchor


def _publish(service, resource, proposal_id, release_id):
    proposal = service.create_proposal(
        proposal_id=proposal_id, release_id=release_id, resources=[resource],
        actor="editor:alice", rationale="test",
    )
    service.validate(proposal["proposal_id"], actor="validator:system")
    service.approve(proposal["proposal_id"], actor="reviewer:bob", rationale="OK")
    service.compile(proposal["proposal_id"], actor="compiler:sys", targets=["mcp"])
    return service.publish(proposal["proposal_id"], actor="publisher:carol")



def test_publish_with_anchor_signature(tmp_path):
    service, _anchor = _make_service(tmp_path)
    resource = _make_resource()
    proposal = service.create_proposal(
        proposal_id="anchored-001",
        release_id="test@1.0.0",
        resources=[resource],
        actor="editor:alice",
        rationale="Anchor E2E.",
    )
    service.validate(proposal["proposal_id"], actor="validator:system")
    service.approve(proposal["proposal_id"], actor="reviewer:bob", rationale="OK")
    service.compile(proposal["proposal_id"], actor="compiler:sys", targets=["mcp"])
    published = service.publish(proposal["proposal_id"], actor="publisher:carol")

    assert "anchor_signature" in published
    assert published["anchor_signature"].startswith("vault:v")


def test_anchor_detects_tampered_release(tmp_path):
    service, anchor = _make_service(tmp_path)
    resource = _make_resource()
    proposal = service.create_proposal(
        proposal_id="anchored-002",
        release_id="test-tamper@1.0.0",
        resources=[resource],
        actor="editor:alice",
        rationale="Tamper detection.",
    )
    service.validate(proposal["proposal_id"], actor="validator:system")
    service.approve(proposal["proposal_id"], actor="reviewer:bob", rationale="OK")
    service.compile(proposal["proposal_id"], actor="compiler:sys", targets=["mcp"])
    published = service.publish(proposal["proposal_id"], actor="publisher:carol")

    # the anchor should verify the honest release
    assert anchor.verify_publish(
        release_id=published["release"]["release_id"],
        release_digest=published["release"]["release_digest"],
        tenant_id=published["tenant_id"],
        publish_decision_id=published["publish_decision_id"],
        anchor_signature=published["anchor_signature"],
    ) is True

    # tamper the release digest: anchor verification fails
    assert anchor.verify_publish(
        release_id=published["release"]["release_id"],
        release_digest="sha256:TAMPERED",
        tenant_id=published["tenant_id"],
        publish_decision_id=published["publish_decision_id"],
        anchor_signature=published["anchor_signature"],
    ) is False


def test_publish_without_anchor_works(tmp_path):
    """后向兼容：不配置 anchor 时 publish 正常工作（无 anchor_signature）。"""
    service, _ = _make_service(tmp_path, with_anchor=False)
    resource = _make_resource()
    proposal = service.create_proposal(
        proposal_id="no-anchor",
        release_id="no-anchor@1.0.0",
        resources=[resource],
        actor="editor:alice",
        rationale="No anchor.",
    )
    service.validate(proposal["proposal_id"], actor="validator:system")
    service.approve(proposal["proposal_id"], actor="reviewer:bob", rationale="OK")
    service.compile(proposal["proposal_id"], actor="compiler:sys", targets=["mcp"])
    published = service.publish(proposal["proposal_id"], actor="publisher:carol")

    assert "anchor_signature" not in published
    assert published["state"] == "published"
