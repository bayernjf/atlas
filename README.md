# Atlas — AI 运营体编排平台

> 让非技术人员用自然语言或拖拽定义一个流程，由 AI 运营体自动执行、自愈与进化。

Atlas 是一个 **AI 运营体（Agent）编排平台**：以 **Harness / Graph / Loop** 三位一体为内核——

- **Harness**：能力接入层。把外部平台（浏览器、API、数据库、消息）抽象为统一适配器，运营体通过 Harness Gateway 调用真实世界能力。
- **Graph**：可执行因果有向图。流程即图：节点 + 连线 + 数据流，支持拖拽编辑与 DSL 编译（LangGraph）。
- **Loop**：OODA 主循环（感知 → 规划 → 执行 → 反馈）。让运营体在图上循环运转，异常自愈、反思进化。

目标场景从企业内部 OA 审批起步，逐步扩展到电商、物联网等异构场景，最终形成"运营体市场"生态（用户发布训练好的运营体，他人一键导入复用）。

## 当前阶段

**题述工程化落地路线（docs/19/20）M1–M10 已全部闭合**（2026-09-18），**M11 记忆/长期上下文已落码收口**（2026-09-19，四批 `5441902`/`58d936c`/`73e53bd`/`75c4150`）：

- **题一（Schema 驱动配置内核）M1–M4 闭合**：M1 节点 Data Schema 提取（零 UI 变化）、M2 L1 字段校验扶正 + 结构化诊断（nodeId/RFC6901 pointer/token range）、M3 WidgetRegistry + FormRenderer 工具 params 表单化、M4 UISchema 条件显示/增量调度/Problems 面板/reverseDeps/quickFix。
- **题二（多智能体端到端）M5–M10 全闭合**：M5 持久化 + 中断恢复（M5a 进程内 Repository 重构、M5b PG 实现 + 中断帧落库 + 恢复扫描器 + run 状态 + `GET /api/runs`）、M6 Graph 版本化（不可变 releaseVersion + subgraph 钉版）、M7 多 Bot 任务总线（任务信封状态机 + 幂等/CAS + 退货退款协同沙盘）、M8 交互模板系统（审批卡片双向 Schema + web/im/email 三渠道渲染降级）、M9 入站 Router + 灰度发布 + 指标门控自动回滚、M10 span 级链路追踪。
- **缓做项提前取回**：D26 用例集报告 v1（通过率历史趋势/导出/定时回放 CI）、D30 余部（数据依赖环/类型 warning/作用域语义）、A+B 打包六项（D15 表达式函数库、D17 loop break·continue、D18 parallel any_success、parallel.result/subgraph.outputs 可见性、改节点 id quickFix 联动）。
- **M11 已落码收口（2026-09-19）**：统一记忆条目（fact/preference）+ 本地确定性 256 维词法向量（纯 stdlib）+ memory/remember·recall 两适配器工具 + 进程内/PG(pgvector) 两档 + REST 浏览/语义搜索/删除 + 前端记忆页；后端 673/前端 449、两档 m11_smoke 全过、d26 零回归。形状权威 [docs/26](docs/26-M11记忆长期上下文契约设计.md)（ADR T23）；商业 embedding/working/summary/case/自动提取等产品化余部仍缓做 D35。
- **M11 之后是「真实接入与交付」批链（docs/27–91，2026-09-20 → 2026-10-01）**，逐批立项与落码条见 [docs/08](docs/08-任务迭代计划.md)，能力面按组：**〔2026-10-04 勘误：批链已延至 docs/94、时间至 2026-10-04——docs/92（反思 L2 v2 前端候选呈现）／docs/93（LLM 模型配置管理面：内置 admin＋BYOK per-tenant）／docs/94（打包 ZU：候选采纳状态持久化＋节点级定位）均已立项并落码收口，能力面见 docs/08 对应收口块〕〔2026-10-06 勘误：批链再延至 docs/95、时间至 2026-10-06——docs/95（打包 AV：首登强制改密）已落码；另一条「错误码到得了人」批链（打包 BC→BJ，2026-10-05→10-06，不另立编号文档、形状见 docs/08 各批块）：五条一码多话粗码拆 46 码、docs/14 D55 冻结桶 27→4，渠道/连接 4xx `detail` 由中文字符串改成结构化 `{code,message[,params]}`，BI 闭合 D54〕〔**2026-10-08 勘误：批链再延至 docs/100、时间至 2026-10-08**——docs/96（打包 BL：认领后崩溃挂起帧的人工收敛）与 docs/97–100（打包 A1–A4：模板库产品化收尾／消息模板系统／图变量受限来源／动态审批人与指派校验）均已立项并落码收口，**A-4 由此 ✅、D55 由 BK 清零、D36 人工半边由 BL 收口**（安全重放另登 D56）。同批还有一条**不是新功能的产品批＝打包 BM**：把客户向 TRIAL.md 当脚本逐字跑了一遍（docs/18 立项 25 天来首次执行，§6.2 有 13 PASS／3 FINDING 与耗时读数），并修掉它抓到的 litellm 首次导入死锁——**这条批链的性质与前面不同：它产出的不是能力面，是"客户跑起来什么样"的第一份实测**〕**
  - **真实接入**：Shopify 渠道适配与入站 Webhook（HMAC 验签/幂等环/订阅注册）、OpenAPI 导入自动生成工具（规格 PG 持久化、securitySchemes 静态密钥、HTTP Basic 子集、去重软删）、通用 HTTP 与数据库渠道、出向安全准入（SSRF 校验 + 凭证信封 AES-256-GCM 加密与脱敏）。
  - **审批与人机协同**：审批卡片三渠道渲染、邮件内一键决策（HMAC 签名 capability URL，免登录）、已决审批历史（打包 H 起 PG 落库）、审计日志页与写操作审计中间件。
  - **可靠性与运维**：告警中心、静默/值班（PG 化 + 惰性按日自动轮换）、告警外部通知与投递退避、外部通知 flapping 抑制、灰度门控自动回滚、影子运行（旁路决策比对，打包 H 起 PG 落库）。
  - **运行时体验**：span 级链路追踪、单步调试与条件断点、wait 三形态（动态时长/到点时刻/事件等待，事件帧跨重启）、condition LLM 语义分支、loop foreach 批处理、编译诊断逐条定位（打包 H 起后端 422 侧车在前端 Problems 面板兑现）。
  - **产品化**：en-US 全量翻译与运行时语言切换、设计 Token 层、用例集报告 PG 持久化与跨图看板、审计过滤 + 游标分页。
  - **对外协议出入口**：**A2A 执行 Agent 面 v1 已落码**（`src/atlas/a2a/`：公开 Agent Card ＋ `POST /api/a2a/tasks`，两个 plan-only skill，独立 Bearer，接进 Zeus 协同决策平台 W2；形状权威 [docs/90](docs/90-A2A执行Agent面-Zeus协同决策平台接入-v1批契约设计.md)／ADR T32）；**MCP server 面 v1 已落码收口**（官方 `mcp` Python SDK v2 ＋ stdio ＋ 只读/plan-only 工具面，`src/atlas/mcp/` 四文件＋U1033–U1045，形状权威 [docs/91](docs/91-MCP-server面-v1契约设计.md)／ADR T33）。
  - **自我进化**：反思进化 **L2 v1（单运营体反思）已落码**——`src/atlas/reflection/` 白名单调参候选 ＋ 未配 LLM 确定性降级，复用调度器（迁移 035 加 `action` 列）触发，只读报告端点；**无自动 apply／publish／promote**（形状权威 [docs/88](docs/88-反思进化模块-v1契约设计.md)）。**L2 v2／v3 亦已落码**：前端候选呈现页与人工采纳引导（[docs/92](docs/92-反思进化L2v2-前端候选呈现与人工采纳入口-v1批契约设计.md)）、候选采纳状态 PG 化与节点级定位（迁移 041，[docs/94](docs/94-反思进化L2v3-采纳状态持久化与节点级定位-v1批契约设计.md)）。
  - **错误码面**：4xx 响应统一结构化（`detail:{code,message[,params]}`）——此前渠道/连接折叠点写中文字符串，异常上的 code 进不了体；condition 表达式诊断补机器可读通道（docs/14 D54 闭合）；docs/14 **D55 桶 27→4**（余 4 条渠道粗码待打包 BK 拆码）。〔2026-10-08 订正：该桶已由**打包 BK 于 2026-10-06 清零（4→0）**，D55 已闭合——本行原文停在 BJ 时点，属入口文档的滞后读数。〕
  - 验证命令：后端 `.venv/bin/pytest`、PG 直连见 [docs/13](docs/13-测试用例清单.md)、前端 `pnpm vitest run` / `pnpm run lint` / `pnpm build`；**最近一次门结果与推送状态以 [handoff.md](handoff.md) 为准**（本文件不复制会随下一次提交过期的数字）。
- **W1-W10 Demo（2026-09-13）为基线**：电商退款端到端链路（webhook 退款单 → AI 决策 → shop 适配器执行退款或转人工，SSE 实时上屏，NL 生成草稿）；规则兜底离线可跑，配置 `LITELLM_MODEL` 即用真实 LLM。〔2026-10-08 试用干跑补三条实测事实，权威 [docs/18 §6.2](docs/18-种子客户验证计划.md)：① 商家控制台现在把**「AI 已自动退款」与「AI 转人工」分开显示**并附判断理由——此前两者都只表现为"单子从待处理列表消失"，场景 B 要演示的边界在页面上不可观察；② 这条链路的"转人工"**只写店铺状态，不产生任何人工待办**（审批队列实测为空），要看到真挂起等人，在画布上把「人机协作」节点拖进流程（该演示形状本身是否改成主路径＝docs/14 **D57** 待拍板）；③ 配了模型但端点不可达时，旧版会因 litellm 首次导入死锁而**整个进程失响应**，现已在启动时串行预热导入修掉（U1208–U1212）。〕
- 设计文档以 `docs/` 为唯一事实源；当前状态与待办见 [handoff.md](handoff.md)，排期见 [docs/08](docs/08-任务迭代计划.md)，种子客户计划见 [docs/18](docs/18-种子客户验证计划.md)。

### 本地运行 Demo

Docker 一键启动（Phase 1 种子客户交付形态，只需 Docker）：

```bash
docker compose up --build        # http://localhost:8000 先到**登录页**：用页面上印着的演示账号 admin-a / admin123 登录，再点「打开流程编辑器」
                                 # 模拟商家控制台在 /demo/shop（demo/demo）
```

> 〔2026-10-08 订正〕这里原来写的是 `curl -X POST http://localhost:8000/api/demo/reset`——**匿名调用会 401**，该端点自 M6 起就是 admin only（docs/12 档位表）。重置归陪同人员（带平台凭证调用），试用者换一单未跑过的即可；另注意**试用数据是持久化的**（compose 钉 `ATLAS_STORAGE_BACKEND: pg`，数据在 named volume `atlas-pgdata` 里，重启容器不清零），要真的回到出厂状态用 `docker compose down -v`。

种子客户试用按 [TRIAL.md](TRIAL.md) 一页纸操作（三场景 + 反馈表）。

> **只能单副本运行**（一个进程、一台机器）。多进程会让同一条挂起审批帧在各进程各自续跑一次——2026-09-25 实测：一次人工"通过"后，一个进程走通过分支、另一个进程超时走拒绝分支，下游真实副作用被执行两遍（证据与机制见 [docs/34](docs/34-MVP上线就绪评审-2026-09-22复审.md) 的复审更新注记）。容器入口对 `workers>1` 直接拒绝启动，compose 已钉 `replicas: 1`；[docs/62](docs/62-挂起帧一次性认领-单副本硬约束护栏-v1批契约设计.md) 的帧一次性认领**已于 2026-09-25 落码**（一次审批下游只跑一遍），但**这不代表支持多副本**：登录节流、`/metrics` 聚合、灰度运行态、跨进程急停仍是进程内。部署面约束全文见 [docs/15](docs/15-环境与分支策略.md) §四。

> **prod 档位（`ATLAS_ENV=prod`）另需三样**：`ATLAS_MASTER_KEY`、`ATLAS_APPROVAL_HMAC_SECRET`（各 ≥32 字节；主密钥还要是 base64url 解码后**恰 32 字节**的 AES-256 密钥——`secrets.token_urlsafe(48)` 解出 48 字节会在加密后端 fail-closed 中止，用 `token_urlsafe(32)`），以及首任管理员引导口令 `ATLAS_ADMIN_BOOTSTRAP_PASSWORD`（[docs/66](docs/66-prod首任管理员引导批-v1批契约设计.md)）——任一缺失或不合规则，进程**拒绝启动**（fail-closed）。缺省档位是 `dev`，上面的 Demo 形态不需要任何密钥。**档位大小写不敏感**（`PROD` 也算 prod），写非法值（如 `production`）同样是起不来。**用引导口令登进来的账号会被强制先改密**（[docs/95](docs/95-首登强制改密与口令轮换位-v1批契约设计.md)）：改密之前任何业务端点都回 403 `AUTH_PASSWORD_CHANGE_REQUIRED`，界面上是一个关不掉的改密框，只有改密这条路可达，所以既绕不过去也不会把自己关在门外；演示形态（`ATLAS_ENABLE_DEMO_MOCK=1`）与 dev 不受此约束。
>
> **prod 档下演示用的模拟面默认全关**（2026-09-27 [docs/75](docs/75-demo模拟面prod闸门收口批-v1批契约设计.md) 打包 P）：`/demo/shop` 商家控制台与 `/api/demo/shop/*`、`/api/demo/mock/*` 一律 **404**；需要对外演示时显式设 `ATLAS_ENABLE_DEMO_MOCK=1`（一个开关开全，不做按路由细粒度）。dev/test 档行为不变——上面的 Demo 形态照旧可跑。哪些路由不需要凭证就能打，现在是一张机器枚举的 allowlist 守着（`tests/test_demo_surface_prod_gate.py`）。

> **定时触发（`schedule/cron` 节点）已有调度器**（2026-09-26 [docs/68](docs/68-定时触发调度器批-v1批契约设计.md) 打包 N 落码收口）：随进程起一条 tick 线程，同图按发布钉版派发。**运营要记住三条**——cron **一律 UTC**（`0 9 * * *` 是北京时间 17:00）、**重启/停摆错过的槽位不补跑**（宁漏不重跑，下游副作用不幂等）、**只跑已发布版本**（改草稿 cron 不生效，要重新发布）。开关见 `.env.example` 的 `ATLAS_SCHEDULE_ENABLED`／`ATLAS_SCHEDULE_TICK_SECONDS`；控制台「定时调度」页与画布里的 cron 预演（接下来三次 UTC 槽位）已随 ⑤ 落码。

> **卡住的运行现在查得到**（2026-09-27 [docs/76](docs/76-挂起点只读投影批-D42收口-v1批契约设计.md) 打包 Q）：单副本 at-most-once 的代价是"进程在续跑途中崩溃 ⇒ 该运行永久停在 suspended、不自动重放"（[docs/62](docs/62-挂起帧一次性认领-单副本硬约束护栏-v1批契约设计.md) §2 D-1）。以前这件事只有重启翻 INFO 日志才看得见，现在 `GET /api/interruptions` 或控制台「等待与任务 → 挂起帧」直接列出：**已认领却仍挂起**（`claimed_suspended`）就是那种现场，另给认领者与已认领时长让你判读。**这个视图只读**——不重放、不清帧、不改状态；要不要重放属 D36，仍是未决事项。**内存档下这张表恒空且会自报"看不见"**（挂起帧只在 PG 档入库），别把空列表读成"没有卡住的运行"。

本地开发双进程：

```bash
cp .env.example .env                                        # 首次：填 LITELLM_MODEL / OPENAI_API_KEY / OPENAI_BASE_URL
.venv/bin/uvicorn atlas.api.main:app --reload --env-file .env --port 8000   # 后端 API + 模拟商家控制台
cd frontend && pnpm dev                                     # 编辑器 http://localhost:5174
```

- 模拟商家售后控制台：http://localhost:8000/demo/shop（demo/demo）
- 配置 `LITELLM_MODEL` + `OPENAI_API_KEY` + `OPENAI_BASE_URL` 即用真实 LLM 决策/生成；不配置时走确定性规则，Demo 离线可跑（见 `.env.example`）。**三者缺任一都发不出真实调用**——`LITELLM_MODEL` 单独配只切换决策器类型，key/base 才是端点凭据；变量名是 `OPENAI_*` 而不是 `LITELLM_API_KEY`/`LITELLM_BASE_URL`（后者只被 litellm 的 proxy 代码读取，直连路径不生效）
- `--env-file .env` 让 uvicorn 读 `.env`（项目本身不加载它）；`docker compose` 形态由 compose 自动读 `.env` 做变量替换，无需该参数

## 文档导航

| 想看什么 | 去读 |
|---|---|
| 从哪开始 / 治理规则 | [docs/00-文档索引与治理.md](docs/00-文档索引与治理.md) |
| 项目当前状态与待办 | [handoff.md](handoff.md) |
| 产品需求与 Demo 范围 | [docs/01-PRD-产品需求规格.md](docs/01-PRD-产品需求规格.md) |
| 架构与选型 | [docs/02-技术架构设计.md](docs/02-技术架构设计.md)（决策记录见 [10](docs/10-技术选型决策记录.md)）|
| 数据结构 / 接口 / 测试 | [03 契约索引](docs/03-数据模型与Schema-契约索引.md) / [12 API](docs/12-API与模块接口清单.md) / [13 测试](docs/13-测试用例清单.md) |
| 排期与里程碑 | [docs/08-任务迭代计划.md](docs/08-任务迭代计划.md) |
| 缓做事项 / 环境与分支 / 反馈工作流 | [14 缓做登记](docs/14-缓做事项登记表.md) / [15 环境与分支](docs/15-环境与分支策略.md) / [16 反馈工作流](docs/16-反馈工作流.md) |
| 贡献 / 迁移 / 基准 | [CONTRIBUTING.md](CONTRIBUTING.md) / [MIGRATION_CONVENTION.md](MIGRATION_CONVENTION.md) / [BENCHMARK.md](BENCHMARK.md) |
| AI 落码规范 | [AGENTS.md](AGENTS.md) + [09 工程骨架](docs/09-工程骨架与目录结构.md)（前端 i18n/视觉契约见 [17](docs/17-前端国际化与设计Token方案.md)） |

## 技术栈（已定选型）

Python 3.11+（引擎）/ Go（Harness 网关产品化目标，Demo 暂用 FastAPI）/ TypeScript + React 19（满足 React 18+）/ **LangChain + LangGraph** / LiteLLM / Playwright + OmniParser（**浏览器自动化本轮显式非目标**：`web-playwright` 不注册进运行期适配器注册表，三层定位与工具实现已在 `src/atlas/web/`、但图上不可达，决策见 [docs/63 §0A N3](docs/63-项目级上线复审-2026-09-25第三次.md) 与 [docs/01 §4.3](docs/01-PRD-产品需求规格.md)）/ PostgreSQL + pgvector / Demo 进程内事件总线（NATS 留 Phase 2）/ `@xyflow/react`（React Flow 12）+ Zustand + Ant Design / FastAPI。

> **「短期记忆」这一档没有独立存储**（2026-09-27 起选型行不再写 Redis）：`redis` 声明依赖已删——它对 `src/`·`tests/`·`scripts/` 零引用却挂着，等于白付安装面还让文档说谎。**别把 `src/atlas/memory/` 的 per-tenant `MemoryStore` 当成它的替代**：那是「长期事实记忆」档（docs/06 §6.3），短期工作记忆的职责由 run 的 `outputs`/`globals` 承担、不另建（docs/26 §1.2）。真要跨进程共享记忆时按 [docs/10 §4 T31](docs/10-技术选型决策记录.md) 另立 ADR 再引入。

> T1-T5 已于 2026-09-13 收口，见 [10 技术选型决策记录 §3](docs/10-技术选型决策记录.md)。

## 路线图

**题述工程化路线（当前主线，M1–M10 已闭合）**：题一 Schema 内核 M1–M4 → 题二多智能体 M5（持久化+中断恢复）→ M6（版本化）→ M7（任务总线）→ M8（交互卡片）→ M9（灰度回滚）→ M10（span 追踪）→ **M11 记忆/长期上下文（2026-09-19 四批落码收口，产品化余部缓做 D35）**。拆解、前置门与验收见 [docs/20](docs/20-题述方案工程化落地路线.md)，逐里程碑立项/落码条见 [docs/08](docs/08-任务迭代计划.md)，M11 形状权威见 [docs/26](docs/26-M11记忆长期上下文契约设计.md)。

**原始阶段路线（背景参考）**：

- **Phase 1** 最小可行引擎（2 个月）：固定操作 Graph + 基础 Harness + 单步决策 Loop，跑通一条审批流程。
- **Phase 2** 动态反馈与 DIY（3 个月）：异常处理 Loop、反思模块、可视化 Graph 编辑器，自然语言定义流程。
- **Phase 3** 平台化适配（3 个月）：开放 Harness 适配器规范，接入电商 + 物联网等异构场景。
- **Phase 4** 智能体市场与进化（持续）：运营体发布与一键导入，策略飞轮。

Demo 里程碑 W1-W10 与验收标准见 [08 任务迭代计划](docs/08-任务迭代计划.md)。

## 给 AI 协作者的说明

接手本仓库先读 [handoff.md](handoff.md)（状态 + 待办 + 文档索引），写代码前读 [AGENTS.md](AGENTS.md)（行为规范与治理规则）。

## License

**不开放（proprietary）**——无 LICENSE 文件，`pyproject.toml` 与 `frontend/package.json` 亦无 license 字段。MIT 曾于 2026-09-22 短暂落地（`c40b46c`），同日由用户决策删除并保持不开放（`f721c18`）；决策记录见 [docs/08](docs/08-任务迭代计划.md) 与 [docs/34](docs/34-MVP上线就绪评审-2026-09-22复审.md) 的 2026-09-22 更新注记。

