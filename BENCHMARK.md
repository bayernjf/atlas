# Benchmark

Atlas 性能与容量基准。范围与结果记录表（参照 agent-world 惯例，代码阶段填充实测）。

## How to run

```bash
.venv/bin/python scripts/benchmark.py [--iterations 300]
# 设置 DATABASE_URL 时追加 DB ping（连接池取连接 + SELECT 1）一项
```

脚本纯标准库、零新依赖（14 D9，2026-09-14）：每场景先 10% warmup 再采样，输出可直接粘贴到下方 Results 表的 Markdown 行（含 commit 短哈希与运行环境）。被测对象：OODA 主循环（`engine.run_loop`）、Graph 编译（`graph.compile_graph`）、退款端到端（`graph.run_graph` 规则决策路径）、Harness 进程内调用开销、可选 DB ping。

## Scope

以下为待测量项（随基准落地更新，依据 [docs/06-运行时与质量保障.md](docs/06-运行时与质量保障.md) 与 [docs/13-测试用例清单.md](docs/13-测试用例清单.md)）：

- Loop 主循环吞吐（OODA 循环 / 秒）
- 决策节点延迟（单步决策 p50/p99）
- Graph 执行吞吐与并行扇出（节点 / 秒，fan-out/fan-in）
- 记忆系统读写延迟（Redis 短期 / pgvector 长期检索）
- Harness 调用开销（网关路由 + 工具调用，不含外部平台耗时）
- 端到端流程时延（典型审批流程：触发 → AI 决策 → 工具调用 → 完成）
- 长流程内存/事件日志增长（递归自动化引擎）

2026-09-14 首版基线覆盖：Loop 主循环吞吐、Graph 编译时延、退款端到端时延、Harness 调用开销（+ 可选 DB ping）。**暂不可测、待接入后补**：LLM 决策延迟（随真实供应商，`LITELLM_MODEL`）、Graph 并行扇出（随条件/循环/并行节点，Phase 2）、Redis/pgvector 记忆读写（随 11 S1 记忆层；当前 DB 层仅有 ping 探针）、长流程内存增长（随递归自动化引擎）。

## Results

| Date | Baseline (commit / version) | Scenario | Metric | Value | Notes |
|---|---|---|---|---|---|
| 2026-09-14 | 407812e | OODA loop throughput | loop latency p50/p99 (ms)；吞吐 loops/s（p50 倒数） | 2.05 / 2.43 ms；487.9 loops/s | 确定性占位节点，无 LLM/IO；CPython 3.11.15，macOS 26.5.2 arm64；300 次/场景 |
| 2026-09-14 | 407812e | Graph compile latency | 3 节点退款图单次编译 p50/p99 (ms) | 1.81 / 2.07 ms | DSL→LangGraph StateGraph 装配，进程内 |
| 2026-09-14 | 407812e | Refund e2e latency（12345 auto-refund） | 触发→规则决策→shop 执行 p50/p99 (ms) | 2.28 / 2.67 ms | 规则决策路径，不含 LLM 网络时延；`service.reset` 在计时外 |
| 2026-09-14 | 407812e | Harness call overhead | shop/list_pending_refunds 单次调用 p50/p99 (ms) | 0.002 / 0.003 ms | 权限校验+审计+进程内分发，不含外部平台耗时 |

数值为单机单次基线，仅作后续回归对比锚点，不代表生产容量；跨环境对比需在同一硬件/负载下重跑脚本并追加行。

## Known limits

- 首版基线全部是进程内确定性路径（规则决策、模拟 shop），端到端墙钟约 2.3 ms；真实 LLM 决策与外部平台 IO 接入后预计成为主导时延，届时补测并分列"引擎开销 / 外部耗时"。
- 尚未测量容量上限（并发数、单图节点数、记忆库规模）：当前 Demo 为单进程同步执行，并发与容量随 Phase 2 多实例部署（14 D5/D6）评估。
