"""T7 证据：多智能体可观测回归（真实本机 HTTP A2A）。

验证链：
- 3 个 Bot 经 A2A `run-graph` 起真实 run；
- task.id ↔ task.metadata.a2a ↔ RunRecord(task_id/run_id/agent_id/runtime_id) ↔ root span attrs；
- `/api/monitoring/metrics` 的 per_bot 与 Prometheus `atlas_bot_*`；
- 失败 Bot 触发既有内置告警，`last_run_id` 回查到同一 task/bot/run；
- 同 idempotency_key 回放不重跑、不新增监控记录，task/run/trace 三键不变。

不引入 OTel/Prometheus 正式栈；只用进程内 monitoring + 既有 `/metrics` 文本。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import uvicorn

HOST = "127.0.0.1"
ATLAS_PORT = 8946
BASE = f"http://{HOST}:{ATLAS_PORT}"
TENANT = "t1"
OUT = Path(__file__).resolve().parents[1] / "outputs" / "t7-multiagent-observability"

BOTS = {
    "bot.alpha": "token-alpha",
    "bot.beta": "token-beta",
    "bot.gamma": "token-gamma",
}


def _http_json(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    token: str | None = None,
    timeout: int = 30,
) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _rpc(method: str, params: dict, token: str) -> dict:
    return _http_json(
        "POST",
        "/api/a2a/tasks",
        payload={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        token=token,
    )


def _send(agent_id: str, graph_id: str, idempotency_key: str) -> dict:
    message = {
        "role": "user",
        "parts": [
            {
                "kind": "data",
                "data": {
                    "skill": "run-graph",
                    "tenant_id": TENANT,
                    "graph_id": graph_id,
                    "agent_id": agent_id,
                    "idempotency_key": idempotency_key,
                },
            }
        ],
    }
    return _rpc("tasks/send", {"message": message}, BOTS[agent_id])["result"]


def _artifact_data(task: dict) -> dict:
    for part in task["artifacts"][0]["parts"]:
        if part.get("kind") == "data":
            return part["data"]
    raise AssertionError("artifact has no data part")


def _wait_ready(path: str) -> None:
    for _ in range(160):
        try:
            with urllib.request.urlopen(path, timeout=1) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.1)
    raise RuntimeError(f"server did not start: {path}")


def _graph(tool: str, params: dict) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "name": "start",
                "config": {"triggerType": "webhook", "webhookUrl": "/hooks/t7"},
            },
            {
                "id": "tool-1",
                "type": "tool_call",
                "name": "call",
                "config": {"tool": tool, "params": json.dumps(params)},
            },
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }


def main() -> int:
    os.environ["ATLAS_ENV"] = "dev"
    os.environ["ATLAS_ENABLE_DEMO_MOCK"] = "1"
    os.environ["ATLAS_PUBLIC_BASE_URL"] = BASE
    os.environ["ATLAS_A2A_TASK_TOKENS"] = ",".join(
        f"{bot}:{TENANT}:{token}" for bot, token in BOTS.items()
    )
    os.environ.pop("ATLAS_A2A_TASK_TOKEN", None)

    from atlas.a2a.rpc import reset_for_tests
    from atlas.api.main import app
    from atlas.iam.agents_store import Agent
    from atlas.iam.deps import tenant_registry

    reset_for_tests()
    atlas_server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=ATLAS_PORT, log_level="warning", access_log=False)
    )
    threading.Thread(target=atlas_server.run, daemon=True).start()
    _wait_ready(f"{BASE}/api/a2a/agent-card")

    OUT.mkdir(parents=True, exist_ok=True)
    evidence: dict = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "real-http-a2a-observability",
        "tenant": TENANT,
        "server": BASE,
        "bots": [],
        "runs": [],
        "alerts": [],
        "replay": {},
        "metrics": {},
        "prometheus": {},
    }

    try:
        login = _http_json(
            "POST", "/api/auth/login",
            payload={"username": "admin-a", "password": "admin123"},
        )
        admin_token = login["token"]
        services = tenant_registry.get(TENANT)

        agents = {
            "bot.alpha": Agent(
                id="bot.alpha", tenant_id=TENANT, name="Alpha Bot",
                adapter_ids=["database"], skills=["observability.success"],
            ),
            "bot.beta": Agent(
                id="bot.beta", tenant_id=TENANT, name="Beta Bot",
                adapter_ids=["database"], skills=["observability.success"],
            ),
            "bot.gamma": Agent(
                id="bot.gamma", tenant_id=TENANT, name="Gamma Bot",
                adapter_ids=["database"], skills=["observability.failure"],
            ),
        }
        for agent in agents.values():
            services.agents_store.put(agent)
        evidence["bots"] = [agents[bot].to_record() | {"botId": bot} for bot in sorted(agents)]

        graphs = {
            "bot.alpha": services.graph_store.save(
                _graph("database/query", {"sql": "SELECT COUNT(*) AS n FROM orders"})
            ),
            "bot.beta": services.graph_store.save(
                _graph("database/query", {"sql": "SELECT 1 AS n"})
            ),
            "bot.gamma": services.graph_store.save(
                _graph("shop/execute_refund", {"order_id": "T7-ORDER-FAIL", "amount": 10})
            ),
        }

        keys = {bot: f"t7-{bot}-{uuid.uuid4().hex}" for bot in BOTS}
        for bot in ("bot.alpha", "bot.beta", "bot.gamma"):
            task = _send(bot, graphs[bot], keys[bot])
            data = _artifact_data(task)
            record = next(
                r for r in services.monitoring.list_runs(limit=200) if r.task_id == task["id"]
            )
            evidence["runs"].append({
                "agentId": bot,
                "taskId": task["id"],
                "taskState": task["status"]["state"],
                "taskMetadata": task.get("metadata"),
                "artifact": {
                    key: data.get(key)
                    for key in ("taskId", "runId", "traceId", "agentId", "runtimeId", "status", "error")
                },
                "runStore": {
                    "agentId": (services.run_store.get(data["runId"]) or {}).get("agentId"),
                    "status": (services.run_store.get(data["runId"]) or {}).get("status"),
                },
                "monitoring": {
                    "id": record.id,
                    "taskId": record.task_id,
                    "runId": record.run_id,
                    "traceId": record.trace_id,
                    "agentId": record.agent_id,
                    "runtimeId": record.runtime_id,
                    "status": record.status,
                    "mode": record.mode,
                    "failedNodes": [n.node_id for n in record.nodes if n.status == "failed"],
                    "spanRootAttrs": (record.spans or {}).get("attrs"),
                },
            })

        snapshot = services.monitoring.snapshot_metrics()
        evidence["metrics"] = {
            "total": snapshot["total"],
            "perBot": snapshot["per_bot"],
        }
        evidence["alerts"] = [
            {
                "id": alert.id,
                "ruleId": alert.rule_id,
                "severity": alert.severity,
                "lastRunId": alert.last_run_id,
                "count": alert.count,
                "status": alert.status,
            }
            for alert in services.monitoring.list_alerts()
        ]

        first = evidence["runs"][0]
        replay_task = _send("bot.alpha", graphs["bot.alpha"], keys["bot.alpha"])
        replay_data = _artifact_data(replay_task)
        replay_records = [
            r for r in services.monitoring.list_runs(limit=200) if r.task_id == first["taskId"]
        ]
        evidence["replay"] = {
            "originalTaskId": first["taskId"],
            "replayedTaskId": replay_task["id"],
            "sameTask": replay_task["id"] == first["taskId"],
            "sameRunId": replay_data["runId"] == first["artifact"]["runId"],
            "sameTraceId": replay_data["traceId"] == first["artifact"]["traceId"],
            "monitoringRecords": len(replay_records),
        }

        metrics = _http_json("GET", "/api/monitoring/metrics", token=admin_token)
        runs_body = _http_json(
            "GET", "/api/monitoring/runs?agent_id=bot.alpha&limit=200", token=admin_token
        )
        trace_body = _http_json(
            "GET", f"/api/monitoring/runs/{first['monitoring']['id']}/trace",
            token=admin_token,
        )
        evidence["api"] = {
            "perBotIds": [row["bot_id"] for row in metrics.get("per_bot", [])],
            "filteredRunCount": len(runs_body.get("items", [])),
            "traceTaskId": ((trace_body.get("spans") or {}).get("attrs") or {}).get("taskId"),
        }

        with urllib.request.urlopen(f"{BASE}/metrics", timeout=10) as resp:
            prometheus_text = resp.read().decode()
        bot_lines = [
            line for line in prometheus_text.splitlines()
            if line.startswith("atlas_bot_")
        ]
        evidence["prometheus"] = {
            "botSeriesCount": len(bot_lines),
            "sample": bot_lines[:6],
        }

        chain_ok = all(
            run["artifact"]["taskId"] == run["taskId"]
            == run["monitoring"]["taskId"]
            == (run["monitoring"]["spanRootAttrs"] or {}).get("taskId")
            and run["artifact"]["runId"] == run["monitoring"]["runId"]
            == (run["monitoring"]["spanRootAttrs"] or {}).get("runId")
            and run["artifact"]["agentId"] == run["agentId"]
            == run["monitoring"]["agentId"]
            == (run["monitoring"]["spanRootAttrs"] or {}).get("agentId")
            and run["artifact"]["runtimeId"] == run["monitoring"]["runtimeId"]
            == (run["monitoring"]["spanRootAttrs"] or {}).get("runtimeId")
            and run["artifact"]["traceId"] == run["monitoring"]["traceId"]
            for run in evidence["runs"]
        )
        per_bot_ok = all(
            bot in {row["bot_id"] for row in snapshot["per_bot"]} for bot in BOTS
        )
        alert_linked = any(
            alert["lastRunId"] == evidence["runs"][2]["monitoring"]["id"]
            for alert in evidence["alerts"]
        )
        replay_ok = (
            evidence["replay"]["sameTask"]
            and evidence["replay"]["sameRunId"]
            and evidence["replay"]["sameTraceId"]
            and evidence["replay"]["monitoringRecords"] == 1
        )
        evidence["checks"] = {
            "chain": chain_ok,
            "perBotMetrics": per_bot_ok,
            "alertLinkedToBotRun": alert_linked,
            "replayNoRerun": replay_ok,
            "prometheusBotSeries": bool(bot_lines),
            "apiFilterAndTrace": (
                "bot.alpha" in evidence["api"]["perBotIds"]
                and evidence["api"]["traceTaskId"] == first["taskId"]
            ),
        }
        evidence["ok"] = all(evidence["checks"].values())
    finally:
        (OUT / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        atlas_server.should_exit = True

    print(json.dumps({
        "ok": evidence.get("ok"),
        "checks": evidence.get("checks"),
        "runCount": len(evidence.get("runs", [])),
        "alertCount": len(evidence.get("alerts", [])),
    }, ensure_ascii=False))
    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
