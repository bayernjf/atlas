# Atlas 前端国际化（i18n）与设计 Token 方案

> **来源**：工程推导文档。需求依据为 [04-组件设计-编辑后台.md](04-组件设计-编辑后台.md) 十、补充项 28「多语言支持」三条（界面多语言中/英切换可扩展、自然语言多语言、组件描述多语言）；01-08 正文为冻结的唯一事实源，本文只做工程化方案，不改写需求。
> **状态**：方案已定（2026-09-13，2026-09-16 补 D7 多租户认证界面契约）。**设计 Token 等价替换已于 2026-09-13 落码**（`theme/tokens.ts` + `setup.ts`，三页面主体零视觉差异；2026-09-16 §3.5 收尾清单五项已全部清零，`src` 下仅 tokens.ts primitive 定义含 hex）；i18n 库按触发条件引入（见 §4 落地节奏与 [14-缓做事项登记表](14-缓做事项登记表.md) D12/D13）。**2026-09-20 M12 先行落地零依赖文案抽取骨架**（`frontend/src/locales/` 自写 t()/useTranslation 对齐 i18next 签名 + zh-CN/common 样板 + 登录页/UserBadge 接线，不装 i18next、不做切换 UI、不翻译 en-US；触发时零返工替换为 i18next，见 §4）。**2026-09-24 docs/57 完成 en-US 首批全量翻译与语言切换**（10 个非空 namespace 全译、localStorage 持久化切换、UserBadge/Login 两入口、antd locale 联动、发布/灰度/反馈/审批/属性面板/表单/画布全接线；决策继续不引入 i18next、不做 navigator 探测；i18next 与元数据多语言仍缓做于 14 D12/D13，详见 docs/57 与本文 §4）。**2026-09-24 取回 i18n 第一批债**：后端认证端点结构化错误码（AUTH_*，detail 为 {code,message}）+ 前端 L1 校验诊断全量 i18n（validation namespace 82 键、语言切换全量重算）；剩余 Graph DSL 422 列表与元数据 D13 仍缓做。
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
- 默认语言 `zh-CN`；用户选择存 `localStorage`（key `atlas.locale`）。**2026-09-24 docs/57 决策 2 收口更正：不做 `navigator.language` 自动探测**（Phase 1 中文种子客户定位，防 en navigator 误显；自动探测留作出海批次），首次访问/非法存储值一律回退 zh-CN，语言只由用户显式切换决定。
- 插值一律走 i18next `{{var}}`，不在组件里字符串拼接；复数用 i18next 后缀规则（`_one/_other`）。

### 2.4 后端文案边界（错误码契约）

- API 继续返回**结构化错误码 + message**：`StructuredError(code, message)` 模式已存在（如 `AUTH_FAILED / ORDER_NOT_FOUND / MISSING_PARAMETER / INVALID_ACTION / UNKNOWN_CAPABILITY`，见 `harness/base.py`、`shop/adapter.py`），Graph DSL 校验返回 422 + 中文错误列表。
- 前端按 **code 映射本地文案**（`locales/*/common.json` 的 `error.{code}` 键），后端 message 仅写入调试日志/控制台，不直接面向最终用户渲染。
- 约束：Demo 阶段后端新增错误码时，必须同步前端错误码表（code 是契约，message 是日志）。
- D7 认证端点（`/api/auth/login`、`iam/deps.py` 的 401/403）**2026-09-24 已补结构化 code**（第一批债取回）：login 用户/口令错 401=`AUTH_INVALID_CREDENTIALS`、账号停用 403=`AUTH_ACCOUNT_DISABLED`、缺凭证 401=`AUTH_UNAUTHENTICATED`、角色不足 403=`AUTH_FORBIDDEN`；响应 detail 形状为 `{"code","message"}`（message 保留中文作日志/默认），前端 `apiClient.resolveErrorMessage` 按 code 映射 `error.auth.*`。**跨租户 404 故意不区分「不存在/越权」（防资源泄漏），不补 code、保持现状**，故原建议的 `AUTH_NOT_FOUND` 未落码。

- **Graph DSL 编译 422（2026-09-24 第二批债取回，已码化）**：编译错误 `Issue` 由 `(message, location)` 二元组演进为 `(message, location, code, params)` 四元组，`GraphValidationError` 与 `validate_graph_report()` 同步返回 codes/params（缺省兜底码 `GRAPH_VALIDATION_FAILED`）；API 编译/保存 422 在 `detail`（中文 message 列表，保留作日志/兜底）与稀疏 `locations` 之外，**并行下发与 detail 等长的 `codes` / `params`**。共 **170 个编译错误码**（前缀 `GRAPH_/DSL_/NODE_/EDGE_/VARIABLE_/COND_/LOOP_/PAR_/WAIT_/SUB_/SUBREF_/APR_/REF_`），节点级错误 params 统一注入 `owner`（出错节点 id，区别于业务键 `nodeId`=被引/未配置节点），数组型参数（supported/strategies/actions/entries/inner/keys）由前端 `join(', ')`。前端 `apiClient.resolveValidationList` 逐下标按 `validation.dsl.<code>` 映射并插值，审批分支枚举 `approved/rejected` 插值前本地化；缺翻译键或无 code 时**回退该条中文 detail**（英文态最坏混中文，绝不泄漏 i18n key）。zh/en 各 172 键（170 码 + `_branch_approved/_branch_rejected`），en dsl 块零汉字。**仍显中文、留作后续小批**：① condition/loop 表达式**求值**错误文案（`graph/conditions.py` 的 `ConditionEvalError`，本批经 `params.detail` 透传，英文态该条混中文）；② 运行时 wait 失败 5 码（`loader.WaitNodeFailure`：`WAIT_DURATION_INVALID/WAIT_ABSOLUTE_TIME_INVALID/WAIT_EVENT_FRAME_INVALID/WAIT_EVENT_KEY_INVALID/WAIT_TIMEOUT_FAILED`，走运行失败通道、不走编译 422，未入 dsl locale）。

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

**收尾清单（2026-09-16 已全部清零，浏览器逐项核对）**：

| 位置 | 现状 | 处理 |
|---|---|---|
| `index.css` paused 节点光晕 | 已补 token，零视觉变化 | ✅ 2026-09-16：tokens.ts 补 primitive `gold7 #d48806` + semantic `color-node-ring-paused`，index.css 去 fallback；探针实算 `rgb(212,136,6) 0 0 0 4px` |
| `index.css:185` | 已替换 | ✅ 2026-09-16：改 `var(--atlas-color-bg-container)`（断点圆点实算 rgb(255,255,255)） |
| `Editor.tsx` 两处占位框边框 | 已替换 | ✅ 2026-09-16：改 `var(--atlas-color-border)`，卡片边框对齐语义边框 #e5e7eb（原恒走 fallback #d9d9d9；模板 Modal 五张卡 + 录制卡实算 rgb(229,231,235)，色差不可察） |
| `Monitoring.tsx` Statistic 健康色 | 已替换 | ✅ 2026-09-16：改 CSS var `--atlas-color-success`/`--atlas-color-danger`（实算 rgb(82,196,26)/rgb(255,77,79)；较旧深色字 #3f8600/#cf1322 略亮，为 AntD 标准状态色、白底可读性已在浏览器确认） |
| `Login.tsx` 页面底色 | 已于 2026-09-16 改 `var(--atlas-color-bg-page)` ✅ | — |

### 3.6 验收（落码时）

- `cd frontend && pnpm build` 通过、`pnpm test` 全绿。
- 浏览器对比替换前后三个页面：编辑器（含节点运行中/完成态）、Dashboard、`/demo/shop` 模拟控制台，无视觉差异。
- `grep -R "#[0-9a-fA-F]\{3,6\}" frontend/src` 仅剩 `tokens.ts` 内的 primitive 定义。

## 4. 落地节奏

| 事项 | 时机 | 依据 |
|---|---|---|
| 设计 Token 等价替换（tokens.ts + CSS 变量 + AntD theme） | ✅ 2026-09-13 已落码（本节 §3.5） | §3 |
| 零依赖文案抽取骨架（自写 t()/useTranslation 对齐 i18next 签名 + zh-CN/common 样板 + 登录页/UserBadge 接线，余 namespace 与 en-US 空对象占位） | ✅ 2026-09-20 M12 落码收口（`9f1963f`/`a894fa7`，U212–U218，前端 495/40 文件，浏览器两截图 m12-login/userbadge；不装 i18next，触发时零返工替换） | §2.3 |
| dashboard namespace 首批生产接线（登录后落地页 Dashboard：demo.* 走 dashboard ns，品牌名/主导航入 common 经 `common:` 前缀跨取；Graph/Loop/Harness 技术专名按 §2.5 不译，en-US/dashboard 仍空） | ✅ 2026-09-20 M12 续批落码（U219–U221，前端 498/40 文件，截图 m12-dashboard；editor/Monitoring/Memory 大页面留后续分批） | §2.3/§2.5 |
| editor namespace 抽取（编辑器大页，分两原子：框架/画布/左栏面板＋属性面板/调试台；纯展示文案全抽，节点类型/DSL/OODA/SSE 等技术专名按 §2.5 不译，后端中文 detail 原样上屏不 key 化） | ✅ 2026-09-21 docs/33 批 1 落码（`97d61dc`/`ad12f98`＋`bf9cdb6`/`888daf8`，en-US/editor 仍空 `{}`） | §2.3/§2.5 |
| monitoring namespace 抽取（监控告警页：指标/规则/告警/适配器调用/Trace 时间线/影子运行/静默/值班全域；P50/P95、read/write/financial/administer、SHADOW_DRY_RUN、kind 名等专名不译） | ✅ 2026-09-21 docs/33 批 1 落码（`0c299ef`/`4bf7c7f`，en-US/monitoring 仍空 `{}`） | §2.3/§2.5 |
| memory namespace 抽取（记忆/长期上下文页：记忆列表/kind 标签/置信度/scope/CRUD 表单；技术专名不译） | ✅ 2026-09-21 docs/33 批 1 落码（`3445576`/`f11dad5`，en-US/memory 仍空 `{}`） | §2.3/§2.5 |
| i18n 库引入（i18next） | zh-CN 抽取与零依赖骨架 ✅（M12/docs/33）；**i18next 本体经 docs/57 决策 1 验证继续不引入**（自写 t() 经 11 namespace 全量翻译与切换验证够用），仍缓做于 14 D12：出现复数规则/namespace 懒加载硬需求时再换，组件侧 `t()`/`useTranslation()` 签名不变、零返工 | §2.2 |
| 完整英文翻译（en-US UI 外壳 + 切换运行时） | ✅ **2026-09-24 docs/57 部分取回落码**：10 个非空 namespace 全量英译（demo 仍空）、localStorage 持久化切换（不做 navigator 探测）、UserBadge 语言组菜单 + Login 页脚两入口、antd locale 三处 ConfigProvider 联动、发布/灰度/反馈/审批卡片/审批两页/7 个 Config/WaitConfig/ToolCallConfig/FormRenderer/nodeWidgets/widgets/画布/Problems 全接线；静态守护三例（zh/en 键集合一致、en 零汉字〔唯一豁免 common:language.zhCN〕、插值标识符集合一致）；U610–U625 全过（前端 662 passed/2 skipped、lint 0 error、build ✓、双语浏览器冒烟 18 截图）。**2026-09-24 第一批债进一步取回**：后端认证端点已结构化 code 化（§2.4，前端按 code 走 i18n、中文 message 仅作日志）；L1 校验诊断消息已全量 i18n（新建 `validation` namespace 82 叶子键 zh/en 对齐，`l1.ts` 用户可见中文清零；语言切换经 `engine.invalidateForLocale()` 清缓存并全量重算 L1/L2/L3，ToolCallConfig 参数诊断同步重算；英文态单测覆盖）。**仍显中文的层**：Graph DSL 422 中文错误列表（`graph/dsl.py`，几十条，另起小批，**2026-09-24 已码化见 §2.4：170 码四元组 + codes/params 并行下发 + validation.dsl.* 映射、英文态浏览器冒烟全英文**；现仅余 condition/loop 表达式求值错误（conditions.py 经 params.detail 透传）与运行时 wait 失败 5 码（WaitNodeFailure 通道））、业务数据与节点目录/模板元数据（D13） | §2.3 |
| 组件/模板描述多语言 | Phase 2 模板库（14 D13；docs/57 未取回：节点目录 label/description、模板 name/description/标签在英文态仍显中文，属业务/元数据豁免） | 04 §28 第 3 条 |
| 暗色模式 | 有需求时按 §3.4 扩展点实施 | §3.4 |

## 5. 与其他文档的关系

- 需求源：04 §补充项 28（冻结正文，不改写）。
- 选型记录：10 文档 §1 决策表（T7）+ §4 变更记录（i18n 库；token 零新依赖方案）。
- 目录落点：09 文档前端树 `locales/`、`theme/tokens.ts`（待落码标注）。
- 触发缓做：14 文档 D12（i18n 落码；docs/57 部分取回翻译与切换、i18next 本体仍缓做且不解除条目）/ D13（组件描述多语言）。
- 落地批次：docs/57（en-US 首批全量翻译 + 语言切换 + 发布协作/审批/属性面板组件抽取，2026-09-24 收口）。
