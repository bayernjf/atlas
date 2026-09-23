"""D28 x D24 alert external notification v1 smoke (docs/52 §6).

Two layers:
  A. In-process MonitoringStore + MessageService with loopback-permitted
     senders against a local fake robot: fixed text, new-alert-only trigger,
     severity filter, fail-safe delivery status, rollout gate, webhook, email.
  B. Live platform API on :8000 (admin-a): configure alert channel to a
     loopback URL, create a node_failed warning via the sql-query-notify
     template stream, production guard denies egress (EGRESS_DENIED), GET
     alert-channel shows the last delivery error; validation / viewer 403.

Run from repo root after starting uvicorn:
    .venv/bin/python .smoke/alert_notify_smoke.py
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from atlas.message.im import DefaultImSender
from atlas.message.service import MessageService
from atlas.message.webhook import DefaultWebhookSender
from atlas.monitoring.metrics import NodeResult
from atlas.monitoring.records import MonitoringStore
from atlas.monitoring.notify import AlertNotifier
from atlas.security.egress import EgressGuard

BASE = "http://127.0.0.1:8000"
ROBOT_PORT = 9912

passed: list[str] = []


def check(name: str, condition: bool, evidence: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAILED: {name} {evidence}")
    passed.append(name)
    print(f"PASS  {name}  {evidence}")


class Captured:
    requests: list[dict] = []


class FakeRobotHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode())
        except Exception:
            payload = raw.decode()
        Captured.requests.append({"path": self.path, "body": payload})
        if self.path.startswith("/fail"):
            self.send_response(500)
            self.end_headers()
            return
        data = json.dumps({"errcode": 0, "errmsg": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _channel_raw(url: str, **overrides) -> dict:
    raw = {
        "enabled": True,
        "channel": "dingtalk",
        "to": url,
        "secret": "",
        "minSeverity": "critical",
    }
    raw.update(overrides)
    return raw


def _error_run(store: MonitoringStore, graph_id: str = "g1"):
    return store.record_run(
        graph_id=graph_id,
        mode="sync",
        status="error",
        started_at="2026-09-23T00:00:00+00:00",
        duration_ms=42,
        nodes=[NodeResult(node_id="n1", node_type="tool_call", status="failed", error="boom")],
        error="run failed",
    )


def _new_store() -> MonitoringStore:
    guard = EgressGuard(permit_cidrs=("127.0.0.0/8",))
    messages = MessageService(
        im_sender=DefaultImSender(guard=guard),
        webhook_sender=DefaultWebhookSender(guard=guard),
    )
    store = MonitoringStore()
    store.set_notifier(AlertNotifier(messages))
    return store


def layer_a(robot_url: str) -> None:
    # ① dingtalk fake robot receives fixed text after failed run
    Captured.requests.clear()
    store = _new_store()
    store.update_alert_channel(_channel_raw(f"{robot_url}/dingtalk"))
    _error_run(store)
    check("① exactly one delivery on failed run", len(Captured.requests) == 1, str(len(Captured.requests)))
    body = Captured.requests[0]["body"]
    content = body["text"]["content"]
    check("① fixed subject + body fields",
          content.startswith("[Atlas告警][critical] run_error")
          and "图：g1" in content and "级别：critical" in content
          and "值班：未指派" in content and "首次：" in content,
          content)

    # ② critical-only suppresses warning; merged alert no repeat
    Captured.requests.clear()
    _error_run(store)
    check("② merged alert does not resend", Captured.requests == [], f"{len(Captured.requests)}")

    # ③ disabled / unconfigured zero delivery
    Captured.requests.clear()
    store = _new_store()
    _error_run(store)
    check("③ unconfigured zero delivery", Captured.requests == [])
    store.update_alert_channel(_channel_raw(f"{robot_url}/dingtalk", enabled=False, to=""))
    Captured.requests.clear()
    _error_run(store)
    check("③ disabled zero delivery", Captured.requests == [])

    # ④ bad robot fail-safe: run recorded, delivery shows error
    store = _new_store()
    store.update_alert_channel(_channel_raw(f"{robot_url}/fail"))
    record = _error_run(store)
    delivery = store.get_alert_channel_delivery()
    check("④ fail-safe after robot 500",
          record.status == "error" and delivery.lastNotifiedAt
          and delivery.errorCode in ("IM_SEND_FAILED", "WEBHOOK_SEND_FAILED"),
          f"{delivery.errorCode} {delivery.errorMessage}")

    # ⑤ rollout_gate critical notifies once, merges stay silent
    store = _new_store()
    store.update_alert_channel(_channel_raw(f"{robot_url}/dingtalk"))
    Captured.requests.clear()
    store.raise_rollout_gate_alert(
        graph_id="g9", message="gate breached", action={"kind": "auto_rollback"}
    )
    store.raise_rollout_gate_alert(
        graph_id="g9", message="gate breached", action={"kind": "auto_rollback"}
    )
    check("⑤ rollout gate notifies only when new",
          len(Captured.requests) == 1
          and Captured.requests[0]["body"]["text"]["content"].startswith("[Atlas告警][critical] rollout_gate"),
          str(len(Captured.requests)))

    # ⑥ webhook once with fixed text
    store = _new_store()
    store.update_alert_channel(
        _channel_raw(f"{robot_url}/webhook", channel="webhook")
    )
    Captured.requests.clear()
    _error_run(store)
    check("⑥ webhook receives fixed text payload",
          len(Captured.requests) == 1
          and Captured.requests[0]["body"]["subject"] == "[Atlas告警][critical] run_error"
          and "图：g1" in Captured.requests[0]["body"]["body"],
          str(Captured.requests[0]["body"])[:80])

    # ⑦ email without SMTP is in_process but still a successful delivery attempt
    store = _new_store()
    store.update_alert_channel(
        _channel_raw("ops@example.com", channel="email")
    )
    _error_run(store)
    delivery = store.get_alert_channel_delivery()
    check("⑦ email delivery recorded without error",
          delivery.lastNotifiedAt and not delivery.errorCode,
          f"{delivery.errorCode} {delivery.errorMessage}")


def layer_b(robot_url: str) -> None:
    client = httpx.Client(base_url=BASE, timeout=30)
    login = client.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    check("login admin-a", login.status_code == 200)
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    client.post("/api/demo/reset")

    # warning channel to loopback: node_failed attempt is denied by production guard
    put = client.put(
        "/api/monitoring/alert-channel",
        json=_channel_raw(f"{robot_url}/dingtalk", minSeverity="warning"),
    )
    check("PUT alert-channel 200", put.status_code == 200, put.text)

    graph = client.get("/api/templates/sql-query-notify").json()["graph"]
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    with client.stream(
        "POST", f"/api/graphs/{graph_id}/run/stream", json={"inputs": {}}
    ) as response:
        check("stream run 200", response.status_code == 200)
        for line in response.iter_lines():
            pass

    payload = client.get("/api/monitoring/alert-channel").json()
    delivery = payload["lastDelivery"]
    check("GET shows EGRESS_DENIED last delivery",
          delivery is not None and delivery["errorCode"] == "EGRESS_DENIED",
          str(delivery))

    # invalid config aggregates Chinese 422
    bad = client.put(
        "/api/monitoring/alert-channel", json=_channel_raw("not-a-url")
    )
    check("invalid PUT 422 Chinese", bad.status_code == 422 and "URL" in bad.json()["detail"], bad.text)

    # viewer cannot administer
    viewer_login = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    forbidden = client.put(
        "/api/monitoring/alert-channel",
        json=_channel_raw(f"{robot_url}/dingtalk"),
        headers={"Authorization": f"Bearer {viewer_login}"},
    )
    check("viewer PUT 403", forbidden.status_code == 403, forbidden.text)


def main() -> None:
    server = HTTPServer(("127.0.0.1", ROBOT_PORT), FakeRobotHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    robot_url = f"http://127.0.0.1:{ROBOT_PORT}"

    layer_a(robot_url)
    layer_b(robot_url)

    server.shutdown()
    print(f"\nALL PASS: {len(passed)} checks")


if __name__ == "__main__":
    main()
