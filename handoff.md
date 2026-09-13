# Handoff — Atlas

更新时间：2026-09-13

## 项目概况

Atlas 是 AI 运营体（Agent）编排平台：以 **Harness（能力接入）/ Graph（可执行因果图）/ Loop（OODA 主循环）** 三位一体为内核，面向企业流程自动化场景（首个目标场景：企业内部 OA 审批），目标是让非技术人员通过自然语言/拖拽定义流程，由运营体自动执行、自愈与进化。

当前阶段：**W1-W10 Demo 全部完成（W9-W10 电商退款端到端 Demo 于 2026-09-13 跑通，08 §7.3 七条验收全部达成）**。退款链路：webhook 退款单 → AI 决策（LiteLLM；未配置 `LITELLM_MODEL` 时规则兜底，对齐 06 §9.2 黄金用例）→ shop 适配器执行退款或转人工；SSE 节点事件实时上屏，自然语言可生成退款流程草稿。下一步进入 Phase 1 种子客户验证与缓做项触发（Redis 短期记忆、业务表 DDL、web 视觉 LLM、Go 网关等，见 docs/14）。

结构：
- `docs/` — 全部规格文档（入口：`00-文档索引与治理.md`）
- `README.md` / `AGENTS.md` / `CLAUDE.md` / `git-commit-message.md` / `CHANGELOG.md` — 根目录元文档
- `src/atlas/` + `frontend/` — OODA 最小循环、Graph DSL 编译运行（FastAPI）、前端编辑器核心（拖拽/配置/变量/一键编译运行）已跑通；规划见 `docs/09-工程骨架与目录结构.md`

## Project documents（文档地图）

**文档地图（按场景怎么读 + 治理规则）**：[docs/00-文档索引与治理.md](docs/00-文档索引与治理.md) 是单一事实源与 AI 工作入口，本区只做直达索引：

* [docs/01-PRD-产品需求规格.md](docs/01-PRD-产品需求规格.md) — 产品概念（Harness/Graph/Loop 定义、全场景图谱、DIY 机制）+ 产品定义 + Demo 范围
* [docs/02-技术架构设计.md](docs/02-技术架构设计.md) — 三位一体架构、系统分层、技术选型、组件全景
* [docs/03-数据模型与Schema-契约索引.md](docs/03-数据模型与Schema-契约索引.md) — 全部 Schema 位置速查与字段概览
* [docs/04-组件设计-编辑后台.md](docs/04-组件设计-编辑后台.md) — 编辑后台组件（节点/工具/适配器/逻辑/变量/上下文）
* [docs/05-组件设计-运营体五项核心.md](docs/05-组件设计-运营体五项核心.md) — Skill / Memory / 智能体协同 / 部署方式 / 自定义前端模板
* [docs/06-运行时与质量保障.md](docs/06-运行时与质量保障.md) — Loop 主循环、决策节点、Harness Gateway、Web 适配器、测试层次、安全清单
* [docs/07-递归自动化引擎设计.md](docs/07-递归自动化引擎设计.md) — 平台探索器 / 行为挖掘器 / 诊断修复器 / 元运营体 / 自我进化
* [docs/08-任务迭代计划.md](docs/08-任务迭代计划.md) — 开发路线图、Demo 计划 W1-W10、Phase 1-4 迭代路线、商业化路径、验收标准
* [docs/09-工程骨架与目录结构.md](docs/09-工程骨架与目录结构.md) — 工程目录树、模块→文档映射、待填项清单 ★落码必读
* [docs/10-技术选型决策记录.md](docs/10-技术选型决策记录.md) — 选型 ADR（✅ 已定 / ⏳ 待决策）、Demo 依赖清单 ★落码必读
* [docs/11-存储与数据持久化设计.md](docs/11-存储与数据持久化设计.md) — 存储分层、数据对象→表映射、读写路径
* [docs/12-API与模块接口清单.md](docs/12-API与模块接口清单.md) — 引擎内部接口、Harness Gateway、后端 REST、协同/评估接口
* [docs/13-测试用例清单.md](docs/13-测试用例清单.md) — 单元/集成/回放/端到端/AI 评估用例 + 上线前四道门
* [docs/14-缓做事项登记表.md](docs/14-缓做事项登记表.md) — 缓做/低优事项登记表（每条带触发条件，单一事实源）
* [docs/15-环境与分支策略.md](docs/15-环境与分支策略.md) — 环境划分（DEV/TEST/PROD）+ 分支→环境映射（main/dev/feature/*）
* [docs/16-反馈工作流.md](docs/16-反馈工作流.md) — 用户与 AI 协作者的反馈方式（截图+标签 / computer use）
* [docs/17-前端国际化与设计Token方案.md](docs/17-前端国际化与设计Token方案.md) — i18n（i18next 触发式引入/错误码边界）+ 设计 Token 三层模型（需求依据 04 §28；方案已定，待落码）
* [docs/18-种子客户验证计划.md](docs/18-种子客户验证计划.md) — Phase 1 种子客户画像/招募标准、三场景试用脚本、Go/No-Go 指标、反馈机制（客户向操作指南见根目录 TRIAL.md）

根目录元文档：[CHANGELOG.md](CHANGELOG.md)（变更日志）/ [CONTRIBUTING.md](CONTRIBUTING.md)（贡献指南）/ [AGENTS.md](AGENTS.md)（AI 行为规范，**新会话必读**）/ [TRIAL.md](TRIAL.md)（种子客户试用一页纸）/ [git-commit-message.md](git-commit-message.md)（commit 详细规范）/ [MIGRATION_CONVENTION.md](MIGRATION_CONVENTION.md)（DB 迁移规范）/ [BENCHMARK.md](BENCHMARK.md)（性能基准记录表）/ [.gitleaks.toml](.gitleaks.toml)（密钥扫描配置）/ [.env.example](.env.example)（环境变量模板）

**代码阶段待补（遵循 agent-world 惯例）**：`.pre-commit-config.yaml`、CI 工作流（`.github/workflows`，见 14 D10）。`Dockerfile` + `docker-compose.yml` + `.dockerignore` 已在 Phase 1 准备期落地（2026-09-13）。`pyproject.toml` 与前端工程已在 W1 创建。

## Current state（当前状态）

- **阶段**：W9-W10 电商退款端到端 Demo 已完成（2026-09-13，08 §7.3 七条验收全达成）。新增 `llm/`（LiteLLM 退款决策 + 规则兜底 + NL 草稿生成）、`shop/`（DemoShopService + ShopHarnessAdapter：login/list_pending_refunds/execute_refund/request_human_approval/process_refund）两个包；`graph/loader.py` 经依赖注入接真实决策/适配器/事件回调；API 新增 SSE 运行流、NL 生成、适配器发现与模拟商家控制台；前端退款单选择、节点实时状态、NL 草稿载入。后端默认 58 单元全绿（+1 店铺集成 opt-in），前端 vitest 23 全绿，浏览器实测 12345→refunded / 12346→human_review。
- **仓库**：git 仓库，**已推送 GitHub private 仓库 `bayernjf/atlas`**（main 为生产分支；当前工作在 `dev` 集成分支，W1-W10 落码提交**尚未 push**，分支策略见 15 文档）。
- **技术选型**：✅ 已全部收口（2026-09-13，T1-T5 见 10 文档 §3）：Python 3.11+ 引擎 / Go Harness 网关为产品化目标、**Demo 用 Python/FastAPI 实现同构接口** / TypeScript + React 19（满足 React 18+）/ LangChain+LangGraph / LiteLLM（Demo 对接主流商业 API，`.env` 切换，不引入 vLLM）/ Playwright / PostgreSQL+pgvector / Redis / **Demo 进程内事件总线替代 NATS** / FastAPI / `@xyflow/react` 12 + Zustand 5 + AntD 6 / 评估优化层仅留接口；剩余 ⏳（vLLM、策略训练、多语言）均为后续阶段范围。
- **Demo 依赖清单已落码**：后端在 `pyproject.toml`，前端在 `frontend/package.json` / `pnpm-lock.yaml`（版本为 2026-09-13 解析的稳定版）。
- **关键文件**：后端 `src/atlas/`（11 个包；engine 最小循环、graph DSL 契约+编译器（W9-W10 注入决策/适配器/事件）、llm 退款决策+NL 生成、shop Demo 店铺服务+适配器、api FastAPI（graphs/SSE/NL/adapters/Demo 控制台）、memory 连接层、harness 契约/注册、web 三层定位适配器）、`db/migrations/001_enable_pgvector.sql`，前端 `frontend/`（Dashboard/Editor + 画布/节点/属性/变量面板 + `lib/` 纯逻辑（变量/节点目录/序列化/API client 含 SSE）+ Zustand store + vitest 单测）；模块填充顺序见 09 文档待填项清单。

## Active work / 待办

按优先级降序：

1. ✅ **W1-W2 基础骨架落地**（08 文档 7.1，2026-09-13 全部完成）：工程骨架/venv、LangGraph 最小 OODA 循环、前端编辑器框架、PostgreSQL+pgvector 连接均已跑通（详见 Recently shipped）。
2. ✅ **W5-W6 编辑器核心功能**（08 文档 7.1，2026-09-13 完成）：拖拽画布、3 种节点类型化配置 + 实时校验、全局变量与 `{{路径}}` 引用、Graph JSON 导出；vitest 20 个单测全绿。
3. ✅ **W7-W8 编译与运行**（08 文档 7.1，2026-09-13 完成）：Graph DSL 契约 + 静态校验、DSL→LangGraph 编译/运行、FastAPI graphs 保存/读取/编译/运行、编辑器一键"编译并运行"；后端新增 16 个用例。
4. ✅ **W9-W10 端到端 Demo**（08 文档 7.1，2026-09-13 完成）：电商退款完整链路跑通（LiteLLM 决策 + 规则兜底、shop 退款业务能力、SSE 实时进度、NL 退款草稿、模拟商家控制台），08 §7.3 七条验收逐条达成（映射见 08 W9-W10 落码记录）。
5. ⏭️ **Demo 之后（Phase 1）**：种子客户试用收集反馈；条件触发时从 docs/14 缓做登记表取回（Redis 短期记忆、11 S1 业务表 DDL 替换进程内存储、web 适配器视觉层接真实 LLM、NATS/Go 网关、条件/循环/并行节点等）。
6. 文档缺口登记：`09-工程骨架` 待定项 1/2/3/4/5/6 均已收口（待定项 3：不新增 deployment/ 包，Dockerfile + docker-compose.yml 放仓库根，2026-09-13）。新增测试运行器选型 vitest 已记 10 文档 §4 ADR（W5-W6）。
8. 🐳 **Phase 1 种子客户交付准备（进行中）**：✅ Docker Compose 一键启动（`Dockerfile` 多阶段 + FastAPI 同源托管 `frontend/dist` + `POST /api/demo/reset` 重置种子数据，镜像实测黄金用例通过，2026-09-13）；✅ 种子验证计划 [docs/18](docs/18-种子客户验证计划.md) + 客户向 [TRIAL.md](TRIAL.md)（2026-09-13）；⏭️ 应用内反馈入口待做（编辑器反馈按钮 + 进程内 `POST /api/feedback`）。
7. 📋 **i18n 与设计 Token 方案已定（2026-09-13，docs/17）**：设计 Token 等价替换**已落码**（`frontend/src/theme/tokens.ts` 单一事实源 + `setup.ts` 注入 `--atlas-*` 变量 + AntD theme，全仓零硬编码色值，浏览器零视觉差异）；i18n 库（i18next+react-i18next）按触发条件引入（14 D12：首个英文使用者/出海需求），组件描述多语言随 Phase 2 模板库（14 D13）。

> 缓做/低优项：统一登记在 [docs/14-缓做事项登记表.md](docs/14-缓做事项登记表.md)（每条带触发条件，条件满足移回本区并标注重启日期）。当前含移动端适配器、策略训练、组件市场、模型路由器、NATS、Go 网关、多租户、运营体市场、BENCHMARK 实测、CI/CD、可观测性、i18n 落码、组件描述多语言等 13 项。

## Recently shipped（最近变更）

1. **feat(llm/shop/graph/api/web): W9-W10 电商退款端到端 Demo（2026-09-13）**——新增 `llm/`（`decision.py` DecisionClient：LITELLM_MODEL 配置时经 LiteLLM 决策、JSON 解析失败 fail-safe 转人工；未配置时 RuleBasedDecisionClient 按 06 §9.2 黄金规则确定性兜底；`nl_generate.py` NL→Graph 草稿，LLM 优先退款模板兜底）与 `shop/`（`service.py` DemoShopService 五笔种子退款单 + 退款/转人工状态流转；`adapter.py` ShopHarnessAdapter 五能力 login/list_pending_refunds/execute_refund(financial)/request_human_approval/process_refund，按上游决策路由，结构化错误 + 权限门）。`graph/loader.py` 依赖注入 decision_client/registry/emit/trigger_payload，webhook 载荷经 run inputs 进入 trigger context.payload（同名键覆盖全局变量），node_start/node_end/run_end 事件回调。`api/main.py` 新增 /run/stream SSE、/nl/generate、/adapters、Demo 店铺登录/订单接口与 /demo/shop 控制台页面，共享 Demo 服务单例；dsl 顺带修复 triggerType schedule/cron 取值对齐前端。前端：退款单选择器、SSE 流式运行（节点脉冲/完成描边 + 调试台事件日志）、NL 生成弹窗载草稿、退款三节点种子图。测试：后端 58 单元（新增决策 6/店铺 8/NL 2/端到端 3/API 6/schedule 1）+ 店铺平台集成 1（opt-in），前端 vitest 23；浏览器实测 12345 破损→approve_refund→refunded、12346 主观→request_human_approval→human_review、NL 草稿 3 节点、控制台 demo/demo 登录拉单。文档 08/09/12/13 已同步。
2. **feat(graph/api): W7-W8 编译与运行（2026-09-13）**——`graph/dsl.py` 承接前端 Graph JSON（version 1）：pydantic GraphDSL/NodeDSL/EdgeDSL + 静态校验（版本、节点/连线 id 唯一、边端点存在、禁自环、三类节点分类型必填配置、未支持类型拒绝），错误一次性聚合为中文列表；`graph/loader.py` 将 DSL 编译为 LangGraph StateGraph（DSL 节点 1:1 成图节点，边原样装配，无前驱接 START、无后继接 END），`run_graph` 播种全局变量并在运行时做 04 §6.3 `{{路径}}` 插值（可读上游节点产出，缺失引用原样保留），三类节点执行器为确定性占位（LLM 随 LiteLLM、真实工具随适配器联动替换）。`api/main.py` FastAPI 落地：`GET /api/health`、`POST /api/graphs`、`GET /api/graphs/{id}`、`POST /api/graphs/{id}/compile`（返回拓扑/入口/终点）、`POST /api/graphs/{id}/run`（返回状态/产出/traces），进程内图存储（与 T4 同假设，持久化随 11 S1），校验失败统一 422。前端新增 `lib/apiClient.ts` 与编辑器"编译并运行"按钮（结果弹窗 + 422 中文错误 Alert）。测试新增 16 个后端用例（dsl 7/loader 5/API 4），默认 `pytest` 32 passed/7 skipped；浏览器对真实后端验证保存→编译→运行（`{{global.approval_limit}}`→500）与 422 错误路径。边界决策（graph 独立包、进程内存储、/run 端点）记 09 待定项 6，03 新增 graph_definition 契约索引，12 REST 表补 /run。
3. **feat(web): W5-W6 编辑器核心功能（2026-09-13）**——`lib/variables.ts`（04 §6.3 `{{路径}}` 语法：extractRefs/interpolate/resolvePath、标识符校验、全局变量+节点输出路径清单）、`lib/nodeCatalog.ts`（trigger/ai_decision/tool_call 三类目录、默认 config/retry、中文实时校验）、`lib/graphSerializer.ts`（03/04 §3.2 node_schema 形状的 Graph JSON，version 1，位置取整）；`store/editorStore.ts` 重写（addNodeAt 用 `nextId` 扫已有 id 防 React Flow id 碰撞、选中/配置/删除级联边/变量 CRUD/连线日志，种子审批示例 3 节点 + 1 变量）；`AtlasNode` 自定义节点（类型配色 + 错误角标）、`FlowCanvas` HTML5 拖拽落点（screenToFlowPosition）、`NodePanel` 拖拽面板、`VariablesPanel` 变量增删、`PropertyPanel` 分类型配置表单（含插入变量引用、retry 策略、实时错误清单）、Editor 加"导出 Graph JSON"预览弹窗。测试：vitest 5 新增（ADR 记 10 §4），4 个测试文件 20 用例全绿（变量/校验/序列化/store 碰撞与级联）；`pnpm build` 通过；浏览器人工验证拖入节点、校验角标联动、变量插入、导出 JSON（version 1、4 节点、2 变量、2 边），应用零控制台错误。修复：根 `.gitignore` 的 Python 规则 `lib/` 误伤 `frontend/src/lib/`，改为 `/lib/` 锚定根目录。4. **feat(web/harness): W3-W4 Harness Web 适配器（2026-09-13）**——`harness/base.py` 落地 06 §6.4 同构契约（HarnessAdapter ABC、ActionRequest/ActionResult/Capability/Observation、Permission 枚举 read/write/delete/financial），execute 模板方法做权限校验+审计（I8）；`harness/registry.py` 进程内注册/发现/心跳健康（04 §4.4）；`web/location.py` 三层定位（层1 选择器 3000ms 超时 + 选择器缓存、层2 视觉语义置信度严格 >0.7、层3 全图推理坐标、全失败兜底文案），层2/3 视觉组件以协议注入（模型接入前为桩）；`web/adapter.py` Playwright sync 适配器，工具集 navigate/click/type/screenshot，headless 受 `PLAYWRIGHT_HEADLESS` 控制。测试 17 个：契约/定位单元测试 13 个全绿，真实 Chromium 集成 4 个（I1/I2/I5 + type/observe）全绿；默认 `pytest` 16 passed/7 skipped，`ATLAS_RUN_INTEGRATION=1` 全量 23 passed。包边界决策（harness=契约/注册，web=Playwright 实现，不合并）已记 09 待定项 2 + 08 §7.2。5. **feat(memory): PostgreSQL + pgvector 连接层验证（2026-09-13）**——本地 Docker 跑 `pgvector/pgvector:pg16`（容器 `atlas-pg`，5432）；迁移 `db/migrations/001_enable_pgvector.sql` 启用 vector 0.8.6；`memory/settings.py`（环境变量单一读取点，`DATABASE_URL` 缺失 fail-closed）、`memory/database.py`（SQLAlchemy 引擎/会话工厂，强制 `postgresql+psycopg` 即 psycopg 3 驱动，`ping`/`pgvector_version` 探针）；`tests/test_database_integration.py` 3 个 integration 标记用例（ping、扩展版本、vector 类型写入+余弦距离排序），默认跳过，`ATLAS_RUN_INTEGRATION=1` + `DATABASE_URL` 开启，对容器全绿。`.env` 已本地创建（gitignored），`.env.example` 连接串同步为 psycopg 写法。6. **feat(web): 前端编辑器框架跑通（2026-09-13）**——`frontend/` 采用 Vite 8 + React 19 + TS 6 + `@xyflow/react` 12（替代弃用包 `react-flow-renderer`）+ Zustand 5 + AntD 6；页面含 Dashboard/Editor，组件含 FlowCanvas/NodePanel/PropertyPanel/DebugConsole，Zustand 管理 nodes/edges/选中节点/日志；`pnpm build` 通过，浏览器验证初始 3 节点、添加节点、属性编辑与日志，控制台零错误。dev 端口 5174，`/api` 代理 8000。7. **feat(engine): LangGraph 最小 OODA 循环跑通（2026-09-13）**——`engine/state.py`（LoopState 按 06 §6.1/12 §1.1 契约，messages/observations 加 add 归约器）、`engine/nodes.py`（observe/orient/decide/act/reflect 五节点 + 三条 conditional 路由，确定性占位、签名不偏离 12 §1.2）、`engine/loop.py`（StateGraph 装配，拓扑同 12 §1.3；`build_graph/initial_state/run_loop`）；`tests/test_engine_loop.py` 3 用例全绿；venv（Python 3.11.15）依赖安装与端到端 `run_loop` 验证通过。8. **chore: W1 工程骨架落地（2026-09-13）**——按 09 文档建 `src/atlas/{engine,harness,graph,nodes,memory,skills,collaboration,api,web}/` + `tests/` 包树（`__init__.py` 占位）；`pyproject.toml` 锁定 10 文档 §2 Demo 依赖（Python >=3.11，src layout，版本下限取当日 PyPI 稳定版），已用 Python 3.11 venv 验证可编辑安装；09 文档同步记录 `api/` 包位决策（FastAPI 入口，待定项 5）。9. **docs: 技术选型 T1-T5 收口（2026-09-13）**——10 文档 §3 待决策项全部转为正式 ADR（LiteLLM+商业 API / 评估层仅留接口 / 前端进骨架 / 进程内事件总线替代 NATS / FastAPI 实现网关接口），§4 补变更记录；02 选型总览、09 待定项与目录树（含 `frontend/`）、08 §7.2 决策注记三处同步；NATS/Go 网关缓做条目已在 14 文档 D5/D6。10. **docs: 移除 LICENSE（2026-09-13，用户指示）**——MIT 许可证不需要，LICENSE 文件删除，README/CONTRIBUTING 的 License 段落改为"暂无（未定）"，handoff 元文档索引与 CHANGELOG 同步清理。11. **docs: 补齐 agent-world 惯例规范文档（2026-09-13）**——根目录：`CONTRIBUTING.md`（贡献指南，Python/09 骨架适配）、`MIGRATION_CONVENTION.md`（DB 迁移规范，PostgreSQL 版）、`BENCHMARK.md`（性能基准记录表）、`.gitleaks.toml`（密钥扫描）、`.env.example`（环境变量模板，按 10 文档 Demo 栈）；docs 体系：`14-缓做事项登记表`、`15-环境与分支策略`、`16-反馈工作流`。索引已同步 00 文档地图与 README。12. **chore/docs: 仓库初始化并推送 GitHub private（2026-09-13）**——`git init -b main`；3 个原子提交：`6fc7728`（chore .gitignore）、`d3c63c6`（docs 规格文档 00-13，commit 原文为 atlas-docs）、`2413ab5`（docs 根元文档）；`gh repo create atlas --private` 创建并推送至 `https://github.com/bayernjf/atlas`（PRIVATE，默认分支 main）。13. **docs: 源文档切分完成并核对（2026-09-13）**——两份原始文档（《产品与总体方案.md》《技术实现设计.md》）按主题切分为 `docs/` 00-13 共 14 份 AI 导向文档；274 个章节标题全部映射、逐段覆盖率机器核对通过；源文档已删除（血统与治理见 00 文档）。14. **docs: 切分核对报告（2026-09-12）**——段落级最长公共子串覆盖率验证，无信息丢失。
## Quality gate（质量门）

- **文档侧**：00 文档"切分核对报告"已确认 274 章节全映射、逐段覆盖无丢失。改规格文档必须遵循 00 文档治理规则（唯一事实源 / 改内容流程 / Schema 契约防漂移）。
- **代码侧**：最小质量门已建立——后端 `.venv/bin/pytest`（当前 58 个单元用例：engine 3 + harness 契约 7 + 三层定位 6 + graph DSL 8 + 编译器 5 + graphs API 4 + 决策 6 + 店铺 8 + NL 2 + 退款端到端 3 + Demo API 6；DB/浏览器/店铺 8 个 integration 用例默认跳过），全量 `ATLAS_RUN_INTEGRATION=1`（+`DATABASE_URL`）含真实 Chromium 与店铺平台集成；前端 `cd frontend && pnpm test`（vitest，23 个单元用例）与 `pnpm build`（TS 检查 + Vite 构建）均通过（有 AntD 首包 >500kB 的体积提示，暂不阻塞）。后续按 13 测试用例清单分层：单元 → 集成 → 回放 → 端到端 → AI 评估，上线前过"四道门"。

## How to run

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                       # 单元测试（integration 标记默认跳过）
.venv/bin/python -c "from atlas.engine.loop import run_loop; print(run_loop('demo', max_steps=2)['status'])"

# FastAPI graphs 接口（W7-W8；前端 dev 经 /api 代理到此端口）
.venv/bin/uvicorn atlas.api.main:app --reload --port 8000

# PostgreSQL + pgvector（本地开发）
docker run -d --name atlas-pg -e POSTGRES_USER=atlas -e POSTGRES_PASSWORD=atlas \
  -e POSTGRES_DB=atlas -p 5432:5432 pgvector/pgvector:pg16
cp .env.example .env                   # 填 DATABASE_URL（本地开发库：atlas/atlas）
docker exec -i atlas-pg psql -U atlas -d atlas < db/migrations/001_enable_pgvector.sql
DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \
  ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration

# Playwright 浏览器（W3-W4 起，浏览器集成测试需要）
.venv/bin/playwright install chromium
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration   # 同时含 DB + Chromium 用例

# 前端
cd frontend
pnpm install
pnpm dev                               # http://localhost:5174
pnpm test                              # vitest 单元测试
pnpm build                             # 类型检查 + 生产构建
```

Demo 里程碑（08 文档 7.1）：
- ✅ W1-W2：LangGraph 最小循环、前端编辑器框架、PostgreSQL+pgvector 连接均已跑通（2026-09-13）。
- ✅ W3-W4：Harness 契约层（注册发现/权限审计）+ Web 适配器（三层定位、navigate/click/type/screenshot，真实 Chromium 验证）已跑通（2026-09-13）；视觉语义层（omni-parser 类解析 + vision LLM）为协议注入桩，待 T1 LiteLLM 接入后替换。
- ✅ W5-W6：编辑器核心（拖拽画布、trigger/ai_decision/tool_call 三类节点类型化配置 + 实时校验、全局变量与 `{{路径}}` 引用、Graph JSON 导出，vitest 20 用例）已跑通（2026-09-13）。
- ✅ W7-W8：Graph DSL 契约 + 静态校验、DSL→LangGraph 编译/运行（`graph/` 包）、FastAPI graphs 保存/读取/编译/运行、编辑器一键编译运行（后端 16 新用例 + 浏览器验证）已跑通（2026-09-13）；节点执行器为确定性占位，待 LiteLLM/业务工具替换。
- ✅ W9-W10：电商退款端到端 Demo（LiteLLM 决策 + 规则兜底、shop 退款业务能力、SSE 实时进度、NL 退款草稿、模拟商家控制台，08 §7.3 七条验收全部达成；后端 58 单元 + 前端 23 用例 + 浏览器实测，2026-09-13）。
- API 已可启动：`uvicorn atlas.api.main:app --reload --port 8000`（graphs/SSE/NL/adapters/Demo 店铺接口；前端 dev 端口 5174，`/api` 代理到 8000；模拟控制台 http://localhost:8000/demo/shop）。

## Known issues / 注意点

- **文档口径**：01-08 为规格唯一事实源；10-13 为工程化推导文档，与正文冲突时以 01-08 为准（00 文档已声明）。
- **技术选型已锁定（2026-09-13 T1-T5 收口）**：`pyproject.toml` 可按 10 文档 §2 清单落码（版本号落码时解析）；Demo 不引入 NATS/vLLM/Go 网关/评估层实现，缓做项见 14 文档触发条件。
- **改 Schema 必须同步所有引用位置**（03 契约索引 + 所在文档正文），防漂移（00 治理规则第 3 条）。
- **新决策必须回写**：08 任务迭代计划记录 + 10 ADR 记录 + 本文档"Active work"同步，三者一致。

## Conventions（约定）

- **commit 消息**：英文、`<type>(<scope>): <subject>` 格式；原子提交；不 push（除非用户明确说）；作者保持用户身份，不加 AI co-author。详见 [git-commit-message.md](git-commit-message.md)。
- **AI 行为规范**：见 [AGENTS.md](AGENTS.md)（文档分层 / 治理规则 / 技术约束，新会话必读）。
- **新增文档后**：在本文档"Project documents"区补一行索引；改规格后同步 00 文档治理规则与 08 计划。
