# i18n en-US 全量翻译 / 语言切换 UI / 发布协作组件补抽 v1 批契约设计

> 状态：**立项（docs-only，2026-09-24）**。AI 承接「一口气搞了」总授权，部分取回 docs/14 D12（i18n 库落码与 en-US 翻译）、D13（组件描述多语言，本批不含模板元数据翻译）。**零新依赖**：继续沿用 M12 自写零依赖 t()/useTranslation（接口对齐 i18next），**本批不换 i18next**（决策见 §3）。承接 docs/17（前端国际化与设计 Token 方案，唯一方案权威）、docs/33（i18n 续批四页抽取）。**不新增 ADR、不解除 D12/D13 缓做条目**（i18next 引入、模板/适配器元数据多语言、自然语言多语言仍缓做）。

## 0. 已核实的现状缺口（2026-09-24 对活代码）

1. **en-US 全空**：`frontend/src/locales/zh-CN/` 11 个 namespace 共 **776 个叶子键**全部填实（common 90 / dashboard 5 / editor 172 / monitoring 247 / memory 39 / connections 64 / channels 58 / approvals 24 / audit 20 / openapi 57 / demo 0）；`en-US/` 仅 monitoring.json 有 18 键（docs/52 告警外发卡片随批翻译），其余 10 个文件为 `{}`。
2. **切换运行时预留未启用**：`locales/index.ts` 的 `changeLanguage()` 已实现（写内存 + localStorage `atlas.locale` + 通知订阅），但模块初始值恒为 `DEFAULT_LOCALE`（**不从 localStorage 读回**，刷新即丢）；无任何切换 UI 入口；`App.tsx` 三处 `ConfigProvider` 只注入 `theme`，**未注入 antd locale**（DatePicker/弹窗内置按钮/Pagination 等内置文案不随语言变）。
3. **四个组件仍硬编码中文**（docs/33 明确留后续批）：
   - `components/release/ReleaseModal.tsx`（476 行，发布门禁弹窗，约 40 处文案：结果列/回放终态/子图体检/配置差异/历史报告等）；
   - `components/release/RolloutModal.tsx`（485 行，灰度发布弹窗，约 50 处：三段分桶标签/路由规则/门控表/模拟事件等，含 `STRATEGY_LABELS` 5 个硬编码 map）；
   - `components/feedback/FeedbackButton.tsx`（101 行，约 12 处：反馈按钮/弹窗/类型选项/占位符）；
   - `components/approval/CardRenderer.tsx`（153 行，约 6 处：审批说明/同意/拒绝/卡片降级提示/必填门控）。
4. **守护测试基于「en-US 空骨架」**：`locales/__tests__/i18n.test.ts` 有 4 组 `falls back to zh-CN ... under the empty en-US skeleton` 用例断言切 en-US 后仍回退中文（common/dashboard/editor/monitoring/memory），翻译落地后这些断言必须演进（见 §5）。

## 1. 范围与非目标

**范围（四项）**：

1. **en-US 全量翻译**：11 个 namespace 与 zh-CN 结构一一对齐（含本批新增键），英文 UI 外壳完整可用。
2. **语言切换运行时启用**：模块初始化从 localStorage 读回；UserBadge 账户菜单（登录后）与 Login 页（未登录第一屏）两个切换入口；App.tsx 三处 ConfigProvider 注入联动的 antd locale。
3. **四组件补抽**：ReleaseModal/RolloutModal 文案入 editor namespace 的 `release.*`/`rollout.*` 子树；FeedbackButton 入 common 的 `feedback.*`；CardRenderer 入 approvals 的 `card.*`；zh-CN/en-US 双语同批填实。
4. **测试演进与守护**：i18n.test.ts 空骨架回退用例改写为「en-US 有翻译返英文、未翻译键仍回退中文」；新增 zh/en 结构对齐静态守护、切换运行时测试、组件抽取测试。

**非目标（仍缓做，不解除 D12/D13）**：

- **不引入 i18next/react-i18next**（决策 §3）；不做 namespace 懒加载、复数规则（plural）、日期/货币 Intl 格式化（本批数字/单位沿用现有字符串拼接形态）。
- **不做 navigator.language 自动探测**（决策 §3）：默认语言恒 zh-CN，只尊重用户显式选择。
- **后端错误码不接 i18n**：后端中文 detail（含 AUTH_* 结构化 code 未补批次，docs/17 §2.4 第一批债）原样上屏；本批不做后端改动。
- **不翻译业务数据**（docs/17 §2.5）：退款原因、LLM 决策 reason、订单号、Graph JSON、节点目录 label/description、模板 name/description、后端枚举（read/write/financial/administer、SHADOW_DRY_RUN、kind 名、P50/P95、HTTP 状态码等）原样展示。
- **不翻译模板/适配器元数据**（D13 本体，随模板库多语言批次）；不做自然语言多语言（docs/17 §2.6，LLM 层）。
- demo namespace 仍为空（无引用键）。

## 2. 翻译契约

### 2.1 结构与键

- en-US 每个 json 与 zh-CN **同路径、同叶子键集合**；嵌套层级一致；值为英文字符串。
- 插值变量逐字保留 `{{name}}`（变量名不翻译，如 `{{count}}`、`{{graphId}}`）；译文给齐变量后不得残留 `{{...}}`（教学语法 token 除外，见 §2.3）。
- 标点：英文用半角标点 + 英文空格习惯；省略号统一 `…`（与 zh 版风格一致）或英文 `...`——本批统一用 `...`（英文惯例），按钮/标签不带句号，提示句可带句号。
- 品牌/专名保留表（docs/17 §2.5 既判，本批扩展）：**Atlas、Graph、Loop、Harness、OODA、SSE、PG/Postgres、HTTP、JSON、API、REST、LLM、LiteLLM、DSL、CRUD、Mock、dry-run、canary、stable、candidate、rollout、promote、P50/P95、Trace、webhook、SMTP、IM、OAuth2、OpenAPI、HMAC、TTFB、kind、tag、id、key、v1/v2/v3**（版本号）不译；产品名「Atlas 运营体编排平台」译为 `Atlas Operations Orchestration Platform`（brand.appName）。
- 角色 viewer/operator/admin 译 `Viewer`/`Operator`/`Admin`；租户/用户界面词正常翻译（tenant、user）。

### 2.2 各 namespace 翻译口径

- **common**：brand/nav/auth/users/button/status/error 全套；登录种子账号提示（seedHint/seed.tenantA/B/accountLine）为 Demo 专用，照译但保留 admin-a/admin123 等字面值；`auth.login.seed.accountLine` 插值结构 `{{user}} / {{pass}} ({{role}})`。
- **editor**：编辑器外壳/画布/左栏/属性面板/调试台 + 新增 release.*/rollout.*；画布分支标签（branchDefault「默认分支」→ `Default branch`）、OODA 日志模板（log.*）照语义译；`log.toolHttpStatus` 译 `(HTTP {{status}})`；后端 debug reason 枚举（step/breakpoint/condition/exception）是 UI 标签，照译（单步→Step 等），未知枚举仍原样返值。
- **monitoring**：最大 namespace（247 键）；severity/状态/列名/规则表单/影子运行/静默/值班/Trace 全套；已有的 alertChannel 18 键英文为人工翻译成品，**保留不改**（若与新译文风格冲突仅做大小写/标点统一）；自定义规则 hint 中 `{{status}}`/`{{durationMs}}`/`{{failedCount}}`/`{{hasError}}` 为教学 token 保留。
- **memory/connections/channels/approvals/audit/openapi**：照译；表单校验提示、空态、删除确认、message.* 成功失败提示全套；技术枚举值不译（connected 等 UI 标签译，后端 status 字面值不译）。
- **dashboard**：5 键（demo.title/description/cards.graph·loop·harness）；demo.title 含「电商退款自动化」译 `E-commerce refund automation`，Graph/Loop/Harness 专名保留。

### 2.3 教学语法 token 的处理

zh-CN 有两处文案故意携带不被插值解析的教学花括号（变量名含中文/点号，不匹配插值标识符正则）：

- editor `variables.hint`：zh 含 `{{变量路径}}`、`{{global.company_name}}`；**en 版改为英文教学示例 `{{variable.path}}`、`{{global.company_name}}`**（注意：`{{variable.path}}` 含点号，同样不匹配插值标识符正则 `[A-Za-z_$][\w$]*`，按字面保留；`{{global.company_name}}` 同理含点号）。
- editor `decision.promptTemplateLabel`：zh 含 `{{路径}}`；en 改 `{{path}}`——`{{path}}` 是合法插值标识符，调用处不传 path 变量时按现有 interpolate 逻辑保留原样（缺变量保留占位），与中文版行为同构；测试不给 path 变量断言其字面存在。
- monitoring `custom.hint`：token 本就是英文标识符（`{{status}}` 等），en 版直接保留、仅翻译周围文字。

## 3. 关键决策（须同步 docs/08）

1. **继续零依赖自写实现，不换 i18next**。docs/17 §2.2 原规划触发时换 i18next；M12 起实际路线为自写 t()/useTranslation，且经 docs/33 四批抽取、11 namespace/776 键验证已满足需求（嵌套查找/插值/namespace/回退/订阅切换齐备）。本批英文落地后，i18next 的独有能力（懒加载/复数/Intl）仍无需求，换库只带来依赖与返工，故继续自写；i18next 引入保持缓做（D12 不解除），未来出现复数/懒加载硬需求时再换，组件侧 `t()`/`useTranslation()` 签名不变，仍零返工。
2. **不做 navigator.language 自动探测**。docs/17 §2.3 原注释「en 无翻译时自动探测会把 UI 变成 key」；本批翻译虽齐，但产品定位为 Phase 1 中文种子客户，自动探测可能使中文环境开发者的浏览器（常见 en-US navigator）误显英文。语言只由用户显式切换决定并持久化；自动探测留作出海批次。
3. **切换入口两处**：登录后 UserBadge 账户菜单（分隔线 + 「中文 / English」带勾选态）；未登录 Login 页（页脚同样的两项小菜单，保证英文使用者第一屏可切）。
4. **新增文案归属**：发布/灰度弹窗虽为 App 级组件，但唯一入口在编辑器工具栏（editor.header.publish/rollout 已在 editor namespace），归入 editor 的 `release.*`/`rollout.*` 子树，不新建 namespace；FeedbackButton 为全局组件归 common `feedback.*`；CardRenderer 归 approvals `card.*`。Namespace 类型枚举不变（不新增 namespace）。

## 4. 运行时切换实现契约

### 4.1 locales/index.ts

- 模块初始化增加 `readStoredLocale()`：try/catch 读 localStorage `atlas.locale`，值属于 SUPPORTED_LOCALES 则用之，否则 DEFAULT_LOCALE；`let language = readStoredLocale()`。
- `changeLanguage` 逻辑不变（已写 storage）；导出不变。
- 新增 `useAntdLocale()` hook：`useTranslation()` 订阅当前语言，返回 antd locale 对象（`zhCN`/`enUS`，静态 import 自 `antd/locale/zh_CN`、`antd/locale/en_US`，antd 6 路径）。放 index.ts 会让非 UI 的纯函数测试拉入 antd——**改为新建 `frontend/src/locales/antdLocale.ts`**（只依赖 react/antd + locales 的 getLanguage/subscribe，或直接用 useTranslation 取 language），index.ts 保持无 antd 依赖。
- Login/UserBadge 切换菜单直接用 `useTranslation().i18n`（language + changeLanguage）。

### 4.2 App.tsx

三处 `<ConfigProvider theme={antdTheme}>` 全部加 `locale={antdLocale}`（邮件深链外壳、Login 外壳、主外壳）；antdLocale 来自 `useAntdLocale()`（App 组件内调用一次）。

### 4.3 UserBadge / Login

- UserBadge Dropdown items：账户组两项之后加分隔线 `{ type: 'divider' }`，再两项 `{ key: 'lang-zh-CN', label: '中文' }`、`{ key: 'lang-en-US', label: 'English' }`，当前语言项加勾选（菜单 item 支持 icon 或 label 内 CheckIcon；用 label 文本前缀 `✓ ` 最轻量，或 antd menu 的 `icon`——本批用 label 内联，当前语言项 label 为 `✓ 中文`/`✓ English`，键 common `language.switchToZh/switchToEn` 与 `language.currentMark` 不设，直接用固定语言原名展示：语言名本身不翻译，中文永远显示「中文」、English 永远显示「English」）。onClick 调 changeLanguage。
- Login 页脚右上加同款 Dropdown（幽灵小按钮，文案为当前语言原名：中文 / English，用 common `language.label` 不需要——按钮直接显示 `🌐 中文`/`🌐 English`，不加 emoji（项目无 emoji 惯例），改用 antd Globe 图标 `GlobalOutlined`，@ant-design/icons 已是依赖；按钮文字为语言原名）。
- 菜单/按钮可访问性：与现有 Dropdown 同款 ghost Button size small。

## 5. 测试契约

### 5.1 i18n.test.ts 演进

- 4 组「empty en-US skeleton 回退中文」用例改写：
  - common 组：切 en-US 后 `auth.login.submit` 断言英文值（`Sign in` 或实际落词，以译文为准），并新增「**未翻译假键**在 en-US 下仍回退 zh-CN/common 或返回 key」断言（用不可能存在的键如 `__nonexistent__.x` 验证回退链不回归）；
  - dashboard/editor/monitoring/memory/connections/channels 组：切 en-US 后断言各 namespace 代表键返回**英文**（不含汉字、不等于 key），教学 token 用例 en 版断言 §2.3 的英文 token；
  - 保留「zh-CN 为默认」「非法语言忽略」「缺键返回 key」「defaultValue」「unknown namespace 前缀」等纯机制用例不变。
- 新增静态守护用例（读 JSON 比对，不依赖渲染）：
  - **结构对齐**：每个 zh-CN namespace 的叶子键集合 === en-US 叶子键集合（含本批新键），不一致时打印缺失/多余键失败；
  - **en-US 无汉字**：所有 en 叶子值不得含 CJK 汉字（豁免清单：无——品牌/示例均不含汉字；若确有保留项显式列豁免并注释）；
  - **插值占位对齐**：zh 值中出现的 `{{x}}` 标识符集合（按现有正则匹配的合法插值）必须 === en 值中的集合（防漏译/错改变量名）；教学 token（含点号/中文，不被正则匹配）不参与此校验。
- localStorage 读回用例：`changeLanguage('en-US')` 后重新调用读回函数（或模拟模块存储键）断言持久化；读回函数导出为纯函数 `readStoredLocale(storage)` 便于单测（非法值/缺省/合法三例）。

### 5.2 组件测试

- UserBadge 测试（若现有 userBadge 测试则增补，否则新建 `__tests__/userBadge.test.tsx`）：渲染 → 打开账户菜单 → 点 English → `getLanguage()` 为 en-US、localStorage 持久化；点中文切回。
- Login 测试：页脚语言按钮存在，点击切换生效（与上同断言）。
- ReleaseModal/RolloutModal 现有测试（release 相关 test 文件）：默认 zh-CN 渲染不断言具体中文文案的用例保持；若有硬编码中文断言改为键解析后的稳定断言（优先断言不随语言变的技术值：v 版本号、Tag color、列 dataIndex）；新增 en-US 渲染快照级断言（代表标题/按钮文本为英文）。
- FeedbackButton/CardRenderer 同例：zh 默认中文、en 下英文的关键文案断言。

### 5.3 门与冒烟

- 前端三道门：`pnpm vitest run`（只许增测）、`pnpm run lint`（0 error）、`pnpm build`（tsc+vite 过）。
- 后端零改动：不跑全量后端门（无后端改动）；收口时若动过任何后端文件则全量复跑。
- 浏览器冒烟（bu plane）：登录页切 English → 登录 → Dashboard/Editor/Monitoring/Memory 全英外壳巡检（截图 en-login/en-dashboard/en-editor/en-monitoring）；切回中文巡检无回归（截图 zh-after）；重点看 ReleaseModal（发布门禁弹窗）、RolloutModal（灰度弹窗）、FeedbackButton、审批 CardRenderer 英文态；AntD 内置文案（如 Modal 按钮、Table 空态）随 en_US。

## 6. 验收用例（docs/13 回填 U610–U625）

| 编号 | 验收点 |
|---|---|
| U610 | en-US/common（含 brand/nav/auth/users/button/status/error/feedback）翻译，结构对齐 |
| U611 | en-US/dashboard + editor（含 release/rollout 子树）翻译，结构对齐 |
| U612 | en-US/monitoring（247 键，保留已有 18 键成品）翻译，结构对齐 |
| U613 | en-US/memory/connections/channels/approvals（含 card）/audit/openapi 翻译，结构对齐 |
| U614 | 静态守护：zh/en 叶子键集合一致、en 无汉字、插值变量集合一致 |
| U615 | localStorage 语言读回（合法/非法/缺省三例）+ changeLanguage 持久化 |
| U616 | UserBadge 账户菜单语言切换（勾选态、即时生效、持久化） |
| U617 | Login 页脚语言切换入口（未登录可切） |
| U618 | App.tsx 三处 ConfigProvider 注入 antd locale 并随语言联动 |
| U619 | ReleaseModal 文案抽 editor.release.*（zh/en 双语） |
| U620 | RolloutModal 文案抽 editor.rollout.*（含 STRATEGY_LABELS，zh/en 双语） |
| U621 | FeedbackButton 文案抽 common.feedback.*（zh/en 双语） |
| U622 | CardRenderer 文案抽 approvals.card.*（zh/en 双语） |
| U623 | i18n.test.ts 空骨架回退用例演进 + 回退机制不回归（假键守护） |
| U624 | 浏览器 en/zh 双语冒烟截图（5+1 张） |
| U625 | docs 收口：17/14/08/00/13 + CHANGELOG + handoff 回填 |

## 7. 原子提交规划（不 push）

1. `docs(i18n): contract en-US translation and language switcher batch (docs/57)` — 立项（本文件 + 08/14/00/handoff/CHANGELOG）。
2. `feat(i18n): translate all namespaces to en-US with parity guard tests (docs/57)` — 全量 en-US + 静态守护 + i18n.test 回退用例演进。
3. `feat(i18n): persisted language switcher and AntD locale linkage (docs/57)` — localStorage 读回 + UserBadge/Login 入口 + antdLocale + App.tsx。
4. `feat(i18n): extract release, rollout, feedback and approval card copy (docs/57)` — 四组件补抽（zh/en 同批）。
5. `docs(i18n): close out en-US first batch and language switcher (docs/57)` — 收口回填（17/14/08/00/13 + CHANGELOG + handoff）。

> 门数字以落码后实跑为准，回填 handoff/docs/13，禁止预估。
