# Atlas 前端国际化（i18n）与设计 Token 方案

> **来源**：工程推导文档。需求依据为 [04-组件设计-编辑后台.md](04-组件设计-编辑后台.md) 十、补充项 28「多语言支持」三条（界面多语言中/英切换可扩展、自然语言多语言、组件描述多语言）；01-08 正文为冻结的唯一事实源，本文只做工程化方案，不改写需求。
> **状态**：方案已定（2026-09-13，2026-09-16 补 D7 多租户认证界面契约）。**设计 Token 等价替换已于 2026-09-13 落码**（`theme/tokens.ts` + `setup.ts`，三页面主体零视觉差异；存量零星硬编码见 §3.5 收尾清单）；i18n 库按触发条件引入（见 §4 落地节奏与 [14-缓做事项登记表](14-缓做事项登记表.md) D12/D13）。
> **AI 使用提示**：前端新增界面文案、颜色/间距/圆角值时必须按本文契约预留（不裸写硬编码、不自创 key 规则）；落码 i18n/token 时以本文为方案依据。

## 1. 背景与现状

| 主题 | 文档现状 | 代码现状（2026-09-13，W10 后） |
|---|---|---|
| i18n | 04 §补充项 28 有三行需求；10 文档"多语言/国际化"原为 ⏳ 未定 | 无 i18n 依赖；UI 文案全部硬编码中文；AntD `ConfigProvider` 未注入 locale |
| 设计 Token | 无 | 无 token 层：`App.tsx` 内联 `colorPrimary: '#1677ff'`；`index.css` 散落 `#0f172a / #1f2937 / #f5f7fa / #eef2f7 / #e5e7eb` 等硬编码色值（含节点运行状态色） |

Phase 1 种子客户验证在即，先把界面文案与视觉值的**契约**定下来，避免出现英文使用者或品牌定制时全仓替换。

## 2. 国际化（i18n）方案

### 2.1 需求分层（对应 04 §28 三条）

| # | 需求（04 §28 原文要点） | 归属层 | 本方案 |
|---|---|---|---|
| 1 | 界面多语言：编辑后台支持中/英切换，可扩展 | 前端 | i18next + react-i18next（§2.2） |
| 2 | 自然语言多语言：用户可用多种语言描述需求，自动翻译为统一内部表示 | LLM 层（非前端） | prompt 语言跟随用户输入，落码见 §2.6 |
| 3 | 组件描述多语言：适配器和模板说明多语言 | 元数据层 | 依赖适配器/模板元数据结构，随 Phase 2 模板库重启（14 D13） |

### 2.2 选型（ADR-2026-09-13 T7）

- **i18next + react-i18next**：React 生态事实标准，namespace 懒加载、复数/插值/格式化齐备，与 AntD 无冲突。
- **AntD locale**：使用 antd 6 自带 `antd/locale/zh_CN`、`antd/locale/en_US`，经 `ConfigProvider locale={...}` 注入，与 i18next 当前语言联动。
- **触发式引入**：Phase 1 种子客户默认中文，**当前不安装依赖、不落码**；引入触发条件 = 出现首个英文使用者或明确出海需求（14 文档 D12）。方案先行是为了冻结目录/key/错误码契约，触发后零返工。

### 2.3 目录与 key 契约（落码时照此执行）

```
frontend/src/locales/
├── index.ts                 # i18next 初始化、语言持久化、AntD locale 映射
├── zh-CN/
│   ├── common.json          # 通用：按钮、状态、错误码文案
│   ├── dashboard.json
│   ├── editor.json          # 编辑器：节点面板/属性面板/调试台/运行
│   └── demo.json            # 退款 Demo：订单选择器、模拟控制台文案
└── en-US/                   # 同构镜像（触发落码时随 zh-CN 同步建空骨架）
```

> D7（2026-09-16）新增的认证界面文案落码时归入 **common.json**，不新建 namespace：登录页（标题/用户名/密码/提交/错误提示/种子账号提示）用 `auth.login.*`；`lib/auth.ts` 的 `ROLE_LABELS` 用 `common.role.viewer|operator|admin`；UserBadge 的租户名/显示名分隔与「退出登录」用 `auth.session.*`；后端 401/403/404 提示用 `error.auth.*`（见 §2.4）。种子账号提示为 Demo 专用，可放 `auth.login.seedHint` 单键整体翻译。

- key 用语义层级命名：`editor.run.compile`、`demo.order.select`，**禁止用中文原文作 key**。
- namespace 按页面/域拆分（common/editor/dashboard/demo），与组件目录对应。
- 默认语言 `zh-CN`；用户选择存 `localStorage`，首次访问回退 `navigator.language`（非受支持语言回退 zh-CN）。
- 插值一律走 i18next `{{var}}`，不在组件里字符串拼接；复数用 i18next 后缀规则（`_one/_other`）。

### 2.4 后端文案边界（错误码契约）

- API 继续返回**结构化错误码 + message**：`StructuredError(code, message)` 模式已存在（如 `AUTH_FAILED / ORDER_NOT_FOUND / MISSING_PARAMETER / INVALID_ACTION / UNKNOWN_CAPABILITY`，见 `harness/base.py`、`shop/adapter.py`），Graph DSL 校验返回 422 + 中文错误列表。
- 前端按 **code 映射本地文案**（`locales/*/common.json` 的 `error.{code}` 键），后端 message 仅写入调试日志/控制台，不直接面向最终用户渲染。
- 约束：Demo 阶段后端新增错误码时，必须同步前端错误码表（code 是契约，message 是日志）。
- D7 认证端点（`/api/auth/login`、`iam/deps.py` 的 401/403、跨租户 404）当前只返回中文 `detail`、**无 code 字段**，属 i18n 触发时的第一批债：触发落码须先在后端补结构化 code（建议 `AUTH_INVALID_CREDENTIALS / AUTH_UNAUTHENTICATED / AUTH_FORBIDDEN / AUTH_NOT_FOUND`），前端再按 `error.auth.*` 映射；在此之前这些中文 detail 直接上屏是被允许的过渡状态（仅 Demo、无英文使用者）。

### 2.5 格式化与"不翻译"边界

- 金额：`Intl.NumberFormat(locale, { style: 'currency', currency: 'CNY' })`——当前退款金额直接渲染数字（如 `299`），落码时改格式化输出。
- 日期/时间：`Intl.DateTimeFormat(locale)`。
- **不翻译的内容**：退款原因、LLM 决策 reason、订单号、Graph JSON 等**业务数据**按原语言原样展示；i18n 只覆盖 UI 外壳（按钮、标题、状态标签、错误提示）。

### 2.6 自然语言多语言（需求 2，LLM 层策略）

- 决策（`llm/decision.py`）与 NL 生成（`llm/nl_generate.py`）的 LiteLLM 路径：prompt 语言跟随用户输入语言，模型输出不强制中文。
- 规则兜底（未配置 `LITELLM_MODEL`）：质量原因关键词表 `_QUALITY_REASONS` 仅覆盖中文，NL 模板仅识别中文退款关键词——规则兜底明确标注**仅中文场景**；英文/其他语言场景必须配置 `LITELLM_MODEL` 走 LiteLLM 路径。
- "自动翻译为统一内部表示"：内部表示本身是 Graph DSL（语言中立），不新增翻译层。

## 3. 设计 Token 方案

### 3.1 三层模型

| 层 | 含义 | 示例 |
|---|---|---|
| primitive | 原始色板/字号/间距/圆角数值，不直接被组件引用 | `blue-6: #1677ff`、`space-4: 16px`、`radius-md: 8px` |
| semantic | 语义角色，组件只引用本层 | `color-bg-canvas`、`color-text-primary`、`color-border`、`color-node-running`、`color-success/warning/danger` |
| component | AntD 组件级覆盖 | `theme.components.Button.borderRadius` |

### 3.2 单一事实源与消费方式（✅ 2026-09-13 已落码）

```
frontend/src/theme/tokens.ts   # token 对象（primitive + semantic），唯一事实源
frontend/src/theme/setup.ts    # main.tsx 引入一次，把 semantic 注入 :root 为 --atlas-* 变量
```

两处派生消费：

1. **AntD**：`tokens.ts` 导出 `antdTheme`（`theme.token` + `theme.components`），在 `App.tsx` 的 `ConfigProvider` 注入，替代内联 `#1677ff`。
2. **原生 CSS**：由 semantic token 派生 CSS 自定义属性 `--atlas-*`（挂在 `:root`），`index.css` 全部改引变量，消除硬编码 hex。

**不引入额外依赖**：CSS Custom Properties 浏览器原生支持；AntD 6 的 theme 机制已在现有依赖内。

### 3.3 命名约定

`--atlas-{类别}-{角色}[-{状态}]`，短横线分层：

- 颜色：`--atlas-color-bg-page` / `--atlas-color-bg-canvas` / `--atlas-color-text-primary` / `--atlas-color-border` / `--atlas-color-success` / `--atlas-color-node-ai` / `--atlas-color-node-ring-running` / `--atlas-color-node-ring-completed`（运行态光晕为 rgba 派生：ring-running / ring-completed / pulse-soft / pulse-strong，节点边框本身用 `--atlas-color-primary`）
- 间距：`--atlas-spacing-1..6`（4px 基线）
- 圆角：`--atlas-radius-sm/md/lg`；字号：`--atlas-font-size-*`；层级：`--atlas-z-header/canvas/modal`

### 3.4 暗色扩展点（本期不实现）

- 暗色模式通过 `[data-theme="dark"]` 选择器覆盖 **semantic 层**变量实现；primitive 不动，组件只引用 semantic。
- 本期只保证分层结构允许该扩展，不产出暗色值、不加切换器。

### 3.5 迁移清单（✅ 2026-09-13 已完成等价替换）

| 位置 | 原值 | 替换为 |
|---|---|---|
| `App.tsx` ConfigProvider | `#1677ff` | `antdTheme`（来自 tokens.ts）✅ |
| `index.css` 头部背景 | `#0f172a` | `--atlas-color-bg-header` ✅ |
| `index.css` 正文/主文字 | `#f5f7fa` / `#1f2937` | `--atlas-color-bg-page` / `--atlas-color-text-primary` ✅ |
| `index.css` 侧栏边框/画布背景 | `#e5e7eb` / `#eef2f7` | `--atlas-color-border` / `--atlas-color-bg-canvas` ✅ |
| `index.css` 节点 running/completed | 脉冲绿/描边绿硬编码 | `--atlas-color-node-ring-running` / `--atlas-color-node-ring-completed`（含 pulse soft/strong）✅ |
| `nodeCatalog.ts` / `FlowCanvas.tsx` | 类型三色/连线 `#1677ff` 内联 | `token('color-*')`（trigger→color-success、ai→color-node-ai、tool/edge→color-primary）✅ |

迁移目标是**零视觉变化**的等价替换（token 值 = 现值 1:1 搬迁），不做视觉改版。

**收尾清单（2026-09-16 核实，触碰相关文件时顺手清，不专门立项）**：

| 位置 | 现状 | 处理 |
|---|---|---|
| `index.css` paused 节点光晕 | `var(--atlas-color-node-ring-paused, #d48806)`——semantic token 未定义，长期走 fallback | 在 `tokens.ts` 补 `color-node-ring-paused`（值取 `#d48806` 同构 rgba 或直接 hex），去掉 fallback |
| `index.css:185` | `background: #fff` | 改 `var(--atlas-color-bg-container)` |
| `Editor.tsx` 两处占位框边框 | `var(--color-border, #d9d9d9)`——变量名缺 `--atlas-` 前缀，恒走 fallback | 改 `var(--atlas-color-border)`（值需对齐现视觉 `#d9d9d9` 或确认可用现有 border token） |
| `Monitoring.tsx` Statistic 健康色 | 内联 `#3f8600` / `#cf1322` | 改引 `--atlas-color-success` / `--atlas-color-danger`（经 CSS var 或 tokens 导出；色差以浏览器对比确认） |
| `Login.tsx` 页面底色 | 已于 2026-09-16 改 `var(--atlas-color-bg-page)` ✅ | — |

### 3.6 验收（落码时）

- `cd frontend && pnpm build` 通过、`pnpm test` 全绿。
- 浏览器对比替换前后三个页面：编辑器（含节点运行中/完成态）、Dashboard、`/demo/shop` 模拟控制台，无视觉差异。
- `grep -R "#[0-9a-fA-F]\{3,6\}" frontend/src` 仅剩 `tokens.ts` 内的 primitive 定义。

## 4. 落地节奏

| 事项 | 时机 | 依据 |
|---|---|---|
| 设计 Token 等价替换（tokens.ts + CSS 变量 + AntD theme） | ✅ 2026-09-13 已落码（本节 §3.5） | §3 |
| i18n 库引入 + zh-CN 抽取 + en-US 骨架 | 触发式：首个英文使用者/明确出海需求（14 D12） | §2.2 |
| 完整英文翻译 | 随首个英文客户试用 | §2.3 |
| 组件/模板描述多语言 | Phase 2 模板库（14 D13） | 04 §28 第 3 条 |
| 暗色模式 | 有需求时按 §3.4 扩展点实施 | §3.4 |

## 5. 与其他文档的关系

- 需求源：04 §补充项 28（冻结正文，不改写）。
- 选型记录：10 文档 §1 决策表（T7）+ §4 变更记录（i18n 库；token 零新依赖方案）。
- 目录落点：09 文档前端树 `locales/`、`theme/tokens.ts`（待落码标注）。
- 触发缓做：14 文档 D12（i18n 落码）/ D13（组件描述多语言）。
