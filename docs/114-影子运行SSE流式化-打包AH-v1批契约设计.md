# 打包 AH：影子运行 SSE 流式化 — v1 批契约设计

> 号段：U1317–U1323｜承接 docs/14 D26 剩余「影子 SSE（实时影子）」｜形状权威：本文｜2026-10-09
> 状态：📝 已立项（落码另立批）

## 1. 背景与现状（落码前实证）

D26 录制回放产品化已多批取回。影子方向现状（读码确认）：

- **影子进程内 v1**（docs/33，2026-09-21）：`POST /api/graphs/{graph_id}/shadow-runs` **同步**跑完整个决策链后一次性返回 `ShadowRun`；READ 透传、写能力短路为 `SHADOW_DRY_RUN`，预置全部 human_approval approved、wait 预置秒过，独立 `ApprovalBroker` 与 `Tracer`，不写 run_store/RunRecord、不触发告警与灰度门控、不产 tool_metric；异常也沉淀 `status="error"` 记录（**HTTP 仍 201，不抛 5xx**）。
- **影子 PG 化**（打包 H4，迁移 028 `shadow_runs`，2026-09-25）：`PgShadowStore` 与内存 `ShadowStore` 方法一比一，REST 与前端零改动。
- **缺口**：影子运行**仅 sync、无 SSE**——运营点「发起」后只能等整链跑完，看不到节点级实时进度（docs/14 D26 多条注记均把「影子 SSE」列为仍缓做）。
- **可复用**：`POST /api/graphs/{graph_id}/run/stream` 的真流式骨架（后台线程 ＋ `queue.Queue` ＋ SSE 帧），前端 `streamRun` 的 POST ＋ ReadableStream 解析（`apiClient.ts:1247`）。

## 2. 本批范围（切片收敛）

**把影子发起从 sync 扩为 SSE 流式，实时下发节点事件；影子纪律与沉淀形状逐字不变。**

### 2.1 做

1. **后端新端点** `POST /api/graphs/{graph_id}/shadow-runs/stream`（`operate` 权限，返回 `StreamingResponse`，`media_type="text/event-stream"`）：
   - 请求线程照 sync 版解析并固定：`services`、`graph`、`inputs`（含 `preset_all_approvals`/`preset_all_wait_events`，经租户 graph resolver）、`human_outcome`、`registry`、`tool_permissions`、`node_index`、独立 `Tracer` 与独立 `ApprovalBroker`。
   - `run_graph(shadow=True, …)` 在后台 daemon 线程执行；事件经 `queue.Queue` 实时下发。
   - emit：**每个**事件 put 上屏；同时照 sync 版收集顶层 `node_end`（不带 `subgraphPath`）到 `top_events`。
   - 终态：无论 completed 还是异常，都照 sync 版 `extract_shadow_events(top_events, node_index, tool_permissions)` 后 `shadow_store.add(...)` 产出**一条 record**（异常时 `status="error"`、`error=...`），经终帧下发。
2. **SSE 帧**：
   - 节点帧：`event: node_start` / `event: node_end`，`data` 为事件原样 JSON（照 run/stream 用事件自身 `type`）。
   - 终帧：`event: result`，`data` 为完整 `ShadowRun`（`model_dump`，含 `id`/`status`/`decisions`/`tool_intents`/`comparison` 等）。
   - **不设 `event: error`**：与 sync 版对齐——影子异常不 HTTP 报错，统一在终帧 record 以 `status="error"` 表达；端点装配/加载阶段的错误（图不存在 404、坏入参 422、鉴权 401/403）仍在流建立前按常规状态码返回。
3. **前端**：
   - `apiClient` 加 `streamShadowRun(graphId, body, onEvent)`：POST 流端点，按 `\n\n` 分帧解析；`node_start`/`node_end` 转发 `onEvent`；`event: result` resolve 为 `ShadowRun`；`!ok` 错误处理照 `streamRun`（401 回登录、detail 走 runtime 文案）。
   - `ShadowRunModal`：发起改走流式，提交后展示「实时进度」（节点 id ＋ 状态 Tag：进行中/完成），终帧后 `setRun` 复用现有结果渲染；human_outcome 入参不变。
   - i18n zh/en 加少量键（实时进度标题、节点状态），过 PARITY、en 零汉字。

### 2.2 不做（继续缓做）

- **影子自动旁路（线上流量自动旁路录制）**：需真实线上流量/生产部署，触发条件外部化，不属本批。
- 影子结果的趋势报表、定时/批量自动对比、与自动回滚联动。
- 用例/报告的跨租户共享、长保留归档。
- 子图内部节点的实时进度细分（本批节点帧虽透传子图事件，但进度列表只按顶层 node_end 归集，与 shadow 顶层口径一致）。

## 3. 错误码

- 不新增错误码、不新增迁移、不新增依赖、无 ADR。
- 流建立前错误沿用：图不存在 404、inputs 非法 422、未登录 401、权限不足 403。

## 4. 验收用例（U1317–U1323）

- **U1317**（完成流）：stream 端点依次下发 node_start/node_end，终帧 `event: result` 为 completed 的 ShadowRun；`shadow_store` 恰沉淀一条且与终帧内容一致。
- **U1318**（影子纪律）：流式影子不写 run_store、不产 RunRecord（监控 runs 为空）、不发 tool_metric；预置审批秒过、不挂起。
- **U1319**（异常沉淀）：run_graph 抛异常时仍下发终帧、record `status="error"` 且带 error，HTTP 不报错；store 沉淀为 error。
- **U1320**（鉴权）：未登录 401、无 operate 权限（viewer）403；未知 graph 404。
- **U1321**（前端 apiClient）：`streamShadowRun` 正确分帧——节点事件转 onEvent、终帧 resolve ShadowRun；`!ok` 时 reject（401 触发回登录）。
- **U1322**（前端 Modal）：发起显示节点实时进度（进行中→完成），终帧渲染完整结果（决策/意图/对比）。
- **U1323**（前端 i18n/parity）：新增键 zh/en 齐、en 零汉字。

## 5. 原子提交序（落码批，英文 message、无 co-author、默认不 push）

1. `docs: propose package AH (shadow run SSE streaming)`（本文、docs/08 立项块、docs/14 D26 注记）
2. `feat(api): add shadow run SSE streaming endpoint`
3. `test(api): cover shadow run streaming endpoint`（U1317–U1320）
4. `feat(frontend): add shadow run streaming and live progress`（apiClient、Modal、i18n）
5. `test(frontend): cover shadow run streaming`（U1321–U1323）
6. `docs: close out package AH`（本文状态/收口注记、docs/13、docs/08、docs/14 D26、CHANGELOG、handoff）

门：后端全量、前端 vitest/oxlint/tsc/build；数字先跑后写。零新依赖/零迁移/无 ADR。

## 6. 收口注记

（落码收口后填写）
