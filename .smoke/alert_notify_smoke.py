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
from atlas.monitoring.alerts import Alert
from atlas.monitoring.notify import AlertChannel, AlertNotifier
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

    # ② merged lifecycle notifies once (docs/52 §7 B1)；不重发 new 主通知
    Captured.requests.clear()
    _error_run(store)
    check("② merged alert sends one lifecycle, not a new alert",
          len(Captured.requests) == 1
          and Captured.requests[0]["body"]["text"]["content"]
          .startswith("[Atlas告警][再次发生已归并] run_error"),
          str([r["body"]["text"]["content"].splitlines()[0] for r in Captured.requests]))

    # ② 第 3 次失败触发 consecutive_failures 新规则（new 不限流）；同一 run_error 的重复
    #    merged 在 60s 窗口内已被限流（不得再出现 run_error 的归并通知）。
    Captured.requests.clear()
    _error_run(store)
    subs3 = [r["body"]["text"]["content"].splitlines()[0] for r in Captured.requests]
    check("② third failure opens consecutive_failures, run_error re-merge throttled",
          subs3 == ["[Atlas告警][critical] consecutive_failures"], str(subs3))

    # ② 第 4 次失败：consecutive_failures 首次 merged 发一条 lifecycle
    Captured.requests.clear()
    _error_run(store)
    subs4 = [r["body"]["text"]["content"].splitlines()[0] for r in Captured.requests]
    check("② consecutive_failures first merge notifies once",
          subs4 == ["[Atlas告警][再次发生已归并] consecutive_failures"], str(subs4))

    # ② lifecycle 限流退避（docs/54 §6）：注入可控单调时钟，同一 (alert, merged)
    #    首次投递、窗口内跳过、窗口外恢复；new 主通知不经此限流（notify 直发）。
    throttle_msgs = MessageService(
        im_sender=DefaultImSender(guard=EgressGuard(permit_cidrs=("127.0.0.0/8",))),
        webhook_sender=DefaultWebhookSender(guard=EgressGuard(permit_cidrs=("127.0.0.0/8",))),
    )
    clock = [0.0]
    throttle = AlertNotifier(
        throttle_msgs, lifecycle_min_interval_seconds=60, time_func=lambda: clock[0]
    )
    t_alert = Alert(
        id="alt-throttle", rule_id="run_error", graph_id="g1", severity="critical",
        message="m", first_seen="2026-09-23T00:00:00+00:00",
        last_seen="2026-09-23T00:00:00+00:00", last_run_id="r1",
    )
    t_cfg = AlertChannel(
        enabled=True, channel="dingtalk", to=f"{robot_url}/dingtalk",
        minSeverity="critical",
    )
    Captured.requests.clear()
    d1 = throttle.notify_lifecycle(t_alert, t_cfg, transition="merged")
    clock[0] = 30.0
    d2 = throttle.notify_lifecycle(t_alert, t_cfg, transition="merged")
    clock[0] = 61.0
    d3 = throttle.notify_lifecycle(t_alert, t_cfg, transition="merged")
    check("② lifecycle throttle: deliver, skip within 60s, deliver after window",
          bool(d1.lastNotifiedAt) and d2.lastNotifiedAt is None
          and bool(d3.lastNotifiedAt) and len(Captured.requests) == 2,
          f"d1={bool(d1.lastNotifiedAt)} d2={d2.lastNotifiedAt} "
          f"d3={bool(d3.lastNotifiedAt)} http={len(Captured.requests)}")

    # ②b recovery 隔离验证：新 store 仅单次失败（不触达 consecutive_failures 阈值），
    #    一次健康运行即自动恢复 open 的 run_error，旁路发一条 recovery（docs/54 §5）。
    rec_store = _new_store()
    rec_store.update_alert_channel(_channel_raw(f"{robot_url}/dingtalk"))
    _error_run(rec_store)
    Captured.requests.clear()
    rec_store.record_run(
        graph_id="g1", mode="sync", status="completed",
        started_at="2026-09-23T00:00:01+00:00", duration_ms=11,
        nodes=[NodeResult(node_id="n1", node_type="tool_call", status="success")],
    )
    rec_subs = [r["body"]["text"]["content"].splitlines()[0] for r in Captured.requests]
    check("②b healthy run sends one recovery lifecycle",
          rec_subs == ["[Atlas告警][告警已自动恢复] run_error"], str(rec_subs))

    # ②b 已 resolved，再一次健康运行不重复 recovery
    Captured.requests.clear()
    rec_store.record_run(
        graph_id="g1", mode="sync", status="completed",
        started_at="2026-09-23T00:00:02+00:00", duration_ms=9,
        nodes=[NodeResult(node_id="n1", node_type="tool_call", status="success")],
    )
    check("②b second healthy run does not repeat recovery",
          Captured.requests == [], f"{len(Captured.requests)}")

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
    check("⑤ rollout gate new notifies once, merge sends merged lifecycle",
          len(Captured.requests) == 2
          and Captured.requests[0]["body"]["text"]["content"]
          .startswith("[Atlas告警][critical] rollout_gate")
          and Captured.requests[1]["body"]["text"]["content"]
          .startswith("[Atlas告警][再次发生已归并]"),
          str([r["body"]["text"]["content"].splitlines()[0] for r in Captured.requests]))

    # ⑤ rollout_gate 不随健康运行自动恢复（docs/54 §5 边界）
    Captured.requests.clear()
    store.record_run(
        graph_id="g9", mode="sync", status="completed",
        started_at="2026-09-23T00:00:03+00:00", duration_ms=7,
        nodes=[NodeResult(node_id="n1", node_type="tool_call", status="success")],
    )
    check("⑤ rollout_gate not auto-recovered by healthy run",
          Captured.requests == [], f"{len(Captured.requests)}")

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
