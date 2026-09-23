"""D19 wait dynamic duration v1 HTTP smoke against live :8000 (docs/49 §8).

Run from repo root after starting uvicorn:
    .venv/bin/python .smoke/dynamic_wait_smoke.py
"""

from __future__ import annotations

import time as wall

import httpx

BASE = "http://127.0.0.1:8000"

passed: list[str] = []


def check(name: str, condition: bool, evidence: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAILED: {name} {evidence}")
    passed.append(name)
    print(f"PASS  {name}  {evidence}")


def wait_graph(expression: str | None = None) -> dict:
    config = {"waitType": "duration", "durationMode": "dynamic",
              "durationExpression": expression or "{{global.waitSecs}}"}
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "动态等待",
             "position": {"x": 2, "y": 0}, "config": config},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "op-after"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "wait-1"},
            {"id": "e2", "source": "wait-1", "target": "tool-after"},
        ],
    }


def static_graph() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "等待",
             "config": {"waitType": "duration", "durationSeconds": 1}},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "op-after"}},
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
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    client.post("/api/demo/reset")

    def create(payload: dict) -> httpx.Response:
        return client.post("/api/graphs", json=payload)

    # ① dynamic variable: waitSecs=2 → ~2s, durationSeconds=2
    resp = create(wait_graph())
    check("① save dynamic graph", resp.status_code == 200, resp.text)
    gid = resp.json()["id"]
    started = wall.monotonic()
    run = client.post(f"/api/graphs/{gid}/run",
                      json={"inputs": {"waitSecs": 2}})
    elapsed = wall.monotonic() - started
    body = run.json()
    out = body["outputs"]["wait-1"]
    check("① dynamic eval waits ~2s",
          run.status_code == 200 and body["status"] == "completed"
          and out["durationSeconds"] == 2
          and out["durationMode"] == "dynamic"
          and out["durationExpression"] == "{{global.waitSecs}}"
          and "tool-after" in body["outputs"]
          and 1.8 < elapsed < 3.5,
          f"elapsed={elapsed:.2f} out={out}")

    # ② arithmetic expression
    resp = create(wait_graph("{{global.waitSecs}} * 2 + 1"))
    started = wall.monotonic()
    run = client.post(f"/api/graphs/{resp.json()['id']}/run",
                      json={"inputs": {"waitSecs": 2}})
    elapsed = wall.monotonic() - started
    out = run.json()["outputs"]["wait-1"]
    check("② arithmetic eval = 5s",
          run.status_code == 200 and out["durationSeconds"] == 5
          and 4.8 < elapsed < 6.5,
          f"elapsed={elapsed:.2f} seconds={out['durationSeconds']}")

    # ③④ bad expression / illegal result → 500, run failed WAIT_DURATION_INVALID, no sleep
    bad_expressions = [
        ("missing variable", "{{global.missing}}", {}),
        ("bad syntax", "1 + ", {}),
        ("zero", "0", {}),
        ("over max", "3601", {}),  # docs/54: duration 上限 600->3600，越界用例同步
        ("non-numeric", "'soon'", {}),
        ("division by zero", "1/0", {}),
    ]
    for label, expression, inputs in bad_expressions:
        resp = create(wait_graph(expression))
        check(f"save graph for {label}", resp.status_code == 200, resp.text)
        gid_bad = resp.json()["id"]
        started = wall.monotonic()
        failed = client.post(f"/api/graphs/{gid_bad}/run",
                             json={"inputs": inputs})
        elapsed = wall.monotonic() - started
        runs = client.get("/api/runs").json()
        summary = next((r for r in runs["items"] if r["graphId"] == gid_bad), None)
        detail = client.get(f"/api/runs/{summary['runId']}").json() if summary else {}
        check(f"③④ {label} failed WAIT_DURATION_INVALID without sleep",
              failed.status_code == 500 and elapsed < 1.0
              and detail.get("status") == "failed"
              and "WAIT_DURATION_INVALID" in (detail.get("error") or ""),
              f"http={failed.status_code} elapsed={elapsed:.2f} "
              f"error={(detail.get('error') or '')[:100]}")

    # ⑤ static zero regression
    resp = create(static_graph())
    check("⑤ save static graph", resp.status_code == 200, resp.text)
    run = client.post(f"/api/graphs/{resp.json()['id']}/run", json={})
    body = run.json()
    out = body["outputs"]["wait-1"]
    check("⑤ static output shape unchanged",
          run.status_code == 200 and body["status"] == "completed"
          and out == {"mode": "wait", "waitType": "duration",
                      "durationSeconds": 1},
          str(out))

    # ⑥ DSL 422
    for label, mutate in [
        ("expression empty", lambda g: g["nodes"][1]["config"].__setitem__("durationExpression", "")),
        ("expression overlong", lambda g: g["nodes"][1]["config"].__setitem__("durationExpression", "x" * 201)),
        ("durationMode invalid", lambda g: g["nodes"][1]["config"].__setitem__("durationMode", "soon")),
    ]:
        payload = wait_graph()
        mutate(payload)
        resp = create(payload)
        check(f"⑥ 422 {label}", resp.status_code == 422,
              f"http={resp.status_code} {resp.text[:120]}")

    print(f"\nALL {len(passed)} SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
