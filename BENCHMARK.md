# Benchmark

Atlas 性能与容量基准。范围与结果记录表（参照 agent-world 惯例，代码阶段填充实测）。

## How to run

> TODO: 基准框架建立后填入具体命令（计划：pytest-benchmark 或独立基准脚本）。

## Scope

以下为待测量项（随基准落地更新，依据 [docs/06-运行时与质量保障.md](docs/06-运行时与质量保障.md) 与 [docs/13-测试用例清单.md](docs/13-测试用例清单.md)）：

- Loop 主循环吞吐（OODA 循环 / 秒）
- 决策节点延迟（单步决策 p50/p99）
- Graph 执行吞吐与并行扇出（节点 / 秒，fan-out/fan-in）
- 记忆系统读写延迟（Redis 短期 / pgvector 长期检索）
- Harness 调用开销（网关路由 + 工具调用，不含外部平台耗时）
- 端到端流程时延（典型审批流程：触发 → AI 决策 → 工具调用 → 完成）
- 长流程内存/事件日志增长（递归自动化引擎）

## Results

| Date | Baseline (commit / version) | Scenario | Metric | Value | Notes |
|---|---|---|---|---|---|
| — | — | — | — | — | — |

## Known limits

> TODO: 记录已测量的容量上限与资源天花板（如并发数、单图节点数、记忆库规模）。
