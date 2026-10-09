# 模板参数声明面结构化：嵌套 object / 数组 / 条件显隐（打包 ZX，D29 第二切片）

- **日期**：2026-10-08 立项
- **形状权威**：本文
- **承接**：docs/14 D29（Schema 驱动配置内核通用化）剩余「嵌套/数组/枚举/if-then 条件显示」；docs/105 ZW（模板参数表单 Schema 驱动化）契约；docs/97 A1 模板参数化向导
- **性质**：纯加法（声明面扩展 + 桥接扩展）；零迁移、零新依赖、无 ADR；向后兼容（既有标量四型声明逐字不变）
- **用户授权**：2026-10-08 用户对 A1/A2/B2 候选清单批复「A1、A2，B2 也搞下吧」

## 1. 问题

ZW（docs/105）已把模板参数表单迁入 forms 内核：`templateParamsToMetaSchema` 把 `TemplateParam` 声明映射为 MetaSchema，Editor 参数 Modal 改挂 FormRenderer。但声明面仍是 **A1 标量四型**（string/number/boolean/select）：

- 后端 `_TEMPLATE_PARAM_TYPES = {string, number, boolean, select}`、`_TEMPLATE_PARAM_KEYS = {type, label, required, default, hint, options}`（api/main.py:3166-3167）
- 前端 `TemplateParam`（apiClient.ts:661）同构四型，`default` 仅标量
- 前端桥接 `templateParamsToMetaSchema`/`templateParamsToUiSchema` 只按标量四型映射

**而 forms 内核渲染层早已就绪**（M3/M4 建立）：`formTree.buildFormTree` 递归 object properties（group）、array items（增删行）、`uiSchema.hiddenWhen`（条件显隐 equals 语义，根层 + rootScoped 嵌套组）。也就是说——**内核能渲染结构化表单，但模板参数声明面喂不进去结构化 schema**。这是 D29 剩余「嵌套/数组/枚举/if-then 条件显示」中纯工程内可闭环的部分，接缝热（ZW 刚铺好桥接）。

D29 触发条件「技能/记忆/部署/交互模板任一实体需要配置表单」已由 A1 + ZT + ZW 实质满足，本批取回第二切片。

## 2. 形状

### 2.1 声明面扩展（前后端同构，纯加法）

`TemplateParam` 增加三个声明键：

```ts
export type TemplateParam = {
  type: 'string' | 'number' | 'boolean' | 'select' | 'object' | 'array'
  label?: string
  required?: boolean
  default?: unknown            // 标量或结构化 default（object/array 透传）
  hint?: string
  options?: string[]           // select 专用（不变）
  // 新增：嵌套 object（properties 递归为 TemplateParam）
  properties?: TemplateParams
  // 新增：数组（items 递归为 TemplateParam；minItems/maxItems 可选）
  items?: TemplateParam
  minItems?: number
  maxItems?: number
  // 新增：条件显隐（if-then：判别字段 == equals 时本字段才显示）
  visibleWhen?: { field: string; equals: unknown }
}
```

- **object**：`properties` 递归声明子字段；渲染走 forms 内核 group（嵌套 object 天然支持）。
- **array**：`items` 声明元素（元素本身可以是 string/number/boolean/select/object——数组行内嵌套 group 由内核递归支持）；minItems/maxItems 透传 MetaSchema 供 ArrayView 增删行门控。
- **visibleWhen**：判别字段 `field`（相对表单根，同 hiddenWhen 语义）当前值 === `equals` 时本字段可见，否则隐藏；映射为 UiSchema.hiddenWhen（equals 语义现成，零新机制）。
- 既有四型声明不受影响（无新键即旧行为）；`_TEMPLATE_PARAM_TYPES`/`_TEMPLATE_PARAM_KEYS` 各加新值/新键。

### 2.2 后端（api/main.py）

1. `_TEMPLATE_PARAM_TYPES` 加 `object`、`array`。
2. `_TEMPLATE_PARAM_KEYS` 加 `properties`、`items`、`minItems`、`maxItems`、`visibleWhen`。
3. `validate_template_params` 递归：object 校验 properties 形状（递归调用）、array 校验 items 形状 + min/max 数值 + min<=max、visibleWhen 校验 field 字符串 + equals 任意标量；递归深度上限 4（防病态嵌套）。既有四型校验逐字保留。
4. `_validate_instantiate_values` 递归：object 值须为 dict（非 dict 422）、子字段按 properties 递归校验；array 值须为 list（非 list 422）、逐元素按 items 校验；visibleWhen 不参与值校验（纯 UI 声明）。既有四型校验逐字保留。

### 2.3 前端桥接（templateParamSchema.ts）

- `templateParamsToMetaSchema` 递归：object → `{type:'object', properties: 递归, default?}`；array → `{type:'array', items: 递归, minItems?, maxItems?, default?}`；visibleWhen 不映射进 MetaSchema（走 UiSchema）。required 收集只做根层 + 每层 object 局部（同 forms 内核 group 语义）。
- `templateParamsToUiSchema` 承接 visibleWhen：`visibleWhen` → `hiddenWhen`（`{field, equals, show: [本字段名]}`），追加到 uiSchema.hiddenWhen；labels/hints 递归（键规则照 pointer 通配，嵌套字段 `parent.child` 风格由 uiKeySegments 已有 `a.b`/`a[].b` 支持）。既有映射逐字保留。

### 2.4 Editor.tsx 参数 Modal（零改动或最小适配）

- 提交路径不变：`validateParamValues` 标量校验对四型逐字保留；object/array 字段值不再走标量分支（补递归或由后端 422 兜底——见 2.5）。
- 计数 Alert「该模板声明了 N 个参数」按顶层声明数（`Object.keys(params)`），嵌套字段不翻倍计数。

### 2.5 前端纯逻辑（lib/templateParams.ts）

- `buildParamFields` 对 object/array 声明生成字段（type 扩展），初值：object → `{}`、array → `[]`（default 优先）；`validateParamValues` 对 object/array 补递归/类型检查（照后端语义对齐）。

## 3. 原子序（docs 立项 → 落码 → 测试 → 收口；本地 dev 不 push）

1. `docs(contract)`：本文 + docs/08 立项块 + docs/14 D29 行注记
2. `feat(api)`：声明面/校验递归（_TEMPLATE_PARAM_TYPES/KEYS + validate_template_params + _validate_instantiate_values）
3. `test(api)`：U1251 起（合法/非法形状、instantiate 递归校验、向后兼容回归）
4. `feat(frontend)`：TemplateParam 类型 + templateParamSchema 递归 + templateParams 纯逻辑递归
5. `test(frontend)`：U1251 起桥接/校验用例
6. `docs(closeout)`：docs/106 §6 收口注记 + docs/08 收口块 + docs/13 小节 + CHANGELOG + handoff + docs/14 D29 行收口注记

## 4. 门

- 后端全量 pytest（含新用例 U1251+）；守护门 test_handoff_integrity/test_migration_convention
- 前端 vitest / oxlint / `npx tsc --noEmit` / build
- 真实浏览器冒烟（admin-a，内存档）：造带 object/array/visibleWhen 的用户模板 → 参数 Modal 渲染嵌套 group/数组增删行/条件显隐 → 提交 instantiate 200 → 画布替换无问题

## 5. 非目标（D29 整体不解除）

- 枚举/if-then 表达式（非 equals 判别、多分支）、自定义控件扩展契约、增量调度/Web Worker、SchemaRegistry 六类实体通用化、嵌套数组（array of array）——触发条件不变

## 6. 收口注记（2026-10-08 实跑）

**状态**：本批落码+测试+冒烟+门全部通过，收口完成。原子链 `03a266f`（feat(api)）→ `31df0c7`（test(api)）→ `d8f0da9`（feat(frontend)）→ `4a3ab29`（test(frontend)），docs 立项 `985e001`（docs 批，含 docs/08/14/handoff 立项注记与契约）。dev，未 push。

**落码形状**：后端 `_TEMPLATE_PARAM_TYPES` 加 object/array、`_TEMPLATE_PARAM_KEYS` 加 properties/items/minItems/maxItems/visibleWhen、`_TEMPLATE_PARAM_MAX_DEPTH=4`、`_validate_param_decl` 递归校验（深度上限、visibleWhen 形状）、`_validate_param_value` 递归值校验（object properties 递归+未知子字段拒绝、array min/max 门控+items 递归）；前端 TemplateParam 类型六值扩展、templateParamSchema.ts 递归桥接（object→properties+局部 required、array→items+min/max、visibleWhen→hiddenWhen 根层/nested rootScoped）、templateParams.ts 递归纯逻辑（初值空容器、validateFieldValue 带路径错误、buildParamFields 顶层展开不递归进数组）。

**测试**：U1251–U1255（后端 17 例：合法结构化声明 5 组 parametrize、非法声明 422 十组、instantiate 递归校验 422 七分支、合法结构化值 variables 原样注入、标量回归）；前端 ZX describe 18 例（templateParamSchema 8＋templateParams 10）。**门（实跑 2026-10-08）**：后端全量 **2481 passed／169 skipped**（基线 2464/147 ＋ U1251–U1255 17 例）；前端 vitest 全量 **878 passed／2 skipped**（基线 876/2 ＋ 本批 2 例 graphSerializer position 兜底，见下）；oxlint **0/0**（189 文件）；`npx tsc --noEmit` 0；build 过（仅既有 chunk 警告）；守护门 test_handoff_integrity/test_migration_convention **8 passed**。

**浏览器冒烟（admin-a，内存档 utpl-1）**：真实浏览器全链路——模板市场打开 zx-structured-smoke 参数 Modal，验证①嵌套 object group 渲染（Webhook 配置：URL* 必填/secret 子字段）②数组增删行（通知渠道「添加 0/3→1/3」+ minItems 门控：0 行提交被「至少需要 1 项」拦截、1 行时删除禁用）③条件显隐（mode=auto 时 manual_reason 隐藏，切 manual 后 textarea 出现）④填全值提交 instantiate 200、画布替换无问题。

**冒烟暴露并修复的真实缺陷（新增 `feat(frontend)` 前置修复，独立原子提交）**：模板 instantiate 返回的图节点缺 position（GraphDSL 默认值不落库），`deserializeGraph` 不兜底 → React Flow 节点 position=undefined → 生成画布后 Editor 序列化崩溃白屏（`graphSerializer.ts:24` `Cannot read properties of undefined (reading 'x')`）。修复：`deserializeGraph` 对缺 position 节点按序生成瀑布默认位置（x=40, y=40+index×80），保留显式 position。补 2 例前端测试（无 position 兜底 + 显式 position 保留）。此修复同时惠及所有无 position 图来源（NL 草稿等），属正确性缺口闭合而非 ZX 引入缺陷。
