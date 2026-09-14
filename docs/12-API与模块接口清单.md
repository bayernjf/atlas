# Atlas API 与模块接口清单

> **来源**：06 运行时代码示例（LoopState / HarnessAdapter 接口 / Web 适配器）+ 04 组件设计（节点接口 / 适配器分层 / 注册发现）+ 08 Demo 技术栈（FastAPI 入口）+ 02 架构（gRPC 统一接口）。本文为**接口契约清单**：模块间接口、服务接口、前端 API，供 AI 落码时对齐边界。
> **AI 使用提示**：实现模块时先对齐本清单的接口签名；与 06 代码示例冲突时以 06 原文为准，本清单为汇总视图。

## 1. 引擎内部接口（Python，依据 06 6.1-6.2）

### 1.1 Loop 状态（LoopState，原文 06 6.1）

```python
class LoopState(TypedDict):
    goal: str
    messages: list          # 对话/执行历史
    current_node: str
    observations: list      # 感知结果
    variables: dict         # 会话变量
    status: str             # running/paused/completed/error
    memory_id: str
```

### 1.2 Loop 节点函数签名（原文 06 6.1-6.2）

| 函数 | 签名 | 职责 |
|---|---|---|
| observe_node | `(state: LoopState) -> dict` | 调用 Harness 感知当前状态 |
| orient_node | `(state: LoopState) -> dict` | 分析数据，定位进展 |
| decide_node | `(state: LoopState) -> dict` | LLM 决策下一步；置信度 <0.6 → paused + request_human（原文 6.2） |
| act_node | `(state: LoopState) -> dict` | 执行工具/操作 |
| reflect_node | `(state: LoopState) -> dict` | 阶段性反思 |

### 1.3 LangGraph 主循环（原文 06 6.1）

```
graph = StateGraph(LoopState)
add_node: observe → orient → decide → act → reflect
edges: observe→orient
       orient→(conditional) should_continue
       decide→(conditional) should_act_or_wait
       act→(conditional) should_reflect_or_continue
```

## 2. Harness Gateway 接口（依据 06 6.4，Go 接口定义）

```go
type HarnessAdapter interface {
    ListCapabilities(ctx context.Context) ([]Capability, error)
    Execute(ctx context.Context, req ActionRequest) (ActionResult, error)
    Observe(ctx context.Context, req ObserveRequest) (Observation, error)
}

type ActionRequest struct {
    CapabilityName string
    Parameters     map[string]interface{}
    Context        map[string]interface{}
    Timeout        time.Duration
}

type ActionResult struct {
    Status       string          // SUCCESS / PARTIAL / FAILED
    Output       interface{}
    Screenshots  [][]byte
    Error        *StructuredError
}
```

> 说明：02 架构提到"统一 gRPC 接口"；Demo 阶段（08 7.2）用 Python/FastAPI 实现同构接口，Go 化留待 Phase 2（见 10 T5）。

## 3. 适配器/节点通用接口（依据 04 3.2 / 04 4.3 / 04 4.4）

### 3.1 节点统一接口（04 3.2 node Schema）

```
node:
  id, type, name, description
  config: object          # 节点类型专属配置
  inputs: [{name, source, required, default}]
  outputs: [{name, type}]
  retry: {max_retries, backoff, timeout, on_error}
```

### 3.2 工具注册（04 4.3 tool Schema + 04 4.4 注册发现）

```
注册: 适配器启动 → 向 Harness Gateway 声明能力与工具列表
发现: 编辑后台 → 网关查询可用适配器 → 画布可用
健康: 适配器定期心跳，不可用置灰
tool: {id, name, description, adapter_id, action,
       input_schema, output_schema, permission, timeout, retry_policy, is_idempotent}
```

### 3.3 Web 适配器三层定位（06 6.5）

```
click(element_desc, selector=None):
  层1 精确选择器（selector, timeout=3000）
  层2 视觉语义定位（screenshot → omni_parser.parse → llm_match，confidence>0.7 → click）
  层3 LLM 全图推理（vision_llm.analyze → 坐标 → click）
  全部失败 → failed("元素未找到，三层定位均失败")
```

## 4. 记忆检索接口（依据 06 6.2 / 05 2.3）

```python
memory_retriever.query(goal: str, recent_messages: list) -> list
# 依据 memory_config 分层检索：working_memory(Redis) / summary_memory(PostgreSQL)
#   / fact_memory(pgvector, confidence≥0.85) / case_memory(相似度阈值) / preference_memory
```

## 5. 后端服务 API（依据 08 7.2 /api/main.py FastAPI 入口）

> 以下路径为按 Demo 需求推导的 REST 端点清单，字段以 03/04/05 Schema 为准；正式定义待落码时随 OpenAPI 生成。

| 方法 | 路径 | 功能 | 关联 |
|---|---|---|---|
| POST | /api/graphs | 保存 Graph 定义（DSL） | node_schema / graph_definition |
| GET | /api/graphs/{id} | 读取 Graph | — |
| POST | /api/graphs/{id}/compile | DSL → LangGraph 编译（08 7.1 W7-W8） | 02 Graph DSL |
| POST | /api/graphs/{id}/run | 编译并运行，返回状态/节点产出/执行轨迹；请求体 `{"inputs": {...}}`，inputs 同名键覆盖全局变量且整体作为 trigger 节点 webhook 载荷 `context.payload`（W9-W10 接入真实决策/适配器） | 02 Graph DSL / LoopState |
| POST | /api/graphs/{id}/run/stream | SSE 流式运行（W9-W10）：事件 `node_start`/`node_end`/最终 `result`，供画布实时进度（验收标准 5）；condition 节点的 node_end 事件 data 含 `{branch, target, evaluation, expression_errors}`（契约 04 §5.2），loop 节点含 `{mode, iterations, index, target, exitReason, expression_errors}`（契约 04 §5.3），parallel 节点扇出时 data 为 running 占位、joinTarget 的 node_end 前该产出被覆盖为终态 `{mode, joinStrategy, status, branches, result, joinTarget}`（契约 04 §5.4），wait 节点的 node_end 事件 data 含 `{mode:"wait", waitType:"duration", durationSeconds}`（契约 04 §5.5；node_start 后同步阻塞等待），human_approval 节点的 node_start 事件 data 含 `approval:{token, summary, approver, timeoutSeconds}`、node_end 含 `{mode:"human_approval", decision, target, token, summary, approver, resolvedBy}`（契约 04 §5.6）；**Phase 2 第五项起本端点为真流式**——run_graph 在后台线程执行、事件经 queue 实时下发（旧实现先跑完再回放，human_approval 会因收不到 node_start 而死锁） | 08 7.1 |

> condition 节点（Phase 2 首版）运行结果写入 `outputs[condition_id] = {branch, target, evaluation:[{label,expression,result}], expression_errors:[string]}`（默认分支 `branch="__default__"`）；执行轨迹 messages 增一行 `condition-x: branch=… → target`。短路求值与 fail-safe 语义见 04 §5.2、06 §6.1。
>
> loop 节点（Phase 2 第二项，v1 仅条件循环）运行结果写入 `outputs[loop_id] = {mode:"while", iterations, index, target, exitReason, expression_errors:[string]}`，节点每轮重入时该产出被覆盖更新；exitReason ∈ `condition_false`/`max_iterations`/`expression_error`/null；trace 增 `loop-x: continue (i/max) → body` 与 `loop-x: exit (reason) after N → exit` 行。循环体内节点可引用 `{{loop-x.index}}`。fail-safe 与 recursion_limit 派生见 04 §5.3、06 §6.1。
>
> parallel 节点（Phase 2 第三项，v1 静态扇出/扇入）运行结果写入 `outputs[parallel_id] = {mode:"parallel", joinStrategy:"all_success"|"all_completed", status:"success"|"failed", branches:[{label,target,status,error}], result:{<分支入口节点id>: <末端节点产出>}, joinTarget}`；入口先写 running 占位，合成网关 `__join__<id>` 汇聚时（joinTarget 执行前）覆盖为终态并以 parallel 节点自身补发第二次 node_end。分支路径上任一节点 `result.status=="FAILED"` 即该分支失败；all_success 下有失败时整体 `status="failed"` 但 joinTarget 照常执行、run 仍 completed（fail-safe）。trace 增 `parallel-x: fork N branches → a, b` 与 `parallel-x: joined (all_success) success` / `parallel-x: joined (all_success) failed: <label>（<error>）` 行。下游引用形如 `{{parallel-x.status}}`、`{{parallel-x.result.tool-a.result.status}}`（result 以入口节点 id 为键）。扇出/barrier/fail-safe 与 outputs 按键合并 reducer 见 04 §5.4、06 §6.1。

> wait 节点（Phase 2 第四项，v1 仅定时等待）运行结果写入 `outputs[wait_id] = {mode:"wait", waitType:"duration", durationSeconds: <int>}`；执行器 node_start 后同步 `time.sleep(durationSeconds)`（1-600 秒整数常量，线程池工作线程内阻塞），到时沿唯一普通边继续。trace 增 `wait-x: waited 5s` 行。下游引用形如 `{{wait-x.durationSeconds}}`。事件等待缓做 14 D19，语义见 04 §5.5、06 §6.1。
>
> human_approval 节点（Phase 2 第五项，v1 进程内审批信号）运行结果写入 `outputs[human_id] = {mode:"human_approval", decision:"approved"|"rejected", target, token, summary, approver, resolvedBy:"human"|"input"|"timeout"}`；执行器在 `ApprovalBroker`（模块级单例，可注入）登记 pending 后阻塞，node_start 携带 `approval` 载荷。决策三来源：REST 人工放行、run inputs 预置 `{"approvals":{"<node-id>":"approved"|"rejected"}}`（非交互/测试）、超时按 onTimeout（10-3600 秒，默认 reject）自动决策；两条出边全 conditional，按 decision 路由 approvedTarget/rejectedTarget。trace 增 `human-x: approved (human) → tool-y` / `… rejected (timeout) → tool-z` 行。下游引用形如 `{{human-x.decision}}`。持久化中断-恢复缓做 14 D20，语义见 04 §5.6、06 §6.1。
| POST | /api/operators | 创建运营体（镜像） | operator |
| POST | /api/operators/{id}/run | 启动 Loop | LoopState |
| GET | /api/operators/{id}/status | 运行状态/进度（验收标准 5：画布实时显示） | LoopState.status |
| POST | /api/operators/{id}/pause / resume | 暂停/恢复（人机协作） | status: paused |
| GET | /api/adapters | 适配器列表（注册发现；W9-W10 已落码，返回 shop 适配器及其能力/权限/幂等标记） | adapter_schema |
| POST | /api/adapters/{id}/tools | 工具查询 | tool |
| GET | /api/operations/{id}/log | 执行日志/审计（06 安全清单） | 审计 |
| POST | /api/nl/generate | 自然语言 → 流程草稿（验收标准 6；W9-W10 已落码：LLM 优先、退款规则模板兜底，无法识别 422） | 08 7.2 |
| POST | /api/demo/shop/login | Demo 商家平台登录（demo/demo，W9-W10） | — |
| GET | /api/demo/shop/orders | Demo 待处理退款单（需登录，W9-W10） | — |
| POST | /api/demo/reset | 重置 Demo 数据（店铺恢复 5 笔种子退款单、清空已保存图与登录态、清空 pending 审批请求，Phase 1 种子客户体验，W10 后） | — |
| GET | /api/approvals | 列出当前 pending 审批请求（`{items:[{token, summary, approver, timeoutSeconds, node_id, graph_id}]}`，进程内单例，重启即失；Phase 2 第五项） | human_approval |
| POST | /api/approvals/{token}/decision | 人工审批决策，请求体 `{decision: "approved"|"rejected", comment?}`（comment v1 仅接收不展示）；首决生效，200 返回决策结果；未知 token 404、已决重复提交 409；Phase 2 第五项 | human_approval |
| POST | /api/feedback | 提交种子试用反馈（type=bug/suggestion、content、contact 选填，201；进程内存储，reset 不清除；Phase 1） | feedback_item |
| GET | /api/feedback | 导出全部反馈（陪同试用收集用，`{items: [...]}`，Phase 1） | feedback_item |
| GET | /demo/shop | 模拟商家售后控制台 HTML 页面（W9-W10，自动登录/抓取演示目标系统） | — |
| GET | / 及静态资源 | 生产形态（Docker）FastAPI 同源托管 `frontend/dist` 构建产物（`ATLAS_FRONTEND_DIST` 指向目录时挂载，html=True；dev 仍用 Vite 5174 代理） | — |
| GET | /api/memories/{operator_id} | 记忆配置读取（05 2.4 配置界面） | memory_config |
| PUT | /api/memories/{operator_id} | 记忆配置保存 | memory_config |

## 6. 协同消息协议（依据 05 3.3 collaboration_message）

```yaml
collaboration_message:
  message_id, from_agent_id, to_agent_id, type
  payload, context: {task_id, business_object}
  urgent, timestamp, ttl, idempotency_key
```

## 7. 评估接口（依据 06 9.2 evaluation_task）

```yaml
evaluation_task:
  task_id, description
  test_cases: [{input: {...}, expected: {action, verify}}]
  metrics: [task_success_rate, average_steps, decision_accuracy]
```

## 8. 接口对齐检查表（AI 落码时逐项确认）

- [x] LoopState 字段与 06 6.1 一致（含 memory_id/status）——✅ 2026-09-13 核对通过，实现于 `src/atlas/engine/state.py`（七字段一致；messages/observations 加 add 归约器以支持追加语义）
- [ ] decide_node 置信度阈值 0.6（06 6.2）
- [x] Web 点击三层定位顺序（06 6.5），层2 置信度 >0.7（W3-W4 落码于 `web/location.py`）
- [x] ActionResult.Status 枚举：SUCCESS/PARTIAL/FAILED（W3-W4 落码于 `harness/base.py`）
- [ ] 节点失败处理枚举：stop/continue/jump_to（03 node_schema）
- [x] 工具权限枚举：read/write/delete/financial（03 adapter_schema；W3-W4 落码于 `harness/base.py`）
- [x] 适配器类型枚举：web/api/mobile/desktop/database/iot/message（W3-W4 已用于 `adapter_type` 字段，web 类型已实现）
- [ ] 记忆检索分层与 memory_config 阈值（05 2.3）
- [ ] 评估指标三元组（06 9.2 metrics）

---

*本文为新增接口汇总文档；所有接口签名均有原文依据（06 代码示例 / 04 组件设计 / 05 Schema），REST 端点路径为按 Demo 需求推导、标注"推导"，落码时以 OpenAPI 正式化为准。*
