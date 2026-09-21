#!/usr/bin/env python3
"""持久化交付 smoke（docs/30 §7，U226）：HTTP 造数据 → 查库行数增量。

对运行中的 compose 全栈（默认 http://localhost:8000）执行：登录、
建图、跑出 run，经 db 容器内 psql 比对行数增量。不做删卷演练
（docs/30 §5.2 走 scripts/ops/backup.sh、restore.sh 人工执行）。

用法：python scripts/dev/p0_persistence_smoke.py [--base-url URL]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request


def _request(method: str, url: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request) as response:
        payload = response.read()
        return json.loads(payload) if payload else None


_GRAPH = {
    "version": 1,
    "variables": [
        {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}
    ],
    "nodes": [
        {"id": "trigger-1", "type": "trigger", "name": "t",
         "config": {"triggerType": "webhook", "webhookUrl": "/hooks/approval"}},
        {"id": "ai_decision-1", "type": "ai_decision", "name": "d",
         "config": {"promptTemplate": "auto"}},
        {"id": "tool_call-1", "type": "tool_call", "name": "tool",
         "config": {"tool": "web-playwright/click"}},
    ],
    "edges": [
        {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
        {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
    ],
}

_TABLES = ("graphs", "runs", "monitoring_runs", "iam_sessions")


def _counts() -> dict[str, int]:
    sql = " UNION ALL ".join(f"SELECT '{t}', count(*) FROM {t}" for t in _TABLES)
    output = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "atlas", "-d", "atlas", "-tAc", sql],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {name: int(value) for name, value in (line.split("|") for line in output.splitlines())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    before = _counts()

    login = _request(
        "POST",
        f"{args.base_url}/api/auth/login",
        body={"username": "admin-a", "password": "admin123"},
    )
    token = login["token"]

    graph_id = _request("POST", f"{args.base_url}/api/graphs", token, _GRAPH)["id"]
    _request(
        "POST",
        f"{args.base_url}/api/graphs/{graph_id}/run",
        token,
        {"inputs": {"approval_limit": "999"}},
    )

    after = _counts()
    expected = {"graphs": 1, "runs": 1, "monitoring_runs": 1, "iam_sessions": 1}
    failed = False
    for name, delta in expected.items():
        actual = after[name] - before[name]
        ok = actual == delta
        failed |= not ok
        print(f"{name}: +{actual} ({'ok' if ok else 'MISMATCH'})")

    print("smoke", "failed" if failed else "passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
