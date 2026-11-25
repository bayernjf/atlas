# -*- coding: utf-8 -*-
"""本机全链 fixture 的最小契约测试：只测 HTTP 面，不测 SMTP 进程管理。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from threading import Thread
from http.server import ThreadingHTTPServer

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "atlas_local_fixtures", _ROOT / "scripts/dev/local_fixtures.py"
)
assert _SPEC and _SPEC.loader
fixtures = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fixtures)


@pytest.fixture()
def fixture_server(tmp_path):
    state = fixtures.FixtureState(tmp_path / "state.json")
    fixtures.FixtureHandler.state = state
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), fixtures.FixtureHandler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], state
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_shopify_fixture_order_refund_and_state(fixture_server):
    port, state = fixture_server
    base = f"http://127.0.0.1:{port}"
    shop = httpx.get(f"{base}/shopify-admin/shop.json", timeout=5).json()
    assert shop["shop"]["domain"] == "atlas-local.myshopify.com"
    order = httpx.get(f"{base}/shopify-admin/orders/1001.json", timeout=5).json()
    assert order["order"]["total_price"] == "299.00"
    refund = httpx.post(
        f"{base}/shopify-admin/orders/1001/refunds.json",
        json={"refund": {"note": "fixture", "refund_line_items": [],
                         "transactions": [{"kind": "refund", "amount": "0.01"}]}},
        timeout=5,
    )
    assert refund.status_code == 200
    assert refund.json()["refund"]["id"] == 99001
    assert state.snapshot()["refunds"][0]["amount"] == 0.01


def test_oauth_and_im_fixture_contract(fixture_server):
    port, state = fixture_server
    base = f"http://127.0.0.1:{port}"
    token = httpx.post(
        f"{base}/oauth/token",
        data={"grant_type": "authorization_code", "code": "fixture-code",
              "client_id": "atlas-local", "client_secret": "fixture-secret"},
        timeout=5,
    )
    assert token.status_code == 200
    assert token.json()["access_token"] == "fixture-access-token"
    im = httpx.post(f"{base}/im/feishu", json={"msg_type": "text"}, timeout=5)
    assert im.json() == {"code": 0, "msg": "ok"}
    assert state.snapshot()["im_messages"][0]["channel"] == "feishu"


def test_openai_compatible_fixture_returns_decision(fixture_server):
    port, state = fixture_server
    base = f"http://127.0.0.1:{port}"
    res = httpx.post(
        f"{base}/v1/chat/completions",
        json={"model": "openai/gpt-4o-mini",
              "messages": [{"role": "user", "content": "退款原因：破损"}]},
        timeout=5,
    )
    assert res.status_code == 200
    content = res.json()["choices"][0]["message"]["content"]
    payload = json.loads(content)
    assert payload["action"] == "approve_refund"
    assert state.snapshot()["llm_calls"][0]["model"] == "openai/gpt-4o-mini"
