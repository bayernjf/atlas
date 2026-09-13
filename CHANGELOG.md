# Changelog

本文件记录 Atlas 仓库的可追溯变更里程碑。详细过程与状态见 [handoff.md](handoff.md)。

## [Unreleased]

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
