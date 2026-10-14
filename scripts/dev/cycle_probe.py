# -*- coding: utf-8 -*-
"""打包 BN 真机端到端（docs/130 §4 的 U1442）：自走循环的真模型验收。

**这不是断言脚本的翻版**：单测跑的是 TestClient 里的假 litellm 与假驱动器，这里要证的是
「真模型跑出来的结果**真的**能让圈判定收手、且圈账与用量账对得上」——最危险的失败形态不是
报错，而是**判定恒假**（圈一直跑到预算触顶）或**归属串了**（圈序号/累计 token 张冠李戴）。

跑法：
    .venv/bin/python scripts/dev/cycle_probe.py            # 规则判定腿（无凭据也能跑）
    .venv/bin/python scripts/dev/cycle_probe.py --live     # 真模型 + completeWhen 由模型产出驱动

退出码：0＝所声明的检查全过；3＝``--live`` 但缺凭据（BLOCKED，点名缺哪个变量）；
2＝起不来／登不进／真模型跑完却查不到圈账（那是真缺陷，不是凭据问题）。
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
CREDENTIAL_KEYS = ("LITELLM_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL")

RET = {"pass": 0, "fail": 0}


def ok(condition: bool, label: str, detail: str = "") -> bool:
    if condition:
        RET["pass"] += 1
        print(f"  PASS  {label}")
    else:
        RET["fail"] += 1
        print(f"  FAIL  {label}" + (f" ｜ {detail}" if detail else ""))
    return condition


def note(label: str) -> None:
    print(f"  NOTE  {label}")


def blocked(label: str) -> None:
    print(f"  BLOCKED  {label}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def read_dotenv() -> dict[str, str]:
    """读仓库 `.env` 的白名单键（值绝不进任何输出）。"""
    values: dict[str, str] = {}
    dotenv = ROOT / ".env"
    if not dotenv.exists():
        return values
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class Server:
    """只管理自己起的那个进程（绝不按命令行模式杀别人的服务）。"""

    def __init__(self, port: int, live: bool, credentials: dict[str, str]) -> None:
        self.port = port
        self.live = live
        self.credentials = credentials
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = dict(os.environ)
        env.update(
            {
                "ATLAS_ENV": "dev",
                "ATLAS_STORAGE_BACKEND": "memory",
                "ATLAS_SCHEDULE_ENABLED": "0",
                "LITELLM_MODEL": "",
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "",
            }
        )
        if self.live:
            for key in CREDENTIAL_KEYS:
                if self.credentials.get(key):
                    env[key] = self.credentials[key]
        env.pop("ATLAS_ENABLE_DEMO_MOCK", None)
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "atlas.api.main:app",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("uvicorn 子进程提前退出（看它自己的日志）")
            try:
                if httpx.get(f"{self.base}/api/health", timeout=1).status_code == 200:
                    return
            except Exception:
                time.sleep(0.3)
        raise RuntimeError("60 秒内 /api/health 没就绪")

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


def _node(node_id: str, node_type: str, config: dict) -> dict:
    return {
        "id": node_id, "type": node_type, "name": node_id, "description": "",
        "position": {"x": 0, "y": 0}, "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 60, "on_error": "stop"},
    }


def cycle_graph(live: bool) -> dict:
    """一条启用圈的真图：触发 → AI 决策 → 通知。

    两条腿验的是**不同的收手机制**（别让一条腿同时证明两件事）：

    - **规则腿（无凭据）**：`completeWhen` 永远为假 ⇒ 断言**三道闸门里最先触顶的是迭代数**
      （恰好 3 圈、`stopReason=maxIterations`）。这条腿的价值在于：判定恒假时圈**必须**
      靠预算收手，绝不能无限跑。
    - **真模型腿**：`completeWhen` 读**模型这一圈的决策**，而 prompt 让模型"上一轮给的是
      转人工，这一轮就改口成自动退款"——`carry` 把上一轮动作注入 `prev`，于是**模型的输出
      真的驱动了终止**（第 1 圈转人工 → carry → 第 2 圈自动退款 → 判定达成 → 收手）。
    """
    if live:
        prompt = (
            "如果变量 prev 的值是 request_human_approval，就输出 action=approve_refund；"
            "否则输出 action=request_human_approval。只输出 JSON。"
        )
        complete_when = "{{ai-1.decision.action}} == 'approve_refund'"
        carry = {"prev": "{{ai-1.decision.action}}"}
    else:
        prompt = "随便给一个动作（这条腿只验预算闸门收手）。只输出 JSON。"
        complete_when = "{{no.such.variable}} == 'never'"   # 恒假：走预算腿
        carry = {"prev": "{{ai-1.decision.action}}"}
    return {
        "version": 1,
        "variables": [{"name": "prev", "type": "string", "value": "", "scope": "global"}],
        "nodes": [
            _node("trigger-1", "trigger", {"triggerType": "manual"}),
            _node("ai-1", "ai_decision", {"promptTemplate": prompt, "confidenceThreshold": 0.6}),
            _node(
                "notify-1", "tool_call",
                {
                    "tool": "message/send",
                    # channel=email 走进程内 sink：探针不打扰真人，也不必配 SMTP
                    "params": '{"channel":"email","to":["ops@example.com"],'
                              '"subject":"圈探针","body":"{{ai-1.decision.action}}"}',
                },
            ),
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai-1"},
            {"id": "e2", "source": "ai-1", "target": "notify-1"},
        ],
        "loop": {
            "enabled": True,
            "maxIterations": 3,
            "maxWallClockSeconds": 120,
            "maxTokens": 100000,
            "completeWhen": complete_when,
            "judgeMode": "rule",
            "carry": carry,
        },
    }


def login(client: httpx.Client) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['token']}"}


def run_cycle(client: httpx.Client, headers: dict[str, str], graph: dict, live: bool) -> dict:
    saved = client.post("/api/graphs", json=graph, headers=headers)
    saved.raise_for_status()
    graph_id = saved.json()["id"]
    # carry 把 attempt 往下一圈带；第一圈从 1 起（条件是 >=2 ⇒ 恰好两圈收手）
    run = client.post(f"/api/graphs/{graph_id}/run", json={"inputs": {}}, headers=headers)
    run.raise_for_status()
    return {"graphId": graph_id, "run": run.json()}


def check(client: httpx.Client, headers: dict[str, str], info: dict, live: bool) -> None:
    runs = client.get("/api/runs?limit=50", headers=headers).json()["items"]
    cycle_runs = sorted(
        [r for r in runs if r["graphId"] == info["graphId"] and r.get("cycleGroupId")],
        key=lambda r: r.get("cycleSeq", 0),
    )
    ok(len(cycle_runs) >= 2, f"至少跑了两圈（实得 {len(cycle_runs)}）")
    seqs = [r.get("cycleSeq") for r in cycle_runs]
    ok(seqs == list(range(1, len(cycle_runs) + 1)), "圈序号连续递增", str(seqs))
    groups = {r["cycleGroupId"] for r in cycle_runs}
    ok(len(groups) == 1, "所有圈属于同一条会话", str(groups))

    last = cycle_runs[-1]
    view = client.get(f"/api/runs/{last['runId']}/cycle", headers=headers)
    ok(view.status_code == 200, "GET /api/runs/{id}/cycle 200")
    body = view.json()
    ok(body["state"] in ("completed", "budget_exhausted"),
       "会话有终态", body["state"])
    if live:
        ok(body["stopReason"] == "complete_when",
           "真模型的输出让判定收手（complete_when）", body["stopReason"])
        ok(body["seq"] == 2, f"真模型腿恰好两圈（实得 {body['seq']}）")
    else:
        ok(body["stopReason"] == "maxIterations",
           "判定恒假时靠迭代闸门收手（不是无限跑）", body["stopReason"])
        ok(body["seq"] == 3, f"规则腿恰好跑满 3 圈（实得 {body['seq']}）")
    note(f"圈会话读数：seq={body['seq']} state={body['state']} "
         f"tokens={body['tokensSpent']} elapsed={body['elapsedSeconds']}s")

    # 圈账与用量账对账（打包 AU 的账是圈 token 预算的唯一来源）
    usage = client.get("/api/usage/summary?group_by=caller", headers=headers).json()["items"]
    ledger_total = sum(row["totalTokens"] for row in usage)
    ok(ledger_total == body["tokensSpent"],
       "圈累计 token == 用量账合计（对账相等）",
       f"cycle={body['tokensSpent']} ledger={ledger_total}")
    if live:
        by_caller = {row["key"]: row for row in usage}
        ok("ai_decision" in by_caller, "真模型调用确实发生（caller=ai_decision）",
           str(list(by_caller)))
        ok(by_caller.get("ai_decision", {}).get("totalTokens", 0) > 0,
           "真模型 token 非零（不是静默记 0）")

    # 停整条圈：已终态时幂等且不覆写历史
    stop = client.post(f"/api/runs/{last['runId']}/cycle/stop", headers=headers)
    ok(stop.status_code == 200, "停整条圈：已终态 ⇒ 200 幂等")
    ok(stop.json()["state"] == body["state"], "终态不被 stop 覆写",
       f"{body['state']} -> {stop.json()['state']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="self-running cycle end-to-end probe")
    parser.add_argument("--live", action="store_true",
                        help="真模型腿（从仓库 .env 读凭据；缺凭据记 BLOCKED 并退出 3）")
    parser.add_argument("--host-port", type=int, default=0)
    args = parser.parse_args()

    credentials = read_dotenv()
    if args.live:
        missing = [key for key in ("LITELLM_MODEL", "OPENAI_API_KEY")
                   if not (credentials.get(key) or os.environ.get(key))]
        if missing:
            blocked("真模型腿缺凭据：" + "、".join(missing)
                    + "（填进仓库 .env 的 LITELLM_MODEL / OPENAI_API_KEY；值不会被打印）")
            return 3
        print("  NOTE  真模型腿启用（凭据来自仓库 .env，只读白名单键，不打印值）")

    port = args.host_port or free_port()
    server = Server(port, args.live, credentials)
    try:
        server.start()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  起服务失败：{exc}", file=sys.stderr)
        return 2

    try:
        with httpx.Client(base_url=server.base, timeout=120) as client:
            headers = login(client)
            ok(True, "登录成功（t1 管理员会话）")
            info = run_cycle(client, headers, cycle_graph(args.live), args.live)
            ok(info["run"].get("status") == "completed",
               "同步入口返回最后一圈的终态", str(info["run"].get("status")))
            check(client, headers, info, args.live)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  探测过程中请求失败：{exc}", file=sys.stderr)
        return 2
    finally:
        server.stop()

    print(f"\n汇总：{RET['pass']} PASS ／ {RET['fail']} FAIL")
    return 0 if RET["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
