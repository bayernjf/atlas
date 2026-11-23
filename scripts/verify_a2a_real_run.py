"""A-02 证据：3 个 A2A bot 走真实 HTTP -> 真实图 run + 冲突/幂等/租户隔离。

不依赖公网：本机 uvicorn（127.0.0.1:8942）+ 进程内 demo SQLite。`slow` 图用递归
CTE 把真实查询拉长到约 1s，用来让两个 bot 在同一个 `(tenant, graph)` 上真冲突。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn

HOST = "127.0.0.1"
PORT = 8942
BASE = f"http://{HOST}:{PORT}"
TOKEN_A = "token-bot-a"
TOKEN_B = "token-bot-b"
TOKEN_C = "token-bot-c"
TENANT_A = "a2a-alpha"
TENANT_B = "a2a-beta"

OUT = Path(__file__).resolve().parents[1] / "outputs" / "a02-a2a-real-run"


def _graph(sql: str) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "name": "start",
                "config": {"triggerType": "webhook", "webhookUrl": "/hooks/a2a"},
            },
            {
                "id": "tool-1",
                "type": "tool_call",
                "name": "query",
                "config": {"tool": "database/query", "params": json.dumps({"sql": sql})},
            },
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }


def _message(skill: str, params: dict) -> dict:
    return {
        "role": "user",
        "parts": [{"kind": "data", "data": {"skill": skill, **params}}],
    }


def _rpc(method: str, params: dict, token: str, *, request_id: int = 1, timeout: float = 30) -> dict:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    req = urllib.request.Request(
        f"{BASE}/api/a2a/tasks",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _send(skill: str, params: dict, token: str, *, timeout: float = 30) -> dict:
    return _rpc("tasks/send", {"message": _message(skill, params)}, token, timeout=timeout)


def _wait_card() -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"{BASE}/api/a2a/agent-card", timeout=1) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.1)
    raise RuntimeError("A2A server did not start")


def _artifact_data(task: dict) -> dict:
    for part in task["artifacts"][0]["parts"]:
        if part.get("kind") == "data":
            return part["data"]
    raise AssertionError("artifact has no data part")


def main() -> int:
    os.environ["ATLAS_ENV"] = "dev"
    os.environ["ATLAS_PUBLIC_BASE_URL"] = BASE
    os.environ["ATLAS_A2A_TASK_TOKENS"] = (
        f"bot-a:{TENANT_A}:{TOKEN_A},bot-b:{TENANT_A}:{TOKEN_B},bot-c:{TENANT_B}:{TOKEN_C}"
    )
    os.environ.pop("ATLAS_A2A_TASK_TOKEN", None)

    from atlas.a2a.card import build_agent_card
    from atlas.a2a.rpc import handle_jsonrpc, reset_for_tests
    from atlas.api.main import app
    from atlas.iam.deps import tenant_registry

    OUT.mkdir(parents=True, exist_ok=True)
    services_a = tenant_registry.get(TENANT_A)
    services_b = tenant_registry.get(TENANT_B)

    alpha_fast = services_a.graph_store.save(
        _graph("SELECT COUNT(*) AS n FROM orders")
    )
    alpha_slow = services_a.graph_store.save(
        _graph(
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 10000000) "
            "SELECT COUNT(*) AS n FROM c"
        )
    )
    beta_fast = services_b.graph_store.save(
        _graph("SELECT COUNT(*) AS n FROM orders WHERE amount > 100")
    )
    alpha_interactive = services_a.graph_store.save(_graph("SELECT 1 AS n"))
    # 再把图改成含 wait 节点（保存后再破坏成交互图，专门验证 A2A 前置拒绝）。
    services_a.graph_store.update_draft(
        alpha_interactive,
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {
                    "id": "trigger-1",
                    "type": "trigger",
                    "name": "start",
                    "config": {"triggerType": "webhook", "webhookUrl": "/hooks/a2a"},
                },
                {"id": "wait-1", "type": "wait", "name": "pause", "config": {}},
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "wait-1"}],
        },
    )

    graph_files = {
        "alpha-fast.json": _graph("SELECT COUNT(*) AS n FROM orders"),
        "alpha-slow.json": _graph(
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 10000000) "
            "SELECT COUNT(*) AS n FROM c"
        ),
        "alpha-interactive.json": {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "start", "config": {"triggerType": "webhook", "webhookUrl": "/hooks/a2a"}},
                {"id": "wait-1", "type": "wait", "name": "pause", "config": {}},
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "wait-1"}],
        },
    }
    for name, graph in graph_files.items():
        (OUT / name).write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n")

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    _wait_card()

    evidence: dict = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "real-http-a2a-real-run",
        "server": BASE,
        "card": {
            "version": build_agent_card()["version"],
            "skills": [s["id"] for s in build_agent_card()["skills"]],
            "dataPolicy": build_agent_card()["x-zeus-handshake"]["dataPolicy"],
        },
        "tokens": {"bot-a": "bot-a->a2a-alpha", "bot-b": "bot-b->a2a-alpha", "bot-c": "bot-c->a2a-beta"},
        "graphs": {
            "alphaFast": alpha_fast,
            "alphaSlow": alpha_slow,
            "alphaInteractive": alpha_interactive,
            "betaFast": beta_fast,
        },
        "runs": [],
        "idempotency": {},
        "conflict": {},
        "tenantIsolation": {},
        "interactiveRejected": {},
        "volatileLedger": {},
    }

    try:
        run_a = _send(
            "run-graph",
            {"tenant_id": TENANT_A, "graph_id": alpha_fast, "idempotency_key": "bot-a-real-1"},
            TOKEN_A,
        )["result"]
        data_a = _artifact_data(run_a)
        evidence["runs"].append(
            {
                "bot": "bot-a",
                "tenant": TENANT_A,
                "graph": alpha_fast,
                "taskStatus": run_a["status"]["state"],
                "taskId": run_a["id"],
                "runId": data_a["runId"],
                "runStore": services_a.run_store.get(data_a["runId"]),
                "outputs": data_a["outputs"],
                "trace": data_a["trace"],
            }
        )

        # 幂等：同 key/同请求 -> 同一个 runId，run_store 不新增。
        replay = _send(
            "run-graph",
            {"tenant_id": TENANT_A, "graph_id": alpha_fast, "idempotency_key": "bot-a-real-1"},
            TOKEN_A,
        )["result"]
        replay_data = _artifact_data(replay)
        runs_for_graph = [r for r in services_a.run_store.list(limit=100) if r["graphId"] == alpha_fast]
        evidence["idempotency"] = {
            "firstTaskId": run_a["id"],
            "replayTaskId": replay["id"],
            "firstRunId": data_a["runId"],
            "replayRunId": replay_data["runId"],
            "sameRun": data_a["runId"] == replay_data["runId"],
            "runStoreRecordsForGraph": len(runs_for_graph),
        }

        # 第二个 bot 串行真实 run（同租户、不同幂等键）。
        run_b = _send(
            "run-graph",
            {"tenant_id": TENANT_A, "graph_id": alpha_fast, "idempotency_key": "bot-b-real-1"},
            TOKEN_B,
        )["result"]
        data_b = _artifact_data(run_b)
        evidence["runs"].append(
            {
                "bot": "bot-b",
                "tenant": TENANT_A,
                "graph": alpha_fast,
                "taskStatus": run_b["status"]["state"],
                "taskId": run_b["id"],
                "runId": data_b["runId"],
                "runStore": services_a.run_store.get(data_b["runId"]),
                "outputs": data_b["outputs"],
            }
        )

        # 第三个 bot 在另一个租户真实 run。
        run_c = _send(
            "run-graph",
            {"tenant_id": TENANT_B, "graph_id": beta_fast, "idempotency_key": "bot-c-real-1"},
            TOKEN_C,
        )["result"]
        data_c = _artifact_data(run_c)
        evidence["runs"].append(
            {
                "bot": "bot-c",
                "tenant": TENANT_B,
                "graph": beta_fast,
                "taskStatus": run_c["status"]["state"],
                "taskId": run_c["id"],
                "runId": data_c["runId"],
                "runStore": services_b.run_store.get(data_c["runId"]),
                "outputs": data_c["outputs"],
            }
        )

        # 冲突：bot-a 的真实慢查询还在 running 时，bot-b 打同一 (tenant, graph)。
        slow_holder: dict = {}
        slow_started = threading.Event()

        def slow_call() -> None:
            slow_started.set()
            slow_holder["resp"] = _send(
                "run-graph",
                {"tenant_id": TENANT_A, "graph_id": alpha_slow, "idempotency_key": "bot-a-slow-1"},
                TOKEN_A,
                timeout=30,
            )

        thread = threading.Thread(target=slow_call)
        thread.start()
        assert slow_started.wait(timeout=2)
        active_run = None
        for _ in range(100):
            active_run = next(
                (r for r in services_a.run_store.list(limit=100) if r["graphId"] == alpha_slow and r["status"] == "running"),
                None,
            )
            if active_run is not None:
                break
            time.sleep(0.02)
        if active_run is None:
            raise AssertionError("slow run never became visible in run_store")
        conflict = _send(
            "run-graph",
            {"tenant_id": TENANT_A, "graph_id": alpha_slow, "idempotency_key": "bot-b-slow-1"},
            TOKEN_B,
        )
        thread.join(timeout=30)
        first_slow = slow_holder["resp"]["result"]
        evidence["conflict"] = {
            "activeRunWhileSecondCall": active_run,
            "secondCallError": conflict.get("error"),
            "firstCallTaskStatus": first_slow["status"]["state"],
            "firstCallRunId": _artifact_data(first_slow)["runId"],
            "secondCallCreatedNoRun": not any(
                r["graphId"] == alpha_slow and r["status"] == "running"
                for r in services_a.run_store.list(limit=100)
            ),
        }

        # 租户绑定：bot-c 不能借参数跨到 alpha。
        cross = _send(
            "run-graph",
            {"tenant_id": TENANT_A, "graph_id": alpha_fast, "idempotency_key": "bot-c-cross-1"},
            TOKEN_C,
        )
        evidence["tenantIsolation"] = {
            "bot": "bot-c",
            "callerTenant": TENANT_B,
            "requestedTenant": TENANT_A,
            "error": cross.get("error"),
        }

        # 交互图在启动前被拒绝，不产生 run_store 记录。
        interactive = _send(
            "run-graph",
            {"tenant_id": TENANT_A, "graph_id": alpha_interactive, "idempotency_key": "bot-a-interactive-1"},
            TOKEN_A,
        )["result"]
        evidence["interactiveRejected"] = {
            "taskStatus": interactive["status"]["state"],
            "error": _artifact_data(interactive),
            "runStoreRecordsForGraph": len(
                [r for r in services_a.run_store.list(limit=100) if r["graphId"] == alpha_interactive]
            ),
        }

        # 任务台账 volatile：reset 后 get 查不到；run_store 仍在同一进程内可查。
        first_task_id = run_a["id"]
        first_run_id = data_a["runId"]
        reset_for_tests()
        after_reset = handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 999, "method": "tasks/get", "params": {"id": first_task_id}}
        )
        evidence["volatileLedger"] = {
            "tasksGetAfterLedgerReset": after_reset.get("error"),
            "runStoreAfterLedgerReset": services_a.run_store.get(first_run_id),
            "note": "same-process reset only; a real process restart clears the in-memory run_store too",
        }

        evidence["runStoreSnapshot"] = {
            TENANT_A: services_a.run_store.list(limit=100),
            TENANT_B: services_b.run_store.list(limit=100),
        }
        evidence["ok"] = True
    finally:
        (OUT / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
        server.should_exit = True
        server_thread.join(timeout=5)

    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
