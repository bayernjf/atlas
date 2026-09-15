"""API 适配器（通用 HTTP）单元测试（13 文档 U25/I9-I11）。

全部外呼经 httpx MockTransport 注入，零真实网络出口。
"""

from __future__ import annotations

import json

import httpx
import pytest

from atlas.harness.base import (
    ActionRequest,
    ActionStatus,
    Permission,
    StructuredError,
)
from atlas.httpapi.adapter import HttpApiHarnessAdapter
from atlas.httpapi.service import HttpApiCallError, HttpApiClient


def make_client(handler, base_url="http://demo.test", default_headers=None):
    transport = httpx.MockTransport(handler)
    return HttpApiClient(
        base_url=base_url,
        default_headers=default_headers,
        client=httpx.Client(transport=transport),
    )


def test_200_json_body_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "http://demo.test/orders"
        return httpx.Response(200, json=[{"id": "o-1"}], request=request)

    result = make_client(handler).request(url="/orders")

    assert result["status"] == 200
    assert result["body"] == [{"id": "o-1"}]
    assert "content-type" in {k.lower() for k in result["headers"]}


def test_200_non_json_body_is_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello", request=request)

    result = make_client(handler).request(url="/ping")

    assert result["status"] == 200
    assert result["body"] == "hello"


def test_404_is_still_success_with_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "missing"}, request=request)

    result = make_client(handler).request(url="/missing")

    assert result["status"] == 404
    assert result["body"] == {"detail": "missing"}


def test_timeout_maps_to_structured_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(HttpApiCallError) as exc_info:
        make_client(handler).request(url="/slow", timeout=1)

    assert exc_info.value.code == "HTTP_TIMEOUT"


def test_connect_error_maps_to_structured_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(HttpApiCallError) as exc_info:
        make_client(handler, base_url="http://127.0.0.1:1").request(url="/orders")

    assert exc_info.value.code == "HTTP_CONNECT_ERROR"


def test_headers_merge_relative_url_node_wins():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["x-demo-token"] = request.headers.get("x-demo-token")
        seen["x-trace"] = request.headers.get("x-trace")
        return httpx.Response(200, json={}, request=request)

    client = make_client(
        handler,
        default_headers={"X-Demo-Token": "demo-token", "X-Trace": "default"},
    )
    client.request(url="orders", headers={"X-Trace": "node-value"})

    assert seen["url"] == "http://demo.test/orders"
    assert seen["x-demo-token"] == "demo-token"
    assert seen["x-trace"] == "node-value"


def test_absolute_url_bypasses_base_url():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={}, request=request)

    make_client(handler, base_url="http://demo.test").request(url="https://other.example.com/x")

    assert seen["url"] == "https://other.example.com/x"


def test_relative_url_without_base_url_is_missing_parameter():
    client = HttpApiClient(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))

    with pytest.raises(HttpApiCallError) as exc_info:
        client.request(url="/orders")

    assert exc_info.value.code == "MISSING_PARAMETER"


def test_dict_body_sent_as_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content-type"] = request.headers.get("content-type")
        seen["content"] = request.content
        return httpx.Response(200, json={"received": True}, request=request)

    make_client(handler).request(method="POST", url="/orders/o-1/receipt", body={"note": "自动处理"})

    assert seen["content-type"] == "application/json"
    assert json.loads(seen["content"]) == {"note": "自动处理"}


def test_string_body_sent_raw():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content-type"] = request.headers.get("content-type")
        seen["content"] = request.content
        return httpx.Response(200, text="ok", request=request)

    make_client(handler).request(method="POST", url="/raw", body="raw-text", headers={"Content-Type": "text/plain"})

    assert seen["content-type"] == "text/plain"
    assert seen["content"] == b"raw-text"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"method": "TRACE", "url": "/orders"},
        {"url": ""},
        {"url": "/orders", "headers": ["not", "a", "dict"]},
        {"url": "/orders", "timeout": 0},
        {"url": "/orders", "timeout": "5"},
        {"url": "/orders", "body": 12345},
    ],
)
def test_invalid_parameters_rejected(kwargs):
    client = HttpApiClient(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))

    with pytest.raises(HttpApiCallError) as exc_info:
        client.request(**kwargs)

    assert exc_info.value.code in {"INVALID_PARAMETER", "MISSING_PARAMETER"}


def test_from_env_reads_base_headers_and_bearer_token(monkeypatch):
    monkeypatch.setenv("ATLAS_HTTPAPI_BASE_URL", "http://env.test")
    monkeypatch.setenv("ATLAS_HTTPAPI_HEADERS", '{"X-Demo-Token": "demo-token"}')
    monkeypatch.setenv("ATLAS_HTTPAPI_TOKEN", "secret")

    client = HttpApiClient.from_env()

    assert client.base_url == "http://env.test"
    assert client.default_headers["X-Demo-Token"] == "demo-token"
    assert client.default_headers["Authorization"] == "Bearer secret"


def test_from_env_rejects_non_object_headers(monkeypatch):
    monkeypatch.setenv("ATLAS_HTTPAPI_HEADERS", "[1, 2]")

    with pytest.raises(ValueError):
        HttpApiClient.from_env()


def make_adapter(handler=None, granted=None, default_headers=None):
    client = make_client(
        handler or (lambda request: httpx.Response(200, json={"ok": True}, request=request)),
        default_headers=default_headers,
    )
    return HttpApiHarnessAdapter(
        client=client,
        granted_permissions=granted or {Permission.READ, Permission.WRITE},
    )


def test_adapter_success_output():
    adapter = make_adapter()

    result = adapter._execute(ActionRequest(capability_name="request", parameters={"url": "/orders"}))

    assert result.status is ActionStatus.SUCCESS
    assert result.output["status"] == 200
    assert result.output["body"] == {"ok": True}


def test_adapter_requires_write_permission_with_audit():
    audited = []
    adapter = HttpApiHarnessAdapter(
        client=make_client(lambda r: httpx.Response(200)),
        granted_permissions={Permission.READ},
        audit_sink=lambda adapter_id, name, req: audited.append((adapter_id, name)),
    )

    result = adapter.execute(ActionRequest(capability_name="request", parameters={"url": "/orders"}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "PERMISSION_DENIED"
    assert audited == [("http", "request")]


def test_adapter_maps_structured_errors():
    adapter = make_adapter()

    result = adapter.execute(ActionRequest(capability_name="request", parameters={"url": ""}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "MISSING_PARAMETER"


def test_adapter_unknown_capability():
    adapter = make_adapter()

    result = adapter.execute(ActionRequest(capability_name="nope", parameters={}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "UNKNOWN_CAPABILITY"


def test_adapter_declares_single_write_capability():
    adapter = make_adapter()
    capabilities = adapter.list_capabilities()

    assert [c.name for c in capabilities] == ["request"]
    assert capabilities[0].permission is Permission.WRITE
    assert capabilities[0].is_idempotent is False
    assert adapter.adapter_id == "http"
    assert adapter.adapter_type == "api"


def test_adapter_observe_reports_base_url_and_last_request():
    adapter = make_adapter()
    adapter.execute(ActionRequest(capability_name="request", parameters={"url": "/orders"}))

    observation = adapter.observe()

    assert observation.url == "http://demo.test"
    assert observation.data["last_request"] == {"method": "GET", "url": "http://demo.test/orders", "status": 200}
