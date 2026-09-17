# Benchmark

Atlas 性能与容量基准。范围与结果记录表（参照 agent-world 惯例，代码阶段填充实测）。

## How to run

```bash
.venv/bin/python scripts/benchmark.py [--iterations 300]
# 设置 DATABASE_URL 时追加 DB ping（连接池取连接 + SELECT 1）一项

# 前端编辑器校验分层基准（M4 批 3 ⑫，零新依赖；常规 pnpm test 默认跳过）：
cd frontend && pnpm bench    # 等价 RUN_BENCH=1 vitest run bench/validation.perf.test.ts
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

2026-09-14 首版基线覆盖：Loop 主循环吞吐、Graph 编译时延、退款端到端时延、Harness 调用开销（+ 可选 DB ping）。**暂不可测、待接入后补**：LLM 决策延迟（随真实供应商，`LITELLM_MODEL`）、Redis/pgvector 记忆读写（随 11 S1 记忆层；当前 DB 层仅有 ping 探针）、长流程内存增长（随递归自动化引擎）。**并行扇出节点（parallel）已于 2026-09-14 端到端落地**（04 §5.4），N=4 分支 fan-out/fan-in 基准场景已补入 `scripts/benchmark.py`（见 Results）。

**前端编辑器校验分层基准（2026-09-17，M4 批 3 ⑫）**：脚本 `frontend/bench/validation.perf.test.ts`（vitest，零新依赖；`RUN_BENCH=1` 门控，常规 `pnpm test` 跳过）。负载为链式图 trigger→tool-1…tool-(N-1)，每个工具节点 params 模板引用直接上游输出（L2 逐节点有活干），N=200/500；每场景 warmup 10 + 采样 50 报 p50/p95。测量项：L1 全量字段校验、L2 冷启动全量（含 ScopeIndex 构建）、L2 热态单节点增量（编辑防抖稳态）、L3 冷启动/结构签名命中、`validateGraph` 全量聚合（L1+L2+L3）。目的是验证 M4 增量调度在大图层下"输入不卡顿"：稳态编辑走增量（L1 同步、L2 防抖、L3 idle），全量聚合只在换图/首挂发生一次。

## Results

| Date | Baseline (commit / version) | Scenario | Metric | Value | Notes |
|---|---|---|---|---|---|
| 2026-09-14 | 407812e | OODA loop throughput | loop latency p50/p99 (ms)；吞吐 loops/s（p50 倒数） | 2.05 / 2.43 ms；487.9 loops/s | 确定性占位节点，无 LLM/IO；CPython 3.11.15，macOS 26.5.2 arm64；300 次/场景 |
| 2026-09-14 | 407812e | Graph compile latency | 3 节点退款图单次编译 p50/p99 (ms) | 1.81 / 2.07 ms | DSL→LangGraph StateGraph 装配，进程内 |
| 2026-09-14 | 407812e | Refund e2e latency（12345 auto-refund） | 触发→规则决策→shop 执行 p50/p99 (ms) | 2.28 / 2.67 ms | 规则决策路径，不含 LLM 网络时延；`service.reset` 在计时外 |
| 2026-09-14 | 407812e | Harness call overhead | shop/list_pending_refunds 单次调用 p50/p99 (ms) | 0.002 / 0.003 ms | 权限校验+审计+进程内分发，不含外部平台耗时 |
| 2026-09-14 | 8cf8dd9 | Parallel fan-out/fan-in latency（N=4 branches） | trigger→parallel→4 只读分支→join 单次运行 p50/p99 (ms) | 8.70 / 20.12 ms | 合成 `__join__` 屏障 + 就绪等待超步（p99 含 wait 超步抖动）；分支均为 list_pending_refunds 只读，CPython 3.11.15，macOS 26.5.2 arm64，300 次 |
| 2026-09-17 | 875922b | 前端 L1 全量字段校验（N=200 链式） | 全节点 validateNodeL1 p50/p95 (ms) | 0.648 / 1.242 ms | Node v22.23.2，macOS 27.0 arm64，vitest 5，warmup 10 + 采样 50 |
| 2026-09-17 | 875922b | 前端 L2 冷启动全量（N=200 链式） | 新引擎 runL2 全节点（含 ScopeIndex 构建）p50/p95 (ms) | 4.837 / 6.318 ms | 每节点 params 模板引用直接上游；换图/首挂一次性成本 |
| 2026-09-17 | 875922b | 前端 L2 热态单节点增量（N=200 链式） | ScopeIndex 缓存命中、1 节点到期 p50/p95 (ms) | 0.765 / 1.814 ms | 编辑防抖层稳态；O(n) 签名遍历在 200 节点亚毫秒~1.8ms |
| 2026-09-17 | 875922b | 前端 L3 冷启动（N=200 链式） | validateL3 不可达/环 p50/p95 (ms) | 0.439 / 1.057 ms | 同构 dsl.py 前端预判 |
| 2026-09-17 | 875922b | 前端 L3 热态（N=200 链式） | 结构签名命中（编辑模板文本）p50/p95 (ms) | 0.063 / 0.098 ms | 记忆化命中≈直接返回缓存 |
| 2026-09-17 | 875922b | 前端全量聚合 L1+L2+L3（N=200 链式） | validateGraph 无缓存一次 p50/p95 (ms) | 5.858 / 7.195 ms | 仅换图/首挂发生 |
| 2026-09-17 | 875922b | 前端 L1 全量字段校验（N=500 链式） | 全节点 validateNodeL1 p50/p95 (ms) | 0.519 / 0.953 ms | tool_call schema 简单，亚毫秒段计时噪声主导，与 N=200 同量级 |
| 2026-09-17 | 875922b | 前端 L2 冷启动全量（N=500 链式） | 新引擎 runL2 全节点（含 ScopeIndex 构建）p50/p95 (ms) | 37.665 / 48.203 ms | 链式图可见性 BFS 总和 O(n²)，随 n 非线性；仅换图一次性 |
| 2026-09-17 | 875922b | 前端 L2 热态单节点增量（N=500 链式） | ScopeIndex 缓存命中、1 节点到期 p50/p95 (ms) | 7.514 / 9.956 ms | 每轮 O(n) configSignature 遍历；仍低于一帧 16.7ms，防抖层异步不卡输入 |
| 2026-09-17 | 875922b | 前端 L3 冷启动（N=500 链式） | validateL3 不可达/环 p50/p95 (ms) | 0.726 / 1.308 ms | 近线性 |
| 2026-09-17 | 875922b | 前端 L3 热态（N=500 链式） | 结构签名命中 p50/p95 (ms) | 0.189 / 0.357 ms | 编辑模板文本不触发重算 |
| 2026-09-17 | 875922b | 前端全量聚合 L1+L2+L3（N=500 链式） | validateGraph 无缓存一次 p50/p95 (ms) | 45.266 / 69.828 ms | 换图/首挂一次性；稳态编辑走增量不在此路径 |

数值为单机单次基线，仅作后续回归对比锚点，不代表生产容量；跨环境对比需在同一硬件/负载下重跑脚本并追加行。

## Known limits

- 首版基线全部是进程内确定性路径（规则决策、模拟 shop），端到端墙钟约 2.3 ms；真实 LLM 决策与外部平台 IO 接入后预计成为主导时延，届时补测并分列"引擎开销 / 外部耗时"。
- 尚未测量容量上限（并发数、单图节点数、记忆库规模）：当前 Demo 为单进程同步执行，并发与容量随 Phase 2 多实例部署（14 D5/D6）评估。
- 前端校验基准为合成链式图（引用关系最密的规则形态之一）：L2 冷启动含 ScopeIndex 构建，链式图每节点反向可达 BFS 总和为 O(n²)，500 节点 p95 ≈ 48ms，但仅在换图/首挂一次性发生；稳态编辑走 L1 同步/L2 防抖/L3 idle 增量路径，500 节点单节点编辑 p95 < 10ms（低于一帧 16.7ms）。Web Worker 仍不引入——若真实编辑在目标机型实测掉帧，再按 04 §6.5 扩展条另开 ADR（阈值实证判据，非基准推测）。
