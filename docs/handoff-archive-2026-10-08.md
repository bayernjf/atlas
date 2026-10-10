# Handoff Archive — 2026-10-08

> 2026-10-09 从 handoff.md「Recently shipped」滚出（保持最近 5 条上限；逐字搬移、原文不改写）。

## 打包 BM 落码收口＝docs/18 首次被执行（照 TRIAL.md 逐字干跑）

✅ **打包 BM＝docs/18 第一次被执行：照 TRIAL.md 逐字跑一遍试用（2026-10-08，承接用户「该产品做多久了，怎么还没把产品原型做出来」＋「那你继续搞」；执行记录权威 docs/18 §6.2；docs/13 打包 BM 小节 U1206–U1212；docs/14 新增 D57/D58/D59；后端 2443/166/0、PG 集成 99/1/0；未 push）**：一次性卷独占 compose 项目（空库首启）＋真 Chromium＋真 LLM 走完三场景，采集器 `scripts/dev/trial_dryrun.py`（**不是断言脚本**：每步失败记 FINDING 继续走，真人卡住时不抛异常、他只是放弃）→ 终局 **13 PASS／3 FINDING**、"打开浏览器到 12345 从待处理消失"19 秒。改掉的：指南三条不实（没给平台登录账号／匿名 `curl /api/demo/reset` 实测 401 而 docs/12 本就写着 admin only＝**指南错**／"数据在内存里重启即清空"与 compose 写死 `pg`＋named volume 冲突）＋**演示后台把「AI 已退款」和「AI 转人工」分开显示**（旧响应只有 pending，两种结果都表现为"单子消失"，场景 B 要演示的边界在页面上不存在；`processed` 段 additive、`orders` 逐键不变，U1206 带正控）＋刷新不再掉回登录框。**跑出来的事故**：模型配了但端点不可达时首次运行抛 `_DeadlockError`（litellm **进程内首次导入落在 worker 线程**，与事件循环线程的日志过滤器互相等模块锁），此后**整个进程不再响应任何请求**（含未鉴权 `/api/health`，CPU 0.37%＝阻塞非空转）→ lifespan `warm_litellm()` 开始服务前导完（环境还原按键集合差，保住 conftest 那条防线；导不起来只 warning 不炸启动），**同臂对照实跑**修前 10/10 超时、修后 2.8s 普通供应商错误＋10/10 通过。**度量自省**：第一版 6 条 FINDING 里 2 条是脚本自己的错（拿运行状态判场景 B、拿**节点数**判草稿载入——草稿恰好也是 3 个同名节点），改比对导出图 JSON 字节才拿到真结论。**只登记不擅自动**：演示图接真「人机协作」节点＝产品方向（D57，指南已明说"演示到此为止"）、自助重置按钮需新能力位端点（D59）、SSE error 不带 code 且第三方库内部字符串进客户界面（D58）。零迁移、零新依赖、无新 ADR；镜像构建自干净工作区 `9d310e9`。详见 Active #129。

## 打包 A4 落码收口

✅ **打包 A4 落码收口＝D20 动态审批人与审批指派校验（2026-10-08，五原子 `d2d5e2a`→`7ebd2c9`；docs/100 §7 注记；后端 2436/166/0、前端 820/2、oxlint 0/0、build 过；A 档四打包 A1–A4 全部收口）**：approver `{{变量}}` 插值两翼（编译 422 `APPROVER_REF_UNRESOLVED`＋运行期残留→空串＋告警）＋决策端点指派校验（`user:<username>` 匹配 principal、邮箱恒 403 因 iam 无 email、空 approver 任何人可决）＋前端表单提示与错误码 i18n（U1205）；两处偏差照实见 docs/100 §7；零迁移、无 ADR。〔**2026-10-08 接力追加**：本批自报缺的**浏览器冒烟已补**——一次性卷的独占 compose 项目＋真 Chromium，拖入「人机协作」后节点数 3→4、属性面板渲染出 A4 新提示，10 项全 PASS；脚本 `scripts/dev/approver_hint_smoke.py`＋截图 `docs/screenshots/a4-approver-hint.png`，证据细节见 docs/100 §7 追加段〕

✅ **打包 BM 缺陷批落码收口＝D57/D58/D59 三缺陷同日闭合（2026-10-08；docs/101 §7 注记；docs/13 U1213–U1217；docs/14 D57/D58/D59 翻 ✅）**：D57 refund-auto 模板 7 节点真转人工（condition 分流＋human_approval-1 真挂起＋execute_refund/reject_refund 双出口，U1214 断言 resolve 前线程不结束）；D58 未知异常 message 收口四处（SSE/同步/续跑/trigger）；D59 health 能力位＋前端 DemoResetButton＋i18n 3 键。定向 74 passed＋前端 103 passed、build 过；零新依赖／零迁移／无 ADR。详见 Active #129。

## 打包 ZS 落码收口＝D28 告警规则模板用户自建 CRUD

✅ **打包 ZS 落码收口＝D28 告警规则模板用户自建 CRUD（2026-10-08 立项+同日收口；docs/102 §6；docs/13 U1220–U1230；docs/14 D28 半边取回、整体不解除）**：用户自建/编辑/删除私有告警规则模板——迁移 045＋`monitoring/rule_user_store.py`＋`pg_rule_user_store.py` 两档 store＋registry 装配/reset＋租户内重名 409；五端点（GET 列表/详情合并内置＋用户 source 区分、POST/PUT/DELETE 写 administer、内置 id 404 保护）；前端 AlertRuleTemplateMarket 新建/编辑/删除 Modal＋source Tag＋i18n 两档 20 键。八原子 `ba6fa2e`→`2a36a5c`＋`ef32be1` fix；门＝后端 2456/166/0、前端 828/2、oxlint 0/0、build 过、守护门 24 passed、PG 集成 20+3 零回归；零新依赖/无 ADR。详见 Active #130、Quality gate。

## 打包 ZT 落码收口＝规则模板配置表单化（RuleConfigEditor 共享）

✅ **打包 ZT 落码收口＝规则模板配置表单化（RuleConfigEditor 共享）（2026-10-08 立项+同日收口；docs/103 §6；docs/13 U1231–U1236；docs/14 D28「规则配置 UI 表单化」闭合、D28 整体不解除）**：Monitoring 生效规则表单抽成纯受控共享组件 `RuleConfigEditor`＋`lib/ruleConfig.ts`（默认值/结构化校验），模板市场新建/编辑 Modal 以表单替换裸 JSON、提交前拦截非法配置；顺带修复 ZS 表单三个 Form.Item 缺 name 的绑定缺陷。四原子 de2a73f→5f2f1f0→5add6a2→b38af24；前端 848/2（+20）、oxlint 0/0、build 过、后端零改动（守护门 24）。

## 打包 AC 落码收口＝AI 评估 Harness v1（离线批评估）

✅ **打包 AC 落码收口＝AI 评估 Harness v1（离线批评估）（2026-10-09 立项+同日收口，承接用户「那你搞」对 docs/107 §六 B 档的批复；docs/110 §一；docs/13 打包 AC 小节 U1278–U1284；docs/14 D62 翻 ✅、D62 不解除）**：`evaluation_task` 契约（docs/06 §9.2）从纸面落成可执行离线批评估——新 `evaluation/` 包（models 六模型＋runner 离线批评估：决策客户端可注入确定性实现做无模型回归、approval 帧收集 `request_human_approval`、verify 复用 condition 白名单表达式＋`{outputs, **inputs}` 上下文、case 级异常不毁批）＋迁移 047 `evaluations`（PK (tenant_id,id)、summary/cases JSONB、复合索引）＋两档 store（内存 capacity 200、PG 档 UPSERT/最新在前/clear 同清）＋registry `evaluation_store` 装配＋`POST /api/evaluations`（admin，同步跑批，404/422/403）＋`GET /api/evaluations`（admin，limit clamp 1..200）＋docs/03 三错误码登记。**落码期两处契约细化**（docs/110 顶部）：审批/等待预置（`preset_all_approvals`＋`preset_all_wait_events`，影子惯例、loader 零改动，离线批量不被真挂起拖死）与 verify 上下文（`{{outputs.<node>.<field>}}`/`{{<input键>}}`、缺失 → False）。黄金 5 例对账 `task_success_rate=0.8`/`decision_accuracy=0.75`。**门（实跑）**：后端全量 **2517 passed／172 skipped**（基线 2500/171＋17）、守护门 8 passed、PG 集成 1 passed（5433 临时库：迁移 047 幂等＋两档写读对账＋跨租户隔离）。前端零改动／零新依赖／无 ADR。

## 打包 AE 落码收口＝condition LLM 分支置信度阈值＋上下文字段级脱敏

✅ **打包 AE 落码收口＝condition LLM 分支置信度阈值＋上下文字段级脱敏（2026-10-09 立项+同日收口，承接用户「第一批搞了、第二批能搞的都搞」对 docs/111 的批复；docs/111 §一；docs/13 打包 AE 小节 U1285–U1292；docs/14 D14 再取回两半、D14 整体不解除）**：复筛 D14 余部实测两条工程内缺口并闭合——① **上下文脱敏漏网通道**：`_execute_llm_condition` 构造 `context_text` 直接 `json.dumps({"global","nodes"})`、未走 redact（打包 A3 脱敏只覆盖结果投影/录制/观察/日志），env/secret 展开值明文出向 LLM 供应商；抽 helper `_sensitive_redaction_mapping(state)`（从 `state["sensitive"]`＋global 构造 `{展开值:<redacted:{source}:{ref}>}`）与 `_redact_outputs` **同源**、后者改为复用，context 序列化前对整个 payload 走 `redact_sensitive`（mapping 空零变化）；② **置信度阈值**：classifier Protocol 与三实现统一加 `confidence_threshold`，LiteLLM `_SYSTEM_PROMPT` 改要求 `{"branch","confidence"}`；配阈值时置信度缺失/非数值/越界（fail-closed）或严格低于阈值 → 抛 `ConditionClassifyError`，loader 现有 catch 走 `__default__`/defaultTarget 并记 llm_errors（condition 的 fail-safe 本就走默认分支，不引入人工挂起）；未配阈值不判定、向后兼容；Scripted 回放忽略阈值；loader 从 config 读 `confidenceThreshold`（仅接受 0–1 数值、排除 bool、坏值忽略不阻断），DSL 编译期越界/非数值 → 新码 `COND_LLM_THRESHOLD_INVALID`。前端 condition.schema 加 0–1 number、nodeUiSchemas 加 label/placeholder 且仅 LLM 模式显示、validation.json zh/en 文案。**两处落码偏差**（docs/111 §7）：节点表单 label 现状硬编码中文（nodeUiSchemas 整体不走 t()），阈值字段照同族加中文 label、未单独立 i18n 键；错误码确无合适既有码可复用、新增登记于 frontend validation.json。**门（实跑）**：后端全量 **2533 passed／172 skipped／0 failed**（+16；首次全量 1 例 live-stream 偶发、复跑 0 failed 证实非回归）、前端 vitest **897 passed／2 skipped**、oxlint 0/0、tsc 0、build 过；既有 classifier 替身 4 文件签名跟进。零新依赖/零迁移/无 ADR。dev，未 push。

## 打包 AF 落码收口＝条件表达式 choice/加权采样＋命名时区

✅ **打包 AF 落码收口＝条件表达式 choice/加权采样＋命名时区（2026-10-09 承接用户「第一批给搞了，第二批把你能搞的给搞了」批复；形状权威 docs/112 §8 收口注记；docs/13 打包 AF 小节 U1293–U1305；docs/14 D15 行落码收口注记；docs/08 打包 AF 收口块；dev，未 push）**：D15 再取回两片（D15 整体不解除）。`choice(*items)` 均匀、`weightedChoice(item,weight,…)` 按权重（候选项同类型、权重非负且总和>0、0 权重项不选、随机只来注入 RNG、rng_seed 回放）；`dateOfInZone/hourOfInZone`（确定性折叠）、`todayInZone/hourInZone`（非确定），后端 stdlib zoneinfo、前端 Intl，仅取 date/hour 分量、不扩展 DateTimeValue；新码 COND_INVALID_TIMEZONE（zh/en）。门：后端 +32（choice 18/时区 14）、前端 vitest 906/2、oxlint 0/0、tsc 0、build 过。三处偏差（§2.2 表 UTC 03:00 勘误、新增 export evaluateConstantExpression、Intl 构造期非法时区转译）见 docs/112 §8。零新依赖/零迁移/无 ADR。

## 打包 AG 落码收口＝监控运行报表聚合与版本对比

✅ **打包 AG 落码收口＝监控运行报表聚合与版本对比（2026-10-09 承接用户「第一批给搞了，第二批把你能搞的给搞了」批复；形状权威 [docs/113](docs/113-监控运行报表聚合与版本对比-打包AG-v1批契约设计.md) §6 收口注记；docs/13 打包 AG 小节 U1306–U1316；docs/14 D28 行落码收口注记；docs/08 打包 AG 收口块；dev，未 push）**：D28 再取回「长保留报表＋版本对比」最小片（D28 整体不解除）。聚合纯函数 `aggregate_runs`（按天 UTC `YYYY-MM-DD`／按版本 `v<int>`、None→`manual-draft`，total/成败计数/成功率/avg·p50·p95 nearest-rank，空列表→空列表）；两档 `list_runs_for_report`（窗口 `[since,until)`，内存过滤 ring、PG 参数绑定 started_at TEXT 字典序）；端点 `GET /api/monitoring/report`（days 1–90、group_by day|version）与 `/report/export`（csv 中文表头＋UTF-8 BOM／json）；前端 `RunReportCard`（Segmented/Select/聚合表/导出，httpOnly Cookie 自动带凭证）＋i18n zh/en。**版本口径**＝resolved_version 为准、None 归 manual-draft、不新增字段不回填。门：后端全量 **2578 passed／173 skipped／0 failed**（基线 2565/172 ＋ 聚合 5 ＋ 端点 8；PG 集成 U1312 过 5433）、前端 vitest **909 passed／2 skipped**（＋3）、oxlint 0/0、tsc 0、build 过。零新依赖/零迁移/无 ADR。

## 打包 AH 落码收口＝影子运行 SSE 流式化

✅ **打包 AH 落码收口＝影子运行 SSE 流式化（docs/114；2026-10-09）**：新端点 `POST /api/graphs/{graph_id}/shadow-runs/stream`（operate，StreamingResponse；后台 daemon 线程跑 run_graph(shadow=True)，节点事件经 queue 实时 SSE、终帧 event:result 携带完整 ShadowRun；异常沉淀 status=error、HTTP 不报错、不设 event:error），影子纪律逐字不变；前端 apiClient.streamShadowRun＋ShadowRunModal「实时进度」顶层节点 running→done Tag＋i18n。门：后端 2582/173/0（基线 2578 ＋流式 4）、前端 915/2（基线 909 ＋apiClient 3 ＋Modal 3）、oxlint/tsc/build 过；浏览器冒烟三节点三绿、自动退款、SHADOW_DRY_RUN。零新依赖/零迁移/无 ADR。D26 不解除（自动旁路/趋势对比/跨租户共享/长保留归档仍缓做）。

## 打包 AI 落码收口＝D29 SchemaRegistry 第四实体接入

✅ **打包 AI ＋ 第二批 docs-only 收口＝D29 第四实体评估 / D21·D32 整体评估 / D49 MCP 加面预备契约（docs/115–117；2026-10-09）**：纯文档、无代码/迁移/依赖。D29 第四实体（部署 RolloutModal）经实证阻断于「自定义控件契约＋oneOf/discriminator 异构数组」、暂不落码（技能/记忆无配置面、触发未到）；D21/D32「钉版＋体检＋手动升级＋升级后回归」已闭环、工程侧收尽，剩余外部化；D49 三加面方向（HTTP 远端/写能力/resources·prompts·扩展）已预契化、触发未到，真做先记 ADR。**工程内干净候选再次清空，剩余未闭合项触发全部外部化。**

✅ **自定义控件扩展契约立项＝D29 解锁路径第 1 步（docs/118；2026-10-10，docs-only）**：把 `registerWidget` 从「M3 预留」升级为正式契约——注册形状与名称规则（禁内置九件冲突、`<domain>-<semantic>` 命名空间、同名覆盖）、组件签名静态校验、`x-widget` 声明与消费范围（node/card 认、tool 忽略；WidgetName 拓宽为「内置∪已注册业务名」、resolveWidget 不再断言绕过）、props 契约（WidgetProps 基线＋schema `x-*` 自读、无新增注入通道、受控单一）、运行期降级（未注册名→json＋warn、开发期 throw 保留）、契约测试清单。**非目标**：异构数组（第 2 步）、RolloutModal 迁移（第 3 步）、新业务控件、解除 D29。落码批待契约通过后另立批（当日已落码收口，见上条）。

## 自定义控件扩展契约落码收口＝D29 解锁路径第 1 步

✅ **自定义控件扩展契约落码收口＝D29 解锁路径第 1 步（docs/118；2026-10-10，同日契约通过/落码/收口）**：`registerWidget` 升级为正式契约并落地——名称规则（禁内置九件冲突、`<domain>-<semantic>`、同名覆盖）＋组件签名静态校验；`WidgetName` 拓宽为「内置九件∪已登记业务控件名」（`BUSINESS_WIDGETS` 常量表、nodeRegistry 注册与声明同源）、`resolveWidget` 去断言、未登记名按类型默认走；运行期降级 json＋`console.warn`（get() throw 保留）；契约测试 17 例。门：前端 vitest 932/2（+17）、oxlint/tsc/build 过、守护门 8 passed。零新依赖/零迁移/无 ADR。第 2/3 步（异构数组、RolloutModal 迁移）仍缓做，D29 不解除。
