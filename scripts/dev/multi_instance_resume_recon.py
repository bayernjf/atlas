"""两个实例并存时，同一条挂起 run 会被**各自续跑一遍**——本脚本把这个副作用数出来。

为什么单独测这一条：docs/34 的进程内状态清单只说明「跨 worker 看不见」，而恢复扫描器
（`main.py:154 recover_pending` → `_resume_from_frame` → `threading.Thread(_resume_run)`）
会在**每一个启动的进程**里为库里每条未决帧重建 pending 并起一条续跑线程。于是滚动发布／
崩溃重启／多副本这几种最普通的部署形态下，同一个审批决策会让两条 run 同时往前走——
下游节点（真实退款、真实写库）执行两次。这不是「看不见」，是「做多遍」。

跑法（仓库根，独立勘察库）：

    docker exec atlas-pg psql -U atlas -d postgres -c "CREATE DATABASE atlas_w2_recon OWNER atlas"
    DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas_w2_recon \\
      .venv/bin/python scripts/ops/apply_migrations.py
    docker exec atlas-pg psql -U atlas -d atlas_w2_recon -c "TRUNCATE interruptions, runs, memory_items"
    .venv/bin/python scripts/dev/multi_instance_resume_recon.py

脚本自己起两个 uvicorn 实例（端口 8220/8221）并在结束时收掉。退出码：
下游被执行 1 次＝0（单实例语义成立），>1 次＝1（双跑，实锤）。
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Any

import httpx

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://atlas:atlas@localhost:5432/atlas_w2_recon"
)
MARKER = f"recon-double-{int(time.time())}"
MARK_OK = f"{MARKER}-approved"
MARK_NO = f"{MARKER}-rejected"
A_PORT, B_PORT = 8220, 8221

GRAPH: dict[str, Any] = {
    "version": 1,
    "variables": [],
    "nodes": [
        {
            "id": "trigger-1",
            "type": "trigger",
            "name": "触发",
            "description": "",
            "position": {"x": 80, "y": 200},
            "config": {"triggerType": "manual"},
        },
        {
            "id": "approval-1",
            "type": "human_approval",
            "name": "审批",
            "description": "",
            "position": {"x": 360, "y": 200},
            "config": {
                "summary": "双跑勘察审批",
                "approver": "",
                "timeoutSeconds": 25,
                "onTimeout": "reject",
                "approvedTarget": "write-1",
                "rejectedTarget": "no-1",
            },
        },
        {
            # 审批通过后写一条记忆——PG 表 memory_items 是跨进程可数的副作用
            "id": "write-1",
            "type": "tool_call",
            "name": "写记忆",
            "description": "",
            "position": {"x": 640, "y": 120},
            "config": {
                "tool": "memory/remember",
                "params": f'{{"kind":"fact","content":"{MARK_OK}"}}',
            },
        },
        {
            "id": "no-1",
            "type": "tool_call",
            "name": "拒绝写记忆",
            "description": "",
            "position": {"x": 640, "y": 300},
            "config": {
                "tool": "memory/remember",
                "params": f'{{"kind":"fact","content":"{MARK_NO}"}}',
            },
        },
    ],
    "edges": [
        {"id": "e1", "source": "trigger-1", "target": "approval-1"},
        {"id": "e2", "source": "approval-1", "target": "write-1"},
        {"id": "e3", "source": "approval-1", "target": "no-1"},
    ],
}


def start_instance(port: int, log_path: str) -> subprocess.Popen:
    env = {**os.environ, "ATLAS_STORAGE_BACKEND": "pg", "DATABASE_URL": DB_URL}
    process = subprocess.Popen(
        [
            ".venv/bin/uvicorn",
            "atlas.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO,
        env=env,
        stdout=open(log_path, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/ready", timeout=2.0).status_code == 200:
                return process
        except Exception:  # noqa: BLE001 - 启动探针，任何异常都只表示还没就绪
            pass
        time.sleep(1.0)
    raise RuntimeError(f"实例 :{port} 未在 60s 内就绪，见 {log_path}")


def client(port: int) -> httpx.Client:
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    token = c.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def counted_memories(client_a: httpx.Client, marker: str) -> int:
    """跨进程共享的 PG 副作用计数：某条分支的 marker 被写了几次。"""
    items = client_a.get("/api/memories?limit=200").json().get("items", [])
    return sum(1 for item in items if marker in str(item.get("content", "")))


def branch_counts(client_a: httpx.Client) -> str:
    return f"通过分支×{counted_memories(client_a, MARK_OK)}／拒绝分支×{counted_memories(client_a, MARK_NO)}"


def main() -> int:
    print(f"=== 双实例续跑勘察（marker={MARKER}，A=:{A_PORT} B=:{B_PORT}）===\n")
    processes: list[subprocess.Popen] = []
    exit_code = 0
    try:
        processes.append(start_instance(A_PORT, "/tmp/recon_a.log"))
        a = client(A_PORT)
        print(f"A) 实例 A 就绪；建图前计数＝{branch_counts(a)}")

        graph_id = a.post("/api/graphs", json=GRAPH).json()["id"]
        holder: dict[str, Any] = {}

        def blocked_run() -> None:
            try:
                response = a.post(f"/api/graphs/{graph_id}/run", json={}, timeout=700.0)
                holder["status"] = response.status_code
                holder["body"] = response.text[:240]
            except Exception as exc:  # noqa: BLE001
                holder["status"] = f"exc:{type(exc).__name__}"

        thread = threading.Thread(target=blocked_run, daemon=True)
        thread.start()
        time.sleep(3.0)
        pending_a = a.get("/api/approvals").json().get("items", [])
        print(f"B) A 已挂起：实例 A 视角 pending＝{len(pending_a)} 条")
        if not pending_a:
            print(f"   run 已立即返回 {holder.get('status')}：{holder.get('body', '（无响应体，说明仍在挂起）')}")
            raise RuntimeError("实例 A 没有挂起审批，勘察无法继续")

        # 关键一步：A 仍持有这条活 pending 时，B 起来——启动钩子会替 B 重建同一个 pending。
        # RECON_SKIP_B=1 用于对照：只起一个实例，用来排除「单进程自己就把 run 跑了两遍」。
        if os.environ.get("RECON_SKIP_B") == "1":
            print("C) 对照模式：不起实例 B（RECON_SKIP_B=1），下面一行中的 B 即 A 自身")
            b = a
        else:
            processes.append(start_instance(B_PORT, "/tmp/recon_b.log"))
            b = client(B_PORT)
        pending_b = b.get("/api/approvals").json().get("items", [])
        time.sleep(6.0)
        pending_b_late = b.get("/api/approvals").json().get("items", [])
        same_token = any(item["token"] == pending_a[0]["token"] for item in pending_b_late)
        print(f"C) 实例 B 就绪：B 视角 pending 立即＝{len(pending_b)} 条、6s 后＝{len(pending_b_late)} 条，"
              f"同 token＝{same_token}")
        print(f"   interruptions 行数：A 视角 run 仍挂起中")

        decision = a.post(f"/api/approvals/{pending_a[0]['token']}/decision", json={"decision": "approved"})
        print(f"D) 一次审批决策（在 A 决策为 approved）→ {decision.status_code}")
        thread.join(timeout=25)
        # B 的续跑线程等的是自己进程里的 Event，A 的决策不会传过去；等它自己走超时分支
        print("   等 45s 跨过帧的 25s 超时，看 B 是否独立续跑第二遍")
        time.sleep(45.0)

        writes = counted_memories(b, MARK_OK) + counted_memories(b, MARK_NO)
        if os.environ.get("RECON_SKIP_B") != "1":
            print(f"   收尾采样：A 视角 pending＝{len(a.get('/api/approvals').json().get('items', []))} 条，"
                  f"B 视角 pending＝{len(b.get('/api/approvals').json().get('items', []))} 条")
        runs = b.get("/api/runs?limit=20").json().get("items", [])
        statuses = {}
        for run in runs:
            if run.get("graphId") == graph_id:
                statuses[run["runId"][:8]] = run["status"]
        print(f"E) 一次审批、一条 run，下游实际落库＝{writes} 次（{branch_counts(b)}）")
        print(f"   该图 run 终态：{statuses}")
        print(f"   A 侧同步运行返回：{holder.get('status', '仍挂起')}")

        if writes > 1:
            print(f"\n结论：同一条挂起 run 被两个进程各续跑一遍（{writes} 次副作用、分支还不一致）"
                  "——多实例＝重复执行且有分叉终态")
            exit_code = 1
        elif writes == 1:
            print("\n结论：本次只落 1 次，未复现双跑（需要看 B 侧日志确认续跑线程是否真的起了）")
        else:
            print("\n结论：一次都没落库，审批后的下游分支未执行，勘察口径需复核")
            exit_code = 1
    finally:
        for process in processes:
            process.send_signal(signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
