# condition LLM 分支：置信度阈值＋上下文字段级脱敏 v1 批契约设计（打包 AE）

> 状态：📝 立项（docs-only，落码另立批）｜号段 U1285–U1292｜形状权威：本文｜承接 docs/14 D14 剩余「置信度阈值、字段级脱敏」｜2026-10-09

## 1. 背景与边界裁定

**D14（condition 节点 LLM 语义分支）已取回**：单次 LiteLLM 分类选唯一标签（docs/48）、回放脚本化（docs/83 打包 V）、节点级 `model` 覆盖＋`response_format=json_object`（打包 ZP）、模型按租户可选 BYOK/内置（docs/93 打包 Y）。

**本批取回两个剩余半边**（均为工程内可闭环、零外部资源）：

1. **字段级脱敏（上下文出向）**——现状实测：`_execute_llm_condition`（loader.py:1872-1876）构造 `context_text` 时直接 `json.dumps({"global": context.global, "nodes": ...})`，**未走 `redact_sensitive`**。打包 A3（docs/99）的脱敏只覆盖 run 结果投影（`_redact_outputs`）、录制、观察、日志通道，**管不到"发给 condition LLM 的上下文"**。于是 `source∈{env,secret}` 变量的展开值若出现在节点输出/global 中，会**明文出向给 LLM 供应商**——这是一条脱敏漏网通道。
2. **置信度阈值**——现状：classifier 只解析 `payload["branch"]`，模型不返回也不消费置信度；docs/06 §6.2 与 docs/14 D14 均列「置信度低于阈值应 fail-safe」。condition 节点的 fail-safe 设计本就是「路由 `defaultTarget`」，故低置信度的正确动作＝**抛 `ConditionClassifyError` → loader 现有 catch 走 `__default__`/`defaultTarget` 并记 `llm_errors`**（不引入人工挂起——condition 节点无审批 broker 配置，与 ai_decision 的阈值挂起语义不同）。

**范围裁定**：
- **取**：condition 上下文脱敏（复用 `redact_sensitive`＋从 `state` 构造 mapping）；LLM 返回 `confidence`，节点可选 `confidenceThreshold`，低置信度 fail-safe。
- **不取（仍缓做，触发条件不变）**：多候选/澄清重问、每分支独立 prompt 或多次调用、门禁 LLM 抖动（发布门禁）、`x-secret-allowed` 节点参数级权限面（docs/99 §5，与 D20 同触发）。

## 2. 后端改动

### 2.1 上下文脱敏

- 新增 helper `_sensitive_redaction_mapping(state: GraphState) -> dict[str, str]`：从 `state["sensitive"]`（`{变量名:(source,ref)}`）与 `state["variables"]["global"]`（`{变量名:展开值}`）构造 `{展开值: "<redacted:{source}:{ref}>"}`，逻辑与 `_redact_outputs`（loader.py:430-434）**同源抽出**，`_redact_outputs` 改为调用本 helper（"同一件事只有一个实现"）。
- `_execute_llm_condition` 构造 `context_text` 时：先取 `mapping = _sensitive_redaction_mapping(state)`，对 `{"global": context.get("global",{}), "nodes": _node_outputs_projection(context)}` 走 `redact_sensitive(payload, mapping)` **再** `json.dumps`。mapping 为空时零行为变化。
- 截断（`_CONTEXT_LIMIT`）在脱敏之后进行，不影响。

### 2.2 置信度阈值

- `ConditionClassifier.classify` Protocol 与三个实现（`Offline`/`LiteLLM`/`Scripted`）统一加关键字参数 `confidence_threshold: float | None = None`。
- **`LiteLLMConditionClassifier`**：
  - `_SYSTEM_PROMPT` 改为要求输出 `{"branch": "<标签>", "confidence": <0.0–1.0 数值>}`，并说明 confidence 是对所选分支把握程度。
  - 解析：`label = payload["branch"]`；`raw_conf = payload.get("confidence")`。
    - `confidence_threshold` 为 None（未启用）→ 不判定 confidence，行为同今天（向后兼容）。
    - `confidence_threshold` 非 None：`raw_conf` 缺失/非数值/越界 → **抛 `ConditionClassifyError`（fail-closed）**（配了阈值就要求模型给出合法置信度）；`float(raw_conf) < confidence_threshold` → 抛 `ConditionClassifyError(f"置信度 {c} 低于阈值 {t}")`；≥ 阈值 → 正常返回 label。
  - 边界：严格小于（与 decision.py 阈值一致）。
- **`ScriptedConditionClassifier`**（回放）：接收并**忽略** `confidence_threshold`——回放标签是录制事实，不因阈值改写（黄金回放/CI 稳定性）。
- **`OfflineConditionClassifier`**：加参数（签名一致），本就抛 `ConditionClassifyError`。
- **loader**：`_execute_llm_condition` 从 `config.get("confidenceThreshold")` 读取：仅当为数值且 `0.0 ≤ v ≤ 1.0` 时作为阈值传入；缺失 → None；类型错/越界 → 忽略（不阻断运行，不写假值）。

## 3. 前端改动

- `condition.schema.ts`（LLM 分支 config）纯超集加可选 `confidenceThreshold?: number`（0–1）。
- `ConditionConfig.tsx`（LLM 模式区）加一个可选 number 输入「置信度阈值（低于则走默认分支）」，留空＝不启用；保存进 config，清空回 undefined。
- i18n：`condition.confidenceThreshold*` zh/en 键（labels/hints），en 零汉字。
- 后端 DSL 编译：`confidenceThreshold` 若给出须为 0–1 数值（越界/非数值 → 422，复用现有 config 校验通道；具体码在落码时按既有 condition config 校验族登记，不新增错误码除非确无合适码）。

## 4. 验收用例

- **U1285（后端）**：脱敏 helper 与 `_redact_outputs` 同源；含 secret/env 展开值的 global 与节点输出，在传给 classifier 的 `context_text` 中为 `<redacted:secret:NAME>`/`<redacted:env:NAME>`，**无明文**（用捕获 `context_text` 的测试 classifier 断言；用"≠手写字面量的独特展开值"，避 docs/99 §7②陷阱）。
- **U1286（后端）**：`confidence ≥ threshold` → 正常选对应分支、target 正确。
- **U1287（后端）**：`confidence < threshold` → fail-safe 走 `defaultTarget`，`llm_errors` 含低置信度记录。
- **U1288（后端）**：配了阈值但模型未返 confidence / 返回非数值 / 越界 → fail-safe 走 defaultTarget（fail-closed）。
- **U1289（后端）**：节点未配 `confidenceThreshold` → 模型返回低 confidence 也照常选分支（向后兼容）。
- **U1290（后端）**：`ScriptedConditionClassifier` 回放即使 config 带阈值也原样返回录制标签，零 LLM 调用。
- **U1291（后端）**：config `confidenceThreshold` 类型错/越界在运行期被忽略（不阻断）；DSL 编译期越界 → 422。
- **U1292（前端）**：ConditionConfig LLM 模式下阈值字段渲染/留空/填值保存/清空；schema 类型面；i18n zh/en 键完整、en 零汉字。

## 5. 非目标与复开条件

- 多候选返回/澄清重问、每分支独立 prompt 或多次调用：触发＝规则表达式与单标签 LLM 都无法覆盖的真实分流场景。
- 发布门禁 LLM 抖动：触发＝发布流接入真实质量门（与 M9 灰度门控同族）。
- `x-secret-allowed` 节点参数级权限、secret 轮换/KMS：触发＝真实细粒度授权需求（D20 节点级角色同批）。
- condition 低置信度转人工挂起（本批只走 defaultTarget）：若产品上要求"拿不准就找人"，须先给 condition 节点引入审批配置，属更大契约变更，另立项。

## 6. 原子提交序（落码批）

1. `refactor(engine)`：抽 `_sensitive_redaction_mapping`，`_redact_outputs` 复用（纯重构，测试不变）。
2. `feat(engine)`：condition context_text 脱敏（U1285）。
3. `feat(llm)`：classifier 三实现加 `confidence_threshold`，LiteLLM 解析/判定，loader 读取（U1286–U1291）。
4. `test(engine)`：后端新增用例（U1285–U1291）。
5. `feat(frontend)`：schema/ConditionConfig/i18n（U1292）。
6. `test(frontend)`：前端用例（U1292）。
7. `docs`：docs/14 D14 注记、docs/13 U1285–U1292、docs/08、CHANGELOG、handoff 收口（含 docs/14 D30 行落码收口注记补登——打包 A3 早已落码、行注记漏回填）。

零新依赖／零迁移／无新 ADR（纯既有机制内闭环）；D14 整体不解除（多候选/每分支 prompt/门禁抖动仍缓做）。
