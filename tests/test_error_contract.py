# -*- coding: utf-8 -*-
"""H2 error contract: uncaught exceptions never leak internals to clients.

The catch-all handler returns a fixed 500 body (code/message/requestId);
business exceptions keep their existing structured semantics.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import session_store
from atlas.iam.principals import authenticate

client = TestClient(app)
# raise_server_exceptions=False so the registered catch-all handler's response
# is observed instead of TestClient re-raising the route exception.
no_raise_client = TestClient(app, raise_server_exceptions=False)

_SECRET = "SUPER-SECRET-TOKEN-9f3k2"


def _admin_headers() -> dict[str, str]:
    principal = authenticate("admin-a", "admin123")
    assert principal is not None
    return {"Authorization": f"Bearer {session_store.issue(principal)}"}


def test_uncaught_exception_returns_fixed_body_without_leak(monkeypatch):
    import atlas.api.main as main_mod

    real_services_for = main_mod.services_for

    def _boom(*_args, **_kwargs):
        raise RuntimeError(f"db password={_SECRET} at /internal/path")

    def _patched(principal):
        services = real_services_for(principal)
        monkeypatch.setattr(services.audit_store, "export_jsonl", _boom)
        return services

    monkeypatch.setattr(main_mod, "services_for", _patched)
    resp = no_raise_client.get("/api/audit/export", headers=_admin_headers())
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"]["code"] == "INTERNAL_ERROR"
    assert body["detail"]["message"] == "internal error"
    assert _SECRET not in resp.text
    assert "RuntimeError" not in resp.text
    assert "/internal/path" not in resp.text
    # request id rides in the body: the catch-all runs in Starlette's outer
    # ServerErrorMiddleware, where the user middleware cannot set response headers.
    assert body["detail"]["requestId"]


def test_business_exception_keeps_semantics(monkeypatch):
    # A normal 422 from an endpoint must not be reshaped by the catch-all.
    resp = client.get(
        "/api/audit/events",
        params={"since": "not-a-date"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 422
    # Existing contract: detail is the aggregated Chinese error list.
    assert isinstance(resp.json()["detail"], list)
