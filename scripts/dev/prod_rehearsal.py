#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 档终门里的 prod 演练（docs/73 4.1／4.2）：把**出厂产物**从空卷起成 prod 档并真驱动。

为什么需要它：`prod_surface_probe.py` 与 `prod_core_loop_probe.py` 都是"本机起一个
uvicorn 打真库"，那条路径证不到三件演练必须证的事——
  ① **出厂镜像**（Dockerfile 里那条 `--workers 1` 与入口脚本的 fail-closed 门）；
  ② **空卷首启**（docs/73 4.2 的就绪竞态就发生在这一次启动上，重启过一次就不算数）；
  ③ **容器边界**（网络与端口是部署形态的一部分，host 进程测不到它）。

七段：
  1. build 当前 HEAD 的镜像 → 记录 image id（"我跑的到底是哪个产物"）。
  2. 只起 `db` ＋ `atlas` 两个服务，**全新项目卷**（先 `down -v` 只删本项目自己的卷）。
  3. 空卷首启：轮询 `/api/ready` 直到 200，记录用时与 `RestartCount`。
     判据＝**一次就到 ready**（不依赖 `restart: unless-stopped` 把首启救回来）。
  4. 引导口令首登 → 改密 → 用新口令重登（`must_change_password` 那类"强制"是否存在，
     本脚本只报观察到的形状，不假装它有）。
  5. 跑一条**内置模板**（`refund-auto`，不是手搓图）到终态；打印每个节点原文；
     并打印 `/api/adapters` 里 prod 的真实注册形状（`shop`／`database`／`web-playwright`
     应当都不在——docs/89 §14 的收口就在这条上第二次被机器看住）。
  6. 重启 `atlas` 容器（卷不动）：图与运行记录仍在、run 数不增（重启不自动重放）、
     `/api/interruptions` 可读。
  7. 演示面与文档面在 prod 档 404；最后**只删本项目自己的卷**。

凭据边界：本脚本不碰任何真实凭据——没有真 LLM／真 Shopify／真 IM，因此第 5 段的
`ai_decision` 会走规则兜底、退款节点会因适配器未注册而 FAILED，**这些是观察结果不是失败**。
密钥与口令均为本机一次性随机值。

用法：.venv/bin/python scripts/dev/prod_rehearsal.py [--host-port 8010] [--keep]
退出码：演练自身完整性（构建失败／起不来／登不进／图没到终态／重启后数据丢了）非 0。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "atlas-prod-rehearsal"
IMAGE = "atlas-demo:latest"
PASSWORD = "Atlas-Rehearsal-" + uuid.uuid4().hex[:12] + "!aA"
ADMIN = "admin-a"


def say(label: str, msg: str) -> None:
    print(f"  {label} {msg}", flush=True)


def ok(cond: bool, msg: str) -> None:
    say("OK   " if cond else "FAIL ", msg)
    if not cond:
        raise AssertionError(msg)


def note(msg: str) -> None:
    say("NOTE ", msg)


def sh(cmd: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> str:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        raise RuntimeError(f"{cmd[:3]}… 退出 {proc.returncode}：\n{out[-2500:]}")
    return out


def compose(env: dict[str, str], *args: str, check: bool = True) -> str:
    return sh(["docker", "compose", "-p", PROJECT, *args], env=env, check=check)


def fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


def rand_secret() -> str:
    return base64.urlsafe_b64encode(os.urandom(48)).decode("ascii").rstrip("=")[:44]


def api(port: int, method: str, path: str, *, token: str | None = None,
        json_body: dict | None = None, base: str = "http") -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.request(method, f"{base}://127.0.0.1:{port}{path}",
                         json=json_body, headers=headers, timeout=30.0)


def main() -> int:
    ap = argparse.ArgumentParser(description="prod rehearsal on the shipped artifact")
    ap.add_argument("--host-port", type=int, default=8010)
    ap.add_argument("--keep", action="store_true", help="演练后不清卷（排障用）")
    args = ap.parse_args()
    port = args.host_port

    if shutil.which("docker") is None:
        print("docker 不可用，本演练无法进行。", file=sys.stderr)
        return 2

    env = {**os.environ,
           "ATLAS_ENV": "prod",
           "ATLAS_MASTER_KEY": fernet_key(),
           "ATLAS_APPROVAL_HMAC_SECRET": rand_secret(),
           "ATLAS_SESSION_SECRET": rand_secret(),
           "ATLAS_ADMIN_BOOTSTRAP_PASSWORD": PASSWORD,
           "LITELLM_MODEL": "",
           "OPENAI_API_KEY": "",
           "OPENAI_BASE_URL": ""}

    override = ROOT / ".rehearsal-override.yml"
    override.write_text(
        f"""services:
  atlas:
    ports: !override
      - "{port}:8000"
    # 演练要的正是"首启一次就到 ready"，restart 兜底会把它藏起来（docs/73 4.2）
    restart: "no"
  db:
    restart: "no"
  caddy:
    profiles: ["skip"]
  prometheus:
    profiles: ["skip"]
  grafana:
    profiles: ["skip"]
""",
        encoding="utf-8",
    )
    files = ["-f", "docker-compose.yml", "-f", override.name]

    try:
        print("1｜构建出厂镜像（当前 HEAD）")
        sha = sh(["git", "rev-parse", "--short", "HEAD"]).strip()
        t0 = time.time()
        sh(["docker", "build", "-t", IMAGE, "."])
        image_id = sh(["docker", "images", "--format", "{{.ID}}", IMAGE]).strip().splitlines()[0]
        ok(bool(image_id), f"镜像已构建：{IMAGE} id={image_id}（源 HEAD `{sha}`，{time.time() - t0:.0f}s）")

        print("2｜清掉本项目的旧卷，用空卷起步")
        compose(env, *files, "down", "-v", "--remove-orphans", check=False)
        compose(env, *files, "up", "-d", "db", "atlas")

        print("3｜空卷首启：等 /api/ready 到 200（一次就到，不许靠重启救）")
        t0 = time.time()
        attempts = 0
        ready = False
        while time.time() - t0 < 240:
            attempts += 1
            try:
                if api(port, "get", "/api/ready").status_code == 200:
                    ready = True
                    break
            except Exception:  # 端口还没监听／连接被拒＝首启窗口内正常
                pass
            time.sleep(2)
        elapsed = time.time() - t0
        restarts = sh(["docker", "compose", "-p", PROJECT, *files, "ps", "-q", "atlas"]).strip()
        count = sh(["docker", "inspect", "--format", "{{.RestartCount}}", restarts]).strip()
        ok(ready, f"/api/ready 在 {elapsed:.1f}s／{attempts} 次轮询内到 200")
        ok(count == "0", f"首启没有依赖容器重启（RestartCount={count}）——docs/73 4.2 要的正是这一条")
        ok(api(port, "get", "/api/health").status_code == 200, "/api/health 200")

        print("4｜引导口令首登 → 改密 → 新口令重登")
        login = api(port, "post", "/api/auth/login", json_body={"username": ADMIN, "password": PASSWORD})
        ok(login.status_code == 200, f"prod 下 admin 可用引导口令登进（{login.status_code}）")
        token = login.json()["token"]
        me = api(port, "get", "/api/auth/me", token=token)
        ok(me.status_code == 200 and me.json()["principal"]["role"] == "admin", f"me 回显 admin：{me.json()}")
        forced = "must_change_password" in json.dumps(me.json())
        note(f"响应里{'有' if forced else '没有'} must_change_password 字段："
             f"{'首登强改密有机制承载' if forced else '“首登强改密”目前只是流程约定，没有服务端强制位'}")
        new_password = PASSWORD + "-rotated-1"
        chg = api(port, "post", "/api/auth/change-password", token=token,
                  json_body={"oldPassword": PASSWORD, "newPassword": new_password})
        ok(chg.status_code == 200, f"改密端点在 prod 可用（{chg.status_code}）")
        again = api(port, "post", "/api/auth/login", json_body={"username": ADMIN, "password": new_password})
        ok(again.status_code == 200, "新口令可登（改密不是纸面动作）")
        stale = api(port, "post", "/api/auth/login", json_body={"username": ADMIN, "password": PASSWORD})
        ok(stale.status_code == 401, f"旧口令立刻失效（{stale.status_code}）")
        token = again.json()["token"]

        print("5a｜凭据边界：带 ai_decision 的代表图在 prod 缺真 LLM 时**拒绝静默降级**")
        tmpl = api(port, "get", "/api/templates/refund-auto", token=token)
        ok(tmpl.status_code == 200, "模板可读（内置目录在 prod 不受演示面门影响）")
        graph = dict(tmpl.json()["graph"])
        graph["name"] = "rehearsal-refund"
        gid = api(port, "post", "/api/graphs", token=token, json_body=graph).json()["id"]
        pub = api(port, "post", f"/api/graphs/{gid}/publish", token=token, json_body={})
        ok(pub.status_code == 200, f"发布：releaseVersion={pub.json().get('releaseVersion')}")
        run = api(port, "post", f"/api/graphs/{gid}/run", token=token,
                  json_body={"releaseVersion": pub.json()["releaseVersion"],
                             "inputs": {"order_id": "R-1", "amount": 12.5, "reason": "演练"}})
        detail = json.dumps(run.json().get("detail", {}), ensure_ascii=False)
        ok(run.status_code == 500 and "LLM_DECISION_UNAVAILABLE" in detail,
           f"prod 没配真 LLM 时 ai_decision **不静默降级到规则兜底**，而是显式失败（{run.status_code}）："
           f"{detail[:120]}")
        note("这一条把 docs/77 R1 那句『凭据到位也证不到』变成演练证据：4.1 剩下的确实是真凭据，不是工程")
        note("但形状值得记一笔：**缺配置**走的是 500（带结构化 code），不是 4xx——对运营它是『配错了』而不是『服务器坏了』，映射成 409/422 更准确；本演练只观察，不擅自改契约")

        print("5b｜无凭据也能走完的一条：审批挂起 → REST 决策 → 到终态，并看 prod 的适配器注册形状")
        tmpl2 = api(port, "get", "/api/templates/approval-timeout-reject", token=token)
        ok(tmpl2.status_code == 200, "第二张内置模板可读")
        graph2 = dict(tmpl2.json()["graph"])
        graph2["name"] = "rehearsal-approval"
        gid2 = api(port, "post", "/api/graphs", token=token, json_body=graph2).json()["id"]
        pub2 = api(port, "post", f"/api/graphs/{gid2}/publish", token=token, json_body={})
        ok(pub2.status_code == 200, f"发布：releaseVersion={pub2.json().get('releaseVersion')}")
        run2 = api(port, "post", f"/api/graphs/{gid2}/run", token=token,
                   json_body={"releaseVersion": pub2.json()["releaseVersion"],
                              "inputs": {"order_id": "R-2"}})
        ok(run2.status_code == 200, f"运行返回 {run2.status_code}")
        body = run2.json()
        run_id, status = body.get("id"), str(body.get("status"))
        say("     ", f"首次响应 status={status}")
        if status == "suspended":
            pending = api(port, "get", "/api/approvals", token=token).json()
            items = pending.get("items", pending if isinstance(pending, list) else [])
            ok(len(items) >= 1, f"挂起的审批在列表里可见（{len(items)} 条）")
            atoken = items[0]["token"]
            decided = api(port, "post", f"/api/approvals/{atoken}/decision", token=token,
                          json_body={"decision": "approved", "comment": "演练批准"})
            ok(decided.status_code == 200, f"REST 决策被接受（{decided.status_code}）")
            terminal = None
            for _ in range(40):
                rows = api(port, "get", "/api/monitoring/runs", token=token).json().get("items", [])
                mine = [r for r in rows if r.get("id") == run_id or r.get("runId") == run_id]
                if mine and str(mine[0].get("status")) not in ("running", "suspended", None):
                    terminal = mine[0]
                    break
                time.sleep(1.5)
            ok(terminal is not None, f"审批后该 run 到达终态：{terminal and terminal.get('status')}")
            say("     ", f"运行记录：{json.dumps(terminal, ensure_ascii=False)[:200]}")
        else:
            ok(status in ("completed", "failed", "interrupted"), f"运行到终态：{status}")
            for node_id, out in (body.get("outputs") or {}).items():
                say("     ", f"{node_id}: {json.dumps(out, ensure_ascii=False)[:150]}")

        print("5c｜人工决策腿：同步运行在后台线程里阻塞，主线程在挂起窗口内批准（用同一张内置模板）")
        holder: dict[str, object] = {}

        def _run() -> None:
            holder["resp"] = api(port, "post", f"/api/graphs/{gid2}/run", token=token,
                                 json_body={"releaseVersion": pub2.json()["releaseVersion"],
                                            "inputs": {"order_id": "R-3"}})

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        atoken = None
        for _ in range(40):  # 挂起窗口只有模板里的 10 秒，轮询到就立刻批
            pending = api(port, "get", "/api/approvals", token=token).json()
            items = pending.get("items", pending if isinstance(pending, list) else [])
            if items:
                atoken = items[0]["token"]
                break
            time.sleep(0.25)
        ok(atoken is not None, "挂起的审批在窗口内可见（GET /api/approvals）")
        decided = api(port, "post", f"/api/approvals/{atoken}/decision", token=token,
                      json_body={"decision": "approved", "comment": "演练批准"})
        ok(decided.status_code == 200, f"人工决策被接受（{decided.status_code}）")
        thread.join(timeout=60)
        resp = holder.get("resp")
        ok(isinstance(resp, httpx.Response) and resp.status_code == 200, "同步运行在人工决策后返回")
        out = (resp.json().get("outputs") or {}) if isinstance(resp, httpx.Response) else {}
        say("     ", "节点：" + ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)[:60]}" for k, v in out.items()))
        approved = [k for k, v in out.items() if isinstance(v, dict) and str(v.get("decision")) == "approved"]
        ok(bool(approved), f"走的是**人工批准**分支而不是超时拒绝（{approved}）")
        note("顺带量到的一件事：同步 /run 会**占住请求线程等审批**（最长到节点 timeoutSeconds），"
             "10 秒模板表现为『请求慢』，长超时图会长期占 worker——真机上是容量问题，不是正确性问题")

        adapters = api(port, "get", "/api/adapters", token=token).json()
        ids = {a.get("id") for a in adapters}
        ok("shop" not in ids and "database" not in ids,
           f"prod 未开演示面：shop／database 均未注册（在册＝{sorted(ids)}）")
        ok("web-playwright" not in ids,
           "docs/89 §14 的收口在**出厂镜像**里同样成立：prod 没有真浏览器适配器")

        print("6｜重启应用容器（卷不动）：数据必须活下来，且不自动重放")
        runs_before = api(port, "get", "/api/monitoring/runs", token=token).json()
        n_before = len(runs_before.get("items", runs_before if isinstance(runs_before, list) else []))
        graphs_before = api(port, "get", "/api/graphs", token=token).json()
        compose(env, *files, "restart", "atlas")
        t0 = time.time()
        while time.time() - t0 < 120:
            try:
                if api(port, "get", "/api/ready").status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(2)
        relogin = api(port, "post", "/api/auth/login",
                      json_body={"username": ADMIN, "password": new_password})
        ok(relogin.status_code == 200, "重启后仍可登进（口令没被重新播种覆盖）")
        token2 = relogin.json()["token"]
        graphs_after = api(port, "get", "/api/graphs", token=token2).json()
        ok(json.dumps(graphs_after, sort_keys=True) == json.dumps(graphs_before, sort_keys=True),
           "重启后图清单逐字不变（PG 档持久化真的生效）")
        runs_after = api(port, "get", "/api/monitoring/runs", token=token2).json()
        n_after = len(runs_after.get("items", runs_after if isinstance(runs_after, list) else []))
        ok(n_after == n_before, f"重启没有自动重放运行：{n_before} → {n_after}")
        inter = api(port, "get", "/api/interruptions", token=token2)
        ok(inter.status_code == 200, f"挂起帧投影可读（{inter.status_code}）")

        print("7｜prod 档的演示面与文档面必须 404，且 404 不透露存在性")
        for path in ("/api/demo/mock/orders", "/api/demo/shop/orders", "/docs", "/openapi.json"):
            res = api(port, "get", path)
            ok(res.status_code == 404, f"GET {path} → 404（打包 P 的匿名模拟面收口）")
        # 别把"路径以 /api/demo 开头"当成"匿名模拟面"：这条是登录后本租户视图，prod 下应 401/200 而不是 404
        msgs = api(port, "get", "/api/demo/messages")
        ok(msgs.status_code == 401, f"GET /api/demo/messages 匿名 → 401（受租户鉴权，不是 prod 该 404 的那批）")
        anon = api(port, "get", "/api/graphs")
        ok(anon.status_code == 401, f"未登录打业务端点是 401 不是 404（{anon.status_code}）")

        print("\n演练全过 ✅")
        return 0
    finally:
        if args.keep:
            note(f"--keep：卷与容器保留；清理命令＝docker compose -p {PROJECT} -f docker-compose.yml "
                 f"-f {override.name} down -v")
        else:
            compose(env, *files, "down", "-v", "--remove-orphans", check=False)
            note(f"已只删除本项目（{PROJECT}）的容器与卷；开发库与其它 compose 项目未触碰")
            override.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
