# 模板参数表单 Schema 驱动化（打包 ZW，D29 部分取回）

- **日期**：2026-10-08 立项
- **形状权威**：本文
- **承接**：docs/14 D29（Schema 驱动配置内核通用化）；docs/97 A1 模板参数化向导；docs/103 ZT RuleConfigEditor；M3/M4 forms 内核（FormRenderer/uiSchema/WidgetRegistry）
- **性质**：纯前端加法；零迁移、零新依赖、无 ADR、后端零改动
- **用户授权**：2026-10-08 用户对 D29 最小切片「模板参数表单 Schema 驱动化」方案批复「好的」

## 1. 问题

表单内核（`frontend/src/lib/forms/`，M3/M4 建立）已服务两类实体：节点 config（`FormRenderer source='node'`）与工具 params（`source='tool'`）——`resolveWidget` 把 schema 声明（boolean→switch、number→number、enum→select、string→text）解析为内置控件，`uiSchema` 承接文案/分组/条件显隐。

但 **A1 模板参数化向导（docs/97 §3.4）仍是一套手写表单范式**：Editor.tsx 1494-1540 按 `field.type` 手写四分支（Switch/Select/InputNumber/Input），字段声明（`ParamField`）与渲染逻辑散在 `lib/templateParams.ts` 纯函数 + Editor 内联 JSX。这是**第四套表单范式**，与节点/工具/卡片三路并行，schema 能力（required 星号、enum 选项、placeholder）重复实现。

D29 触发条件「技能/记忆/部署/交互模板任一实体需要配置表单」已由 A1（模板市场参数化向导）+ ZT（规则模板配置表单）实质满足。本批取回最小切片：**把模板参数表单迁入 forms 内核**，让「模板参数」成为第三个吃同一套「schema 声明 → widget 渲染」链路的实体；同时让 A1 的 `ParamField` 声明与 forms 的 `MetaSchema` 建立一条可复用的桥接（未来技能/记忆/部署实体的表单化照此通道）。

## 2. 形状

### 2.1 新增桥接纯函数 `frontend/src/lib/forms/templateParamSchema.ts`

零 React、零网络 IO，vitest 可单测（照 `templateParams.ts` 先例）。

```ts
/** A1 TemplateParam 声明 → MetaSchema（root object + properties + required + default/enum）。 */
export function templateParamsToMetaSchema(params: TemplateParams): MetaSchema
/** A1 TemplateParam 声明 → UiSchema（labels 承接 label；hints 承接 hint）。 */
export function templateParamsToUiSchema(params: TemplateParams): UiSchema
```

映射规则（逐字段）：

| TemplateParam | MetaSchema 字段 |
| --- | --- |
| `type: 'string'` | `{ type: 'string', default? }` |
| `type: 'number'` | `{ type: 'number', default? }` |
| `type: 'boolean'` | `{ type: 'boolean', default? }` |
| `type: 'select'` | `{ type: 'string', enum: options, default? }`（**enum 驱动 select 控件**，resolveWidget 现成通道） |

- root：`{ type: 'object', properties, required: [...required 字段] }`。
- `label` → `uiSchema.labels[name]`（缺省显示字段名，与 A1 现行为一致）。
- `hint` → `uiSchema.hints[name]`（新增槽位，见 2.2；缺省不渲染）。
- 不映射 `options` 之外的自定义键；`default` 只透传合法类型。
- **buildInitialValues 语义不变**（select 无 default 取首 option 等）——Editor 弹窗初值仍走 `templateParams.ts.buildInitialValues`，本批不迁移。

### 2.2 UiSchema 新增 `hints` 槽位（字段级说明文字）

`uiSchema.ts` 的 `UiSchema` 增加：

```ts
/** 字段下方说明文字（键规则同 labels；模板参数 hint 与既有字段说明共用）。 */
hints?: Record<string, string>
```

`FormRenderer` 的 `Field` 包装（label + required 星号 + children）增加可选 hint 渲染：hint 存在时在控件下方输出一行 secondary 小字。`decorateField`/`decorateNodeForRender` 同步烘焙（照 labels 同一指针规则）。**缺省 undefined 时行为与现状完全一致**（节点/工具/卡片三路零影响）。

### 2.3 Editor.tsx 参数 Modal 渲染区迁移（1494-1540）

- 删除 `field.type` 手写四分支（Switch/Select/InputNumber/Input）。
- 替换为：

```tsx
<FormRenderer
  schema={templateParamSchema}
  uiSchema={templateParamUiSchema}
  value={templateParamValues}
  onChange={(next) => setTemplateParamValues(next as TemplateParamValues)}
  source="tool"
/>
```

- `templateParamSchema`/`templateParamUiSchema` 由 `detail.params` 经桥接纯函数派生（`useMemo`，弹窗打开时随 fields 一起算）。
- 弹窗宽度/高度、提交按钮、错误提示、`submitTemplateParams` 校验逻辑（`validateParamValues` + 后端 422 对齐）**全部不变**。
- 删除后 `templateParams.ts` 的 `buildParamFields` 若不再被消费则一并删除（**保留 buildInitialValues/validateParamValues**）；若仍有其他消费方则保留（实证核对）。

## 3. 边界与非目标

- **不迁移 RuleConfigEditor（ZT）**：其「开关组 + 阈值 + custom 增删行」形态依赖嵌套/数组/条件显示能力，属 D29 剩余部分（嵌套/数组/if-then 显示），本片不做；ZT 已收口、行为逐字不变为硬约束。
- **不改后端 / 不改 TemplateParam 声明形态（apiClient） / 不改 instantiateTemplate 契约**。
- **不新增业务控件注册**：模板参数只用内置 text/number/switch/select；`registerWidget` 扩展点保持零调用。
- **不迁移 buildInitialValues/validateParamValues 进 schema 校验**：提交校验仍走 A1 既有纯函数（与后端 422 语义对齐），FormRenderer 不承担提交校验。
- **不做 L1 schema 校验器迁移 / 结构化诊断接入**（M2 已建，但模板参数无诊断需求，属 D29 剩余统一时再评估）。
- **D29 整体不解除**：剩余（嵌套/数组/枚举/if-then 显示、自定义控件扩展契约、增量调度/Web Worker 图级校验、SchemaRegistry 六类实体通用化）触发条件不变。

## 4. 原子序

1. docs 立项（docs/08 立项块 + docs/14 D29 行注记 + 本文）
2. feat(frontend)：`templateParamSchema.ts` 桥接纯函数 + `UiSchema.hints` 槽位（uiSchema.ts + FormRenderer Field 渲染 + decorate 烘焙）
3. feat(frontend)：Editor.tsx 参数 Modal 渲染区改挂 FormRenderer（删手写四分支；删 buildParamFields 若实证无消费）
4. test(frontend)：U1245 起
5. docs 收口（docs/08 收口块 + docs/13 小节 + CHANGELOG + handoff）

不 push（用户偏好）。

## 5. 验收用例（docs/13 登记）

- **U1245**：`templateParamsToMetaSchema`——四类型映射（string/number/boolean/select→enum）、required 收集、default 透传、root object。
- **U1246**：`templateParamsToUiSchema`——labels/hints 承接 label/hint；缺省回退字段名/不渲染。
- **U1247**：`UiSchema.hints`——Field 有 hint 渲染 secondary 行、无 hint 不渲染（渲染契约）。
- **U1248**：Editor 参数 Modal——`detail.params` 非空时 FormRenderer 挂载、字段数=声明字段数、初值=buildInitialValues（契约）。
- **U1249**：提交路径不变——`validateParamValues` 四档校验（required/数字/布尔/select 范围）行为与 A1 一致（回归）。
- **U1250**：回归——无 params 的模板应用路径不受影响（`applyTemplate` 空 params 直走 `finishApplyTemplate`）。
