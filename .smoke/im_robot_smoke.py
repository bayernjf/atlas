"""D24 IM group robot v1 smoke (docs/51 §7).

Two layers:
  A. Real HTTP loopback against a local fake robot server (fake platform endpoints
     capture URL signing + request envelopes); sender uses the code-injected
     permit_cidrs guard, fixed clock — no network, byte-exact signatures.
  B. Live platform API on :8000 (admin-a): production guard blocks loopback
     (EGRESS_DENIED), parameter validation (INVALID_PARAMETER), failures leave no
     message record, email/webhook/unknown channels zero regression.

Run from repo root after starting uvicorn:
    .venv/bin/python .smoke/im_robot_smoke.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_lib
import json
from urllib.parse import quote
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from atlas.message.im import DefaultImSender, ImDeliveryError
from atlas.message.service import MessageSendError, MessageService
from atlas.security.egress import EgressGuard

BASE = "http://127.0.0.1:8000"
ROBOT_PORT = 9911
FIXED_TS = 1700000000
SECRET = "SEC"

passed: list[str] = []


def check(name: str, condition: bool, evidence: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAILED: {name} {evidence}")
    passed.append(name)
    print(f"PASS  {name}  {evidence}")


class Captured:
    requests: list[dict] = []


class FakeRobotHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode())
        except Exception:
            payload = None
        Captured.requests.append(
            {"path": self.path, "query": self.path.split("?", 1)[1] if "?" in self.path else "",
             "body": payload}
        )
        if self.path.startswith("/non2xx"):
            self.send_response(500)
            self.end_headers()
            return
        if self.path.startswith("/dingtalk"):
            ok = self.path.startswith("/dingtalk-ok")
            response = ({"errcode": 0, "errmsg": "ok"} if ok
                        else {"errcode": 310000, "errmsg": "sign not match"})
        elif self.path.startswith("/wecom"):
            response = {"errcode": 0, "errmsg": "ok"}
        else:  # feishu
            ok = self.path.startswith("/feishu-ok")
            response = ({"code": 0, "msg": "success"} if ok
                        else {"code": 19021, "msg": "sign match fail"})
        data = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def expected_dingtalk_sign(ts_ms: int, secret: str) -> str:
    return base64.b64encode(
        hmac_lib.new(secret.encode(), f"{ts_ms}\n{secret}".encode(), hashlib.sha256).digest()
    ).decode()


def expected_feishu_sign(ts: int, secret: str) -> str:
    return base64.b64encode(
        hmac_lib.new(b"", f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
    ).decode()


def message_graph(channel: str, to, secret=None) -> dict:
    params: dict = {"channel": channel, "to": to, "subject": "通知标题", "body": "通知正文"}
    if secret is not None:
        params["secret"] = secret
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "msg-1", "type": "tool_call", "name": "发消息",
             "position": {"x": 2, "y": 0}, "config": {"tool": "message/send", "params": json.dumps(params)}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "msg-1"}],
    }


def main() -> None:
    server = HTTPServer(("127.0.0.1", ROBOT_PORT), FakeRobotHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # ============================ Layer A：真实 HTTP 协议冒烟 ============================
    sender = DefaultImSender(
        guard=EgressGuard(permit_cidrs=("127.0.0.0/8",)),
        clock=lambda: FIXED_TS,
    )
    url = f"http://127.0.0.1:{ROBOT_PORT}"

    # ① dingtalk signed
    Captured.requests.clear()
    sender.send("dingtalk", f"{url}/dingtalk-ok?access_token=xxx", "通知标题", "通知正文", SECRET)
    req = Captured.requests[-1]
    check("① dingtalk signed URL",
          f"timestamp={FIXED_TS * 1000}" in req["query"]
          and f"sign={quote(expected_dingtalk_sign(FIXED_TS * 1000, SECRET))}" in req["query"],
          req["query"])
    check("① dingtalk text envelope",
          req["body"] == {
              "msgtype": "text",
              "text": {"content": "通知标题\n通知正文"},
              "at": {"atMobiles": [], "atUserIds": [], "isAtAll": False},
          },
          str(req["body"]))

    # ② wecom unsigned
    Captured.requests.clear()
    sender.send("wecom", f"{url}/wecom-ok?key=xxx", "hi", "body", None)
    req = Captured.requests[-1]
    check("② wecom unsigned URL + text envelope",
          req["query"] == "key=xxx"
          and req["body"] == {
              "msgtype": "text",
              "text": {"content": "hi\nbody", "mentioned_list": [],
                       "mentioned_mobile_list": []},
          },
          str(req))

    # ③ feishu signed body
    Captured.requests.clear()
    sender.send("feishu", f"{url}/feishu-ok?hook=x", "hi", "body", SECRET)
    req = Captured.requests[-1]
    body = req["body"]
    check("③ feishu signed body envelope",
          req["query"] == "hook=x"
          and body["timestamp"] == str(FIXED_TS)
          and body["sign"] == expected_feishu_sign(FIXED_TS, SECRET)
          and body["msg_type"] == "text"
          and body["content"] == {"text": "hi\nbody"},
          str(body))

    # ④ dingtalk unsigned when no secret
    Captured.requests.clear()
    sender.send("dingtalk", f"{url}/dingtalk-ok?access_token=xxx", "hi", "body", None)
    req = Captured.requests[-1]
    check("④ dingtalk unsigned without secret",
          req["query"] == "access_token=xxx", req["query"])

    # ⑤ platform error codes / non-2xx
    try:
        sender.send("dingtalk", f"{url}/dingtalk-fail", "hi", "body", None)
        raise AssertionError("expected ImDeliveryError dingtalk")
    except ImDeliveryError as exc:
        check("⑤ dingtalk errcode!=0 fails", "310000" in str(exc), str(exc))
    try:
        sender.send("feishu", f"{url}/feishu-fail", "hi", "body", None)
        raise AssertionError("expected ImDeliveryError feishu")
    except ImDeliveryError as exc:
        check("⑤ feishu code!=0 fails", "19021" in str(exc), str(exc))
    try:
        sender.send("wecom", f"{url}/non2xx", "hi", "body", None)
        raise AssertionError("expected ImDeliveryError non2xx")
    except ImDeliveryError as exc:
        check("⑤ non-2xx fails", "非 2xx" in str(exc), str(exc))

    # ⑤b docs/58 markdown + @人 envelopes（三家，假服务器逐字节捕获）
    Captured.requests.clear()
    sender.send("dingtalk", f"{url}/dingtalk-ok?access_token=x", "标题", "**正文**", None,
                msg_format="markdown", mentions={"userIds": ["uid1"], "mobiles": ["13800000000"]})
    body = Captured.requests[-1]["body"]
    check("⑤b dingtalk markdown+at",
          body["msgtype"] == "markdown" and body["markdown"]["title"] == "标题"
          and body["markdown"]["text"].endswith("@13800000000 @uid1")
          and body["at"] == {"atMobiles": ["13800000000"], "atUserIds": ["uid1"],
                             "isAtAll": False},
          str(body))
    Captured.requests.clear()
    sender.send("wecom", f"{url}/wecom-ok?key=x", "标题", "**正文**", None,
                msg_format="markdown", mentions={"userIds": ["zhangsan"], "atAll": True})
    body = Captured.requests[-1]["body"]
    check("⑤b wecom markdown inline at tags",
          body == {"msgtype": "markdown",
                   "markdown": {"content": "**正文** <@zhangsan> <@all>"}},
          str(body))
    Captured.requests.clear()
    sender.send("feishu", f"{url}/feishu-ok?hook=x", "标题", "行1\n行2", None,
                msg_format="markdown", mentions={"userIds": ["ou_a"]})
    body = Captured.requests[-1]["body"]
    check("⑤b feishu post rich text + at paragraph",
          body["msg_type"] == "post"
          and body["content"]["post"]["zh_cn"] == {
              "title": "标题",
              "content": [[{"tag": "text", "text": "行1"}],
                          [{"tag": "text", "text": "行2"}],
                          [{"tag": "at", "user_id": "ou_a"}]]},
          str(body))

    # ⑤c docs/58 多 URL fan-out（MessageService 注入 permit-guard sender，零等待退避）
    fan = MessageService(im_sender=sender, retry_delays=(0, 0), sleep_func=lambda _s: None)
    Captured.requests.clear()
    rec = fan.send("dingtalk",
                   [f"{url}/dingtalk-ok?access_token=a", f"{url}/dingtalk-ok?access_token=b"],
                   "群标题", "群正文")
    check("⑤c fan-out all succeed one record",
          rec["delivered"] == "dingtalk" and fan.count == 1
          and len(Captured.requests) == 2
          and Captured.requests[0]["query"].startswith("access_token=a")
          and Captured.requests[1]["query"].startswith("access_token=b"),
          f"{rec['delivered']} {fan.count} {len(Captured.requests)}")
    # 注：dingtalk 信封不含 id（平台消息体无该字段），幂等 id 用于 webhook payload
    deliveries = fan.list_deliveries()
    check("⑤c fan-out per-URL delivery records",
          len(deliveries) == 2 and all(d["status"] == "delivered:dingtalk" for d in deliveries)
          and {tuple(d["to"]) for d in deliveries} == {
              (f"{url}/dingtalk-ok?access_token=a",), (f"{url}/dingtalk-ok?access_token=b",)},
          str([(d["to"], d["status"]) for d in deliveries]))
    # 半败 best-effort：ok 与 fail 都尝试（fail 单目标重试 3 次），聚合抛错、不写消息
    Captured.requests.clear()
    try:
        fan.send("dingtalk", [f"{url}/dingtalk-ok?access_token=ok", f"{url}/dingtalk-fail"],
                 "s", "b")
        raise AssertionError("expected aggregated IM_SEND_FAILED")
    except MessageSendError as exc:
        fail_hits = sum(1 for r in Captured.requests if r["path"].startswith("/dingtalk-fail"))
        ok_hits = sum(1 for r in Captured.requests if "access_token=ok" in r["query"])
        check("⑤c fan-out partial failure best-effort + aggregate",
              exc.code == "IM_SEND_FAILED" and "1/2" in str(exc)
              and ok_hits == 1 and fail_hits == 3 and fan.count == 1,  # 上一条成功记录仍在
              f"{exc.code} {exc} ok={ok_hits} fail={fail_hits}")
    # EGRESS fail-fast：生产 guard（默认拦截 loopback）遇拦截即停，不投第二个 URL
    prod_sender = DefaultImSender(clock=lambda: FIXED_TS)  # 默认 EgressGuard 拦 loopback
    prod_fan = MessageService(im_sender=prod_sender, retry_delays=(0, 0), sleep_func=lambda _s: None)
    Captured.requests.clear()
    try:
        prod_fan.send("wecom",
                      [f"{url}/wecom-ok?key=first", f"{url}/wecom-ok?key=second"], "s", "b")
        raise AssertionError("expected EGRESS_DENIED")
    except MessageSendError as exc:
        check("⑤c fan-out egress fail-fast",
              exc.code == "EGRESS_DENIED" and len(Captured.requests) == 0,
              f"{exc.code} requests={len(Captured.requests)}")

    # ============================ Layer B：平台 API (:8000) ============================
    client = httpx.Client(base_url=BASE, timeout=30)
    login = client.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    check("login admin-a", login.status_code == 200)
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    client.post("/api/demo/reset")

    def run_graph(payload: dict):
        created = client.post("/api/graphs", json=payload)
        check("save graph", created.status_code == 200, created.text)
        return client.post(f"/api/graphs/{created.json()['id']}/run", json={})

    # ⑥ production guard blocks loopback: EGRESS_DENIED, no record
    run = run_graph(message_graph("dingtalk", f"{url}/dingtalk-ok"))
    body = run.json()
    msg_out = body["outputs"]["msg-1"]
    error_code = (msg_out.get("result") or {}).get("code")
    check("⑥ loopback EGRESS_DENIED no record",
          body["status"] == "completed" and error_code == "EGRESS_DENIED",
          f"{run.status_code} {body.get('status')} {error_code}")
    messages = client.get("/api/demo/messages")
    check("⑥ no message record on denial", messages.json().get("items") == [], messages.text)

    # ⑦ docs/58 array fan-out: production guard blocks loopback fail-fast (EGRESS_DENIED)
    run = run_graph(message_graph("wecom", [f"{url}/wecom-ok", f"{url}/wecom-ok?two"]))
    error_code = (run.json()["outputs"]["msg-1"].get("result") or {}).get("code")
    check("⑦ array fan-out loopback EGRESS_DENIED fail-fast",
          error_code == "EGRESS_DENIED", error_code)

    # ⑧ secret on unsupported channel INVALID_PARAMETER
    run = run_graph(message_graph("wecom", f"{url}/wecom-ok", secret=SECRET))
    error_code = (run.json()["outputs"]["msg-1"].get("result") or {}).get("code")
    check("⑧ secret unsupported channel INVALID_PARAMETER",
          error_code == "INVALID_PARAMETER", error_code)

    # ⑨ email / unknown channel zero regression (in_process without SMTP)
    run = run_graph(message_graph("email", ["ops@example.com"]))
    delivered = run.json()["outputs"]["msg-1"].get("result", {}).get("delivered")
    check("⑨ email in_process regression",
          run.json()["status"] == "completed" and delivered == "in_process", delivered)
    run = run_graph(message_graph("sms", "10086"))
    delivered = run.json()["outputs"]["msg-1"].get("result", {}).get("delivered")
    check("⑨ unknown channel in_process regression",
          run.json()["status"] == "completed" and delivered == "in_process", delivered)

    server.shutdown()
    print(f"\nALL PASS: {len(passed)} checks")


if __name__ == "__main__":
    main()
