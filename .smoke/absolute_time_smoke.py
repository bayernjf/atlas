"""D19 wait absolute time v1 HTTP smoke against live :8000 (docs/50 §8).

Run from repo root after starting uvicorn:
    .venv/bin/python .smoke/absolute_time_smoke.py
"""

from __future__ import annotations

import time as wall
from datetime import datetime, timedelta, timezone

import httpx

BASE = "http://127.0.0.1:8000"
UTC = timezone.utc

passed: list[str] = []


def check(name: str, condition: bool, evidence: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAILED: {name} {evidence}")
    passed.append(name)
    print(f"PASS  {name}  {evidence}")


def wait_graph(absolute_time: str | None = None) -> dict:
    config = {"waitType": "duration", "durationMode": "absolute",
              "absoluteTime": absolute_time or (
                  datetime.now(UTC) + timedelta(seconds=2)
              ).isoformat()}
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "到点等待",
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

    # ① ISO with offset: target now+2s at +08:00 representation
    target = datetime.now(UTC) + timedelta(seconds=2)
    target_cst = target.astimezone(timezone(timedelta(hours=8)))
    resp = create(wait_graph(target_cst.isoformat(timespec="seconds")))
    check("① save absolute graph", resp.status_code == 200, resp.text)
    gid = resp.json()["id"]
    started = wall.monotonic()
    run = client.post(f"/api/graphs/{gid}/run", json={})
    elapsed = wall.monotonic() - started
    body = run.json()
    out = body["outputs"]["wait-1"]
    expected_iso = target.astimezone(UTC).isoformat(timespec="seconds")
    check("① ISO offset waits ~2s",
          run.status_code == 200 and body["status"] == "completed"
          and out["durationSeconds"] in (1, 2)
          and out["durationMode"] == "absolute"
          and out["absoluteTime"] == expected_iso
          and "tool-after" in body["outputs"]
          and 0.8 < elapsed < 3.5,
          f"elapsed={elapsed:.2f} out={out}")

    # ② naive ISO treated as UTC
    target = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=3)
    resp = create(wait_graph(target.isoformat(timespec="seconds")))
    started = wall.monotonic()
    run = client.post(f"/api/graphs/{resp.json()['id']}/run", json={})
    elapsed = wall.monotonic() - started
    out = run.json()["outputs"]["wait-1"]
    check("② naive ISO as UTC waits ~3s",
          run.status_code == 200 and out["durationSeconds"] in (2, 3)
          and out["absoluteTime"].endswith("+00:00")
          and 1.8 < elapsed < 4.5,
          f"elapsed={elapsed:.2f} out={out['absoluteTime']}")

    # ③ Z suffix
    target = datetime.now(UTC) + timedelta(seconds=2)
    z_text = target.isoformat(timespec="seconds").replace("+00:00", "Z")
    resp = create(wait_graph(z_text))
    started = wall.monotonic()
    run = client.post(f"/api/graphs/{resp.json()['id']}/run", json={})
    elapsed = wall.monotonic() - started
    out = run.json()["outputs"]["wait-1"]
    check("③ Z suffix waits ~2s",
          run.status_code == 200 and out["durationSeconds"] in (1, 2)
          and out["absoluteTime"].endswith("+00:00")
          and 0.8 < elapsed < 3.5,
          f"elapsed={elapsed:.2f}")

    # ④ epoch seconds string
    epoch_value = str(int(wall.time()) + 4)
    resp = create(wait_graph(epoch_value))
    started = wall.monotonic()
    run = client.post(f"/api/graphs/{resp.json()['id']}/run", json={})
    elapsed = wall.monotonic() - started
    out = run.json()["outputs"]["wait-1"]
    check("④ epoch string normalized to ISO, waits ~4s",
          run.status_code == 200 and out["durationSeconds"] in (3, 4)
          and out["absoluteTime"].endswith("+00:00")
          and 2.8 < elapsed < 5.5,
          f"elapsed={elapsed:.2f} absoluteTime={out['absoluteTime']}")

    # ⑤ {{}} interpolation
    target = datetime.now(UTC) + timedelta(seconds=2)
    resp = create(wait_graph("{{global.targetAt}}"))
    check("⑤ save interpolation graph", resp.status_code == 200, resp.text)
    started = wall.monotonic()
    run = client.post(
        f"/api/graphs/{resp.json()['id']}/run",
        json={"inputs": {"targetAt": target.isoformat(timespec="seconds")}},
    )
    elapsed = wall.monotonic() - started
    out = run.json()["outputs"]["wait-1"]
    check("⑤ interpolated absoluteTime waits ~2s",
          run.status_code == 200 and out["durationSeconds"] in (1, 2)
          and out["absoluteTime"]
          == target.astimezone(UTC).isoformat(timespec="seconds")
          and 0.8 < elapsed < 3.5,
          f"elapsed={elapsed:.2f}")

    # ⑥ bad absolute times → 500, run failed WAIT_ABSOLUTE_TIME_INVALID, no sleep
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds")
    future = (datetime.now(UTC) + timedelta(seconds=602)).isoformat(
        timespec="seconds"
    )
    bad_cases = [
        ("bad iso", "not-a-time", {}),
        ("unknown variable", "{{global.missing}}", {}),
        ("past", past, {}),
        ("over 600s", future, {}),
    ]
    for label, absolute_time, inputs in bad_cases:
        resp = create(wait_graph(absolute_time))
        check(f"save graph for {label}", resp.status_code == 200, resp.text)
        gid_bad = resp.json()["id"]
        started = wall.monotonic()
        failed = client.post(f"/api/graphs/{gid_bad}/run",
                             json={"inputs": inputs})
        elapsed = wall.monotonic() - started
        runs = client.get("/api/runs").json()
        summary = next((r for r in runs["items"] if r["graphId"] == gid_bad), None)
        detail = client.get(f"/api/runs/{summary['runId']}").json() if summary else {}
        check(f"⑥ {label} failed WAIT_ABSOLUTE_TIME_INVALID without sleep",
              failed.status_code == 500 and elapsed < 1.0
              and detail.get("status") == "failed"
              and "WAIT_ABSOLUTE_TIME_INVALID" in (detail.get("error") or ""),
              f"http={failed.status_code} elapsed={elapsed:.2f} "
              f"error={(detail.get('error') or '')[:100]}")

    # ⑦ static regression
    resp = create(static_graph())
    check("⑦ save static graph", resp.status_code == 200, resp.text)
    run = client.post(f"/api/graphs/{resp.json()['id']}/run", json={})
    body = run.json()
    out = body["outputs"]["wait-1"]
    check("⑦ static output shape unchanged",
          run.status_code == 200 and body["status"] == "completed"
          and out == {"mode": "wait", "waitType": "duration",
                      "durationSeconds": 1},
          str(out))

    # ⑧ DSL 422
    for label, mutate in [
        ("absoluteTime empty",
         lambda g: g["nodes"][1]["config"].__setitem__("absoluteTime", "")),
        ("absoluteTime overlong",
         lambda g: g["nodes"][1]["config"].__setitem__("absoluteTime", "x" * 65)),
        ("durationMode invalid",
         lambda g: g["nodes"][1]["config"].__setitem__("durationMode", "soon")),
    ]:
        payload = wait_graph()
        mutate(payload)
        resp = create(payload)
        check(f"⑧ 422 {label}", resp.status_code == 422,
              f"http={resp.status_code} {resp.text[:120]}")

    print(f"\nALL {len(passed)} SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
