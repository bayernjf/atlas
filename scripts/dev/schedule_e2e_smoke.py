#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docs/68 §4 末条 —— 定时触发真机端到端冒烟（打包 N 收口证据）。

这条脚本存在的理由：docs/63 判否 N4 时说的那句话是"不能拿单测代替一次真跑"。所以这里
不起 TestClient、不注时钟、不假认领表——起真 uvicorn、等真分钟翻转、杀真进程再起来。

断言的契约（docs/68 §1）：
  场景 1（真的会自己跑）：发布一张 `* * * * *` 的图 ⇒ `/api/schedules` 出现登记 ⇒
    **不点任何东西**，一个分钟槽后 `runs` 里出现一条 completed 运行、`lastFiredAt` 跟上。
  场景 2（认领落库）：PG 档 `schedule_fires` 出现该槽位行（内存档测不到这条，因为它易失）。
  场景 3（**不补跑**）：停服 ≥130 秒（跨过 ≥2 个槽）再起 ⇒ 停摆期那几个分钟槽
    **既没有 run、也没有认领行**；恢复后只发当下这一个槽。
  场景 4（run-now 与撤销）：`run-now` 起得来；把定时节点改成手动再发布 ⇒ 登记被撤销、
    不再有新的派发（脚本自己建的图，自己收尾，不给开发库留一条每分钟乱跑的调度）。

用法：
  .venv/bin/python scripts/dev/schedule_e2e_smoke.py [--port 8160]

前置：本地 PG 在跑（默认 postgresql+psycopg://atlas:atlas@localhost:5432/atlas，
可用 DATABASE_URL 覆盖）。脚本自己跑迁移，不需要先手动 apply。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / ".venv" / "bin" / "python")
DEFAULT_DB = "postgresql+psycopg://atlas:atlas@localhost:5432/atlas"
TICK_SECONDS = "5"
# 停摆要跨过几个槽：130 秒保证至少错过 2 个分钟槽。
DOWNTIME_SECONDS = 130


def ok(cond: bool, msg: str) -> None:
    print(f"  {'OK   ' if cond else 'FAIL '} {msg}", flush=True)
    if not cond:
        raise AssertionError(msg)


class Server:
    """自管子进程：只记自己起的 PID，绝不按名字找别人的服务杀。"""

    def __init__(self, port: int, db_url: str) -> None:
        self.port = port
        self.db_url = db_url
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = dict(os.environ)
        env["DATABASE_URL"] = self.db_url
        env["ATLAS_STORAGE_BACKEND"] = "pg"
        env["ATLAS_SCHEDULE_ENABLED"] = "1"
        env["ATLAS_SCHEDULE_TICK_SECONDS"] = TICK_SECONDS
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "atlas.api.main:app",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._wait_ready()

    def _wait_ready(self, timeout: float = 60) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError("uvicorn exited during startup")
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/api/health", timeout=1).status_code == 200:
                    return
            except Exception:
                time.sleep(0.3)
        raise RuntimeError("server not ready in time")

    def stop(self) -> datetime:
        """杀掉自己起的进程，返回"确认死亡"的时刻（=停摆窗右边界）。"""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None
        return datetime.now(timezone.utc)


def slot_of(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0, tzinfo=timezone.utc)


def fired_slots(db_url: str, graph_id: str) -> list[datetime]:
    """直接读认领表：这是"哪个槽被派发过"的唯一权威，比列表接口更接近事实。"""
    import psycopg

    with psycopg.connect(db_url.replace("postgresql+psycopg", "postgresql")) as conn:
        rows = conn.execute(
            "SELECT slot_utc FROM schedule_fires WHERE graph_id = %s ORDER BY slot_utc",
            (graph_id,),
        ).fetchall()
    return [r[0].astimezone(timezone.utc).replace(second=0, microsecond=0) for r in rows]


def schedule_runs(client: httpx.Client, graph_id: str) -> list[dict]:
    return [r for r in client.get("/api/runs").json()["items"] if r.get("graphId") == graph_id]


def wait_for_new_run(client: httpx.Client, graph_id: str, known: int,
                     timeout: float) -> dict | None:
    end = time.time() + timeout
    while time.time() < end:
        runs = schedule_runs(client, graph_id)
        if len(runs) > known:
            return sorted(runs, key=lambda r: r.get("startedAt") or "")[-1]
        time.sleep(1.0)
    return None


def graph_with_trigger(cron: str, name: str, trigger_type: str = "schedule") -> dict:
    return {
        "version": 1,
        "name": name,
        "nodes": [
            {"id": "trig", "type": "trigger", "name": "定时",
             "config": {"triggerType": trigger_type, "cron": cron}},
            {"id": "dec", "type": "ai_decision", "name": "决策",
             "config": {"promptTemplate": "这笔退款要不要转人工？"}},
        ],
        "edges": [{"id": "e1", "source": "trig", "target": "dec"}],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="scheduled-trigger end-to-end smoke")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SMOKE_PORT", "8160")))
    args = parser.parse_args()
    db_url = os.environ.get("DATABASE_URL", DEFAULT_DB)

    subprocess.run(
        [PYTHON, "-m", "scripts.ops.apply_migrations"],
        cwd=str(ROOT), check=True,
        env={**os.environ, "DATABASE_URL": db_url},
        stdout=subprocess.DEVNULL,
    )
    print(f"迁移已 apply（含 030）；端口 {args.port}；库 {db_url.split('@')[-1]}")

    server = Server(args.port, db_url)
    name = f"smoke-schedule-{int(time.time())}"
    graph_id = ""
    try:
        server.start()
        client = httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=15)
        login = client.post("/api/auth/login",
                            json={"username": "admin-a", "password": "admin123"})
        ok(login.status_code == 200, f"admin-a 登录（{login.status_code}）")
        client.headers["Authorization"] = f"Bearer {login.json()['token']}"

        print("\n场景 1｜发布即登记，并且**没人点**也会自己跑")
        graph_id = client.post("/api/graphs", json=graph_with_trigger("* * * * *", name)).json()["id"]
        published = client.post(f"/api/graphs/{graph_id}/publish")
        version = published.json()["releaseVersion"]
        rows = client.get("/api/schedules").json()["items"]
        mine = next((r for r in rows if r["graphId"] == graph_id), None)
        ok(mine is not None, f"发布 v{version} 后 /api/schedules 出现该图")
        ok(mine["cron"] == "* * * * *" and mine["version"] == version, "登记内容＝发布号＋cron")
        ok(mine["nextFireAt"] is not None, f"nextFireAt 可算（{mine['nextFireAt']}）")

        first = wait_for_new_run(client, graph_id, 0, 90)
        ok(first is not None, "90 秒内出现第一条自动运行（调度线程自己发的）")
        ok(first["status"] == "completed", f"自动运行跑完（status={first['status']}）")
        fired_at = datetime.fromisoformat(first["startedAt"])
        print(f"       首条自动运行 {first['runId'][:8]} 起于 {fired_at.isoformat()}")

        print("\n场景 2｜槽位认领真的落库（PG 档才测得到这条）")
        slots = fired_slots(db_url, graph_id)
        ok(len(slots) >= 1, f"schedule_fires 有 {len(slots)} 行认领")
        ok(slot_of(fired_at) in slots or any(abs((s - slot_of(fired_at)).total_seconds()) <= 90 for s in slots),
           "认领槽位与那条 run 的分钟对得上")

        print("\n场景 3｜停服 130 秒：错过的槽既不补跑、也不留认领")
        before_stop = len(fired_slots(db_url, graph_id))
        before_runs = len(schedule_runs(client, graph_id))
        stop_at = server.stop()
        print(f"       {stop_at.strftime('%H:%M:%S')} 停服（kill 自己起的 PID），等 {DOWNTIME_SECONDS}s")
        time.sleep(DOWNTIME_SECONDS)
        missed_start = slot_of(stop_at) + timedelta(minutes=1)
        restart_at = datetime.now(timezone.utc)
        server.start()
        print(f"       {restart_at.strftime('%H:%M:%S')} 起来（当前分钟槽 {slot_of(restart_at).strftime('%H:%M')}）")
        after_run = wait_for_new_run(client, graph_id, before_runs, 90)
        ok(after_run is not None, "重启后当分钟槽照常派发（说明登记与认领都从库里活着回来）")
        recovered_slots = fired_slots(db_url, graph_id)
        in_window = [s for s in recovered_slots
                     if missed_start <= s < slot_of(restart_at)]
        print(f"       停摆窗 [{missed_start.strftime('%H:%M')},"
              f" {slot_of(restart_at).strftime('%H:%M')}) 内的认领行 = {[s.strftime('%H:%M') for s in in_window]}")
        ok(not in_window, f"停摆期间的 {len(in_window)} 个槽位被补跑了（应为 0）")
        ok(len(recovered_slots) > before_stop, "认领表在重启后继续增长（不是只读到旧数据）")

        print("\n场景 4｜run-now 起得来；改成手动再发布 ⇒ 撤销登记")
        runs_before = {r["runId"] for r in schedule_runs(client, graph_id)}
        run_now = client.post(f"/api/schedules/{graph_id}/run-now")
        ok(run_now.status_code == 200, f"run-now 200（实得 {run_now.status_code}）")
        manual_run_id = run_now.json()["runId"]
        ok(manual_run_id not in runs_before, "run-now 给的是一个新 runId")
        deadline = time.time() + 30
        manual_run = None
        while time.time() < deadline:
            manual_run = next(
                (r for r in schedule_runs(client, graph_id) if r["runId"] == manual_run_id),
                None,
            )
            if manual_run is not None:
                break
            time.sleep(1.0)
        ok(manual_run is not None, "run-now 那条 run 真出现在 /api/runs 里")
        ok(manual_run["status"] in ("running", "suspended", "completed", "failed"),
           f"run-now 的 run 有可查状态（{manual_run['status']}）")
        manual = graph_with_trigger("* * * * *", name, trigger_type="manual")
        client.put(f"/api/graphs/{graph_id}", json=manual)
        client.post(f"/api/graphs/{graph_id}/publish")
        ok(next((r for r in client.get("/api/schedules").json()["items"]
                 if r["graphId"] == graph_id), None) is None,
           "撤掉定时节点再发布后，登记随之撤销（不给开发库留每分钟乱跑的调度）")

        print("\n冒烟全过 ✅")
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
