# RolloutModal 规则区迁 forms＝D29 解锁路径第 3 步第二片（契约设计，docs-only）

- 状态：⬜ **契约立项（docs-only）**——落码批待用户批复「开搞」后执行（docs/08 §八 立项块同步）。
- 承接：docs/115 §5 第 3 步第二片（「异构规则数组＝internal/lowValueBucket/canary 手写区」仍缓做，依赖第 2 步 oneOf/discriminator）；docs/119 §7.1（第 2 步异构数组渲染已落码收口，判别键切换交互按 §4 非目标**留给本片单独设计**）；docs/118（第 1 步自定义控件契约）；docs/115 §5 第 3 步第一片（gate 配置面迁 forms 已收口）。
- 关联：docs/119（判别渲染）；docs/115（解锁路径）；docs/14 D29 行；docs/08 立项块。
- 日期：2026-10-10。

## 1. 背景与目标

部署配置面（`RolloutModal.tsx` 灰度发布）在 gate 片（第 3 步第一片）迁入 forms 内核后，规则区仍是**手写表单**：

- `RolloutModal.tsx:261-370`：三行固定开关式——internal（Switch＋逗号分隔 Input）、lowValueBucket（Switch＋InputNumber×2）、canary（Switch＋InputNumber×1），经 `withRule`/`findRule`（`release.ts:103-124`）维护 `rules` 数组。
- `apiClient.ts:1780-1790` 形状：`RolloutRule = InternalRule | BucketRule | CanaryRule | FullRule`，判别键 `to`（`internal`／`lowValueBucket`／`canary`／`full`）。
- `routing/store.py:137-160` 语义：canary 期 `full` 段不生效（求值时剔除）；`withRule` 保证**每段至多一条、按 `ROLLOUT_RULE_ORDER`（internal→lowValueBucket→canary→full）固定序**（`release.ts:32/103-112`）。

**目标**：把规则区从手写三行开关替换为 forms 内核渲染的**判别异构数组**（消费 docs/119 能力），并交付 docs/119 §4 明确留给本片的**判别键切换交互**；规则语义（段唯一＋固定序）不漂移；`rulesLocked` 禁用语义保持。纯前端渲染层替换，后端零改动。

## 2. 形状契约

### 2.1 规则数组 schema（新 `release/rolloutRulesSchema.ts`）

```ts
buildRolloutRulesSchema(): MetaSchema
// {
//   type: 'array',
//   items: { oneOf: [internalBranch, bucketBranch, canaryBranch] },
//   description?: …
// }
```

- **三分支**（编辑面不含 `full`——后端 canary 期求值剔除 full、promote 后才全量，`store.py:152-157`；现状手写 UI 亦无 full 行，行为不变）：
  - `internal`：`to: { const: 'internal' }`；`tenants: { type: 'array', items: { type: 'string' } }`；
  - `lowValueBucket`：`to: { const: 'lowValueBucket' }`；`field: { type: 'string' }`、`op: { const: '<=' }`（现状 UI 固定 `<=`，可编辑字段仅 value/percent，`field`/`op` 保留形状不编辑）、`value: { type: 'number' }`、`percent: { type: 'number', minimum: 1, maximum: 100 }`；
  - `canary`：`to: { const: 'canary' }`；`percent: { type: 'number', minimum: 1, maximum: 100 }`。
- 判别协议沿用 docs/119 §2.1：各分支 object 且恰好一个键带 const、判别键同名（`to`）、const 值唯一 ⇒ `oneOfDiscriminant` 成立。
- MetaSchema 封闭四 `x-*` 不新增；无 `minItems/maxItems`（规则数组可空＝未配置，与后端一致）。

### 2.2 段唯一＋固定序规约（新纯函数，同文件）

```ts
normalizeRolloutRules(rules: RolloutRule[]): RolloutRule[]
// 同 to 只保留最后一条；按 ROLLOUT_RULE_ORDER（release.ts:32，含 full）重排；
// 未知 to 段剔除（防御）；空数组保留空。与 withRule 语义同族、可交换（对合法输入结果一致）。
```

- **不新增表单约束**：数组行允许临时重复段，`onChange` 收口处规约——语义由规约层保证，forms 内核保持通用。

### 2.3 判别键切换交互（docs/119 §4 交付项）

- **位置**：`FormRenderer.tsx` `ArrayView` 判别数组行（`node.schema.items.oneOf` 经 `oneOfDiscriminant` 成立的行）。
- **形态**：行内不再渲染只读 const 判别键 Select（第 2 步行为）；改为**行头段切换 Select**——选项＝全部分支 const 值，当前值＝该行判别值；切换 → 该行值整体替换为目标分支默认对象。
- **目标分支默认对象**：新导出 `oneOfBranchDefault(branches: Map<string, MetaSchema>, constValue: string)`（`formTree.ts`，docs/119 §2.3 首分支组装的任意分支推广）：判别键＝该分支 const 值＋其余 properties 逐字段 `defaultValueFor`；未登记 constValue → `null`（调用方忽略）。
- **通用能力**：任何判别数组消费方均获得行内切分支能力；SelectWidget const 只读行为（docs/119 §2.2）不改（非判别数组场景不受影响）。

### 2.4 `rulesLocked` 禁用（FormRenderer 新 `disabled` prop）

- `FormRendererProps` 增 `disabled?: boolean`（缺省 false，缺省行为零变化）→ `ViewContext.disabled`。
- 实现：`FormNodeView` 输出外包 `<fieldset disabled>`（浏览器原生，行内全部表单控件禁用；ArrayView 增删按钮在 fieldset 内同样禁用）。**零 widgets 改动**；AntD 控件兼容性由冒烟确认。
- RolloutModal 传 `disabled={rulesLocked}`（`status !== 'idle'`，现状 Switch/Input disabled 语义保持——从行内灰化变为整区灰化，见 §5 UI 变化）。

## 3. 实现清单（落码批，原子序见 §6）

1. `formTree.ts`：导出 `oneOfBranchDefault(branches, constValue)`（默认对象组装：判别键=const＋properties 递归 defaultValueFor）。
2. `FormRenderer.tsx`：`disabled` prop → `ViewContext.disabled` → `FormNodeView` 包 `<fieldset disabled>`（仅 disabled 为 true 时包裹，缺省输出不变）。
3. `FormRenderer.tsx` `ArrayView`：判别数组行渲染行头段切换 Select（推导 `discriminant`；行内判别键字段过滤不渲染）；切换 onChange 行值替换。
4. `release/rolloutRulesSchema.ts`（新）：`buildRolloutRulesSchema()`＋`normalizeRolloutRules()`。
5. `RolloutModal.tsx`：删手写三行（Switch×3／Input×1／InputNumber×3）与 `internal/bucket/canary` 变量与 `withRule` 调用；改挂 `<FormRenderer schema={rulesSchema} value={draft.rules} source="node" registry={deployRegistry} disabled={rulesLocked} uiSchema={{ labels: … }} onChange={…normalize 后 setDraft}/>`。`withRule`/`findRule` **保留导出**（`release.test.ts` 覆盖，仍为通用工具；RolloutModal 不再 import `withRule`，`findRule` 若不再被引用则从 RolloutModal import 清单移除，release.ts 本体不动）。
6. i18n：段 label 复用既有 `editor.json` `rollout.segment.*` 键（SEGMENT_LABEL_KEYS 语义）；uiSchema labels 覆盖字段文案（observe 先例）。

## 4. 非目标（明确不做）

- `full` 规则编辑面（canary 期剔除语义在后端，promote 后运行态，不属编辑面）；
- 后端形状／校验／迁移／依赖／ADR（纯前端渲染层替换，`RolloutConfig` 形状不变）；
- 新 `x-*`／新业务控件（段切换是判别数组通用交互，非控件）；
- L1 对 oneOf 的动态诊断（诊断层现状不动，docs/119 §4 维持）；
- 判别数组其他业务场景（本片只消费 RolloutModal 规则区）；
- GateMetricsWidget／gate 区再迁移（已收口，不动）；
- 解除 D29 整体（SchemaRegistry 通用化仍缓做，重开判据见 docs/118 §6）。

## 5. UI 行为变化（照实登记，与 gate 片「两处 UI 微差」同口径）

1. **三行固定开关 → 判别数组行增删＋行头段切换**：默认空数组（原默认 internal 含当前租户）——用户点「添加」得到 internal 行（首分支默认），行头 Select 可切段；段唯一由规约收敛（重复段只留最后一条）。
2. **tenants 逗号分隔 Input → 嵌套 string 数组行增删**（每租户一行输入＋删除）。
3. **rulesLocked：行内控件禁用 → 整规则区 fieldset 禁用**（灰化范围扩大；禁用语义不变，保存按钮另受 busy 控制）。

## 6. 原子序与门

- 原子：`docs(120 立项，本文件＋docs/08 立项块＋docs/14 D29 行＋handoff)` → `feat(frontend：formTree/FormRenderer/rolloutRulesSchema/RolloutModal)` → `test(frontend：rolloutRulesContract)` → `docs(收口)`。作者 bayernjf、无 AI co-author、默认不 push。
- 门（先跑后写，取实跑）：前端 vitest 全量（基线 956/2 → 本批净增后记实数）、oxlint 0/0、tsc 0、build 过、守护门 8 passed、真实浏览器冒烟（admin-a 登录 → 流程编辑器 → 灰度发布 Modal：规则区判别数组渲染、段切换、增删行、rulesLocked 禁用）。
- 收口同步：docs/119 §7、docs/115 §5 第 3 步第二片翻 ✅、docs/14 D29 行、docs/08 收口块、CHANGELOG、handoff（Active＋Recently shipped＋Quality gate）。

## 7. 测试清单

- `oneOfBranchDefault`：目标分支默认对象组装（判别键=const＋字段递归默认）／未登记值→null；
- `normalizeRolloutRules`：重复段保留最后一条／固定序重排／未知段剔除／空数组保留空／与 withRule 结果一致（对合法输入）；
- 段切换交互：internal 行切换 bucket → 行值替换为 bucket 默认对象（含 to=lowValueBucket、value/percent 默认）；同值切换 no-op；
- disabled 渲染：`disabled` 时输出含 `<fieldset disabled>`、增删按钮不可点；缺省 false 输出不含 fieldset；
- 判别数组行头：行内不再渲染只读 to Select（判别键字段过滤）；行头 Select 选项=三分支 const 值；
- SSR 渲染不崩；既有 release.test（withRule/findRule）、gateContract、oneOfContract 零回归。

## 8. 收口注记（落码后回填）

（预留：落码收口事实、偏差登记、门实跑数字。）
