# 打包 AJ：节点级 `on_error` 真执行（局部异常）v1 批契约设计

> **承接**：原始需求《编辑后台组件设计.docx》§5.9 第一条「局部异常：节点级 on_error，可配置重试、跳过、跳转到指定节点」（docs/04 §5.9 同文）；docs/107 §四功能域 2 缺口「状态机子图、全局异常+自愈仍缺」的局部半边。
> **用户决策**：2026-10-10「那你搞」批准；基线实测报告＝下节 §1。
> **形状权威**：本文件。落码偏差一律回填 §6 收口注记，不回溯改正文。

## 1. 基线实测（2026-10-10，先于一切设计）

**`retry.on_error` 今天是纯纸面契约**：

- DSL 已声明 `RetryConfig{max_retries:int=0, backoff:str="1s", timeout:int=30, on_error: Literal["stop","continue","jump_to"]="stop"}`（`graph/dsl.py:117-121`），前端 `nodeCatalog.ts` 有 `OnErrorStrategy` 与默认值 `{maxRetries:0, backoff:'1s', timeout:30, onError:'stop'}`，五张内置模板逐节点带 `on_error:"stop"`。
- **`graph/loader.py` 全文 0 处读取 `retry`/`on_error`/`max_retries`**（grep 实测）；`jump_to` 全仓仅出现在 DSL 声明与 NL 生成的提示词文本里。
- 真跑实测（trigger → ghost-adapter 工具 FAILED → 下游工具）：`on_error:"stop"` 下**下游照样执行、run 仍 completed**。即今天 stop/continue/jump_to 三个值行为完全等同——失败被结构化记录后图继续走。
- 例外出口只有两类：① 执行器抛异常（如 `AiDecisionUnavailable`）→ 穿透 → run failed／HTTP 500 结构化码；② 工具/子图/并行产出 FAILED → `_node_failure`/`_span_error` 只用于 span 标记，**不影响路由**。
- condition 表达式求值失败已有自己的 fail-safe 通道（走 defaultTarget，`expression_errors`），**不是**「节点失败」，本批不接管。

**推论**：本批不是"补三个小分支"，而是让一条从 W7 起就写在 DSL 里、从未被执行的字段**第一次生效**。默认 `stop` 一旦变真，存量图（模板全部 stop）在节点失败时的行为从「跑完且 completed」变为「就地终止且 run failed」——**这是有意的 fail-closed 行为变更**，不是回归；§5 列出受影响的既有断言与改判口径。

## 2. 范围决策

**D-1 本批只做「局部异常」一条**（需求 5.9 三条中的第一条）：

- ✅ `on_error` 三值真执行（stop/continue/jump_to）＋ `max_retries` 真执行（配合 `backoff` 解析）。
- ❌ **不在本批**：`retry.timeout` 字段（继续纸面、契约明示，强制执行需异步包帧属另一批）；「全局异常处理器」（运营体级统一降级）；「自愈/诊断子图」；「状态机子图」——后三者是需求 5.9 后两条与 5.5，另行立项（docs/14 新登 D63，见 §7）。
- 零新依赖／零迁移／无 ADR；纯引擎＋DSL＋前端属性面板。

**D-2 「节点失败」的定义**（与现有判定点同源，不另造第三套）：

1. 执行器抛异常（除 `RunCancelled`/`DebugStopped`/`RunSuperseded`/`SubgraphSuspendUnsupported` 四个控制流异常——它们永远穿透，**不触发** on_error，也不被 retry 拦截）；
2. 节点产出被 `_node_failure`/`_span_error` 判失败（工具 FAILED、subgraph failed、parallel join failed）。
- condition 表达式错误、loop/foreach 的 expression_error 出口、human_approval 拒绝分支、wait 超时按 onTimeout 走——**都不是失败**，各有自己的通道，不触发 on_error。
- 挂起（human_approval/wait 正常挂起）当然不是失败。

**D-3 三值语义**：

| on_error | 节点失败后 |
| --- | --- |
| `stop`（默认） | 抛 `RunNodeFailed(node_id, output)` 穿透执行器 → run 终态 **failed**，下游一律不执行。同步 `/run` 返回 500 结构化 `{code:"NODE_EXECUTION_FAILED", params:{nodeId}}`（与 `LLM_DECISION_UNAVAILABLE` 同形）；SSE 走既有 error 帧通道；`run_store.finish(status="failed")` 复用现有通用异常路径（`main.py` 已有，零改动）。 |
| `continue` | 失败产出照常落 `outputs`，下游照常执行，run 照常 completed——**即今天的实际行为**，本批把它变成显式可选而不是唯一行为。 |
| `jump_to` | 路由到 `retry.error_target` 指定的节点（错误处理分支），**正常后继一个都不执行**；error_target 节点自身的失败按它自己的 on_error 处理。成功路径完全不变（含多后继扇出）。 |

**D-4 `max_retries` 真执行**：

- 判定失败的同一次执行可重试至多 `max_retries` 次（总共 1+N 次）；重试对「异常」与「FAILED 产出」同一生效。
- `backoff` 只接受 `<数字>ms`／`<数字>s`（如 `500ms`、`1s`、`2.5s`），编译期校验，非法 422（新码 `NODE_RETRY_BACKOFF_INVALID`）；重试间隔按该值**固定** sleep（不做指数退避——`backoff` 字段语义就是间隔，指数退避属 D63 全局策略议题）。
- 重试仍失败 → 按 on_error 处理。产出纯超集带 `attempts: <int>`（≥1，含首次）；既有产出键一律不动。
- **重试不恢复副作用**：工具重试＝重新调用适配器，是否安全由工具幂等性保证（`is_idempotent` 已在适配器 Schema 声明），本批在文档写明、不替用户挡。

**D-5 `jump_to` 需要落点字段**：DSL `RetryConfig` 新增可选 `error_target: str | None = None`（纯超集）。编译期校验：

- `on_error="jump_to"` 且无 `error_target` → 422 新码 `NODE_ERROR_TARGET_REQUIRED`；
- `error_target` 指向不存在的节点或自身 → 422 新码 `NODE_ERROR_TARGET_INVALID`（params `{nodeId, errorTarget}`）；
- 非 jump_to 时配了 `error_target` → 允许保存但运行期不读（宽松超集，校验告警不做——与 DSL 现有风格一致）。

**D-6 路由实现**：仅 `on_error="jump_to"` 的节点的**普通出边**改走 conditional edges（route 读 `outputs[id].get("__on_error_target")`：失败时为 error_target、成功时为 None ⇒ 走原有后继列表；route 返回 list 保持扇出语义）。condition/loop/human/parallel 节点不允许配 jump_to（它们的出口语义已被条件路由占据，配了 → 422 `NODE_ERROR_TARGET_INVALID`）；stop/continue 节点路由零改动。`__on_error_target` 是内部键，不写进 `outputs` 的持久形态——执行器消费后从产出剔除，保持产出形状稳定。

**D-7 默认行为变更的明说**（本批唯一的兼容性议题）：DSL 与模板默认 `stop`。落地后「节点失败 ⇒ run failed 且下游不执行」成为默认行为；要旧行为（失败也跑完）须显式 `on_error="continue"`。五张内置模板维持 `stop` 不变（fail-closed 正是模板语义）。受影响既有断言按 §5 改判。

## 3. 前端

- `nodeCatalog.ts`：`RetryConfig`/`OnErrorStrategy` 加 `errorTarget?: string`；属性面板在 `onError=jump_to` 时显示「失败跳转节点」下拉（同图其它节点），缺省校验提示（前端只做提示，422 由后端兜底）；i18n zh/en 新键进 PARITY_PAIRS 守护。
- 运行视图零改动（失败节点本来就有 FAILED 呈现；jump_to 走到的节点就是普通节点执行）。

## 4. 错误码与通道

| 码 | 位置 | 说明 |
| --- | --- | --- |
| `NODE_EXECUTION_FAILED` | 运行期（HTTP 500/SSE/run 记录） | params `{nodeId}`；进 runtime.json zh/en 与 U1149/U1162 通道守护 |
| `NODE_ERROR_TARGET_REQUIRED` | DSL 422 | validation.json zh/en |
| `NODE_ERROR_TARGET_INVALID` | DSL 422 | validation.json zh/en |
| `NODE_RETRY_BACKOFF_INVALID` | DSL 422 | validation.json zh/en |

## 5. 验收用例（U1324–U1331，docs/13 转正式）

- **U1324 stop**：工具 FAILED 且 `on_error:"stop"` → run_graph 抛 `RunNodeFailed`、下游零执行、trace 含失败节点行；同步 API 500 `NODE_EXECUTION_FAILED`＋params.nodeId；run_store failed。
- **U1325 continue**：同图改 `continue` → 下游执行、run completed（钉住＝今天的行为未丢）。
- **U1326 jump_to**：失败 ⇒ 只执行 error_target（如 message/send 告警节点），正常后继零执行；成功 ⇒ error_target 零执行、原后继照常（含扇出两后继）。
- **U1327 retry**：monkeypatch 适配器第一次抛、第二次成 → `max_retries:1` 成功且 `attempts==2`；始终 FAILED → 重试耗尽后按 on_error 处理；`backoff:"2.5s"` 只解析不真睡（测试注入 sleep 桩）。
- **U1328 DSL 校验**：jump_to 缺 target → 422 REQUIRED；target 不存在/自身 → 422 INVALID；`backoff:"1h"` → 422 BACKOFF_INVALID；condition 配 jump_to → 422 INVALID。
- **U1329 控制流穿透**：`RunCancelled`/`RunSuperseded` 在 on_error=continue 节点上仍穿透（不触发 continue、不被 retry 吞）。
- **U1330 模板回归**：refund-auto 在 shop 缺注册（prod 演示面关）时 run failed（stop 语义）——改判既有「FAILED 也 completed」断言；五模板 DSL 全量校验过。
- **U1331 前端**：nodeCatalog errorTarget 序列化往返、onError=jump_to 显示下拉、i18n PARITY。

## 6. 收口注记（落码后回填）

**已收口（2026-10-10，dev，未 push）。**

- **提交序列**：`d24c72c` feat(graph) 三值真执行＋max_retries＋编译期三码＋API 500 映射；fix(evaluation) runner 执行副本 tool_call 降级 continue；test(graph) U1324–U1331 ＋ 既有断言按 D-7 改判；feat(frontend) errorTarget 面板＋snake_case 序列化打通。
- **门读数**：后端 **2596 passed／173 skipped／0 failed**（基线 2582/173 ＋ 新套件 14）；前端 vitest **972 passed／2 skipped**、oxlint 0/0、tsc 0、build 过。
- **偏差照实**：
  1. **parallel 区域不装配 on_error 包装**——区域内节点失败归汇聚网关按 joinStrategy 裁决（04 §5.4），stop 穿透会绕过 join 语义让整 run 假死；D-3 的 stop 语义仅作用于非区域节点。
  2. **调试会话不包 retry**——docs/28 §3.2 要求节点异常原样重抛（异常断点观测），RunNodeFailed 包装会破坏 paused 帧的原始异常形状。
  3. **evaluation runner 执行副本**把 tool_call 降级为 continue（评估语义＝遍历全图比对决策，工具失败不截断），决策类节点异常仍穿透为 case 级 error；不改写 graph_store。
  4. **前端键名鸿沟修复照实**：序列化层此前把 camelCase retry 直发后端被 pydantic 静默丢弃（前端面板配置从未到达运行期）——本批在 graphSerializer 做显式 camelCase↔snake_case 互转，链路首次真正打通。

## 7. 登记与联动

- docs/14 新登 **D63**「全局异常处理器＋自愈/诊断子图＋状态机子图」（需求 5.9 后两条＋5.5；触发＝本批落地后用户拍板全局降级策略形态）；docs/107 §四功能域 2 行注记「局部异常已由打包 AJ 落码」。
- 不改 docs/04 §5.9 原文，在该节末尾按惯例追加落码注记指向本文件。
