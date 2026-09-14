# Changelog

本文件记录 Atlas 仓库的可追溯变更里程碑。详细过程与状态见 [handoff.md](handoff.md)。

## [Unreleased]

### feat / graph+collaboration+api+frontend+llm（2026-09-14）

- Phase 2 第五项「人机协作节点（human_approval，进程内审批挂起）」端到端落地。契约（04 §5.6 权威 blockquote，原状态机/异常处理顺延 §5.7/§5.8；Graph JSON 仍为 version 1、EdgeDSL 不变）：单节点 config 挂 `{summary, approver, timeoutSeconds, onTimeout, approvedTarget, rejectedTarget}`——summary 必填支持 `{{路径}}`、approver 可选仅展示不鉴权、timeoutSeconds 整数 10-3600、onTimeout approve/reject 默认 reject（超时沿拒绝分支继续、run 仍 completed）、双目标须存在/互异/非自身，恰好 2 条出边对配通过/拒绝且不直连 END；可位于 condition 分支/loop 体内/parallel 区域，阻塞不产生超步故 recursion_limit 不变。新增 `atlas.collaboration.approvals.ApprovalBroker`（uuid4 token + `threading.Event`，模块单例可注入；首决生效、list_pending/reset）；决策三来源——REST `POST /api/approvals/{token}/decision`（resolvedBy="human"，404 未知/409 重复/422 坏值）、run inputs 预置 `{"approvals":{"<node-id>":"approved|rejected"}}`（resolvedBy="input"）、超时（resolvedBy="timeout"，与并发人工竞速人工胜出）；新增 `GET /api/approvals` 调试列表，`/api/demo/reset` 按超时拒绝放行并清空。node_start 在登记后阻塞前发出且携带 `approval:{token,summary,approver,timeoutSeconds}`；产出 `{mode:"human_approval",decision,target,token,summary,approver,resolvedBy}`、trace `human-x: approved (human) → tool-y`、下游可引 `{{human-x.decision}}`。**连带项：`/run/stream` 由「先跑完再回放」改真流式**——run_graph 在 daemon 线程执行、`queue.Queue` 实时下发（旧实现下节点阻塞等审批、浏览器收不到 node_start 会死锁），终帧 `event: result`、运行期异常新增 `event: error` 哨兵帧；同步 /run 不变。前端新增 HumanApprovalConfig（说明 TextArea+变量插入/审批人/超时秒/策略 Radio/双目标 Select）、双 Handle（approved/rejected，通过/拒绝边标签）、删除清理、运行中 AntD 审批 Modal（node_end 自动关窗，409/404 窗内提示，mask 不可关闭）、SSE 日志「人工审批：通过/拒绝（人工/超时/预置）」，节点配色 slate900（避开既有七色）；NL prompt 枚举七类→八类。持久化中断-恢复（LangGraph interrupt/checkpointer）、多实例/外部信号、IM/邮件/钉钉通知、动态审批人与角色权限、审批评论流/队列页面缓做 14 D20（与 D19 事件等待同批，触发：11 S1 就绪或真实审批业务）。测试：后端 155 passed/8 skipped（+collaboration broker 7/DSL 13/loader 5/Demo API 3），前端 vitest 43；浏览器实测人工同意（仅通过侧执行）、人工拒绝（仅拒绝侧执行）、10s 超时自动拒绝且弹窗自动关闭、空说明即时报错、pending 列表可见。

### feat / graph+frontend+llm（2026-09-14）

- Phase 2 第四项「等待节点（wait，定时挂起）」端到端落地。契约（04 §5.5 权威 blockquote，Graph JSON 仍为 version 1、EdgeDSL 不变）：单节点 config 挂 `{waitType:"duration", durationSeconds}`，整数秒常量 1-600；恰好 1 条出边指向已存在后继且不直连 END，无新增拓扑规则（可位于 condition 分支/loop 体内/parallel 区域，recursion_limit 公式不变）。运行时执行器内同步 `time.sleep`（SSE 同步生成器跑在 Starlette 线程池，不卡事件循环；600s 上限即 worker 占用上限），等待不失败不重试；产出 `{mode:"wait",waitType:"duration",durationSeconds}`、trace `wait-x: waited Ns`、下游可引用 `{{wait-x.durationSeconds}}`；普通边装配零改动。事件等待（interrupt/checkpointer+外部信号恢复）与动态时长缓做 14 D19（触发：事件驱动/分钟级长等待业务 + 11 S1 持久化就绪）。DSL 中文聚合校验覆盖坏 waitType（event 明确「暂不支持」）/非整/bool/越界/出边数与目标。前端新增 WaitConfig（事件等待 Radio 禁用带 tooltip、InputNumber 1-600 越界钳制+空值即时报错，antd6 Space.Compact 替代弃用 addonAfter）、默认单 Handle（画布/Store 零改动）、SSE 日志「等待完成：N 秒」、节点配色 orange6（purple6 已属 AI 节点）；NL prompt 枚举六类→七类。测试：后端 127 passed/8 skipped（+DSL 12/loader 2/NL 1），前端 vitest 39；浏览器实测 trigger→wait(1s)→tool_call 真实 1s 墙钟、后继执行、output/trace 形状正确，面板校验零新增控制台错误。

### feat / graph+frontend+llm（2026-09-14）

- Phase 2 第三项「并行节点（parallel，扇出/汇聚网关）」端到端落地。契约（04 §5.4 权威 blockquote，Graph JSON 仍为 version 1、EdgeDSL 不变）：单节点 config 挂 `{joinStrategy, branches:[{label,target}], joinTarget}`，分支 2-10 个、label 非空唯一、target 互异、出边恰好 N 条且不直连 END；分支区域由 BFS 界定（遇 joinTarget 与 parallel 节点停止），区内允许 condition/自包含 loop，禁嵌套 parallel/trigger/分支交叉与外泄/分支不可达 join，中文聚合报错。两策略：`all_success`（任一分支 FAILED 则汇聚 status=failed，但走 **fail-safe 汇聚**——joinTarget 照常执行、run 仍 completed，下游可判 `{{parallel-x.status}}`）与 `all_completed`（各分支走到汇聚即成功）；any_success（OR-join+取消分支）缓做 14 D18。运行时关键事实：spike 实测 LangGraph 普通多入边不构成 barrier，故编译期注入合成网关 `__join__<id>`（分支末端 retarget 网关，网关就绪计数 self-loop wait 超步等待不等长分支，齐后聚合再路由 joinTarget），`GraphState.outputs` 改 `Annotated[dict, _merge_outputs]` key 级合并、`status` 用 last-write、执行器只回自身分片；网关事件不外泄，汇聚完成以 parallel 节点自身补发第二次 node_end（fork 时 running）。聚合产出 `{mode,joinStrategy,status,branches:[{label,target,status,error}],result:{<入口节点id>:末端产出},joinTarget}` 与 fork/joined trace 行。前端新增 ParallelConfig（策略 Radio/分支增删/汇聚目标）、N 出口 Handle 与分支边标签、删除清理、SSE 日志（并行启动/汇聚成功/分支失败 fail-safe），节点配色 magenta6；NL 生成 prompt 枚举补 parallel。测试：后端 112 passed/8 skipped（+DSL 10/loader 3/退款 e2e 1/NL 1），前端 vitest 37；浏览器对真实后端实测三场景——不等长双分支汇聚一次（汇聚节点渲染 `状态=success`）、一支未注册适配器 fail-safe 文案、坏配置三条即时报错。

### feat / graph+frontend+llm（2026-09-14）

- Phase 2 第二项「循环节点（loop）」端到端落地，v1 仅条件循环 while。契约（04 §5.3 权威 blockquote，Graph JSON 仍为 version 1、EdgeDSL 不变）：节点 config 挂 `{mode:"while", continueExpression, maxIterations(1-100, 默认10), bodyTarget, exitTarget}`，恰好两条出边对配循环体/退出目标；循环体由 BFS 界定，体→同 loop 的回边是**唯一合法环**（DSL 摘除白名单回边后对余图 DFS 环检测，调和 U7），体内禁嵌套 loop/trigger、禁泄漏 exitTarget、无回边/游离体分支均中文聚合报错。运行时：可重入执行器每轮重求 continueExpression（首轮播种 `{{loop-x.index}}`，从 1 起），为真进体、为假退 `condition_false`；达最大次数与表达式异常均 **fail-safe 退出**（路由 exitTarget、`exitReason=max_iterations/expression_error`、运行仍 completed）；出边全 conditional，`recursion_limit` 按循环体规模派生。产出 `{mode,iterations,index,target,exitReason,expression_errors}` 与 continue/exit trace 行。前端新增 LoopConfig（表达式实时校验/变量插入/最大次数/体·退出目标）、双出口 Handle 与「循环体」「退出」边标签、删除目标清理、SSE 日志中文化（条件不满足/达到最大次数/表达式异常），节点配色 cyan6；NL 生成 prompt 枚举补 loop。测试：后端 97 passed/8 skipped（+DSL 8/loader 3/退款 e2e 1/NL 1），前端 vitest 34；浏览器对真实后端实测三场景——`{{loop-1.index}} < 2` 体执行 2 轮后条件不满足退出、恒真 3 轮撞上限 fail-safe、坏变量 0 轮表达式异常退出。遍历循环（foreach/item/聚合）与 break/continue 缓做（14 D16/D17），嵌套循环 v1 拒绝。

### feat / graph+frontend+llm（2026-09-14）

- Phase 2 首项「条件分支节点（condition）」端到端落地。契约（04 §5.1/§5.2，Graph JSON 仍为 version 1、EdgeDSL 不变）：节点 config 挂 `branches:[{label,expression,target}]` + 必填 `defaultTarget`，按序短路；label/target 节点内唯一、target≠default、每个目标须有出边且每条出边须被分支覆盖、不允许直连 END。后端新增 `atlas.graph.conditions`（纯 stdlib 手写递归下降表达式：`{{路径}}`、比较 `> >= < <= == !=`、逻辑 `&& || !`、括号、数字/字符串/true/false/null；禁 eval/算术/函数；校验期报语法与纯字面量类型错误，运行时错误 fail-safe 走 defaultTarget），`dsl.py` 增 condition 图级校验与 trigger 根可达 BFS，`loader.py` condition 出边全部 `add_conditional_edges`（执行一次写 outputs，router 只读 target），产出 `{branch,target,evaluation,expression_errors}` 与 trace 行。前端新增 ConditionConfig 属性面板（实时中文校验/变量插入/目标 Select）、节点多出口 Handle 与出边分支标签、删除目标自动清引用、运行 trace 分支行；`lib/conditions.ts` 为后端同构的 TS 校验。NL 生成 prompt 枚举补 condition。测试：后端 84 passed/8 skipped，前端 vitest 32；浏览器实测 12346（¥5000）单侧转人工 human_review、12347（¥128）默认分支 refunded。LLM 判断分支、函数库/算术缓做（14 D14/D15）。

### docs（2026-09-14）

- 沙盘假定 Phase 1 种子验证 Go、进入 Phase 2（用户指示，非真实试用结论）：docs/18 文首加假定声明、新增 §8 验证结论表（注明无 §5 实测值、无 §6.1 C1-C5 记录，真实试用 No-Go 时回退）；08 Phase 1 补假定结论并记 PR #3（dev→main，19 提交，merge f069b53，main CI 全绿）；handoff 阶段口径、仓库状态（PR 已合并）、Active work（Phase 2 范围与 D6/D7 触发提示）、Quality gate（gitleaks 403 修复、PR/main 实跑全绿）同步。

### chore / bench（2026-09-14）

- D9 BENCHMARK 首版落地：新增 `scripts/benchmark.py`（纯 stdlib，零新依赖；10% warmup + 300 次/场景，p50/p99，输出 BENCHMARK Results 格式 Markdown，`DATABASE_URL` 时追加 DB ping）。场景：OODA 循环吞吐、退款 3 节点图编译时延、12345 自动退款端到端时延（规则决策路径）、Harness 进程内调用开销。首份基线（commit 407812e，CPython 3.11.15 / macOS arm64）：OODA 2.05ms p50（≈488 loops/s）/ 编译 1.81ms / 退款端到端 2.28ms / Harness 0.002ms；LLM 决策、记忆层读写、并行扇出、长流程内存增长在 BENCHMARK Scope 标注待接入补测。09 目录树补 `scripts/`，08/14/handoff 同步。

### chore / ci（2026-09-14）

- D10a CI 质量门落地：`.github/workflows/ci.yml`（push main/dev 与 PR 触发，同 ref 并发取消）三道门——gitleaks 全历史密钥扫描（沿用根 `.gitleaks.toml`）、后端 Python 3.11 `pytest`（integration 默认跳过）、前端 Node 22 / pnpm 10 `pnpm lint`（oxlint）+ `pnpm test`（vitest 23）+ `pnpm build`。本地等价命令全绿（后端 62 passed/8 skipped、gitleaks 55 commits 无泄漏）；Actions 实跑待 dev 推送后验证。14 D10 拆为 D10a（完成）/D10b（CD，缓做至远程部署目标确定），ADR T8 落 10 文档 §4，08/09/14/handoff 同步。

### docs（2026-09-14）

- docs/18 新增 §6.1 试用进度跟踪表（C1-C5 一行一客户：决策路径、三场景结果与耗时、黄金用例认同数、意向、反馈分类、跟进项）与每家详细记录模板；handoff 新增「Active feedback」入口区并指向该表，试用反馈按 16 文档流程闭环。

### feat / api+web（2026-09-13）

- Phase 1 应用内反馈入口落地（18 文档 §6）：后端新增 `POST /api/feedback`（type=bug/suggestion、content 1-2000 字、contact 选填，201 + id/created_at）与 `GET /api/feedback`（陪同试用导出），进程内 `FeedbackStore`（重启清空，与 Demo 存储同假设；`/api/demo/reset` 不清除反馈）；非法 type/空 content 经 pydantic 校验 422。前端编辑器头部新增"反馈"按钮与弹窗（类型切换、字数计数、联系方式选填、提交成功态、错误回显），`apiClient.submitFeedback`。测试：后端新增 2 用例（62 passed/8 skipped），前端 vitest 23、`pnpm build` 通过；浏览器同源（:8000）实测两类反馈提交与 GET 导出落库，控制台零错误。03 新增 feedback_item 契约、12 REST 表补两端点、13 进度补行。

### docs（2026-09-13）

- Phase 1 种子验证准备：新增 `docs/18-种子客户验证计划.md`（客户画像与 3-5 家招募标准、对齐 08 §7.3 的三场景试用脚本、Go/No-Go 量化指标、应用内+试用表双反馈机制与边界）与根目录客户向一页纸 `TRIAL.md`（docker compose 启动、12345 自动退款 / 12346 转人工 / NL 生成草稿三场景、reset 用法、5 题反馈表）。08 Phase 1 补准备记录，00 文档地图加 18，AGENTS 文档范围改 00-18，README/handoff 索引同步。

### feat / api+infra（2026-09-13）

- Phase 1 种子客户一键交付：根目录新增 `Dockerfile`（node:22-slim 多阶段构建前端 → python:3.11-slim 安装后端，dist 拷至 `/app/frontend/dist`，不装 Playwright 浏览器）、`docker-compose.yml`（单服务 8000 端口，`LITELLM_MODEL` 透传）、`.dockerignore`。`api/main.py` 在 `ATLAS_FRONTEND_DIST`（默认 `frontend/dist` 相对于 cwd）存在时用 StaticFiles 同源挂载到 `/`（显式路由优先，dev 仍走 Vite 5174 代理）；新增 `POST /api/demo/reset`（店铺恢复 5 笔种子退款单、清空已保存图与登录态），`DemoShopService.reset()`/`GraphStore.clear()`。测试新增 reset 全链路与静态托管条件用例，后端 60 passed/8 skipped；镜像 `docker compose up --build` 实测编辑器、/api/health、/demo/shop 同源可访问。

### feat / web（2026-09-13）

- 设计 Token 等价替换落地（17 文档 §3）：新增 `frontend/src/theme/tokens.ts`（primitive/semantic 两层，唯一事实源，导出 `antdTheme` 与 `token()`）与 `setup.ts`（main.tsx 引入一次，注入 `--atlas-*` CSS 变量）；`App.tsx` ConfigProvider 改消费 `antdTheme`，`index.css` 全部硬编码色值/rgba 光晕换为语义变量（含 keyframes pulse），`nodeCatalog.ts` 节点三色与 `FlowCanvas.tsx` 连线色改引 `token()`。现值 1:1 搬迁、零新依赖；`pnpm test` 23/23、`pnpm build` 通过，浏览器 computed-style 逐页核对零视觉差异、控制台零错误，src 下硬编码色值仅剩 tokens.ts。

### docs（2026-09-13）

- 新增 `docs/17-前端国际化与设计Token方案.md`（工程推导，需求依据 04 §补充项 28）：i18n 选型 i18next + react-i18next + AntD locale（T7，触发式引入，当前不落码），冻结 locales 目录/key 命名/后端错误码本地化/金额日期 Intl 格式化/NL 多语言边界等契约；设计 Token 三层模型（primitive/semantic/component），`frontend/src/theme/tokens.ts` 单一事实源派生 AntD theme 与 `--atlas-*` CSS 变量（零新依赖），含硬编码色值迁移清单与零视觉变化验收。10 文档 §1/§4 记 T7 与 token ADR，09 前端树补 `theme/`、`locales/`（待落码），14 文档新增 D12/D13，handoff/00 索引同步。
- 同步 README/CONTRIBUTING 阶段口径至 W1-W10 Demo 完成，03 契约索引补 W9-W10 运行时 Schema（refund_decision/refund_order/run_event/nl_generate_request）。

### feat / llm+shop+graph+api+web（2026-09-13）

- W9-W10 电商退款端到端 Demo，08 §7.3 七条验收全部达成。新增 `src/atlas/llm/`：`decision.py` 定义 DecisionClient 协议，配置 `LITELLM_MODEL` 时经 LiteLLM 决策（只取 JSON，解析失败 fail-safe 转人工），未配置时 RuleBasedDecisionClient 按 06 §9.2 黄金规则确定性兜底（质量原因且金额 ≤ 限额 → approve_refund，其余 → request_human_approval）；`nl_generate.py` 自然语言生成 Graph 草稿（LLM 优先、退款模板兜底、无法识别 422）。新增渠道包 `src/atlas/shop/`：`service.py` DemoShopService（五笔种子退款单 12345-12349、登录态、退款/转人工状态流转），`adapter.py` ShopHarnessAdapter（login/list_pending_refunds/execute_refund[financial]/request_human_approval/process_refund 五能力，按上游决策路由，权限门与 StructuredError）。`graph/loader.py` 改为依赖注入（decision_client/registry/emit/trigger_payload），webhook 载荷经 run inputs 进入 trigger `context.payload`（同名键覆盖全局变量），执行事件 node_start/node_end/run_end；dsl 修复 triggerType `schedule` 与 `cron` 均触发 cron 校验（对齐前端取值）。`api/main.py` 新增 `POST /api/graphs/{id}/run/stream`（SSE 实时进度）、`POST /api/nl/generate`、`GET /api/adapters`、Demo 店铺登录/订单接口与 `GET /demo/shop` 模拟商家控制台页面，共享 Demo 服务单例。前端：退款单选择器、SSE 流式运行（节点 running 脉冲/completed 描边 + 调试台事件）、NL 生成弹窗载草稿（deserializeGraph）、种子图改为退款三节点、Dashboard 文案 W9-W10。测试：后端新增 26 用例（决策 6/店铺 8/NL 2/退款端到端 3/Demo API 6/店铺平台集成 1/schedule 1），默认 58 passed/8 skipped，opt-in 集成全绿；前端 vitest 23 全绿、`pnpm build` 通过；浏览器实测 12345 破损→approve_refund→refunded、12346 主观→request_human_approval→human_review、NL 草稿载入、控制台 demo/demo 登录拉单。文档 08（落码记录+验收映射）/09（llm、shop 包树+清单）/12（新端点）/13（W9-W10 测试行）同步；`.env.example` 补 `LITELLM_MODEL`。

### feat / graph+api（2026-09-13）

- W7-W8 编辑器→DSL→LangGraph→运行链路打通：`src/atlas/graph/dsl.py` 承接前端 Graph JSON（version 1，pydantic GraphDSL/NodeDSL/EdgeDSL）并做静态校验（版本、节点/连线 id 唯一、边端点存在且禁止自环、trigger/ai_decision/tool_call 分类型必填配置、未支持节点类型拒绝），错误聚合为中文列表；`graph/loader.py` 将 DSL 编译为 LangGraph StateGraph（节点 1:1、边按 DSL、START/END 自动接线），`run_graph` 播种全局变量并在运行时执行 04 §6.3 `{{路径}}` 插值（可读上游节点产出，缺失引用原样保留），三类节点为确定性占位执行器（LLM/真实工具待后续接入）。`api/main.py` FastAPI 提供 `GET /api/health`、`POST /api/graphs`、`GET /api/graphs/{id}`、`POST /api/graphs/{id}/compile`、`POST /api/graphs/{id}/run`（进程内图存储，校验失败统一 422）。前端新增 `lib/apiClient.ts`，编辑器新增"编译并运行"按钮（拓扑+产出结果弹窗、422 中文错误 Alert），Dashboard 文案更新至 W7-W8。测试：新增 `test_graph_dsl.py`（7）、`test_graph_loader.py`（5）、`test_api_graphs.py`（TestClient 4）共 16 用例，默认 pytest 32 passed/7 skipped；浏览器对真实 uvicorn 验证三请求链路（`{{global.approval_limit}}` 插值为 500）与 422 错误路径。决策记录：graph_loader 落 `graph/` 包（不进 engine）、进程内图存储、新增 /run 端点（09 待定项 6）；03 新增 graph_definition 契约索引，12 REST 表补 /run。

### feat / web（2026-09-13）

- W5-W6 编辑器核心功能：`frontend/src/lib/` 新增纯逻辑模块——`variables.ts`（04 §6.3 `{{路径}}` 模板语法：引用提取、插值（缺失引用原样保留）、点号/下标路径解析、变量名校验、全局变量与节点输出路径清单）、`nodeCatalog.ts`（trigger/ai_decision/tool_call 三类节点目录、默认 config/retry、中文实时校验规则）、`graphSerializer.ts`（按 03/04 §3.2 node_schema 契约序列化 Graph JSON，version 1，坐标取整）；`store/editorStore.ts` 重写为类型化 store（`nextId` 扫描已有 id 消除 React Flow 节点 id 碰撞、选中/配置更新/删除级联连线/变量增删/连线日志，种子改为 OA 审批示例）；UI 新增自定义 `AtlasNode`（类型配色 + 校验错误角标）、`VariablesPanel`（变量 CRUD + 标识符校验），重写 `FlowCanvas`（HTML5 拖拽 + screenToFlowPosition 落点）、`NodePanel`（拖拽面板）、`PropertyPanel`（三类节点分类型配置表单、插入 `{{变量}}` 引用、retry 策略、实时校验清单）、`Editor`（节点/变量双 Tab + Graph JSON 导出预览弹窗）；Dashboard 文案同步到 W5-W6 状态。测试：新增 vitest 5（选型 ADR 见 10 文档 §4），4 个测试文件 20 个用例全绿；`pnpm build` 通过；浏览器人工验证拖拽新增、错误角标与属性面板联动、变量插入恢复校验、导出 JSON 结构正确，应用零控制台错误。
- chore：修复根 `.gitignore` 的 Python 规则 `lib/` 误伤 `frontend/src/lib/`（导致前端逻辑目录被静默忽略），改为 `/lib/`、`/lib64/` 锚定仓库根目录。

### feat / web（2026-09-13）

- W3-W4 Harness Web 适配器：`harness/base.py` 落地 06 §6.4 同构契约（HarnessAdapter 抽象类、ActionRequest/ActionResult/Capability/Observation、ActionStatus 与 Permission 枚举），execute 模板方法内置权限校验与审计回调（I8）；`harness/registry.py` 进程内适配器注册/发现/心跳健康（04 §4.4，TTL 30s）。`web/location.py` 实现三层定位（层1 选择器 3000ms 超时 + 成功选择器缓存，层2 视觉语义置信度严格 >0.7 并回填缓存，层3 全图推理坐标，全失败返回"元素未找到，三层定位均失败"），层 2/3 视觉组件以 Protocol 注入；`web/adapter.py` 为 Playwright sync API 适配器，工具集 navigate/click/type/screenshot，headless 受 `PLAYWRIGHT_HEADLESS` 控制。测试：契约/定位 13 个单元用例（假页面）全绿，`test_web_browser_integration.py` 4 个真实 Chromium 用例（I1 selector、I2 语义层真实坐标、I5 缓存短路、type/observe）全绿；I3/I4 编排由单元测试覆盖，真实视觉模型链路待 LiteLLM 接入。包边界决策（harness=契约/注册、web=Playwright 实现，不合并）记入 09 待定项 2 与 08 §7.2；12 一致性清单勾选 4 项。

### feat / memory（2026-09-13）

- PostgreSQL + pgvector 连接层验证：本地 Docker 运行 `pgvector/pgvector:pg16`（容器 `atlas-pg`），迁移 `db/migrations/001_enable_pgvector.sql` 启用 vector 扩展（0.8.6）；`memory/settings.py`（`DATABASE_URL` 单一读取点，缺失 fail-closed）与 `memory/database.py`（SQLAlchemy 引擎/会话工厂，限定 `postgresql+psycopg` 即 psycopg 3，`ping`/`pgvector_version` 探针）；`tests/test_database_integration.py` 3 个 `integration` 标记用例（连通性、扩展版本、vector 写入与余弦距离排序），默认跳过，经 `ATLAS_RUN_INTEGRATION=1` + `DATABASE_URL` 开启，对容器全绿。`.env.example` 连接串改为 psycopg 3 写法；本地 `.env` 已建且被 gitignore。

### docs（2026-09-13）

- 项目文档与 W1 实现状态对齐：00 文档地图更新 09/10 描述；12 对齐检查表勾选 LoopState 一致性（实现于 `engine/state.py`）；13 新增 §9 实现进度（后端冒烟用例、前端构建与人工验证）；AGENTS 文档分层范围更正为 00-16；handoff 仓库行反映 dev 分支与未推送状态。

### feat / web（2026-09-13）

- W1 前端编辑器框架：Vite 8 + React 19 + TypeScript 6，接入 `@xyflow/react` 12（替代已弃用的 `react-flow-renderer`）、Zustand 5、Ant Design 6；实现 Dashboard/Editor 页面与 FlowCanvas/NodePanel/PropertyPanel/DebugConsole，初始 触发→AI决策→工具调用 三节点，支持添加节点、选中编辑名称、连线状态与调试日志。`pnpm build` 通过，浏览器交互验证无控制台错误；dev 端口 5174，`/api` 代理 8000。

### feat / engine（2026-09-13）

- W1 最小 OODA 循环跑通：`engine/state.py`（LoopState，契约 06 §6.1/12 §1.1）、`engine/nodes.py`（observe/orient/decide/act/reflect 确定性占位节点 + 三条 conditional 路由，签名遵循 12 §1.2）、`engine/loop.py`（StateGraph 拓扑同 12 §1.3，`build_graph/initial_state/run_loop`）；3 个 pytest 冒烟用例全绿，`run_loop` 端到端收敛 `completed`。LLM 决策与 Harness 感知为后续周次接入点。

### chore / scaffold（2026-09-13）

- W1 工程骨架：按 09 文档建 `src/atlas/{engine,harness,graph,nodes,memory,skills,collaboration,api,web}/` + `tests/`（`__init__.py` 占位）；`pyproject.toml`（Python >=3.11、src layout、pytest 配置），依赖按 10 文档 §2 锁定，版本下限取当日 PyPI 稳定版；Python 3.11 venv 可编辑安装验证通过。
- 09 文档记录包边界决策：新增 `atlas.api` 承载 FastAPI 入口与 REST 路由（待定项 5），harness 保持网关契约。

### docs（2026-09-13）

- 技术选型 T1-T5 收口为正式 ADR（10 文档 §3/§4）：Demo 模型经 LiteLLM 对接主流商业 API（`.env` 切换，不引入 vLLM）；评估与优化层仅留抽象接口；编辑后台前端进 Demo 骨架（`frontend/`，W1-W2）；进程内事件总线替代 NATS（Phase 2 重启）；Harness 网关 Demo 用 Python/FastAPI 实现同构接口（Go 化留 Phase 2）。02/08/09 同步，14 文档 D5/D6 已登记缓做触发条件。

### docs（2026-09-13）

- 移除 `LICENSE`（用户指示：现在不需要，许可证暂未定）；README / CONTRIBUTING 的 License 段落与 handoff / CHANGELOG 引用同步清理。
- 补齐 agent-world 惯例规范文档：根目录 `CONTRIBUTING.md` / `MIGRATION_CONVENTION.md` / `BENCHMARK.md` / `.gitleaks.toml` / `.env.example`；docs 新增 `14-缓做事项登记表` / `15-环境与分支策略` / `16-反馈工作流`。索引同步 00 文档地图、README、handoff。

### chore / infra（2026-09-13）

- 仓库初始化：`git init -b main`，添加 Python 项目 `.gitignore`；创建 GitHub **private** 仓库 `bayernjf/atlas` 并推送（3 个原子提交：chore / docs specs / docs meta）。

### docs（2026-09-13）

- 建立根目录元文档体系（参考 agent-world 惯例）：`handoff.md` / `README.md` / `AGENTS.md` / `CLAUDE.md` / `git-commit-message.md` / `CHANGELOG.md`。
- 源文档切分完成并核对：两份原始方案文档按主题切分为 `docs/` 00-13 共 14 份 AI 导向文档，274 章节全映射、逐段覆盖率机器核对通过，源文档已删除（详见 [00-文档索引与治理.md](docs/00-文档索引与治理.md)）。
