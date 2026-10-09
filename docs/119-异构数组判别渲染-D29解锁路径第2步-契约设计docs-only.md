# 异构数组判别渲染（oneOf/discriminator 最小子集）——D29 解锁路径第 2 步（契约设计，docs-only）

- 批次：D29「Schema 驱动配置内核」解锁路径第 2 步（docs/115 §5 第 2 步；docs/118 §6 重开判据第 1 条：「第 3 步立项前必须完成——部署迁移的规则数组需要 oneOf/discriminator 表达」）。
- 日期：2026-10-10，承接用户对「或需你拍板从缓做表开批（如 D29 第 2 步异构数组）」的批复「那你开搞」。
- 形状权威：本件 §2/§3；落码偏差登记 §7。
- 性质：docs-only 契约设计，落码批待契约通过后另立原子。

## 1. 背景与目标

docs/118 第 1 步（自定义控件扩展契约）已落码收口，`RolloutModal` 第 3 步第二片（异构规则数组）的渲染前提是 forms 内核能表达**判别联合数组**：数组元素是 `{to: 'internal'|'lowValueBucket'|'canary'|'full', …}` 的判别联合（`RolloutRule`，apiClient.ts 判别键 `to`），每个分支字段形状不同。现状 `resolveWidget` 对无统一 properties 的 `oneOf` 一律降级 json（resolveWidget.ts:46-48），formTree 的 array 分支也只认单 `items` schema（formTree.ts:125-140）。

本件目标：给 formTree/resolveWidget/defaultValueFor 补上**判别异构数组渲染的最小子集**——按元素实际值匹配 `oneOf` 分支子树渲染、新增行取首个分支默认对象、判别键只读展示。**只做渲染层**；校验层、后端、分支切换交互一律不进本批。

## 2. 形状契约

### 2.1 判别协议（不加新 keyword）

MetaSchema 封闭四 x-*（x-variable/x-widget/x-ref/x-outputSchema，docs/118 §3 维持）**不新增**。判别完全由标准 keyword `const` 推导：

- `items.oneOf` 各分支为 object schema，且**恰好一个键带 `const`**（该键即候选判别键）；
- 所有分支的判别键**同名**、且各 const 值**互不相同** → 判别成立（`oneOfDiscriminant` 返回 `{ key, branches: Map<constValue, MetaSchema> }`）；
- 任一分支缺 const、判别键不一致、或 const 值重复 → 判别不成立（返回 null），数组元素按现状降级。

### 2.2 元素渲染

- 元素按 `value[判别键]` 匹配 `branches` 取分支 schema，走既有 object→group 递归（properties/required/default 全复用）；
- `value` 非 object、判别键缺失、或判别值未登记 → 整行降级 `json`（保真展示，不猜分支）；
- 判别键字段本身在分支 properties 内，其 schema 为 `const` → SelectWidget 单选项且 `disabled`（widgets.tsx:68-81 现成行为）——**判别键只读展示，天然不可切分支**（切换交互见 §4 非目标）。

### 2.3 新增行默认值

- FormRenderer 数组「添加」走 `defaultValueFor(itemsSchema)`（FormRenderer.tsx:214）——items 为 oneOf 时，`defaultValueFor` 返回**首个分支的默认对象**：判别键=该分支 const 值＋其余 properties 逐字段 default→const→enum 首项→类型零值（复用 `defaultValueFor` 自身递归）。
- 空数组、minItems 门控等既有行为不变。

### 2.4 resolveWidget

- `case 'array'`：`schema.items` 存在即返回 `{ kind: 'array' }`（含 items.oneOf）——判别逻辑全部落在 formTree；
- object 级顶层 `oneOf`（无统一 properties）**维持 json 降级**（resolveWidget.ts:46-48 不改，注释补充判别数组通道）。

## 3. 实现清单（落码批，原子序见 §6）

1. `frontend/src/lib/forms/formTree.ts`：
   - 新增纯函数 `oneOfDiscriminant(oneOf: MetaSchema[]): { key: string; branches: Map<string, MetaSchema> } | null`；
   - array 分支：`schema.items.oneOf` 存在时逐元素选分支（`branches.get(String(value?.[key]))`，未中→`{}` 空 schema→json 降级），命中分支以该分支 schema 走 `buildFormTree`（depth+1）。
2. `formTree.ts` `defaultValueFor`：`schema.oneOf` 存在时组装首个分支默认对象（先取判别 const 键值，再逐 properties 递归 default）。
3. `resolveWidget.ts`：仅注释同步（判别数组属 array 通道），选择序不变。
4. `metaSchema.ts`：**零改动**（oneOf 已在白名单并递归自检）。

## 4. 非目标（明确不做，避免内核膨胀）

- object 级顶层 oneOf 联合渲染（保持 json 降级）；
- 嵌套 oneOf（分支内再 oneOf）、`discriminator`/`if-then-else` 等条件 schema；
- **判别键切换**（改判别值→整体换分支的交互）——SelectWidget 的 const 只读语义天然拒绝，切换形态留给第 3 步第二片（RolloutModal 规则区迁移）单独设计；
- L1 校验对 oneOf 的动态诊断（诊断层现状不动）；
- `RolloutModal` 规则区迁移（第 3 步第二片，依赖本批渲染能力后另立批）；
- 新控件、新 x-*、后端形状、迁移、依赖。

## 5. 测试清单（落码批）

- `oneOfDiscriminant`：一致判别提取／分支 const 键不一致→null／分支缺 const→null／const 值重复→null；
- formTree 判别数组：四分支元素各按 `to` 匹配分支子树（internal 行含 tenants 字段、bucket 行含 value/percent、canary 行含 percent、full 行无额外字段）；
- 未知 `to` 值／非 object 元素 → 行降级 json；
- `defaultValueFor` oneOf → 首个分支默认对象（判别键=const 值＋字段默认）；
- FormRenderer 判别数组渲染（SSR 不崩、判别键只读单选项、添加行默认值）；
- 既有 formTree/widgets/gateContract 测试零回归。

## 6. 原子序与门

- 原子：docs(plan 本件) → `feat(frontend)`（§3 三项）→ `test(frontend)`（§5 清单）→ docs 收口（docs/115 §5 第 2 步翻 ✅＋docs/14/08/CHANGELOG/handoff）。
- 门：前端 vitest 全量＋oxlint＋`tsc -b`＋build；守护门 8 passed（test_handoff_integrity＋test_migration_convention）；后端零改动沿用 AH 基线（2582/173/0）。

## 7. 收口注记

- 本件为纯契约设计：**无代码改动、无测试、无迁移、无依赖变更**。
- 关联：docs/118（第 1 步契约）；docs/115（第 2/3 步解锁路径）；docs/14 D29 行；docs/106（ZX 声明面结构化先例——递归桥接同族）。
