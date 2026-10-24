# 挂起超时转 suspended（onTimeout="suspend"）——打包 BS v1 批契约设计

> 承接：docs/08 §八 O-5 收口块末「下一候选：挂起超时转 suspended（引擎级：wait 超时落 suspended 待恢复而非 onTimeout reject/approve）——本轮未做，跨模块风险大，列为下一候选待拍板」；docs/135 §6.9（O-5 彻底解耦收口）；docs/14 无对应 D 号（工程级能力扩展，非新待办）。
> 批次名：打包 BS（BR 已占，docs/134；AT→BR 连续命名已用尽，接续 BS）。
> 用例号：U1528 起（当前最大 U1527，docs/13）。
> 状态：✅ 已落码收口（2026-10-22；落码与反向门见 docs/08 §八「打包 BS 落码收口」，用例 U1528–U1534 见 docs/13 打包 BS 段，后端全量 2940 passed／213 skipped／0 failed）。
> 形状权威：本文件。零新迁移／零新端点／零新错误码／零新依赖／无 ADR。

## 1. 基线实核（2026-10-22 读码）

**挂起机制现状**（三条路径一致）：loader 在挂起点（human_approval / wait event）经 `_emit_frame` → API 层注入的 `frame_sink`（`main.py:420 _frame_sink_for`）**先落 run 状态 suspended（所有后端）**，PG 后端再写 `interruptions` 帧；随后 `broker.wait(token)` 阻塞等待。恢复经既有路径（审批决策端点 `broker.resolve` → 续跑走 approvedTarget/rejectedTarget；事件 `signal` → 续跑走 matchedEventKey）。

**超时现状（两条挂起节点各一）**：

| 节点 | 超时行为 | 枚举（dsl.py） | 代码位 |
| --- | --- | --- | --- |
| human_approval | `broker.wait` 返回 None → `complete_timeout(token, fallback)` 落 **reject/approve**（缺省 reject）→ 继续走 approvedTarget/rejectedTarget | `APPROVAL_TIMEOUT_ACTIONS = ("approve", "reject")`（:66） | `_await_human_approval`（loader.py:1675-1690） |
| wait（event） | `event_wait_broker.wait` 返回 None → onTimeout=="fail" 则 raise `WaitNodeFailure`，否则 **continue**（output signaled=False, resolvedBy=timeout） | `WAIT_TIMEOUT_POLICIES = ("continue", "fail")`（:62） | loader.py:1024-1045 |

**三个关键事实（决定形状）**：
1. 超时**不是**新的挂起——挂起期间 run 已是 suspended（frame_sink 设的）、帧已在表。所谓"转 suspended"＝**超时后不落决定、不 continue/fail，run 维持 suspended**（把"超时自动决"改为"超时继续挂起等人工"）。
2. `complete_timeout` 是唯一把 token 从 pending 转终态的执行路径；超时后不调它，token 保持 pending、帧保留——人工后续仍可经既有决策端点 resolve。
3. 穿透异常族已成熟：`RunCancelled/DebugStopped/RunSuperseded/SubgraphSuspendUnsupported` 在 loader retry/自愈包装（:1220/:1451/:2212）与三条 API 路径（同步 `_run_sync_cycle` :5862/:5875、续跑 :407、流式 worker :6145/:6152）各有**一致的"不写终态、不清帧、不发终帧"**处理先例（RunSuperseded 家族）。本批新异常加入同一族。

## 2. 决策（docs/08「待拍板」按推荐口径拍板，全部记录在此）

**BS-D1（拍板点：新枚举值形态）**：`human_approval.onTimeout` 与 `wait.onTimeout` 均**超集扩展**加 `"suspend"`；**缺省不变**（human_approval 缺省 reject、wait 缺省 continue），未配置该值的既有图逐字不变、零兼容性影响。推荐理由＝显式声明优于隐式行为切换（与打包 AJ 的 `retry.on_error` 同族：行为变更必须写进 DSL 让图作者看见）。

**BS-D2（拍板点：超时后语义）**：超时后**不落决定、不 continue/fail**；run 保持 suspended、挂起帧保留、token 保持 pending，等待人工后续恢复。SSE 流：挂起时已发 node_start（带 wait 载荷），超时转 suspended 发一条提示帧（`__wait_timeout_suspended__`，含 node_id 与 token）后流结束——run 未终态，前端据帧展示「审批/事件超时，仍挂起待决」。

**BS-D3（拍板点：恢复路径）**：完全复用既有机制、不新增端点——
- human_approval：人工经既有审批决策端点（带 token + decision）`broker.resolve` → 续跑走 approvedTarget/rejectedTarget；token 已超时仍 pending 可决（`complete_timeout` 未调即未消费）。
- wait event：事件后续 signal → 既有续跑路径恢复；帧 deadline 已过时**以 signal 为准继续**（signal 已满足等待条件，不再扣剩余时长）。

**BS-D4（拍板点：三条 API 路径处理）**：新增 `WaitTimeoutSuspend` 异常（loader 定义，携带 node_id/token），加入 loader 穿透族；同步 `_run_sync_cycle`、续跑段、流式 worker 各加显式 except——**不 finish、不 record_run 终态、不清帧**；同步返回 `(CycleOutcome(status="suspended", stop_run=True), RunGraphResponse(status="suspended", trace=[...]))`（与 RunSuperseded 同形）；流式发提示帧后结束；续跑段打 INFO 保持 suspended（与 :407 RunSuperseded 同形）。

**BS-D5（边界）**：零新错误码（沿打包 AK 撤销 `MEMORY_EXTRACT_FAILED` 的理由：永不发射的码会混进 U1149 可译集合造假覆盖——超时转 suspended 不是失败，无码可发）、零新迁移（帧/列全复用）、零新端点、零新依赖、无 ADR；**不解锁多副本**（D19/D20/D27/D31/D32 与单副本三道闸一字不动）；不碰 D36（重放语义）、D44（圈帧）、D56（安全重放）。

## 3. 验收（docs/13 登记，U1528 起）

| 用例 | 断言 |
| --- | --- |
| U1528 | DSL：`human_approval.onTimeout="suspend"` 与 `wait.onTimeout="suspend"` 编译通过；非法值 422（既有校验覆盖，验证超集扩展未破坏） |
| U1529 | human_approval 超时 suspend：run 保持 **suspended**（非 failed）、帧保留、token 保持 pending；人工 decision=approved → 续跑 completed 走 **approvedTarget** |
| U1530 | human_approval 超时 suspend：人工 decision=rejected → 续跑 completed 走 **rejectedTarget** |
| U1531 | wait event 超时 suspend：run 保持 suspended；事件 signal → 续跑 completed（deadline 已过仍以 signal 继续） |
| U1532 | 兼容性：缺省 onTimeout（human_approval=reject、wait=continue）行为**逐字不变**（录制回放黄金用例零回归） |
| U1533 | 反向门：摘掉同步路径 except 分支 → 超时 suspend 被 `except Exception` 标 **failed**（红） |
| U1534 | 反向门：摘掉 loader 穿透族 → `WaitTimeoutSuspend` 被 retry/自愈包装吞掉或二次包装（红） |

## 4. 落码原子序（另批执行，先 docs-only 后动代码）

1. `feat(engine)`：loader 定义 `WaitTimeoutSuspend`；`_await_human_approval` 超时且 onTimeout=="suspend" → raise；wait event 超时且 onTimeout=="suspend" → raise；三处穿透族加成员（:1220/:1451/:2212）。
2. `feat(engine)`：dsl.py 两枚举超集扩展（`WAIT_TIMEOUT_POLICIES`/`APPROVAL_TIMEOUT_ACTIONS` 加 "suspend"）。
3. `feat(api)`：同步 `_run_sync_cycle` 加 `except WaitTimeoutSuspend`（suspended 返回、不 finish、不进 evaluate_after_run）；续跑段（:407 旁）加同形分支；流式 worker（:6145 旁）加分支发 `__wait_timeout_suspended__` 帧。
4. `test(engine)`：U1528–U1534（新文件 `tests/test_wait_timeout_suspend.py`）。
5. docs 收口原子：docs/08 收口块、docs/13 U 号转正式、CHANGELOG、handoff。

## 5. 同步矩阵

docs/03（DSL 两节点 onTimeout 枚举注记）、docs/04 §5.x（wait/human_approval 运行期语义追加 suspend 行为）、docs/12（无新端点；run 状态枚举不动）、docs/13（U1528–U1534）、docs/08（本批收口块）、CHANGELOG、handoff、README 能力表（如涉）。

## 6. 不解除

D36（重放语义）、D44（圈帧）、D56（安全重放）、D19/D20/D27/D31/D32 与单副本三道闸；O-5 彻底解耦本身已于 2026-10-20 收口（docs/135 §6.9），本批是它的**行为补全**而非追加缺口。
