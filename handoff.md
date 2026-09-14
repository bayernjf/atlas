# Handoff — Atlas

更新时间：2026-09-14

## 项目概况

Atlas 是 AI 运营体（Agent）编排平台：以 **Harness（能力接入）/ Graph（可执行因果图）/ Loop（OODA 主循环）** 三位一体为内核，面向企业流程自动化场景（首个目标场景：企业内部 OA 审批），目标是让非技术人员通过自然语言/拖拽定义流程，由运营体自动执行、自愈与进化。

当前阶段：**Phase 1 沙盘假定通过，进入 Phase 2（2026-09-14，用户指示的假定，非真实试用结论）**。W1-W10 Demo 全部完成（W9-W10 电商退款端到端 Demo 于 2026-09-13 跑通，08 §7.3 七条验收全部达成）。**Phase 2 前四项「条件分支节点 condition」「循环节点 loop（v1 条件循环 while）」「并行节点 parallel（扇出/汇聚网关，v1 all_success/all_completed）」「等待节点 wait（v1 定时等待 duration 1-600s）」均已于 2026-09-14 端到端落地**：condition 节点 config 挂 branches/defaultTarget；loop 节点 config 挂 mode/continueExpression/maxIterations/bodyTarget/exitTarget，循环体回边是唯一合法环（U7 调和），达最大次数/表达式异常 fail-safe 退出；parallel 节点 config 挂 joinStrategy/branches/joinTarget，合成 `__join__` 网关节点做汇聚屏障（spike 实测普通多入边不构成 barrier），all_success 分支失败 fail-safe 汇聚不短路（any_success 缓做 14 D18）；wait 节点 config 挂 waitType/durationSeconds，执行器同步 sleep（线程池边界，600s 上限），恰好一条普通出边纯透传，事件等待/动态时长缓做 14 D19；四项均 EdgeDSL 与 Graph version 不变。纯 stdlib 安全规则表达式（禁 eval/算术/函数）为 condition/loop 两类节点共用，DSL 图级校验、LangGraph 全 conditional 出边齐备，前端配置面板/多出口 Handle/出边标签齐备，浏览器实测金额分流、循环三场景（条件收敛/撞上限 fail-safe/表达式异常）与并行三场景（不等长双分支汇聚/一支失败 fail-safe/坏配置报错）。退款链路：webhook 退款单 → AI 决策或条件分支 → shop 适配器执行退款或转人工；SSE 节点事件实时上屏，自然语言可生成退款流程草稿。Phase 1 种子验证工程侧齐备（Docker 一键交付/reset/反馈入口/docs 18+TRIAL），但**无真实 C1-C5 试用数据**，Go 结论为沙盘假定（18 文档 §8）；真实试用若 No-Go 需回退。下一步按 08 Phase 2：等待/子图/人机协作节点、API/DB/消息适配器、模板库、多租户（14 D7）等，下一项待用户拍板。

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

**代码阶段待补（遵循 agent-world 惯例）**：`.pre-commit-config.yaml`。CI 工作流（`.github/workflows/ci.yml`，gitleaks/后端 pytest/前端 vitest+build 三道门）已于 2026-09-14 落地（14 D10a，ADR T8）；CD 仍缓做（14 D10b）。`Dockerfile` + `docker-compose.yml` + `.dockerignore` 已在 Phase 1 准备期落地（2026-09-13）。`pyproject.toml` 与前端工程已在 W1 创建。

## Current state（当前状态）

- **阶段**：W9-W10 电商退款端到端 Demo 已完成（2026-09-13，08 §7.3 七条验收全达成）。新增 `llm/`（LiteLLM 退款决策 + 规则兜底 + NL 草稿生成）、`shop/`（DemoShopService + ShopHarnessAdapter：login/list_pending_refunds/execute_refund/request_human_approval/process_refund）两个包；`graph/loader.py` 经依赖注入接真实决策/适配器/事件回调；API 新增 SSE 运行流、NL 生成、适配器发现与模拟商家控制台；前端退款单选择、节点实时状态、NL 草稿载入。Phase 2 节点推进后后端 127 单元全绿、前端 vitest 39 全绿（明细见 Quality gate），浏览器实测 12345→refunded / 12346→human_review。
- **仓库**：git 仓库，**已推送 GitHub private 仓库 `bayernjf/atlas`**（main 为生产分支、dev 为集成分支，分支策略见 15 文档）。PR #3（dev→main，W9-W10 后全部 Phase 1 工作：设计 Token/Docker/反馈/CI/benchmark，19 提交）已于 2026-09-14 合并（merge `f069b53`），main CI 全绿。
- **技术选型**：✅ 已全部收口（2026-09-13，T1-T5 见 10 文档 §3）：Python 3.11+ 引擎 / Go Harness 网关为产品化目标、**Demo 用 Python/FastAPI 实现同构接口** / TypeScript + React 19（满足 React 18+）/ LangChain+LangGraph / LiteLLM（Demo 对接主流商业 API，`.env` 切换，不引入 vLLM）/ Playwright / PostgreSQL+pgvector / Redis / **Demo 进程内事件总线替代 NATS** / FastAPI / `@xyflow/react` 12 + Zustand 5 + AntD 6 / 评估优化层仅留接口；剩余 ⏳（vLLM、策略训练、多语言）均为后续阶段范围。
- **Demo 依赖清单已落码**：后端在 `pyproject.toml`，前端在 `frontend/package.json` / `pnpm-lock.yaml`（版本为 2026-09-13 解析的稳定版）。
- **关键文件**：后端 `src/atlas/`（11 个包；engine 最小循环、graph DSL 契约+编译器（W9-W10 注入决策/适配器/事件）、llm 退款决策+NL 生成、shop Demo 店铺服务+适配器、api FastAPI（graphs/SSE/NL/adapters/Demo 控制台）、memory 连接层、harness 契约/注册、web 三层定位适配器）、`db/migrations/001_enable_pgvector.sql`，前端 `frontend/`（Dashboard/Editor + 画布/节点/属性/变量面板 + `lib/` 纯逻辑（变量/节点目录/序列化/API client 含 SSE）+ Zustand store + vitest 单测）；模块填充顺序见 09 文档待填项清单。

## Active work / 待办

按优先级降序：

1. ✅ **W1-W2 基础骨架落地**（08 文档 7.1，2026-09-13 全部完成）：工程骨架/venv、LangGraph 最小 OODA 循环、前端编辑器框架、PostgreSQL+pgvector 连接均已跑通（详见 Recently shipped）。
2. ✅ **W5-W6 编辑器核心功能**（08 文档 7.1，2026-09-13 完成）：拖拽画布、3 种节点类型化配置 + 实时校验、全局变量与 `{{路径}}` 引用、Graph JSON 导出；vitest 20 个单测全绿。
3. ✅ **W7-W8 编译与运行**（08 文档 7.1，2026-09-13 完成）：Graph DSL 契约 + 静态校验、DSL→LangGraph 编译/运行、FastAPI graphs 保存/读取/编译/运行、编辑器一键"编译并运行"；后端新增 16 个用例。
4. ✅ **W9-W10 端到端 Demo**（08 文档 7.1，2026-09-13 完成）：电商退款完整链路跑通（LiteLLM 决策 + 规则兜底、shop 退款业务能力、SSE 实时进度、NL 退款草稿、模拟商家控制台），08 §7.3 七条验收逐条达成（映射见 08 W9-W10 落码记录）。
5. ⏭️ **Phase 2 推进中（2026-09-14，沙盘假定 Go）**：18 文档 §8 记录假定验证通过。**首项「条件分支节点」、第二项「循环节点（while）」、第三项「并行节点（parallel 扇出/汇聚）」、第四项「等待节点（wait 定时等待）」已完成（2026-09-14，见 Recently shipped 1/2/3/4）**。08 Phase 2 剩余范围——新增节点（子图、人机协作）、API/DB/消息适配器、模板库、操作录制、单步调试断点、基础监控告警、多租户权限（14 D7 触发条件随假定满足，待立项取回）；下一项任务待用户拍板。condition 的 LLM 判断分支与表达式函数库/算术缓做（14 D14/D15），loop 的遍历循环与 break/continue 缓做（14 D16/D17），parallel 的 any_success（OR-join+取消分支）缓做（14 D18），wait 的事件等待（中断-恢复）与动态时长缓做（14 D19，触发：事件驱动/长等待业务 + 11 S1 持久化就绪）。缓做触发：D6 Go 网关触发条件字面为"进入 Phase 2"，但建议待多实例部署需求明确再启动（与 D5 NATS/D10b CD 同批）。
6. 文档缺口登记：`09-工程骨架` 待定项 1/2/3/4/5/6 均已收口（待定项 3：不新增 deployment/ 包，Dockerfile + docker-compose.yml 放仓库根，2026-09-13）。新增测试运行器选型 vitest 已记 10 文档 §4 ADR（W5-W6）。
8. ✅ **Phase 1 种子客户交付准备（工程侧已齐，试用为沙盘假定）**：Docker Compose 一键启动（`Dockerfile` 多阶段 + FastAPI 同源托管 `frontend/dist` + `POST /api/demo/reset` 重置种子数据，镜像实测黄金用例通过，2026-09-13）、种子验证计划 [docs/18](docs/18-种子客户验证计划.md) + 客户向 [TRIAL.md](TRIAL.md)（2026-09-13）、应用内反馈入口（编辑器"反馈"按钮 + `POST/GET /api/feedback`，进程内存储、reset 不清除，后端 62 测试/浏览器实测通过，2026-09-13）。业务动作（招募 3-5 家、回填 18 §6.1）在沙盘假定下标记完成但无真实数据；若真实试用启动，仍按 18 文档执行并以实测覆盖假定。
9. ✅ **D10a CI 质量门（2026-09-14 完成并验证）**：14 D10 拆分后 CI 部分提前触发（ADR T8 见 10 文档 §4，08 Phase 1 已记录）。`.github/workflows/ci.yml` 三道门——gitleaks 全历史密钥扫描、后端 pytest（Python 3.11，integration 默认跳过）、前端 oxlint + vitest + `pnpm build`（Node 22 / pnpm 10）；main/dev 推送与 PR 触发，重复运行并发取消。PR #3 实跑发现 gitleaks 在 pull_request 事件需 `pull-requests: read`（403，commit `247372e` 修复），修复后 PR 全绿、合并后 main（f069b53）CI success。CD 留 14 D10b（远程部署目标确定后）。
10. ✅ **D9 BENCHMARK 实测（2026-09-14 完成）**：新增 `scripts/benchmark.py`（纯 stdlib，零新依赖：10% warmup + 300 次/场景，p50/p99，输出 Markdown 行，DATABASE_URL 时追加 DB ping）；[BENCHMARK.md](BENCHMARK.md) 填首份基线（commit 407812e，macOS arm64 / CPython 3.11.15）：OODA 循环 2.05ms / ~488 loops/s、3 节点图编译 1.81ms、退款端到端（规则路径）2.28ms、Harness 进程内调用 0.002ms。LLM 决策延迟、记忆层读写、并行扇出、长流程内存增长在 BENCHMARK Scope 标注待接入补测。

**Active feedback**：暂无（种子试用开始后，由 docs/18 §6.1 跟进项迁移至此，解决后留痕 Recently shipped；流程见 16 文档）。
7. 📋 **i18n 与设计 Token 方案已定（2026-09-13，docs/17）**：设计 Token 等价替换**已落码**（`frontend/src/theme/tokens.ts` 单一事实源 + `setup.ts` 注入 `--atlas-*` 变量 + AntD theme，全仓零硬编码色值，浏览器零视觉差异）；i18n 库（i18next+react-i18next）按触发条件引入（14 D12：首个英文使用者/出海需求），组件描述多语言随 Phase 2 模板库（14 D13）。

> 缓做/低优项：统一登记在 [docs/14-缓做事项登记表.md](docs/14-缓做事项登记表.md)（每条带触发条件，条件满足移回本区并标注重启日期）。当前含移动端适配器、策略训练、组件市场、模型路由器、NATS、Go 网关、多租户、运营体市场、CD 自动化部署、可观测性、i18n 落码、组件描述多语言等项（D10a CI、D9 BENCHMARK 均于 2026-09-14 完成移出）。

## Recently shipped（最近变更）

1. **feat(graph/frontend/llm): Phase 2 第四项——等待节点 wait（定时挂起，2026-09-14）**——契约写入 04 §5.5 权威 blockquote（单节点 config `{waitType:"duration", durationSeconds}`，**EdgeDSL 与 Graph version 1 仍不变**）。两项用户拍板决策：① v1 仅定时等待（执行器内同步 `time.sleep`，node_start 在 sleep 前发出，到点沿唯一普通出边继续），事件等待（外部信号/回调恢复，需 LangGraph interrupt/checkpointer）与动态/表达式时长一并缓做 14 D19（触发：真实事件驱动或分钟级长等待业务 + 11 S1 持久化就绪）；② 时长为整数常量 1-600 秒。运行时边界：SSE 同步生成器跑在 Starlette 线程池 worker，阻塞不卡事件循环但占用一个 worker，600s 即占用上限；等待不失败不重试。纯透传控制节点：恰好 1 条出边（目标存在、≠自身、不直连 END），无新增拓扑规则（可位于 condition 分支/loop 体内/parallel 区域），recursion_limit 公式与普通边装配零改动。产出 `{mode:"wait",waitType:"duration",durationSeconds}`，trace `wait-x: waited Ns`，下游可引用 `{{wait-x.durationSeconds}}`。DSL `_validate_wait_config`（waitType 非 duration——含 event 明确文案「事件等待暂不支持」——/非整/bool/越界/出边 0 或 2/自环/悬空中文聚合报错）。前端：橙色 orange6 节点（新增 `color-node-wait`；purple6 已属 AI 节点，不撞色）、WaitConfig（定时 Radio 可选/事件 Radio 禁用 tooltip「随持久化层开放」、InputNumber 1-600 precision 0 越界失焦钳制 + 空值即时中文报错，antd6 弃用 addonAfter 改 Space.Compact）、默认单 Handle（AtlasNode/FlowCanvas/editorStore 零改动）、变量路径 `wait-x.durationSeconds`、SSE 日志「等待完成：N 秒」。NL prompt 枚举六类→七类。测试：后端 127 单元（+DSL wait 12/loader 2/NL prompt 1，8 integration 跳过），前端 vitest 39（catalog wait 校验矩阵/variables，5 文件）；浏览器对真实后端实测 trigger→wait(1s)→tool_call 非流式运行真实 1s 墙钟（总 1.09s）、后继恰好执行、outputs 形状与 `waited 1s` trace 正确，面板 0/601/1.5 钳制为 1/600/2、空值报错，控制台零新增错误。注：Demo `/run/stream` 先跑完再回放事件队列为既有设计（非 wait 引入），逐节点实时性留待持久化阶段治理。commits：232ea16（docs 契约）/d072db5（DSL 校验）/132d785（loader sleep）/d45b0ae（frontend lib）/2f7c5b1（frontend UI）/29d027e（addonAfter 修正）/ff37311（LLM prompt）。

2. **feat(graph/frontend/llm): Phase 2 第三项——并行节点 parallel（扇出/汇聚网关，2026-09-14）**——契约写入 04 §5.4 权威 blockquote（单节点 config `{joinStrategy, branches:[{label,target}], joinTarget}`，**EdgeDSL 与 Graph version 1 仍不变**）。三项用户拍板决策：① v1 仅 `all_success`/`all_completed` 两策略，`any_success`（真 OR-join，需提前汇聚+取消分支，LangGraph 静态扇入不支持）缓做 14 D18（触发：真实竞速/先到先得业务）；② all_success 下分支 FAILED 走 **fail-safe 汇聚**——joinTarget 照常执行、parallel 产出 `status=failed`+失败分支错误、run 仍 completed，下游可 condition 判 `{{parallel-x.status}}`；③ 分支区域（branch targets BFS、遇 joinTarget/parallel 停止）内允许 condition/自包含 loop，禁嵌套 parallel/trigger/交叉/外泄，各入口必须可达 joinTarget。关键技术事实：spike 实测 LangGraph 普通多入边**不构成 barrier**——`GraphState.outputs` 改 `Annotated[dict, _merge_outputs]`（key 级合并）、`status` last-write、各执行器只回自身分片（横切五处执行器，零回归）；编译期注入合成网关 `__join__<parallel-id>`（冒号为 LangGraph 保留名），分支末端 retarget 网关，网关就绪计数 self-loop wait 超步（空 messages 写）等待不等长分支，齐后聚合再路由真 joinTarget；`__join__` 事件不外泄，汇聚完成时以 parallel 节点自身补发第二次 node_end（fork 时第一次为 running）。DSL `_validate_parallel_config`（2-10 分支/label 非空唯一/target 互异/出边恰好 N 条不直连 END/区域泄漏/交叉/嵌套/trigger/不可达 join 中文聚合报错）；聚合产出 `{mode,joinStrategy,status,branches:[{label,target,status,error}],result:{<入口节点id>:末端产出},joinTarget}`，trace `fork N branches → …`/`joined (strategy) …`。前端：品红 magenta6 节点、ParallelConfig（策略 Radio 中文说明/分支增删 2-10/汇聚目标 Select）、N source Handle（b{index}）与分支边标签、删除清理、变量路径 `parallel-x.status`、SSE 日志（并行启动 N 分支/汇聚全部成功/N 支失败 fail-safe 文案）。NL prompt 枚举补 parallel。测试：后端 112 单元（+DSL parallel 10/loader 3/退款 e2e 1/NL prompt 1，8 integration 跳过），前端 vitest 37（catalog/variables/store parallel 用例，5 文件）；浏览器对真实后端三场景实测——trigger→parallel→{短支/长支}→join 两支都执行且 join 恰好一次（params 渲染 `状态=success`，result 按入口 id 聚合）、长支配未注册适配器触发「1 个分支失败：长支（适配器未注册：bogus）（汇聚节点仍执行）」、空 label/重复 target/缺 joinTarget 属性面板三条即时报错，控制台零新增错误。commits：9bbadb6（frontend lib）/7d9ff89（gate SSE emit）/b27055e（frontend UI）/918831b（LLM prompt）。

3. **feat(graph/frontend/llm): Phase 2 第二项——循环节点 loop（条件循环 while，2026-09-14）**——契约写入 04 §5.3 权威 blockquote（config `{mode:"while", continueExpression, maxIterations(1-100, 默认10), bodyTarget, exitTarget}`，**EdgeDSL 与 Graph version 1 仍不变**）；两项用户拍板决策：① v1 仅 while，复用 condition 表达式白名单，循环变量只暴露 `index`（遍历循环/item/break-continue 缓做 14 D16/D17）；② 达最大次数 fail-safe 退出（正常路由 exitTarget、`exitReason=max_iterations`、运行仍 completed），表达式运行时异常同样 fail-safe（`expression_error`）。拓扑：loop 恰好两出边对配 body/exit，循环体 BFS 界定，回边（体→同 loop）是唯一合法环（DSL 临时摘除白名单回边后 DFS 环检测，调和 U7），体内禁嵌套 loop/trigger、禁泄漏 exitTarget、游离体分支拒绝，中文聚合报错。后端：`dsl.py` `_validate_loop_config`/`_loop_body_set`/`_validate_illegal_cycles`；`loader.py` 可重入 `_execute_loop`（重访覆盖 outputs、首轮播种 `{{loop-x.index}}`）、出边全 conditional、`recursion_limit=2N+2·ΣmaxIter·(bodySize+1)+10` 经 invoke config 传入；产出 `{mode,iterations,index,target,exitReason,expression_errors}`，trace 行 `continue (i/max) → body` / `exit (reason) after N → exit`。前端：循环节点面板（青色 cyan6）、LoopConfig（表达式实时校验/变量插入/最大次数/体·退出目标）、双 Handle（body/exit 纯视觉）、出边「循环体」「退出」标签、删除目标清理、SSE 日志中文化（继续第 N 轮；退出：条件不满足/达到最大次数/表达式异常）。NL prompt 枚举补 loop。测试：后端 97 单元（+DSL loop 8/loader 3/退款 e2e 1/NL prompt 1，8 integration 跳过），前端 vitest 34（catalog/variables/store loop 用例）；浏览器对真实后端三场景实测——条件收敛（体执行 2 轮→条件不满足退出）、恒真撞上限（3 轮→达到最大次数）、坏变量（0 轮→表达式异常），边标签/节点高亮/控制台无新增错误。

4. **feat(graph/frontend/llm): Phase 2 首项——条件分支节点 condition（2026-09-14）**——契约写入 04 §5.1（首版语法白名单）/§5.2（condition config 权威契约）：分支挂节点 config `{branches:[{label,expression,target}], defaultTarget}`，**EdgeDSL 与 Graph version 1 均不变**；branches 按序短路、default 必填、label/target 唯一、target 须存在且有出边、每条出边须被分支覆盖、不允许直连 END。后端：新增 `graph/conditions.py`（纯 stdlib 手写递归下降：`{{路径}}` 变量、比较 `> >= < <= == !=`、逻辑 `&& || !`、括号、数字/字符串/true/false/null；校验期中文报语法与纯字面量类型错误，运行时错误 fail-safe 走 defaultTarget，禁 eval 零新依赖）；`dsl.py` condition 图级校验 + 从 trigger 根做可达性 BFS；`loader.py` condition 出边全部走 `add_conditional_edges`（执行器只求值一次写 outputs，router 只读 target，规避同源普通/条件边双激活），产出 `{branch,target,evaluation,expression_errors}`，trace 行 `condition-x: branch=… → target`。前端：节点面板新增「条件分支」（金色）、ConditionConfig 属性面板（分支增删/表达式实时中文校验/插入 `{{变量}}`/目标节点 Select/默认分支）、节点多 source Handle（b{index}/default 纯视觉不序列化）、出边按 target 反查显示分支标签、删除目标节点自动清空引用并报错、运行 trace 显示分支与终态工具结果；`lib/conditions.ts` 与后端同构的 TS 校验（不做求值）。NL 生成 system prompt 枚举补 condition 与 config 形状。测试：后端 84 单元（+conditions 9/DSL 5/loader 4/e2e 3/NL prompt 1 等，8 integration 跳过），前端 vitest 32（conditions 4 等）；浏览器对真实后端实测 trigger→condition→两个 tool_call：订单 12346（¥5000）只执行转人工（human_review），12347（¥128）走默认分支自动退款（refunded），坏表达式面板中文报错，删除目标节点报「必须配置默认分支」。LLM 判断分支、函数库/算术缓做（14 D14/D15）。

5. **chore(bench): D9 性能基准脚本与首份基线（2026-09-14）**——新增 `scripts/benchmark.py`（纯 stdlib 零新依赖：每场景 10% warmup + 300 次采样，p50/p99 + 吞吐，输出 BENCHMARK.md Results 格式 Markdown 行；`DATABASE_URL` 存在时追加 DB ping）。场景：OODA `run_loop(max_steps=2)` 吞吐、退款 3 节点图 `compile_graph` 编译时延、12345 自动退款 `run_graph` 端到端时延（规则决策路径，`service.reset` 在计时外）、`shop/list_pending_refunds` Harness 调用开销。首份基线（commit 407812e，CPython 3.11.15 / macOS arm64）：OODA p50 2.05ms（≈488 loops/s）、编译 1.81ms、退款端到端 2.28ms、Harness 调用 0.002ms。BENCHMARK.md Scope 标注四项待接入补测（LLM 决策延迟随真实供应商、Redis/pgvector 记忆读写随 11 S1、并行扇出随 Phase 2 节点、长流程内存增长随递归引擎）；Known limits 说明本基线仅为进程内确定性回归锚点。09 目录树补 `scripts/`；08 Phase 1 记 D9 重启（零新依赖故无 ADR）。

6. **chore(ci): D10a GitHub Actions CI 质量门（2026-09-14）**——新增 `.github/workflows/ci.yml`：gitleaks 全历史密钥扫描（沿用根 `.gitleaks.toml`，关闭 PR 评论/产物上传，仅 contents:read）、后端 pytest（Python 3.11，pip 缓存；integration 标记默认跳过）、前端 oxlint + vitest + `pnpm build`（Node 22 / pnpm 10，frozen-lockfile）；push main/dev 与 PR 触发，同 ref 并发取消。D10 拆分为 D10a（CI，本次完成）/D10b（CD，仍缓做，触发条件：远程部署目标确定），ADR T8 落 10 文档 §4，08 Phase 1、09 待定项 3 注记、14 登记表同步。本地等价命令全绿：后端 62 passed/8 skipped、前端 vitest 23、`pnpm build` 通过（AntD 体积提示为已知非阻塞项）、gitleaks 55 commits 无泄漏；Actions 实跑结果待 dev 推送后验证。

7. **feat(llm/shop/graph/api/web): W9-W10 电商退款端到端 Demo（2026-09-13）**——新增 `llm/`（`decision.py` DecisionClient：LITELLM_MODEL 配置时经 LiteLLM 决策、JSON 解析失败 fail-safe 转人工；未配置时 RuleBasedDecisionClient 按 06 §9.2 黄金规则确定性兜底；`nl_generate.py` NL→Graph 草稿，LLM 优先退款模板兜底）与 `shop/`（`service.py` DemoShopService 五笔种子退款单 + 退款/转人工状态流转；`adapter.py` ShopHarnessAdapter 五能力 login/list_pending_refunds/execute_refund(financial)/request_human_approval/process_refund，按上游决策路由，结构化错误 + 权限门）。`graph/loader.py` 依赖注入 decision_client/registry/emit/trigger_payload，webhook 载荷经 run inputs 进入 trigger context.payload（同名键覆盖全局变量），node_start/node_end/run_end 事件回调。`api/main.py` 新增 /run/stream SSE、/nl/generate、/adapters、Demo 店铺登录/订单接口与 /demo/shop 控制台页面，共享 Demo 服务单例；dsl 顺带修复 triggerType schedule/cron 取值对齐前端。前端：退款单选择器、SSE 流式运行（节点脉冲/完成描边 + 调试台事件日志）、NL 生成弹窗载草稿、退款三节点种子图。测试：后端 58 单元（新增决策 6/店铺 8/NL 2/端到端 3/API 6/schedule 1）+ 店铺平台集成 1（opt-in），前端 vitest 23；浏览器实测 12345 破损→approve_refund→refunded、12346 主观→request_human_approval→human_review、NL 草稿 3 节点、控制台 demo/demo 登录拉单。文档 08/09/12/13 已同步。
4. **feat(graph/api): W7-W8 编译与运行（2026-09-13）**——`graph/dsl.py` 承接前端 Graph JSON（version 1）：pydantic GraphDSL/NodeDSL/EdgeDSL + 静态校验（版本、节点/连线 id 唯一、边端点存在、禁自环、三类节点分类型必填配置、未支持类型拒绝），错误一次性聚合为中文列表；`graph/loader.py` 将 DSL 编译为 LangGraph StateGraph（DSL 节点 1:1 成图节点，边原样装配，无前驱接 START、无后继接 END），`run_graph` 播种全局变量并在运行时做 04 §6.3 `{{路径}}` 插值（可读上游节点产出，缺失引用原样保留），三类节点执行器为确定性占位（LLM 随 LiteLLM、真实工具随适配器联动替换）。`api/main.py` FastAPI 落地：`GET /api/health`、`POST /api/graphs`、`GET /api/graphs/{id}`、`POST /api/graphs/{id}/compile`（返回拓扑/入口/终点）、`POST /api/graphs/{id}/run`（返回状态/产出/traces），进程内图存储（与 T4 同假设，持久化随 11 S1），校验失败统一 422。前端新增 `lib/apiClient.ts` 与编辑器"编译并运行"按钮（结果弹窗 + 422 中文错误 Alert）。测试新增 16 个后端用例（dsl 7/loader 5/API 4），默认 `pytest` 32 passed/7 skipped；浏览器对真实后端验证保存→编译→运行（`{{global.approval_limit}}`→500）与 422 错误路径。边界决策（graph 独立包、进程内存储、/run 端点）记 09 待定项 6，03 新增 graph_definition 契约索引，12 REST 表补 /run。
5. **feat(web): W5-W6 编辑器核心功能（2026-09-13）**——`lib/variables.ts`（04 §6.3 `{{路径}}` 语法：extractRefs/interpolate/resolvePath、标识符校验、全局变量+节点输出路径清单）、`lib/nodeCatalog.ts`（trigger/ai_decision/tool_call 三类目录、默认 config/retry、中文实时校验）、`lib/graphSerializer.ts`（03/04 §3.2 node_schema 形状的 Graph JSON，version 1，位置取整）；`store/editorStore.ts` 重写（addNodeAt 用 `nextId` 扫已有 id 防 React Flow id 碰撞、选中/配置/删除级联边/变量 CRUD/连线日志，种子审批示例 3 节点 + 1 变量）；`AtlasNode` 自定义节点（类型配色 + 错误角标）、`FlowCanvas` HTML5 拖拽落点（screenToFlowPosition）、`NodePanel` 拖拽面板、`VariablesPanel` 变量增删、`PropertyPanel` 分类型配置表单（含插入变量引用、retry 策略、实时错误清单）、Editor 加"导出 Graph JSON"预览弹窗。测试：vitest 5 新增（ADR 记 10 §4），4 个测试文件 20 用例全绿（变量/校验/序列化/store 碰撞与级联）；`pnpm build` 通过；浏览器人工验证拖入节点、校验角标联动、变量插入、导出 JSON（version 1、4 节点、2 变量、2 边），应用零控制台错误。修复：根 `.gitignore` 的 Python 规则 `lib/` 误伤 `frontend/src/lib/`，改为 `/lib/` 锚定根目录。4. **feat(web/harness): W3-W4 Harness Web 适配器（2026-09-13）**——`harness/base.py` 落地 06 §6.4 同构契约（HarnessAdapter ABC、ActionRequest/ActionResult/Capability/Observation、Permission 枚举 read/write/delete/financial），execute 模板方法做权限校验+审计（I8）；`harness/registry.py` 进程内注册/发现/心跳健康（04 §4.4）；`web/location.py` 三层定位（层1 选择器 3000ms 超时 + 选择器缓存、层2 视觉语义置信度严格 >0.7、层3 全图推理坐标、全失败兜底文案），层2/3 视觉组件以协议注入（模型接入前为桩）；`web/adapter.py` Playwright sync 适配器，工具集 navigate/click/type/screenshot，headless 受 `PLAYWRIGHT_HEADLESS` 控制。测试 17 个：契约/定位单元测试 13 个全绿，真实 Chromium 集成 4 个（I1/I2/I5 + type/observe）全绿；默认 `pytest` 16 passed/7 skipped，`ATLAS_RUN_INTEGRATION=1` 全量 23 passed。包边界决策（harness=契约/注册，web=Playwright 实现，不合并）已记 09 待定项 2 + 08 §7.2。5. **feat(memory): PostgreSQL + pgvector 连接层验证（2026-09-13）**——本地 Docker 跑 `pgvector/pgvector:pg16`（容器 `atlas-pg`，5432）；迁移 `db/migrations/001_enable_pgvector.sql` 启用 vector 0.8.6；`memory/settings.py`（环境变量单一读取点，`DATABASE_URL` 缺失 fail-closed）、`memory/database.py`（SQLAlchemy 引擎/会话工厂，强制 `postgresql+psycopg` 即 psycopg 3 驱动，`ping`/`pgvector_version` 探针）；`tests/test_database_integration.py` 3 个 integration 标记用例（ping、扩展版本、vector 类型写入+余弦距离排序），默认跳过，`ATLAS_RUN_INTEGRATION=1` + `DATABASE_URL` 开启，对容器全绿。`.env` 已本地创建（gitignored），`.env.example` 连接串同步为 psycopg 写法。6. **feat(web): 前端编辑器框架跑通（2026-09-13）**——`frontend/` 采用 Vite 8 + React 19 + TS 6 + `@xyflow/react` 12（替代弃用包 `react-flow-renderer`）+ Zustand 5 + AntD 6；页面含 Dashboard/Editor，组件含 FlowCanvas/NodePanel/PropertyPanel/DebugConsole，Zustand 管理 nodes/edges/选中节点/日志；`pnpm build` 通过，浏览器验证初始 3 节点、添加节点、属性编辑与日志，控制台零错误。dev 端口 5174，`/api` 代理 8000。7. **feat(engine): LangGraph 最小 OODA 循环跑通（2026-09-13）**——`engine/state.py`（LoopState 按 06 §6.1/12 §1.1 契约，messages/observations 加 add 归约器）、`engine/nodes.py`（observe/orient/decide/act/reflect 五节点 + 三条 conditional 路由，确定性占位、签名不偏离 12 §1.2）、`engine/loop.py`（StateGraph 装配，拓扑同 12 §1.3；`build_graph/initial_state/run_loop`）；`tests/test_engine_loop.py` 3 用例全绿；venv（Python 3.11.15）依赖安装与端到端 `run_loop` 验证通过。8. **chore: W1 工程骨架落地（2026-09-13）**——按 09 文档建 `src/atlas/{engine,harness,graph,nodes,memory,skills,collaboration,api,web}/` + `tests/` 包树（`__init__.py` 占位）；`pyproject.toml` 锁定 10 文档 §2 Demo 依赖（Python >=3.11，src layout，版本下限取当日 PyPI 稳定版），已用 Python 3.11 venv 验证可编辑安装；09 文档同步记录 `api/` 包位决策（FastAPI 入口，待定项 5）。9. **docs: 技术选型 T1-T5 收口（2026-09-13）**——10 文档 §3 待决策项全部转为正式 ADR（LiteLLM+商业 API / 评估层仅留接口 / 前端进骨架 / 进程内事件总线替代 NATS / FastAPI 实现网关接口），§4 补变更记录；02 选型总览、09 待定项与目录树（含 `frontend/`）、08 §7.2 决策注记三处同步；NATS/Go 网关缓做条目已在 14 文档 D5/D6。10. **docs: 移除 LICENSE（2026-09-13，用户指示）**——MIT 许可证不需要，LICENSE 文件删除，README/CONTRIBUTING 的 License 段落改为"暂无（未定）"，handoff 元文档索引与 CHANGELOG 同步清理。11. **docs: 补齐 agent-world 惯例规范文档（2026-09-13）**——根目录：`CONTRIBUTING.md`（贡献指南，Python/09 骨架适配）、`MIGRATION_CONVENTION.md`（DB 迁移规范，PostgreSQL 版）、`BENCHMARK.md`（性能基准记录表）、`.gitleaks.toml`（密钥扫描）、`.env.example`（环境变量模板，按 10 文档 Demo 栈）；docs 体系：`14-缓做事项登记表`、`15-环境与分支策略`、`16-反馈工作流`。索引已同步 00 文档地图与 README。12. **chore/docs: 仓库初始化并推送 GitHub private（2026-09-13）**——`git init -b main`；3 个原子提交：`6fc7728`（chore .gitignore）、`d3c63c6`（docs 规格文档 00-13，commit 原文为 atlas-docs）、`2413ab5`（docs 根元文档）；`gh repo create atlas --private` 创建并推送至 `https://github.com/bayernjf/atlas`（PRIVATE，默认分支 main）。13. **docs: 源文档切分完成并核对（2026-09-13）**——两份原始文档（《产品与总体方案.md》《技术实现设计.md》）按主题切分为 `docs/` 00-13 共 14 份 AI 导向文档；274 个章节标题全部映射、逐段覆盖率机器核对通过；源文档已删除（血统与治理见 00 文档）。14. **docs: 切分核对报告（2026-09-12）**——段落级最长公共子串覆盖率验证，无信息丢失。
## Quality gate（质量门）

- **文档侧**：00 文档"切分核对报告"已确认 274 章节全映射、逐段覆盖无丢失。改规格文档必须遵循 00 文档治理规则（唯一事实源 / 改内容流程 / Schema 契约防漂移）。
- **代码侧**：最小质量门已建立——GitHub Actions CI（`.github/workflows/ci.yml`，2026-09-14 落地；push main/dev 与 PR 触发）：gitleaks 全历史扫描 + 后端 pytest + 前端 oxlint/vitest/build；**PR #3 与合并后 main 均已实跑全绿**（gitleaks 初版 403 经补 `pull-requests: read` 修复）。本地后端 `.venv/bin/pytest`（当前 127 个单元用例：engine 3 + harness 契约 7 + 三层定位 6 + graph conditions 9 + graph DSL 43 + 编译器/condition/loop/parallel/wait 路由 17 + graphs API 4 + 决策 6 + 店铺 8 + NL 6 + 退款端到端 8 + Demo API 10；DB/浏览器/店铺 8 个 integration 用例默认跳过），全量 `ATLAS_RUN_INTEGRATION=1`（+`DATABASE_URL`）含真实 Chromium 与店铺平台集成；前端 `cd frontend && pnpm test`（vitest，39 个单元用例，5 个文件）与 `pnpm build`（TS 检查 + Vite 构建）均通过（有 AntD 首包 >500kB 的体积提示，暂不阻塞）。后续按 13 测试用例清单分层：单元 → 集成 → 回放 → 端到端 → AI 评估，上线前过"四道门"。

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
