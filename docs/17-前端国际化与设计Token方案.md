# Atlas 前端国际化（i18n）与设计 Token 方案

> **来源**：工程推导文档。需求依据为 [04-组件设计-编辑后台.md](04-组件设计-编辑后台.md) 十、补充项 28「多语言支持」三条（界面多语言中/英切换可扩展、自然语言多语言、组件描述多语言）；01-08 正文为冻结的唯一事实源，本文只做工程化方案，不改写需求。
> **状态**：方案已定（2026-09-13，2026-09-16 补 D7 多租户认证界面契约）。**设计 Token 等价替换已于 2026-09-13 落码**（`theme/tokens.ts` + `setup.ts`，三页面主体零视觉差异；2026-09-16 §3.5 收尾清单五项已全部清零，`src` 下仅 tokens.ts primitive 定义含 hex）；i18n 库按触发条件引入（见 §4 落地节奏与 [14-缓做事项登记表](14-缓做事项登记表.md) D12/D13）。**2026-09-20 M12 先行落地零依赖文案抽取骨架**（`frontend/src/locales/` 自写 t()/useTranslation 对齐 i18next 签名 + zh-CN/common 样板 + 登录页/UserBadge 接线，不装 i18next、不做切换 UI、不翻译 en-US；触发时零返工替换为 i18next，见 §4）。**2026-09-24 docs/57 完成 en-US 首批全量翻译与语言切换**（10 个非空 namespace 全译、localStorage 持久化切换、UserBadge/Login 两入口、antd locale 联动、发布/灰度/反馈/审批/属性面板/表单/画布全接线；决策继续不引入 i18next、不做 navigator 探测；i18next 与元数据多语言仍缓做于 14 D12/D13，详见 docs/57 与本文 §4）。**2026-09-24 取回 i18n 第一批债**：后端认证端点结构化错误码（AUTH_*，detail 为 {code,message}）+ 前端 L1 校验诊断全量 i18n（validation namespace 82 键、语言切换全量重算）；剩余 Graph DSL 422 列表与元数据 D13 仍缓做。**〔2026-09-25 收口更正〕"剩余 Graph DSL 422 列表"已不成立**：该批码化实际已覆盖全量——`src/atlas/graph/` 可抛码 197 个，前端 `locales/*` 有 191 个键，余 6 个是 `SUCCESS`/`FAILED`/`SIMULATED`/`SHADOW_DRY_RUN`/`INVALID_PARAMETER`/`UNKNOWN` 动作与参数枚举、非用户可读消息不必译；解析承载在 `frontend/src/lib/apiClient.ts:53` 的 `t('dsl.<CODE>', { ns: 'validation' })`（本仓从未有过 `graphErrors.ts`）。**同次测量新暴露的缺口才是本条余部**：13 个 namespace 里唯独 `validation` 未纳入 `i18n.test.ts` 的 `PARITY_PAIRS`（现 12 项），"新增 422 码漏配英文键"无门可挡；直接纳入会当场红一处真漂移（`dsl.SUB_INPUT_VALUE_REQUIRED` 的 zh `{{路径}}` vs en `{{path}}` 插值标识不一致），两步收口见 14 D12 的 2026-09-25 注记与 08 §八「下一批候选」B 组首行。**〔已收口 2026-09-25：`776b62c` 文案＋`1a07582` 守护〕**`776b62c` 改文案、`1a07582` 扩守护，13 个 namespace 至此全覆盖——**新增 422 码却漏配英文键，从此会当场红在 i18n 门上**。
> **〔2026-10-10 主题打磨批〕** 在 §3.5 等价替换完成的基础上做**有意视觉打磨**（非零视觉变化）：antdTheme 补全 token 层（colorBgLayout/colorBgContainer/colorBorder/colorText/colorLink，对齐 semanticTokens 现状）＋ borderRadius 6/borderRadiusLG 10/fontSize 14/controlHeight 32/40/24；components 层 Card.boxShadowTertiary、Modal.borderRadiusLG 12、Button.fontWeight 500。新增 semantic token：`shadow-node-hover`、`shadow-card`、`shadow-card-hover`（index.css 节点/卡片 hover 阴影消费）。主色 `#1677ff` 不变（避免节点光晕/全仓涟漪）。消费方：仅 App.tsx 引用 antdTheme，无测试依赖。原子 `71f0164` feat(theme)。
> **〔2026-10-10 画布自适应修复〕** 同批顺带修复两个真实既有缺陷（与「UI 随窗口自适应」同族）：① 编辑器内层 antd Layout 未被外层撑开，`.react-flow` 容器塌陷至 65px，演示图节点被压缩不可见——`index.css` 补 `.editor-layout > .ant-layout { flex:1; min-height:0 }` ＋ `.editor-content .canvas-panel { height:100% }`；② 编辑器先挂载空图、loadGraph 异步填充后节点停在原始坐标（viewport transform 留 none、位于容器外被裁剪）——`FlowCanvas.tsx` 在节点首次出现时显式 `fitView({padding:0.2,duration:150})`（rAF＋200ms 双保险）。原子 `23353ed` feat(frontend)。浏览器实测：画布 65→1165px、3 节点适配可见、节点圆角 10px 计算样式确认。
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
  - **2026-10-06 打包 BK（runtime.json vs channels.json 两命名空间边界注记）**：渠道错误码随一码一答案拆码后，前端消费它们的有**两个互不重合的键空间**——① 经 `_channel_http_error` 结构化出体的 4xx `detail.code`（含 register/unregister remote-webhook）走 **runtime.json**（9 条 HTTP 面码，受 U1162 zh/en 双份与 U1149 目录成员守护）；② `GET /api/channels/{id}/remote-webhooks` 授权失败的**非异常**特殊返回 200 `{items:[],error:"CHANNEL_UPSTREAM_UNAUTHORIZED"}` 走 **channels.json** 的 `remoteWebhooks.error.<code>`（`WebhookSubscriptionsModal` 消费，不进 runtime 目录）。同一个 `CHANNEL_UPSTREAM_UNAUTHORIZED` 因此在两个命名空间各有一键、文案各自独立：runtime 模板带 `{{status}}`（管理端点的完整诊断），channels.json 是面向用户的「请重新授权」短句。拆码时两档 zh/en 必须同步改键（旧 `CHANNEL_UNAUTHORIZED` 键不留），这由 U1162（runtime 侧）与前端 apiClient/i18n 守护（channels 侧键齐）分别钉住。仅节点产出的 5 条 shape 码两个空间都不进（docs/57 §2.5）。
- **2026-10-05 打包 AW＝A-4 的可译半边收口＋"识别"从第二份真相改成查目录**（用户「继续推」；承接 docs/89 §12 A-4；实测量：全仓 **211 条**结构化错误码字面量，收口后 **185 条有目录文案／26 条在带理由的豁免表上／0 条无归属**）：
  - **补了 10 条码的 zh/en 文案**：运行期 6 条（`SUBGRAPH_SUSPEND_UNSUPPORTED`、`OPENAPI_DUPLICATE`、`OPENAPI_NO_IMPORTABLE_OPERATION`、`WAIT_ALREADY_SIGNALED`、`WAIT_TOKEN_NOT_FOUND`、`COND_INVALID_RANGE`）＋编译期 4 条（`COND_LLM_MODEL_NOT_STRING`、`COND_LLM_MODEL_TOO_LONG`、`LOOP_PARALLEL_IN_BODY`、`PAR_SUSPEND_IN_REGION`）。
  - **根因不是"没人写文案"，是识别用了一张前缀白名单**：`runtimeError.ts` 曾以 `^(COND_|WAIT_|LOOP_|FOREACH_|LLM_|RUNTIME_)` 判"能不能译"，于是 `SUBGRAPH_*`／`OPENAPI_*` 这些码**补了文案也永远不生效**（A-4 里有 5 条就是这么"看起来没救"的）。现改为**目录成员＝可译**（读 `zh-CN/runtime.json` 的键集合，zh/en 键一致由 `i18n.test.ts` 的奇偶守护钉住），第二份真相消失。**改判带来的可见后果**：`isRuntimeErrorCode('COND_FOO')` 从 `true` 变 `false`——编造的码本来就没有文案，判 true 只是让下一位误以为"补了键就有英文"。
  - **占位符守卫收进唯一入口**：节点结果的 `expressionErrorCodes` 通道只发码不发 params，含 `{{low}}` 的模板必然填不满；旧代码只在 `resolveExpressionErrors` 里局部防，现收进 `resolveRuntimeError`（填不上就回退后端中文原文，绝不把 `{{low}}` 上屏）。**这条是我自己的新测试抓出来的**，不是我预先想到的。
  - **顺手补了一个通道缺陷（U1148）**：`SubgraphSuspendUnsupported` 只有 SSE 承载，同步 `/run` 没有异常处理器 ⇒ 运营拿到的是**没有 code 的裸 500**，"把节点移到图顶层"这句可执行下一步只存在于日志里。现与 `LLM_DECISION_UNAVAILABLE` 同形返回结构化 500＋`nodeId`。
  - **剩余 26 条码不译，理由分三组逐条写进 `tests/test_error_code_channels.py` 的豁免表**：**17 条**只出现在工具／适配器结果的 `error_code`（含 `StructuredError("X", …)` 形状——**这一族是加完形状才被发现漏计的**，docs/89 当初"19 条"的口径正是从它来的），**4 条**是已入库的 webhook 死信原因码；这两组按 §2.5"节点产出＝业务数据不译"是**长期口径**，不是待办。剩下 **5 条一码多话**需先拆码或补 params → 登记 docs/14 **D53**。守护同时断言两件事：豁免表**不许过期**（表上的码若已不再发出必须删条目——我第一次填表凭印象写了 6 条 `CHANNEL_*`／`EGRESS_*`，它们并不从这些形状发出，被这条守护当场打回），且三组之间**不许重叠**（一条码只该有一个「为什么不译」的理由）。
  - **U1149/U1150 是这批的长寿产出**：新增结构化码却忘了配文案 ⇒ 守护点名；反向门把缺陷植进**合成源码片段**（不动真文件）并断言枚举器**五种**发射形状都看得见（`detail={"code":…}`／异常类属性 `code = "…"`／dsl 的 `code="…"`／`StructuredError("X", …)`／`{"error_code": "X"}`）。
  - 零迁移、零新依赖、无新 ADR；两档 locale 键集合实测一致（`runtime` 29／`validation.dsl` 177）。
- **2026-10-05 打包 AX＝把 params 送回节点结果通道**（用户「继续推」；docs/14 **D53 的 ②** 半边收口；用例 docs/13 **U1153–U1155**）：
  - **AW 记下的那条结构事实现场修掉了**：`ConditionEvalError` 自 docs/60 G1 就带 `code`＋`params`，但节点结果只并行下发了 `expressionErrorCodes`——params 在 HTTP 边界被丢掉，于是**必须插值**的模板（`COND_TYPE_MISMATCH` 的 `{{detail}}`、`COND_DIVIDE_BY_ZERO` 的 `{{op}}`）在英文态永远只能回退后端中文原文。现在 loop 与 foreach 两条通道都并行下发 `expressionErrorParams`（按下标对齐，无参的码发 `{}` 而不是缺位），前端 `resolveExpressionErrors` 用第三个参数接住。
  - **等长由装配期保证，不靠约定**：新增 `_expression_error_channels()`，`errors/codes/params` 三组长度不一致直接 `ValueError`。错位的后果是第 n 条明细静默读到第 n−1 条的参数＝运营看见另一句话，比崩难查得多。**反向门 U1155 证明它会红**：把那条检查临时改成 `if False`，U1155 立刻失败；改回即绿。
  - **写测试时纠正了自己的假设**：原以为 `randint(9, 1)`（`COND_INVALID_RANGE`）走这条通道，实测它在 **dsl 校验期**就变成 `GraphValidationError`→422，根本到不了运行结果；运行态真带 params 的是**依赖运行数据**那一类（`!"abc"` ⇒ `COND_TYPE_MISMATCH` 的 `op/expected/actual`）。U1153 因此按真实可达形状断言，而不是按我想的形状。
  - **同一批看见的另一个缺口没有顺手做**：condition 节点的表达式诊断连 code 都没有（`_execute_condition` 收的是 `f"分支 {label}：{exc}"` 纯中文拼接），要译必须先定"分支前缀＋内层明细"的组合文案形状＝契约设计，不是补词条 ⇒ 登记 docs/14 **D54**（避免在文案批里造第二份真相）。
  - 零迁移、零新依赖、无新 ADR；前端 `x-outputSchema` 的 loop 声明同步补 `expressionErrorParams`（`nodeSchemas.test.ts` 逐字段钉住），旧持久化运行结果没有该键时前端按"无参"回退。
- **2026-10-05 打包 BC＝把 D53 的 ① 那一"契约半边"落掉：五条一码多话粗码拆成具体码，并把"一码多话"从手抄清单变成机检**（承接 docs/14 **D53 ①**；用例 docs/13 **U1160–U1164**）：
  - **五条粗码全仓零发射**（`INVALID_PARAMETER`／`NOT_FOUND`／`OPENAPI_INVALID_CREDENTIAL`／`OPENAPI_INVALID_DOCUMENT`／`WAIT_EVENT_PAYLOAD_INVALID`），拆成 **46 条**具体码——29 条是契约 D-1 列举的，**另 15 条是契约自己没数全的**：`OPENAPI_INVALID_DOCUMENT` 经 `UnsupportedSchema` 一直把 `OPENAPI_REF_*`／`PARAM_*`／`BODY_*`／`KEYWORD_*`／`TYPE_*` 族压在同一个码下（契约只记了 4 条答案）；另有 2 条来自机检一上就抓到的 `OPENAPI_DUPLICATE`（`OPENAPI_SPEC_ALREADY_IMPORTED`／`OPENAPI_RESTORE_CLASH`）。
  - **目录面**：`runtime.json` **29→57 键**（新增 29 条、删除旧的 `OPENAPI_DUPLICATE` 1 条）；只出现在节点产出的 **17 条**进 `TOOL_OUTPUT_ONLY` 豁免表（§2.5 不译）；带插值的 5 条同时补了 params 通道（否则英文态模板填不满会回退后端中文原文）；`COARSE_CODE` 那 5 条豁免**清空为 `set()`**。
  - **真产出是 U1160**：按形状抓 `(码, 消息字面量)` 去重，同码 ≥2 条不同字面量即红——**将来谁再写一条多话的码，守护会红**，不再靠手抄清单。U1163 反向门证明枚举器看得见；U1164 把枚举器新量出的 **27 条**盲区码钉成只许缩小的冻结桶（docs/14 **D55**）。
  - **枚举器盲区有三层，本批补齐**：`XxxError("CODE", …)` 形状（浮出 27 条）之外，`XxxSchema("CODE", …)`（`UnsupportedSchema`，类名不以 `Error` 结尾）与带类型标注的默认值 `code: str = "CODE"` 此前都看不见——不补后两层，本批自己拆出的 **16 条**码就落在守护之外，"把一码多话变成机检"对本批的码是空话。
  - 零迁移、零新依赖、无新 ADR；`isRuntimeErrorCode` 仍是"目录成员"判定（本批只加键、不改判据）。`A-4` 的 ① 由此闭合，剩 ②③（长期口径不译）与 D54。

- D7 认证端点（`/api/auth/login`、`iam/deps.py` 的 401/403）**2026-09-24 已补结构化 code**（第一批债取回）：login 用户/口令错 401=`AUTH_INVALID_CREDENTIALS`、账号停用 403=`AUTH_ACCOUNT_DISABLED`、缺凭证 401=`AUTH_UNAUTHENTICATED`、角色不足 403=`AUTH_FORBIDDEN`；响应 detail 形状为 `{"code","message"}`（message 保留中文作日志/默认），前端 `apiClient.resolveErrorMessage` 按 code 映射 `error.auth.*`。**跨租户 404 故意不区分「不存在/越权」（防资源泄漏），不补 code、保持现状**，故原建议的 `AUTH_NOT_FOUND` 未落码。**2026-10-05 打包 AV（docs/95）同族再加一码**：`AUTH_PASSWORD_CHANGE_REQUIRED`（403，未满足首登强制改密时由 `iam/deps.require()` 发出，排在角色门之前）→ `error.auth.passwordChangeRequired` 两档齐；同一批把 `users.forceChange.{title,body,submit,signOut}` 落进 `common`（PARITY 守护自动覆盖）。**为什么这批必须连文案一起落**：A-4 量过的缺陷面＝"新错误码只在后端存在、前端按默认回退打印中文原文"，英文态就露中文——本仓现在由 `tests/test_password_rotation_gate.py::test_u1146_code_string_is_shared_not_forked` 机检（回读 `apiClient.ts` 与两档 `common.json`，缺一红）。

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

### 3.7 主题打磨批（2026-10-10，落码完成）

在 §3.5「零视觉变化等价替换」之后的有意打磨，**允许**视觉差异（目标：核心面质感提升，非改版）：

| 层 | 变更 | 落点 |
|---|---|---|
| token | antdTheme token 层补全（colorBgLayout/colorBgContainer/colorBorder/colorText/colorLink）、radius 6/10、fontSize 14、controlHeight 32/40/24 | `tokens.ts` |
| component | Card.boxShadowTertiary、Modal.borderRadiusLG 12、Button.fontWeight 500 | `tokens.ts` antdTheme.components |
| semantic | `shadow-node-hover`、`shadow-card`、`shadow-card-hover`（RGBA 黑阴影） | `tokens.ts` primitive/semantic |
| CSS | 节点面板项圆角 8＋hover 阴影/位移/描边；atlas-node 圆角 10＋分层阴影＋hover 提升；header 圆角 7 7 0 0；ant-card transition＋hover 阴影 | `index.css` |
| 空态 | 画布 `nodes.length===0` 居中提示（⊕＋i18n `editor.canvas.emptyHint`，zh/en） | `FlowCanvas.tsx`＋locales |

**保持不变的硬约束**：主色 `#1677ff`；不引入暗色模式；零新依赖；不改 primitive 数值（仅新增）。验证：vitest 972/2 零回归、oxlint 0/0、tsc 0、build 过、浏览器冒烟（节点圆角/阴影计算样式、画布撑满、节点适配可见）。

- `cd frontend && pnpm build` 通过、`pnpm test` 全绿。
- 浏览器对比替换前后三个页面：编辑器（含节点运行中/完成态）、Dashboard、`/demo/shop` 模拟控制台，无视觉差异。
- `grep -R "#[0-9a-fA-F]\{3,6\}" frontend/src` 仅剩 `tokens.ts` 内的 primitive 定义。

### 3.8 UX 走查与微改进批（2026-10-10，落码完成）

全流程 UX 走查（登录→Dashboard→各功能页→编辑器→运行→错误校验，浏览器实测）结论：**缺陷级硬伤基本没有**（页面切换 loading、空态文案、错误校验分层、引导说明均到位）；检出并闭合 5 项可做项：

| # | 项 | 类型 | 落点 |
|---|---|---|---|
| 1 | 等待页返回按钮只有「←」图标无文字 → 「← 返回」（`t('common:button.back')`，common 顶层 button 补齐 `back` 键 zh/en——此前 `back` 仅存在于 audit/users 各自字典，顶层无此键） | 缺陷级·可访问性 | `Waits.tsx`＋`common.json` |
| 2 | 审计日志筛选空态「当前没有符合条件的审计事件」实测确认（既有 `audit.empty`＋`locale.emptyText`，无需改码） | 缺陷级·验证 | — |
| 3 | 运行完成后无轻提示 → `message.success(t('log.runComplete', {status}))`（App.tsx 主分支包 antd `<App>`，Editor 经 `App.useApp()`；复用既有 `log.runComplete` 键 zh/en） | 品味级·反馈 | `App.tsx`＋`Editor.tsx` |
| 4 | Dashboard Graph/Loop/Harness 三卡片静态 → `hoverable`＋`onClick` 跳转（Graph→编辑器、Loop→反思进化、Harness→API 导入）＋extra「进入 →」`demo.cards.enter` zh/en | 品味级·可发现性 | `Dashboard.tsx`＋`dashboard.json` |
| 5 | **走查新发现**：编辑器无返回工作台入口（只能刷新页面）→ header 左加「← 返回」（`onBack` prop，App 传 `setPage('dashboard')`） | 缺陷级·路径断裂 | `Editor.tsx`＋`App.tsx` |

**验证**（浏览器实测，admin-a）：等待页「← 返回」、审计筛选空态文案、运行完成 toast「运行结束：refunded」、三卡片 hover/跳转（Graph→编辑器实测）、编辑器「← 返回」回 Dashboard。门：前端 vitest **972/2**（零回归）、oxlint **0/0**、tsc **0**、build 过（仅既有 chunk>500kB warning）；守护门 8 passed。零新依赖/零迁移/无 ADR。

## 4. 落地节奏

| 事项 | 时机 | 依据 |
|---|---|---|
| 设计 Token 等价替换（tokens.ts + CSS 变量 + AntD theme） | ✅ 2026-09-13 已落码（本节 §3.5） | §3 |
| 零依赖文案抽取骨架（自写 t()/useTranslation 对齐 i18next 签名 + zh-CN/common 样板 + 登录页/UserBadge 接线，余 namespace 与 en-US 空对象占位） | ✅ 2026-09-20 M12 落码收口（`9f1963f`/`a894fa7`，U212–U218，前端 495/40 文件，浏览器两截图 m12-login/userbadge；不装 i18next，触发时零返工替换） | §2.3 |
| dashboard namespace 首批生产接线（登录后落地页 Dashboard：demo.* 走 dashboard ns，品牌名/主导航入 common 经 `common:` 前缀跨取；Graph/Loop/Harness 技术专名按 §2.5 不译，en-US/dashboard 仍空） | ✅ 2026-09-20 M12 续批落码（U219–U221，前端 498/40 文件，截图 m12-dashboard；editor/Monitoring/Memory 大页面留后续分批） | §2.3/§2.5 |
| editor namespace 抽取（编辑器大页，分两原子：框架/画布/左栏面板＋属性面板/调试台；纯展示文案全抽，节点类型/DSL/OODA/SSE 等技术专名按 §2.5 不译，后端中文 detail 原样上屏不 key 化） | ✅ 2026-09-21 docs/33 批 1 落码（`97d61dc`/`ad12f98`＋`bf9cdb6`/`888daf8`，en-US/editor 仍空 `{}`） | §2.3/§2.5 |
| monitoring namespace 抽取（监控告警页：指标/规则/告警/适配器调用/Trace 时间线/影子运行/静默/值班全域；P50/P95、read/write/financial/administer、SHADOW_DRY_RUN、kind 名等专名不译） | ✅ 2026-09-21 docs/33 批 1 落码（`0c299ef`/`4bf7c7f`，en-US/monitoring 仍空 `{}`） | §2.3/§2.5 |
| memory namespace 抽取（记忆/长期上下文页：记忆列表/kind 标签/置信度/scope/CRUD 表单；技术专名不译） | ✅ 2026-09-21 docs/33 批 1 落码（`3445576`/`f11dad5`，en-US/memory 仍空 `{}`） | §2.3/§2.5 |
| schedules namespace 首批生产接线（控制台「定时调度」页：表头/开关/立即运行/UTC 与不补跑口径；画布 cron 字段的语法提示与预演文案）| ✅ **2026-09-26 打包 N 落码**（docs/68 §7）：zh/en 双档同时建、纳入 `PARITY_PAIRS` 三条守护（同键集／en 无汉字／插值标识符一致）；`validation.dsl.NODE_TRIGGER_CRON_INVALID` 两档补齐，**英文态刻意不断"无汉字"**——cron 只有一份解释器（后端），其解释文本天然中文，复刻第二份解析器或把弱译文冻进 locale 都比混语言更糟 | §2.5；docs/68 |
| i18n 库引入（i18next） | zh-CN 抽取与零依赖骨架 ✅（M12/docs/33）；**i18next 本体经 docs/57 决策 1 验证继续不引入**（自写 t() 经 11 namespace 全量翻译与切换验证够用），仍缓做于 14 D12：出现复数规则/namespace 懒加载硬需求时再换，组件侧 `t()`/`useTranslation()` 签名不变、零返工 | §2.2 |
| 完整英文翻译（en-US UI 外壳 + 切换运行时） | ✅ **2026-09-24 docs/57 部分取回落码**：10 个非空 namespace 全量英译（demo 仍空）、localStorage 持久化切换（不做 navigator 探测）、UserBadge 语言组菜单 + Login 页脚两入口、antd locale 三处 ConfigProvider 联动、发布/灰度/反馈/审批卡片/审批两页/7 个 Config/WaitConfig/ToolCallConfig/FormRenderer/nodeWidgets/widgets/画布/Problems 全接线；静态守护三例（zh/en 键集合一致、en 零汉字〔唯一豁免 common:language.zhCN〕、插值标识符集合一致）；U610–U625 全过（前端 662 passed/2 skipped、lint 0 error、build ✓、双语浏览器冒烟 18 截图）。**2026-09-24 第一批债进一步取回**：后端认证端点已结构化 code 化（§2.4，前端按 code 走 i18n、中文 message 仅作日志）；L1 校验诊断消息已全量 i18n（新建 `validation` namespace 82 叶子键 zh/en 对齐，`l1.ts` 用户可见中文清零；语言切换经 `engine.invalidateForLocale()` 清缓存并全量重算 L1/L2/L3，ToolCallConfig 参数诊断同步重算；英文态单测覆盖）。**仍显中文的层**：Graph DSL 422 中文错误列表（`graph/dsl.py`，几十条，另起小批，**2026-09-24 已码化见 §2.4：170 码四元组 + codes/params 并行下发 + validation.dsl.* 映射、英文态浏览器冒烟全英文**；现仅余 condition/loop 表达式求值错误（conditions.py 经 params.detail 透传）与运行时 wait 失败 5 码（WaitNodeFailure 通道，**已随 docs/60 G1 落 runtime namespace**））、业务数据与节点目录/模板元数据（D13）。**2026-09-25 docs/61 H1 收口「422 逐条定位」这一层**：后端 422 的 `locations` 稀疏侧车自 M2 起就存在但前端从未消费（`apiClient` 只读 detail/codes/params 且把逐条映射 join 成单串），本批前端接线后编译诊断可逐条按 nodeId+RFC6901 pointer 定位并闪烁字段（**后端零改动、不新增 namespace**，170 码复用 `validation.dsl.*`）；新增键仅 `editor.problems.serverTag`（zh「后端编译」/en「Backend compile」，过三静态守护）；**配套契约微调**：为让语言成为渲染路径的真依赖（oxlint `exhaustive-deps` 正确指出它在 memo 内是隐式全局读），`t()` 增可选 `lng`（`locales/index.ts:51/216`），缺省仍取全局语言、对全部既有调用方零行为变化——**零依赖自写 t() 的路线不变，仍不换 i18next** | §2.3 |
| 组件/模板描述多语言 | Phase 2 模板库（14 D13；docs/57 未取回：节点目录 label/description、模板 name/description/标签在英文态仍显中文，属业务/元数据豁免）。**2026-09-27 模板 name/description/标签后端切片取回**（docs/70：`TEMPLATE_I18N`/`CAPABILITY_I18N` 集中翻译表，三端点按 Accept-Language 换值，前端 apiClient 自动带；适配器 name 是 wire id 不译）。**2026-10-02 节点目录 label/description 切片取回（打包 ZI）**：`NODE_CATALOG` 9 种节点 label/description 抽入 `editor.nodeCatalog.<kind>.label/description` 18 键（zh/en 两档），消费方 NodePanel/AtlasNode/PropertyPanel 走 `t()`；`NODE_CATALOG` 本体保留作数据源/兜底（默认 label 落图 JSON 与中文日志＝业务数据豁免）。**已落码收口（2026-10-02，`3b785c1`/`5a20fcf`）**：前端 753 passed / 2 skipped、lint 零新增、build 过、i18n.test 59 passed（含 nodeCatalog 键 zh 数据源一致/en 零汉字断言与 PARITY 守护） | 04 §28 第 3 条 |
| 暗色模式 | 有需求时按 §3.4 扩展点实施 | §3.4 |

## 5. 与其他文档的关系

- 需求源：04 §补充项 28（冻结正文，不改写）。
- 选型记录：10 文档 §1 决策表（T7）+ §4 变更记录（i18n 库；token 零新依赖方案）。
- 目录落点：09 文档前端树 `locales/`、`theme/tokens.ts`（待落码标注）。
- 触发缓做：14 文档 D12（i18n 落码；docs/57 部分取回翻译与切换、i18next 本体仍缓做且不解除条目）/ D13（组件描述多语言）。
- 落地批次：docs/57（en-US 首批全量翻译 + 语言切换 + 发布协作/审批/属性面板组件抽取，2026-09-24 收口）。
