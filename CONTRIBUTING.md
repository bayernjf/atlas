# Contributing

Thanks for helping build Atlas. This is an AI 运营体（Agent）编排平台 —— Python 引擎 + Go Harness 网关（Demo 阶段暂用 FastAPI 接口）+ React 前端。**题述工程化路线（docs/19/20，M1–M10）推进中，M1–M7 已落码**（题一 Schema 内核 M1–M4、题二 M5 持久化+中断恢复 / M6 版本化 / M7 任务总线）；W1-W10 Demo（2026-09-13）为基线。当前状态与待办见 [handoff.md](handoff.md)，缓做事项见 [docs/14-缓做事项登记表.md](docs/14-缓做事项登记表.md)，排期见 [docs/08](docs/08-任务迭代计划.md)。

## Prerequisites

- **Python >= 3.11**（引擎与 Demo 后端，见 [docs/10-技术选型决策记录.md](docs/10-技术选型决策记录.md)）
- **PostgreSQL + pgvector**（主库，见 10 文档 §1）
- **Redis**（短期工作记忆缓存）
- **Node >= 18 + pnpm**（前端 React 18，Demo 含可视化画布时）
- **Playwright**（Web 操作适配器）

> 具体依赖清单以 `pyproject.toml`（后端）和 `frontend/package.json`（前端）为准；新增选型必须先按 10 文档 §4 记录 ADR（见 [AGENTS.md](AGENTS.md) 技术约束）。

## Develop

代码骨架按 [docs/09-工程骨架与目录结构.md](docs/09-工程骨架与目录结构.md)：

```bash
# 后端（引擎 + Demo API）
pip install -e ".[dev]"          # 依赖清单见 pyproject.toml（落码时生成）
pytest                            # 单元测试（层级见 13 测试用例清单）
uvicorn atlas.api.main:app --reload --port 8000

# 前端（Demo 含画布）
cd frontend && pnpm install && pnpm dev   # http://localhost:5174，/api 代理到 8000
pnpm build                                # TypeScript 检查 + Vite 生产构建
```

> 具体命令以 09 文档与 10 文档锁定后的 `pyproject.toml` / 前端工程为准，本文件落码后同步更新。

## Project layout

- `src/atlas/engine` — Loop 主循环（感知→规划→执行→反馈）、决策节点
- `src/atlas/harness` — Harness Gateway：平台抽象与能力集成总线
- `src/atlas/graph` — Graph 定义与执行（LangGraph 层）
- `src/atlas/nodes` — 节点/工具/逻辑/变量组件
- `src/atlas/memory` — 记忆系统（Redis + PostgreSQL + pgvector）
- `src/atlas/skills` — 技能（Skill）
- `src/atlas/collaboration` — 智能体协同
- `src/atlas/web` — Web 操作适配器（Playwright）
- `tests/` — 单元/集成/端到端测试
- `frontend/src/components/` — 画布、节点面板、属性面板、调试控制台
- `frontend/src/pages/` — Dashboard / Editor
- `frontend/src/store/` — Zustand 编辑器状态

模块职责与接口契约见 09 文档「模块 → 文档映射」表、03 Schema 契约索引、12 API 清单。

## Commit messages

- 英文、祈使句：`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`
- **原子提交**——一次 commit 只做一件事
- 相关时引用文档/阶段（如 `feat(engine): implement OODA loop skeleton (W1)`）
- 完整规范见 [git-commit-message.md](git-commit-message.md)

## Coding conventions

- **文档是契约**：数据结构与接口签名以 `docs/03`（Schema）与 `12`（API）为基准，实现不得偏离；确需调整先走「改内容流程」（见 [docs/00-文档索引与治理.md](docs/00-文档索引与治理.md)）。
- **选型已锁定**：后端依赖清单见 `pyproject.toml`；前端 React 19 + `@xyflow/react` 12（React Flow 现行包名）+ Zustand 5 + AntD 6，见 `frontend/package.json` 与 10 文档 §2/§4。
- **DB 访问走统一抽象**：数据访问/ORM 层集中管理，禁止在节点/路由里散写裸 SQL（迁移规范见 [MIGRATION_CONVENTION.md](MIGRATION_CONVENTION.md)）。
- **记忆分层**：短期（Redis）/ 长期（PostgreSQL + pgvector）按 06 文档 6.3 分层实现，不混用。
- **安全基线**：密钥走环境变量/Vault（见 `.env.example`），代码不落明文密钥。

## Tests

- 单元测试 `tests/test_*.py`，pytest 运行；变更引擎行为须补/改对应用例（层级见 13 文档）。
- Demo 验收对照 [docs/08-任务迭代计划.md](docs/08-任务迭代计划.md) 7.3 七条验收标准。
- 上线前过「四道门」：单元 → 集成 → 回放 → 端到端 + AI 评估（见 13 文档）。

## Branch & PR

- 分支策略：`feature/*`（开发）→ `dev`（集成/准生产）→ `main`（生产稳定版），详见 [docs/15-环境与分支策略.md](docs/15-环境与分支策略.md)。
- 推送分支并开 PR 到 `dev`（或相关 feature 分支）；PR 描述写明改了什么、为什么、关联文档。
- CI 质量门（`.github/workflows/ci.yml`，push main/dev 与 PR 触发）：gitleaks 密钥扫描、后端 pytest、前端 oxlint + vitest + build 全绿才可合并；integration 用例不在 CI 内（opt-in，见 [handoff.md](handoff.md)「How to run」）。

## License

暂无（许可证未定，LICENSE 文件已移除）。
