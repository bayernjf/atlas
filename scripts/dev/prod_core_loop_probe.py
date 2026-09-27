#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第六次项目级上线复审（docs/77）的 〔跑〕 证据：prod 形态下把**核心闭环**真跑一遍。

前五次复审的教训是"绿灯不等于路径可走"（docs/63 §0A 的 N1/N2、docs/76 §8 的帧残留都是
这么抓出来的）。本脚本不问"闸门存不存在"，只问一个运营体在 `ATLAS_ENV=prod` 里按
PRD 的方式跑一条退款，**它动的是不是真实世界**，以及如果是假的，它看不看得出来。

五段：
  1. prod＋PG（一次性探针库）起真进程，引导口令登进 admin。
  2. 用**内置 golden 模板 `refund-auto`**（不是本脚本手搓的图）跑一条退款：trigger →
     ai_decision → `shop/process_refund`。打印每个节点原文。
  3. `database/query` 在 prod、未配 `ATLAS_DATABASE_URL` 时打到哪儿——打印行数与内容，
     并打印 `/api/adapters` 里运营能看到的那份连接投影。
  4. `message/send`（channel=sms，v1 只进程内记录）报什么状态；然后**在 prod 里找那条
     记录**：唯一查看面 `/api/demo/messages` 是打包 P 关掉的 404。
  5. 正向对照：同一张图同一份入参，在 `ATLAS_ENABLE_DEMO_MOCK=1` 下查看面回到 200
     ——证明第 4 段那条 404 是档位行为，不是探测姿势错了。

退出码：探测自身的完整性（起不来/登不进/运行没到终态）非 0。**观察到的替换不判红**
——那是本次评审要拿去写结论的证据，不是本脚本要守的断言。

密钥与口令都是本机一次性随机值，不是任何真实凭据。探针库名固定 `atlas_review6`，
脚本结束时自己删；不碰开发库 `atlas`。

用法：.venv/bin/python scripts/dev/prod_core_loop_probe.py [--port 8191]
                                                [--keep-db] [--database-url URL]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / ".venv" / "bin" / "python")
PROBE_DB = "atlas_review6"
BOOTSTRAP_PASSWORD = "Atlas-Review6-" + uuid.uuid4().hex[:12] + "-not-secret"
ADMIN_USERNAME = "admin-a"


def say(label: str, msg: str) -> None:
    print(f"  {label} {msg}", flush=True)


def ok(condition: bool, msg: str) -> None:
    say("OK   " if condition else "FAIL ", msg)
    if not condition:
        raise AssertionError(msg)


def observed(msg: str) -> None:
    say("观 测", msg)


def dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _key() -> str:
    """master key 要 base64url 解码后恰 32 字节（`security/secrets.py`），所以按最严的生成。"""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")


def _admin_url(database_url: str) -> str:
    """把 `.../atlas_review6` 换成 `.../postgres`，用来建/删探针库。"""
    return database_url.rsplit("/", 1)[0] + "/postgres"


def prepare_database(database_url: str) -> None:
    import sqlalchemy as sa

    admin = sa.create_engine(_admin_url(database_url), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": PROBE_DB}
        ).first()
        if exists is not None:
            raise SystemExit(
                f"探针库 {PROBE_DB} 已存在——本脚本只用自己建的库，拒绝在别人的库上跑。"
            )
        conn.execute(sa.text(f'CREATE DATABASE "{PROBE_DB}"'))
    admin.dispose()
    apply_migrations(database_url)


def apply_migrations(database_url: str) -> None:
    subprocess.run(
        [PYTHON, "-m", "scripts.ops.apply_migrations"],
        cwd=str(ROOT),
        env={**os.environ, "DATABASE_URL": database_url, "ATLAS_ENV": "dev"},
        check=True,
        capture_output=True,
        text=True,
    )


def drop_database(database_url: str) -> None:
    import sqlalchemy as sa

    admin = sa.create_engine(_admin_url(database_url), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{PROBE_DB}"'))
    admin.dispose()


def count_rows(database_url: str, table: str) -> int:
    import sqlalchemy as sa

    engine = sa.create_engine(database_url)
    with engine.connect() as conn:
        value = conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar()
    engine.dispose()
    return int(value or 0)


class Server:
    def __init__(self, port: int, database_url: str) -> None:
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.database_url = database_url
        self.proc: subprocess.Popen | None = None

    def start(self, *, demo_mock: bool = False) -> None:
        env = dict(os.environ)
        for stale in ("ATLAS_ENV", "ATLAS_ENABLE_DEMO_MOCK", "LITELLM_MODEL", "ATLAS_DATABASE_URL"):
            env.pop(stale, None)  # 先清继承，再让本脚本说话
        env.update({
            "ATLAS_ENV": "prod",
            "ATLAS_STORAGE_BACKEND": "pg",
            "DATABASE_URL": self.database_url,
            "ATLAS_MASTER_KEY": _key(),
            "ATLAS_APPROVAL_HMAC_SECRET": _key(),
            "ATLAS_ADMIN_BOOTSTRAP_PASSWORD": BOOTSTRAP_PASSWORD,
            "ATLAS_SCHEDULE_ENABLED": "0",
            "PLAYWRIGHT_HEADLESS": "1",
        })
        if demo_mock:
            env["ATLAS_ENABLE_DEMO_MOCK"] = "1"
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "atlas.api.main:app",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        # 本机 load 曾到 300+（同机并行会话），uvicorn 导 langgraph/litellm 会很慢；
        # 等不到就把子进程输出尾巴交出来，别让下一个人重猜是崩了还是慢了。
        deadline = time.time() + 240
        while time.time() < deadline:
            if self.proc.poll() is not None:
                tail = (self.proc.communicate(timeout=5)[0] or "")[-3000:]
                raise RuntimeError(f"prod 档进程没起来，uvicorn 输出尾部：\n{tail}")
            try:
                if httpx.get(f"{self.base}/api/health", timeout=2).status_code == 200:
                    return
            except Exception:
                time.sleep(0.4)
        tail = ""
        if self.proc is not None:
            try:
                self.proc.terminate()
                tail = (self.proc.communicate(timeout=10)[0] or "")[-3000:]
            except Exception:
                tail = "<读不到子进程输出>"
        self.proc = None
        raise RuntimeError(f"prod 档进程 240s 内没就绪，uvicorn 输出尾部：\n{tail}")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


def graph_payload(name: str, nodes: list[dict], edges: list[dict]) -> dict:
    return {"version": 1, "name": name, "variables": [], "nodes": nodes, "edges": edges}


def tool_graph(name: str, tool: str, params: str) -> dict:
    return graph_payload(
        name,
        [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": f"/hooks/{name}"}},
            {"id": "tool-1", "type": "tool_call", "name": "工具",
             "config": {"tool": tool, "params": params}},
        ],
        [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    )


def run_graph(server: Server, headers: dict, graph_id: str, inputs: dict) -> dict:
    response = httpx.post(f"{server.base}/api/graphs/{graph_id}/run",
                          json={"inputs": inputs}, headers=headers, timeout=90)
    ok(response.status_code == 200, f"POST /api/graphs/{graph_id}/run 应 200（实得 {response.status_code}："
                                    f"{response.text[:200]}）")
    return response.json()


def save_graph(server: Server, headers: dict, payload: dict) -> str:
    response = httpx.post(f"{server.base}/api/graphs", json=payload, headers=headers, timeout=30)
    ok(response.status_code in (200, 201),
       f"保存图应成功（实得 {response.status_code}：{response.text[:300]}）")
    return response.json()["id"]


def node_output(body: dict, node_id: str) -> dict:
    return (body.get("outputs") or {}).get(node_id) or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="prod-form core-loop probe")
    parser.add_argument("--port", type=int, default=8191)
    parser.add_argument("--keep-db", action="store_true", help="保留探针库（默认跑完删）")
    parser.add_argument(
        "--database-url",
        default="postgresql+psycopg://atlas:atlas@127.0.0.1:5432/atlas_review6",
    )
    args = parser.parse_args()
    database_url = args.database_url
    findings: dict[str, object] = {}

    print(f"探针库：{database_url}")
    prepare_database(database_url)
    server = Server(args.port, database_url)
    try:
        # ---------------------------------------------------------------- 1
        print("\n1｜prod＋PG 真进程：登得进吗")
        server.start()
        login = httpx.post(f"{server.base}/api/auth/login",
                           json={"username": ADMIN_USERNAME, "password": BOOTSTRAP_PASSWORD},
                           timeout=15)
        ok(login.status_code == 200, f"引导口令登录应 200（实得 {login.status_code}）")
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        say("--", f"档位 prod／后端 pg／一次性引导口令可登（N1 复验）")

        # ---------------------------------------------------------------- 2
        print("\n2｜核心闭环：内置 golden 模板 refund-auto 跑一条退款")
        tpl = httpx.get(f"{server.base}/api/templates/refund-auto", headers=headers, timeout=15)
        ok(tpl.status_code == 200, f"模板 refund-auto 应 200（实得 {tpl.status_code}）")
        refund_graph = tpl.json()["graph"]
        refund_graph["name"] = f"review6-refund-{uuid.uuid4().hex[:8]}"
        refund_id = save_graph(server, headers, refund_graph)
        body = run_graph(server, headers, refund_id,
                         {"order_id": "202606270001", "reason": "商品破损", "amount": 39.9})
        ok(body.get("status") == "completed",
           f"退款图应到 completed（实得 {body.get('status')}／{str(body)[:200]}）")
        decision = node_output(body, "ai_decision-1")
        refund = node_output(body, "tool_call-1")
        say("--", f"ai_decision 输出：{dump(decision)[:260]}")
        say("--", f"shop/process_refund 输出：{dump(refund)[:260]}")
        findings["decision_source"] = (
            (decision.get("decision") or {}).get("source")
            or decision.get("source")
            or (decision.get("result") or {}).get("source")
        )
        findings["shop_result"] = refund
        refund_result = refund.get("result") if isinstance(refund.get("result"), dict) else refund
        shop_success = str((refund_result or {}).get("status", "")).upper() not in {"FAILED", ""}
        observed(f"prod 下 PRD 旗舰链跑到终态：decision.source="
                 f"{findings['decision_source']!r}，shop/process_refund 是否报成功={shop_success}")
        observed("→ 该 `shop` 适配器是 `api/main.py:345` 无条件注册的进程内 "
                 "`DemoShopService`；prod 门（打包 P）关的是 demo 的 **HTTP 路由面**，"
                 "图内的演示适配器不在其管辖内")
        ok(count_rows(database_url, "runs") >= 1, "运行应落进 pg 档 runs 表（持久化面复验）")

        # ---------------------------------------------------------------- 3
        print("\n3｜数据适配器：prod 且未配 ATLAS_DATABASE_URL 时，查的是谁的库")
        sql = json.dumps({"sql": "SELECT order_id, item, amount FROM orders ORDER BY amount DESC"})
        db_id = save_graph(server, headers,
                           tool_graph(f"review6-db-{uuid.uuid4().hex[:8]}", "database/query", sql))
        db_body = run_graph(server, headers, db_id, {"order_id": "unused"})
        rows = node_output(db_body, "tool-1")
        say("--", f"database/query 输出：{dump(rows)[:400]}")
        findings["database_query"] = rows
        adapters = httpx.get(f"{server.base}/api/adapters", headers=headers, timeout=15).json()
        db_view = next((a for a in adapters if a.get("id") == "database"), {})
        shop_view = next((a for a in adapters if a.get("id") == "shop"), {})
        observed(f"运营侧 `GET /api/adapters` 里 database 的投影：{dump(db_view)[:260]}")
        observed(f"shop 的投影（「可发现性」只靠这条，运行结果里没有演示标记）：{dump(shop_view)[:300]}")
        observed("→ `api/main.py:338-340`：`ATLAS_DATABASE_URL` 未配即回退内置 "
                 "`demo_engine()`（SQLite 内存库，`database/service.py:42` 播两笔演示订单）；"
                 "compose 未设该变量（`docker-compose.yml:50-56`）")

        # ---------------------------------------------------------------- 4
        print("\n4｜通知落点：prod 里一条'已发送'的记录，运营看得见吗")
        msg_params = json.dumps({"channel": "sms", "to": "+8613800000000",
                                 "subject": "退款进度", "body": "您的退款已受理"})
        msg_id = save_graph(server, headers,
                            tool_graph(f"review6-msg-{uuid.uuid4().hex[:8]}", "message/send",
                                       msg_params))
        msg_body = run_graph(server, headers, msg_id, {"order_id": "unused"})
        msg_out = node_output(msg_body, "tool-1")
        say("--", f"message/send 输出：{dump(msg_out)[:300]}")
        findings["message_send"] = msg_out
        viewer = httpx.get(f"{server.base}/api/demo/messages", headers=headers, timeout=15)
        observed(f"prod 下唯一的消息记录查看面 GET /api/demo/messages → "
                 f"{viewer.status_code}（打包 P 的 404 口径）")
        findings["messages_view_prod"] = viewer.status_code

        # ---------------------------------------------------------------- 5
        print("\n5｜正向对照：同一件事在 demo 开关下应回到可见")
        server.stop()
        server.start(demo_mock=True)
        login2 = httpx.post(f"{server.base}/api/auth/login",
                            json={"username": ADMIN_USERNAME, "password": BOOTSTRAP_PASSWORD},
                            timeout=15)
        ok(login2.status_code == 200,
           f"对照进程登录应 200（实得 {login2.status_code}）——否则第 4 段的 404 说明不了档位")
        headers2 = {"Authorization": f"Bearer {login2.json()['token']}"}
        viewer2 = httpx.get(f"{server.base}/api/demo/messages", headers=headers2, timeout=15)
        observed(f"ATLAS_ENABLE_DEMO_MOCK=1 下同一端点 → {viewer2.status_code}")
        findings["messages_view_demo_flag"] = viewer2.status_code
        ok(viewer2.status_code == 200,
           f"对照必须可见（实得 {viewer2.status_code}），否则第 4 段测的不是档位")

        print("\n—— 汇总（写进 docs/77 的 〔跑〕 证据）——")
        print(dump(findings), flush=True)
        print("\n探测完成 ✅（观察到的替换不判红；只有探测自身失败才非 0）")
        return 0
    finally:
        server.stop()
        if args.keep_db:
            say("--", f"按要求保留探针库 {PROBE_DB}")
        else:
            drop_database(database_url)
            say("--", f"探针库 {PROBE_DB} 已删除")


if __name__ == "__main__":
    sys.exit(main())
