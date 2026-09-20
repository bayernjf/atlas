# Schema 驱动配置内核与多智能体端到端方案（题述设计）

> **文档性质**：工程推导方案文档（同 17/18，非规格唯一事实源；与 01-08 冲突时以 01-08 为准）。
> **来源**：2026-09 两道设计题（① Schema 驱动编辑器内核；② 多智能体协同 + 部署 + 交互模板端到端）的完整作答，结合当前代码现状标注「已有 / 缺口」。
> **状态**：题一「设计态内核前置项」（Capability input/outputSchema 声明 + 拓扑作用域索引 + L2 模板引用校验）**已于 2026-09-16 落码并随 PR #19 合并 main**（范围以 08 立项记录为准）；其余提案（Schema 内核通用化、作用域 v2、多智能体任务总线、版本化灰度、交互模板、span 追踪）登记于 14 文档 D29-D34。文内新增依赖（AJV、Web Worker 等）、协同协议、版本/灰度模型仍为提案；落码前须按治理规则先走 10 文档 ADR + 08 计划记录 + 03 契约索引同步，不得直接视为已定选型或已生效契约。
> **与现有契约的关系**：文内 Schema 示例是目标形态提案；当前权威数据结构仍以 03 索引定位、04/05/06 正文为准。

## 0. 总览：一份契约，两个时期

两道题分别对应平台的**设计态**与**运行态/发布态**，咬合点是同一份节点 config Schema 与同一套 `{{路径}}` 变量语义：

- 题一（设计态）：Schema 驱动前端配置内核——异构实体（节点/工具/技能/记忆/部署/交互模板）共用一套「Schema → 表单 → 变量补全 → 分层校验」机制。
- 题二（运行态/发布态）：自然语言生成 → 多智能体协同 → 人机交互 → 多渠道部署 → 灰度回滚 → 可观测的端到端闭环。

接缝是**适配器/工具能力声明上的 `inputSchema/outputSchema`**：

- 设计态靠它生成工具参数表单、推导 `{{}}` 变量作用域与类型；
- 运行态靠它让 NL planner 合法组合、让卡片模板绑定变量、让回放入参形状稳定；
- 编辑期静态解析的 `{{}}` 与运行期插值是同一语义的两个时期；
- 设计态图级校验是发布态灰度的准入，运行态录制回放是静态校验的运行期延伸。

---

# 题一：Schema 驱动配置内核

## 1.1 核心模块划分

```
┌─────────────────────────────────────────────────────────┐
│                    Editor Runtime                       │
├──────────────┬──────────────┬───────────────────────────┤
│ Schema 层    │ 渲染层       │ 校验/智能层               │
├──────────────┼──────────────┼───────────────────────────┤
│ SchemaRegistry│ FormRenderer│ ValidationEngine          │
│ (实体×来源)   │ (递归下降)   │  ├ field: JSON Schema     │
│ MetaSchema    │ UISchema     │  ├ template: {{}} 扫描    │
│ 自定义keyword │ WidgetRegistry│ └ graph: 拓扑分析        │
│               │ (控件注册表) │ ScopeIndex (作用域图)     │
│               │ Visibility   │ Diagnostics (诊断模型)    │
│               │ CondResolver │ IncrementalScheduler      │
└──────────────┴──────────────┴───────────────────────────┘
```

| 模块 | 职责 | 关键设计 |
|---|---|---|
| SchemaRegistry | 按实体类型（节点/工具/技能/记忆/部署/卡片）装载 schema | schema 多来源：内置节点随包发布；工具/技能参数 schema 来自适配器能力声明；部署/卡片来自渠道插件。注册表只持有引用+版本，不复制 |
| MetaSchema | 约束「schema 本身合法」 | 规定允许的 JSON Schema 子集 + Atlas 扩展 keyword |
| FormRenderer | data + uiSchema → React 控件树 | object 递归分组、array 增删行、enum→Select、oneOf/if-then→条件区块；值不可变更新，产出 JSON Patch |
| WidgetRegistry | 控件名→组件 的唯一扩展点 | 内置 widget（text/number/select/textarea/json/code/expression/variable-input）；业务侧 register 自定义控件，声明 value/onChange/校验契约 |
| UISchema / CondResolver | 布局与条件显示 | `ui:field`、`ui:order`、`ui:hidden: "expr"`；可见性表达式声明依赖，响应式求值（不命令式 show/hide） |
| TemplateAnalyzer | 横切层：扫描所有字符串值中的 `{{...}}` | 与 JSON Schema 正交——Schema 只看到 string，引用由这层提取 |
| ScopeIndex | 变量作用域索引 | 图拓扑 + 各节点 output schema 的派生物（见 1.4） |
| ValidationEngine | 三级校验 + 诊断聚合 | field/template/graph 三层（见 1.5） |
| IncrementalScheduler | 脏标记传播与调度 | 编辑→失效范围计算→分层重算（见 1.6） |

**关键架构事实**：`{{}}` 模板引用校验是一层**横切机制**，不归 JSON Schema 管。模板引用嵌在字符串值里，标准 JSON Schema 只看得到「这是个 string」；一个普通 string 字段是否允许变量补全，由 schema 上的自定义 keyword（`x-variable`）/widget 声明。因此必须是两套正交机制：Schema 数据校验 + 模板感知校验器。

### Atlas 现状与缺口

- ✅ 节点目录与手写校验：`frontend/src/lib/nodeCatalog.ts`（默认 config + 每类型 validate）、`frontend/src/lib/conditions.ts`（表达式语法校验，与后端同构）。
- ✅ Widget Registry 的手工前身：每类型一个手写 Config 组件（ConditionConfig/ToolCallConfig 等 8 个），PropertyPanel 按 kind 分派；**没有注册表抽象**。
- ❌ 无 SchemaRegistry/MetaSchema：节点 schema 隐含在 TS 类型和手写组件里，不是声明式 JSON Schema。
- ❌ 工具 params 当前是单个 JSON 字符串框 + 占位符示例，**工具参数 schema 没有驱动表单**（适配器能力声明只有 id/permission/idempotent，无参数/输出 JSON Schema）。
- ❌ 技能（`src/atlas/skills/` 空包）、记忆、部署、交互模板四类实体尚无配置 UI，更未纳入同一内核。
- **缺口结论**：两个前置——(a) nodeCatalog 手写规则升级为「内置节点 Data Schema 包 + UISchema」；(b) 适配器 Capability 增加 `inputSchema/outputSchema`。

## 1.2 数据流

```
实体选择(节点类型/工具)
   │ 1. 取 schema        SchemaRegistry.get(kind, version)
   ▼
[Data Schema] [UI Schema] ──► FormRenderer 递归建控件树
   │                              │
   │                       用户编辑（受控值）
   ▼                              ▼
config draft (Immutable) ──► IncrementalScheduler
                                  │ 局部失效
            ┌─────────────────────┼──────────────────────┐
            ▼                     ▼                      ▼
     FieldValidator        TemplateAnalyzer         GraphAnalyzer
   (AJV 数据约束)       extractRefs → ScopeIndex    (拓扑/可达/环/分支)
            └─────────────────────┼──────────────────────┘
                                  ▼
                          Diagnostics[] 统一诊断流
                                  │
              ┌───────────────────┼────────────────────┐
              ▼                   ▼                    ▼
        字段红字/边框        token 高亮(内嵌)      画布节点角标+边高亮
              └───────────────────┴────────────────────┘
                                  ▼
                    Problems 面板（点击定位 nodeId+字段+token）
保存/编译 ──► 后端同一规则集权威校验（422 聚合）  ← 不信任前端
```

要点：**编辑流（体验）与提交流（权威）共用规则定义、分开执行**。前端规则以可序列化形式从同一契约生成（TS 与 Python 各一份，或后端下发 schema）。

### Atlas 现状与缺口

- ✅ 双端校验分工已存在：前端 catalog/conditions 实时提示，后端 `src/atlas/graph/dsl.py`（保存/编译期聚合中文 422）；`conditions.ts` 头部明确「图级校验由后端兜底」。
- ✅ 节点错误角标、实时错误清单已在 W5-W6 存在。
- ❌ 诊断非结构化（无 nodeId + JSONPointer + token range），不能「点击错误跳到字段中的 token」。
- ❌ `{{}}` 缺失目前只在变量插入下拉层面，不做引用存在性高亮。

## 1.3 关键 Schema 示例

约定：标准 JSON Schema 做数据约束，Atlas 扩展 keyword 一律 `x-` 前缀。

### 1.3.1 节点 Data Schema（以 human_approval 为例的目标形态）

```json
{
  "$id": "atlas/node/human_approval/v1",
  "type": "object",
  "required": ["summary", "approver", "timeoutSeconds"],
  "properties": {
    "summary":     { "type": "string", "minLength": 1, "x-variable": true,
                     "x-i18nKey": "node.approval.summary" },
    "approver":    { "type": "string", "x-widget": "approver-picker" },
    "timeoutSeconds": { "type": "integer", "minimum": 10, "maximum": 3600 },
    "onTimeout":   { "enum": ["approve", "reject"], "default": "reject" },
    "approvedTarget": { "type": "string", "x-ref": "nodeId" },
    "rejectedTarget": { "type": "string", "x-ref": "nodeId" }
  },
  "allOf": [
    { "if": { "properties": { "onTimeout": { "const": "approve" } } },
      "then": { "required": ["approvedTarget"] } }
  ],
  "x-outputSchema": {
    "type": "object",
    "properties": {
      "decision":  { "enum": ["approved", "rejected"] },
      "resolvedBy":{ "enum": ["human", "input", "timeout"] },
      "target":    { "type": "string" }
    }
  }
}
```

### 1.3.2 UI Schema（与数据解耦）

```json
{
  "ui:order": ["summary", "approver", "timeoutSeconds", "onTimeout",
               "approvedTarget", "rejectedTarget"],
  "summary":     { "ui:widget": "tpl-textarea", "ui:rows": 3 },
  "approver":    { "ui:widget": "approver-picker" },
  "timeoutSeconds": { "ui:group": "timeout" },
  "onTimeout":   { "ui:widget": "radio", "ui:group": "timeout" }
}
```

### 1.3.3 工具能力声明（让工具参数也能驱动表单）

```json
{
  "adapterId": "database", "tool": "database/query",
  "permission": "read", "idempotent": true,
  "inputSchema": {
    "type": "object",
    "required": ["sql"],
    "properties": {
      "sql":     { "type": "string", "x-widget": "sql-editor" },
      "params":  { "type": "object", "x-variable": true,
                   "description": "绑定参数 :name → {{路径}}/字面量" },
      "limit":   { "type": "integer", "minimum": 1, "maximum": 1000, "default": 500 }
    }
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "columns":   { "type": "array", "items": { "type": "string" } },
      "rows":      { "type": "array", "items": { "type": "object" } },
      "row_count": { "type": "integer" },
      "truncated": { "type": "boolean" }
    }
  }
}
```

语法覆盖：嵌套对象 → `properties` 递归；数组 → `items` + array widget；枚举 → enum；条件显示 → `if/then/allOf`（数据约束）或 `x-ui:if`（纯展示条件）。分工原则：**影响数据合法性的用 if/then，只影响观感的用 UI 条件**。

### 1.3.4 自定义控件契约

```ts
interface WidgetProps<T = unknown> {
  value: T
  onChange: (next: T) => void
  schema: JSONSchema          // 该字段的 schema 片段
  uiSchema: UISchemaNode
  scope: ScopeQuery           // 控件可查当前作用域（variable-input 用）
  diagnostics: Diagnostic[]   // 命中本字段的诊断（含 token 区间）
  renderMarkers?: (ranges: TokenRange[]) => ReactNode  // {{}} 高亮
}
registerWidget('approver-picker', ApproverPicker)
registerWidget('tpl-textarea', TemplateTextArea)  // 内含补全弹层
```

### Atlas 现状与缺口

- ✅ 1.3.1 字段语义与边界（10–3600、默认 reject、双 target）已在 `nodeCatalog.ts` 常量与 HumanApprovalConfig 中**硬编码实现**——迁到 Schema 是「提取声明」，非重新设计。
- ✅ 1.3.3 outputSchema 形状即现有真实产出（database 适配器就返回 columns/rows/row_count/truncated）——**输出形状已有事实标准，缺的是把它声明进 Capability**。
- ❌ 1.3.3 inputSchema 完全缺失：tool_call 的 params 是单个 JSON 字符串，前端无法生成逐字段表单，NL 只能在 prompt 里口述 JSON 形状。
- ❌ 无 `x-variable` 声明；「插入变量」按钮靠各 Config 组件手工放置。

## 1.4 变量作用域索引设计

### 1.4.1 两层结构

```
ScopeIndex {
  symbols: Map<path, SymbolInfo>   // "http-1.result.status" → {type, source, nodeId}
  visibleAt(nodeId): Set<path>     // 该节点配置位置可见的符号
  reverseDeps: BidirectionalGraph  // path ↔ 引用它的字段/节点（失效传播+数据环检测）
}

SymbolInfo = {
  source: 'trigger' | 'global' | 'node' | 'subgraph' | 'secret' | 'env'
  type:   JSON type 片段（来自 outputSchema）
  nodeId?: string
}
```

### 1.4.2 可见性规则（沿拓扑推导，不是全局平铺）

对编辑位置节点 `N`：

1. `trigger.context.*`（trigger payload）、`global.*`：恒可见；
2. **控制流上游**：从 N 沿入边反向 BFS 可达的全部节点 outputs（可达路径上的才合法）；
3. 结构投影：
   - `condition`：`branch / target / evaluation`；
   - `loop`：体内额外可见 `{{loop-x.index}}`（体外不可见）；
   - `parallel`：`status` 静态可见；`result.<分支入口节点id>.*` 仅在汇聚点之后可见；
   - `subgraph`：`outputs.*`（深层路径依赖子图 output schema 展开）；
   - `human_approval`：`decision / resolvedBy`；
4. 受限来源单列：`secret.*`、`env.*` 只对标记 `x-secret-allowed` 的字段可见，且不回显明文。

构建算法：拓扑序一遍扫描，每个节点的可见集 = 其全部前驱可见集的并集（分支汇聚点取并）∪ 自身 output，再叠加结构规则。复杂度 O(V+E)。

作用域来源不止图节点：对工具/技能/记忆/部署等实体的配置表单，候选源至少含 trigger payload、全局变量、上游 outputs、子图 outputs、密钥/环境变量（受限单列）。

### 1.4.3 引用解析与诊断

`{{a.b[0].c}}` 解析后分级：

- 节点不存在 → `REF_NODE_NOT_FOUND`；
- 节点存在但不在可见集 → `REF_NOT_IN_SCOPE`（引用下游/并行另一支）；
- 路径在 outputSchema 中不存在 → `REF_PATH_NOT_FOUND`；
- 类型与使用点不符 → `REF_TYPE_MISMATCH`（warning 级；outputSchema 缺省时降级为仅存在性检查，这是题目硬要求下限）。

另有**数据依赖环**（A 配置引用 B 输出、B 又引用 A）与控制流拓扑环分开建图、分开检测。

### Atlas 现状与缺口（差距最大的一块）

- ✅ 引用语法与路径解析完备：`frontend/src/lib/variables.ts` 的 `extractRefs / resolvePath`（支持 `a.b[0].c`），运行期缺失保留原样；后端 loader 插值同语义。
- ⚠️ **作用域全局平铺、非拓扑推导**：`listVariablePaths` 返回所有 global + 画布每个节点的**一个固定顶层 key**；引用下游节点不报错；嵌套 output 路径（如 `result.status`）不在清单里靠手打；parallel.result 动态键明确不列；只有 global 作用域（源码注释自述 session/environment/secret 待补）。
- ❌ 无 outputSchema → 无类型信息，只能做存在性；无 reverseDeps（改节点 id/输出时引用方不标红，当前仅在删除目标节点时手工清引用）。
- **升级方向**：从「扁平路径清单」升级为「拓扑作用域索引 + 反向依赖图」。

## 1.5 校验分层与伪代码

### 1.5.1 结构化诊断模型

```ts
Diagnostic = {
  severity: 'error' | 'warning',
  layer: 'field' | 'template' | 'graph',
  code: string,                       // REF_NODE_NOT_FOUND ...
  message: string,                    // 中文，i18n key 同源
  loc: { nodeId?: string, pointer?: string,  // RFC6902 字段定位
         token?: { start: number, end: number, raw: string } },
  quickFix?: Fix[]                    // 改名/删除悬空引用/补 default
}
```

### 1.5.2 三层校验伪代码

```
validate(graph, changed):
  diags = []
  # L1 字段级：数据约束，AJV 按 schema 跑（仅重算变更实体）
  for node in changed.nodes:
    schema = SchemaRegistry.get(node.kind)
    diags += ajv(schema, node.config).map(toDiag(node.id))

  # L2 模板引用级（横切）
  scope = ScopeIndex.of(graph)                    # 增量重建
  for (nodeId, pointer, value) in allStringFields(graph, changed):
    for {raw, range} in extractRefsWithRange(value):
      sym = scope.resolve(raw, visibleAt=nodeId)
      if !sym: diags += {layer:'template', loc:{nodeId,pointer,token:range}, ...}
      else if typeMismatch(sym, schemaAt(pointer)): diags += warning(...)

  # L3 图级
  g = adjacency(graph)
  diags += BFS(trigger, g).unreached.map(UNREACHABLE)
  diags += detectCycles(removeLegalLoopBackEdges(g))   # 控制流环（豁免 loop 回边）
  diags += detectCycles(reverseDeps.dataGraph)         # 变量数据依赖环
  diags += branchCompleteness(graph)
  return rank(diags)   # error 优先，按拓扑序
```

`branchCompleteness` 覆盖题干「无条件分支缺失」全部形态：

- condition：无 branches / 存在空 expression 分支 / **缺 defaultTarget** / 出边未被任一分支覆盖（有边无分支）/ 分支 target 不存在；
- parallel：分支数越界、label 空/重、target 重复、有分支不可达 joinTarget；
- loop：body/exit 目标缺失或指向自身/END；
- human_approval：双出口未配对、target 不存在；
- 普通节点：悬空边、自环、直连 END 违规。

### 1.5.3 前后端职责

| 层 | 前端（实时体验） | 后端（保存/编译权威） |
|---|---|---|
| L1 | AJV 即时红字 | pydantic + 同规则 422 |
| L2 | 输入时高亮、补全过滤 | 编译期复查（防绕过） |
| L3 | 画布角标/边高亮（可延迟） | **唯一权威**，聚合报错拒绝保存 |

### Atlas 现状与缺口

- ✅ L3 后端已是这一形态：`graph/dsl.py` 含 BFS 可达性、临时摘除 loop 合法回边后的 DFS 环检测、condition/parallel/loop/human 出边完备与分支区域校验，中文聚合 422。
- ✅ L1 前端有手写实时校验；L3 前端有部分镜像（catalog 校验矩阵有 vitest）。
- ❌ L2 不存在；前端无 L3 诊断引擎（依赖保存时 422，画布上不预判不可达/环）。
- ❌ 诊断非结构化、无 token range/quickFix。
- 方向约束：**不把 dsl.py 搬去前端重跑**；应抽出声明式规则集，TS/Python 两端共用（`conditions.ts` 已是「同构」先例）。

## 1.6 性能优化方案

| 手段 | 做法 | 解决什么 |
|---|---|---|
| 增量校验 | 依赖图：config 变更只失效该实体 L1；增删边/改 target 失效受影响子图 L3；改 output schema 经 reverseDeps 只重算引用方 L2 | 避免每次按键全图重算 |
| 分层调度 | L1 同步（按键）→ L2 微任务/50–100ms 防抖 → L3 requestIdleCallback / Web Worker | 拖拽/输入不掉帧 |
| 索引缓存 | ScopeIndex 拓扑序记忆化，结构版本号（nodes/edges hash）未变直接复用；visibleAt 按节点缓存 | 补全弹层 O(1) |
| 虚拟化 | 大数组表单虚拟滚动；Problems 列表虚拟化 | 大图 DOM 爆炸 |
| 不可变更新 + 结构化 memo | 字段级订阅，控件仅在自身值/诊断变化时重渲染 | 表单联动重渲染风暴 |
| AJV 编译缓存 | schema 按 $id+version 编译一次，validate 复用 | JSON Schema 校验主要开销 |
| 补全检索 | 符号按段建 trie/前缀索引 | 千级符号仍即时 |
| 降级策略 | 节点数超阈值（如 500）：L3 降为保存时 + 手动「检查图」，编辑中只给 L1/L2 | 超大图保底 |

### Atlas 现状与缺口

- ✅ Zustand 5 不可变 store、纯函数 lib 层（无副作用便于记忆化）、vitest 52 例——增量改造地基好。
- ❌ 当前全量手跑、无 Worker、无索引缓存；Demo 量级（数十节点）不构成真实瓶颈，**属架构储备**：建议随「工具 schema 表单化」一起做增量 L2，L3 Worker 缓做。
- ⚠️ AJV/Web Worker/trie 等均为**新增前端依赖提案**，落码前须入 10 文档 §4 ADR，不视为已定选型。

---

# 题二：多智能体协同 + 部署 + 交互模板端到端

## 2.1 总体架构

```
┌─────────────────────────────── 入站渠道层 ───────────────────────────────┐
│ Webhook 网关        IM 适配(钉钉/企微/飞书)      网页嵌入(SDK/iframe)      │
│              统一归一化为 TriggerEvent{channel, tenant, payload}          │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌──────────────────────── 设计态（题一内核的产物） ────────────────────────┐
│ NL 生成 → 技能/模板检索 → 图规划 → 人确认 → 编译校验 → 版本化发布          │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼ 不可变 Graph 版本 vN
┌──────────────────────────── 运行时（控制平面） ──────────────────────────┐
│ Router(灰度规则: 租户/业务桶/百分比 → 版本)                               │
│ Graph Engine（确定性主干: 路由/分支/并行/等待/子图/审批挂起）              │
│                                                                          │
│   Coordinator(主协调)                                                    │
│     ├─ Task Envelope Bus ──► CustomerService Bot (技能+适配器)           │
│     ├─                    ──► Logistics Bot                             │
│     └─ Approval Service ◄──── 人工决策（卡片回调 / 超时 / 预置）          │
│                                                                          │
│ Harness 适配器层: shop / http / database / message(+真实渠道)            │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌──────────────────────────── 可观测与发布 ────────────────────────────────┐
│ Trace(事件流/span) │ Metrics(系统+业务) │ Alerts(门控) │ Recording 回放  │
│ 灰度切流 ─ 指标门控 ─ 自动回滚（在途实例钉版本）                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**设计立场（显式声明）**：主协调者是**确定性 Graph，不是自由 Agent**。理由：可静态校验（题一全部图级规则）、可回放（recording 前提）、灰度可按版本切流、金融动作可审计。柔性判断（路由拿不准、客诉情绪升级）在节点内嵌受护栏的 Coordinator Agent：只输出「下一步意图 + 置信度」，低置信走规则/升级人工，**LLM 不掌握控制流写入权**。纯多 Agent 自治 mesh 为备选，退款这类金融+合规场景不采用。

### Atlas 现状与缺口

- ✅ 确定性主干：9 类节点（trigger/ai_decision/tool_call/condition/loop/parallel/wait/subgraph/human_approval）+ LangGraph 引擎 + SSE 事件流。
- ✅ 入站归一化思想：webhook payload 经 run inputs 进 `trigger.context.payload`；trigger 枚举 manual/schedule/webhook。
- ✅ 适配器层：shop/http/database/message + 注册发现（permission/idempotent 标记）。
- ⚠️ **多 Bot 协同不存在**：图直接调适配器，无 Bot 执行体抽象、无任务总线；`skills/` 空包。
- ❌ Router/版本化/真实渠道网关没有（GraphStore 进程内、无版本）；message 适配器零真实投递；IM/网页嵌入无。
- ⚠️ **长流程上线第一前置缺失**：wait/human_approval 为进程内 sleep/Event（D19/D20 缓做），持久化中断-恢复依赖 11 存储层 S1。

## 2.2 关键流程

### 2.2.1 NL → 可运行图（六步，生成与推荐/组合分开）

```
1 解析    NL → 意图(退款流程) + 实体(订单号/金额/渠道/时限)
2 检索    意图+实体 → 技能库/模板库候选原子（retrieve + rank：标签/历史成功率）
3 规划    planner 出图骨架: trigger→决策→分支→工具/审批→通知
           （LLM 只产出节点+边声明，工具 id 必须取自注册表，禁捏造）
4 填充    参数绑定 {{路径}}，引用必须在 ScopeIndex 可见集内（题一 L2 卡点）
5 校验    完整过 L1/L2/L3（NL 草稿与手画同等待遇，无豁免）
6 确认    草稿进编辑器，人审改后保存为新版本 —— NL 永远不到「发布」
```

步骤 2（技能推荐 = retrieve/rank 选原子）与步骤 3（自动组合 = plan/connect 出控制流）是两个独立考点。

**Atlas 现状**：✅ 步骤 1/2（弱）/3/5/6 已有——`src/atlas/llm/nl_generate.py` LLM 优先、退款模板兜底，prompt 强制「工具取自 GET /api/adapters 禁捏造」+ 枚举九类节点；模板库 5 个内置模板可复用；产物是载入画布的草稿。缺口：步骤 2 无真正 rank（固定兜底）；步骤 4 不做作用域合法性检查（靠保存时 422 暴露）；无技能库（skills 空包）。

### 2.2.2 退款协同（dispatch / join / escalate 三模式）

```
webhook 退款单
  → condition: 金额/类型分流
     ├─ 仅退款·低额·信用好 → 客服 Bot 核验 → shop.execute_refund（financial 权限）
     ├─ 退货退款           → 并行 dispatch:
     │                        客服 Bot: 核验订单/沟通
     │                        物流 Bot: 确认签收入库
     │                      join(两者 completed) → human_approval(>阈值) → 放款
     └─ 争议/高情绪/低置信  → escalate: 人工客服主管（审批卡片）
```

- **dispatch**：协调者发任务信封给单 Bot，状态机 `pending→accepted→running→done/failed/timeout`；
- **join**：parallel 网关等两 Bot 任务都 completed（现有 all_completed 语义承载，物流签收入库结果经 `{{parallel-x.result...}}` 进放款条件）；「退货退款必须签收后放款」是跨 Bot 时序/数据依赖，复用题一作用域；
- **escalate**：升级是带审批卡片的特殊 human_approval，不是自由 @人。

### 2.2.3 审批与交互模板（双向；渠道=渲染降级）

```
Web 嵌入  : 完整表单(订单明细/金额/历史对话/意见框/批准·拒绝按钮)
IM 卡片   : 受限交互组件(文本+两按钮，详情跳链接)
邮件      : 只读摘要 + 带 token 的批准/拒绝链接
回调      : POST /approvals/{token}/decision（首决生效，重复 409）
```

卡片模板是**双向**的：input bindings 变量渲染展示；actions 的 output schema 把按钮/表单回写成结构化决策流回流程。

**Atlas 现状**：✅ human_approval v1：ApprovalBroker（uuid token + Event，首决生效 409）、超时 10–3600s 默认 reject、REST 决策、SSE 真流式、run inputs 预置（回放秒过）。❌ 无卡片模板系统（summary 是字符串，无 bindings/output schema/渠道渲染器）；无 IM/邮件真实渠道（message 进程内记录，D24）；无持久化中断/通知/动态审批人（D20）。

### 2.2.4 发布流

```
保存 → 编译校验全绿 → 录制用例批量回放(门禁) → 新版本 vN(不可变)
     → 灰度(内部/低金额桶/百分比) → 指标门控观察期 → 全量
                                            └─ 越阈 → 自动回滚 vN-1
```

## 2.3 核心 Schema（均为提案，未生效契约）

### 2.3.1 多 Bot 任务信封

```json
{
  "taskId": "task-uuid",
  "runId": "run-123",
  "idempotencyKey": "refund-12345|approve|v3",
  "traceId": "run-123",
  "graphVersion": "refund-flow@7",
  "type": "refund.verify_order | logistics.check_receipt",
  "assignee": "bot.customer | bot.logistics",
  "payload": { "orderId": "12345", "amount": 128,
               "refs": { "receipt": "{{logistics-1.result.signed}}" } },
  "deadlineMs": 30000,
  "state": "pending|accepted|running|done|failed|timeout",
  "result": {},
  "attempt": 1
}
```

### 2.3.2 审批卡片 / 交互模板

```json
{
  "$id": "card/refund_approval/v1",
  "channels": ["web", "im", "email"],
  "sections": [
    { "type": "fields",
      "bindings": [
        { "label": "订单号", "value": "{{trigger.context.payload.order_id}}" },
        { "label": "退款金额", "value": "{{tool-1.result.amount}}" },
        { "label": "物流签收", "value": "{{logistics-1.result.signed}}" }
      ]},
    { "type": "textarea", "name": "comment", "required": false }
  ],
  "actions": [
    { "id": "approve", "label": "批准", "style": "primary",
      "output": { "decision": "approved", "comment": "{{form.comment}}" },
      "channels": { "email": { "render": "link" } } },
    { "id": "reject", "label": "拒绝", "style": "danger",
      "output": { "decision": "rejected", "comment": "{{form.comment}}" } }
  ],
  "fallback": { "im": { "detailUrl": "/a/{token}" },
                "email": { "timeoutHint": true } }
}
```

`bindings` 里的 `{{}}` 受题一 ScopeIndex 同一套规则校验——**卡片模板就是题一内核的第六类实体**；actions.output 回写结果成为节点产出。

### 2.3.3 部署与灰度描述

```json
{
  "graphId": "refund-flow", "version": 7,
  "ingress": [
    { "channel": "webhook", "path": "/ingress/refund", "secretRef": "secret.webhook" },
    { "channel": "im", "adapters": ["dingtalk", "wecom"] },
    { "channel": "embed", "allowedOrigins": ["https://*.example.com"] }
  ],
  "rollout": {
    "strategy": "progressive",
    "rules": [
      { "to": "internal", "when": "tenant in allowlist" },
      { "to": "lowValueBucket", "when": "payload.amount <= 200", "percent": 100 },
      { "to": "canary", "percent": 5 },
      { "to": "full" }
    ],
    "gate": {
      "observeMinutes": 60,
      "autoRollback": true,
      "metrics": [
        { "id": "run_error_rate",         "threshold": 0.02 },
        { "id": "manual_escalation_rate", "threshold": 0.10, "compareWith": "v6" },
        { "id": "refund_amount_diff_rate","threshold": 0.005 }
      ]
    },
    "inFlightPolicy": "pin-to-version"
  }
}
```

### 2.3.4 Trace 事件（现有 SSE 事件超集）

```json
{ "traceId": "run-123", "spanId": "task-2", "parentSpanId": "parallel-1",
  "graphVersion": "refund-flow@7", "kind": "task_dispatch|task_done|approval|tool",
  "actor": "bot.logistics", "ts": "...", "status": "done",
  "attrs": { "orderId": "12345" } }
```

### Atlas 现状与缺口

- ⚠️ 2.3.1 全无（进程内事件总线是发布订阅，不是任务状态机）。
- ⚠️ 2.3.2「动作=决策枚举 + token 端点」在 ApprovalBroker 有运行时对应物，**无可编辑模板实体**。
- ❌ 2.3.3/2.3.4 无；现有 trace 是节点级文本行 + SSE node_start/node_end，有 runId 但不跨服务、无 span 父子（parallel/子图内部不外露）。
- ✅ monitoring v1 已有 RunRecord/指标/告警规则（`src/atlas/monitoring/`，规则 run_error/node_failed/consecutive_failures/failure_rate）——gate 指标采集有雏形；告警目前是展示+状态流（open/acknowledged/resolved），不触发回滚。

## 2.4 冲突解决策略（三层分开）

| 层 | 场景 | 策略 |
|---|---|---|
| L1 投递冲突 | 超时重试致同一任务执行两次 | 幂等键 `资源|动作|版本`；Bot 侧 idempotencyKey 去重，重复请求返回**首结果**；至少一次投递 + 幂等消费 |
| L2 写入冲突 | 两 Bot 并发改同一退款单 | 资源带版本号，**乐观锁 CAS**（`UPDATE ... WHERE version=n`）；金融写只走 shop 单一通道且需 financial 权限；失败方重读发现已终态则放弃 |
| L3 决策冲突 | 客服 Bot 说可退、物流 Bot 说未签收 | 硬约束写死图里（「未签收→禁止放款」condition）；规则未覆盖**不做 Agent 投票，升级 human_approval**；双方结论进卡片供人判断 |

超时三级：任务级 deadline（2.3.1）+ 节点级 retry.timeout + 审批级 timeoutSeconds（默认 reject）；超时动作链：重试（幂等键不变）→ 升级分支 → fail-safe（run 仍 completed，下游 condition 判 status，与现有 parallel/human fail-safe 哲学一致）。

### Atlas 现状与缺口

- ✅ 幂等/首决思想两处：审批首决 409、database/query 标 read·幂等与 write 权限分离；金融权限门 shop.execute_refund(financial)。
- ❌ 无任务级幂等键、无乐观锁（GraphStore/业务状态进程内，无并发写场景）；L3 目前靠 condition 硬规则（金额分流），无升级型仲裁实体。

## 2.5 灰度与回滚机制

1. **版本不可变**：发布产物 `graphId@version`，Graph + 绑定的模板/技能引用一起冻结（subgraph 钉版，D21 已识别）；编辑产生新版本，老版本永不原地改。
2. **灰度维度**：租户 allowlist（内部先行）→ **业务桶**（低金额自动退款先灰，高金额/退货退款强制人工且不参与早期灰度）→ 百分比（订单 id 哈希稳定分桶，同一单不跨版本跳动）。
3. **Router**：TriggerEvent 入口查路由表定版本；**在途实例钉版**（pin-to-version）——回滚只影响新流量，跑到一半的流程用启动时版本跑完（持久化中断恢复后仍读老版本）。
4. **门控自动回滚**：观察窗内 2.3.3 指标任一越阈 → Router 切回 vN-1 → 告警（复用 alerts 通道）→ 新版本运行用 recording 回放定位（冻结快照在新版本重放，即 D26 跨版本对比）。
5. **回滚安全前提**：回滚不改外部世界已发生的事实（已退款不可逆），故 gate 必须含业务结果指标（退款金额差异率），把问题挡在放量前；这是金融场景灰度必须按业务桶而非纯百分比的原因。

### 可观测小结

| 能力 | 方案 | Atlas 现状 |
|---|---|---|
| 链路追踪 | traceId 从入站贯穿到 Bot 任务（span 父子），parallel/subgraph 内部建 span、可选不外泄 | ⚠️ 有 runId+SSE 节点事件+文本 trace，无 span 模型/跨服务传播 |
| 指标 | 系统（时延/失败率/超时率）+ 业务（自动退款率/升级率/金额差异）分列 | ✅ RunRecord+metrics v1（系统侧），业务指标无 |
| 告警 | 规则触发→展示→（未来）挂回滚门控 | ✅ 四规则+状态流，不驱动动作 |
| 回放 | 发布前批量回放门禁 + 事故后新版本重放 | ✅ 单例录制/快照/归一化比对；❌ 批量/跨版本（D26） |

### 题二总缺口与建议落地顺序

最大缺口集中区：Graph 版本化（现在 id 即最新、进程内）、Router、灰度规则引擎、在途钉版（依赖 11 S1 + D19/D20 中断模型）、指标→回滚控制回路、CD（D10b）、多租户（D7；iam v1 有 principals/sessions 无租户隔离）。

建议顺序：

1. 持久化中断（D19/D20）；
2. Graph 版本化 + 子图钉版（D21）；
3. 批量回放门禁（D26）；
4. 灰度 Router / 自动回滚（新立项）；
5. 多渠道真实投递（D24）与多租户（D7）并行。

## 3. 跨两题的落地总纲

最值得先做的一件事：**给适配器 Capability 补 `inputSchema/outputSchema` 声明**——同时解掉题一最大缺口（工具表单化 + 拓扑作用域索引 + 模板引用校验）与题二生成管线的参数填充合法性。

本文件提案落码前的治理动作：

1. 新增前端依赖（AJV 等）与新运行时组件（任务总线/Router）→ 10 文档 §4 记 ADR；
2. 立项与排期 → 08 任务迭代计划；
3. Schema 由提案转权威 → 03 契约索引 + 04/05/06 正文同步；
4. 缓做项沿用 14 登记表触发条件模式（版本化/灰度/多 Bot 等可新登记触发条件）。
