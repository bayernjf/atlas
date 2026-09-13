# Atlas — AI 运营体编排平台

> 让非技术人员用自然语言或拖拽定义一个流程，由 AI 运营体自动执行、自愈与进化。

Atlas 是一个 **AI 运营体（Agent）编排平台**：以 **Harness / Graph / Loop** 三位一体为内核——

- **Harness**：能力接入层。把外部平台（浏览器、API、数据库、消息）抽象为统一适配器，运营体通过 Harness Gateway 调用真实世界能力。
- **Graph**：可执行因果有向图。流程即图：节点 + 连线 + 数据流，支持拖拽编辑与 DSL 编译（LangGraph）。
- **Loop**：OODA 主循环（感知 → 规划 → 执行 → 反馈）。让运营体在图上循环运转，异常自愈、反思进化。

目标场景从企业内部 OA 审批起步，逐步扩展到电商、物联网等异构场景，最终形成"运营体市场"生态（用户发布训练好的运营体，他人一键导入复用）。

## 当前阶段

**W1-W10 Demo 已全部完成**（2026-09-13）。电商退款端到端链路跑通（08 §7.3 七条验收全达成）：webhook 退款单 → AI 决策（LiteLLM；未配置 `LITELLM_MODEL` 时规则兜底，对齐 06 §9.2 黄金用例）→ shop 适配器执行退款或转人工，SSE 节点事件实时上屏，自然语言可生成退款流程草稿。设计文档以 `docs/` 为唯一事实源（00-16 共 17 份）。下一步进入 Phase 1 种子客户验证（见 [handoff.md](handoff.md)）。

### 本地运行 Demo

```bash
.venv/bin/uvicorn atlas.api.main:app --reload --port 8000   # 后端 API + 模拟商家控制台
cd frontend && pnpm dev                                     # 编辑器 http://localhost:5174
```

- 模拟商家售后控制台：http://localhost:8000/demo/shop（demo/demo）
- 配置 `LITELLM_MODEL`（及供应商 key）即用真实 LLM 决策/生成；不配置时走确定性规则，Demo 离线可跑（见 `.env.example`）

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
| AI 落码规范 | [AGENTS.md](AGENTS.md) + [09 工程骨架](docs/09-工程骨架与目录结构.md) |

## 技术栈（已定选型）

Python 3.11+（引擎）/ Go（Harness 网关产品化目标，Demo 暂用 FastAPI）/ TypeScript + React 19（满足 React 18+）/ **LangChain + LangGraph** / LiteLLM / Playwright + OmniParser / PostgreSQL + pgvector / Redis / Demo 进程内事件总线（NATS 留 Phase 2）/ `@xyflow/react`（React Flow 12）+ Zustand + Ant Design / FastAPI。

> T1-T5 已于 2026-09-13 收口，见 [10 技术选型决策记录 §3](docs/10-技术选型决策记录.md)。

## 路线图

- **Phase 1** 最小可行引擎（2 个月）：固定操作 Graph + 基础 Harness + 单步决策 Loop，跑通一条审批流程。
- **Phase 2** 动态反馈与 DIY（3 个月）：异常处理 Loop、反思模块、可视化 Graph 编辑器，自然语言定义流程。
- **Phase 3** 平台化适配（3 个月）：开放 Harness 适配器规范，接入电商 + 物联网等异构场景。
- **Phase 4** 智能体市场与进化（持续）：运营体发布与一键导入，策略飞轮。

Demo 里程碑 W1-W10 与验收标准见 [08 任务迭代计划](docs/08-任务迭代计划.md)。

## 给 AI 协作者的说明

接手本仓库先读 [handoff.md](handoff.md)（状态 + 待办 + 文档索引），写代码前读 [AGENTS.md](AGENTS.md)（行为规范与治理规则）。

## License

暂无（许可证未定，LICENSE 文件已移除）。

