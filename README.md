# Atlas — AI 运营体编排平台

> 让非技术人员用自然语言或拖拽定义一个流程，由 AI 运营体自动执行、自愈与进化。

Atlas 是一个 **AI 运营体（Agent）编排平台**：以 **Harness / Graph / Loop** 三位一体为内核——

- **Harness**：能力接入层。把外部平台（浏览器、API、数据库、消息）抽象为统一适配器，运营体通过 Harness Gateway 调用真实世界能力。
- **Graph**：可执行因果有向图。流程即图：节点 + 连线 + 数据流，支持拖拽编辑与 DSL 编译（LangGraph）。
- **Loop**：OODA 主循环（感知 → 规划 → 执行 → 反馈）。让运营体在图上循环运转，异常自愈、反思进化。

目标场景从企业内部 OA 审批起步，逐步扩展到电商、物联网等异构场景，最终形成"运营体市场"生态（用户发布训练好的运营体，他人一键导入复用）。

## 当前阶段

**设计文档已完成，代码未启动**（2026-09-13）。全部规格以 `atlas-docs/` 为唯一事实源，共 14 份 AI 导向文档，由两份原始方案文档切分核对而成，零信息丢失。

## 文档导航

| 想看什么 | 去读 |
|---|---|
| 从哪开始 / 治理规则 | [atlas-docs/00-文档索引与治理.md](atlas-docs/00-文档索引与治理.md) |
| 项目当前状态与待办 | [handoff.md](handoff.md) |
| 产品需求与 Demo 范围 | [atlas-docs/01-PRD-产品需求规格.md](atlas-docs/01-PRD-产品需求规格.md) |
| 架构与选型 | [atlas-docs/02-技术架构设计.md](atlas-docs/02-技术架构设计.md)（决策记录见 [10](atlas-docs/10-技术选型决策记录.md)）|
| 数据结构 / 接口 / 测试 | [03 契约索引](atlas-docs/03-数据模型与Schema-契约索引.md) / [12 API](atlas-docs/12-API与模块接口清单.md) / [13 测试](atlas-docs/13-测试用例清单.md) |
| 排期与里程碑 | [atlas-docs/08-任务迭代计划.md](atlas-docs/08-任务迭代计划.md) |
| 缓做事项 / 环境与分支 / 反馈工作流 | [14 缓做登记](atlas-docs/14-缓做事项登记表.md) / [15 环境与分支](atlas-docs/15-环境与分支策略.md) / [16 反馈工作流](atlas-docs/16-反馈工作流.md) |
| 贡献 / 迁移 / 基准 / 许可 | [CONTRIBUTING.md](CONTRIBUTING.md) / [MIGRATION_CONVENTION.md](MIGRATION_CONVENTION.md) / [BENCHMARK.md](BENCHMARK.md) / [LICENSE](LICENSE) |
| AI 落码规范 | [AGENTS.md](AGENTS.md) + [09 工程骨架](atlas-docs/09-工程骨架与目录结构.md) |

## 技术栈（已定选型）

Python 3.11+（引擎）/ Go（Harness 网关，Demo 可暂用 FastAPI）/ TypeScript + React 18（前端）/ **LangChain + LangGraph** / LiteLLM / Playwright + OmniParser / PostgreSQL + pgvector / Redis / NATS / React Flow + Zustand / FastAPI。

> ⏳ 5 项待决策（模型部署、评估层、前端进 Demo 与否、NATS、Go 网关），见 [10 技术选型决策记录 §3](atlas-docs/10-技术选型决策记录.md)。

## 路线图

- **Phase 1** 最小可行引擎（2 个月）：固定操作 Graph + 基础 Harness + 单步决策 Loop，跑通一条审批流程。
- **Phase 2** 动态反馈与 DIY（3 个月）：异常处理 Loop、反思模块、可视化 Graph 编辑器，自然语言定义流程。
- **Phase 3** 平台化适配（3 个月）：开放 Harness 适配器规范，接入电商 + 物联网等异构场景。
- **Phase 4** 智能体市场与进化（持续）：运营体发布与一键导入，策略飞轮。

Demo 里程碑 W1-W10 与验收标准见 [08 任务迭代计划](atlas-docs/08-任务迭代计划.md)。

## 给 AI 协作者的说明

接手本仓库先读 [handoff.md](handoff.md)（状态 + 待办 + 文档索引），写代码前读 [AGENTS.md](AGENTS.md)（行为规范与治理规则）。

## License

MIT License（Copyright © 2026 bayernjf），与 agent-world 一致。贡献即视为同意 MIT 授权（见 [CONTRIBUTING.md](CONTRIBUTING.md)）。
