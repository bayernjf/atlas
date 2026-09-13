# Handoff — Atlas

更新时间：2026-09-13

## 项目概况

Atlas 是 AI 运营体（Agent）编排平台：以 **Harness（能力接入）/ Graph（可执行因果图）/ Loop（OODA 主循环）** 三位一体为内核，面向企业流程自动化场景（首个目标场景：企业内部 OA 审批），目标是让非技术人员通过自然语言/拖拽定义流程，由运营体自动执行、自愈与进化。

当前阶段：**W1-W2 基础骨架启动中**。设计文档已完成（`docs/` 00-16 为唯一事实源）；工程骨架已落地（`src/atlas/` 包树 + `pyproject.toml`，2026-09-13），业务代码待填。

结构：
- `docs/` — 全部规格文档（入口：`00-文档索引与治理.md`）
- `README.md` / `AGENTS.md` / `CLAUDE.md` / `git-commit-message.md` / `CHANGELOG.md` — 根目录元文档
- `src/atlas/` + `frontend/` — W1 工程骨架（后端最小 OODA 循环、前端编辑器框架已跑通；规划见 `docs/09-工程骨架与目录结构.md`）

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

根目录元文档：[CHANGELOG.md](CHANGELOG.md)（变更日志）/ [CONTRIBUTING.md](CONTRIBUTING.md)（贡献指南）/ [AGENTS.md](AGENTS.md)（AI 行为规范，**新会话必读**）/ [git-commit-message.md](git-commit-message.md)（commit 详细规范）/ [MIGRATION_CONVENTION.md](MIGRATION_CONVENTION.md)（DB 迁移规范）/ [BENCHMARK.md](BENCHMARK.md)（性能基准记录表）/ [.gitleaks.toml](.gitleaks.toml)（密钥扫描配置）/ [.env.example](.env.example)（环境变量模板）

**代码阶段待补（遵循 agent-world 惯例）**：`.dockerignore` / `Dockerfile` / `.pre-commit-config.yaml` / CI 工作流（`.github/workflows`）。`pyproject.toml` 与前端工程已在 W1 创建。

## Current state（当前状态）

- **阶段**：W1-W2 基础骨架启动中。设计文档完成（2026-09-13 源文档切分）；2026-09-13 已建工程骨架：`src/atlas/{engine,harness,graph,nodes,memory,skills,collaboration,api,web}/` + `tests/`、`pyproject.toml`（依赖按 10 §2，版本下限取 2026-09-13 PyPI 稳定版，已验证可编辑安装）；后端最小 OODA 循环与 `frontend/` Vite React TS 编辑器框架均已跑通。
- **仓库**：git 仓库（`main` 分支），**已推送 GitHub private 仓库 `bayernjf/atlas`**（2026-09-13，3 个原子提交：chore .gitignore + docs 规格 + docs 根元文档）。
- **技术选型**：✅ 已全部收口（2026-09-13，T1-T5 见 10 文档 §3）：Python 3.11+ 引擎 / Go Harness 网关为产品化目标、**Demo 用 Python/FastAPI 实现同构接口** / TypeScript + React 19（满足 React 18+）/ LangChain+LangGraph / LiteLLM（Demo 对接主流商业 API，`.env` 切换，不引入 vLLM）/ Playwright / PostgreSQL+pgvector / Redis / **Demo 进程内事件总线替代 NATS** / FastAPI / `@xyflow/react` 12 + Zustand 5 + AntD 6 / 评估优化层仅留接口；剩余 ⏳（vLLM、策略训练、多语言）均为后续阶段范围。
- **Demo 依赖清单已落码**：后端在 `pyproject.toml`，前端在 `frontend/package.json` / `pnpm-lock.yaml`（版本为 2026-09-13 解析的稳定版）。
- **关键文件**：后端 `src/atlas/`（9 个包；engine 已有 W1 最小循环）、前端 `frontend/`（Dashboard/Editor + 四组件 + Zustand store）；模块填充顺序见 09 文档待填项清单。

## Active work / 待办

按优先级降序：

1. ★ **W1-W2 基础骨架落地**（08 文档 7.1）：✅ 工程骨架与 venv 已就绪（2026-09-13，`.venv` Python 3.11，11 个运行时依赖安装通过）；✅ **LangGraph 最小 OODA 循环已跑通**（`engine/{state,nodes,loop}.py`，确定性占位节点，3 个 pytest 用例全绿，`run_loop` 端到端收敛 completed）；✅ **前端编辑器框架已跑通**（`frontend/` Vite 8 + React 19 + TS + `@xyflow/react` 12 + Zustand 5 + AntD 6；Dashboard/Editor、3 初始节点、节点添加/选中/改名/日志均已浏览器验证，控制台零错误）。剩余：PostgreSQL 连接（+pgvector 扩展验证）。
2. 文档缺口登记：`09-工程骨架` 待定项 2/3（harness 与 web 边界在 W3-W4 决策、是否新增 deployment/）在落码时一并决策；待定项 1/4 已随 T1-T5 收口。

> 缓做/低优项：统一登记在 [docs/14-缓做事项登记表.md](docs/14-缓做事项登记表.md)（每条带触发条件，条件满足移回本区并标注重启日期）。当前含移动端适配器、策略训练、组件市场、模型路由器、NATS、Go 网关、多租户、运营体市场、BENCHMARK 实测、CI/CD、可观测性等 11 项。

## Recently shipped（最近变更）

1. **feat(web): 前端编辑器框架跑通（2026-09-13）**——`frontend/` 采用 Vite 8 + React 19 + TS 6 + `@xyflow/react` 12（替代弃用包 `react-flow-renderer`）+ Zustand 5 + AntD 6；页面含 Dashboard/Editor，组件含 FlowCanvas/NodePanel/PropertyPanel/DebugConsole，Zustand 管理 nodes/edges/选中节点/日志；`pnpm build` 通过，浏览器验证初始 3 节点、添加节点、属性编辑与日志，控制台零错误。dev 端口 5174，`/api` 代理 8000。
2. **feat(engine): LangGraph 最小 OODA 循环跑通（2026-09-13）**——`engine/state.py`（LoopState 按 06 §6.1/12 §1.1 契约，messages/observations 加 add 归约器）、`engine/nodes.py`（observe/orient/decide/act/reflect 五节点 + 三条 conditional 路由，确定性占位、签名不偏离 12 §1.2）、`engine/loop.py`（StateGraph 装配，拓扑同 12 §1.3；`build_graph/initial_state/run_loop`）；`tests/test_engine_loop.py` 3 用例全绿；venv（Python 3.11.15）依赖安装与端到端 `run_loop` 验证通过。
3. **chore: W1 工程骨架落地（2026-09-13）**——按 09 文档建 `src/atlas/{engine,harness,graph,nodes,memory,skills,collaboration,api,web}/` + `tests/` 包树（`__init__.py` 占位）；`pyproject.toml` 锁定 10 文档 §2 Demo 依赖（Python >=3.11，src layout，版本下限取当日 PyPI 稳定版），已用 Python 3.11 venv 验证可编辑安装；09 文档同步记录 `api/` 包位决策（FastAPI 入口，待定项 5）。
4. **docs: 技术选型 T1-T5 收口（2026-09-13）**——10 文档 §3 待决策项全部转为正式 ADR（LiteLLM+商业 API / 评估层仅留接口 / 前端进骨架 / 进程内事件总线替代 NATS / FastAPI 实现网关接口），§4 补变更记录；02 选型总览、09 待定项与目录树（含 `frontend/`）、08 §7.2 决策注记三处同步；NATS/Go 网关缓做条目已在 14 文档 D5/D6。
5. **docs: 移除 LICENSE（2026-09-13，用户指示）**——MIT 许可证不需要，LICENSE 文件删除，README/CONTRIBUTING 的 License 段落改为"暂无（未定）"，handoff 元文档索引与 CHANGELOG 同步清理。
6. **docs: 补齐 agent-world 惯例规范文档（2026-09-13）**——根目录：`CONTRIBUTING.md`（贡献指南，Python/09 骨架适配）、`MIGRATION_CONVENTION.md`（DB 迁移规范，PostgreSQL 版）、`BENCHMARK.md`（性能基准记录表）、`.gitleaks.toml`（密钥扫描）、`.env.example`（环境变量模板，按 10 文档 Demo 栈）；docs 体系：`14-缓做事项登记表`、`15-环境与分支策略`、`16-反馈工作流`。索引已同步 00 文档地图与 README。
7. **chore/docs: 仓库初始化并推送 GitHub private（2026-09-13）**——`git init -b main`；3 个原子提交：`6fc7728`（chore .gitignore）、`d3c63c6`（docs 规格文档 00-13，commit 原文为 atlas-docs）、`2413ab5`（docs 根元文档）；`gh repo create atlas --private` 创建并推送至 `https://github.com/bayernjf/atlas`（PRIVATE，默认分支 main）。
8. **docs: 源文档切分完成并核对（2026-09-13）**——两份原始文档（《产品与总体方案.md》《技术实现设计.md》）按主题切分为 `docs/` 00-13 共 14 份 AI 导向文档；274 个章节标题全部映射、逐段覆盖率机器核对通过；源文档已删除（血统与治理见 00 文档）。
9. **docs: 切分核对报告（2026-09-12）**——段落级最长公共子串覆盖率验证，无信息丢失。

## Quality gate（质量门）

- **文档侧**：00 文档"切分核对报告"已确认 274 章节全映射、逐段覆盖无丢失。改规格文档必须遵循 00 文档治理规则（唯一事实源 / 改内容流程 / Schema 契约防漂移）。
- **代码侧**：最小质量门已建立——后端 `.venv/bin/pytest`（当前 3 个 engine 冒烟用例），前端 `cd frontend && pnpm build`（TS 检查 + Vite 构建，当前通过；有 AntD 首包 >500kB 的体积提示，暂不阻塞）。后续按 13 测试用例清单分层：单元 → 集成 → 回放 → 端到端 → AI 评估，上线前过"四道门"。

## How to run

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                       # 单元测试
.venv/bin/python -c "from atlas.engine.loop import run_loop; print(run_loop('demo', max_steps=2)['status'])"

# 前端
cd frontend
pnpm install
pnpm dev                               # http://localhost:5174
pnpm build                             # 类型检查 + 生产构建
```

Demo 里程碑（08 文档 7.1）：
- ✅ W1-W2：LangGraph 最小循环与前端编辑器框架均已跑通（2026-09-13）；PostgreSQL 连接待补。
- API 启动命令（后续 W2 实现后可用）：`uvicorn atlas.api.main:app --reload --port 8000`；前端：`cd frontend && pnpm install && pnpm dev`（当前 dev 端口 5174，`/api` 代理到 8000）。

## Known issues / 注意点

- **文档口径**：01-08 为规格唯一事实源；10-13 为工程化推导文档，与正文冲突时以 01-08 为准（00 文档已声明）。
- **技术选型已锁定（2026-09-13 T1-T5 收口）**：`pyproject.toml` 可按 10 文档 §2 清单落码（版本号落码时解析）；Demo 不引入 NATS/vLLM/Go 网关/评估层实现，缓做项见 14 文档触发条件。
- **改 Schema 必须同步所有引用位置**（03 契约索引 + 所在文档正文），防漂移（00 治理规则第 3 条）。
- **新决策必须回写**：08 任务迭代计划记录 + 10 ADR 记录 + 本文档"Active work"同步，三者一致。

## Conventions（约定）

- **commit 消息**：英文、`<type>(<scope>): <subject>` 格式；原子提交；不 push（除非用户明确说）；作者保持用户身份，不加 AI co-author。详见 [git-commit-message.md](git-commit-message.md)。
- **AI 行为规范**：见 [AGENTS.md](AGENTS.md)（文档分层 / 治理规则 / 技术约束，新会话必读）。
- **新增文档后**：在本文档"Project documents"区补一行索引；改规格后同步 00 文档治理规则与 08 计划。
