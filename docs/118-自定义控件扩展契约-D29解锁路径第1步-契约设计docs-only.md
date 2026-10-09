# 自定义控件扩展契约（D29 解锁路径第 1 步）— 契约设计（docs-only）

> 号段：无新增用例（本件为契约设计，不落码）｜承接 docs/14 D29「自定义控件扩展契约」余部与 docs/115 §5 解锁路径第 1 步｜2026-10-10
> 状态：✅ **契约已立且已落码收口（2026-10-10，同日落码）**；本文是「自定义控件扩展契约」的**形状权威**——定义注册形状、`x-widget` 声明与消费范围、props/校验/降级契约。落码详情与偏差见 §5 落码执行注记与 §7 收口注记；§3 契约定义本身不变，作为后续第 2/3 步与新增业务控件的形状基准。

## 1. 背景与目的

docs/115（D29 第四实体接入可行性评估）结论：**部署（RolloutModal）配置面的迁移阻断于「自定义控件扩展契约＋oneOf/discriminator 异构数组」**——当前 forms 内核只有同构数组（`items`）＋八内置控件，表达不了异构规则数组（discriminator `to`）、可选规则 Switch、固定行门禁指标阈值表三形态。docs/115 §5 给出的最小解锁顺序：

1. **docs-only 先立「自定义控件扩展契约」**（本文）；
2. 再补异构数组（oneOf/discriminator 最小子集）的 MetaSchema 与 formTree 支持；
3. 契约稳定后，把 RolloutModal 规则区与门禁指标表分两片迁入 forms。

本文完成第 1 步：把 `registerWidget` 从「M3 预留、靠各批临时约定使用」升级为**正式契约**，让第三方的业务控件（规则编辑器、tenants 编辑器、固定行阈值表等）有明确的注册、声明、消费、校验与降级规则，为第 2/3 步铺地基。

## 2. 现状实证（落契约前的事实基线）

### 2.1 注册机制

- `WidgetRegistry`（`forms/registry.ts`）：纯 map 逻辑，`register(name, component)`／`has(name)`／`get(name)`／`names()`；`get()` 对未注册名 **throw** `WidgetRegistry：未注册控件 "${name}"`。
- `buildDefaultRegistry()`（`forms/defaultRegistry.ts`）：注册内置九件；导出 **`registerWidget(name, component)`** 扩展点（M3 预留，直接写 `widgetRegistry`）。
- **节点业务控件已实际注册 6 个**（`nodeRegistry.ts:23-27`，独立于工具表单的全局 defaultRegistry）：`target-select`（M4）、`saved-graph-select`（M4 批2⑨）、`card-select`（M8）、`cron-input`（docs/68 打包 N）、`timezone-input`（打包 ZL）。

### 2.2 声明与消费

- `resolveWidget.ts` 选择序：① 非 tool 源且 schema 带非空字符串 `x-widget` → 按该名解析控件；② 类型结构默认（object→group、enum→select、bool→switch、number/number、array→array 或 json、string→variable-input/text、带 additionalProperties 的 object→keyvalue）；③ 白名单外一律降级 `json`。
- **`x-widget` 消费范围**：`node` 与 `card` 源认；**`tool` 源忽略一切 `x-*`**（工具 Capability schema 拒绝业务控件，遇之走类型默认/降级）。
- 类型约束：`WidgetName = (typeof BUILTIN_WIDGETS)[number]`（`types.ts`，内置九件字面量联合）。业务控件名（`target-select` 等）**不在该 union 内**，`resolveWidget` 里 `schema['x-widget'] as WidgetName` 是类型断言绕过——即**现有自定义控件名没有类型层面的注册登记**，全靠注册表运行时命中。

### 2.3 内置控件与 props 基线

- 内置九件：`text/number/select/radio/textarea/switch/json/expression/variable-input`（`BUILTIN_WIDGETS`）。
- `WidgetProps` 基线（`types.ts`）：`value`／`onChange`／`schema`／`scope?`／`nodeId?`／`diagnostics?`／`markers?`／`placeholder?`／`rows?`／`optionLabels?`／`timeZone?`；`WidgetComponent = (props: WidgetProps) => ReactNode`。
- 既有业务控件对基线的消费模式：`target-select` 消费 `scope.listNodeTargets`；`variable-input` 消费 `scope.listPathsAt(nodeId)`；`cron-input` 消费同 config 的 `timeZone` 字段（`timeZone` prop 由 FormRenderer 注入）；`optionLabels` 供 select/radio 的 enum 中文文案。**控件特定数据全部经 WidgetProps 基线＋schema 读取，无额外运行时注入通道**。

## 3. 契约定义

### 3.1 注册形状（registerWidget）

- 签名维持 `registerWidget(name: string, component: WidgetComponent)`，语义＝**往全局 defaultRegistry 注册一个业务控件**（`node`/`card` 源共享）。
- **名称规则（新增硬约束）**：
  - 不得与内置九件名冲突（`text/number/select/radio/textarea/switch/json/expression/variable-input`）；
  - 建议命名空间化：`<domain>-<semantic>`（如 `gate-metric-table`、`rollout-rule-editor`），与既有 `target-select`/`cron-input` 风格一致；
  - 同批内重复注册同名＝**覆盖**（现状 map 语义保留）；跨批撞名需在契约测试中显式断言。
- **组件签名规则（新增校验）**：注册的组件必须是可调用函数、入参为 `WidgetProps`、返回 `ReactNode`。静态校验在落码批实现（`registerWidget` 包装内加开发期 `typeof` 检查，不引额外依赖）。

### 3.2 x-widget 声明与消费范围

- **声明**：字段 schema 片段顶层带 `x-widget: <RegisteredWidgetName>`（字符串）。位置与 `resolveWidget` 现状一致——第一优先级；`tool` 源**忽略一切 `x-*`**（维持现状，工具 Capability schema 不注册业务控件、遇之走类型默认）。
- **类型登记（落码批要做）**：`WidgetName` 从「内置九件字面量」拓宽为「内置九件 ∪ 已注册业务控件名」。实现取向二选一（落码时定，本文不代选）：
  - (a) 常量表维护：`types.ts` 增 `BUSINESS_WIDGETS` 常量联合，`WidgetName = BUILTIN | BUSINESS`，`nodeRegistry` 注册处与常量表同源（注册列表直接来自常量表，杜绝漂移）；
  - (b) 派生宽类型：`WidgetName = (typeof BUILTIN_WIDGETS)[number] | (string & {})`，由注册表运行时兜底。倾向 (a)（可静态校验、可 grep、注册与声明同源），但 (a) 要求注册时点集中在类型定义处，与 `nodeRegistry` 现有惰性注册（图编辑页装配时注册）需对齐——落码批据实取舍。
- **resolveWidget 不再断言绕过**：改经 `WidgetName` union 校验；union 外字符串在编译期即报错（schema 写错控件名当场可见）。

### 3.3 props 契约（自定义控件能拿到什么）

- **全部输入来自 WidgetProps 基线**，不新增运行时注入通道（维持现状消费模式）：`value`（当前字段值）、`onChange(next)`（受控更新整个字段值）、`schema`（字段 schema 片段，控件可读 `x-*` 扩展与 `enum`/`const` 等）、`scope`（拓扑作用域：`listPathsAt` 必选、`listNodeTargets` 可选）、`nodeId`、`diagnostics`/`markers`（M2 诊断与 token 高亮）、`placeholder`/`rows`/`optionLabels`/`timeZone`（FormRenderer 按字段特征注入）。
- **控件特定的辅助数据**经 schema 的 `x-*` 扩展字段声明、控件自读：如 `x-options`（下拉候选项）、`x-metric-specs`（固定行阈值表的三指标规格）、`x-rule-kind`（异构规则编辑器的判别集合）。这些扩展字段由 MetaSchema 透传，不额外建 props 通道。
- **受控单一**：自定义控件必须把用户编辑结果经 `onChange(next)` 交回整字段值；不允许自持游离状态（除 json 控件既有的未解析草稿先例）。FormRenderer 负责不可变合并。

### 3.4 校验与降级契约

- **开发期**：`registerWidget` 静态校验（函数组件、名称合规）；`WidgetRegistry.get()` 对未注册名的 throw 语义**保留**（开发期 fail-fast，帮助发现 schema 引用了未注册控件）。
- **运行期降级（新增，落码批实现）**：`FormRenderer` 分发控件前先 `registry.has(name)`；未命中 → 不 throw、渲染**降级 json 控件**并在 console.warn 记一条「未注册控件 `<name>`，已降级 json」。
- **tool 源**：不认 `x-widget`（现状），因此不存在"工具表单引用了未注册业务控件"的降级场景——工具表单遇 `x-widget` 直接按类型默认/降级走。
- **契约测试（落码批）**：
  - 未注册名 → 运行期降级 json、不 throw；
  - 同名覆盖语义（后注册者生效）；
  - 内置名不可被业务注册覆盖（或覆盖即红——二选一，落码时定，倾向"拒绝注册内置名"）；
  - `tool` 源忽略 `x-widget`（既有用例延续）。

## 4. 范围与非目标

**本契约做**：注册形状、名称规则、组件签名校验、WidgetName 类型登记取向、props 基线边界、降级契约、测试清单。

**明确不做（后续步骤，不在本批）**：
- 不实现 oneOf/discriminator 异构数组支持（docs/115 解锁路径第 2 步，独立立项）；
- 不迁移 RolloutModal（第 3 步，分两片：先 gate 标量与指标表、再异构规则数组）；
- 不写任何新的业务控件（本契约只立规则，`gate-metric-table`/`rollout-rule-editor` 等控件留到第 3 步按本契约实现）；
- 不解除 D29（六类实体通用化仍缓做；本文只解锁"自定义控件"这一个前置）。

## 5. 落码批范围（契约通过后）

按 AGENTS.md 原子拆分、英文 message、无 co-author、默认不 push。预计原子序：

1. `feat(frontend)`：`types.ts` WidgetName 拓宽（按 3.2 取向落地）＋`registerWidget` 名称/签名静态校验＋FormRenderer `has()` 降级分发（json＋warn）；
2. `test(frontend)`：3.4 契约测试清单落地（vitest）；
3. `docs` 收口：docs/14 D29 行注记（自定义控件扩展契约已立）、docs/08 收口块、CHANGELOG、handoff（Active #142）。

三道门（vitest/oxlint/tsc/build）＋守护门（handoff/migration）在收口前复跑。

### 5.1 落码执行注记（2026-10-10，同日落码）

已按上述原子序执行，两原子提交（`5f2bac5` feat、`1a09875` test；docs 收口原子随后）。

- **3.2 取向**：选 (a) 常量表——`types.ts` 增 `BUSINESS_WIDGETS`（由既有五个业务控件名常量派生）与 `BusinessWidgetName`，`WidgetName = BUILTIN ∪ BUSINESS`；nodeRegistry 改为经 `NODE_WIDGET_COMPONENTS` 组件表遍历 `BUSINESS_WIDGETS` 注册（注册与声明同源、编译期强制覆盖）。`isWidgetName` 运行时守卫导出，resolveWidget 与测试共用。
- **FormRenderer `has()` 降级分发**：落码实证发现 `widgetComponent()`（defaultRegistry 现有降级通道）已具 `has?get:get('json')` 降级，仅缺 warn——本批在 `widgetComponent` 补 `console.warn`（未注册名且非 json 时），FormRenderer 分发处零改动；`registry.get()` 对未注册名 throw 语义保留。
- **既有测试适配**：`widgets.test.ts`「模块级默认表与扩展点注册到同一实例」原以覆盖内置名 `text` 验证，与新契约「禁内置名」冲突——改为业务名 `widgets-ext-same-instance` 注册、断言模块级单例持有且全新默认表不含（意图不变）。
- **契约测试**：新增 `customWidgetContract.test.ts` 17 例，覆盖 §3.4 清单全部四件＋§3.2 守卫与同源守护。
- **门（实跑）**：前端全量 **932 passed／2 skipped**（基线 915 → +17）、oxlint 0/0、tsc 0、build 过；守护门 8 passed。零新依赖/零迁移/无 ADR。

## 6. 重开判据（何时真做第 2/3 步）

- 第 2 步（异构数组）：第 3 步立项前必须完成——部署迁移的规则数组需要 `oneOf`/discriminator 表达；或出现其他异构数组配置需求。
- 第 3 步（部署迁移）：本契约落码并稳定后，按 docs/115 §5 分两片迁 RolloutModal（先 gate 标量＋指标表，再异构规则数组），每片一原子、一收口。
- D29 整体解除判据：六类实体（节点/工具/技能/记忆/部署/卡片）中至少五类走同一 forms 内核且无手写配置面残留，且 docs/115 重开判据满足。

## 7. 收口注记

- 本件为纯契约设计：**无代码改动、无测试、无迁移、无依赖变更**。
- 关联：docs/115（第四实体评估＝本契约的来源）；docs/14 D29（本契约登记为"契约已立、落码待批"）；docs/110（打包 AC，评估面先例）；docs/106（打包 ZX，声明面结构化先例）。

### 7.1 落码收口注记（2026-10-10）

- **落码已完成**：§3 契约全部落地（§5.1），D29 解锁路径第 1 步闭环；第 2/3 步（异构数组、部署迁移）仍缓做，重开判据见 §6。
- **落码偏差登记**：无契约级偏差——三处实现细节按 §3 授权范围内取舍并记于 §5.1（取向 (a)、降级落地在 `widgetComponent` 而非 FormRenderer 新增分支、既有测试适配）。
- **门**：前端 vitest 932/2（+17）、oxlint 0/0、tsc 0、build 过；守护门 8 passed。docs 收口原子＝本文件状态更新＋docs/14 D29 行＋docs/08 收口块＋CHANGELOG＋handoff（Active #142 ✅）。
- 关联补记：docs/13 打包批小节（若按批记）未新增——本批按契约批登记，不重复记 docs/13。
