#!/usr/bin/env python3
"""Atlas 性能基准脚本（BENCHMARK.md 配套，14 D9；2026-09-14 首版）。

纯标准库，零新依赖：对当前可跑的确定性路径采样墙钟时延，输出可直接粘贴到
BENCHMARK.md Results 表的 Markdown 行。覆盖范围：

- OODA 主循环吞吐（engine.run_loop，确定性占位节点）
- Graph 编译时延（graph.compile_graph，退款三节点图）
- 退款端到端时延（graph.run_graph：触发→规则决策→shop 适配器，不含 LLM）
- Harness 调用开销（权限校验 + 审计 + 适配器分发，不含外部平台耗时）
- DB ping（opt-in：设置 DATABASE_URL 时测 PostgreSQL 连接 + SELECT 1）

边界（BENCHMARK.md Scope 中本次不测）：
- LLM 决策 p50/p99 随真实供应商接入补测（规则路径零网络）。
- Redis 短期记忆 / pgvector 记忆表读写随 11 S1 记忆层接入补测。
- 并行扇出、长流程内存增长随对应节点/引擎落地补测。

用法：.venv/bin/python scripts/benchmark.py [--iterations 300]
"""

from __future__ import annotations

import argparse
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.engine.loop import run_loop
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import build_demo_registry, compile_graph, run_graph
from atlas.harness.base import ActionRequest, Permission
from atlas.llm.decision import RuleBasedDecisionClient
from atlas.shop.adapter import ShopHarnessAdapter
from atlas.shop.service import DemoShopService


def _refund_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [
                {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}
            ],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "新退款申请",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
                {"id": "ai_decision-1", "type": "ai_decision", "name": "退款决策",
                 "config": {"promptTemplate": "退款 {{trigger-1.context.payload.reason}} 金额 {{trigger-1.context.payload.amount}}"}},
                {"id": "tool_call-1", "type": "tool_call", "name": "执行处理",
                 "config": {"tool": "shop/process_refund"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
                {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
            ],
        }
    )


def _measure(fn: Callable[[], None], iterations: int) -> list[float]:
    warmup = max(5, iterations // 10)
    for _ in range(warmup):
        fn()
    samples_ms: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples_ms.append((time.perf_counter() - start) * 1000)
    return samples_ms


def _p99(samples: list[float]) -> float:
    if len(samples) < 100:
        return max(samples)
    return statistics.quantiles(samples, n=100, method="inclusive")[98]


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _benchmark_scenarios(iterations: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    env_note = (
        f"{platform.python_implementation()} {platform.python_version()}, "
        f"{platform.platform()}, {platform.machine()}"
    )

    samples = _measure(lambda: run_loop("benchmark", max_steps=2), iterations)
    med = statistics.median(samples)
    rows.append({
        "scenario": "OODA loop throughput",
        "metric": "loop latency p50/p99 (ms)；吞吐 loops/s（取 p50 倒数）",
        "value": f"{med:.2f} / {_p99(samples):.2f} ms；{1000 / med:.1f} loops/s",
        "notes": "确定性占位节点，无 LLM/IO；" + env_note,
    })

    graph = _refund_graph()
    samples = _measure(
        lambda: compile_graph(graph, decision_client=RuleBasedDecisionClient()),
        iterations,
    )
    med = statistics.median(samples)
    rows.append({
        "scenario": "Graph compile latency",
        "metric": "3 节点退款图单次编译 p50/p99 (ms)",
        "value": f"{med:.2f} / {_p99(samples):.2f} ms",
        "notes": "DSL→LangGraph StateGraph 装配，进程内",
    })

    service = DemoShopService()
    registry = build_demo_registry()
    registry.unregister("shop")
    registry.register(
        ShopHarnessAdapter(
            service=service,
            granted_permissions={Permission.READ, Permission.WRITE, Permission.FINANCIAL},
        )
    )

    def e2e_once() -> None:
        service.reset()
        run_graph(
            graph,
            inputs={"order_id": "12345", "reason": "商品破损", "amount": 299},
            decision_client=RuleBasedDecisionClient(),
            registry=registry,
        )

    samples = _measure(e2e_once, iterations)
    med = statistics.median(samples)
    rows.append({
        "scenario": "Refund e2e latency (12345 auto-refund)",
        "metric": "触发→规则决策→shop 执行 p50/p99 (ms)",
        "value": f"{med:.2f} / {_p99(samples):.2f} ms",
        "notes": "规则决策路径，不含 LLM 网络时延；service.reset 在计时外",
    })

    adapter = ShopHarnessAdapter(
        granted_permissions={Permission.READ, Permission.WRITE, Permission.FINANCIAL}
    )
    samples = _measure(
        lambda: adapter.execute(ActionRequest(capability_name="list_pending_refunds")),
        iterations,
    )
    med = statistics.median(samples)
    rows.append({
        "scenario": "Harness call overhead",
        "metric": "shop/list_pending_refunds 单次调用 p50/p99 (ms)",
        "value": f"{med:.3f} / {_p99(samples):.3f} ms",
        "notes": "权限校验+审计+进程内分发，不含外部平台耗时",
    })

    if os.environ.get("DATABASE_URL"):
        from sqlalchemy import text

        from atlas.memory.database import create_database_engine, ping

        engine = create_database_engine()
        samples = _measure(lambda: ping(engine), iterations)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        med = statistics.median(samples)
        rows.append({
            "scenario": "DB ping (opt-in)",
            "metric": "连接池取连接 + SELECT 1 p50/p99 (ms)",
            "value": f"{med:.2f} / {_p99(samples):.2f} ms",
            "notes": f"DATABASE_URL={os.environ['DATABASE_URL'].split('@')[-1]}",
        })

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Atlas performance benchmark")
    parser.add_argument("--iterations", type=int, default=300, help="每场景计时迭代数（另含 10%% warmup）")
    args = parser.parse_args()

    commit = _git_commit()
    rows = _benchmark_scenarios(args.iterations)

    print(f"# Baseline: {commit} | iterations={args.iterations}\n")
    print("| Date | Baseline (commit / version) | Scenario | Metric | Value | Notes |")
    print("|---|---|---|---|---|---|")
    date = time.strftime("%Y-%m-%d")
    for row in rows:
        print(
            f"| {date} | {commit} | {row['scenario']} | {row['metric']} | "
            f"{row['value']} | {row['notes']} |"
        )


if __name__ == "__main__":
    main()
