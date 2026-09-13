# Agent Instructions

Atlas 项目的 AI 协作规范。**接手先读 [handoff.md](handoff.md)（状态 + 待办 + 文档索引），写任何代码前读本文件**——尤其是「文档治理」与「技术约束」两条，这是本项目最常被违反的约定。

## 任务追踪与文档分层

**任务追踪与待办的主入口是 `handoff.md`**（状态、进度、待办、文档索引都在这里）；**方案/设计/规格的全文放 `docs/` 的编号文档**，handoff 只索引、不复制全文。

- 活跃待办 → `handoff.md`「Active work」区
- 缓做/低优项 → `docs/14-缓做事项登记表.md`（每条带触发条件；handoff 只给索引）
- 方案/设计/规格 → `docs/00-16`，一个主题一份（单一事实源，入口 `00-文档索引与治理.md`；14-16 为治理类文档）
- 文档索引与治理规则 → `docs/00`（唯一事实源声明 + 改内容流程 + Schema 契约规则）
- 排期与迭代 → `docs/08-任务迭代计划.md`（新决策必须在此记录）
- 环境与分支 → `docs/15-环境与分支策略.md`（main=PROD / dev=集成 / feature/*=开发）

一句话：handoff = 索引 + 状态 + 待办，docs = 方案 + 设计 + 明细。接手先读 handoff，再按索引跳转。

## 文档治理规则（00 文档，必须遵守）

1. **唯一事实源**：`docs/` 下 01-08 为规格文档的唯一事实源；10-13 为工程化推导文档，与正文冲突时以 01-08 为准。
2. **改内容流程**：定位到对应文档 → 修改 → 若涉及新决策，在 08 任务迭代计划中记录 → 同步更新 03 契约索引。
3. **Schema 契约**：数据结构以 03 索引定位、以所在文档正文为唯一权威；改 Schema 必须同步所有引用该结构的位置（防漂移）。
4. **新增文档后**：在 `handoff.md`「Project documents」区补一行索引，并同步 00 文档的文档地图表。

## 技术约束

### 选型变更必须先记录

- 后端依赖以 `pyproject.toml`、前端依赖以 `frontend/package.json` 为准；来源与边界见 [docs/10-技术选型决策记录.md](docs/10-技术选型决策记录.md) §2。
- T1-T5 已于 2026-09-13 收口（10 文档 §3）：LiteLLM 接商业 API、评估层仅留接口、前端进 Demo、进程内事件总线、Demo 网关用 FastAPI。后续新增/更换选型必须先记录到 10 文档 §4，不擅自拍板。
- 决策变更必须三处同步：10 文档 ADR 记录 → 02 技术架构选型总览 → 09 工程骨架待填清单。

### 工程结构按 09 骨架

- 代码目录按 [docs/09-工程骨架与目录结构.md](docs/09-工程骨架与目录结构.md) 组织：后端 `src/atlas/{engine,harness,graph,nodes,memory,skills,collaboration,api,web}/` + `tests/`，前端 `frontend/`。
- 模块职责以 09 文档"模块 → 文档映射"表为准；包边界有歧义时（如 harness 与 web 是否合并）先记录决策，不静默二选一。

### 数据访问与接口

- 数据结构与接口签名以 03/12 文档为契约基准；接口实现不得偏离文档签名，确需调整先走"改内容流程"。

## Commit 规范

- 英文 `<type>(<scope>): <subject>`，如 `feat(engine): add OODA loop skeleton`
- 原子提交：一次只做一件事（docs / chore / feat / fix / test / refactor 分开）
- 不 push（除非用户明确说）
- 作者保持用户身份，不加 AI co-author

git commit message 详细规范见 [git-commit-message.md](git-commit-message.md)。

## 验证命令

W1 起已有最小验证命令；后续按 [docs/13-测试用例清单.md](docs/13-测试用例清单.md) 扩展：

- 后端单元测试：`.venv/bin/pytest`
- 前端类型检查与构建：`cd frontend && pnpm build`
- 前端本地预览：`cd frontend && pnpm dev`（http://localhost:5174）
- 集成/回放/端到端：按 13 文档分层
- 上线前四道门：见 13 文档（Demo 阶段不视为可上线）
