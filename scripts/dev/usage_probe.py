# -*- coding: utf-8 -*-
"""打包 AU 真机端到端（docs/129 §4 U1426）：LLM 用量计量的真模型验收。

**这不是断言脚本的翻版**：单测跑的是 TestClient 里的假 litellm 与纯函数，这里要证的是
「真模型跑一次 → 账上真的有数，且归属挂在正确的 run/节点上」——计量最危险的失败形态不是
报错，而是**静默记 0**或**挂错 run**，两者在单测里都看不出来。

跑法：
    .venv/bin/python scripts/dev/usage_probe.py            # 无真凭据：结构性核对 + BLOCKED 点名
    .venv/bin/python scripts/dev/usage_probe.py --live     # 真模型（从仓库 .env 读凭据，不打印值）

退出码：0＝所声明的检查全部通过；3＝``--live`` 但缺凭据（BLOCKED，点名缺哪个变量）；
2＝起不来／登不进／真模型跑完却查不到数（那是真缺陷，不是凭据问题）。
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
    """读仓库 `.env` 的键值（只取白名单键，值不进任何输出）。"""
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
            [
                PYTHON, "-m", "uvicorn", "atlas.api.main:app",
                "--host", "127.0.0.1", "--port", str(self.port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
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


def _node(node_id: str, node_type: str, name: str, config: dict) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "name": name,
        "description": "",
        "position": {"x": 0, "y": 0},
        "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 60, "on_error": "stop"},
    }


def decision_graph() -> dict:
    """一条真会走模型的最小图：触发 → AI 决策 → 通知（只求"模型真被调用且挂对归属"）。"""
    return {
        "version": 1,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": [
            _node("trigger-1", "trigger", "触发：新退款申请", {"triggerType": "manual"}),
            _node(
                "ai_decision-1",
                "ai_decision",
                "AI 决策：退款还是人工",
                {
                    "promptTemplate": (
                        "退款单 {{trigger-1.context.payload.order_id}}，"
                        "原因 {{trigger-1.context.payload.reason}}，"
                        "金额 {{trigger-1.context.payload.amount}} 元，限额 {{global.approval_limit}}"
                    ),
                    "confidenceThreshold": 0.6,
                },
            ),
            _node(
                "notify-1",
                "tool_call",
                "通知",
                {
                    "tool": "message/send",
                    "params": (
                        # channel=email 走进程内 sink：探针不打扰真人，也不必配 SMTP
                        '{"channel":"email","to":["ops@example.com"],'
                        '"subject":"退款处置","body":"{{ai_decision-1.decision.action}}"}'
                    ),
                },
            ),
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "notify-1"},
        ],
    }


def login(client: httpx.Client) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['token']}"}


def run_live_checks(client: httpx.Client, headers: dict[str, str]) -> None:
    """真模型跑一笔退款 + 一次 NL 生成，然后核对账。"""
    saved = client.post("/api/graphs", json=decision_graph(), headers=headers)
    saved.raise_for_status()
    graph_id = saved.json()["id"]
    published = client.post(f"/api/graphs/{graph_id}/publish", headers=headers)
    if published.status_code != 200:
        note(f"发布失败（{published.status_code}），按草稿跑：{published.text[:120]}")

    result = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "12345", "reason": "商品破损", "amount": "299"}},
        headers=headers,
    )
    result.raise_for_status()
    body = result.json()
    decision = body.get("outputs", {}).get("ai_decision-1", {}).get("decision", {})
    ok(
        str(decision.get("source", "")).startswith("llm:"),
        "真模型决策（source 以 llm: 开头，不是 rule:）",
        f"source={decision.get('source')!r}",
    )

    nl = client.post(
        "/api/nl/generate", json={"prompt": "退款审批：AI 判断后通知运营"}, headers=headers
    )
    ok(nl.status_code == 200, "NL 生成端点跑通（端点级调用）", f"{nl.status_code} {nl.text[:120]}")

    summary = client.get("/api/usage/summary?group_by=caller", headers=headers).json()
    by_caller = {row["key"]: row for row in summary["items"]}
    ok("ai_decision" in by_caller, "按 caller 聚合里有 ai_decision", str(list(by_caller)))
    if "ai_decision" in by_caller:
        ok(
            by_caller["ai_decision"]["totalTokens"] > 0,
            "ai_decision 的 token 合计非零（计量不是静默记 0）",
            str(by_caller["ai_decision"]),
        )
        # 实测读数留痕：收口记录与 docs/13 的门读数同源
        note(f"ai_decision 实测：{by_caller['ai_decision']}")
    ok("nl_generate" in by_caller, "按 caller 聚合里有 nl_generate（端点级调用也计）",
       str(list(by_caller)))

    detail = client.get("/api/usage/recent?limit=50", headers=headers).json()["items"]
    decision_rows = [row for row in detail if row["caller"] == "ai_decision"]
    ok(bool(decision_rows), "明细里有 ai_decision 行")
    if decision_rows:
        row = decision_rows[0]
        ok(row["graphId"] == graph_id, "归属 graphId 正确", f"{row['graphId']} != {graph_id}")
        ok(row["nodeId"] == "ai_decision-1", "归属 nodeId 正确", row["nodeId"])
        ok(bool(row["runId"]), "归属 runId 非空（来自运行入口）", row["runId"])
        ok(row["latencyMs"] > 0, "耗时已记录", str(row["latencyMs"]))
        ok(
            not any(key.lower().endswith("key") for key in row),
            "记录里没有密钥字段",
            str(list(row)),
        )

    # 三档聚合与明细对账（同一份数据不得出现两种说法）
    for group_by in ("caller", "model", "day"):
        items = client.get(f"/api/usage/summary?group_by={group_by}", headers=headers).json()["items"]
        ok(
            sum(row["totalTokens"] for row in items) == sum(row["totalTokens"] for row in detail),
            f"group_by={group_by} 的合计与明细相等",
        )

    bad = client.get("/api/usage/summary?group_by=nope", headers=headers)
    ok(bad.status_code == 422, "非法 group_by ⇒ 422", str(bad.status_code))

    # 无调用就不该有账：清一次再查（reset 是运行数据同清）
    client.post("/api/demo/reset", headers=headers)
    after = client.get("/api/usage/recent?limit=10", headers=headers).json()["items"]
    ok(after == [], "reset 后用量账清空（运行数据语义）")


def run_structural_checks(client: httpx.Client, headers: dict[str, str]) -> None:
    """无真凭据时仍可证的结构面：端点在、鉴权在、无模型就不记账。"""
    summary = client.get("/api/usage/summary", headers=headers)
    ok(summary.status_code == 200, "GET /api/usage/summary 200（read 档）")
    ok(summary.json()["items"] == [], "没有任何调用时聚合为空（不是假数据）")
    recent = client.get("/api/usage/recent", headers=headers)
    ok(recent.status_code == 200 and recent.json()["items"] == [], "GET /api/usage/recent 200 且为空")

    saved = client.post("/api/graphs", json=decision_graph(), headers=headers)
    saved.raise_for_status()
    graph_id = saved.json()["id"]
    result = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "12345", "reason": "商品破损", "amount": "299"}},
        headers=headers,
    )
    result.raise_for_status()
    decision = result.json().get("outputs", {}).get("ai_decision-1", {}).get("decision", {})
    ok(decision.get("source") == "rule", "无模型时走规则兜底（没调模型就没账可记）",
       str(decision.get("source")))
    rows = client.get("/api/usage/recent", headers=headers).json()["items"]
    ok(rows == [], "规则兜底路径不产生用量记录（零调用零账）", str(rows[:1]))


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM usage metering end-to-end probe")
    parser.add_argument("--live", action="store_true",
                        help="真模型腿（从仓库 .env 读凭据；缺凭据记 BLOCKED 并退出 3）")
    parser.add_argument("--host-port", type=int, default=0, help="0＝随机空闲端口")
    args = parser.parse_args()

    credentials = read_dotenv()
    if args.live:
        missing = [key for key in ("LITELLM_MODEL", "OPENAI_API_KEY")
                   if not (credentials.get(key) or os.environ.get(key))]
        if missing:
            blocked(
                "真模型腿缺凭据：" + "、".join(missing)
                + "（填进仓库 .env 的 LITELLM_MODEL / OPENAI_API_KEY；值不会被打印）"
            )
            return 3
        print("  NOTE  真模型腿启用（凭据来自仓库 .env，只读白名单键，不打印值）")

    port = args.host_port or free_port()
    server = Server(port, args.live, credentials)
    try:
        server.start()
    except Exception as exc:  # noqa: BLE001 - 起不来是环境问题，报清楚即可
        print(f"  FAIL  起服务失败：{exc}", file=sys.stderr)
        return 2

    try:
        with httpx.Client(base_url=server.base, timeout=60) as client:
            headers = login(client)
            ok(True, "登录成功（t1 管理员会话）")
            if args.live:
                run_live_checks(client, headers)
            else:
                run_structural_checks(client, headers)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  探测过程中请求失败：{exc}", file=sys.stderr)
        return 2
    finally:
        server.stop()

    print(f"\n汇总：{RET['pass']} PASS ／ {RET['fail']} FAIL")
    return 0 if RET["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
