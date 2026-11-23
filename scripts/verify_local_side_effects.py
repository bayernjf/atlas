#!/usr/bin/env python3
"""A-05：本地真实副作用 + 本地审批/投递证据。

路径：
- 本地 SQLite 文件库（真实写入，不是内存 mock）；
- human_approval 真挂起，审批通过前不许写入；
- 审批通过后 database/execute 写库，再由 message/send 投递到 127.0.0.1 一次性 webhook；
- 拒绝路径不写库；
- 投递失败路径保留“已写库但未送达”的失败证据，并用补偿 DELETE 回滚。

证据：outputs/a05-local-side-effects/ 下的 evidence.json、graph.json、a05.db。
默认只在非 prod 本地档运行；不涉及公网 SMTP/IM/真实店铺。
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from atlas.collaboration.approvals import ApprovalBroker
from atlas.database.adapter import DatabaseHarnessAdapter
from atlas.database.service import DatabaseClient
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.harness.base import Permission
from atlas.harness.registry import AdapterRegistry
from atlas.message.adapter import MessageHarnessAdapter
from atlas.message.service import MessageService
from atlas.message.webhook import DefaultWebhookSender


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wait_frame(frames: list[dict[str, Any]], timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while not frames and time.monotonic() < deadline:
        time.sleep(0.01)
    if not frames:
        raise RuntimeError("human_approval 未在超时前产生挂起帧")
    return frames[0]


def _start_run(
    graph: dict[str, Any], registry: AdapterRegistry, broker: ApprovalBroker, inputs: dict[str, Any]
):
    frames: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []

    def worker() -> None:
        try:
            result.append(
                run_graph(
                    parse_graph(graph),
                    inputs=inputs,
                    registry=registry,
                    approval_broker=broker,
                    frame_sink=frames.append,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 证据脚本要把失败原样留下
            result.append({"exception": type(exc).__name__, "message": str(exc)})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread, frames, result


def _row_count(client: DatabaseClient) -> int:
    result = client.query("SELECT COUNT(*) AS n FROM a05_orders")
    rows = result.get("rows", [])
    return int(rows[0]["n"]) if rows else 0


def _load_rows(client: DatabaseClient) -> list[dict[str, Any]]:
    return list(client.query("SELECT order_id, amount, note, created_at FROM a05_orders ORDER BY order_id").get("rows", []))


def _build_graph(webhook_url: str) -> dict[str, Any]:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "name": "本地写入申请",
                "config": {"triggerType": "webhook", "webhookUrl": "/hooks/a05"},
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
            {
                "id": "human_approval-1",
                "type": "human_approval",
                "name": "人工审批：本地写库",
                "config": {
                    "summary": "A-05 本地写库：订单 {{trigger-1.context.payload.order_id}} / {{trigger-1.context.payload.amount}} 元",
                    "approver": "",
                    "timeoutSeconds": 30,
                    "onTimeout": "reject",
                    "approvedTarget": "tool-db",
                    "rejectedTarget": "tool-reject-msg",
                },
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
            {
                "id": "tool-db",
                "type": "tool_call",
                "name": "工具：写本地 SQLite",
                "config": {
                    "tool": "database/execute",
                    "params": json.dumps(
                        {
                            "sql": "INSERT INTO a05_orders (order_id, amount, note, created_at) VALUES (:order_id, :amount, :note, :created_at)",
                            "params": {
                                "order_id": "{{trigger-1.context.payload.order_id}}",
                                "amount": "{{trigger-1.context.payload.amount}}",
                                "note": "approved-local-write",
                                "created_at": "{{trigger-1.context.payload.created_at}}",
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
            {
                "id": "tool-ok-msg",
                "type": "tool_call",
                "name": "工具：投递本地 webhook",
                "config": {
                    "tool": "message/send",
                    "params": json.dumps(
                        {
                            "channel": "webhook",
                            "to": webhook_url,
                            "subject": "A-05 本地写入完成",
                            "body": "订单 {{trigger-1.context.payload.order_id}} 已写入本地 SQLite",
                        },
                        ensure_ascii=False,
                    ),
                },
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
            {
                "id": "tool-reject-msg",
                "type": "tool_call",
                "name": "工具：投递拒绝通知",
                "config": {
                    "tool": "message/send",
                    "params": json.dumps(
                        {
                            "channel": "webhook",
                            "to": webhook_url,
                            "subject": "A-05 本地写入被拒绝",
                            "body": "订单 {{trigger-1.context.payload.order_id}} 未写入（人工拒绝）",
                        },
                        ensure_ascii=False,
                    ),
                },
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "human_approval-1"},
            {"id": "e2", "source": "human_approval-1", "target": "tool-db"},
            {"id": "e3", "source": "human_approval-1", "target": "tool-reject-msg"},
            {"id": "e4", "source": "tool-db", "target": "tool-ok-msg"},
        ],
    }


def _run_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {"available": False}
    return {
        "status": result.get("status"),
        "run_id": result.get("run_id"),
        "error": result.get("error"),
        "exception": result.get("exception"),
        "message": result.get("message"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="outputs/a05-local-side-effects")
    parser.add_argument("--order-ok", default="A05-OK-001")
    parser.add_argument("--order-rejected", default="A05-REJECT-001")
    parser.add_argument("--order-fail", default="A05-FAIL-001")
    args = parser.parse_args()

    if os.getenv("ATLAS_ENV", "dev").strip().lower() == "prod":
        print("A-05 只跑本地自用档；ATLAS_ENV=prod 已关闭回环放行，拒绝运行")
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in ("a05.db", "a05.db-wal", "a05.db-shm"):
        (out_dir / stale).unlink(missing_ok=True)

    db_path = (out_dir / "a05.db").resolve()
    engine = create_engine(f"sqlite:///{db_path}")
    client = DatabaseClient(engine=engine, url=f"sqlite:///{db_path}", demo=True)
    client.execute(
        "CREATE TABLE a05_orders ("
        "order_id TEXT PRIMARY KEY, amount REAL NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL)"
    )

    deliveries: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001
                body = raw.decode("utf-8", errors="replace")
            deliveries.append({"path": self.path, "body": body})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    webhook_url = f"http://127.0.0.1:{port}/a05-hook"

    graph = _build_graph(webhook_url)
    (out_dir / "graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2))

    registry = AdapterRegistry()
    permissions = {Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL}
    registry.register(DatabaseHarnessAdapter(client=client, granted_permissions=permissions))
    registry.register(
        MessageHarnessAdapter(
            service=MessageService(webhook_sender=DefaultWebhookSender()),
            granted_permissions=permissions,
        )
    )

    evidence: dict[str, Any] = {
        "started_at": _now(),
        "target": {"db": str(db_path), "webhook": webhook_url},
        "cases": {},
    }

    try:
        # 1) 拒绝路径：审批前 0 行，拒绝后仍 0 行，拒绝通知本地可达。
        broker = ApprovalBroker()
        thread, frames, results = _start_run(
            graph, registry, broker, {"order_id": args.order_rejected, "amount": 88.0, "created_at": _now()}
        )
        frame = _wait_frame(frames)
        evidence["cases"]["rejected"] = {
            "frame_kind": frame.get("kind"),
            "row_count_before_decision": _row_count(client),
            "suspended_before_decision": thread.is_alive(),
        }
        broker.resolve(frame["resume_token"], "rejected", comment="A-05 拒绝路径")
        thread.join(timeout=5)
        evidence["cases"]["rejected"].update(
            {
                "row_count_after_decision": _row_count(client),
                "suspended_after_decision": thread.is_alive(),
                "result": _run_summary(results[0] if results else None),
            }
        )

        # 2) 通过路径：审批前 0 行 → approve → 1 行 → webhook 收到写库通知。
        before = len(deliveries)
        broker = ApprovalBroker()
        thread, frames, results = _start_run(
            graph, registry, broker, {"order_id": args.order_ok, "amount": 129.5, "created_at": _now()}
        )
        frame = _wait_frame(frames)
        evidence["cases"]["approved"] = {
            "frame_kind": frame.get("kind"),
            "row_count_before_decision": _row_count(client),
            "suspended_before_decision": thread.is_alive(),
            "rows_before_decision": _load_rows(client),
        }
        broker.resolve(frame["resume_token"], "approved", comment="A-05 通过路径")
        thread.join(timeout=5)
        evidence["cases"]["approved"].update(
            {
                "row_count_after_decision": _row_count(client),
                "suspended_after_decision": thread.is_alive(),
                "rows_after_decision": _load_rows(client),
                "result": _run_summary(results[0] if results else None),
                "deliveries": deliveries[before:],
            }
        )

        # 3) 投递失败路径：写库成功、webhook 端口不可达；留下失败证据，再用补偿 DELETE 回滚。
        before = len(deliveries)
        broker = ApprovalBroker()
        graph_fail = _build_graph("http://127.0.0.1:1/a05-hook")
        thread, frames, results = _start_run(
            graph_fail, registry, broker, {"order_id": args.order_fail, "amount": 66.0, "created_at": _now()}
        )
        frame = _wait_frame(frames)
        broker.resolve(frame["resume_token"], "approved", comment="A-05 投递失败路径")
        thread.join(timeout=5)
        failure_count = _row_count(client)
        compensate = client.execute(
            "DELETE FROM a05_orders WHERE order_id = :order_id",
            {"order_id": args.order_fail},
        )
        evidence["cases"]["delivery_failure"] = {
            "row_count_after_side_effect": failure_count,
            "side_effect_committed": failure_count == 2,
            "delivery_received": len(deliveries) - before,
            "result": _run_summary(results[0] if results else None),
            "result_full": results[0] if results else None,
            "compensation": {
                "sql": "DELETE FROM a05_orders WHERE order_id = :order_id",
                "rowcount": compensate.get("rowcount"),
                "row_count_after_compensation": _row_count(client),
            },
        }

        # 4) 最终库内只剩通过路径一行。
        evidence["final"] = {
            "row_count": _row_count(client),
            "rows": _load_rows(client),
            "deliveries": deliveries,
        }
        evidence["ended_at"] = _now()
        evidence["ok"] = (
            evidence["cases"]["rejected"]["row_count_after_decision"] == 0
            and evidence["cases"]["approved"]["row_count_before_decision"] == 0
            and evidence["cases"]["approved"]["row_count_after_decision"] == 1
            and len(evidence["cases"]["approved"]["deliveries"]) == 1
            and evidence["cases"]["delivery_failure"]["side_effect_committed"] is True
            and evidence["cases"]["delivery_failure"]["compensation"]["row_count_after_compensation"] == 1
        )
        (out_dir / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        print(json.dumps({"ok": evidence["ok"], "cases": list(evidence["cases"]), "out": str(out_dir)}, ensure_ascii=False))
        return 0 if evidence["ok"] else 1
    finally:
        server.shutdown()
        server.server_close()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
