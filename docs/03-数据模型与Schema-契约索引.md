# Atlas 数据模型与 Schema 契约索引

> **用途**：全部数据结构/接口契约的**位置速查表**——字段定义正文在对应文档中原样保留，本文件只做索引与字段概览，不复制正文。
> **AI 使用提示**：实现或修改数据结构时，先查本表定位 → 打开对应文档看完整字段定义 → 改动后回填本表。

---

## 契约总览

| Schema | 位置 | 上下文章节 |
|---|---|---|
| `node` | 04 / 三、节点组件详细设计 3.2 节点的通用化接口设计 | ### 3.2 节点的通用化接口设计 |
| `tool` | 04 / 四、工具/适配器组件 4.3 工具定义 Schema | ### 4.3 工具定义 Schema |
| `node_schema` | 04 / 5.2 节点系统 Schema 示例代码 | ### 5.2 节点系统 Schema 示例代码 |
| `node_data_schema` | 04 / §4.9 前端 MetaSchema 扩展（`x-*` keyword 权威）+ `frontend/src/lib/schemas/`（M1 新增） | ### 4.9 Capability JSON Schema 子集与发现投影（v1） |
| `diagnostic` | 04 / §6.5 结构化诊断 blockquote（M2 2026-09-16 立项并同日落码，234a95f→fbd9f77）+ `frontend/src/lib/validation/`（落码承载） | ### 6.5 拓扑作用域与 L2 模板引用校验（v1） |
| `graph_definition` | 04 / 5.2 节点系统 Schema（节点形状）+ `src/atlas/graph/dsl.py`（GraphDSL 权威实现，W7-W8） | ### 5.2 节点系统 Schema 示例代码 |
| `form_renderer` | 04 / §4.10 Schema 驱动表单渲染（M3 2026-09-16 立项、**2026-09-17 落码收口**）+ `frontend/src/lib/forms/`（落码承载） | ### 4.10 Schema 驱动表单渲染（M3 立项 2026-09-16，落码 2026-09-17） |
| `ui_schema` | 04 / §4.10 末 M4 UISchema 最小子集扩展条（**M4 批 1/批 2 2026-09-17 已落码**：groups/hiddenWhen/hideFields/文案层〔批 2 起嵌套通配键+rows/keyPlaceholders〕，控件 8→9 + target-select/saved-graph-select 两个节点业务控件）+ `frontend/src/lib/forms/{uiSchema,nodeWidgets,nodeRegistry,nodeUiSchemas,NodeConfigForm}` | ### 4.10 Schema 驱动表单渲染（M4 UISchema 扩展） |
| `graph_diagnostics` | 04 / §6.5 末 M4 前端 L3 预判 + Problems 面板扩展条（**M4 批 2/批 3 2026-09-17 已落码**：l3.ts 同构对拍、分层调度+记忆化、ProblemsPanel 点击定位；批 3 reverseDeps 反向索引、quickFix v1 删除悬空引用、200/500 节点 BENCHMARK）+ `frontend/src/lib/validation/{l3,reverseDeps}.ts` + `components/canvas/ProblemsPanel.tsx`；后端图级规则权威仍为 `src/atlas/graph/dsl.py` | ### 6.5 拓扑作用域与 L2 模板引用校验（M4 前端 L3/Problems 扩展） |
| `adapter_schema` | 04 / 5.4 工具/适配器注册 Schema 示例代码 | ### 5.4 工具/适配器注册 Schema 示例代码 |
| `skill_schema` | 05 / 一、技能（Skill）1.2 技能的数据结构 | ## 1.2 技能的数据结构（示例） |
| `memory_config` | 05 / 二、记忆（Memory）2.3 记忆策略配置 Schema | ## 2.3 记忆策略配置 Schema（示例） |
| `collaboration_message` | 05 / 三、智能体协同 3.3 协同通信协议 | ## 3.3 协同通信协议（示例） |
| `deployment_config` | 05 / 四、部署方式 4.3 部署配置 Schema | ## 4.3 部署配置 Schema（示例） |
| `interaction_template` | 05 / 五、自定义前端模板 5.3 交互模板 Schema（**愿景大 Schema**；M8 工程化最小子集＝下行 `card_template`） | ## 5.3 交互模板 Schema（示例） |
| `card_template` | **M8 已落码收口 2026-09-18（2b35fc9 起，08 M8 落码条）**；`src/atlas/cards/{catalog,render}.py`（审批卡片双向 Schema + web/im/email 三渠道渲染）+ 04 §5.6 追加段（human_approval.cardTemplateId）；权威＝08 M8 立项/落码条 | —（工程契约） |
| `evaluation_task` | 06 / 9.2 评估 Harness 设计 | ### 9.2 评估 Harness 设计（借鉴 lm-evaluation-harness）代码示例 |
| `refund_decision` | `src/atlas/llm/decision.py`（W9-W10 权威实现；规则对齐 06 §9.2 黄金用例） | —（工程推导契约） |
| `refund_order` | `src/atlas/shop/service.py`（W9-W10 Demo 电商数据结构） | —（工程推导契约） |
| `run_event` | `src/atlas/graph/loader.py`（emit 产出）+ `src/atlas/api/main.py`（SSE 帧，W9-W10）；M10 起三帧为 19 §2.3.4 超集（加 traceId/spanId/parentSpanId、run_end 加 graphVersion） | —（工程推导契约） |
| `nl_generate_request` | `src/atlas/api/main.py` NLGenerateRequest（W9-W10；响应为 `{graph: graph_definition}`） | —（工程推导契约） |
| `feedback_item` | `src/atlas/api/main.py` FeedbackRequest（Phase 1；进程内反馈，响应含 id/created_at） | —（工程推导契约） |
| `http_request_params` | 04 / 四、工具/适配器组件 4.6 API 适配器（通用 HTTP）v1 契约（权威 blockquote）+ `src/atlas/httpapi/{service,adapter}.py` | ### 4.6 API 适配器（通用 HTTP）v1 契约 |
| `db_sql_params` | 04 / 四、工具/适配器组件 4.7 数据适配器（通用 SQL）v1 契约（权威 blockquote）+ `src/atlas/database/{service,adapter}.py`（query/execute 两能力） | ### 4.7 数据适配器（通用 SQL）v1 契约 |
| `message_send_params` | 04 / 四、工具/适配器组件 4.8 消息适配器（进程内消息服务）v1 契约（权威 blockquote）+ `src/atlas/message/{service,adapter}.py`（单能力 message/send） | ### 4.8 消息适配器（进程内消息服务）v1 契约 |
| `template_catalog` | 04 / 五、逻辑组件 5.10 流程模板库（内置只读）v1 契约（权威 blockquote）+ `src/atlas/template/catalog.py`（5 个内置模板元数据与 graph） | ### 5.10 流程模板库（内置只读） |
| `recording_case` | 04 / 五、逻辑组件 5.11 操作录制与回放 v1 契约（权威 blockquote）+ `src/atlas/recording/{cases,replay,gate,snapshots}.py`（录制用例模型与进程内存储；M9 增 gate 发布前批量回放门禁；D26-b 增 `subgraphs` 快照内联，仅 replay 内联、gate 保持实时，见下行 `release_gate`） | ### 5.11 操作录制与回放 |
| `debug_session` | 04 / 五、逻辑组件 5.12 单步调试与断点 v1 契约（权威 blockquote）+ `src/atlas/debug/{sessions,controller}.py`（运行期调试会话、暂停状态机、paused/stopped 帧） | ### 5.12 单步调试与断点 |
| `monitoring` | 04 / 五、逻辑组件 5.13 基础监控告警 v1 契约（权威 blockquote）+ `src/atlas/monitoring/{records,metrics,alerts,business}.py`（运行记录 ring、指标聚合、规则求值与告警状态机；M9 增业务结果指标与 rollout_gate 告警动作，见下行 `business_metrics`） | ### 5.13 基础监控告警 |
| `identity_session` | 04 / 五、逻辑组件 5.14 多租户与权限 v1 契约（权威 blockquote）+ `src/atlas/iam/{principals,sessions,registry,deps}.py`（种子租户/账号、Principal、sess- token、按租户服务注册表、Bearer 依赖） | ### 5.14 多租户与权限 |
| `trace_span` | 04 / 五、逻辑组件 5.15 链路追踪 v1 契约（**M10 已落码 2026-09-18**；权威 blockquote）+ `src/atlas/tracing/`（与 OTel 同形最小 Span/Tracer、contextvars 进程内传播、to_tree 折叠开关） | ### 5.15 链路追踪（span v1） |
| `rollout_config` | **M9 已落码 2026-09-18（批 1-4＋收口；后端 602/前端 396，提交链见 08 与 CHANGELOG）**；04 / 五、逻辑组件 5.16 灰度发布与门控回滚 v1 契约（权威 blockquote）+ `src/atlas/routing/{models,router,store,gate}.py`（rollout 配置/三段分桶/状态机/门控；ADR T22）；形状来源 docs/19 §2.3.3 提案转权威 | ### 5.16 灰度发布与门控回滚 |
| `route_decision` | **M9 已落码 2026-09-18（批 1-4＋收口；后端 602/前端 396，提交链见 08 与 CHANGELOG）**；同上 04 §5.16 + `routing/router.py` resolve_version 纯函数（入站 event→发布版本/分桶段，pin-to-version） | ### 5.16 灰度发布与门控回滚 |
| `release_gate` | **M9 已落码 2026-09-18（批 1-4＋收口；后端 602/前端 396，提交链见 08 与 CHANGELOG）**；04 §5.11 末发布前批量门禁段 + `src/atlas/recording/gate.py`（GateReport，D26 部分取回；D26 报告 v1 起响应纯超集加 `id` 并沉淀，见下行） | ### 5.11 操作录制与回放 |
| `release_report` | **D26 报告 v1 已落码收口（2026-09-18，U60 转正式）；2026-09-19 收尾批补 CSV/JSON 导出（`8a37b4e`，`.../export?format=csv|json`）**；04 §5.11 末用例集报告段 + `src/atlas/recording/reports.py`（ReleaseReport 沉淀/按图历史/通过率趋势/导出，ring 100/租户、reset 清空、不 PG 化） | ### 5.11 操作录制与回放 |
| `business_metrics` | **M9 已落码 2026-09-18（批 1-4＋收口；后端 602/前端 396，提交链见 08 与 CHANGELOG）**；04 §5.13 末业务指标段 + `src/atlas/monitoring/business.py`（extract_business 业务结果三率，金融灰度门控信号源） | ### 5.13 基础监控告警 |

---

> **持久化注记（M5 契约设计轮，2026-09-17；设计权威＝[docs/24](docs/24-M5持久化与中断恢复契约设计.md)，M5a/M5b 立项后按此落码）**：本索引三条进程内契约在 PG 档下的落库口径统一为——`graph_definition` 落 `graphs` 表（definition jsonb，M5b 起运行期按帧内嵌快照引用，M6 版本化前防止恢复错位）；`debug_session` 与审批挂起统一进 `interruptions` 中断帧表（`kind: approval|debug|wait`，含 `deadline_at` 绝对时刻与 `resume_state` 续跑载荷，见下 `interruption_frame`）；`identity_session` 落 `iam_sessions` 表（token/租户/角色/签发时刻，进程内 `SessionStore` 语义不变）。reset 分档：以上均 resettable，`recordings`/`feedback` 为 persistent（`/api/demo/reset` PG 档 truncate 运行时表、保留两表）。

### `node` — 字段概览（完整定义见 04-组件设计-编辑后台.md #54，上下文章节：### 3.2 节点的通用化接口设计）

```yaml
id: string
type: string
name: string
description: string
config: object          # 节点类型专属配置
inputs: # 输入映射
source: string      # 变量名/上下文路径
required: boolean
default: any
outputs: # 输出声明
type: string
retry: # 重试策略
max_retries: number
backoff: string
timeout: number
on_error: string        # 失败处理：stop/continue/跳转节点
```

### `tool` — 字段概览（完整定义见 04-组件设计-编辑后台.md #103，上下文章节：### 4.3 工具定义 Schema）

```yaml
id: string
name: string
description: string          # 自然语言描述，供LLM理解
adapter_id: string           # 所属适配器
action: string               # 具体动作
input_schema: object         # 输入参数 JSON Schema **子集**（形状权威见 04 §4.9，白名单 keyword；空 {} = 未声明）
output_schema: object        # 输出参数 JSON Schema 子集（描述 ActionResult.output，同上）
permission: string           # 所需权限
timeout: number
retry_policy: object
is_idempotent: boolean       # 是否幂等
```

### `node_schema` — 字段概览（完整定义见 04-组件设计-编辑后台.md #422，上下文章节：### 5.2 节点系统 Schema 示例代码）

```yaml
id: string                    # 唯一ID
type: enum[trigger, ai_decision, tool_call, condition, loop, parallel, wait, subgraph, human_approval]
name: string
description: string
position: {x, y}
config:
type: object               # 节点类型专属配置；condition 节点 config 形状：
                           #   {branches:[{label,expression,target}], defaultTarget}
                           #   唯一权威见 04 §5.2「condition 节点 config 契约」，表达式白名单见 04 §5.1
                           # loop 节点 config 形状：
                           #   {mode:"while", continueExpression, maxIterations, bodyTarget, exitTarget}
                           #   唯一权威见 04 §5.3「loop 节点 config 契约」
                           # parallel 节点 config 形状：
                           #   {joinStrategy: all_success|all_completed|any_success, branches:[{label,target}], joinTarget}  # any_success(D18) 语义见 04 §5.4
                           #   唯一权威见 04 §5.4「parallel 节点 config 契约」
                           # wait 节点 config 形状：
                           #   {waitType:"duration", durationSeconds: 1-600 整数}
                           #   唯一权威见 04 §5.5「wait 节点 config 契约」
                           # human_approval 节点 config 形状：
                           #   {summary, approver?, timeoutSeconds: 10-3600 整数, onTimeout: approve|reject(默认reject),
                           #    approvedTarget, rejectedTarget, cardTemplateId?（M8 新增，可选内置卡片 id，不填走 summary 旧路径）}
                           #   唯一权威见 04 §5.6「human_approval 节点 config 契约」（含 M8 cardTemplateId 追加段）
                           # subgraph 节点 config 形状：
                           #   {graphId, inputs?: {<子图入参键>: "<父图 {{路径}}/字面量>"}}
                           #   唯一权威见 04 §5.7「subgraph 节点 config 契约」
inputs: 
source: string           # 变量路径
required: boolean
default: any
outputs: 
type: string
retry: 
max_retries: number
backoff: string
timeout: number              # 秒
on_error: enum[stop, continue, jump_to]
breakpoint: boolean          # 是否断点
```

### `graph_definition` — 字段概览（W7-W8；节点形状权威见上 `node_schema`，容器结构以 `src/atlas/graph/dsl.py` GraphDSL 为准）

```yaml
version: 1                   # Graph JSON 容器（结构）版本，当前仅支持 1；M6 起与发布版本分维
releaseVersion: 1            # 业务发布版本号（M6 新增，int，仅发布产物带；草稿 latest 缺省）；
                             #   发布动作冻结 Graph JSON 为不可变版本，graphId@vN 读取（ADR T19）
variables:                   # 全局变量（GraphVariable: name/type/value/scope=global）
nodes:                       # node_schema 节点列表；当前可编译类型：
                             #   trigger / ai_decision / tool_call / condition / loop / parallel / wait / subgraph / human_approval（Phase 2 起），
                             #   其余类型校验拒绝；condition 见 04 §5.2，loop 见 04 §5.3，parallel 见 04 §5.4，wait 见 04 §5.5，human_approval 见 04 §5.6，subgraph 见 04 §5.7
edges:                       # {id, source, target}，端点必须存在且禁止自环；
                             #   仅 loop 循环体回到 loop 节点的回边允许成环（白名单见 04 §5.3）；
                             #   parallel 扇出/汇聚为无环菱形（分支区域规则见 04 §5.4）；
                             #   wait 恰好一条出边且不直连 END（见 04 §5.5）；
                             #   subgraph 恰好一条普通出边且不直连 END，跨图引用经 graph_resolver 编译期解析（禁自引用/环/深度>3，见 04 §5.7）；
                             #   human_approval 恰好两条出边分别对配 approvedTarget/rejectedTarget，均不直连 END（见 04 §5.6）
```
> 前端序列化 `frontend/src/lib/graphSerializer.ts`（version 1）；后端解析/校验 `atlas.graph.dsl.parse_graph`，错误一次性聚合；编译执行 `atlas.graph.loader.compile_graph/run_graph`。
>
> **租户注记（2026-09-16，§5.14）**：已保存图按租户分区（每租户独立 GraphStore，graph-N 计数各自从 1 起）；tenant 由 token 推断，不进 Graph JSON。跨租户访问图 id → 404。
>
> **模板引用与拓扑作用域注记（2026-09-16，§6.5）**：config 内 `{{路径}}` 的节点输出可见性按图拓扑推导（visibleAt = 沿入边反向可达上游 + 全局变量 + loop 体区域），各节点类型输出投影、L2 三错误码（REF_NODE_NOT_FOUND / REF_NOT_IN_SCOPE / REF_PATH_NOT_FOUND）与 token 区间权威见 04 §6.5；前端实现 `frontend/src/lib/scope.ts`，后端编译期复查在 `atlas.graph.dsl`，运行期插值缺失保留原样语义不变。工具 `result.*` 深层路径以 04 §4.9 的 output_schema 子集为来源。 **2026-09-19 A+B 批补三条可见性/联动规则（权威见 04 §5.1/§5.3/§5.4/§6.5，U66–U71）**：表达式扩算术 + 白名单函数 + 确定性日期（D15，禁 eval、不含 now()/today()）；loop 支持 break（体内 condition→exitTarget，exitReason 增 `break`，合成 `__break__` 网关）/continue（回边重入）（D17）；parallel 增 any_success OR-join（未开始分支短路 `status:"skipped"`、已发起调用不回滚、全失败才 failed，合成 `__join__` 网关幂等 + done→END）（D18）；`parallel.result.<入口id>` 仅汇聚点后可见（区域内 REF_NOT_IN_SCOPE）、`subgraph.outputs.<子图内部节点id>` 按已解析子图深层校验（后端编译期权威、前端未解析降级，随 D21 接编辑器拉取）、节点 id 可在属性面板编辑并由 renameNode 全量联动模板头/target/边（D30 B1/B2/B3）。
>
> **版本化注记（2026-09-17，M6 立项 / ADR T19）**：`version`（容器结构版本，恒 1）与 `releaseVersion`（业务发布版本号，仅发布产物带）两维分离；发布＝冻结不可变版本（`src/atlas/versioning/`）+ subgraph 钉版（子图 `graphId@vN` 递归钉版本号，版本不可变故钉号即冻结引用内容）；草稿（latest）可变、`graph-N` 保存零回归。权威见 08 M6 立项条、12 §5 发布/版本端点。 M9 增 `PUT /api/graphs/{id}`（operate）：同一 graph id 迭代时覆盖 latest 草稿（首次 POST 建图之后复用该 id），不动不可变发布版、不新建 id；body 同 SerializedGraph（过 parse_graph 校验），图不存在/跨租户 → 404，返 `{id, version}`。前端编辑器编译运行/录制/发布统一经此端点（收口期修复了早期每次运行都 POST 新 graph-N、致录制用例 graph_id 与发布门禁筛选错位、门禁恒「暂无匹配用例」的缺陷）。

### `node_data_schema` — 字段概览（M1 已落码 2026-09-16，十提交 4ade416→51cb57f；权威见 04 §4.9「前端 MetaSchema 扩展」，前端承载 `frontend/src/lib/schemas/`）

```yaml
# 九类内置节点各一份：nodes/{trigger,ai_decision,tool_call,condition,loop,parallel,wait,human_approval,subgraph}.schema.ts
# 字段规则：04 §4.9 同源 20 个 JSON Schema 白名单 keyword（type/properties/required/items/enum/minimum/maximum/...）
<field>:
  x-variable: boolean            # 该字段接受 {{路径}} 引用（L2 校验规则仍以 04 §6.5 为准）
  x-widget: string               # 自定义控件名；M1 仅声明，M3 WidgetRegistry 消费
  x-ref: {kinds?: [string]}      # 值须为图内节点 id 且种类匹配（缺省/"*" 任意）；用于 target 类字段
  x-outputSchema: object         # 节点 outputs 形状声明；M1 须与 lib/scope.ts 投影逐字段一致
# metaSchema.ts：TS 类型 + schema 形状自检；index.ts：最小 SchemaRegistry get(kind, version)（M1 仅内置节点单一来源）
```
> 边界：`x-*` 仅前端节点 schema，后端 Capability schema 拒绝 `x-*`（U32）；M1 零 UI 变化、零新依赖、不引 AJV（ADR T16 已于 2026-09-16 收口为手写最小子集，M2 已立项把 M1 解释器扶正迁入 `lib/validation/l1.ts`——见下 `diagnostic` 契约，M3 重开判据见 10 §4）、不做表单生成；schema 为 nodeCatalog 手写规则的声明式投影，M1 期手写校验保留并双跑等价比对（U36；M2 扶正后双跑脚手架与已覆盖手写规则下线，covered:false 跨字段规则保留）。工具/技能等五类实体入册缓做 D29。

### `diagnostic` — 字段概览（M2 已于 2026-09-16 立项并同日按五段原子序落码收口，234a95f→fbd9f77；权威见 08 M2 立项条+落码条与 04 §6.5 结构化诊断 blockquote，落码承载 `frontend/src/lib/validation/`）

```ts
// diagnostics.ts
type Diagnostic = {
  severity: 'error' | 'warning'
  layer: 'field' | 'template' | 'graph'   // field=L1 字段/schema+手写跨字段；template=L2 模板引用；graph=类型位（M2 前端不产，L3 权威在后端）
  code: string        // L1: FIELD_REQUIRED/FIELD_TYPE/FIELD_ENUM/FIELD_CONST/FIELD_RANGE/FIELD_LENGTH/FIELD_PATTERN/FIELD_ITEMS_MIN/FIELD_ITEMS_MAX/FIELD_ADDITIONAL_PROPERTIES/FIELD_ONEOF + 手写跨字段规则稳定码；L2: REF_NODE_NOT_FOUND/REF_NOT_IN_SCOPE/REF_PATH_NOT_FOUND
  message: string     // 中文，与 M1/M0 现文案逐条一致；未来 i18n key 同源
  loc: {
    nodeId?: string                          // 所属图节点 id；图级诊断可缺省
    pointer?: string                        // RFC 6901 JSON Pointer，相对该节点 config 对象根：/durationSeconds、/branches/0/expression、/inputs/<键>
    token?: { start: number; end: number; raw: string }  // 仅 L2：{{...}} 在模板字段源串中的区间与原文（含 {{}}）
  }
  quickFix?: Fix[]    // 类型位 only：M2 不产任何动作（首个动作随 M4）
}
// rank(diags)：error 优先；同严重度按节点拓扑稳定序（上游在前）→ pointer → token.start
// validateGraph 聚合单节点 L1（l1.ts schema 解释器 + 手写跨字段）与 L2（scope.ts）；PropertyPanel/AtlasNode 角标共用，消费 Diagnostic[].message
```
> 后端 compile 422 形状微调（见 12 `/api/graphs/{id}/compile`、06 §6.13）：`detail: string[]`（中文文案/顺序/状态码不变）之外增稀疏侧车 `locations?: Array<{ index: number; nodeId?: string; pointer?: string }>`，index 对齐 detail 下标；图级错误（version/空图/连线/重复 id/全局变量）不出条目。不引入 Python 版 schema 解释器；运行期插值 fail-soft 不变。U37（前端）/U38（后端侧车）为候选用例。


### `form_renderer` — 字段概览（M3 2026-09-16 立项、**2026-09-17 落码收口（2de5053→6d4a863）**；权威见 08 M3 立项条+落码条与 04 §4.10，落码承载 `frontend/src/lib/forms/`；ADR T17 见 10 §4）

```ts
// lib/forms/WidgetRegistry.ts
type WidgetProps = {           // 19 §1.3.4 的 M3 子集；不含 uiSchema（UISchema 随 M4）
  value: unknown
  onChange(next: unknown): void
  schema: JsonSchemaFragment   // 当前字段 schema 片段（MetaSchema/Capability 白名单子集）
  scope: { visibleAt(nodeId: string, kind?: string): VarEntry[] }  // 复用 M0 scope 索引
  diagnostics: Diagnostic[]    // M2 Diagnostic[]，pointer 命中本字段时传入
}
// WidgetRegistry：name -> Component；内置 text/number/select/textarea/switch/json/expression/variable-input
// registerWidget(name, comp)：扩展点导出，M3 不注册业务自定义控件（D29）
// resolveWidget(schema)：x-widget（仅节点 schema）→ 类型结构默认（enum/const、boolean、integer/number、
//   object.properties 递归分组、array.items 增删行、additionalProperties-only 键值行、string）
//   → 降级 json：oneOf / 无 type 无 properties（含空 {}）/ 当前值非 JSON 对象（含裸 {{}} 整串）
// lib/forms/FormRenderer.tsx：schema+值 → 控件树；不可变更新 onChange 产出下一整个 params 对象
// 写回：JSON.stringify(next) → config.params（Graph v1 不变，params 永远是 JSON 字符串，后端零改动）
// SchemaRegistry 第二来源：/api/adapters tools[].input_schema，键 `<adapter>/<tool>`，持发现快照引用
//   未注册工具/发现失败/空 schema → 旧 JSON TextArea
// M3 只迁 ToolCallConfig；其余 7 个手写 Config M4 起逐个原子迁移
```
> 验收候选用例 **U39**（13 文档，**已随 M3 落码转正式**；registry/resolveWidget/降级、不可变写回与字符串回写、第二来源、variable-input+诊断+NL paramWarnings、九工具表单生成与 sql-query-notify 金链、浏览器双路径）。oneOf 等白名单外结构以 JSON 文本降级承接，T16 不重开；重开判据与 M4/M8 边界见 10 §4 T17、04 §4.10。**落码注记（2026-09-17）**：第二来源落为 `lib/forms/toolSchemas.ts`（`/api/adapters` 发现快照按 `<adapter>/<tool>` 入表、持引用不复制、空 schema 不可表单化）；`config.params` 的文本↔对象转换与 `JSON.stringify` 回写在 `lib/forms/params.ts`；结构树与不可变更新（`setAtPath`/`removeAtPath`/`appendAtPath`/`renameKeyAtPath`）在 `lib/forms/formTree.ts`；字段诊断复用 `lib/validation/l1.validateParamFields`（pointer 相对 params 根），NL paramWarnings 由 `lib/forms/nlWarnings.ts` 按节点归为非阻塞 warning。

### `ui_schema` — 字段概览（**M4 批 1/批 2 已落码 2026-09-17**；权威以代码 `frontend/src/lib/forms/uiSchema.ts` 为准，立项契约见 08 M4 立项条与 04 §4.10 末扩展条；ADR T17 复查不重开见 10 §4）

```ts
// lib/forms/uiSchema.ts（M4 批 2 落码形态；纯逻辑，零 React）
// 最小子集，不引入完整 JSON Schema UI 规范：
type UiSchema = {
  groups?: Array<{ key: string; label?: string; fields: string[]; layout?: 'row'|'column'; visual?: boolean }>
  // ui:group：把同层字段归入分组容器；layout:'row' 承接 HumanApproval approved/rejected 并排
  hiddenWhen?: Array<{ field: string; equals: unknown; show: string[] }>
  // 值驱动条件显隐：判别字段 field 取 equals 时才显示 show 中字段；trigger triggerType 显隐 cron/webhookUrl
  hideFields?: string[]
  // 静态隐藏（值保留、不参与渲染）：Loop 内部字段 mode 用；区别于值驱动的 hiddenWhen
  // 批 2 起文案键支持嵌套路径通配：根字段 'joinTarget'、数组行 'branches[].label'（[] 匹配任一下标）、
  // 键值行 'inputs.*'（* 匹配任意单段）；点号或斜杠分隔均可（uiKeySegments/pointerMatches）
  labels?: Record<string, string>        // 字段中文 label（空串显式盖掉字段名直出，parallel/subgraph 用）
  placeholders?: Record<string, string>  // 字段 placeholder（键规则同 labels）
  optionLabels?: Record<string, Record<string, string>> // enum/const 枚举值中文案
  rows?: Record<string, number>          // 批 2：多行控件行数（subgraph inputs.* 值压单行）
  keyPlaceholders?: Record<string, string> // 批 2：keyvalue 键输入框占位（subgraph inputs 用「入参键」）
}
// groups/hiddenWhen 只作用根 object；labels 等文案由 FormRenderer 渲染期逐节点装饰
// （decorateNodeForRender，含数组行与 keyvalue 懒建值节点）；不做 ui:order、不做 if/then
```

**内置控件九件**（`lib/forms/types.ts` BUILTIN_WIDGETS + `defaultRegistry.ts`）：text / number / select / **radio**（M4 新增，承接 onTimeout/joinStrategy 这类 Radio 形态）/ textarea / switch / json / expression / variable-input。M4 另增**节点业务控件层**（`nodeWidgets.tsx` + `nodeRegistry.ts` 的 buildNodeRegistry，与内置控件分层、不进默认注册表）：`target-select`（连线目标选择，scope `listNodeTargets()`）与批 2 新增 `saved-graph-select`（subgraph graphId，挂载拉一次 `/api/graphs`，空态 Empty/错误态红字/option 文案 `id（n 节点）`）；节点表单共享包装 `NodeConfigForm.tsx`（schemaRegistry + NODE_UI_SCHEMAS + node registry，只透传带 pointer 的诊断）。`resolveWidget` 选择序：节点 x-widget → **有非空 properties 的 object 展开为 group（已提到 oneOf 降级之前，fix fba124a）** → 无统一 properties 的 oneOf 联合降级 json → enum/const select → 类型分支。NumberWidget 对 integer 设 precision=0/step=1。批 2 ArrayView 消费 schema `minItems/maxItems`：删除在 ≤minItems 禁用、添加在 ≥maxItems 禁用并显 `添加（n/max）`、行卡片边框 + formTree 行号 `#n`。

**迁移状态**：HumanApproval / Trigger / Loop（批 1）、**Parallel / Subgraph（批 2，06b62fc）** 已切 NodeConfigForm（旧内联组件已删/瘦包装化）；**Wait 经落码判定保留手写**（waitType 为 const、旧 UI 含一个禁用 event 单选项 + Tooltip 预告 D19，schema const/radio 无法表达"禁用选项+Tooltip"，强迁要么丢预告要么逼 radio 加 disabledOptions 过度设计；符合"等价才迁、不设硬指标"）；Condition 批 3（动态 branches 最复杂置末）。

> 验收候选用例 **U40**（13 文档）：ui:group 分组容器与并排布局、hiddenWhen 三分支显隐、未命中条件时字段不渲染且不参与校验、hideFields 静态隐藏值保留、字段/枚举中文案、嵌套通配文案（批 2 补）。复杂度 dump 证据与落码细化见 08 M4 立项/批 1/批 2 落码条。

### `graph_diagnostics` — 字段概览（**M4 批 2 已落码 2026-09-17**（0dc9b7f/1468e18/2ad4177）；权威见 08 M4 批 2 落码条与 04 §6.5 末扩展条，落码承载 `frontend/src/lib/validation/{l3,engine,useValidationEngine}.ts` + `store/validationStore.ts` + `components/canvas/ProblemsPanel.tsx`；后端图级规则唯一权威仍为 `src/atlas/graph/dsl.py`）

```ts
// lib/validation/l3.ts（已落码，规则从 dsl.py 同构提取，跨运行时对拍夹具守漂移）：
//   GRAPH_UNREACHABLE：从 trigger BFS 不可达节点（同构 _validate_reachability）
//   GRAPH_ILLEGAL_CYCLE：移除 loop 白名单回边后 DFS 三色检测成环（同构 _validate_illegal_cycles）
//   GRAPH_DATA_CYCLE（D30-b，2026-09-19 已落码 9a357df）：数据依赖环——边为「通过可见性判定的
//       模板引用」viewer→provider（scope.dataDependencyEdges），DFS 三色；新增价值在 loop 回边区
//       （loop 条件引用体内节点、体内又引用 loop.index）；后端 _validate_data_dependency_cycles 权威、error 进编译 422
// L2 另有 REF_TYPE_MISMATCH（D30-a，47ecf48，warning）：tool_call params 单模板叶子标量类型与
//       工具 input/outputSchema 不符（number 接受 integer、integer 不接受 number、object/array/拼接/缺 schema 放行）
// 聚合顺序环→不可达→数据环，与 validate_graph_report 一致；message 与后端字节对齐
// 对拍：scripts/dev/generate_l3_fixtures.py 以后端实跑生成 11 用例 JSON，
//       tests/test_l3_fixtures.py（后端重建重跑）+ l3.test.ts（前端对拍）双侧守
// 唯一有意偏差：GRAPH_UNREACHABLE 的 loc.nodeId 取不可达节点自身（后端 locations 侧车无图级条目）
// 调度（useValidationEngine）：L1 useLayoutEffect 同步 / L2 300ms 防抖 / L3 requestIdleCallback（+500ms setTimeout 回退）
// ScopeIndex 按 structureSignature 记忆化（编辑模板不重建索引）；L3 自 D30-b 起按 graphDataSignature
//   （结构 + 各节点模板字段投影）记忆化——数据环依赖模板文本，编辑模板须使 L3 重算；dirty.ts 维护增量失效与 revision 守卫
// Problems 面板（ProblemsPanel.tsx）：validationStore 全图 Diagnostic 经 rank 聚合，
//   点击 nodeId 条目 selectNode+setCenter，pointer 条目滚到 [data-pointer] 字段并闪烁；无 nodeId 环条目不可点
// quickFix v1 与 reverseDeps：批 3 已落（reverseDeps.ts 双类反向索引 + removeDanglingRef；
//   REF_NODE_NOT_FOUND 挂 DELETE_DANGLING_REF_FIX，ProblemsPanel 内联按钮；改名联动随 D30）
```
> 验收候选用例 **U41**（13 文档，候选）：构造含环/不可达图，前端 L3 诊断与后端 dsl.py 逐条同构对拍（**批 2 已落 11 夹具对拍**）；Problems 聚合/rank/点击定位（**批 2 已落并浏览器冒烟**）；分层调度/增量脏标记（**批 2 已落**）；quickFix 删除悬空引用、reverseDeps、200/500 节点基准 BENCHMARK.md（**批 3 已落并浏览器冒烟**，脚本 scripts/dev/m4_batch3_smoke.py）。

### `adapter_schema` — 字段概览（完整定义见 04-组件设计-编辑后台.md #459，上下文章节：### 5.4 工具/适配器注册 Schema 示例代码）

```yaml
id: string
name: string
type: enum[web, api, mobile, desktop, database, iot, message]
connection: 
auth_type: enum[oauth2, api_key, basic, cookie, none]
config: object
capabilities: 
description: string       # 自然语言描述，供LLM理解
input_schema: object      # JSON Schema 子集（白名单/形状权威见 04 §4.9）
output_schema: object     # 同上；GET /api/adapters 投影携带两者，未声明为 {}
permission: enum[read, write, delete, financial]
timeout: number
is_idempotent: boolean
```

### `skill_schema` — 字段概览（完整定义见 05-组件设计-运营体五项核心.md #20，上下文章节：## 1.2 技能的数据结构（示例））

```yaml
id: string                      # 唯一ID
name: string                    # 技能名称
description: string              # 自然语言描述（供LLM理解和推荐）
version: string                  # 语义版本号
category: enum[communication, data_processing, business_logic, integration, repo
```

### `memory_config` — 字段概览（完整定义见 05-组件设计-运营体五项核心.md #172，上下文章节：## 2.3 记忆策略配置 Schema（示例））

```yaml
```
> **M11 边界（2026-09-19 立项，docs-only）**：05 文档的五层记忆策略 Schema（memory_config）为**愿景**，v1 不实现策略配置表单（缓做 D35）。M11 仅取回最小可用长期记忆：统一 `memory_item`（fact/preference）+ 本地确定性 embedding + `memory/remember`、`memory/recall` 两适配器工具，见文末 `memory_item` / `memory_remember` 契约（权威＝docs/26、ADR T23）。

### `collaboration_message` — 字段概览（完整定义见 05-组件设计-运营体五项核心.md #323，上下文章节：## 3.3 协同通信协议（示例））

```yaml
message_id: string          # 唯一消息ID
from_agent_id: string       # 发送方运营体ID
to_agent_id: string         # 接收方（可为广播）
type: enum[
payload: object             # 消息内容
context: # 上下文传递
task_id: string           # 关联任务ID
business_object: string   # 关联业务对象
urgent: boolean           # 紧急标志
timestamp: datetime
ttl: number                 # 超时时间（秒）
idempotency_key: string     # 幂等键
```

> **租户注记（2026-09-16，§5.14）**：v1 的进程内消息（`message/send` 与 `/api/messages`）按租户分区（每租户独立 MessageService）；vision 协议的 agent_id/广播字段为愿景，v1 未实现 agent 身份。

### `deployment_config` — 字段概览（完整定义见 05-组件设计-运营体五项核心.md #440，上下文章节：## 4.3 部署配置 Schema（示例））

```yaml
runtime: 
environment: enum[cloud, private, edge]
region: string                 # 运行地域
concurrency_limit: number      # 最大并发实例数
timeout_per_run: number        # 单次运行超时
```

### `interaction_template` — 字段概览（**愿景大 Schema**，完整定义见 05-组件设计-运营体五项核心.md #552，上下文章节：## 5.3 交互模板 Schema（示例））

> **M8 边界（2026-09-18 立项）**：下列 style/layout/conditional_display/channel_adaptations 等为**愿景形态**，M8 不实现；M8 只取回审批卡片的**双向最小子集**，工程契约见下行 `card_template`（权威＝08 M8 立项条 + 04 §5.6 追加段）。

```yaml
id: string
name: string
type: enum[approval, form, notification, guide, progress, choice, alert]
version: string
# 愿景另含 style（主题/字体/圆角/Logo/暗色）、layout.sections（header/body/footer/actions）、
# bindings（element_id/source/transform）、conditional_display、actions（payload/feedback）、
# channel_adaptations（wechat_work/web/mobile/email）——M8 不做，见 05 §5.3/§5.4。
```

### `card_template` — 字段概览（**M8 已落码收口 2026-09-18（2b35fc9/5f4384e/4b45571/25afa10，08 M8 落码条）**；权威实现 `src/atlas/cards/catalog.py`，形状来源 docs/19 §2.3.2 提案转权威；渲染见 `cards/render.py`，接线见 04 §5.6 追加段）

```yaml
# 内置只读目录（照 template 包：随代码发布、无 DB/CRUD、reset 不影响）；v1 一张 refund-approval
id: string                          # kebab-case 目录内唯一（refund-approval）
name: string                        # 展示名
channels: [enum[web, im, email]]    # 支持的渲染降级渠道
sections:
  - type: "fields"                  # 只读展示行
    bindings:
      - label: string
        value: string               # {{路径}} 模板，复用 graph.loader.interpolate；可见集＝该审批节点 visibleAt（L2 校验）
  - type: "textarea" | "input"      # 可编辑表单字段（FormRenderer 第三类 schema 来源 kind:"card"）
    name: string                    # 表单字段名，action.output 以 {{form.<name>}} 引用
    label: string?
    required: boolean?
    default: string?
actions:
  - id: string                      # approve / reject
    label: string
    style: enum[primary, danger, default]?
    output:
      decision: enum[approved, rejected]
      comment: string?              # 形如 {{form.comment}}，map_action_output 回填
    channels: object?               # 如 {email: {render: "link"}}
fallback:
  im:    { detailUrl: string }?     # IM 详情兜底链接（含 {token} 占位）
  email: { timeoutHint: boolean }?  # 邮件超时提示
# 渲染产物 render_card(card, context, *, token, channel, approver, timeout_seconds)：
#   web  ＝结构化投影（fields 插值只读行 + form 字段描述 + actions 按钮，前端 CardRenderer 渲染）
#   im   ＝{channel:"im", text, buttons:[{label,url}], detailUrl}（纯文本+两按钮回调 URL）
#   email＝{channel:"email", subject, html, links:[{id,label,url}]}（只读 HTML + 带 token 两链接；GET 不产生决策）
# action 回调：POST /api/approvals/{token}/decision，体 {decision,comment?} 或 {actionId,form?}（服务端 map_action_output 映射）
```
> 卡片是图的附属实体（第六类实体，复验 D29 多来源）：bindings 的 `{{}}` 走 M0/L2 同一 ScopeIndex，坏引用编辑期被同一诊断流拦截；actions 只回写审批 decision/comment，不回写任意节点。进程内三渠道渲染 + message/send 落记录为沙盘语义，**不解除 D33**（真实 IM/邮件渠道随 D24/D20）。REST：`GET /api/cards`、`GET /api/approvals/{token}/card?channel=`，见 12 §5。

### `evaluation_task` — 字段概览（完整定义见 06-运行时与质量保障.md #125，上下文章节：### 9.2 评估 Harness 设计（借鉴 lm-evaluation-harness）代码示例）

```yaml
task_id: "refund_processing"
description: "处理退款申请"
test_cases: 
order_id: "12345"
reason: "商品破损"
amount: 299
expected: 
action: "approve_refund"
verify: "refund_status == 'completed'"
order_id: "12346"
reason: "不想要了"
amount: 5000
expected: 
action: "request_human_approval"
verify: "approval_request_created"
metrics: 
```

### `refund_decision` — 字段概览（W9-W10；权威实现 `src/atlas/llm/decision.py`）

退款决策客户端 `DecisionClient.decide_refund(reason, amount, limit)` 的输出 dict，ai_decision 节点产出与 shop/process_refund 入参均以此为准：

```yaml
action: enum[approve_refund, request_human_approval]   # 自动退款 / 转人工审批
reason: string          # 中文决策说明（写入工具 note / 审批意见）
confidence: number      # 规则兜底恒为 1.0；LLM 取模型输出，解析失败 0.0
source: string          # "rule" 或 "llm:{model}"
```
> 规则（06 §9.2）：质量原因（破损/质量/错漏发等关键词）且金额 ≤ 限额 → approve_refund；其余及 LLM JSON 解析失败 → request_human_approval（fail-safe）。

### `refund_order` — 字段概览（W9-W10；权威实现 `src/atlas/shop/service.py`）

Demo 电商平台退款单（dataclass；进程内单例，持久化随 11 S1 业务表 DDL 重启）：

```yaml
order_id: string        # 种子 12345-12349
reason: string          # 退款原因
amount: number          # 金额（元）
status: enum[pending, refunded, human_review]
history: list[string]   # 操作记录（自动退款/转人工审批）
```

### `run_event` — 字段概览（W9-W10；`POST /api/graphs/{id}/run/stream` 的 SSE 帧）

`loader.run_graph(emit=...)` 产出事件 dict，API 以 `event: <type>\ndata: <json>` 帧推送；前端 `streamRun` 消费（见 12 文档 REST 表）：

```yaml
# 节点开始
type: "node_start"
node_id: string
node_type: enum[trigger, ai_decision, tool_call]
traceId: string          # M10：本次 run 的 trace id（32hex），整棵树一致
spanId: string           # M10：本节点 span id（16hex）
parentSpanId: string     # M10：父 span id（run root；subgraph 内为 subgraph span）
# 节点结束
type: "node_end"
node_id: string
node_type: string
output: object          # 该节点产出（决策 dict / 工具 ActionResult 输出）
traceId: string          # M10：同 node_start
spanId: string
parentSpanId: string
# 运行结束（SSE 末帧为 event: result，载荷 {id, status, outputs, traces}）
type: "run_end"         # 随 run_graph 返回值展开
traceId: string          # M10：run root span 的 trace id
spanId: string           # M10：run root span id（parentSpanId 缺省）
graphVersion: string     # M10：`graphId@<releaseVersion:int>`（发布版本，无 v 前缀，对齐 M6 钉版）/`graphId@draft`（草稿）
```
> M10 起三帧均为 **19 §2.3.4 Trace 事件超集**：只新增 traceId/spanId/parentSpanId（run_end 另加 graphVersion），现有字段与帧类型不变，前端忽略未知字段即零改动。span 三元组位于事件顶层、**不进节点 output**（录制回放 collect_steps 只取 output，天然不受随机 id/时间影响）；完整 span 树经 `tracing` 包进程内导出，不进 SSE 高频帧。subgraph 子图以 `emit=None` 重入、事件仍不外泄，子图内部 span 经 `to_tree(include_internal=False)` 折叠（见下 `trace_span`）。

### `trace_span` — 字段概览（**M10 已落码收口 2026-09-18（ba9e0d2 起，08 M10 落码条）**；权威＝docs/19 §2.3.4 + 10 §4 ADR T21 + 08 M10 立项条，落码承载 `src/atlas/tracing/`）

```yaml
# 与 OTel 同形的最小 span（纯 stdlib、零新依赖；不引 OTel SDK，合流随 D11）
Span:
  traceId: string        # 32hex（uuid4），一次 run（含 subgraph/任务信封）一致
  spanId: string         # 16hex（uuid4 前 16）
  parentSpanId: string | None   # root run span 为 None
  name: string           # 如 run:<graphId> / node:<id> / tool:<adapter>/<cap> / task:<type>
  kind: enum[run, node, tool, parallel, subgraph, task_dispatch, task_done, approval]
  startedAt: str         # UTC ISO
  durationMs: float
  status: enum[ok, error]
  graphVersion: string | None   # run root 标注 graphId@<int> / @draft
  attrs: dict            # actor（任务 assignee）、node_type、adapter、orderId 等
  internal: bool         # subgraph 内部 span 标 true，include_internal=False 时折叠
# 进程内传播：contextvars 记当前 span（同线程 节点→工具 就近取父）；
# Tracer 经 run_graph/compile_graph 参数显式透传（SSE worker 后台线程，守 06 §6.12）。
# to_tree(include_internal=True/False) -> 嵌套 dict；False 把子图内部折叠为单个 subgraph span。
```
> M7 `task_envelope.traceId/graphVersion` 落码时为占位（traceId=runId）；**M10 起填真实 traceId 并记录 parentSpanId**（dispatch 建 task_dispatch span、complete 建 task_done span，actor=assignee）。`RunRecord` 同期加可选 `trace_id`（见监控段；M9 再加 `resolved_version`/`business`，见下 `business_metrics`）。


> `node_type` 实际已随 Phase 2 扩展为全部可编译类型（含 subgraph）。subgraph 节点内部子图以 `emit=None` 重入执行，**不产生 node_start/node_end/run_end 事件**；子图 trace 与 outputs 收入 subgraph 节点产出（权威形状见 04 §5.7）。

### `subgraph_node_output` — 字段概览（Phase 2 第六项；subgraph 节点 outputs[id]）

```yaml
mode: "subgraph"
graphId: string               # 被引用的已保存图 id
status: "success" | "failed"  # 子图运行期异常 fail-safe 为 failed，父 run 仍 completed
error: string                 # 仅 failed
outputs: object               # 子图全部节点 outputs（key 为子图节点 id），父图经 subgraph-x.outputs.<子节点id>.<键> 引用
trace: [string]               # 子图 trace 行
```
> config 契约（graphId/inputs）与引用校验（自引用/环/深度上限 3/递归图校验）权威见 04 §5.7；运行时重入语义见 06 §6。

### `nl_generate_request` — 字段概览（W9-W10；`POST /api/nl/generate`）

```yaml
# 请求
prompt: string          # 自然语言流程描述
# 响应
graph: graph_definition # 见上 graph_definition（version 1，可直接保存/编译）
paramWarnings: [string] # 2026-09-16 起；tool_call 参数填充对 input_schema 的尽力校验中文警告（可空数组；非阻塞，草稿照常回显）
```
> 无法识别意图时返回 422；未配置 `LITELLM_MODEL` 时仅退款关键词走规则模板兜底。`paramWarnings` 只做静态可判项（必填缺失、顶层浅类型、enum、additionalProperties:false），未知工具/空 schema/模板插值值/无法解析的 params 一律放行（04 §4.9 ⑤）。

### `feedback_item` — 字段概览（Phase 1；`POST/GET /api/feedback`）

```yaml
# 请求 POST /api/feedback（FeedbackRequest）
type: "bug" | "suggestion"   # 反馈类型（非法值 422）
content: string              # 反馈内容，1-2000 字（空串 422）
contact: string              # 联系方式，选填，最长 200；默认 ""
# 响应（201；GET 返回 {items: feedback_item[]}）
id: string                   # feedback-{自增}
created_at: string           # UTC ISO-8601
```
> 进程内存储（重启清空，与 Demo 存储同假设）；`POST /api/demo/reset` 不清除反馈。
>
> **租户注记（2026-09-16，§5.14）**：反馈按租户分区（feedback-N 计数各租户从 1 起），reset 不清除但跨租户不可见；POST 对全部登录角色开放，GET 仅 admin（且只列本租户）。

### `template_catalog` — 字段概览（Phase 2 能力项，2026-09-15；`GET /api/templates`、`GET /api/templates/{id}`）

```yaml
# 模板元数据（权威实现 src/atlas/template/catalog.py 的 TemplateMeta）
id: string                 # kebab-case 目录内唯一，随代码稳定（refund-auto / http-orders-branch /
                           # sql-query-notify / sql-approval-write / approval-timeout-reject）
name: string               # 展示名
description: string        # 一句话场景
tags: [string]             # 展示标签
graph: graph_definition    # 完整 version 1 Graph JSON，节点 id 固定，加载不重映射
# GET /api/templates 列表投影（不含 graph）
items: [{id, name, description, tags, node_count}]
node_count: int            # graph.nodes 数量（服务端投影）
# GET /api/templates/{id} 返回完整 TemplateMeta（含 graph）；未知 id 404
```
> 只读内置目录：随代码版本发布，无 DB、无 CRUD、`/api/demo/reset` 不影响；「从模板新建」为客户端整画布替换，保存后为普通 graph-N 与模板无关。权威契约见 04 §5.10，REST 见 12 §5。

### `recording_case` — 字段概览（Phase 2 能力项，2026-09-15；`/api/recordings*`）

```yaml
# 请求 POST /api/recordings（RecordingCreateRequest）
name: string               # 用例名，1-100 字
graph_id: string           # 已保存图 id（服务端据此取图快照；未知 404）
inputs: object | null      # 录制时的运行入参（trigger payload）
steps: [{node_id, node_type, output}]  # 至少 1 步；node_id 重复时服务端保末
status: string             # 录制运行终态
# 响应 201 / GET 详情（RecordingCase）
id: string                 # rec-{自增}
graph_id: string           # M9 新增纯超集：所属图 id（创建请求已收，M9 起落库；旧用例为空串，发布门禁不入选）
graph: graph_definition    # 录制时的图快照（冻结，非 graph_id 活引用）
subgraphs: {graphId: raw}  # D26-b（2026-09-19，29bb3d9）纯超集：录制时递归冻结的子图 raw（深度≤3、visited 防环、引用缺失不阻断）；旧用例缺省 {}
created_at: string         # UTC ISO-8601
# GET /api/recordings 列表投影（不含 graph/steps）
items: [{id, name, graph_id, node_count, step_count, status, created_at}]
# POST /api/recordings/{id}/replay 响应（ReplayReport）
# D26-b：单用例 replay 走「内联优先」resolver（snapshots.inline_first_resolver）——先查 subgraphs 快照、未命中回退租户 GraphStore，
#   reset/删除/改动子图后旧用例仍可回放；发布门禁 gate.run_release_gate 刻意保持实时 resolver 不内联（验当前 latest 草稿，坏子图应 block）
matches: boolean           # 操作序列与逐节点归一化产出全部一致
baseline_status: string
replay_status: string      # 回放异常（如子图引用缺失）折叠为 "failed"
steps: [{node_id, match, note, diff_keys?}]  # diff_keys 为归一化后差异顶层键
```
> 进程内存储（重启清空，持久化随 11 S1）；`/api/demo/reset` 不清除（测试资产，同 feedback）。回放从 human_approval 步骤抽解决策预置为 inputs.approvals，不挂起；比对前递归剔除 token/sent_at、消息记录 uuid id、HTTP headers date。权威契约见 04 §5.11，REST 见 12 §5。
>
> **M9 发布门禁（2026-09-18 已落码，D26 部分取回）**：`graph_id` 用于发布前批量回放筛选；`POST /api/graphs/{id}/release-gate` 对当前 latest 草稿逐例重跑 + compare 产 `release_gate` 报告（见下），publish 可带 `gate:true` 拦截坏版本。用例集趋势报告/影子模式/Mock 外部系统仍缓做 14 D26。
>
> **租户注记（2026-09-16，§5.14）**：录制用例按租户分区（rec-N 计数各自从 1，图快照取自本租户 GraphStore）；跨租户访问录制 id → 404，reset 不清除。

### `debug_session` — 字段概览（Phase 2 能力项，2026-09-15；`/api/debug*` 与 /run/stream 的 debug 入参）

```yaml
# POST /api/graphs/{id}/run/stream 请求体可选 debug 字段
debug:
  breakpoints:
    - node_id: string          # 必须是本图节点（未知 422）
      expression: string?      # 可选，§5.1 白名单表达式；校验失败 422，运行时求值异常 fail-safe 不命中
# 启动即 step 模式（每个节点执行前暂停）；断点不进 Graph JSON、会话级。
# SSE event: paused
type: "paused"
token: string                  # dbg-<uuid>，resume 凭据
node_id: string                # 暂停在该节点 node_start 之后、逻辑之前
node_type: string
reason: "step" | "breakpoint" | "condition"
globals: object                # 当前全局变量快照（深拷贝，只读）
outputs: object                # 截至暂停点全部已完成节点终态产出（深拷贝，只读）
# POST /api/debug/{token}/resume 请求体
action: "step" | "continue" | "stop"   # step=下一节点再停；continue=关逐节点仅断点停；stop=取消运行
# SSE event: stopped（无 result 帧）
type: "stopped"
node_id: string
reason: "user_stop"
# GET /api/debug → {items:[{token, node_id, node_type, graph_id, reason}]}
```
> 进程内会话（threading.Event，重启即失，持久化中断随 11 S1/14 D19/D20）；未知 token 404、重复 resume 409；`/api/demo/reset` 按 stop 释放全部暂停；parallel 暂停串行化、`__join__` 网关与 subgraph 内部不暂停。权威契约见 04 §5.12，REST 见 12 §5。
>
> **租户注记（2026-09-16，§5.14）**：调试会话按租户分区（dbg- token 绑定所属租户 broker）；持他租户 token 调 resume/查询 → 404。审批会话（human_approval 的 approval token）同理，按租户 broker 分区。

### `monitoring` — 字段概览（Phase 2 能力项，2026-09-15；`/api/monitoring*`、`/api/alerts*`）

```yaml
# RunRecord（GET /api/monitoring/runs 列表元素；metrics 基于全部保留记录聚合）
id: string                 # run-{自增}
graph_id: string
mode: "sync" | "stream"    # 仅真实运行；debug/回放/子图重入不记录
status: "completed" | "error"   # 未捕获异常=error；节点 FAILED 是数据不是异常
started_at: string         # ISO 8601 UTC
finished_at: string
duration_ms: number
nodes:
  - node_id: string
    node_type: string
    status: "success" | "failed"   # output.status=="failed" 或 output.result.status=="FAILED"
    error: string?                 # 优先 error / result.message / result.code
error: string?
trace_id: string?          # M10：本次 run 的 trace id（32hex）
resolved_version: int?     # M9：入站 event 运行经 Router 解析钉住的发布版本号；编辑器手动运行为 null
business:                  # M9：业务结果提取（见下 business_metrics；无业务结果时各值为 null/false）
  auto_refunded: boolean
  manual_escalated: boolean
  refunded_amount: number?
  expected_amount: number?
  amount_diff: boolean
# GET /api/monitoring/metrics
total: integer
healthy: integer                 # completed 且无失败节点
unhealthy: integer
success_rate: number | null      # 空集 null
p50: number | null               # duration_ms nearest-rank 百分位
p95: number | null
per_graph: [{graph_id, total, healthy, unhealthy, success_rate, p50, p95}]
failed_nodes: [{node_id, node_type, count, last_error, last_seen}]  # count 降序
business:                        # M9：业务结果指标段（与系统指标分列；分母＝有业务结果的 run，样本 0 为 null）
  auto_refund_rate: number | null
  manual_escalation_rate: number | null
  refund_amount_diff_rate: number | null
  per_graph: [{graph_id, ...上述三率, samples}]
  per_version: [{graph_id, resolved_version, ...三率, samples}]
# RuleConfig（GET/PUT /api/monitoring/rules，PUT 全量替换，非法中文 422）
run_error:            {enabled: boolean}
node_failed:          {enabled: boolean}
consecutive_failures: {enabled: boolean, threshold: 1..200 整数}
failure_rate:         {enabled: boolean, window: 1..200, min_samples: 1..200, rate: 0..1}
# rollout_gate（M9）阈值不在全局 RuleConfig：随每图 RolloutConfig.gate 配置（见下 rollout_config）
# Alert（GET /api/alerts?status=；POST /api/alerts/{id}/acknowledge|resolve）
id: string                 # alt-{自增}
rule_id: "run_error" | "node_failed" | "consecutive_failures" | "failure_rate" | "rollout_gate"  # M9 增 rollout_gate（critical）
graph_id: string
severity: "critical" | "warning"
message: string
first_seen: string
last_seen: string
count: integer             # 同 (rule_id, graph_id) 合并非 resolved 最新告警
status: "open" | "acknowledged" | "resolved"
last_run_id: string
action:                    # M9 纯超集，仅 rollout_gate 告警携带；其余规则无此字段
  type: "rollback"
  from_version: int        # 被撤流的 candidate
  to_version: int          # 接全量的 stable
  reason: string           # 越阈指标/阈值/观察窗
  actor: "auto" | "manual"
```
> 进程内 ring buffer（200 条，满则丢最旧）+ 单锁同步评估（写运行→按图 streak→四规则），重启即失；`/api/demo/reset` 清空运行/告警并恢复默认规则（持久化随 11 S1/D11/D28）。未知告警 404、重复状态迁移 409。权威契约见 04 §5.13，内部接口见 12 §3.9，REST 见 12 §5。
>
> **租户注记（2026-09-16，§5.14）**：运行记录、指标、告警、规则均按租户分区（每租户独立 MonitoringStore 实例，run-/alt- 计数各租户从 1 起）；reset 仅清调用方租户的运行/告警并恢复该租户默认规则（计数器不重置）。

### `identity_session` — 字段概览（Phase 2 能力项，2026-09-16；`/api/auth/*` 与 `Authorization: Bearer`）

```yaml
# POST /api/auth/login 请求（公开端点；坏凭证 401 中文 detail）
username: string
password: string
# 登录响应 / GET /api/auth/me
token: string                 # sess-<uuid4.hex>，进程内 SessionStore 单锁保存，重启即失
principal:
  tenant_id: string           # "t1" | "t2"（v1 种子租户；不写进资源 JSON，由 token 推断）
  tenant_name: string         # 演示企业 A / 演示企业 B
  username: string
  display_name: string
  role: "viewer" | "operator" | "admin"
# POST /api/auth/logout：吊销当前 token，无返回体
```

> 种子租户/账号为代码常量（非 DB，明文密码仅 Demo）：t1 演示企业 A = admin-a/admin123（admin）、operator-a/operator123（operator）、viewer-a/viewer123（viewer）；t2 演示企业 B = admin-b/admin123（admin）。除 login、health、静态、`/demo/shop`、`/api/demo/**` 外全部端点必须 Bearer：缺失/坏 token → 401「缺少或无效的登录凭证」；角色不足 → 403「当前角色无权执行此操作」；访问他租户对象 → 404（不泄漏存在性）。角色矩阵（端点级白名单）、分区资源与全局基础设施清单、reset 本租户语义权威见 04 §5.14；内部接口（iam 包）见 12 §3.10；REST 鉴权列见 12 §5。持久化账号/密码哈希/SSO/JWT 缓做 11 S1 + 14 D22。

### `interruption_frame` — 字段概览（M5 契约设计轮 2026-09-17 新增，**设计已定、T18 已拍板（B）、2026-09-17 已随 M5b 落码**；权威＝docs/24 §2.3/§5，落码承载 `src/atlas/storage/frame.py` + `recovery.py`）

```yaml
resume_token: string            # uuid4 hex，即现有 approval/debug token（决策/恢复端点零改动）
tenant_id: string               # 由 for_tenant 工厂注入，方法签名不含租户
run_id: string
node_id: string
kind: "approval" | "debug" | "wait"
created_at: string              # UTC ISO-8601
deadline_at: string | null      # UTC 绝对时刻（审批超时/wait 到点）；恢复后按剩余时长等待，不重计
graph_snapshot: object          # 保存时图定义副本（M6 版本化未落地前随帧内嵌，防恢复错位）
resume_state:                   # 续跑载荷（挂起点续跑，不重跑上游）
  inputs: object                # run inputs（同名覆盖全局变量口径不变）
  outputs: object               # 截至挂起点的已完成节点产出
  decisions: object             # 已记录 branch/target 决策（condition/loop 续跑照走）
  trace_prefix: string[]        # trace 前缀，恢复后追加
  retry: object                 # 节点 retry 配置
```

> 可恢复边界：重启仅 `suspended` 帧可恢复（恢复扫描器逐帧重建 pending：approval 重挂 Event+剩余 deadline、debug 重挂暂停、wait 重排剩余 sleep）；`running` 中断的运行标 `interrupted`（失败档），不重放副作用。决策信号＝内存 `Event.set` + 恢复行落库双写，执行线程 `Event.wait(剩余)` + 1s PG 轮询复合等待。查询端点 `GET /api/runs?status=suspended`、`GET /api/runs/{run_id}`（12 已登记，**2026-09-17 已随 M5b 落码生效**）。

### `repository` — 字段概览（M5a 立项 2026-09-17、**同日落码（0358696）**；权威＝docs/24 §1，落码承载 `src/atlas/storage/base.py`）

```python
# typing.Protocol（结构化类型，实现类不强制继承）；方法签名与现有八 store 公开 API 一比一、不增删改名
class GraphRepository(Protocol):        # GraphStore: save/get/list/clear
class RecordingRepository(Protocol):    # RecordingStore: add/list/get/delete
class FeedbackRepository(Protocol):     # FeedbackStore: add/list
class SessionRepository(Protocol):      # SessionStore: issue/principal_for_token/revoke/reset
class ApprovalRepository(Protocol):     # ApprovalBroker: request/wait/resolve/complete_timeout/get/list_pending/reset
class DebugRepository(Protocol):        # DebuggerBroker: create/get_session/list_pending/reset
class MonitoringRepository(Protocol):   # MonitoringStore: record_run/list_runs/list_alerts/get_alert/acknowledge_alert/resolve_alert/get_rules/update_rules/snapshot_metrics/reset
# 设计时的 InterruptionRepository（审批+调试「挂起-决断」）拆为 ApprovalRepository/DebugRepository：
# 二者是两个独立实现类，无单一类满足合并方法集；共享 token+Event+首决生效语义 M5b 统一落 interruption_frame

def for_tenant(tenant_id: str) -> Repository: ...   # 由 TenantRegistry.get 承担；租户分区是构造期关切，方法签名不含 tenant_id
RESET_RESETTABLE / RESET_PERSISTENT: ...            # reset 分档：graph/approval/debug/monitoring/session=resettable；recording/feedback=persistent
```

> M5a 落码＝**零行为变化的进程内重构**：`storage/memory.py` 聚合**七个租户 store**（`api/main.py` 内联的 GraphStore/FeedbackStore 移出 + Approval/Debug/Monitoring/Recording 四类 re-export、延迟导入环消除），`TenantServices` 字段类型自 `object` 收紧为七个 Protocol（message_service 是服务非存储、保持 object）；**`SessionStore` 是 iam 包内全局会话单例（非租户 store、不进 TenantServices、不入 storage.memory 聚合——M6 落码修复了 M5a 遗留的 import 环）**，`SessionRepository` 协议仍供其结构化满足。类名/返回形状/中文文案/UTC 时间戳/id 生成不变。PG 实现与中断落库（`storage/pg.py`/`storage/recovery.py`）随 M5b。验收＝后端 445 passed/8 skipped（M6 后；M5a 当时 441）+ `/api/*` 端点签名零变化。


### `task_envelope` — 字段概览（M7 立项 2026-09-17、**同日三批落码（0f934d6/ff4acf9/e46fc09，08 M7 立项条+落码条）**；权威＝docs/19 §2.3.1 提案转权威 + 10 §4 ADR T20，落码承载 `src/atlas/coordination/{envelope,store,sandbox}.py`）

```yaml
taskId: string            # uuid4，任务唯一标识
runId: string             # 所属 run
idempotencyKey: string    # 幂等键 `资源|动作|版本`（L1 去重返首结果）
traceId: string           # 追溯（M10 span 前 = runId）
graphVersion: string      # 图版本 `graphId@<int>`（发布）/`graphId@draft`（M6 后可得，无 v 前缀）
type: string              # 任务类型 `refund.verify_order | logistics.check_receipt`
assignee: string          # 指派人 `bot.customer | bot.logistics`
payload: object           # 载荷；refs 含跨 Bot 数据引用 `{{...}}`
deadlineMs: number        # 任务级 deadline（超时三级链第①级）
state: string             # pending|accepted|running|done|failed|timeout
result: object            # 任务结果
attempt: number           # 尝试次数（重试幂等键不变）
```

> 状态机 dispatch `pending→accepted→running→done/failed/timeout`；join 复用 parallel all_completed 网关与 `result.<入口id>` 承载；escalate＝带审批卡片的特殊 human_approval。冲突三层（19 §2.4）：L1 幂等键、L2 CAS（demo shop 进程内 version 模拟）、L3 硬约束 condition + 升级。`TaskStore` 进程内首版、按租户分区（沿用 storage Repository 约定）；沙盘语义期不解除 D31。

### `rollout_config` — 字段概览（**M9 已落码 2026-09-18（批 1-4＋收口；后端 602/前端 396，提交链见 08 与 CHANGELOG）**；权威＝docs/19 §2.3.3 提案转权威 + 10 §4 ADR T22 + 08 M9 立项条，落码承载 `src/atlas/routing/{models,store}.py`，契约 04 §5.16）

```yaml
# PUT /api/graphs/{id}/rollout（存配置不启动，非法 422 中文）；GET 投影含状态与分流计数
strategy: "progressive"          # v1 唯一值
rules:                           # 求值顺序固定，v1 不做 when 表达式解析器（字符串条件落为结构化字段）
  - {to: "internal", tenants: [string]}          # 内部租户 allowlist 段
  - {to: "lowValueBucket", field: "payload.amount", op: "<=", value: number, percent: 100}
  - {to: "canary", percent: 1..100}              # 稳定哈希百分比段
  - {to: "full"}                                 # 放量段（promote 后生效，全量 candidate）
gate:
  observeMinutes: integer        # 默认 60；按 RunRecord.finished_at 时间过滤
  autoRollback: boolean          # 默认 true；false 时越阈只告警不切流
  minSamples: integer            # 默认 3；窗口内 candidate 样本不足不判（fail-safe 防冷启动误杀）
  metrics:
    - {id: "run_error_rate", threshold: number, minSamples?: int}
    - {id: "manual_escalation_rate", threshold: number, compareWith?: int, minSamples?: int}
    - {id: "refund_amount_diff_rate", threshold: number, minSamples?: int}
inFlightPolicy: "pin-to-version"  # v1 唯一值：一次 run 钉启动时不可变发布快照
# RoutingStore 每图运行态（GET /api/graphs/{id}/rollout 投影）
status: "idle" | "canary" | "full" | "rolled_back"
stable: int | null               # 前一发布版（start 时取最新两版）
candidate: int | null            # 最新发布版
started_at: string?
rolled_back_at: string?
rollback_reason: string?
traffic: {stable: int, candidate: int, segments: {internal:int, lowValueBucket:int, canary:int, full:int, fallback:int}}
# 状态迁移（POST .../rollout/{start|promote|rollback}）
# configure 存配置不启动；start: idle→canary（发布版不足 2 个 409）；
# promote: canary→full（唯一放量路径，仅手动，无任何自动 promote 代码）；
# rollback: */→rolled_back（candidate 撤流、stable 接全量；actor auto|manual 同一幂等函数）
```
> 每租户一个 RoutingStore 进程内实例（挂 TenantServices、reset 清空），与 iam 进程内分区/T20 任务总线同策略；真实多实例路由表同步/热推送/配置中心随 D6/D10b，沙盘不解除 D32。金融灰度硬条款（19 §2.5.5/20 §7.5）：业务桶 + 业务结果门控指标 + 回滚不改外部已发生事实，三条不可简化。

### `route_decision` — 字段概览（**M9 已落码 2026-09-18（批 1-4＋收口；后端 602/前端 396，提交链见 08 与 CHANGELOG）**；`src/atlas/routing/router.py` resolve_version 纯函数 + run 端点 event 接线）

```yaml
# POST /api/graphs/{id}/run 与 /run/stream 请求体新增可选字段（编辑器手动运行不传＝旧行为零回归）
event:
  channel: "api" | "webhook" | "im" | "embed"   # 缺省 "api"；v1 仅经现有 run 端点承载，无真实 ingress 服务器
  payload: object        # 入站业务载荷；tenant 由会话 Principal 定，不从请求体取
# resolve_version(*, graph_id, stable, candidate, event, config) -> (version:int|None, segment:str)
#   internal：event.tenant ∈ tenants → candidate
#   lowValueBucket：payload.amount 为数字且 ≤ value（percent=100）→ candidate
#   canary：桶键取 payload.order_id（缺省 payload.id）；
#           int(sha256(f"{graph_id}:{bucket_key}").hexdigest(), 16) % 100 < percent → candidate
#           桶键缺失/非字符串 → fail-safe 落 stable（segment="fallback"）
#   full：→ candidate；未命中任何段/无 candidate/未配置 → stable
# 带 event 但图无任何发布版 → 409「图尚未发布，入站事件无版本可路由」
# pin：按解析版本加载不可变快照运行（含 M6 subgraph 递归钉版）；中断帧 M5b 已冻结 graph_snapshot，
#      回滚后在途实例续跑仍为启动版本（不新增机制，U56 补帧快照断言）
```
> 入站复用 /run[/stream] + 可选 event（ADR T22④，vs 新增 /ingress 两套端点）：pin 解析集中一处，调试/录制/监控/追踪既有旁路自动复用；event 缺省严格保持草稿手动运行旧行为。RunRecord.resolved_version 记录解析版本（见上 `monitoring`）。
>
> **落码注记（2026-09-18）**：event 解析统一经 `RoutingStore.resolve`，入站事件路由绑定灰度生命周期——图只 `publish` 而从未 `PUT rollout`/`start`（RoutingStore 无该图 entry）时，即便已有发布版也返 409「图尚未发布，入站事件无版本可路由」；`start` 后按 idle/canary/full/rolled_back 状态机解析（rolled_back 后新事件全落 stable，在途实例 pin 见上）。

### `release_gate` — 字段概览（**M9 已落码 2026-09-18（批 1-4＋收口；后端 602/前端 396，提交链见 08 与 CHANGELOG）**；`src/atlas/recording/gate.py`，D26 仅取回发布前批量回放 + 跨版本 diff）

```yaml
# POST /api/graphs/{id}/release-gate（operate，只跑门禁不发布）→ GateReport
# POST /api/graphs/{id}/publish 请求体从无改可选 {gate?: boolean}；
#   gate=true 先跑门禁，blocked → 409 带完整报告且不产新版本
graph_id: string
target: "draft"               # 门禁对象＝当前 latest 草稿（publish 冻结物）
total: integer                # case.graph_id 匹配且非空的录制用例数（旧用例 graph_id="" 不入选）
passed: integer
failed: integer
skipped: boolean              # total=0 时 true：无用例不阻塞发布，但报告明示「未覆盖」，不假装通过
blocked: boolean              # total>0 且任一用例不匹配
cases:
  - case_id: string
    name: string
    matches: boolean
    replay_status: string
    note: string?             # 分支漂移/回放异常等
# 逐例对草稿走标准 run_graph（审批预置同现有 replay 端点），复用 recording.replay.compare 产逐节点 diff_keys
# D26 报告 v1（2026-09-18 已落码，见下 `release_report`）：响应纯超集加 id（沉淀报告 rr-N）；
#   release-gate 沉淀 trigger=manual、publish gate 沉淀 trigger=publish-gate（含 blocked 409 报告体），skipped 也沉淀
```

### `release_report` — 字段概览（**D26 报告 v1 已落码收口，2026-09-18，U60 转正式**；`src/atlas/recording/reports.py`，08 D26 落码条；用例集报告 v1＝沉淀 + 按图历史 + 通过率趋势）

```yaml
# ReportCaseRow：与 GateReport.cases 行同形 {case_id,name,matches,replay_status,note?}
# ReleaseReport（id rr-{n}，进程内 per-tenant，ring 100，reset 清空；不进 Repository 抽象/不 PG 化，照 RoutingStore 先例）:
id: string                      # rr-N
graph_id: string
target: "draft"
trigger: "manual" | "publish-gate"   # release-gate 端点＝manual；publish {gate:true}＝publish-gate（通过/blocked 均沉淀）
total: integer
passed: integer
failed: integer
skipped: boolean                # total=0 也沉淀（留「当时未覆盖」痕迹，覆盖趋势可见）
blocked: boolean
pass_rate: number | null        # passed/total；total=0 为 null
cases: ReportCaseRow[]
created_at: string              # ISO UTC
# GET /api/graphs/{id}/release-reports（read）→ {items:[摘要…]} 倒序、不含 cases
# GET /api/graphs/{id}/release-reports/{rid}（read）→ 完整 ReleaseReport；不属于该图/不存在 404，跨租户不泄漏
# D26-a（2026-09-19，8a37b4e）导出：GET /api/graphs/{id}/release-reports/{rid}/export?format=csv|json（read）
#   → attachment 下载（rr-N.json / rr-N.csv）；非法 format 422，404 照详情；CSV 为元信息行 + 空行 + 逐 case 行，带 UTF-8 BOM 供 Excel；reports.report_to_csv（stdlib csv/io）
```
> 2026-09-19 D26 收尾批已落：报告导出 CSV/JSON（8a37b4e）、subgraph 快照内联（29bb3d9，见 `recording_case`）、定时 CI 回放（cea70f4，`.github/workflows/release-gate-cron.yml`，cron 仅 main 生效）。仍缓做（不解除 D26）：影子模式、Mock 工具响应、用例编辑/参数化、PG 持久化与多租户共享、跨图聚合看板、报告删除端点（ring 自然淘汰）。

### `business_metrics` — 字段概览（**M9 已落码 2026-09-18（批 1-4＋收口；后端 602/前端 396，提交链见 08 与 CHANGELOG）**；`src/atlas/monitoring/business.py` extract_business 纯函数；金融灰度门控信号源）

```yaml
# extract_business(graph, outputs) -> BusinessOutcome（业务结果以 shop 终态为准，不以 AI 中间决策为准）
auto_refunded: boolean        # 任一 tool_call 节点 output.result.status == "refunded"
manual_escalated: boolean     # 任一 tool_call 节点 output.result.status == "human_review"
expected_amount: number?      # trigger 产出 context.payload.amount（回退入站 event.payload.amount）
refunded_amount: number?      # 退款节点 result.amount；demo shop 现不回金额 → 回退 expected（全额退口径）
amount_diff: boolean          # 两者皆在且不等
# 门控三率（分母＝有业务结果的 run；样本 0 为 null；按 resolved_version 分版本对照）
#   auto_refund_rate / manual_escalation_rate / refund_amount_diff_rate
# evaluate_after_run(services, record)（routing/gate.py，API 层 record_run 之后调用，
#   守 06 §6.11「API 层埋点、执行器零分支」，monitoring 不反向依赖 routing）：
#   仅当 record.resolved_version 是某图 candidate 且 status=canary 且 gate.autoRollback 时求值；
#   取 observeMinutes 窗内该图 candidate 运行（finished_at 过滤），<minSamples 不判；
#   run_error_rate 复用 is_healthy，另两率取 business 段；任一越阈 →
#   routing.rollback(actor="auto") + 产 rollout_gate critical 告警（携带 action，见 monitoring 段）；
#   compareWith 版本 v1 落为告警 message 中 stable 同期对照值（stable 样本不足明示），不做额外阻断。
```
> demo shop 不返回退款金额，沙盘现状 refund_amount_diff_rate 恒为 0：**门控机制与阈值先行、真实金额字段随正式 shop 接入**；不改造 demo shop、不碰录制归一化（避免破坏黄金用例比对）。自动回滚不改外部已发生事实（已退款不可逆，切流只影响新流量）。

### `memory_item` — 字段概览（**M11 立项 2026-09-19，docs-only 未开工**；权威＝docs/26 + 10 ADR T23，落码承载 `src/atlas/memory/{models,embeddings,items}.py`、`storage` MemoryRepository）

```yaml
# 统一记忆条目（fact/preference 以 kind 区分；进程内为 dict、PG memory_items 一行）
id: string                      # mem-{自增}（进程内租户计数；PG 取 storage_id_seq）
kind: "fact" | "preference"     # 必填：长期事实 / 用户偏好
content: string                 # 记忆文本，1-2000 字；remember 时据此算 embedding
scope: {string: string}         # 业务绑定（如 user_id/order_id），缺省 {}；recall 子集匹配
confidence: number              # 0-1，缺省 1.0（自动提取 <1 随 D35 缓做）
source: "tool" | "manual" | "run"   # 缺省 tool；v1 仅经工具写
metadata: object                # 附加信息，缺省 {}（PG 列名 meta，JSONB）
created_at: string              # UTC ISO-8601；v1 不可变（只追加/删，无 update）
# embedding: number[256]        # 内部字段，LocalDeterministicEmbedder 产出，不进 API 响应
```
> 第九个 Repository `MemoryRepository`（remember/recall/list/delete/clear，RESET_RESETTABLE）：进程内 `MemoryStore` + PG/pgvector `PgMemoryStore` 两档，租户分区（构造期注入 tenant_id，不进方法签名/资源 JSON）。`EmbeddingProvider` Protocol + 本地确定性 embedder（EMBED_DIM=256、signed hashing、中文 unigram+bigram、纯 stdlib、离线且回放确定；商业 embedding 缓做 D35）；PG 迁移 `006_memory.sql` vector(256)、`embedding <=> :q::vector(256)` 余弦、`scope @> :scope::jsonb` 子集。本地词法向量只验证机制与接缝、非真实语义。权威设计见 docs/26 §2–§4。

### `memory_remember` / `memory_recall` — 工具契约（**M11 立项 2026-09-19，docs-only 未开工**；`memory` Harness 适配器，adapter_id/type="memory"；零新节点、零 DSL/编译器改动）

```yaml
# 能力 memory/remember（action=memory_remember，permission=write，非幂等）
input:  {kind: enum[fact,preference]（必填）, content: string 1-2000（必填，支持 {{变量}}）,
         scope?: {string:string}, confidence?: number 0-1, metadata?: {string:string}}
output: {id, kind, content, confidence, source, scope, created_at}
# 能力 memory/recall（action=memory_recall，permission=read，幂等）
input:  {query: string（必填，支持 {{变量}}）, kind?: enum[fact,preference],
         scope?: {string:string}, top_k?: int 1-20=5, min_score?: number 0-1=0}
output: {results: [{id, kind, content, score: number, confidence, scope, created_at}]}  # 无命中 results=[]
```
> 走现有 tool_call/harness 链路（params 由 M3 FormRenderer 按 input_schema 自动生成、M2 变量补全零额外）；schema 守 Capability keyword 白名单（无 x- 扩展）。装配照 message 两段式：全局注册仅供发现，`_runtime_registry` 按租户克隆注入 `services.memory_store`。REST：`GET /api/memories`（viewer+，kind 过滤）、`GET /api/memories/search?q=`（viewer+，q 空 422）、`DELETE /api/memories/{id}`（**admin**，跨租户 404）；**写入不开 REST**（只走图工具）。docs/12 原 `GET/PUT /api/memories/{operator_id}` 据此订正为按租户、operator 降为 `scope.user_id`。working/summary/case 层、自动提取、决策隐式注入、PII/更新策略均缓做（D35）。
