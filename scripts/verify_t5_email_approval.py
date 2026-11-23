"""T5 证据：真实本地 SMTP 审批邮件出站 + 邮件深链回站决策。

- 本机 `smtpd` 起真实 SMTP 服务（明文、无认证，仅 127.0.0.1）；
- `human_approval` 节点配置 `notifyEmails`，由 `EmailApprovalNotifier` 经真实
  `SmtpSender` 发挂起通知；
- HTTP `GET /api/approvals/email-view` 读邮件深链；`POST /api/approvals/email-decision`
  用邮件里的签名 token 回站决策；
- 决策后图继续执行、结果邮件再次经同一 SMTP 投递；坏 token 404 作为失败可见证据。

保留 A-05 的本地 webhook 证据不变；本脚本只补 SMTP 这一条真实可达渠道。
"""

from __future__ import annotations

import json
import os
import re
import smtpd
import threading
import time
from email import message_from_bytes, policy
from datetime import datetime, timezone
from pathlib import Path

import asyncore

OUT = Path(__file__).resolve().parents[1] / "outputs" / "t5-email-approval"
TENANT = "t1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _CaptureSMTP(smtpd.SMTPServer):
    messages: list[dict] = []

    def process_message(self, peer, mailfrom, rcpttos, data, **kwargs):  # noqa: ANN001
        raw = data if isinstance(data, bytes) else str(data).encode()
        message = message_from_bytes(raw, policy=policy.default)
        body_part = message.get_body(preferencelist=("plain",))
        body = body_part.get_content() if body_part is not None else raw.decode("utf-8", errors="replace")
        self.messages.append(
            {
                "from": mailfrom,
                "to": list(rcpttos),
                "subject": str(message.get("Subject") or ""),
                "body": body,
            }
        )
        return None


def _wait_until(predicate, timeout: float = 10.0, label: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise RuntimeError(f"timeout waiting for {label}")


def _graph() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "name": "start",
                "config": {"triggerType": "webhook", "webhookUrl": "/hooks/t5"},
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
            {
                "id": "approval-1",
                "type": "human_approval",
                "name": "邮件审批",
                "config": {
                    "summary": "T5 邮件审批：订单 {{trigger-1.context.payload.order_id}}",
                    "notifyEmails": ["approver@example.com"],
                    "timeoutSeconds": 30,
                    "onTimeout": "reject",
                    "approvedTarget": "msg-approved",
                    "rejectedTarget": "msg-rejected",
                },
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
            {
                "id": "msg-approved",
                "type": "tool_call",
                "name": "通过通知",
                "config": {
                    "tool": "message/send",
                    "params": json.dumps(
                        {
                            "channel": "email",
                            "to": ["approver@example.com"],
                            "subject": "T5 审批已通过",
                            "body": "订单 {{trigger-1.context.payload.order_id}} 的审批已通过",
                        },
                        ensure_ascii=False,
                    ),
                },
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
            {
                "id": "msg-rejected",
                "type": "tool_call",
                "name": "拒绝通知",
                "config": {
                    "tool": "message/send",
                    "params": json.dumps(
                        {
                            "channel": "email",
                            "to": ["approver@example.com"],
                            "subject": "T5 审批被拒绝",
                            "body": "订单 {{trigger-1.context.payload.order_id}} 的审批被拒绝",
                        },
                        ensure_ascii=False,
                    ),
                },
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "approval-1"},
            {"id": "e2", "source": "approval-1", "target": "msg-approved"},
            {"id": "e3", "source": "approval-1", "target": "msg-rejected"},
        ],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    smtp = _CaptureSMTP(("127.0.0.1", 0), None)
    smtp_port = smtp.socket.getsockname()[1]
    threading.Thread(target=asyncore.loop, kwargs={"timeout": 0.1}, daemon=True).start()

    os.environ.update(
        {
            "ATLAS_ENV": "dev",
            "ATLAS_ENABLE_DEMO_MOCK": "1",
            "ATLAS_PUBLIC_URL": "http://127.0.0.1:8951",
            "ATLAS_APPROVAL_HMAC_SECRET": "t5-local-approval-secret",
            "ATLAS_SMTP_HOST": "127.0.0.1",
            "ATLAS_SMTP_PORT": str(smtp_port),
            "ATLAS_SMTP_FROM": "atlas@local.test",
            "ATLAS_SMTP_USE_TLS": "0",
        }
    )

    from fastapi.testclient import TestClient
    import atlas.api.main as main
    from atlas.collaboration.notifications import EmailApprovalNotifier
    from atlas.graph.dsl import parse_graph
    from atlas.graph.loader import run_graph
    from atlas.harness.base import Permission
    from atlas.harness.registry import AdapterRegistry
    from atlas.iam.deps import tenant_registry
    from atlas.message.adapter import MessageHarnessAdapter

    services = tenant_registry.get(TENANT)
    permissions = {Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL}
    registry = AdapterRegistry()
    registry.register(
        MessageHarnessAdapter(service=services.message_service, granted_permissions=permissions)
    )
    notifier = EmailApprovalNotifier(
        services.message_service,
        os.environ["ATLAS_PUBLIC_URL"],
        TENANT,
    )

    results: list[dict] = []
    frames: list[dict] = []
    graph = _graph()
    (OUT / "graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

    def worker() -> None:
        try:
            results.append(
                run_graph(
                    parse_graph(graph),
                    inputs={"order_id": "T5-ORDER-1"},
                    registry=registry,
                    approval_broker=services.approval_broker,
                    approval_notifier=notifier,
                    frame_sink=frames.append,
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"exception": type(exc).__name__, "message": str(exc)})

    threading.Thread(target=worker, daemon=True).start()
    _wait_until(lambda: bool(services.approval_broker.list_pending()), label="pending approval")
    _wait_until(lambda: len(_CaptureSMTP.messages) >= 1, label="outbound email")

    pending = services.approval_broker.list_pending()[0]
    first_mail = _CaptureSMTP.messages[-1]
    match = re.search(r"https?://[^\s]+/approvals/([A-Za-z0-9._~-]+)", first_mail["body"])
    if not match:
        raise RuntimeError("email body has no /approvals/<token> link")
    signed = match.group(1)

    with TestClient(main.app) as client:
        view = client.get("/api/approvals/email-view", params={"token": signed})
        assert view.status_code == 200, view.text
        assert view.json()["status"] == "pending"
        bad = client.post(
            "/api/approvals/email-decision",
            json={"token": "not-a-signed-token", "decision": "approved"},
        )
        decision = client.post(
            "/api/approvals/email-decision",
            json={"token": signed, "decision": "approved", "comment": "T5 SMTP 深链"},
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["decision"] == "approved"

    _wait_until(lambda: len(_CaptureSMTP.messages) >= 2, label="result email")
    _wait_until(lambda: bool(results), label="graph result")
    result_mail = _CaptureSMTP.messages[-1]

    evidence = {
        "generatedAt": _now(),
        "mode": "real-local-smtp-approval-roundtrip",
        "smtp": {"host": "127.0.0.1", "port": smtp_port, "tls": False},
        "tenant": TENANT,
        "pending": pending,
        "frame": frames[0] if frames else None,
        "outboundEmail": first_mail,
        "emailView": view.json(),
        "badTokenStatus": bad.status_code,
        "decision": decision.json(),
        "decisionEmail": result_mail,
        "graphResult": results[0] if results else None,
        "messages": _CaptureSMTP.messages,
    }
    evidence["ok"] = (
        len(_CaptureSMTP.messages) >= 2
        and view.json()["status"] == "pending"
        and decision.status_code == 200
        and decision.json()["decision"] == "approved"
        and results
        and results[0].get("status") == "completed"
        and bad.status_code == 404
    )
    (OUT / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"ok": evidence["ok"], "smtpPort": smtp_port, "messages": len(_CaptureSMTP.messages)}, ensure_ascii=False))
    smtp.close()
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
