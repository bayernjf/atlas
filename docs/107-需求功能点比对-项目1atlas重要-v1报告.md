# docs/107 · 需求功能点比对报告（项目1-atlas-重要）

> **元信息**
> - 日期：2026-10-09
> - 比对对象：`/Users/jiangfeng/Downloads/项目1-atlas-重要/`（4 份设计文档 + 3 张微信参考图）
> - 比对基准：atlas 仓库 dev 分支（HEAD `736dd597`，Active work #1–#135 全 ✅，工作树干净）
> - 方法：逐文档提取功能点 → 与代码结构（`src/atlas/` 35 包、`frontend/src/lib/` 30 模块）、handoff、docs/14 缓做登记表、docs/08 候选池逐项核对 → 完成度按需求条目占比实证
> - 状态：报告落档；推进任务见 §六

---

## 一、需求材料清单

| 材料 | 性质 | 角色 |
| --- | --- | --- |
| 产品概念 .docx | 愿景/架构层：Harness+Loop+Graph 三位一体、三层四环、全场景图谱、DIY 输出机制、路线图 | 方向基准（远期） |
| 技术方案.docx | 实现层：Demo 范围（4.1–4.3）、节点 Schema、适配器 Schema、变量系统、Loop/记忆/Harness 设计、里程碑、**Demo 验收标准（§7.3）**、迭代路线图 Phase 1–4、测试/安全清单 | **验收基准（本次比对主尺）** |
| 编辑后台组件设计.docx | 产品化蓝图：节点 12 类、适配器分层、逻辑组件、变量/知识、五项核心（技能/记忆/协同/部署/交互模板）、通用化标准、补充细节 28 项 | 产品化/平台化蓝图 |
| 递归自动化引擎设计.docx | Meta Layer：平台探索器/行为挖掘器/诊断修复器/元运营体/自我进化 | 远期路线图 |
| 微信图片 3 张 | Harloops 协作平台、Agent Team 团队管理、8 类 Agent 分工表 | 参考产品/愿景，非验收项 |

---

## 二、比对基准（atlas 现状）

- **代码**：`src/atlas/` 35 包（engine/graph/nodes/harness/web/httpapi/database/message/shop/llm/memory/recording/debug/monitoring/iam/template/versioning/routing/cards/skills/a2a/collaboration/connections/mcp/openapi/reflection/security/storage/scheduling/tracing/logistics/coordination/observability/channels）；`frontend/src/lib/` 30 模块 + locales（zh/en）
- **门基线**（2026-10-08 ZX 实跑）：后端 2481 passed/169 skipped；前端 vitest 878/2；oxlint 0/0；tsc 0；build 过
- **产品判定**：A 档（可演示/可陪同试用）✅；B 档（生产 MVP）❌，差三条真实外部资源（docs/73）

---

## 三、逐文档比对结果

### 3.1 技术方案.docx —— ✅ Demo 范围 100% 达成（超出）

| 需求项 | Atlas 现状 | 状态 |
| --- | --- | --- |
| 4.3 可视化流程编辑器（拖拽/连线/配置） | frontend Editor.tsx + React Flow | ✅ |
| 4.3 节点：触发器/工具调用/AI 决策 | 实际 9 种（+condition/loop/parallel/wait/subgraph/human_approval） | ✅ 超出 |
| 4.3 Harness 适配器：Web 浏览器操作 | web/adapter.py 三层定位（选择器→视觉语义→LLM 全图） | ✅ |
| 4.3 自然语言生成流程（简单场景） | llm/nl_generate.py | ✅ |
| 4.3 基础执行引擎（LangGraph） | graph/loader.py + engine | ✅ |
| 4.3 运行日志与单步调试 | debug/（断点/step/continue/条件断点/快照） | ✅ |
| 5.2 节点 Schema（id/type/config/inputs/outputs/retry/timeout/on_error/breakpoint） | graph/dsl.py 契约 | ✅ |
| 5.3 节点类型清单（trigger/ai_decision/tool_call/condition） | 全部存在 | ✅ |
| 5.4 适配器 Schema（auth_type/capabilities/input/output schema/permission/is_idempotent） | connections/ + harness/registry | ✅ |
| 5.5 变量系统（全局/会话/节点/秘密/环境 + {{路径}}） | variables.ts + graph/interpolation.py + secrets 信封 | ✅ |
| 5.6 适配方式：模板/自然语言/拖拽/操作录制/API 导入 | 模板库 + NL + 画布 + recording/ + openapi/ | ✅ 五种全有 |
| 6.1 Loop 主循环（OODA + Reflect） | engine/ + reflection/（L2 单运营体反思已收口） | ✅ |
| 6.2 决策节点（记忆检索/思考缓冲/结构化输出/置信度） | llm/decision.py（结构化输出；置信度走 condition） | ✅ 基本 |
| 6.3 记忆系统（短期/中期摘要/长期事实/案例） | M11 最小可用长期记忆已落码（fact+preference）；摘要/案例缓做 | ⚡ 部分 |
| 6.4 Harness Gateway（ListCapabilities/Execute/Observe） | harness/base.py + registry.py + runtime.py | ✅ |
| 6.5 Web 三层定位 | web/adapter.py | ✅ |
| 7.3 七条验收标准 | 2026-09-13 起逐条实跑通过（handoff Current state） | ✅ |
| 8 Phase 2：新增节点（条件/循环/并行/等待/子图/人机协作） | 全部存在 | ✅ |
| 8 Phase 2：新增适配器（API/数据库/消息） | httpapi/ + database/ + message/ | ✅ |
| 8 Phase 2：模板库（5-10 预置） | template/ 5 内置 + 用户自建（打包 X）+ 分类/URL 导入（打包 ZM） | ✅ |
| 8 Phase 2：操作录制 | recording/（录制/回放/批量回放/参数化向导） | ✅ |
| 8 Phase 2：单步调试与断点 | debug/ | ✅ |
| 8 Phase 2：基础监控告警 | monitoring/（四规则/静默/升级/值班） | ✅ |
| 8 Phase 2：多租户权限 | iam/（RBAC/租户分区/登录/改密/首登强制改密） | ✅ |
| 8 Phase 3：A/B 测试与灰度发布 | routing/ 灰度分桶 + gate 自动回滚（灰度有，A/B 无） | ⚡ 半边 |
| 8 Phase 3：完整审计追踪 | observability/tracing + 审计过滤分页 | ⚡ 基本 |
| 9.1 测试层次（单元/集成/回放/E2E/AI 评估） | pytest + 回放 + 冒烟；AI 评估 Harness 仅文档契约（docs/06 §9.2） | ⚡ 部分 |
| 9.3 上线前强制测试（Schema/黄金集/沙盒/安全审查） | 发布门禁（gate）+ 黄金回放 + 安全准入（docs/32） | ✅ 基本 |
| 10 安全清单（RBAC/阈值/二次确认/脱敏/密钥） | security/ + human_approval + redact + secrets；Guardrails/Presidio/Vault 未引入 | ⚡ 基本 |

**4.3「不包含」清单现状**：复杂循环/并行 ✅ 已做；多租户权限 ✅ 已做；模板市场 ⚡ 半做（模板库+自建已做，市场未做）；反思进化 ⚡ L2 已做、L3 离线训练未做；移动/桌面适配器 ❌（D1 缓做）。

### 3.2 编辑后台组件设计.docx —— ⚡ 约 55%

**已完成**：9 种核心节点、表达式引擎（白名单函数库）、条件分支（表达式/多条件/LLM/默认分支）、循环（遍历/条件/break/continue/循环变量）、并行合并（all_success/any_success）、on_error（stop/continue/jump_to）、变量 6 作用域、{{}} 引用、断点续跑（storage/PG）、数据脱敏（redact）、灰度+自动回滚、语义版本+草稿发布分离、子图 @N 钉版+手动升级（ZU2）、审批卡片三渠道渲染（cards + ZS 用户自建）、录制回放+批量回放（recording + D26 报告）、影子模式（shadow.ts + PG 化）、Mock 工具响应、单步调试+变量快照、配置 diff、多语言（i18n + 节点目录双语）、技能基础（skills/）、智能体协同基础（a2a/collaboration）、部署（手动/Webhook/定时，scheduling）。

**未完成**：
- **知识组件整块缺失**（§7）：FAQ/SOP/产品手册/业务规则/历史案例向量库、文档导入向量化、知识更新——**无独立 D 号**（D35 是"记忆"，与"知识"边界不同，见 §2.5）
- 三个节点类型（§3.1）：意图识别、信息抽取、内容生成——**无 D 号**
- 移动端/桌面端/IoT 适配器（D1）；NL 生成适配器配置（LLM 生成适配器定义）；开发者 SDK
- 状态机子图、全局异常处理器、自愈机制（诊断子图）
- 技能库/版本管理/推荐（技能 v1 只有基础封装）
- 记忆配置 UI（memory_config 策略表单、记忆浏览器）——D35 余部
- 复杂协同拓扑/冲突仲裁/协同调试
- 部署方式：IM 嵌入/网页嵌入/SDK/私有化/边缘
- 交互模板编辑器（完整 WYSIWYG）+ 模板市场
- 组件市场、跨租户共享（D3/D25 余部）
- 画布虚拟化/聚合节点、一键修复建议、节点/工具 AI 推荐、多人协作编辑、移动端管理、冷启动预热、租户配额限流、完整审计导出

### 3.3 产品概念.docx —— 愿景层

核心架构（Harness+Loop+Graph 三位一体、三层四环）已在 Demo 落地；评估与优化层（评估 Harness/策略训练器/模型路由器）仅留接口，属 Phase 3/4。

### 3.4 递归自动化引擎设计.docx —— ❌ 0% 未启动

平台探索器（Adapter Auto-Builder）、行为挖掘器（Agent Auto-Generator）、诊断修复器（Self-Healing）、元运营体（Meta-Agent）、自我进化（Platform Evolution）五模块均未立项，属远期路线图（文档自身 Phase 1 即要求探索器 MVP + 行为挖掘简单版 + 基础诊断修复）。

### 3.5 微信图片 3 张 —— 参考产品，非验收项

Harloops（任务对话/模型选择）、Agent Team（8 角色团队管理：执行日记/Token 消耗/对话明细）、8 类 Agent 分工表——竞品/愿景参考。其中"多智能体团队管理界面"形态 atlas 无对应，但底层 monitoring/运行记录数据已有，可作产品方向候选。

---

## 四、完成度矩阵（8 功能域）

| # | 功能域 | 完成度 | 状态 | 主要缺口 |
| --- | --- | --- | --- | --- |
| 1 | Demo 范围（技术方案） | 100% | ✅ | 无（验收基准，全部达成且超出） |
| 2 | 节点与逻辑组件 | ≈85% | ⚡ | 意图识别/信息抽取/内容生成节点、状态机子图、全局异常+自愈 |
| 3 | 工具与适配器 | ≈60% | ⚡ | 移动/桌面/IoT 适配器、NL 生成适配器、开发者 SDK、健康心跳 |
| 4 | 数据·变量·知识 | ≈50% | ⚡ | **知识库/RAG 整块**、上下文类型化、文件/富文本变量 |
| 5 | 测试·发布·版本 | ≈90% | ✅ | AI 评估 Harness 独立实现、冷启动预热、租户配额 |
| 6 | 安全与权限 | ≈75% | ⚡ | 节点/工具/变量级权限（D20）、完整审计导出、Guardrails/Presidio/Vault |
| 7 | 五项核心（技能/记忆/协同/部署/交互模板） | ≈45% | ⚡ | 技能库/推荐/市场、记忆配置 UI、复杂协同/仲裁、IM/网页/SDK 嵌入、模板编辑器+市场 |
| 8 | 递归自动化引擎（Meta Layer） | 0% | ❌ | 五模块全未启动（远期） |

---

## 五、缺口清单（按价值排序）

### A 档：需求文档明确、工程内可闭环（新候选，无既有 D 号）

1. **知识库/RAG**（编辑后台组件设计 §7 整块）：FAQ/SOP/产品手册/业务规则/历史案例 + 文档导入向量化 + 知识更新。与 D35（记忆）边界不同（知识＝用户主动配置、可跨运营体共享；记忆＝自动积累）。缓做表无对应 D 号，**需登记新 D + 契约立项**。
2. **三个节点类型**（§3.1）：意图识别（意图列表/置信度阈值/槽位）、信息抽取（抽取字段 Schema）、内容生成（模板/风格/长度）。与 ai_decision 同族（LLM 结构化输出），工程内可闭环，**需契约立项**。

### B 档：工程内可闭环但有依赖/需拍板

3. **AI 评估 Harness 独立实现**：`evaluation_task` 契约已存在于 docs/06 §9.2，无独立实现（无 D 号）——需定义"评估 Harness"落地形态（影子评估/离线批评估）再立项。
   > **〔2026-10-09 打包 AC 立项注记〕**：本项已立项（docs/110，D62），形态取**离线批评估**（影子评估待真 LLM 部署）；docs/12 §7「评估指标三元组」已勾。**同节勘误**：第 4 项「D20 节点/工具/变量级权限」D 号错引——docs/14 D20 实为审批域（human_approval 持久化中断-恢复），「变量级权限」对应 D30（涉安全语义契约级）、「节点/工具级权限」为无 D 号新权限模型，均需拍权限模型后立项；第 5 项「记忆配置 UI」实测 `memory_config` 代码零命中、可配置机制（auto_extract/注入开关/decay）不存在（docs/12 §7 原文「愿景，M11 不实现、缓做 D35」），直接落表单＝无机制空表单，D35 保持缓做。
4. **D20 节点/工具/变量级权限**：需求 §补充 5（组件级权限控制），docs/14 已登记缓做，触发＝组件级权限真实需求。
5. **记忆配置 UI**（memory_config 策略表单 + AI 节点记忆注入开关）：D35 余部，依赖运营体级配置表单入册（D29 第六类实体）。

### C 档：外部触发 / Phase 3-4

6. 移动端适配器（D1）、组件市场（D3）、模型路由器（D4）、策略离线训练（D2）、NATS（D5）、Go Harness（D6）——触发条件外部化
7. 递归自动化引擎五模块——远期路线图
8. B 档三缺（真 Shopify/SMTP/域名+TLS）——真实外部资源

---

## 六、推进建议

- **第一优先**：A 档两项（知识库/RAG、三节点）是需求文档明确点名、工程内可闭环、无既有 D 号的真实空白——建议各立 docs-only 契约批，形状权威照 docs/106 惯例。
- **次优**：B 档 AI 评估 Harness（需先定评估形态）。
- **外部线**：真凭据/真客户/多实例照 docs/73 推进，与工程内候选互不阻塞。

---

## 七、留痕注记

- 本报告为比对台账，非契约/非立项文档；A/B 档候选入 docs/14 缓做登记表与 docs/08 候选池须另行立项动作。
- 完成度百分比为按需求条目实证占比的近似值，非精确计量；逐项证据见 §三各表。
- 3 张微信图为参考产品，不作为验收项；若"多智能体团队管理界面"成为产品方向，需另行立项。
