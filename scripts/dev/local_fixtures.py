#!/usr/bin/env python3
"""本机全链演练的外部系统替身（SMTP / Shopify Admin / IM / OAuth / OpenAI-compatible LLM）。

只给 `prod_rehearsal.py --local-fixtures` 用：监听宿主机端口，容器通过
`host.docker.internal` 访问；不接真实外部系统，不写生产数据。所有收到的请求都落在
`--state-file`，供演练脚本断言“确实发出去了”，而不是只看工具返回 SUCCESS。

这不是生产服务：没有鉴权、没有持久队列、没有重试；仅用于本地最大完成度演练。
"""

from __future__ import annotations

import argparse
import asyncore
import json
import os
import re
import signal
import smtpd
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

LOCAL_ORDER_ID = "1001"
LOCAL_ORDER_TOTAL = 299.00
LOCAL_REFUND_ID = 99001


class FixtureState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock = threading.RLock()
        self.emails: list[dict[str, Any]] = []
        self.im_messages: list[dict[str, Any]] = []
        self.refunds: list[dict[str, Any]] = []
        self.webhooks: list[dict[str, Any]] = []
        self.oauth_tokens: list[dict[str, Any]] = []
        self.llm_calls: list[dict[str, Any]] = []

    def add(self, bucket: str, item: dict[str, Any]) -> None:
        with self.lock:
            getattr(self, bucket).append(item)
            self.write()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "emails": list(self.emails),
                "im_messages": list(self.im_messages),
                "refunds": list(self.refunds),
                "webhooks": list(self.webhooks),
                "oauth_tokens": list(self.oauth_tokens),
                "llm_calls": list(self.llm_calls),
            }

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def _order() -> dict[str, Any]:
    return {
        "id": int(LOCAL_ORDER_ID),
        "name": f"#{LOCAL_ORDER_ID}",
        "email": "buyer@local.test",
        "financial_status": "paid",
        "fulfillment_status": None,
        "total_price": f"{LOCAL_ORDER_TOTAL:.2f}",
        "currency": "CNY",
        "created_at": "2026-11-25T00:00:00Z",
        "line_items": [{"id": 11, "quantity": 1}],
    }


class FixtureHandler(BaseHTTPRequestHandler):
    state: FixtureState
    server_version = "AtlasLocalFixture/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - BaseHTTPRequestHandler API
        return

    def _send(self, status: int, payload: dict[str, Any] | None = None) -> None:
        raw = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length)

    def _json_body(self) -> dict[str, Any]:
        raw = self._body()
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path in ("/state", "/health"):
            self._send(200, self.state.snapshot() if path == "/state" else {"ok": True})
            return
        if path == "/shopify-admin/shop.json":
            self._send(200, {"shop": {
                "id": 1,
                "name": "Atlas Local Fixture",
                "domain": "atlas-local.myshopify.com",
            }})
            return
        if path == "/shopify-admin/orders.json":
            self._send(200, {"orders": [_order()]})
            return
        if re.fullmatch(r"/shopify-admin/orders/[^/]+\.json", path):
            self._send(200, {"order": _order()})
            return
        if path == "/shopify-admin/webhooks.json":
            with self.state.lock:
                items = list(self.state.webhooks)
            self._send(200, {"webhooks": items})
            return
        if path == "/oauth/authorize":
            query = parse_qs(urlparse(self.path).query)
            redirect_uri = (query.get("redirect_uri") or [""])[0]
            state = (query.get("state") or [""])[0]
            # 浏览器授权页的本地替身：不真跳转，直接给演练脚本一个可换 token 的 code。
            self._send(200, {
                "code": "fixture-code",
                "state": state,
                "redirectUri": redirect_uri,
            })
            return
        self._send(404, {"error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/shopify-admin/orders/1001/refunds.json":
            body = self._json_body()
            refund = body.get("refund") if isinstance(body.get("refund"), dict) else {}
            try:
                amount = float((refund.get("transactions") or [{}])[0].get("amount", "0"))
            except (TypeError, ValueError):
                amount = 0.0
            if amount <= 0 or amount > LOCAL_ORDER_TOTAL:
                self._send(422, {"errors": "invalid refund amount"})
                return
            item = {
                "id": LOCAL_REFUND_ID,
                "orderId": LOCAL_ORDER_ID,
                "amount": amount,
                "note": refund.get("note", ""),
                "lineItems": refund.get("refund_line_items", []),
            }
            self.state.add("refunds", item)
            self._send(200, {"refund": {
                "id": LOCAL_REFUND_ID,
                "status": "refunded",
                "order_id": int(LOCAL_ORDER_ID),
            }})
            return
        if path == "/shopify-admin/webhooks.json":
            body = self._json_body()
            webhook = body.get("webhook") if isinstance(body.get("webhook"), dict) else {}
            topic = str(webhook.get("topic") or "")
            address = str(webhook.get("address") or "")
            with self.state.lock:
                duplicate = any(
                    item.get("topic") == topic and item.get("address") == address
                    for item in self.state.webhooks
                )
            if duplicate:
                self._send(422, {"errors": "already registered"})
                return
            item = {"id": 70001, "topic": topic, "address": address}
            self.state.add("webhooks", item)
            self._send(201, {"webhook": item})
            return
        if path == "/oauth/token":
            raw = self._body()
            form = parse_qs(raw.decode("utf-8")) if raw else {}
            grant = (form.get("grant_type") or ["authorization_code"])[0]
            item = {
                "grantType": grant,
                "code": (form.get("code") or [""])[0],
                "clientId": (form.get("client_id") or [""])[0],
            }
            self.state.add("oauth_tokens", item)
            self._send(200, {
                "access_token": "fixture-access-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "fixture-refresh-token",
                "scope": "read write",
            })
            return
        if path in ("/im/feishu", "/im/dingtalk", "/im/wecom"):
            body = self._json_body()
            channel = path.rsplit("/", 1)[-1]
            self.state.add("im_messages", {"channel": channel, "payload": body})
            if channel == "feishu":
                self._send(200, {"code": 0, "msg": "ok"})
            else:
                self._send(200, {"errcode": 0, "errmsg": "ok"})
            return
        if path == "/v1/chat/completions":
            body = self._json_body()
            model = str(body.get("model") or "atlas-local")
            messages = body.get("messages") or []
            content = json.dumps({
                "action": "approve_refund",
                "reason": "质量问题破损，金额在审批限额内",
                "confidence": 0.98,
            }, ensure_ascii=False)
            self.state.add("llm_calls", {
                "model": model,
                "messages": messages,
                "content": content,
            })
            self._send(200, {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 12, "completion_tokens": 18, "total_tokens": 30},
            })
            return
        self._send(404, {"error": "not_found", "path": path})

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        match = re.fullmatch(r"/shopify-admin/webhooks/[^/]+\.json", path)
        if match:
            remote_id = path.rsplit("/", 1)[-1].removesuffix(".json")
            with self.state.lock:
                before = len(self.state.webhooks)
                self.state.webhooks[:] = [
                    item for item in self.state.webhooks if str(item.get("id")) != remote_id
                ]
                changed = len(self.state.webhooks) < before
            if changed:
                self.state.write()
            self._send(200, {"deleted": changed})
            return
        self._send(404, {"error": "not_found", "path": path})


class FixtureSMTPServer(smtpd.SMTPServer):
    def __init__(self, localaddr: tuple[str, int], state: FixtureState) -> None:
        super().__init__(localaddr, None, decode_data=True)
        self.state = state

    def process_message(  # noqa: N802 - smtpd API
        self,
        peer: tuple[str, int],
        mailfrom: str,
        rcpttos: list[str],
        data: str | bytes,
        **kwargs: Any,
    ) -> None:
        if isinstance(data, bytes):
            raw = data.decode("utf-8", errors="replace")
        else:
            raw = data
        self.state.add("emails", {
            "from": mailfrom,
            "to": list(rcpttos),
            "peer": str(peer),
            "data": raw,
        })


def main() -> int:
    parser = argparse.ArgumentParser(description="Atlas local fixture services")
    parser.add_argument("--http-host", default="0.0.0.0")
    parser.add_argument("--http-port", type=int, default=9099)
    parser.add_argument("--smtp-host", default="0.0.0.0")
    parser.add_argument("--smtp-port", type=int, default=1025)
    parser.add_argument("--state-file", default="outputs/local-fixtures/state.json")
    args = parser.parse_args()

    state = FixtureState(args.state_file)
    state.write()

    FixtureHandler.state = state
    httpd = ThreadingHTTPServer((args.http_host, args.http_port), FixtureHandler)
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()

    smtp_server = FixtureSMTPServer((args.smtp_host, args.smtp_port), state)
    smtp_thread = threading.Thread(
        target=asyncore.loop,
        kwargs={"timeout": 0.2},
        daemon=True,
    )
    smtp_thread.start()

    print(json.dumps({
        "http": f"http://127.0.0.1:{args.http_port}",
        "smtp": f"{args.smtp_host}:{args.smtp_port}",
        "state": str(Path(args.state_file).resolve()),
    }, ensure_ascii=False), flush=True)

    def _stop(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
        smtp_server.close()
        state.write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
