# Changelog

本文件记录 Atlas 仓库的可追溯变更里程碑。详细过程与状态见 [handoff.md](handoff.md)。

## [Unreleased]

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
