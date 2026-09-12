# Handoff — Atlas

更新时间：2026-09-13

## 项目概况

Atlas 是 AI 运营体（Agent）编排平台：以 **Harness（能力接入）/ Graph（可执行因果图）/ Loop（OODA 主循环）** 三位一体为内核，面向企业流程自动化场景（首个目标场景：企业内部 OA 审批），目标是让非技术人员通过自然语言/拖拽定义流程，由运营体自动执行、自愈与进化。

当前阶段：**设计文档已完成，代码未启动**。全部规格以 `atlas-docs/` 下 00-13 编号文档为唯一事实源（由两份原始方案文档切分而成，已机器核对无信息丢失）。

结构：
- `atlas-docs/` — 全部规格文档（入口：`00-文档索引与治理.md`）
- `README.md` / `AGENTS.md` / `CLAUDE.md` / `git-commit-message.md` / `CHANGELOG.md` — 根目录元文档
- 工程代码目录尚未创建（规划见 `atlas-docs/09-工程骨架与目录结构.md`）

## Project documents（文档地图）

**文档地图（按场景怎么读 + 治理规则）**：[atlas-docs/00-文档索引与治理.md](atlas-docs/00-文档索引与治理.md) 是单一事实源与 AI 工作入口，本区只做直达索引：

* [atlas-docs/01-PRD-产品需求规格.md](atlas-docs/01-PRD-产品需求规格.md) — 产品概念（Harness/Graph/Loop 定义、全场景图谱、DIY 机制）+ 产品定义 + Demo 范围
* [atlas-docs/02-技术架构设计.md](atlas-docs/02-技术架构设计.md) — 三位一体架构、系统分层、技术选型、组件全景
* [atlas-docs/03-数据模型与Schema-契约索引.md](atlas-docs/03-数据模型与Schema-契约索引.md) — 全部 Schema 位置速查与字段概览
* [atlas-docs/04-组件设计-编辑后台.md](atlas-docs/04-组件设计-编辑后台.md) — 编辑后台组件（节点/工具/适配器/逻辑/变量/上下文）
* [atlas-docs/05-组件设计-运营体五项核心.md](atlas-docs/05-组件设计-运营体五项核心.md) — Skill / Memory / 智能体协同 / 部署方式 / 自定义前端模板
* [atlas-docs/06-运行时与质量保障.md](atlas-docs/06-运行时与质量保障.md) — Loop 主循环、决策节点、Harness Gateway、Web 适配器、测试层次、安全清单
* [atlas-docs/07-递归自动化引擎设计.md](atlas-docs/07-递归自动化引擎设计.md) — 平台探索器 / 行为挖掘器 / 诊断修复器 / 元运营体 / 自我进化
* [atlas-docs/08-任务迭代计划.md](atlas-docs/08-任务迭代计划.md) — 开发路线图、Demo 计划 W1-W10、Phase 1-4 迭代路线、商业化路径、验收标准
* [atlas-docs/09-工程骨架与目录结构.md](atlas-docs/09-工程骨架与目录结构.md) — 工程目录树、模块→文档映射、待填项清单 ★落码必读
* [atlas-docs/10-技术选型决策记录.md](atlas-docs/10-技术选型决策记录.md) — 选型 ADR（✅ 已定 / ⏳ 待决策）、Demo 依赖清单 ★落码必读
* [atlas-docs/11-存储与数据持久化设计.md](atlas-docs/11-存储与数据持久化设计.md) — 存储分层、数据对象→表映射、读写路径
* [atlas-docs/12-API与模块接口清单.md](atlas-docs/12-API与模块接口清单.md) — 引擎内部接口、Harness Gateway、后端 REST、协同/评估接口
* [atlas-docs/13-测试用例清单.md](atlas-docs/13-测试用例清单.md) — 单元/集成/回放/端到端/AI 评估用例 + 上线前四道门

**尚未创建（代码阶段再补，遵循 agent-world 惯例）**：`BENCHMARK.md`（性能基准）、`MIGRATION_CONVENTION.md`（DB 迁移约定）、`CONTRIBUTING.md`（贡献指南）。

## Current state（当前状态）

- **阶段**：设计文档完成（2026-09-13 两份源文档切分核对后删除，内容已并入 `atlas-docs/` 00-13）；**代码未启动**。
- **仓库**：**非 git 仓库**（根目录仅 `atlas-docs/`）。建议下一步 `git init` + 首提交。
- **技术选型**：✅ 已定（Python 3.11+ 引擎 / Go Harness 网关（Demo 可先用 FastAPI 接口）/ TS+React 18 前端 / LangChain+LangGraph / LiteLLM / Playwright / PostgreSQL+pgvector / Redis / NATS（Demo 可缓）/ FastAPI / React Flow+Zustand）；⏳ 待决策 5 项（T1-T5，见 10 文档 §3）。
- **Demo 依赖清单已就绪**：10 文档 §2 可直接进 `pyproject.toml`（版本号落码时解析）。
- **关键文件**：目前只有文档。工程骨架规划见 09 文档（`src/atlas/{engine,harness,graph,nodes,memory,skills,collaboration,web}/` + `tests/`）。

## Active work / 待办

按优先级降序：

1. ★ **git init + 首提交**（本批元文档：handoff / README / AGENTS / CLAUDE / git-commit-message / CHANGELOG + atlas-docs 全量）。
2. ★ **技术选型待决策项收口**（10 文档 §3 T1-T5）：模型部署方式（Demo 建议 LiteLLM 对接任一主流 API）、评估层（留接口）、编辑后台前端是否进 Demo 骨架（**建议进**，08 验收标准第 5 条含可视化画布）、NATS 缓做、Go 网关缓做。收口后在 10 文档记录变更。
3. ★ **W1-W2 基础骨架落地**（08 文档 7.1）：LangGraph 引擎跑通、前端编辑器框架、PostgreSQL 连接。按 09 文档待填项清单建目录与 `pyproject.toml` / `.gitignore`。
4. 文档缺口登记：`09-工程骨架` 待定项 2/3/4（harness 与 web 边界、是否新增 deployment/、前端工程结构）在落码时一并决策。

> 缓做/低优项：BENCHMARK / MIGRATION_CONVENTION / CONTRIBUTING 文档、移动端适配器（Appium）、策略训练（GRPO/DPO）、组件市场、商业化计费——均在 08 文档 Phase 3/4 触发。

## Recently shipped（最近变更）

1. **docs: 源文档切分完成并核对（2026-09-13）**——两份原始文档（《产品与总体方案.md》《技术实现设计.md》）按主题切分为 `atlas-docs/` 00-13 共 14 份 AI 导向文档；274 个章节标题全部映射、逐段覆盖率机器核对通过；源文档已删除（血统与治理见 00 文档）。
2. **docs: 切分核对报告（2026-09-12）**——段落级最长公共子串覆盖率验证，无信息丢失。

## Quality gate（质量门）

- **文档侧**：00 文档"切分核对报告"已确认 274 章节全映射、逐段覆盖无丢失。改规格文档必须遵循 00 文档治理规则（唯一事实源 / 改内容流程 / Schema 契约防漂移）。
- **代码侧**：暂无（代码未启动）。落码后按 13 测试用例清单建立测试门：单元 → 集成 → 回放 → 端到端 → AI 评估，上线前过"四道门"。

## How to run

代码未启动，暂无运行方式。Demo 里程碑（08 文档 7.1）：
- W1-W2 跑通 LangGraph 引擎与前端框架后，此处补启动命令。
- 规划端口/目录见 09 文档与 10 文档 §2。

## Known issues / 注意点

- **文档口径**：01-08 为规格唯一事实源；10-13 为工程化推导文档，与正文冲突时以 01-08 为准（00 文档已声明）。
- **技术选型锁定前不要写 `pyproject.toml`**：10 文档 T1-T5 未收口，依赖清单以 10 文档 §2 为准，版本号落码时解析。
- **改 Schema 必须同步所有引用位置**（03 契约索引 + 所在文档正文），防漂移（00 治理规则第 3 条）。
- **新决策必须回写**：08 任务迭代计划记录 + 10 ADR 记录 + 本文档"Active work"同步，三者一致。

## Conventions（约定）

- **commit 消息**：英文、`<type>(<scope>): <subject>` 格式；原子提交；不 push（除非用户明确说）；作者保持用户身份，不加 AI co-author。详见 [git-commit-message.md](git-commit-message.md)。
- **AI 行为规范**：见 [AGENTS.md](AGENTS.md)（文档分层 / 治理规则 / 技术约束，新会话必读）。
- **新增文档后**：在本文档"Project documents"区补一行索引；改规格后同步 00 文档治理规则与 08 计划。
