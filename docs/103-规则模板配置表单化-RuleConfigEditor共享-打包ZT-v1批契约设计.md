# 打包 ZT 规则模板配置表单化（RuleConfigEditor 共享）v1 批契约设计

> 立项：2026-10-08，承接 docs/08 §八「候选池复筛（2026-10-08）」新识别的唯一工程内候选——打包 ZS（docs/102）让用户能自建告警规则模板，但新建/编辑 Modal 里 `config` 是**裸 JSON 文本框**；而生效规则页（Monitoring）早已有完整的规则表单（内置规则开关/阈值＋custom 规则增删改）。ZS 契约 §5 明确把「规则配置 UI 表单化」列为非目标，本批正式取回。
>
> 性质：**纯前端重构＋功能补齐**。后端零改动、零迁移、零新依赖、无 ADR、无新错误码。

## §1 背景与目标

ZS 落码后，租户 admin 可以在「规则模板市场」里新建/编辑/删除私有模板，但配置录入路径是：

```
Modal TextArea（手写 RuleConfig JSON）→ JSON.parse → 提交
```

对照体验断层：

1. 同一套 `RuleConfig` 结构，在 Monitoring「告警规则」卡片里已有可视化表单（4 个内置规则开关与阈值、升级/恢复三组参数、custom 规则行内编辑），模板 Modal 却要求用户手写 JSON——包括 `run_error: { enabled: true }` 这种样板字段。
2. JSON 路径唯一的前端校验是 `JSON.parse` 语法层；字段语义错误（custom 名称为空、表达式非法、阈值越界）只能等后端 422 `RULE_TEMPLATE_CONFIG_INVALID`。
3. 两份编辑入口各自维护一套配置形状认知，后续 RuleConfig 加字段要改两处。

**目标**：把 Monitoring 页的规则编辑表单抽成纯受控共享组件 `RuleConfigEditor`，两个宿主复用：

- Monitoring「告警规则」卡片：行为逐字不变（含保存前前端校验）；
- 规则模板市场新建/编辑 Modal：以表单替换 JSON TextArea，提交结构化 `RuleConfig` 对象。

## §2 现状（实测，2026-10-08）

- 类型（`frontend/src/lib/apiClient.ts:1444-1462`）：

  ```ts
  CustomRuleConfig = { cid: string; name: string; enabled: boolean;
                       expression: string; severity: 'critical'|'warning' }
  RuleConfig = {
    run_error: { enabled: boolean }
    node_failed: { enabled: boolean }
    consecutive_failures: { enabled: boolean; threshold: number }
    failure_rate: { enabled: boolean; window: number; min_samples: number; rate: number }
    custom?: CustomRuleConfig[]
    escalation_ack_minutes?: number | null      // docs/33 §5.2
    recovery_healthy_streak?: number            // docs/55，默认 1，1-20
    recovery_cooldown_minutes?: number | null   // docs/55
  }
  ```

- Monitoring 表单（`frontend/src/pages/Monitoring.tsx:638-859`）：内置 4 规则 `Space wrap` 区（Switch＋InputNumber，min/max 已钉死）、升级/恢复三组参数、custom 列表（name Input 150px／expression Input 340px monospace／severity Select 100px／enabled Switch／删除按钮）＋新增按钮；行内函数 `updateCustom`/`addCustom`/`removeCustom`（151-188 行）；保存前校验在 `saveRules`（190-214 行）：custom 名称空→`rules.nameEmpty`，表达式过 `validateExpression`（`frontend/src/lib/conditions.ts:613`，禁 eval 安全引擎）。
- 模板 Modal（`frontend/src/components/monitoring/AlertRuleTemplateMarket.tsx:269-302`）：antd `Form` 管 name/description/tags/config 四字段，config 为 `Input.TextArea`（monospace，10-20 行）；`openCreate` 填 `defaultConfig()`（文件底部 307-318 行，与后端默认同形）、`openEdit` 回填 `JSON.stringify(detail.config, null, 2)`；`submit` 里 `JSON.parse`，失败置 `templates.configInvalid`。
- i18n 键两档齐备（`frontend/src/locales/{zh-CN,en-US}/monitoring.json`）：`builtinRule.*`、`ruleForm.*`（threshold/unitTimes/failureRate/window/minSamples/rate/escalation*/recoveryStreak*/cooldown*）、`custom.*`（title/hint/namePlaceholder/enabled/add/nameEmpty）、`severity.warning/critical`、`common:button.delete`。模板专有：`templates.configLabel/configInvalid`。

## §3 决策

### §3.1 共享组件 `RuleConfigEditor`

新文件 `frontend/src/components/monitoring/RuleConfigEditor.tsx`：

```ts
export function defaultRuleConfig(): RuleConfig  // 从模板市场底部迁入，形状逐字不变

export function validateRuleConfig(config: RuleConfig): string[]
// 纯函数：对每个 custom 规则收集错误（名称空→沿用 custom.nameEmpty 文案由宿主拼？
// 不——纯函数返回结构化错误，文案归组件/宿主，见 §3.3）

export function RuleConfigEditor(props: {
  value: RuleConfig
  onChange: (next: RuleConfig) => void
  disabled?: boolean
}): JSX.Element
```

- **纯受控**：不发请求、不碰 antd Form、不持有最终态；内部只做 `{ ...value, ... }` 不可变更新后调 `onChange`。
- 内容＝Monitoring 638-859 行表单主体（内置 4 规则区＋升级/恢复三组＋custom 列表＋新增按钮），**不含** Card 外壳、保存按钮、保存结果 Alert（宿主各异）。
- custom 行的 cid 生成沿用 `crypto.randomUUID()`（与现 addCustom 一致）。
- 各 InputNumber 的 min/max/step、suffix、宽度逐字搬迁。
- 即时校验红框与错误提示保留在 custom 行内（名称空／表达式非法）。

### §3.2 校验函数形状

`validateRuleConfig` 返回结构化错误数组，避免在纯函数里耦合 i18n：

```ts
export type RuleConfigError =
  | { kind: 'custom-name-empty'; index: number }
  | { kind: 'custom-expression-invalid'; index: number; messages: string[] }
export function validateRuleConfig(config: RuleConfig): RuleConfigError[]
```

- 表达式校验仍调 `lib/conditions` 的 `validateExpression`（禁 eval 引擎唯一入口，不另造）。
- 组件内部用它渲染红框/行内提示（文案键沿用 `custom.nameEmpty` 与表达式 messages 本身——`validateExpression` 返回的已是 i18n 后文案，与现状一致）。
- 宿主保存/提交时调它：Monitoring 的 `saveRules` 用返回值置 `ruleError`（保持现有「逐行前端校验、不发请求」语义）；模板 Modal 的 `submit` 用它拦截并置 `formError`。

### §3.3 Monitoring 接线

- 删除内联 `updateCustom`/`addCustom`/`removeCustom` 与 638-797、799-859 两段 JSX，替换为：

  ```tsx
  <RuleConfigEditor value={rules} onChange={setRules} />
  ```

  保存按钮留在 Monitoring（`saveRules` 经 `updateRules` 发请求，属宿主行为）。
- `saveRules` 的循环校验改为 `validateRuleConfig(rules)`，错误展示文案与现状等价（名称空→`rules.nameEmpty`（带 cid 插值）／表达式→`rules.exprInvalid`（带 name 与 messages））。
- 不改变任何 state 形状、拉取/保存时序、Alert 展示条件。

### §3.4 模板 Modal 接线

- antd Form 只管 name/description/tags；config 改为独立 `useState<RuleConfig | null>`。
- `openCreate`：`setConfig(defaultRuleConfig())`；`openEdit`：`setConfig(detail.config)`（结构拷贝，避免编辑态污染列表项）。
- Modal 内 config 字段区：

  ```tsx
  <Form.Item label={t('templates.configLabel')} required>
    {config && <RuleConfigEditor value={config} onChange={setConfig} />}
  </Form.Item>
  ```

  容器加 `maxHeight: 420; overflowY: auto`（表单内容高于 JSON 框），Modal `width` 680→760。
- `submit`：删 `JSON.parse` 分支；先 `validateRuleConfig(config)`，有错置 `formError`（沿用 `templates.configInvalid` 作为「配置校验未通过」总提示，行内红框已给明细）不发请求；通过则 payload 直接带结构化 config。
- 后端契约不变：收到的仍是同一 `RuleConfig` JSON 形状，`validate_rules` 照常把关。

### §3.5 i18n

- 组件只复用现有键，**零新增文案键**（两档 PARITY_PAIRS 守护不破）。
- `templates.configInvalid` 语义从「JSON 解析失败」变为「配置校验未通过」，文案相应微调（两档同步；zh「规则配置校验未通过，请检查标红项」/en「Rule config validation failed, please check the highlighted fields」）。`templates.configLabel` 保留。

### §3.6 验收

1. Monitoring 页规则编辑、保存、校验/报错行为与改动前逐字一致（现有 Monitoring 相关测试全绿）。
2. 模板市场新建：表单默认值＝defaultRuleConfig；改开关/阈值/加 custom 后提交，请求 payload.config 为结构化对象且与表单一致；名称空/表达式非法时被前端拦截、不发请求。
3. 模板市场编辑：回填该模板 config 到表单；保存后 id/seq/created_at 不变（后端既有契约，前端不碰）。
4. 内置模板与一键应用路径零改动。
5. 前端三道门：vitest 全绿、oxlint 0/0、`pnpm build` 过；后端零改动，后端测试不受影响（守护门照常跑）。

## §4 落码原子序

1. `refactor(frontend)`：新建 `RuleConfigEditor.tsx`（含 defaultRuleConfig 迁入、validateRuleConfig 导出），Monitoring 接线删内联代码；**此原子单独可验**（Monitoring 行为不变，现有测试全绿）。
2. `feat(frontend)`：模板 Modal 表单化（config state、openCreate/openEdit、submit 校验、宽度/滚动、configInvalid 文案两档微调）。
3. `test(frontend)`：U1231–U123x（见 docs/13 打包 ZT 小节）。
4. docs 收口原子（docs/103 §5 收口注记、docs/13 转正式、docs/08、docs/14 D28 注记、docs/00、CHANGELOG、handoff）。

## §5 边界与不做

- **不做**：custom DSL 专用表达式编辑器（保持单行 Input＋现有安全校验）；规则按图绑定、发布门禁联动、灰度（D32 触发条件不变）；跨租户模板市场/抽成；模板版本历史/回滚；多值班组；规则模板的导入导出 JSON（JSON 文本录入路径删除后，高级用户如需批量搬运，另立项）。
- **不做**：RuleConfig 结构本身的任何字段增删（纯呈现层重组）；后端、API、存储、错误码、迁移一律不动。
- JSON TextArea 路径**直接删除**而非保留「高级模式」开关：两套录入并存会重新制造双份真相；如未来确有 JSON 批量导入需求，按导入功能单独立项（文件上传＋校验预览）。
- D28 整体仍不解除（跨租户市场、OTel 正式栈、多实例锁、长保留时序等触发条件不变）。

## §6 收口注记（落码后回填）

（待填）
