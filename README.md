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
- **M11 之后是「真实接入与交付」批链（docs/27–61，2026-09-20 → 2026-09-25）**，逐批立项与落码条见 [docs/08](docs/08-任务迭代计划.md)，能力面按组：
  - **真实接入**：Shopify 渠道适配与入站 Webhook（HMAC 验签/幂等环/订阅注册）、OpenAPI 导入自动生成工具（规格 PG 持久化、securitySchemes 静态密钥、HTTP Basic 子集、去重软删）、通用 HTTP 与数据库渠道、出向安全准入（SSRF 校验 + 凭证信封 AES-256-GCM 加密与脱敏）。
  - **审批与人机协同**：审批卡片三渠道渲染、邮件内一键决策（HMAC 签名 capability URL，免登录）、已决审批历史（打包 H 起 PG 落库）、审计日志页与写操作审计中间件。
  - **可靠性与运维**：告警中心、静默/值班（PG 化 + 惰性按日自动轮换）、告警外部通知与投递退避、外部通知 flapping 抑制、灰度门控自动回滚、影子运行（旁路决策比对，打包 H 起 PG 落库）。
  - **运行时体验**：span 级链路追踪、单步调试与条件断点、wait 三形态（动态时长/到点时刻/事件等待，事件帧跨重启）、condition LLM 语义分支、loop foreach 批处理、编译诊断逐条定位（打包 H 起后端 422 侧车在前端 Problems 面板兑现）。
  - **产品化**：en-US 全量翻译与运行时语言切换、设计 Token 层、用例集报告 PG 持久化与跨图看板、审计过滤 + 游标分页。
  - 验证命令：后端 `.venv/bin/pytest`、PG 直连见 [docs/13](docs/13-测试用例清单.md)、前端 `pnpm vitest run` / `pnpm run lint` / `pnpm build`；**最近一次门结果与推送状态以 [handoff.md](handoff.md) 为准**（本文件不复制会随下一次提交过期的数字）。
- **W1-W10 Demo（2026-09-13）为基线**：电商退款端到端链路（webhook 退款单 → AI 决策 → shop 适配器执行退款或转人工，SSE 实时上屏，NL 生成草稿）；规则兜底离线可跑，配置 `LITELLM_MODEL` 即用真实 LLM。
- 设计文档以 `docs/` 为唯一事实源；当前状态与待办见 [handoff.md](handoff.md)，排期见 [docs/08](docs/08-任务迭代计划.md)，种子客户计划见 [docs/18](docs/18-种子客户验证计划.md)。

### 本地运行 Demo

Docker 一键启动（Phase 1 种子客户交付形态，只需 Docker）：

```bash
docker compose up --build        # 编辑器 http://localhost:8000，模拟商家控制台 /demo/shop（demo/demo）
curl -X POST http://localhost:8000/api/demo/reset   # 重置种子退款单与已保存图
```

种子客户试用按 [TRIAL.md](TRIAL.md) 一页纸操作（三场景 + 反馈表）。

> **只能单副本运行**（一个进程、一台机器）。多进程会让同一条挂起审批帧在各进程各自续跑一次——2026-09-25 实测：一次人工"通过"后，一个进程走通过分支、另一个进程超时走拒绝分支，下游真实副作用被执行两遍（证据与机制见 [docs/34](docs/34-MVP上线就绪评审-2026-09-22复审.md) 的复审更新注记）。容器入口对 `workers>1` 直接拒绝启动，compose 已钉 `replicas: 1`；[docs/62](docs/62-挂起帧一次性认领-单副本硬约束护栏-v1批契约设计.md) 的帧一次性认领**已于 2026-09-25 落码**（一次审批下游只跑一遍），但**这不代表支持多副本**：登录节流、`/metrics` 聚合、灰度运行态、跨进程急停仍是进程内。部署面约束全文见 [docs/15](docs/15-环境与分支策略.md) §四。

> **prod 档位（`ATLAS_ENV=prod`）另需三样**：`ATLAS_MASTER_KEY`、`ATLAS_APPROVAL_HMAC_SECRET`（各 ≥32 字节），以及首任管理员引导口令 `ATLAS_ADMIN_BOOTSTRAP_PASSWORD`（[docs/66](docs/66-prod首任管理员引导批-v1批契约设计.md)）——任一缺失或不合规则，进程**拒绝启动**（fail-closed）。缺省档位是 `dev`，上面的 Demo 形态不需要任何密钥。

本地开发双进程：

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
| AI 落码规范 | [AGENTS.md](AGENTS.md) + [09 工程骨架](docs/09-工程骨架与目录结构.md)（前端 i18n/视觉契约见 [17](docs/17-前端国际化与设计Token方案.md)） |

## 技术栈（已定选型）

Python 3.11+（引擎）/ Go（Harness 网关产品化目标，Demo 暂用 FastAPI）/ TypeScript + React 19（满足 React 18+）/ **LangChain + LangGraph** / LiteLLM / Playwright + OmniParser / PostgreSQL + pgvector / Redis / Demo 进程内事件总线（NATS 留 Phase 2）/ `@xyflow/react`（React Flow 12）+ Zustand + Ant Design / FastAPI。

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

**不开放（proprietary）**——无 LICENSE 文件，`pyproject.toml` 与 `frontend/package.json` 亦无 license 字段。MIT 曾于 2026-09-22 短暂落地（`c40b46c`），同日由用户拍板删除并保持不开放（`f721c18`）；决策记录见 [docs/08](docs/08-任务迭代计划.md) 与 [docs/34](docs/34-MVP上线就绪评审-2026-09-22复审.md) 的 2026-09-22 更新注记。

