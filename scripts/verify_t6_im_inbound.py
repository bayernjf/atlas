"""T6 证据：本地可达 IM 入站/出站 + 签名/去重/死信 + 网页嵌入回归。

- 本机 HTTP 端点充当 IM 群机器人平台（wecom 形状，返回 errcode=0）；
- `MessageService` 默认 `DefaultImSender` 经真实 HTTP POST 投递，记录 delivered；
- `ChannelBinding` 配 `inbound_secret` 与会话→图映射，`POST /api/channels/inbound/{id}`
  用 HMAC 签名入站，后台起真实 run，再由图内 `message/send` 出站回本地 IM；
- 同 messageId 二次入站为 duplicate；未知会话落死信并可查；
- 最后调用 `/api/embeds/config` 确认网页嵌入配置面未破。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "outputs" / "t6-im-inbound"
TENANT = "t1"
SECRET = "t6-inbound-secret"
IM_HITS: list[dict] = []


def _post_json(path: str, body: dict, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:8961{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _sign(raw: bytes) -> str:
    return base64.b64encode(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()).decode()


def _graph(im_url: str) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "name": "IM inbound",
                "config": {"triggerType": "message_inbound"},
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
            {
                "id": "msg-1",
                "type": "tool_call",
                "name": "IM 出站",
                "config": {
                    "tool": "message/send",
                    "params": json.dumps(
                        {
                            "channel": "wecom",
                            "to": [im_url],
                            "subject": "T6 入站已处理",
                            "body": "会话 {{trigger-1.context.payload.event.payload.conversationId}} 消息已收到",
                        },
                        ensure_ascii=False,
                    ),
                },
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "msg-1"}],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw.decode())
            IM_HITS.append({"path": self.path, "body": body})
            payload = json.dumps({"errcode": 0, "errmsg": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 8960), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    im_url = "http://127.0.0.1:8960/im-hook"
    os.environ.update(
        {
            "ATLAS_ENV": "dev",
            "ATLAS_ENABLE_DEMO_MOCK": "1",
            "ATLAS_MESSAGE_IM_ALLOW_LOOPBACK": "1",
        }
    )

    from fastapi.testclient import TestClient
    import atlas.api.main as main
    from atlas.channels.base import ChannelBinding
    from atlas.iam.deps import tenant_registry

    services = tenant_registry.get(TENANT)
    graph_id = services.graph_store.save(_graph(im_url))
    version_1 = services.graph_store.publish(graph_id, _graph(im_url))
    version_2 = services.graph_store.publish(graph_id, _graph(im_url))
    from atlas.routing.models import RolloutConfig

    services.routing_store.configure(
        graph_id, RolloutConfig(rules=[{"to": "full"}])
    )
    services.routing_store.start(
        graph_id, [version_1, version_2], stable=version_1, candidate=version_2
    )
    services.routing_store.promote(graph_id)
    binding = services.channel_registry._store.create(
        ChannelBinding(
            id="",
            tenant_id=TENANT,
            provider="shopify",
            connection_id="conn-t6-local",
            config={"inbound_secret": SECRET, "shop": "local-im", "apiVersion": "2024-01"},
            inbound_mappings=[
                {"conversation_id": "conv-t6-1", "graph_id": graph_id, "enabled": True}
            ],
        )
    )

    with TestClient(main.app) as client:
        raw = json.dumps(
            {
                "messageId": "msg-t6-1",
                "conversationId": "conv-t6-1",
                "channel": "wecom",
                "data": {"order_id": "T6-ORDER-1"},
            },
            ensure_ascii=False,
        ).encode()
        headers = {"X-Inbound-Signature": _sign(raw)}
        first = client.post(
            f"/api/channels/inbound/{binding.id}",
            content=raw,
            headers={**headers, "Content-Type": "application/json"},
        )
        assert first.status_code == 200, first.text
        duplicate = client.post(
            f"/api/channels/inbound/{binding.id}",
            content=raw,
            headers={**headers, "Content-Type": "application/json"},
        )
        bad = client.post(
            f"/api/channels/inbound/{binding.id}",
            content=raw,
            headers={"X-Inbound-Signature": "bad", "Content-Type": "application/json"},
        )
        dead_raw = json.dumps(
            {
                "messageId": "msg-t6-dead",
                "conversationId": "conv-t6-unknown",
                "channel": "wecom",
                "data": {},
            }
        ).encode()
        dead = client.post(
            f"/api/channels/inbound/{binding.id}",
            content=dead_raw,
            headers={"X-Inbound-Signature": _sign(dead_raw), "Content-Type": "application/json"},
        )
        login = client.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
        assert login.status_code == 200
        dead_list = client.get("/api/channels/inbound/dead-letters").json()
        embeds = client.get("/api/embeds/config")

    run_id = first.json().get("runId")
    for _ in range(200):
        run = services.run_store.get(run_id) if run_id else None
        if run and run.get("status") not in ("running", "suspended"):
            break
        time.sleep(0.02)
    else:
        run = services.run_store.get(run_id) if run_id else None

    evidence = {
        "mode": "local-im-inbound-outbound",
        "binding": services.channel_registry.get(binding.id),
        "graph": {"graphId": graph_id, "version": version_2, "routing": "full"},
        "inbound": {
            "received": first.json(),
            "duplicate": duplicate.json(),
            "badSignatureStatus": bad.status_code,
            "deadLetter": dead.json(),
            "deadLetters": dead_list,
        },
        "run": run,
        "outboundText": (
            IM_HITS[-1].get("body", {}).get("text", {}).get("content", "")
            if IM_HITS
            else ""
        ),
        "outboundImHits": IM_HITS,
        "embedsConfigStatus": embeds.status_code,
        "embedsConfig": embeds.json() if embeds.status_code == 200 else None,
    }
    evidence["ok"] = (
        first.json().get("received") is True
        and duplicate.json().get("duplicate") is True
        and bad.status_code == 401
        and dead.json().get("deadLetter") is True
        and any(item.get("messageId") == "msg-t6-dead" for item in dead_list.get("items", []))
        and bool(IM_HITS)
        and "conv-t6-1" in evidence["outboundText"]
        and "{{" not in evidence["outboundText"]
        and run is not None
        and run.get("status") == "completed"
        and embeds.status_code == 200
    )
    (OUT / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": evidence["ok"], "runId": run_id, "imHits": len(IM_HITS)}, ensure_ascii=False))
    server.shutdown()
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
