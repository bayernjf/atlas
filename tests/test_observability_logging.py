"""docs/65 K-D 日志 formatter + request-id 测试（常跑）。

请求中间件行为经 TestClient 实测；formatter/filter 语义直接单测。
"""

from __future__ import annotations

import logging
import re

from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.observability.logging import (
    RequestIdFilter,
    _UtcFormatter,
    configure_logging,
    request_id_var,
    set_request_id,
)

client = TestClient(app)

_HEX12 = re.compile(r"^[0-9a-f]{12}$")


def test_response_has_request_id_header() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-Id")
    assert rid is not None
    assert _HEX12.match(rid)


def test_request_ids_differ_between_requests() -> None:
    rid1 = client.get("/api/health").headers.get("X-Request-Id")
    rid2 = client.get("/api/health").headers.get("X-Request-Id")
    assert rid1 and rid2 and rid1 != rid2


def test_request_id_filter_injects_contextvar() -> None:
    record = logging.LogRecord("atlas.test", logging.INFO, "f", 1, "msg", None, None)
    # 无请求上下文 → 空白
    assert RequestIdFilter().filter(record) is True
    assert record.request_id == ""
    # 有请求上下文 → 注入（finally 复位，防跨测试污染）
    set_request_id("abc123456789")
    try:
        RequestIdFilter().filter(record)
        assert record.request_id == "abc123456789"
    finally:
        request_id_var.set("")


def test_utc_formatter_emits_iso_timestamp() -> None:
    formatter = _UtcFormatter("%(asctime)s %(levelname)s %(message)s")
    record = logging.LogRecord("atlas.test", logging.INFO, "f", 1, "hello", None, None)
    rendered = formatter.format(record)
    # 前缀为 UTC ISO：YYYY-MM-DDTHH:MM:SS.mmm+00:00
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00 ", rendered)
    assert "hello" in rendered


def test_configure_logging_idempotent(monkeypatch) -> None:
    from atlas.observability import logging as logging_module

    monkeypatch.setattr(logging_module, "_configured", False)
    configure_logging()
    root = logging.getLogger()
    handlers_after_first = list(root.handlers)
    configure_logging()  # 幂等：不再添加 handler
    assert list(root.handlers) == handlers_after_first
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, _UtcFormatter)
