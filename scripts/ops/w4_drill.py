#!/usr/bin/env python3
"""W4 全链演练脚本骨架（docs/73 W4.1）：触发 → AI 决策 → 人工审批 → 退款落账。

安全边界（默认即最安全）：
- 默认 `--dry-run`：**零网络、零外部副作用**，只做本地 env 预检并把每一步的真实调用打印出来；
- `--execute` 只允许打本机/私网目标（`--allow-remote` 需显式署名）；目标必须挂着内置 demo shop，
  一旦适配器里出现 `shopify` 之类真实渠道立即拒绝；
- **永不**调用真实 SMTP / IM / SMS / Shopify：审批走进程内 broker，退款走 demo shop。

用法：
    python -m scripts.ops.w4_drill                     # 演练计划 + env 预检
    python -m scripts.ops.w4_drill --json              # 机器可读
    python -m scripts.ops.w4_drill --execute --base-url http://127.0.0.1:8000 \
        --username admin --password '...'

退出码：0 = dry-run 完成或 execute 全链通过；1 = 某一步失败；2 = 安全守卫拒绝。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from scripts.ops.preflight_env import collect_findings
from atlas.security.bootstrap import read_env_profile

TEMPLATE_ID = "refund-auto"
TERMINAL_STATES = {"completed", "failed", "interrupted", "cancelled"}
DRILL_PAYLOAD = {
    "event": {
        "type": "order.refund_requested",
        "payload": {
            "order_id": "W4-DRILL-001",
            "reason": "质量问题：包装破损（W4 dry-run 演练数据）",
            "amount": 199,
        },
    }
}


@dataclass
class Step:
    name: str
    action: str
    expected: str
    status: str = "PLANNED"
    detail: str = ""


class Refused(Exception):
    """安全守卫拒绝执行（不是链路故障）。"""


class StepFailed(Exception):
    """链路某一步失败。"""


def _steps_dry_run() -> list[Step]:
    return [
        Step("env-preflight", "本地读取 ATLAS_ENV / 必需密钥 / 存储档位", "0 阻断项"),
        Step(
            "health",
            "GET /healthz（无鉴权）",
            "200 且返回 status=ok",
        ),
        Step(
            "auth",
            "POST /api/auth/login 或复用一个 Bearer token",
            "拿到 token 且 GET /api/auth/me 返回当前账号",
        ),
        Step(
            "demo-guard",
            "GET /api/adapters",
            "存在内置 shop 适配器，且不含 shopify 等真实渠道",
        ),
        Step(
            "instantiate",
            f"GET /api/templates/{TEMPLATE_ID} → POST /api/graphs",
            "返回新 graph id（草稿版本）",
        ),
        Step("publish", "POST /api/graphs/{id}/publish", "产出不可变发布版本"),
        Step(
            "run",
            "POST /api/graphs/{id}/run（payload 见 DRILL_PAYLOAD）",
            "status=suspended（停在人工审批节点）",
        ),
        Step(
            "approve",
            "GET /api/approvals → POST /api/approvals/{token}/decision {approved}",
            "审批被消费，运行恢复（不触发任何邮件/IM/SMS）",
        ),
        Step(
            "settle",
            "轮询 GET /api/runs/{run_id}",
            "status=completed，demo shop 产生 execute_refund 轨迹",
        ),
    ]


def _base_host_is_local(base_url: str) -> bool:
    host = (urlsplit(base_url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    if host.startswith("10.") or host.startswith("192.168."):
        return True
    if host.startswith("172.") and 16 <= int(host.split(".")[1]) <= 31:
        return True
    return False


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    timeout: float = 30,
) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise StepFailed(f"{method} {path} → HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise StepFailed(f"{method} {path} → 连接失败: {exc.reason}") from exc
    return json.loads(raw) if raw else {}


def _login(base_url: str, username: str, password: str) -> str:
    body = _request(base_url, "POST", "/api/auth/login", body={"username": username, "password": password})
    token = body.get("token") or body.get("access_token")
    if not token:
        raise StepFailed(f"登录响应里没有 token：{sorted(body)}")
    return token


def _adapters(base_url: str, token: str) -> list[dict]:
    result = _request(base_url, "GET", "/api/adapters", token=token)
    return result if isinstance(result, list) else result.get("items", [])


def _pending_tokens(base_url: str, token: str) -> list[str]:
    result = _request(base_url, "GET", "/api/approvals", token=token)
    return [item["token"] for item in result.get("items", []) if item.get("token")]


def _wait_run(base_url: str, token: str, run_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    current: dict = {}
    while time.time() < deadline:
        current = _request(base_url, "GET", f"/api/runs/{run_id}", token=token)
        if current.get("status") in TERMINAL_STATES:
            return current
        time.sleep(2)
    raise StepFailed(f"运行 {run_id} 在 {timeout}s 内未到终态（最后状态 {current.get('status')}）")


def run_dry(strict: bool, *, quiet: bool = False) -> int:
    findings = collect_findings(read_env_profile(), strict)
    blocks = [item for item in findings if item.level == "BLOCK"]
    if not quiet:
        print(f"[w4] dry-run：不发起任何网络请求。env 预检 {len(blocks)} 阻断、"
              f"{sum(item.level == 'WARN' for item in findings)} 提示。")
        for step in _steps_dry_run():
            print(f"  [{step.name}] {step.action} → 期望：{step.expected}")
        print("[w4] 下一步：在 **demo 面** 实例上运行 --execute；真实 SMTP/IM/SMS/Shopify 永不被调用。")
    return 1 if blocks else 0


def run_execute(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    if not _base_host_is_local(base_url) and not args.allow_remote:
        raise Refused(
            f"目标 {base_url} 不是本机/私网地址；W4 演练只允许 demo 面。"
            "确需在预发跑请显式加 --allow-remote，并确认该实例没有接真实渠道。"
        )
    token = args.token or ""
    if token:
        _request(base_url, "GET", "/api/auth/me", token=token)
    elif args.username and args.password:
        token = _login(base_url, args.username, args.password)
    else:
        raise Refused("execute 需要 --token，或同时给 --username/--password")

    health = _request(base_url, "GET", "/healthz")
    if health.get("status") not in {"ok", "healthy", "up"}:
        raise StepFailed(f"/healthz 返回异常：{health}")

    adapters = _adapters(base_url, token)
    adapter_ids = {str(item.get("id") or item.get("name") or "").lower() for item in adapters}
    if any("shopify" in item for item in adapter_ids):
        raise Refused("检测到真实 Shopify 适配器 id，拒绝执行（W4 只允许 demo shop）")
    if not any("shop" in item for item in adapter_ids):
        raise Refused(f"没有内置 demo shop 适配器（现有：{sorted(adapter_ids)}）")

    template = _request(base_url, "GET", f"/api/templates/{TEMPLATE_ID}", token=token)
    graph = template.get("graph") or template
    saved = _request(base_url, "POST", "/api/graphs", token=token, body=graph)
    graph_id = saved.get("id")
    if not graph_id:
        raise StepFailed(f"保存图未返回 id：{saved}")

    _request(base_url, "POST", f"/api/graphs/{graph_id}/publish", token=token, body={})
    run = _request(base_url, "POST", f"/api/graphs/{graph_id}/run", token=token, body=DRILL_PAYLOAD)
    run_id = run.get("id")
    if not run_id:
        raise StepFailed(f"运行未返回 id：{run}")
    if run.get("status") not in {"suspended", "completed"}:
        raise StepFailed(f"运行既未挂起也未完成：{run.get('status')}")

    if run.get("status") == "suspended":
        approval_token = args.approval_token
        if not approval_token:
            pending = _pending_tokens(base_url, token)
            if len(pending) != 1:
                raise Refused(
                    f"pending 审批有 {len(pending)} 条，无法唯一判定演练对应项；"
                    "请用 --approval-token 指定"
                )
            approval_token = pending[0]
        _request(
            base_url,
            "POST",
            f"/api/approvals/{approval_token}/decision",
            token=token,
            body={"decision": "approved", "comment": "W4 drill (demo surface)"},
        )

    final = _wait_run(base_url, token, run_id, args.timeout)
    if final.get("status") != "completed":
        raise StepFailed(f"终态是 {final.get('status')}，期望 completed：{final.get('trace')}")

    trace = " | ".join(final.get("trace") or [])
    if "execute_refund" not in trace:
        raise StepFailed("轨迹里没有 demo shop 的 execute_refund，退款腿未真正走通")
    print(f"[w4] PASS run_id={run_id} graph_id={graph_id}")
    print(f"[w4] trace: {trace[:400]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Atlas W4 full-chain drill (dry-run by default)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只打印演练计划与环境预检（默认行为）")
    mode.add_argument("--execute", action="store_true", help="真正调用 API（默认只打印计划）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="", help="复用已有 Bearer token")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--approval-token", default="", help="pending 审批不唯一时显式指定")
    parser.add_argument("--allow-remote", action="store_true", help="允许非本机/私网目标（仅预发）")
    parser.add_argument("--timeout", type=float, default=120, help="等待运行到终态的秒数")
    parser.add_argument("--strict", action="store_true", help="env 预检按 prod 口径拦截")
    parser.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = parser.parse_args()

    try:
        code = run_execute(args) if args.execute else run_dry(args.strict, quiet=args.json)
    except Refused as exc:
        print(f"[w4] REFUSED: {exc}", file=sys.stderr)
        code = 2
    except StepFailed as exc:
        print(f"[w4] FAILED: {exc}", file=sys.stderr)
        code = 1

    if args.json:
        print(json.dumps({"mode": "execute" if args.execute else "dry-run", "exitCode": code},
                         ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
