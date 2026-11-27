"""T4 证据：任务总线 SQLite 跨重启 + 优先级抢占 + 失败可见 + API 视图。

`--seed` 在独立 Python 进程里写台账；主进程随后启动一个全新应用实例，
经登录后的 `GET /api/tasks` 读同一份 SQLite，证明不是进程内字典。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "outputs" / "t4-task-bus"
DB = OUT / "tasks.sqlite3"
TENANT = "t1"


def _seed() -> int:
    from atlas.coordination.sqlite_store import SqliteTaskStore

    OUT.mkdir(parents=True, exist_ok=True)
    store = SqliteTaskStore(DB, TENANT)
    store.reset()

    low, low_created = store.dispatch(
        run_id="run-t4-low",
        idempotency_key="t4-low",
        trace_id="trace-t4",
        graph_version="return-flow@1",
        type="refund.verify_order",
        assignee="bot.customer",
        payload={"order_id": "T4-ORDER"},
        deadline_ms=1000,
        conflict_key="refund:T4-ORDER",
        priority=1,
    )
    store.accept(low.taskId)
    store.start(low.taskId)

    high, high_created = store.dispatch(
        run_id="run-t4-high",
        idempotency_key="t4-high",
        trace_id="trace-t4",
        graph_version="return-flow@1",
        type="refund.finance_review",
        assignee="bot.finance",
        payload={"order_id": "T4-ORDER"},
        deadline_ms=1000,
        conflict_key="refund:T4-ORDER",
        priority=5,
    )
    store.accept(high.taskId)
    store.start(high.taskId)
    store.fail(high.taskId, "PAYMENT_CHANNEL_REJECTED")

    # 无冲突的普通任务，验证幂等键与终态一起可恢复。
    normal, normal_created = store.dispatch(
        run_id="run-t4-normal",
        idempotency_key="t4-normal",
        trace_id="trace-t4-normal",
        graph_version="return-flow@1",
        type="refund.verify_order",
        assignee="bot.customer",
        payload={"order_id": "T4-NORMAL"},
        deadline_ms=1000,
    )
    store.accept(normal.taskId)
    store.start(normal.taskId)
    store.complete(normal.taskId, {"signed": True})

    (OUT / "seed.json").write_text(
        json.dumps(
            {
                "low": low.taskId,
                "high": high.taskId,
                "normal": normal.taskId,
                "created": {
                    "low": low_created,
                    "high": high_created,
                    "normal": normal_created,
                },
                "lowResult": store.get(low.taskId).result,
                "highResult": store.get(high.taskId).result,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def _api_view() -> int:
    from fastapi.testclient import TestClient

    from atlas.api.main import app

    from atlas.iam.deps import tenant_registry

    # 新进程内首次访问 t1 时才会建 SqliteTaskStore；这里显式访问一次，确保读的是同一文件。
    store = tenant_registry.get(TENANT).task_store
    assert store.path == str(DB) or getattr(store, "path", "") == str(DB)

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin-a", "password": "admin123"},
        )
        login.raise_for_status()
        tasks = client.get("/api/tasks?limit=50").json()["items"]
        task_ids = {task["taskId"] for task in tasks}
        seed = json.loads((OUT / "seed.json").read_text(encoding="utf-8"))
        assert {seed["low"], seed["high"], seed["normal"]} <= task_ids
        low = next(task for task in tasks if task["taskId"] == seed["low"])
        high = next(task for task in tasks if task["taskId"] == seed["high"])
        assert low["state"] == "failed"
        assert low["result"]["error"] == "TASK_PREEMPTED"
        assert low["result"]["preemptedBy"] == seed["high"]
        assert high["state"] == "failed"
        assert high["result"]["error"] == "PAYMENT_CHANNEL_REJECTED"
        assert all(task["conflictKey"] in ("", "refund:T4-ORDER") for task in tasks)

        evidence = {
            "mode": "real-cross-process-sqlite-task-bus",
            "db": str(DB),
            "tenant": TENANT,
            "api": "GET /api/tasks",
            "taskCount": len(tasks),
            "tasks": tasks,
            "seed": seed,
            "ok": True,
        }
    (OUT / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": True, "taskCount": len(tasks)}, ensure_ascii=False))
    return 0


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "ATLAS_TASK_STORE_PATH": str(DB),
        "ATLAS_ENV": "dev",
        "ATLAS_ENABLE_DEMO_MOCK": "1",
    }
    os.environ.update(env)
    if "--seed" in sys.argv:
        return _seed()
    seed = subprocess.run(
        [sys.executable, __file__, "--seed"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if seed.returncode != 0:
        print(seed.stderr[-2000:])
        return seed.returncode
    return _api_view()


if __name__ == "__main__":
    raise SystemExit(main())
