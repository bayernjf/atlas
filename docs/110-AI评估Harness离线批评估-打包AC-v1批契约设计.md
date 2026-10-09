# docs/110 · AI 评估 Harness v1（离线批评估）批契约（打包 AC）

> **元信息**
> - 日期：2026-10-09
> - 承接：docs/107 §五 B 档第 3 项（「AI 评估 Harness 独立实现」，`evaluation_task` 契约已存在于 docs/06 §9.2，无独立实现；docs/12 §7 检查表「评估指标三元组」未打勾）
> - 立项依据：用户「那你搞」批复（docs/107 §六 次优）；B 档三项实测探查后，本项为**唯一零拍板、零外部资源、不涉安全语义**的可闭环项（D20 号错引见 §四勘误、D35 机制缺失见 §四）
> - docs/14 新 D 号：**D62**（本条不解除：影子评估、在线评估、评估任务调度、决策质量 LLM 评分、租户配额仍缓做）
> - 形状权威：本文件；docs/14 D62 行注记；docs/13 打包 AC 小节
> - 验收：U1278 起

## 一、范围（MVP 切片）

**背景**：docs/06 §9.2 定义了 `evaluation_task` 形状（task_id/description/test_cases[{input, expected{action, verify}}]/metrics 三元组 task_success_rate、average_steps、decision_accuracy），docs/12 §7 检查表「评估指标三元组（06 9.2 metrics）」至今未勾——评估能力只有契约没有实现。docs/09 §9.3「上线前强制测试」的黄金测试集回归（至少 5 个用例）正是离线批评估的直接消费者：本批让「黄金测试集回归」从口头要求变成可执行动作。

**形态决策（待点工①带推荐）**：v1 取**离线批评估**——对既有图（graph_id 指定）批量跑 `run_graph`，用确定性决策客户端与 verify 表达式断言，产出 metrics。**影子评估**（对真实生产流量旁路评分）依赖真 LLM 部署与决策质量评分，留待 B 档真实化后评估；形态不入 v1。

**本批取回**：
1. **evaluation_task 落码模型**：docs/06 §9.2 形状正式化（`src/atlas/evaluation/models.py`）：`EvaluationTask{task_id, description, test_cases[], metrics[]}`、`TestCase{name, inputs, expected{action?, verify?}}`、`EvaluationResult{case 级：passed/decision_matched/steps/error?}`、`EvaluationSummary{逐指标值}`
2. **离线批评估 runner**（`src/atlas/evaluation/runner.py`）：`run_task(graph_store, task, *, decision_client=None)`——对每个 test_case：`inputs` 注入 → `run_graph`（复用 loader.compile_graph/run_graph 全链路，含 subgraph 解析；决策客户端 v1 默认 `get_decision_client` 现网形态，可显式注入确定性客户端做无模型回归）→ 从 frames/trace 收集**决策动作** → `expected.action` 匹配 → `expected.verify` 表达式断言 → 汇总 metrics
3. **决策动作对齐规则**（契约层定义）：`expected.action` 匹配两类语义动作——① ai_decision 节点决策结论（`RuleBasedDecisionClient`/`LiteLLMDecisionClient` 产出的 `action` 字段，rule 命中或 LLM 结构化输出；决策帧 `kind="decision"` 携带）；② human_approval 节点行为（`request_human_approval`＝挂起、`approve`＝放行，审批帧/放行帧携带）。未声明 `expected.action` 的 case 只跑 verify，不参与 decision_accuracy 分子。
4. **verify 表达式**：复用既有**白名单表达式求值**（D15 条件表达式函数库 `condition` 包：算术/日期/比较，`safe_eval` 路径），对 run 终态全局变量取值断言；表达式非法 → case 级 error（422 由端点统一拦截，运行期非法 verify 记 case error 不使整批失败）
5. **REST**：`POST /api/evaluations`（admin：body `{graph_id, task}`，同步跑批，返回 `{task_id, summary, cases[]}`；图不存在/编译失败 404/422）＋`GET /api/evaluations`（admin：历史列表，v1 **PG 持久化**——`evaluations` 表两档 store（进程内/pg_evaluation_store），迁移 **047**；评估结果属审计价值数据，不留进程内裸奔）
6. **错误码**：`EVALUATION_TASK_INVALID`（task 形状校验 422）、`EVALUATION_GRAPH_NOT_FOUND`（404）、`EVALUATION_VERIFY_INVALID`（verify 表达式非法，422 于端点、运行期 case error）——照 MESSAGE_TEMPLATE_* 惯例：契约层识别、中文 detail，不进 docs/17 文案目录

**非目标（D62 不解除）**：影子评估（真流量旁路评分）、在线评估端点、评估任务调度/定时、决策质量 LLM 评分、租户配额、评估报告导出、前端评估页（v1 纯后端，前端零改动）。

## 二、契约形状

### 2.1 数据模型

```
EvaluationTask:
  task_id: str            # 必填，1-64 字符
  description: str | None
  test_cases: TestCase[]  # 必填，≥1 条；>200 条 422
  metrics: str[]          # 枚举子集：task_success_rate / average_steps / decision_accuracy（未知项 422）

TestCase:
  name: str | None        # 缺省 auto:case-<n>
  inputs: dict            # run_graph inputs 注入
  expected: {action?: str, verify?: str} | None   # 至少声明一项，否则 422

EvaluationCaseResult:
  name: str
  passed: bool            # verify 通过（未声明 verify 恒 true）
  decision_matched: bool | None   # 声明 action 时：匹配/不匹配；未声明 None
  steps: int              # 执行步数（frames 计数）
  error: str | None       # case 级运行异常/verify 非法

EvaluationSummary:
  task_success_rate: float   # verify 全过 cases / 总 cases（0..1）
  average_steps: float       # 平均步数（含 error case 记实际步数）
  decision_accuracy: float | None   # action 匹配 / 声明 action 的 cases（分母 0 → null）
```

### 2.2 决策帧对齐

runner 从 `emit` 帧流收集：`kind="decision"` 帧的 `decision.action`（ai_decision 产出）与审批相关帧的 `approval_requested`/`approval_resolved` 动作。对齐为语义动作名后与 `expected.action` 精确字符串匹配；**首个匹配即胜**（同 run 多次决策取首次命中该 case 的 action）。

### 2.3 verify 求值

`expected.verify` 为白名单表达式（复用 `condition` 包安全求值），上下文＝run 终态 `global` 变量；比较式如 `refund_status == 'completed'`。取值路径缺失 → False（不抛）。

## 三、验收（U1278 起）

- U1278 模型校验：task 形状/枚举/边界（≥1 case、≤200、metrics 枚举、expected 至少一项）
- U1279 runner：确定性决策客户端下 action 匹配（命中/不命中/未声明三分支）
- U1280 runner：verify 断言（真/假/表达式非法三态）+ 终态变量取值
- U1281 metrics：task_success_rate/decision_accuracy（含分母 0 → null）/average_steps 计算
- U1282 端点：POST 200/404（图不存在）/422（形状）/403（非 admin）；GET 列表分页
- U1283 PG 持久化：迁移 047 + 两档 store 写读 + reset 清空
- U1284 黄金用例端到端：内置模板（如 refund-auto）跑 5 例任务，summary 数值手算对账

## 四、docs/107 B 档实测勘误（本批立项时核实，报告原文不改）

1. **D20 号错引**：docs/107 §五 B 档第 4 项「D20 节点/工具/变量级权限」——docs/14 第 28 行 D20 实为**审批域**（human_approval 持久化中断-恢复等，2026-09-17 M5 已设计）。「变量级权限」真实对应 **D30**（变量作用域 v2，docs/14 标注"涉安全语义，属契约级变更"）；「节点/工具级权限」为**无 D 号新权限模型**（安全语义契约级）。B 档第 4 项需拍权限模型后方可立项，本批不触碰。
2. **D35 机制缺失**：docs/107 §五 B 档第 5 项「记忆配置 UI（memory_config 策略表单）」——实测 `memory_config/memoryConfig` 在 src/atlas/ 与 frontend/src/ **零命中**；可配置机制（auto_extract、决策节点隐式记忆注入、decay/retention）均不存在，docs/12 §7 已标"愿景，M11 不实现、缓做 D35"。直接落"策略表单"＝无机制的空表单，违背"不写假集成"纪律；**D35 保持缓做**（先落机制或 D29 第六类实体前置满足后再评估）。
3. **记忆浏览器已闭环**：docs/107 §五 行 80「记忆配置 UI（memory_config 策略表单、记忆浏览器）」——「记忆浏览器」半件已由打包 AA（记忆/知识双标签 + 导入 + 编辑 + 删除）与既有记忆 CRUD（docs/28 批 4⑩）闭环；余缺仅为策略表单。

## 五、门与提交

- 后端全量 pytest（基线 2500/171）＋守护门 8 passed；前端零改动（不跑前端门，收口注记说明）
- 原子链：docs(plan) → feat(api) → test(api) → docs(close)，英文 message、无 AI co-author、不 push（用户偏好）

## 六、待点工（带推荐，落码前无需用户确认）

1. **评估形态**：v1 离线批评估（推荐，本批取回）；影子评估待真 LLM 部署
2. **verify 求值器**：复用 condition 白名单表达式（推荐）；不引入新表达式语言
3. **PG 持久化**：迁移 047 + 两档 store（推荐，评估结果有审计价值）；进程内 v1 备选
