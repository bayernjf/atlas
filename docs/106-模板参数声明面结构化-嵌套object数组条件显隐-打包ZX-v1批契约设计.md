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
