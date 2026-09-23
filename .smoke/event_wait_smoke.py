"""D19 wait event v1 HTTP smoke against live :8000 (docs/47 §8).

Run from repo root after starting uvicorn:
    .venv/bin/python .smoke/event_wait_smoke.py
"""

from __future__ import annotations

import threading
import time

import httpx

BASE = "http://127.0.0.1:8000"

passed: list[str] = []


def check(name: str, condition: bool, evidence: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAILED: {name} {evidence}")
    passed.append(name)
    print(f"PASS  {name}  {evidence}")


def graph(event_key: str, timeout: int = 30, on_timeout: str = "continue") -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "等待",
             "position": {"x": 2, "y": 0},
             "config": {"waitType": "event", "eventKey": event_key,
                        "timeoutSeconds": timeout, "onTimeout": on_timeout}},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "position": {"x": 3, "y": 0},
             "config": {"tool": "web-playwright/click"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "wait-1"},
            {"id": "e2", "source": "wait-1", "target": "tool-after"},
        ],
    }


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=30)

    login = client.post("/api/auth/login",
                        json={"username": "admin-a", "password": "admin123"})
    check("login admin-a", login.status_code == 200)
    token = login.json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"

    client.post("/api/demo/reset")

    def create(payload: dict) -> str:
        resp = client.post("/api/graphs", json=payload)
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    def wait_for_pending(event_key: str) -> dict:
        for _ in range(100):
            items = client.get("/api/waits").json()["items"]
            for item in items:
                if item["eventKey"] == event_key:
                    return item
            time.sleep(0.1)
        raise AssertionError(f"pending wait for {event_key} never appeared")

    # ① signal by event key with payload propagation
    gid = create(graph("smoke_paid_a", timeout=30))
    run_box: dict = {}

    def do_run() -> None:
        run_box["resp"] = client.post(f"/api/graphs/{gid}/run", json={})

    worker = threading.Thread(target=do_run)
    worker.start()
    pending = wait_for_pending("smoke_paid_a")
    check("① wait visible in GET /api/waits", pending["token"].startswith("wait-"),
          str({k: pending[k] for k in ("token", "eventKey", "timeoutSeconds")}))
    signal = client.post("/api/waits/events",
                         json={"eventKey": "smoke_paid_a",
                               "payload": {"paidAt": "2026-09-23", "amount": 199}})
    check("① signal event releases 1", signal.status_code == 200 and signal.json()["released"] == 1)
    worker.join(30)
    body = run_box["resp"].json()
    out = body["outputs"]["wait-1"]
    check("① payload propagates, resolvedBy signal",
          run_box["resp"].status_code == 200
          and out["signaled"] is True
          and out["resolvedBy"] == "signal"
          # docs/54：信号 payload 注入命中键 matchedEventKey（单键也带，契约增强）。
          and out["payload"] == {"paidAt": "2026-09-23", "amount": 199,
                                 "matchedEventKey": "smoke_paid_a"}
          and out.get("matchedEventKey") == "smoke_paid_a"
          and "tool-after" in body["outputs"],
          str({k: out[k] for k in ("signaled", "resolvedBy", "payload")}))
    check("① pending cleared", client.get("/api/waits").json()["items"] == [])

    # ② timeout continue
    gid = create(graph("smoke_timeout_continue", timeout=2, on_timeout="continue"))
    resp = client.post(f"/api/graphs/{gid}/run", json={})
    out = resp.json()["outputs"]["wait-1"]
    check("② timeout continue keeps graph completed",
          resp.status_code == 200
          and out["signaled"] is False
          and out["resolvedBy"] == "timeout"
          and "tool-after" in resp.json()["outputs"],
          str({k: out[k] for k in ("signaled", "resolvedBy", "waitedSeconds")}))

    # ③ timeout fail
    gid = create(graph("smoke_timeout_fail", timeout=2, on_timeout="fail"))
    resp = client.post(f"/api/graphs/{gid}/run", json={})
    check("③ timeout fail returns 500", resp.status_code == 500, f"http={resp.status_code}")
    runs = client.get("/api/runs").json()
    failed_summary = [r for r in runs["items"] if r["graphId"] == gid][0]
    failed = client.get(f"/api/runs/{failed_summary['runId']}").json()
    check("③ run failed with WAIT_TIMEOUT_FAILED", failed["status"] == "failed"
          and "WAIT_TIMEOUT_FAILED" in (failed.get("error") or ""),
          (failed.get("error") or "")[:120])

    # ④ token direct signal: 200 -> 409 -> unknown 404
    gid = create(graph("smoke_token_direct", timeout=30))
    run_box = {}
    worker = threading.Thread(
        target=lambda: run_box.update(resp=client.post(f"/api/graphs/{gid}/run", json={})))
    worker.start()
    pending = wait_for_pending("smoke_token_direct")
    wait_token = pending["token"]
    first = client.post(f"/api/waits/{wait_token}/signal", json={"payload": {"x": 1}})
    check("④ direct token signal 200", first.status_code == 200 and first.json()["released"] is True)
    repeat = client.post(f"/api/waits/{wait_token}/signal", json={})
    check("④ repeat signal 409 WAIT_ALREADY_SIGNALED",
          repeat.status_code == 409 and repeat.json()["detail"]["code"] == "WAIT_ALREADY_SIGNALED")
    worker.join(30)
    missing = client.post("/api/waits/wait-deadbeef/signal", json={})
    check("④ unknown token 404 WAIT_TOKEN_NOT_FOUND",
          missing.status_code == 404 and missing.json()["detail"]["code"] == "WAIT_TOKEN_NOT_FOUND")

    # ⑤ waitEvents preset
    gid = create(graph("smoke_preset", timeout=30))
    resp = client.post(f"/api/graphs/{gid}/run",
                       json={"inputs": {"waitEvents": {"wait-1": {"via": "preset"}}}})
    out = resp.json()["outputs"]["wait-1"]
    check("⑤ preset resolves without waiting",
          resp.status_code == 200 and out["resolvedBy"] == "input"
          and out["payload"] == {"via": "preset"},
          str({k: out[k] for k in ("resolvedBy", "payload")}))

    # ⑥ cancel while waiting (cooperative cancellation is wired on stream runs)
    gid = create(graph("smoke_cancel", timeout=60))
    frames: list[str] = []

    def do_stream() -> None:
        with client.stream("POST", f"/api/graphs/{gid}/run/stream", json={}) as resp:
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    frames.append(line.removeprefix("event: "))

    worker = threading.Thread(target=do_stream)
    worker.start()
    wait_for_pending("smoke_cancel")
    run_id = None
    for _ in range(100):
        items = client.get("/api/runs").json()["items"]
        # docs/53：event wait 挂起即落中断帧，run 在所有后端都置为 suspended（非 running）；
        # run 已在 broker 挂起（wait_for_pending 已通过），suspended 的活线程仍可协作取消。
        candidates = [r for r in items
                      if r["graphId"] == gid and r["status"] in ("running", "suspended")]
        if candidates:
            run_id = candidates[0]["runId"]
            break
        time.sleep(0.1)
    check("⑥ running stream run listed", run_id is not None, run_id or "")
    cancel = client.post(f"/api/runs/{run_id}/cancel")
    check("⑥ cancel returns 200", cancel.status_code == 200, cancel.text)
    worker.join(30)
    time.sleep(0.3)
    check("⑥ SSE cancelled frame emitted", "cancelled" in frames, str(frames))
    check("⑥ pending wait removed on cancel",
          all(i["eventKey"] != "smoke_cancel"
              for i in client.get("/api/waits").json()["items"]))

    # ⑦ invalid rendered eventKey fails without registering
    gid = create(graph("k_{{trigger-1.context.payload.bad}}", timeout=30))
    resp = client.post(f"/api/graphs/{gid}/run", json={"inputs": {"bad": "has space"}})
    check("⑦ rendered invalid key returns 500", resp.status_code == 500)
    failed_summary = [r for r in client.get("/api/runs").json()["items"]
                      if r["graphId"] == gid][0]
    failed = client.get(f"/api/runs/{failed_summary['runId']}").json()
    check("⑦ run failed with WAIT_EVENT_KEY_INVALID",
          failed["status"] == "failed" and "WAIT_EVENT_KEY_INVALID" in (failed.get("error") or ""),
          (failed.get("error") or "")[:120])
    check("⑦ no pending registered",
          all(i["eventKey"] != "k_has space"
              for i in client.get("/api/waits").json()["items"]))

    print(f"\nALL {len(passed)} SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
