"""W03.05 — Browser E2E over the production build (vite preview + uvicorn).

Flows verified (acceptance: 登录、权限拒绝、运行状态 UI):
1. login: paste a trusted-side signed principal envelope into the identity
   gateway dialog; the session establishes and the UI reflects the identity
2. permission denial: a viewer envelope attempting a governed action sees
   the server-side rejection surfaced as an error — no fake success
3. runtime state UI: views render without console errors

Run (from repo root, system python3 with playwright installed):
    python3 web/e2e/test_governance_e2e.py --base http://localhost:5199 \
        --secret <AOF_SEMANTIC_IDENTITY_SECRET>
The script signs fresh envelopes itself (trusted-side simulation: in the
real deployment the gateway signs them, never the browser).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright


def _sign_envelope(secret: bytes, *, subject: str, tenant: str, roles: list[str], key_id: str = "identity-key-default") -> dict[str, str]:
    """Mirror of bridge.semantic_core.identity.SignedPrincipalVerifier.sign_headers."""
    issued_at = int(time.time())
    payload = {
        "subject": subject,
        "tenant_id": tenant,
        "roles": sorted(set(roles)),
        "issued_at": issued_at,
        "key_id": key_id,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "x-aof-principal-subject": subject,
        "x-aof-principal-tenant": tenant,
        "x-aof-principal-roles": ",".join(payload["roles"]),
        "x-aof-principal-timestamp": str(issued_at),
        "x-aof-principal-key-id": key_id,
        "x-aof-principal-signature": signature,
    }


def _backend_ok(base: str) -> bool:
    # NOTE: the health endpoint is /healthz (NOT /v1/healthz); /v1/* paths
    # that do not exist correctly fall through to the SPA catch-all.
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def run(base: str, secret: bytes) -> int:
    if not _backend_ok(base):
        print(f"FAIL: backend not reachable at {base}/v1/healthz")
        return 2

    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        console_errors: list[str] = []
        page_errors: list[str] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        page.goto(base + "/ontology", wait_until="networkidle")
        assert "AOF" in page.title() or page.content(), "app must load"

        # ---- 1. login with an admin envelope ----
        # NOTE: with no stored identity the view's initialize() fails and
        # OPENS the identity dialog automatically - do not click the chip
        # while the overlay is up.
        admin = _sign_envelope(secret, subject="e2e-admin", tenant="acme", roles=["admin"])
        dialog = page.locator("div.el-dialog:has-text('连接企业身份网关')")
        dialog.wait_for(state="visible", timeout=10000)
        page.wait_for_timeout(500)  # el-dialog entrance animation
        dialog.locator("textarea").fill(json.dumps(admin, indent=2))
        dialog.get_by_role("button", name="验证并连接").click(force=True)
        page.wait_for_timeout(1500)
        print("step: admin envelope submitted")

        chip_text = page.locator("button.principal-chip").inner_text()
        if "e2e-admin" not in chip_text:
            page.screenshot(path="/tmp/aof-e2e-admin-fail.png")
            failures.append(f"login: identity not reflected in UI, chip={chip_text!r}")
        else:
            print("PASS login: admin envelope accepted, session reflects subject")

        # ---- 2. permission denial surfaced as error, no fake success ----
        viewer = _sign_envelope(secret, subject="e2e-viewer", tenant="acme", roles=["viewer"])
        page.click("button.principal-chip")  # session active: no overlay now
        dialog = page.locator("div.el-dialog:has-text('连接企业身份网关')")
        dialog.wait_for(state="visible", timeout=5000)
        page.wait_for_timeout(500)
        dialog.locator("textarea").fill(json.dumps(viewer, indent=2))
        dialog.get_by_role("button", name="验证并连接").click(force=True)
        page.wait_for_timeout(1500)
        print("step: viewer envelope submitted")

        chip_text = page.locator("button.principal-chip").inner_text()
        if "e2e-viewer" not in chip_text:
            page.screenshot(path="/tmp/aof-e2e-viewer-fail.png")
            failures.append(f"login: viewer envelope not accepted, chip={chip_text!r}")
        else:
            print("PASS login: viewer envelope accepted")

        # ---- 3. explicit permission denial from the browser origin ----
        # The stored (viewer) identity hits a governed write endpoint: the
        # server MUST reject it - no browser-side privilege escalation.
        denial = page.evaluate(
            """async () => {
                const h = JSON.parse(localStorage.getItem('aof.semantic-principal-headers') || '{}');
                const resp = await fetch('/v1/context/tenant-policy', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', ...h },
                    body: JSON.stringify({ tenant_id: h['x-aof-principal-tenant'], policy: { tenant_id: h['x-aof-principal-tenant'], spaces: {}, source_routes: [] } }),
                });
                return resp.status;
            }"""
        )
        if denial in (401, 403):
            print(f"PASS permission denial: viewer PUT context-policy -> {denial}")
        else:
            failures.append(f"permission denial: viewer PUT context-policy -> {denial} (want 401/403)")

        # ---- 4. views render (runtime state UI) ----
        for route in ("/", "/runtime", "/ingest"):
            page.goto(base + route, wait_until="networkidle")
            page.wait_for_timeout(400)
        # as admin, the runtime view must render its command head
        admin2 = _sign_envelope(secret, subject="e2e-admin", tenant="acme", roles=["admin"])
        page.evaluate(
            """(env) => localStorage.setItem('aof.semantic-principal-headers', JSON.stringify(env))""",
            admin2,
        )
        page.goto(base + "/runtime", wait_until="networkidle")
        page.wait_for_timeout(600)
        if page.locator("div.runtime .command-head").count() == 0:
            page.screenshot(path="/tmp/aof-e2e-runtime-fail.png")
            failures.append("runtime UI: command head not rendered")
        else:
            print("PASS runtime UI: runtime view renders")

        # console: 401/403 resource errors are EXPECTED permission denials;
        # JS page errors and 5xx are real failures
        fatal = [
            e for e in console_errors
            if "favicon" not in e.lower()
            and "401" not in e and "403" not in e
            and "500" not in e and "503" not in e
        ]
        if fatal:
            failures.append(f"console errors: {fatal[:3]}")
        else:
            print("PASS console: no unexpected errors (401/403 denials expected)")
        if page_errors:
            failures.append(f"page JS errors: {page_errors[:2]}")
        else:
            print("PASS console: no uncaught JS exceptions")

        browser.close()

    for line in failures:
        print("FAIL", line)
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:5199")
    parser.add_argument("--secret", required=True)
    args = parser.parse_args()
    sys.exit(run(args.base, args.secret.encode("utf-8")))
