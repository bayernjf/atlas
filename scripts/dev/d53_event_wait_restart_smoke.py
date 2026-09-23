#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docs/53 wait 事件跨重启持久化 v1 —— 真实 HTTP 重启冒烟（PG 单实例）。

脚本自管 uvicorn 子进程的启动 / kill / 重启，断言契约（docs/53 §1/§5）：

  场景 1（直投）：event wait 挂起并落 kind=wait 中断帧 → kill 服务 → 重启后
    GET /api/waits 仍见同一 token（recover 经 EventWaitBroker.restore 重注册、
    超时只计剩余）→ POST /api/waits/{token}/signal 直投 → run completed、
    wait-1 resolvedBy=signal、后继节点执行、帧清。
  场景 2（广播）：同形挂起 → 重启 → POST /api/waits/events 按 eventKey 广播 → run completed。
  场景 3（超时 fail）：onTimeout=fail、timeoutSeconds=1 → 挂起 → 重启 →
    不发信号、剩余超时到点 → run failed（WAIT_TIMEOUT_FAILED）。

非目标（仍缓做）：多实例跨进程信号路由、停摆期信号排队、公开免登录信号口。

用法：
  .venv/bin/python scripts/dev/d53_event_wait_restart_smoke.py [--port 8150]

前置：docker 容器 atlas-pg 在跑（DATABASE_URL 可经环境覆盖）。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / ".venv" / "bin" / "python")
DEFAULT_DB = "postgresql+psycopg://atlas:atlas@localhost:5432/atlas"


def ok(cond: bool, msg: str) -> None:
    print(f"  {'OK ' if cond else 'FAIL '} {msg}")
    if not cond:
        raise AssertionError(msg)


def event_wait_graph(event_key: str, *, on_timeout: str = "continue", timeout: int = 30) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "等待",
             "position": {"x": 2, "y": 0},
             "config": {"waitType": "event", "eventKey": event_key,
                        "timeoutSeconds": timeout, "onTimeout": on_timeout}},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "op-after"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "wait-1"},
            {"id": "e2", "source": "wait-1", "target": "tool-after"},
        ],
    }


class Server:
    def __init__(self, port: int, db_url: str) -> None:
        self.port = port
        self.db_url = db_url
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = dict(os.environ)
        env["DATABASE_URL"] = self.db_url
        env["ATLAS_STORAGE_BACKEND"] = "pg"
        env.pop("ATLAS_RUN_INTEGRATION", None)
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "atlas.api.main:app",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._wait_ready()

    def _wait_ready(self, timeout: float = 30) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError("uvicorn exited during startup")
            try:
                resp = httpx.get(f"http://127.0.0.1:{self.port}/openapi.json", timeout=1)
                if resp.status_code == 200:
                    return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("server not ready within timeout")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


def login(cli: httpx.Client) -> None:
    resp = cli.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    ok(resp.status_code == 200, f"admin-a 登录 {resp.status_code}")
    cli.headers["Authorization"] = f"Bearer {resp.json()['token']}"


def start_run_and_suspend(cli: httpx.Client, graph: dict, event_key: str) -> tuple[str, str, str]:
    """建图并在后台线程发起同步 /run（会阻塞在 event wait），轮询直到挂起：
    返回 (graph_id, wait_token, run_id)。"""
    gid = cli.post("/api/graphs", json=graph).json()["id"]

    def fire() -> None:
        try:
            cli.post(f"/api/graphs/{gid}/run", json={"inputs": {}})
        except Exception:
            pass  # kill / 重启时连接中断，预期异常

    threading.Thread(target=fire, daemon=True).start()

    end = time.time() + 15
    while time.time() < end:
        waits = cli.get("/api/waits").json()["items"]
        suspended = cli.get("/api/runs", params={"status": "suspended"}).json()["items"]
        hit_wait = next((item for item in waits if item["eventKey"] == event_key), None)
        hit_run = next((item for item in suspended if item["graphId"] == gid), None)
        if hit_wait is not None and hit_run is not None:
            return gid, hit_wait["token"], hit_run["runId"]
        time.sleep(0.1)
    raise AssertionError("event wait 未在超时前挂起")


def wait_run_terminal(cli: httpx.Client, run_id: str, timeout: float = 12) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        run = cli.get(f"/api/runs/{run_id}").json()
        if run["status"] not in {"running", "suspended"}:
            return run
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内到终态")


def scenario_direct_signal(port: int, db_url: str) -> None:
    print("场景 1：重启后按 token 直投信号放行")
    key = f"evt-direct-{int(time.time() * 1000) % 100000}"
    srv = Server(port, db_url)
    srv.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as cli:
            login(cli)
            gid, token, run_id = start_run_and_suspend(cli, event_wait_graph(key), key)
    finally:
        srv.stop()

    srv.start()  # 重启
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as cli:
            login(cli)
            items = cli.get("/api/waits").json()["items"]
            ok(any(item["token"] == token for item in items), "重启后同一 wait token 仍在")
            resp = cli.post(f"/api/waits/{token}/signal", json={"payload": {"paidAt": "2026-09-23"}})
            ok(resp.status_code == 200, f"直投信号 {resp.status_code}")
            run = wait_run_terminal(cli, run_id)
            ok(run["status"] == "completed", "run completed")
            out = run["outputs"]["wait-1"]
            ok(out["resolvedBy"] == "signal" and out["signaled"] is True,
               "wait-1 resolvedBy=signal")
            ok(out["payload"] == {"paidAt": "2026-09-23"}, "信号 payload 透传")
            ok("tool-after" in run["outputs"], "尾图后继节点已执行")
            ok(cli.get("/api/waits").json()["items"] == [], "完成后 pending 已清")
    finally:
        srv.stop()


def scenario_broadcast(port: int, db_url: str) -> None:
    print("场景 2：重启后按 eventKey 广播放行")
    key = f"evt-broadcast-{int(time.time() * 1000) % 100000}"
    srv = Server(port, db_url)
    srv.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as cli:
            login(cli)
            gid, token, run_id = start_run_and_suspend(cli, event_wait_graph(key), key)
    finally:
        srv.stop()

    srv.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as cli:
            login(cli)
            resp = cli.post("/api/waits/events", json={"eventKey": key, "payload": {"x": 1}})
            ok(resp.status_code == 200 and resp.json().get("released") == 1,
               f"广播 released=1（{resp.status_code} {resp.text if resp.status_code != 200 else ''}）")
            run = wait_run_terminal(cli, run_id)
            ok(run["status"] == "completed", "广播后 run completed")
            ok(run["outputs"]["wait-1"]["resolvedBy"] == "signal", "resolvedBy=signal")
    finally:
        srv.stop()


def scenario_timeout_fail(port: int, db_url: str) -> None:
    print("场景 3：重启后剩余超时到点、onTimeout=fail → run failed")
    key = f"evt-fail-{int(time.time() * 1000) % 100000}"
    srv = Server(port, db_url)
    srv.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as cli:
            login(cli)
            gid, _token, run_id = start_run_and_suspend(
                cli, event_wait_graph(key, on_timeout="fail", timeout=3), key
            )
    finally:
        srv.stop()

    srv.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as cli:
            login(cli)
            run = wait_run_terminal(cli, run_id, timeout=10)  # 不发信号、等到点
            ok(run["status"] == "failed", "剩余超时到点 run failed")
            ok("WAIT_TIMEOUT_FAILED" in (run.get("error") or ""),
               f"错误码 WAIT_TIMEOUT_FAILED（{run.get('error')}）")
    finally:
        srv.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8150)
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    args = parser.parse_args()

    try:
        scenario_direct_signal(args.port, args.db_url)
        scenario_broadcast(args.port + 1, args.db_url)
        scenario_timeout_fail(args.port + 2, args.db_url)
    except Exception as exc:
        print(f"SMOKE FAILED: {type(exc).__name__}: {exc}")
        return 1
    print("ALL SCENARIOS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
