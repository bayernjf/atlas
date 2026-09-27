#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docs/76 §4 —— 挂起点只读投影的真进程探测（打包 Q 的 〔跑〕 证据）。

为什么又写一个脚本而不是只留测试：本批全部价值在一句"崩在续跑途中的 run 终于查得到"，
而这句话有两个失败方向都不在单测的视野里——
① **档位诚实**：内存档下帧表根本不写，端点回一个空列表就足够让运维汇报"没有卡住的 run"；
② **真库语义**：常跑测里的帧是我自己写的字典，它证明不了那条 `WHERE tenant_id` 在挡什么，
   也证明不了 `TIMESTAMPTZ` 的认领时刻读出来还是同一个时刻。

三段：
  1. `ATLAS_STORAGE_BACKEND=memory` ⇒ 端点 200、`visibility=frames-not-persisted`，
     且**不**伪装成一次健康的空查询。
  2. PG 档：用**产品自己的** `make_frame_sink`/`claim_frame_for_resume`/`PgRunsStore` 写真帧
     与真认领，再经 HTTP 投影 ⇒ t1 看到自己的 awaiting 与 `claimed_suspended`，看不到 t2 的；
     t2 只看到自己的。无凭证 401。
  3. 只读性：投影跑两遍，库里帧的 `resumed_at/resumed_by` 一字不差。
  4. **活进程内审批通过**（真图真运行，常见路径）⇒ 运行到终态时它的帧必须一起了结。
     这段是 2026-09-27 实测逼出来的：当时只有"启动恢复后续跑成功"会清帧，所以每次正常
     审批都永久留一行，把这张表淹成"健康运行的墓地"。只用 uvicorn 子进程测不到它。

用法：
    .venv/bin/python scripts/dev/interruptions_probe.py --database-url postgresql+psycopg://…
不给 `--database-url`（也不给环境变量 DATABASE_URL）时，第 2/3 段跳过并以**非零退出**收场——
跳过自己最强的那半边不该被读成通过。加 `--allow-skip-pg` 才允许 0 退出。
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from atlas.storage.pg import PgRunsStore
from atlas.storage.recovery import claim_frame_for_resume, make_frame_sink

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / ".venv" / "bin" / "python")


def ok(condition: bool, message: str) -> None:
    print(f"  {'OK   ' if condition else 'FAIL '} {message}", flush=True)
    if not condition:
        raise AssertionError(message)


def _key() -> str:
    """主密钥/HMAC 密钥：环境变量串 ≥32 是闸门，`ATLAS_MASTER_KEY` 另需 base64url
    解码后恰 32 字节（`security/secrets.py:84`），所以统一按最严的生成。"""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")


class Server:
    def __init__(self, port: int) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None

    def start(self, env_overrides: dict[str, str]) -> None:
        env = dict(os.environ)
        env.update({
            "ATLAS_MASTER_KEY": _key(),
            "ATLAS_APPROVAL_HMAC_SECRET": _key(),
            "ATLAS_SCHEDULE_ENABLED": "0",
            "ATLAS_STORAGE_BACKEND": "memory",
            **env_overrides,
        })
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "atlas.api.main:app",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                tail = (self.proc.communicate()[0] or "")[-2500:]
                raise RuntimeError(f"服务没起来，uvicorn 输出尾部：\n{tail}")
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/api/health", timeout=1).status_code == 200:
                    return
            except Exception:
                time.sleep(0.3)
        self.stop()
        raise RuntimeError("服务 60 秒内没就绪")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


def _login(base: str, username: str, password: str) -> dict[str, str]:
    response = httpx.post(f"{base}/api/auth/login",
                          json={"username": username, "password": password}, timeout=10)
    ok(response.status_code == 200, f"{username} 登录应 200（实得 {response.status_code}）")
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _rows(base: str, headers: dict[str, str] | None = None):
    response = httpx.get(f"{base}/api/interruptions", headers=headers or {}, timeout=10)
    return response


@contextmanager
def _engine(url: str):
    """短生命周期连接，正常退出时提交（探针里的删除要真的落下去）。"""
    eng = create_engine(url)
    try:
        with eng.begin() as conn:
            yield conn
    finally:
        eng.dispose()


def _apply_migrations(engine: Engine) -> None:
    for path in sorted((ROOT / "db" / "migrations").glob("*.sql")):
        statements: list[str] = []
        current: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            current.append(line)
            if stripped.endswith(";"):
                statements.append("\n".join(current))
                current = []
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))


def _seed(engine: Engine, suffix: str) -> dict[str, str]:
    """真写帧、真认领、真 run 状态——全用产品自己的函数，不手写行。"""
    ids = {
        "t1_unclaimed": f"qprobe-{suffix}-t1-a",
        "t1_claimed": f"qprobe-{suffix}-t1-b",
        "t2_frame": f"qprobe-{suffix}-t2-c",
    }
    plan = [("t1", "r" + ids["t1_unclaimed"], ids["t1_unclaimed"]),
            ("t1", "r" + ids["t1_claimed"], ids["t1_claimed"]),
            ("t2", "r" + ids["t2_frame"], ids["t2_frame"])]
    for tenant, run_id, token in plan:
        make_frame_sink(engine, tenant, run_id)({
            "resume_token": token,
            "node_id": "approval-1",
            "kind": "approval",
            "deadline_at": None,
            "resume_state": {"graph_id": f"g-{suffix}"},
        })
        runs = PgRunsStore(engine, tenant)
        runs.begin(run_id=run_id, graph_id=f"g-{suffix}", mode="api")
        runs.suspend(run_id=run_id, node_id="approval-1", kind="approval",
                     resume_token=token, deadline_at=None)
    ok(claim_frame_for_resume(engine, ids["t1_claimed"], "qprobe-control") is True,
       "真认领成功（否则第 2 段的现场是假的）")
    return {"runs": ",".join(run for _, run, _ in plan), **ids}


def _cleanup(engine: Engine, seeded: dict[str, str]) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM interruptions WHERE resume_token = ANY(:tokens)"),
                     {"tokens": [seeded["t1_unclaimed"], seeded["t1_claimed"], seeded["t2_frame"]]})
        conn.execute(text("DELETE FROM runs WHERE id = ANY(:ids)"),
                     {"ids": [f"r{seeded[key]}" for key in ("t1_unclaimed", "t1_claimed", "t2_frame")]})


def _frame_snapshot(engine: Engine, seeded: dict[str, str]) -> list[tuple]:
    with engine.connect() as conn:
        return sorted(
            (row[0], str(row[1]), row[2])
            for row in conn.execute(
                text("SELECT resume_token, resumed_at, resumed_by FROM interruptions "
                     "WHERE resume_token = ANY(:tokens)"),
                {"tokens": [seeded["t1_unclaimed"], seeded["t1_claimed"], seeded["t2_frame"]]},
            ).all()
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8241)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--allow-skip-pg", action="store_true")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    print("1｜内存档：端点必须自报看不见")
    server = Server(args.port)
    server.start({"ATLAS_STORAGE_BACKEND": "memory"})
    try:
        viewer = _login(base, "viewer-a", "viewer123")
        body = _rows(base, viewer).json()
        ok(body["backend"] == "memory", f"backend 应为 memory（实得 {body['backend']}）")
        ok(body["visibility"] == "frames-not-persisted",
           f"内存档必须回 frames-not-persisted，而不是一个健康的空列表（实得 {body.get('visibility')}）")
        ok(body["items"] == [], "内存档不该凭空造出帧")
    finally:
        server.stop()

    if not args.database_url:
        print("\n2/3｜PG 段：SKIPPED（没给 --database-url / DATABASE_URL）")
        print("     ⇒ 本探测只证到内存档那半边；租户隔离与真认领时刻都还没验。")
        if args.allow_skip_pg:
            print("\n按 --allow-skip-pg 允许跳过，退出 0")
            return 0
        return 1

    print("2｜PG 档：真写帧＋真认领之后，投影里必须看得见")
    engine = create_engine(args.database_url)
    _apply_migrations(engine)
    suffix = uuid.uuid4().hex[:8]
    server = Server(args.port)
    server.start({"ATLAS_STORAGE_BACKEND": "pg", "DATABASE_URL": args.database_url})
    # **先起服务再播种**是必须的：启动恢复会把"未认领"的帧当成待续跑的真中断，替它起一条
    # 续跑线程；我们的合成帧没有 graph_snapshot，那次续跑必然失败 ⇒ 运行落 failed ⇒
    # 帧按"运行到终态即了结其帧"一并清掉（这条规则见 docs/76 §7 的 R 追记）。反过来先播种，第 2 段就会看见少一行（实测踩过）。
    seeded = _seed(engine, suffix)
    try:
        ok(_rows(base).status_code == 401, "无凭证必须 401")
        viewer_a = _login(base, "viewer-a", "viewer123")
        admin_b = _login(base, "admin-b", "admin123")
        a_body = _rows(base, viewer_a).json()
        ok(a_body["backend"] == "pg" and a_body["visibility"] == "tenant-scoped",
           f"PG 档标记＝pg/tenant-scoped（实得 {a_body.get('backend')}/{a_body.get('visibility')}）")
        by_token = {row["resumeToken"]: row for row in a_body["items"]}
        ok(set(by_token) >= {seeded["t1_unclaimed"], seeded["t1_claimed"]},
           f"本租户两条帧都该在（实得 {sorted(by_token)}）")
        ok(seeded["t2_frame"] not in by_token, "t2 的帧没漏进 t1 的投影")
        ok(by_token[seeded["t1_unclaimed"]]["state"] == "awaiting", "未认领帧档位＝awaiting")
        ok(by_token[seeded["t1_claimed"]]["state"] == "claimed_suspended",
           "已认领却仍 suspended ⇒ claimed_suspended（那颗雷显形了）")
        ok(by_token[seeded["t1_claimed"]]["claimedBy"] == "qprobe-control",
           "认领者读得出来（resumed_by 链路完好）")
        claimed_at = by_token[seeded["t1_claimed"]]["claimedAt"]
        ok(claimed_at is not None and "+00:00" in claimed_at,
           f"认领时刻是 UTC ISO：{claimed_at}")
        b_rows = _rows(base, admin_b).json()["items"]
        ok([row["resumeToken"] for row in b_rows] == [seeded["t2_frame"]],
           f"t2 只能看见自己那条（实得 {[r['resumeToken'] for r in b_rows]}）")
        ok(all("tenantId" not in row for row in a_body["items"]),
           "投影不回显 tenant id（没有可猜的参数）")

        print("3｜只读性：投影跑两遍，认领状态一字不差")
        before = _frame_snapshot(engine, seeded)
        _rows(base, viewer_a)
        _rows(base, admin_b)
        ok(_frame_snapshot(engine, seeded) == before, "两遍投影之间帧的认领状态未变（真只读）")
    finally:
        server.stop()
        _cleanup(engine, seeded)
        with engine.connect() as conn:
            left = conn.execute(
                text("SELECT count(*) FROM interruptions WHERE resume_token LIKE :p"),
                {"p": f"qprobe-{suffix}-%"},
            ).scalar()
        ok(left == 0, f"探针自己写的帧已全部删除（残留 {left} 行）")
        print(f"  OK    清理完成：本探针写的 {len(seeded) - 1} 帧＋对应 run 已全部删除")

    print("4｜活进程内审批通过（**常见路径**）⇒ 运行到终态时它的帧必须一起了结")
    _live_approval_flow_clears_frames(args.database_url)

    print("\n探测全过 ✅")
    return 0


def _live_approval_graph(name: str) -> dict:
    return {
        "version": 1, "name": name, "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/qprobe"}},
            {"id": "human-1", "type": "human_approval", "name": "人工审批",
             "config": {"summary": f"{name} 审批", "approver": "客服主管", "timeoutSeconds": 60,
                        "onTimeout": "reject", "approvedTarget": "tool-approve",
                        "rejectedTarget": "tool-reject"}},
            {"id": "tool-approve", "type": "tool_call", "name": "通过侧",
             "config": {"tool": "op-approve"}},
            {"id": "tool-reject", "type": "tool_call", "name": "拒绝侧",
             "config": {"tool": "op-reject"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "human-1"},
            {"id": "e2", "source": "human-1", "target": "tool-approve"},
            {"id": "e3", "source": "human-1", "target": "tool-reject"},
        ],
    }


def _live_approval_flow_clears_frames(database_url: str) -> None:
    """真起一次"审批在同一个进程里被决定"的运行——2026-09-27 就是这条路把帧表淹掉的。

    只用 uvicorn 子进程测不到它：那清的是"启动恢复后续跑成功"那条少见的清帧路径。
    """
    import threading

    os.environ["ATLAS_STORAGE_BACKEND"] = "pg"
    os.environ["DATABASE_URL"] = database_url
    os.environ["ATLAS_MASTER_KEY"] = _key()
    os.environ["ATLAS_APPROVAL_HMAC_SECRET"] = _key()
    os.environ["ATLAS_SCHEDULE_ENABLED"] = "0"
    from fastapi.testclient import TestClient

    from atlas.api.main import app

    name = f"qprobe-live-{uuid.uuid4().hex[:8]}"
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    ok(login.status_code == 200, f"admin-a 登录应 200（实得 {login.status_code}）")
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    graph_id = client.post("/api/graphs", json=_live_approval_graph(name), headers=headers).json()["id"]

    outcome: dict = {}

    def do_run() -> None:
        outcome["resp"] = client.post(f"/api/graphs/{graph_id}/run",
                                      json={"inputs": {"order_id": name}}, headers=headers)

    thread = threading.Thread(target=do_run, daemon=True)
    thread.start()
    token = None
    for _ in range(120):
        time.sleep(0.25)
        pending = client.get("/api/approvals", headers=headers).json().get("items", [])
        hit = [a for a in pending if name in str(a.get("summary", ""))]
        if hit:
            token = hit[0]["token"]
            break
    ok(token is not None, "等到挂起的审批了（等不到＝常见路径没走通，本段结论无效）")
    with _engine(database_url) as conn:
        during = conn.execute(text("SELECT count(*) FROM interruptions WHERE resume_token = :t"),
                              {"t": token}).scalar()
    ok(during == 1, f"挂起中应当有 1 帧（实得 {during}）")

    decision = client.post(f"/api/approvals/{token}/decision",
                           json={"decision": "approved", "comment": "qprobe"}, headers=headers)
    ok(decision.status_code == 200, f"决策应 200（实得 {decision.status_code}）")
    thread.join(timeout=30)
    resp = outcome.get("resp")
    ok(resp is not None and resp.status_code == 200 and resp.json().get("status") == "completed",
       f"运行该正常完成（实得 {resp.status_code if resp else '线程没结束'}）")
    with _engine(database_url) as conn:
        after = conn.execute(text("SELECT count(*) FROM interruptions WHERE resume_token = :t"),
                             {"t": token}).scalar()
        run_row = conn.execute(text("SELECT status FROM runs WHERE graph_id = :g"),
                               {"g": graph_id}).scalar()
    ok(after == 0, f"运行到终态后它的帧已了结（实得残留 {after} 行；不清就会把审批通过记成雷）")
    ok(run_row == "completed", f"run 终态应为 completed（实得 {run_row}）")
    with _engine(database_url) as conn:
        conn.execute(text("DELETE FROM runs WHERE graph_id = :g"), {"g": graph_id})
        conn.execute(text("DELETE FROM graphs WHERE id = :g"), {"g": graph_id})
    print("  OK    本段建的图与运行已删除（帧由代码自己清，探针只验它没了）")


if __name__ == "__main__":
    sys.exit(main())
