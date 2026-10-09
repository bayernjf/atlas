# 打包 AI：D29 SchemaRegistry 第四实体接入 — 可行性评估（docs-only）

> 号段：无新增用例（本件为评估，不落码）｜承接 docs/14 D29 剩余「SchemaRegistry 六类实体通用化」｜2026-10-09
> 状态：⬜ **评估结论＝暂不落码，转回缓做**；第四实体（部署）的配置面迁移阻断于「自定义控件扩展契约」（D29 明确缓做的余部），技能/记忆无现存配置面、触发条件未到。

## 1. 背景

D29「Schema 驱动配置内核通用化」的目标是把六类实体（**节点 / 工具 / 技能 / 记忆 / 部署 / 卡片**）的配置面统一到 `frontend/src/lib/forms/` 内核（MetaSchema 声明 → `resolveWidget` 渲染 → L1/L2 校验 → 结构化提交）。

经多批推进，forms 内核当前已接入三类 schema 来源。`resolveWidget.ts:17` 定义：

```ts
export type SchemaSource = 'node' | 'tool' | 'card'
```

本评估回答：**第四类实体现在能否干净接入？**

## 2. 六类实体配置面现状（实证）

| 实体 | forms 接入状态 | 证据 |
|---|---|---|
| 节点 config | ✅ 部分接入 | `PropertyPanel.tsx:170` trigger 走 `NodeConfigForm`；`:180` intent_recognition/info_extraction/content_generation 走 `StructuredConfig`；其余 7 类节点（ai_decision/condition/loop/parallel/wait/subgraph/human_approval）仍走手写 Config 组件 |
| 工具 params | ✅ 已接入 | M3：tool_call 的 params 经工具 `input_schema`（`toolSchemas.ts`，第二来源）渲染 |
| 卡片 | ✅ 已接入 | M8：审批卡片经 `FormRenderer source="card"`（第三来源）渲染，双向回写 |
| 技能 | ❌ 无配置面 | `frontend/src` 全量检索 `skill/Skill`（components/pages/lib）**零相关文件** |
| 记忆 | ❌ 无配置面 | 无 `memory_config/MemoryConfig` 相关 UI；记忆页目前为列表/导入，无策略配置表单（D35 触发条件「运营体级记忆配置进入 UI」未到） |
| 部署 | ⚠️ 配置面存在但高度定制 | `RolloutModal.tsx` 手写灰度配置表单（详见 §3） |

## 3. 第四实体候选逐一分析

### 3.1 技能 —— 不成立

前端无任何技能定义/配置 UI，后端亦无对应配置端点。接入 forms 需先从零设计技能实体、配置模型与提交 API，属新功能立项而非表单迁移；触发条件（技能系统进入产品范围）未到。

### 3.2 记忆 —— 触发条件未到

D35 明确「运营体级记忆配置策略表单」的触发条件是「运营体级记忆配置进入 UI」，当前记忆页只有事实/知识列表与导入，没有策略配置面。在没有真实配置需求时预先造一套配置表单，违背「不为假想需求写代码」的纪律。

### 3.3 部署 —— 配置面存在，但当前 forms 内核表达不了

部署配置面即灰度发布 `RolloutModal.tsx`，其数据形状（`apiClient.ts:1811`）为：

```ts
type RolloutConfig = {
  rules: RolloutRule[]   // 异构，discriminator 为字段 to
  gate: GateConfig
}
type RolloutRule = InternalRule | BucketRule | CanaryRule | FullRule
//  to='internal'        → { tenants: string[] }
//  to='lowValueBucket'  → { field?, op?, value: number, percent? }
//  to='canary'          → { percent: number }
//  to='full'            → { }（无额外字段）
type GateConfig = {
  observeMinutes: number; autoRollback: boolean; minSamples: number
  metrics: { id: GateMetricId; threshold: number; compareWith?; minSamples? }[]
}
```

该表单有三处当前 forms 内核无法干净表达的形态：

1. **异构规则数组（discriminator）**：`rules` 数组每一项按 `to` 字段呈现完全不同的字段集。ZX 已落地的数组能力是**同构 `items`**（增删同型行），不支持按判别字段切换子 schema（JSON Schema 的 `oneOf`/discriminator）。
2. **「可选规则」开关不是普通数组增删**：UI 用一个 `Switch` 控制某条规则（按 `to` 唯一）是否存在，启用后再展开其参数（tenants 逗号输入、value/percent 数值）。这是「固定判别集合中某成员的存在性 + 成员参数」编辑，需要专门控件。
3. **固定行的动态指标阈值表**：门禁指标表的行是**固定**的 `GATE_METRIC_SPECS`（run_error_rate/manual_escalation_rate/refund_amount_diff_rate），每行一个阈值输入，并用 included/excluded 表示 `gate.metrics` 数组是否包含该 id。这是「固定集合成员的可空阈值」编辑，既非同构数组、也非简单标量。

## 4. 阻断点：自定义控件扩展契约

要让 forms 内核干净表达 §3.3 的三种形态，需先具备以下能力——它们正是 docs/14 D29 行明确标注「仍缓做」的余部：

- **自定义控件扩展契约**：`registerWidget` 扩展点 M3 已预留但未明确 per-field `x-widget` 的消费范围、props 契约与降级序；需定义业务自定义控件（规则编辑器、tenants 编辑器、固定行阈值表）如何注册与渲染。
- **异构/判别数组支持**：MetaSchema 需支持 `oneOf` + discriminator（或等价的最小声明子集），formTree 需能按判别字段选择子 schema 与对应控件。
- **可选成员/固定集合编辑控件**：表达「固定判别集合中成员的存在性 + 参数」与「固定行的可空值」两类编辑。

在该契约未立前硬迁部署，只有两种结果，均不可接受：
1. 为迁就表单而**临时扩张 forms 内核**（在没有契约的情况下塞入 oneOf/自定义控件），破坏内核演进纪律；
2. 保留一个**半迁移、仍夹带大量手写分支**的 FormRenderer，制造「已 schema 化」的假象。

故本批不硬迁。

## 5. 解锁路径（最小前置）与重开判据

**推荐的最小解锁顺序（每步独立立项）：**

1. **docs-only 先立「自定义控件扩展契约」**：明确 `registerWidget` 的注册形状、`x-widget` 声明与消费范围、props/校验/降级契约；✅ **2026-10-10 已立并落码收口（docs/118，第 1 步闭环）**。
2. ✅ **2026-10-10 已落码收口（docs/119，`071aecc` feat→`b1c74a0` test＋docs 收口）**：oneOf/discriminator 渲染最小子集——const 判别键匹配分支子树（`oneOfDiscriminant`＋formTree array 分支逐元素判别、未知判别值降级 json）、新增行取首个分支默认对象（`defaultValueFor` 判别键=const 组装）、判别键只读（SelectWidget const 单选项 disabled）；MetaSchema 封闭四 x-* 不新增；object 级顶层 oneOf／嵌套 oneOf／判别键切换／L1 动态诊断仍非目标。
3. 契约稳定后，再把 `RolloutModal` 的规则区与门禁指标表分两片迁入（先 gate 标量与指标表，再异构规则数组），每片一原子。✅ **2026-10-10 第一片（gate 标量与指标表）已落码收口**（两原子 `95041d4` feat→`ee928ac` test＋本 docs 收口）：gate 配置面迁入 forms 内核——`buildGateSchema()`（四字段：observeMinutes/minSamples/autoRollback 走内置 number/switch、metrics 数组声明 `x-widget: deploy-gate-metrics`）＋部署业务控件 `GateMetricsWidget`（固定三行 `GATE_METRIC_SPECS` 可空阈值编辑，复用自 `setGateMetric` 提炼的 `upsertGateMetric` 纯函数）＋独立 `buildDeployRegistry()`（内置九件＋部署组，与工具/节点表单隔离、互不污染）；RolloutModal gate 区改挂 `FormRenderer source="node"`，后端 `GateConfig` 形状不变、纯前端渲染层替换。门：前端 vitest **943 passed／2 skipped**、oxlint 0/0、tsc 0、build 过；真实浏览器冒烟（admin-a）确认观察窗 60／最少样本 3／自动回滚开关／三行指标阈值 0.02·0.10＋included Tag／metricsHint 全部渲染。**两处 UI 微差照实**：观察窗字段不再带「分钟」后缀（NumberWidget 无 suffix 槽，单位改放 label 文案）、指标表上方新增「门控指标」字段 label（原手写 UI 无）。⬜ **第二片（异构规则数组＝internal/lowValueBucket/canary 手写区）仍缓做**，依赖第 2 步 oneOf/discriminator，触发未到。

**重开判据（满足其一即应重评本件）：**

- 出现真实的部署/灰度配置 schema 化需求，且 §5 第 1 步契约已立；
- 另一个实体（技能/记忆）先于部署进入配置表单开发，可作为第四实体按已立契约接入；
- forms 内核因其他需求已先行获得 oneOf/discriminator 与自定义控件能力，部署迁移的增量成本随之降低。

## 6. 结论

- **第四实体现在不接入 forms 内核**：技能/记忆无配置面、触发未到；部署配置面的迁移阻断于「自定义控件扩展契约 + 异构数组支持」（D29 明确缓做）。
- 本件以 docs-only 登记现状、阻断点、最小前置与重开判据，**不写实现、不扩张内核、不制造半成品**。
- docs/14 D29 行据本件追加注记（第四实体评估结论），docs/08 §八候选池同步；D29 整体维持缓做。

## 7. 收口注记

- 本件为纯文档评估，**无新增用例、无代码改动、无迁移、无依赖变更**。
- 关联：D29 形状脉络见 docs/105（ZW）、docs/106（ZX）、docs/103（ZT）；MCP 加面（D49）的 docs-only 预备契约另见 docs/117；D21/D32 版本钉版整体评估见 docs/116。
- **2026-10-10 追加（部署 gate 片落码收口）**：docs/115 §5 解锁路径第 3 步第一片「gate 标量与指标表迁 forms」已落码收口（`95041d4`＋`ee928ac`＋本 docs 收口原子；前端 vitest 943/2、oxlint 0/0、tsc 0、build 过、冒烟通过），部署配置面自 node/tool/card 之后成为第四实体接入 forms 的**第一片实际落地**；第 2 步异构数组与第 3 步第二片仍缓做、D29 整体不解除。
