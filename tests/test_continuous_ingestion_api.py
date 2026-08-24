"""Signed REST boundary for enterprise source registration and run evidence."""

from bridge.semantic_core import (
    SignedPrincipalVerifier,
    SourceBatch,
    SourceConnectorRegistry,
)


class ApiFixtureConnector:
    def fetch(self, source, cursor):
        return SourceBatch.create(
            cursor_from=cursor,
            cursor_to="event:1",
            records=[{"id": "contract:1", "title": "Framework Agreement"}],
            source_snapshot={"api_etag": "contracts-v1"},
        )


def _headers(role: str, subject: str, tenant: str = "acme") -> dict[str, str]:
    return SignedPrincipalVerifier(
        key_id="ingestion-identity", secret=b"ingestion-secret"
    ).sign_headers(subject=subject, tenant_id=tenant, roles=[role])


def test_signed_rest_source_ingestion_is_tenant_isolated_and_auditable(
    tmp_path, monkeypatch
) -> None:
    from fastapi.testclient import TestClient
    import services.semantic_middle_layer_api.app as api_module

    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "ingestion-secret")
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_KEY_ID", "ingestion-identity")
    monkeypatch.setenv(
        "AOF_CONTINUOUS_INGESTION_DATABASE", str(tmp_path / "ingestion.sqlite3")
    )
    connectors = SourceConnectorRegistry()
    connectors.register("fixture-api", ApiFixtureConnector())
    api_module.app.state.knowledge_source_connectors = connectors
    client = TestClient(api_module.app)

    unauthorized = client.post(
        "/v1/knowledge/sources",
        json={
            "source_id": "contracts",
            "source_type": "fixture-api",
            "config": {"identity_field": "id"},
        },
    )
    created = client.post(
        "/v1/knowledge/sources",
        json={
            "source_id": "contracts",
            "source_type": "fixture-api",
            "config": {"identity_field": "id"},
        },
        headers=_headers("owner", "alice"),
    )
    run = client.post(
        "/v1/knowledge/sources/contracts/ingest",
        json={"attempt_id": "api-attempt-1"},
        headers=_headers("ingestor", "worker"),
    )
    stored = client.get(
        f"/v1/knowledge/ingestion-runs/{run.json()['run_id']}",
        headers=_headers("viewer", "auditor"),
    )
    other = client.get(
        "/v1/knowledge/sources", headers=_headers("viewer", "mallory", "other")
    )

    assert unauthorized.status_code == 401
    assert created.status_code == 201 and created.json()["owner"] == "owner:alice"
    assert run.status_code == 201 and run.json()["status"] == "succeeded"
    assert run.json()["audit_decision_id"].startswith("decision:ingestion:")
    assert stored.json()["change_set"]["summary"] == {
        "added": 1,
        "updated": 0,
        "deleted": 0,
    }
    assert other.json()["count"] == 0
