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
| `ui_schema` | 04 / §4.10 末 M4 UISchema 最小子集扩展条（**M4 2026-09-17 立项、未落码**）+ `frontend/src/lib/forms/uiSchema.ts` + CondResolver（落码承载，规划） | ### 4.10 Schema 驱动表单渲染（M4 UISchema 扩展） |
| `graph_diagnostics` | 04 / §6.5 末 M4 前端 L3 预判 + Problems 面板扩展条（**M4 2026-09-17 立项、未落码**）+ `frontend/src/lib/validation/l3.ts`（规划）；后端图级规则权威仍为 `src/atlas/graph/dsl.py` | ### 6.5 拓扑作用域与 L2 模板引用校验（M4 前端 L3/Problems 扩展） |
| `adapter_schema` | 04 / 5.4 工具/适配器注册 Schema 示例代码 | ### 5.4 工具/适配器注册 Schema 示例代码 |
| `skill_schema` | 05 / 一、技能（Skill）1.2 技能的数据结构 | ## 1.2 技能的数据结构（示例） |
| `memory_config` | 05 / 二、记忆（Memory）2.3 记忆策略配置 Schema | ## 2.3 记忆策略配置 Schema（示例） |
| `collaboration_message` | 05 / 三、智能体协同 3.3 协同通信协议 | ## 3.3 协同通信协议（示例） |
| `deployment_config` | 05 / 四、部署方式 4.3 部署配置 Schema | ## 4.3 部署配置 Schema（示例） |
| `interaction_template` | 05 / 五、自定义前端模板 5.3 交互模板 Schema | ## 5.3 交互模板 Schema（示例） |
| `evaluation_task` | 06 / 9.2 评估 Harness 设计 | ### 9.2 评估 Harness 设计（借鉴 lm-evaluation-harness）代码示例 |
| `refund_decision` | `src/atlas/llm/decision.py`（W9-W10 权威实现；规则对齐 06 §9.2 黄金用例） | —（工程推导契约） |
| `refund_order` | `src/atlas/shop/service.py`（W9-W10 Demo 电商数据结构） | —（工程推导契约） |
| `run_event` | `src/atlas/graph/loader.py`（emit 产出）+ `src/atlas/api/main.py`（SSE 帧，W9-W10） | —（工程推导契约） |
| `nl_generate_request` | `src/atlas/api/main.py` NLGenerateRequest（W9-W10；响应为 `{graph: graph_definition}`） | —（工程推导契约） |
| `feedback_item` | `src/atlas/api/main.py` FeedbackRequest（Phase 1；进程内反馈，响应含 id/created_at） | —（工程推导契约） |
| `http_request_params` | 04 / 四、工具/适配器组件 4.6 API 适配器（通用 HTTP）v1 契约（权威 blockquote）+ `src/atlas/httpapi/{service,adapter}.py` | ### 4.6 API 适配器（通用 HTTP）v1 契约 |
| `db_sql_params` | 04 / 四、工具/适配器组件 4.7 数据适配器（通用 SQL）v1 契约（权威 blockquote）+ `src/atlas/database/{service,adapter}.py`（query/execute 两能力） | ### 4.7 数据适配器（通用 SQL）v1 契约 |
| `message_send_params` | 04 / 四、工具/适配器组件 4.8 消息适配器（进程内消息服务）v1 契约（权威 blockquote）+ `src/atlas/message/{service,adapter}.py`（单能力 message/send） | ### 4.8 消息适配器（进程内消息服务）v1 契约 |
| `template_catalog` | 04 / 五、逻辑组件 5.10 流程模板库（内置只读）v1 契约（权威 blockquote）+ `src/atlas/template/catalog.py`（5 个内置模板元数据与 graph） | ### 5.10 流程模板库（内置只读） |
| `recording_case` | 04 / 五、逻辑组件 5.11 操作录制与回放 v1 契约（权威 blockquote）+ `src/atlas/recording/cases.py`（录制用例模型与进程内存储） | ### 5.11 操作录制与回放 |
| `debug_session` | 04 / 五、逻辑组件 5.12 单步调试与断点 v1 契约（权威 blockquote）+ `src/atlas/debug/{sessions,controller}.py`（运行期调试会话、暂停状态机、paused/stopped 帧） | ### 5.12 单步调试与断点 |
| `monitoring` | 04 / 五、逻辑组件 5.13 基础监控告警 v1 契约（权威 blockquote）+ `src/atlas/monitoring/{records,metrics,alerts}.py`（运行记录 ring、指标聚合、规则求值与告警状态机） | ### 5.13 基础监控告警 |
| `identity_session` | 04 / 五、逻辑组件 5.14 多租户与权限 v1 契约（权威 blockquote）+ `src/atlas/iam/{principals,sessions,registry,deps}.py`（种子租户/账号、Principal、sess- token、按租户服务注册表、Bearer 依赖） | ### 5.14 多租户与权限 |

---

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
                           #   {joinStrategy: all_success|all_completed, branches:[{label,target}], joinTarget}
                           #   唯一权威见 04 §5.4「parallel 节点 config 契约」
                           # wait 节点 config 形状：
                           #   {waitType:"duration", durationSeconds: 1-600 整数}
                           #   唯一权威见 04 §5.5「wait 节点 config 契约」
                           # human_approval 节点 config 形状：
                           #   {summary, approver?, timeoutSeconds: 10-3600 整数, onTimeout: approve|reject(默认reject),
                           #    approvedTarget, rejectedTarget}
                           #   唯一权威见 04 §5.6「human_approval 节点 config 契约」
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
version: 1                   # Graph JSON 版本，当前仅支持 1
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
> **模板引用与拓扑作用域注记（2026-09-16，§6.5）**：config 内 `{{路径}}` 的节点输出可见性按图拓扑推导（visibleAt = 沿入边反向可达上游 + 全局变量 + loop 体区域），各节点类型输出投影、L2 三错误码（REF_NODE_NOT_FOUND / REF_NOT_IN_SCOPE / REF_PATH_NOT_FOUND）与 token 区间权威见 04 §6.5；前端实现 `frontend/src/lib/scope.ts`，后端编译期复查在 `atlas.graph.dsl`，运行期插值缺失保留原样语义不变。工具 `result.*` 深层路径以 04 §4.9 的 output_schema 子集为来源。

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

### `ui_schema` — 字段概览（**M4 2026-09-17 立项、未落码**；权威见 08 M4 立项条与 04 §4.10 末扩展条，落码承载 `frontend/src/lib/forms/uiSchema.ts` + CondResolver；ADR T17 复查不重开见 10 §4）

```ts
// lib/forms/uiSchema.ts（M4 规划，落码以 04 §4.10 扩展条为准）
// 最小子集，仅承接复杂度 dump 实测的两件需求，不引入完整 JSON Schema UI 规范：
type UiSchema = {
  groups?: Array<{ key: string; label?: string; fields: string[]; layout?: 'row' }>
  // ui:group：把同层字段归入分组容器；实测仅 HumanApproval approved/rejected 并排（layout:'row'）
  hiddenWhen?: Array<{ field: string; equals: unknown; show: string[] }>
  // 条件显隐：判别字段 field 取 equals 时才显示 show 中的字段；其余隐藏
  // 实测仅 trigger：triggerType=schedule 显 cron、=webhook 显 webhookUrl、=manual 全隐
}
// 不做 ui:order（M3 已按 schema properties 顺序渲染）、不做 if/then 动态 dependencies
// x-widget 沿用 M3 resolveWidget，不在本契约重复
```
> 验收候选用例 **U40**（13 文档，候选）：ui:group 分组容器与并排布局、hiddenWhen 三分支显隐、未命中条件时字段不渲染且不参与校验。复杂度 dump 证据（8/9 节点零条件结构、UISchema 真实需求 2 处）见 08 M4 立项条。

### `graph_diagnostics` — 字段概览（**M4 2026-09-17 立项、未落码**；权威见 08 M4 立项条与 04 §6.5 末扩展条，落码承载 `frontend/src/lib/validation/l3.ts` + Problems 面板组件；后端图级规则唯一权威仍为 `src/atlas/graph/dsl.py`）

```ts
// lib/validation/l3.ts（M4 规划，规则从 dsl.py 同构提取，参照 conditions.ts 先例）
// 首批两条图级规则，产 layer:'graph' Diagnostic（M2 仅留类型位、M4 转正）：
//   GRAPH_UNREACHABLE：从 trigger BFS 不可达节点（同构 dsl.py _validate_reachability）
//   GRAPH_ILLEGAL_CYCLE：移除 loop 白名单回边后 DFS 三色检测成环（同构 _validate_illegal_cycles）
// 前端 L3 仅实时预判；后端 dsl.py 仍是唯一权威，不搬后端重跑、M4 不改后端
// 分支完备已由 condition L1 手写规则（defaultTarget 必填）覆盖、悬空引用由 L2 REF_NODE_NOT_FOUND 覆盖
// Problems 面板：消费 validateGraph 全图 Diagnostic[]，rank 排序，点击按 nodeId+pointer 定位节点/字段
// quickFix v1：仅「删除悬空引用」一个动作（20 §2.5 法定），挂 L2 REF_NODE_NOT_FOUND；reverseDeps 支撑改 id 引用方定位
```
> 验收候选用例 **U41**（13 文档，候选）：构造含环/不可达图，前端 L3 诊断与后端 dsl.py 逐条同构对拍；Problems 聚合/rank/点击定位；quickFix 删除悬空引用。分层调度（L1 同步/L2 防抖/L3 requestIdleCallback）、增量脏标记失效范围、200/500 节点基准入 BENCHMARK.md，见 08 M4 立项条③⑦。

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

### `interaction_template` — 字段概览（完整定义见 05-组件设计-运营体五项核心.md #552，上下文章节：## 5.3 交互模板 Schema（示例））

```yaml
id: string
name: string
type: enum[approval, form, notification, guide, progress, choice, alert]
version: string
```

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
# 节点结束
type: "node_end"
node_id: string
node_type: string
output: object          # 该节点产出（决策 dict / 工具 ActionResult 输出）
# 运行结束（SSE 末帧为 event: result，载荷 {id, status, outputs, traces}）
type: "run_end"         # 随 run_graph 返回值展开
```
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
graph: graph_definition    # 录制时的图快照（冻结，非 graph_id 活引用）
created_at: string         # UTC ISO-8601
# GET /api/recordings 列表投影（不含 graph/steps）
items: [{id, name, node_count, step_count, status, created_at}]
# POST /api/recordings/{id}/replay 响应（ReplayReport）
matches: boolean           # 操作序列与逐节点归一化产出全部一致
baseline_status: string
replay_status: string      # 回放异常（如子图引用缺失）折叠为 "failed"
steps: [{node_id, match, note, diff_keys?}]  # diff_keys 为归一化后差异顶层键
```
> 进程内存储（重启清空，持久化随 11 S1）；`/api/demo/reset` 不清除（测试资产，同 feedback）。回放从 human_approval 步骤抽解决策预置为 inputs.approvals，不挂起；比对前递归剔除 token/sent_at、消息记录 uuid id、HTTP headers date。权威契约见 04 §5.11，REST 见 12 §5。
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
# GET /api/monitoring/metrics
total: integer
healthy: integer                 # completed 且无失败节点
unhealthy: integer
success_rate: number | null      # 空集 null
p50: number | null               # duration_ms nearest-rank 百分位
p95: number | null
per_graph: [{graph_id, total, healthy, unhealthy, success_rate, p50, p95}]
failed_nodes: [{node_id, node_type, count, last_error, last_seen}]  # count 降序
# RuleConfig（GET/PUT /api/monitoring/rules，PUT 全量替换，非法中文 422）
run_error:            {enabled: boolean}
node_failed:          {enabled: boolean}
consecutive_failures: {enabled: boolean, threshold: 1..200 整数}
failure_rate:         {enabled: boolean, window: 1..200, min_samples: 1..200, rate: 0..1}
# Alert（GET /api/alerts?status=；POST /api/alerts/{id}/acknowledge|resolve）
id: string                 # alt-{自增}
rule_id: "run_error" | "node_failed" | "consecutive_failures" | "failure_rate"
graph_id: string
severity: "critical" | "warning"
message: string
first_seen: string
last_seen: string
count: integer             # 同 (rule_id, graph_id) 合并非 resolved 最新告警
status: "open" | "acknowledged" | "resolved"
last_run_id: string
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

