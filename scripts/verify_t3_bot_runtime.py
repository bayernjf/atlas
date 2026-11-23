"""T3 证据：3 个独立 Bot 运行时 + 物流 HTTP 适配器（真实本机 HTTP）。

- 本机 uvicorn 起 Atlas A2A 入口；
- 另一个本机 HTTP 服务模拟物流签收系统，`HttpLogisticsAdapter` 真发 HTTP；
- 3 个 Agent（customer / logistics / finance）各自 `agent_id` + 适配器白名单；
- 每个 Agent 经 A2A `run-graph` 跑一张图，证据里串 task→agentId→runtimeId→runId→run_store。

不依赖公网、不接真实物流厂商；`ATLAS_LOGISTICS_URL` 指向本机服务即“本地可达”。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn

HOST = "127.0.0.1"
ATLAS_PORT = 8944
LOGISTICS_PORT = 8945
BASE = f"http://{HOST}:{ATLAS_PORT}"
LOGISTICS_BASE = f"http://{HOST}:{LOGISTICS_PORT}"
TENANT = "t3-bots"
OUT = Path(__file__).resolve().parents[1] / "outputs" / "t3-bot-runtime"

TOKENS = {
    "bot.customer": "token-customer",
    "bot.logistics": "token-logistics",
    "bot.finance": "token-finance",
}


class _LogisticsHandler(BaseHTTPRequestHandler):
    hits: list[dict] = []
    signed: set[str] = set()

    def log_message(self, *_args) -> None:  # noqa: D401 - 静音访问日志
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server 契约
        order_id = self.path.rsplit("/", 1)[-1]
        self.hits.append({"method": "GET", "path": self.path, "orderId": order_id})
        self._json(200, {"order_id": order_id, "signed": order_id in self.signed})

    def do_POST(self) -> None:  # noqa: N802 - http.server 契约
        if not self.path.endswith("/sign"):
            self._json(404, {"error": "not found"})
            return
        order_id = self.path.split("/receipts/", 1)[-1].removesuffix("/sign")
        self.hits.append({"method": "POST", "path": self.path, "orderId": order_id})
        self.signed.add(order_id)
        self._json(200, {"order_id": order_id, "signed": True})


def _graph(tool: str, params: dict) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "name": "start",
                "config": {"triggerType": "webhook", "webhookUrl": "/hooks/t3"},
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


def _rpc(method: str, params: dict, token: str) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = urllib.request.Request(
        f"{BASE}/api/a2a/tasks",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _send(agent_id: str, graph_id: str, token: str) -> dict:
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
                    "idempotency_key": f"{agent_id}-{uuid4()}",
                },
            }
        ],
    }
    return _rpc("tasks/send", {"message": message}, token)["result"]


def uuid4() -> str:
    import uuid

    return uuid.uuid4().hex


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


def main() -> int:
    os.environ["ATLAS_ENV"] = "dev"
    os.environ["ATLAS_ENABLE_DEMO_MOCK"] = "1"
    os.environ["ATLAS_PUBLIC_BASE_URL"] = BASE
    os.environ["ATLAS_LOGISTICS_URL"] = LOGISTICS_BASE
    os.environ["ATLAS_A2A_TASK_TOKENS"] = ",".join(
        f"{agent}:{TENANT}:{token}" for agent, token in TOKENS.items()
    )
    os.environ.pop("ATLAS_A2A_TASK_TOKEN", None)

    from atlas.a2a.card import build_agent_card
    from atlas.a2a.rpc import reset_for_tests
    from atlas.api.main import app
    from atlas.iam.agents_store import Agent
    from atlas.iam.deps import tenant_registry
    from atlas.logistics.http_adapter import HttpLogisticsAdapter

    reset_for_tests()
    services = tenant_registry.get(TENANT)
    agents = {
        "bot.customer": Agent(
            id="bot.customer", tenant_id=TENANT, name="Customer Bot",
            adapter_ids=["shop", "message"], skills=["refund.query"],
        ),
        "bot.logistics": Agent(
            id="bot.logistics", tenant_id=TENANT, name="Logistics Bot",
            adapter_ids=["logistics"], skills=["logistics.check_receipt"],
        ),
        "bot.finance": Agent(
            id="bot.finance", tenant_id=TENANT, name="Finance Bot",
            adapter_ids=["database", "message"], skills=["finance.summary"],
        ),
    }
    for agent in agents.values():
        services.agents_store.put(agent)

    graphs = {
        "bot.customer": services.graph_store.save(
            _graph("shop/list_pending_refunds", {})
        ),
        "bot.logistics": services.graph_store.save(
            _graph("logistics/check_receipt", {"order_id": "T3-ORDER-1"})
        ),
        "bot.finance": services.graph_store.save(
            _graph("database/query", {"sql": "SELECT COUNT(*) AS n FROM orders"})
        ),
    }

    logistics_server = ThreadingHTTPServer(
        (HOST, LOGISTICS_PORT), _LogisticsHandler
    )
    threading.Thread(target=logistics_server.serve_forever, daemon=True).start()
    _wait_ready(f"{LOGISTICS_BASE}/receipts/T3-WARMUP")

    atlas_server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=ATLAS_PORT, log_level="warning", access_log=False)
    )
    threading.Thread(target=atlas_server.run, daemon=True).start()
    _wait_ready(f"{BASE}/api/a2a/agent-card")

    OUT.mkdir(parents=True, exist_ok=True)
    for agent_id, graph_id in graphs.items():
        (OUT / f"{agent_id.replace('.', '-')}.graph.json").write_text(
            json.dumps(_graph(
                "shop/list_pending_refunds" if agent_id == "bot.customer" else (
                    "logistics/check_receipt" if agent_id == "bot.logistics" else "database/query"
                ),
                {} if agent_id == "bot.customer" else (
                    {"order_id": "T3-ORDER-1"} if agent_id == "bot.logistics" else
                    {"sql": "SELECT COUNT(*) AS n FROM orders"}
                ),
            ), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    evidence: dict = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "real-http-a2a-bot-runtime",
        "tenant": TENANT,
        "server": BASE,
        "logisticsServer": LOGISTICS_BASE,
        "card": {
            "version": build_agent_card()["version"],
            "skills": [s["id"] for s in build_agent_card()["skills"]],
        },
        "agents": [agents[a].to_record() for a in sorted(agents)],
        "graphs": graphs,
        "runs": [],
        "logisticsAdapter": {
            "class": HttpLogisticsAdapter.__name__,
            "hits": _LogisticsHandler.hits,
        },
    }

    try:
        for agent_id in ("bot.customer", "bot.logistics", "bot.finance"):
            task = _send(agent_id, graphs[agent_id], TOKENS[agent_id])
            data = _artifact_data(task)
            run = services.run_store.get(data["runId"])
            evidence["runs"].append(
                {
                    "agentId": agent_id,
                    "taskId": task["id"],
                    "taskState": task["status"]["state"],
                    "taskMetadata": task.get("metadata"),
                    "artifactAgentId": data.get("agentId"),
                    "runtimeId": data.get("runtimeId"),
                    "runId": data.get("runId"),
                    "graphId": data.get("graphId"),
                    "status": data.get("status"),
                    "error": data.get("error"),
                    "outputs": data.get("outputs"),
                    "runStoreAgentId": (run or {}).get("agentId"),
                    "runStoreStatus": (run or {}).get("status"),
                }
            )
        evidence["ok"] = all(
            r["taskState"] == "completed"
            and r["artifactAgentId"] == r["agentId"]
            and r["runtimeId"] == f"bot-runtime:{TENANT}:{r['agentId']}"
            and r["runStoreAgentId"] == r["agentId"]
            for r in evidence["runs"]
        )
        evidence["logisticsAdapter"]["hits"] = list(_LogisticsHandler.hits)
        evidence["logisticsAdapter"]["signed"] = sorted(_LogisticsHandler.signed)
        evidence["distinctRuntimeIds"] = sorted({r["runtimeId"] for r in evidence["runs"]})
        evidence["distinctRunIds"] = sorted({r["runId"] for r in evidence["runs"]})
    finally:
        (OUT / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        logistics_server.shutdown()
        atlas_server.should_exit = True

    print(json.dumps({
        "ok": evidence.get("ok"),
        "runs": len(evidence["runs"]),
        "distinctRuntimeIds": evidence.get("distinctRuntimeIds"),
        "logisticsHits": len(evidence["logisticsAdapter"]["hits"]),
    }, ensure_ascii=False))
    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
