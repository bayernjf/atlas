# 图变量受限来源（env/secret）与字段级可见 v1 批契约设计

> 状态：📝 立项（docs-only，落码另立批）｜号段 U1196–U1200｜形状权威：本文｜承接 docs/14 D30 剩余「secret/env/session 受限来源与字段级可见」｜2026-10-07

## 1. 背景与边界裁定

**D30 剩余**（已取回：outputSchema 类型级校验 REF_TYPE_MISMATCH、变量数据依赖环 GRAPH_DATA_CYCLE、parallel 动态路径、subgraph 深层展开、改名 quickFix 全量联动）。本条＝**受限来源与字段级可见**。

**现状实测**：
- 连接面：`secret://<name>` 引用与 `enc$v1$...` 信封已在适配器连接面由 SecretProvider 解析（docs/32 §2，ADR T26：`ATLAS_SECRETS`/`ATLAS_MASTER_KEY`、AES-256-GCM，明文不落 last_request/observe/录制/日志，缺失 → `SECRET_UNAVAILABLE` 且零外呼）。
- 图变量面：`loader._seed_variables(graph)`（loader.py:350）只产出 `{"global": {variable.name: variable.value}}` 字面量；run inputs overrides 合并（loader.py:350-405）。**变量无法声明来自 env/secret**——需要密文参与图的运营只能把明文塞进 `graph.variables`（落库明文、进列表投影、进录制）。
- 节点 schema 预留 `x-secret-allowed`（M1 契约 docs/19）但**代码零命中**（grep `x-secret-allowed|secret_allowed` 于 `src/atlas` 无结果）——本批**不消费该扩展键**（它约束「节点参数可否引用 secret 来源」，属更深一层的字段级权限面，见 §5 非目标），只做变量来源声明＋投影脱敏。

**范围收敛**：原文「secret/env/session 受限来源与字段级可见」——**「env」取**（os.environ 只读注入）；**「secret」取**（复用 docs/32 SecretProvider，不新造密钥面）；**「session」不取**（无会话变量机制，触发＝11 文档 S1 持久化就绪，D20 同触发）；**字段级可见取**（敏感来源变量展开值不落投影/录制/观察，脱敏占位）。

## 2. 变量来源声明与运行期解析

### 2.1 声明形状（GraphDSL.variables 纯超集）

`graph.variables[]` 项现为 `{name, type, value, scope}`——**新增可选字段 `source: "env" | "secret"`**（缺省＝无，照旧字面量语义）。`source` 存在时：
- `value` 语义变为**引用名**：`source="env"` 时 `value` 为环境变量名（≤128）；`source="secret"` 时 `value` 为 `secret://<name>` 或裸 `<name>`（≤128，解析时归一为 SecretProvider 引用）。
- 前端变量面板：来源徽标（`普通/Env/Secret`），值区只编辑引用名、不显示明文（Secret 型值输入框显示 `••••` 占位＋"引用 secret://name"提示）。

### 2.2 DSL 校验（dsl.py，编译期 422，模板不变）

- `source` 非法值（非 `env`/`secret`）→ 422 `VAR_SOURCE_INVALID`（params `{source}`）；
- `source` 非空时 `value` 为空/超长 → 422 `VAR_SOURCE_REF_EMPTY`；
- `source="env"` 的 `value` 须匹配环境变量名白名单 `[A-Za-z_][A-Za-z0-9_]*`（否则 `VAR_SOURCE_REF_INVALID`）；
- **`source` 与 `scope` 互斥**：secret/env 来源变量必须 `scope="global"`（局部作用域变量不许声明受限来源，`VAR_SOURCE_SCOPE_MISMATCH`）。

### 2.3 运行期解析（loader `_seed_variables` 增分支）

`_seed_variables(graph)` 对 `source` 变量：
- `source="env"`：`os.environ.get(value)`——**命中** → 展开值进 `global`；**未命中** → 抛 `EnvVariableUnavailable`（码 `ENV_VARIABLE_UNAVAILABLE`，params `{name}`），该 run 显式 FAILED（与 `SECRET_UNAVAILABLE` 同族 fail-closed，不静默空串）。
- `source="secret"`：走 SecretProvider 解析（同 docs/32 接口）——命中 → 展开值；未命中/解密失败 → 抛既有 `SecretUnavailableError`（`SECRET_UNAVAILABLE`，params 带 secret 名），fail-closed。
- **展开值在运行期与普通字面量变量同权**（表达式、插值、tool_call params 均可用），但带**敏感标记**（见 §3）。
- **不变量**：`source` 变量的**引用形态**（声明）仍随图持久化/投影；**展开值**绝不写回 `graph.variables`、不进保存。

### 2.4 错误码

- 新增 `ENV_VARIABLE_UNAVAILABLE`（运行期，`{name}`）——进 runtime.json zh/en（模板 `环境变量 {name} 未配置`）。
- 复用 `SECRET_UNAVAILABLE`（连接面已有码，补齐 params `{name}` 透传；前端 runtime.json 已有该键则复用，缺则补）。
- 编译期 422 码：`VAR_SOURCE_INVALID`/`VAR_SOURCE_REF_EMPTY`/`VAR_SOURCE_REF_INVALID`/`VAR_SOURCE_SCOPE_MISMATCH`（进编译错误码目录 zh/en）。

## 3. 字段级可见（脱敏）

**判据**：`source ∈ {env, secret}` 的变量，其**展开值**在以下投影/落盘通道一律脱敏为 `<redacted:{source}:{引用名}>`：
1. `GET /api/graphs/{id}` 变量投影（运行时值区）；
2. RunResult `outputs`/`globals`（`run_end` 帧、`GET /api/runs/{id}`）；
3. 录制（RecordingCase 快照/录制端点）、回放比对（敏感变量展开值不参与逐键比对——比对键保留引用形态）；
4. 观察/observe 事件、告警消息上下文（若含变量值）；
5. 日志（结构化日志若有变量值打印点，脱敏；**新增脱敏 helper `redact_sensitive(value)`**，消费点显式调用）。

**实现**：`_seed_variables` 返回的 `global` 对敏感来源值包一层标记（如 `{"__sensitive__": ("env", name), "value": ...}` 或简单 tuple），`_redact_globals()` 在投影出口统一脱敏；比对侧用引用形态而非展开值。**明文绝不落**：录制/投影/日志任何通道都不写展开值。

## 4. 验收（U1196–U1200）

- **U1196（后端）**：DSL 校验——`source` 非法值/空引用/坏变量名/scope 非 global 均 422（含 params）且模板不变；合法声明编译通过。
- **U1197（后端）**：env 解析——命中变量展开可用（表达式/插值/tool_call 引用）；未命中 `ENV_VARIABLE_UNAVAILABLE` 且 run FAILED（fail-closed 反向对照：命中 run completed）。
- **U1198（后端）**：secret 解析——经 SecretProvider 命中展开可用；缺失/坏信封 `SECRET_UNAVAILABLE` 且零外呼（连接面同语义）。
- **U1199（后端）**：字段级可见——图投影/run 输出/录制/观察五通道中敏感展开值均 `<redacted:env|secret:NAME>`，无明文；比对以引用形态进行（敏感值差异不误报）。
- **U1200（前端 vitest）**：变量面板——来源徽标渲染、Secret 值区 `••••` 占位、只传引用名不传明文；apiClient 类型含 `source`。

## 5. 非目标与缓做注记

- **session 来源**（触发＝会话变量机制 11 S1）、**`x-secret-allowed` 节点参数级受限**（字段级权限面，触发＝真实细粒度授权需求，与 D20 节点级角色同批评估）、secret 轮换/多密钥（KMS，D20 同族）、图保存时校验引用可达性（缺省：运行期才知，运行期 fail-closed）。触发条件不变。

## 6. 落码顺序（docs-only 立项后另立批）

DSL 校验 → `_seed_variables` 分支＋错误码 → 脱敏 helper＋投影出口接线 → 录制/比对适配 → 后端测试 → 前端变量面板 → 前端测试 → 文档收口。

## 7. 收口注记（2026-10-07 落码完成）

七原子：`aae9fa8` feat(engine)（dsl.py/loader.py/redact.py）→ `c7346ea` feat(api)（六处 run_graph 注入 `secret_provider`）→ `c93d090` test(engine)（U1196–U1199，15 passed）→ `e12e4ba` feat(frontend)（来源 Select＋`••••` 占位＋i18n）→ `1703a45` test(frontend)（U1200）→ `fe5a146` fix(test)＋`d7a5809` fix(frontend)（错误码通道守护修正）。门：后端全量 **2424 passed／166 skipped／0 failed**（195s）、前端 vitest **816 passed／2 skipped**、oxlint 0/0、build 过、守护门（`test_error_code_channels` 等）16 passed。零新依赖／零迁移／无 ADR。

- **四处落码偏差/细化（照实）**：① `VAR_SOURCE_INVALID` 是防御码——pydantic `Literal` 会在模型层先拦非法 `source`（422 literal_error），该码实际只对**程序化构造**（`GraphVariable.model_construct`）可达，测试已注明；② **泄漏断言陷阱**：condition 输出 `expressionResults.expr` 含**表达式手写字面量**（操作期望值，非展开值），泄漏断言须用「≠手写字面量的独特展开值」（如 `sk-live-9f3a-unique`）；③ `SECRET_UNAVAILABLE` 此前在错误码豁免表（粗码组、无前端文案），本次补前端 runtime 文案后**移出豁免表**（`test_error_code_channels.py`）；④ U1200 覆盖 lib 类型面（`source` 字段/`VARIABLE_SOURCES` 枚举），组件渲染面（徽标/占位）由 tsc＋oxlint＋手动冒烟守护——项目无组件渲染测试基建，照 U1199 的 `tests/test_variable_sources.py` 后端 15 测为功能主证。
- **运行语义**：env 未命中 `EnvVariableUnavailable`（`ENV_VARIABLE_UNAVAILABLE`，fail-closed）；secret 走 SecretProvider（docs/32），未命中/未注入 `SecretUnavailable` 复用；`source` 存在时 `value` 为引用名，图投影/run 输出/录制/观察/日志五通道脱敏 `<redacted:{source}:{引用名}>`。
