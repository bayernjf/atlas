# 93-LLM模型配置管理面-v1契约设计.md

> **打包 Y（暂定名）：LLM 模型配置管理面——内置模型（admin 管理）＋BYOK（用户自配置）**
>
> - **立项**：2026-10-04，docs-only 先行。承接 [docs/73 §10](73-自主上线MVP缺口清单追踪.md) 用户产品口径（内置模型＝平台方内置 Agnes、admin UI 管理；BYOK＝用户自配置 key）＋docs/08 C 组候选行（承接 14 D14 余部「按租户模型配置」）。
> - **本批性质**：**契约设计轮（形状权威＝本文），零代码、零迁移、零依赖、零 ADR**。落码与否由用户另行拍板；本文把范围/非目标/验收/原子序/同步面写死，供落码批直接执行。
> - **触发条件已满足**：用户已明确产品口径（docs/73 §10），不再等"稳定供应商"；Agnes 已定为内置模型。

---

## 0. 现状（实测，2026-10-04 dev HEAD `c9f49c2`）

- **四个 LLM 消费点全部直读全局 env**：
  - `src/atlas/llm/decision.py:162` `get_decision_client()`：`os.getenv("LITELLM_MODEL")` 非空→`LiteLLMDecisionClient(model)`，否则 `RuleBasedDecisionClient()`（docs/64 J-2a：prod 档 fail-closed 门在 loader 侧，判据是**客户端类型**）。
  - `src/atlas/llm/condition_classifier.py:140` `get_condition_classifier()`：同构，`OfflineConditionClassifier()` 兜底（docs/73 5.4：prod 档 `LLM_CLASSIFIER_UNAVAILABLE` 门判据是分类器类型）。
  - `src/atlas/llm/nl_generate.py:33` `generate_graph()`：有模型走 `_generate_with_llm`，否则规则兜底（仅退款/售后）。
  - `src/atlas/reflection/adapter.py:125` `get_summarizer()`：同构，`NullSummarizer()` 兜底。
- **litellm 直连路径读的是 `OPENAI_API_KEY`/`OPENAI_BASE_URL`**（docs/73 §0 演练实证；`security/bootstrap.py:132` `_HYDRATED_ENV_KEYS = ("OPENAI_API_KEY",)` 已支持 `_FILE` 补水）。
- **节点级 `model` 覆盖已存在**：`condition_classifier.py:76-77`（ZP 批）、`decision.py` 同构——节点配置 `model` 非空则覆盖构造期默认，仅本次调用生效。**管理面要解决的是"构造期默认"从哪来**。
- **per-tenant 服务先例**：`TenantServices`（`src/atlas/iam/registry.py:66`）已挂 `connection_service`（T4 OAuth2 连接，内存/PG 两档 store、reset 不清）、`channel_registry`（docs/38，ADR T28）——**模型配置 store 照此模式挂入，天然 per-tenant**。
- **权限角色**：`require("administer")`（管理）／`require("operate")`（操作）／`require("read")`（读）现成（`api/main.py` 大量使用；Users/Connections 管理页同为 administer 门）。
- **前端管理页先例**：`frontend/src/pages/` 已有 Users.tsx / Connections.tsx / AuditLog.tsx（admin 门），模型管理页照 Connections 页形态落。

---

## 1. 产品口径（docs/73 §10 原文）

| 模式 | 定义 | 谁配置 | 谁使用 | 生效范围 |
|---|---|---|---|---|
| **内置模型** | 平台方内置（Agnes 已给），admin UI 管理（密钥/模型/开关） | `administer` 角色 | 所有用户直接用，**不需要自带 key** | 平台级（per-tenant 共享同一内置配置） |
| **BYOK** | 用户自己配置自己的 key | 用户本人（operate） | 仅该用户/该租户的图 | per-tenant（租户内共享，用户级字段可后续） |

> **语义约束（本文拍板口径，落码批不得改）**：
> 1. **内置模型是平台级单一配置**（一个 tenant 数组或全局一份），admin 在管理页维护；用户图不配置 model 时默认走内置。
> 2. **BYOK 是 per-tenant 配置**（每个租户一份），租户管理员（administer）或该租户 operator 配置自己的 key；配置后**该租户内所有图**默认走 BYOK，而非内置。
> 3. **优先级**（节点显式 model > BYOK > 内置 > env 兜底）：节点级 `model` 覆盖最优先（现状已支持）；未配置节点时，租户配了 BYOK 用 BYOK，否则用内置；env（`LITELLM_MODEL`）降为**最后兜底**（Demo/dev 形态不变）。

---

## 2. 范围（落码批做）

1. **配置模型与存储**：新增 `src/atlas/llm/config.py`＋两档 store（内存/PG）：
   - `ModelConfig`：`mode: "builtin" | "byok"`、`model: str`（litellm 模型名，如 `openai/agnes-2.5-flash`）、`api_key_enc: str`（AES-GCM 信封，复用 docs/32 T26 的 secrets 工具，**不明文**）、`base_url: str | None`、`enabled: bool`、`updated_by`/`updated_at`。
   - **内置**：全局一份（`ModelConfigStore.get_builtin()`）；**BYOK**：per-tenant 一份（`get_byok(tenant_id)`）。
   - 挂 `TenantServices.model_config`（照 `connection_service` 模式；reset 不清，PG 档持久化）。
2. **消费点改造（4 处）**：`decision.py`/`condition_classifier.py`/`nl_generate.py`/`reflection/adapter.py` 的 `get_*()` 改为**先查配置再落 env**——优先级：显式传参 > 租户 BYOK > 内置 > env。签名保持向后兼容（无租户上下文时回退现状）。
3. **REST 管理面**（全部 `require("administer")`，read 门可读）：
   - `GET /api/models`：返回内置配置（脱敏：api_key 只回 `***` 后 4 位）＋当前生效摘要。
   - `PUT /api/models/builtin`：admin 维护内置（model/api_key/base_url/enabled）。
   - `GET/PUT /api/models/byok`：租户管理员维护本租户 BYOK。
   - **密钥写入即加密**（AES-GCM），读回脱敏，日志/响应零明文。
4. **前端管理页**：`frontend/src/pages/Models.tsx`（admin 门）——内置模型卡片（model/api_key/enabled）＋BYOK 卡片（本租户），照 Connections.tsx 形态；i18n 新 ns `models.json`（zh/en，PARITY 守护）。
5. **prod fail-closed 保持**：docs/73 1.1/5.4 的 prod 门（`AiDecisionUnavailable`/`ConditionClassifierUnavailable`）**判据是客户端类型，不随配置来源改变**——配置面只是"默认模型从哪来"，门语义零变化。

## 3. 非目标（本批不做，写死防漂移）

- **不做用户级 BYOK**（仅租户级；用户级字段留 docs/14 缓做）。
- **不做多模型/模型路由/按节点持久选模型**（节点级 `model` 覆盖已存在，本次只接默认来源）。
- **不做模型健康探测/用量统计/成本追踪**（商业 provider 用量数据属 D 组外部资源）。
- **不做 admin 权限细分**（沿用 administer 单一角色，不引 RBAC 扩展）。
- **不解除 docs/14 D14 其余余部**（置信度阈值/多候选/结构化输出字段级脱敏仍缓做）。
- **不动 env 兜底路径**（Demo/dev 形态零变化；`LITELLM_MODEL` 仍有效）。

## 4. 验收（落码批门禁；13 候选用例 U1090 起）

- **U1090**：内置配置 CRUD（admin）——写 key 后读回脱敏、响应零明文；enabled=false 时消费点回退 env。
- **U1091**：BYOK 配置 per-tenant 隔离——租户 A 配 key 不影响租户 B；B 无配置走内置。
- **U1092**：优先级——节点显式 model > BYOK > 内置 > env（各档构造正确客户端类型）。
- **U1093**：四消费点改造后，无任何配置（清 env）时行为与现状一致（规则/Offline/Null 兜底）。
- **U1094**：prod fail-closed 门零回归（`test_ai_decision_prod_gate.py`/`test_condition_classifier_prod_gate.py` 原样绿）。
- **U1095**：密钥 AES-GCM 信封——库中明文零命中（gitleaks/机检）、`_FILE` 补水路径兼容（复用 ZO）。
- **U1096**：前端 Models 页——admin 可编辑内置/BYOK、脱敏展示、双语键 PARITY。
- **后端门**：全量 pytest（基线 2229 passed/136 skipped）零回归；**前端门**：vitest（基线 772/2）＋oxlint 0/0＋`pnpm build`；**守护门**：`test_handoff_integrity.py`/`test_migration_convention.py`（若零迁移则后者原样绿）。

## 5. 原子提交序（落码批执行）

1. `docs(contract)` 本文（+08 立项条/03 契约注记/14 注记/00 地图/handoff/CHANGELOG）。
2. `feat(llm)` `config.py`＋两档 store＋`TenantServices.model_config` 装配（含 AES-GCM 信封复用）。
3. `feat(llm)` 四消费点改造（优先级解析；无租户上下文回退 env）。
4. `feat(api)` 三 REST 端点（administer/read 门，脱敏）。
5. `feat(frontend)` Models 页＋i18n 双语。
6. `test` U1090–U1096＋既有门零回归。
7. `docs(closeout)` 回填 08/13/73/14/00/handoff/CHANGELOG。

## 6. 同步矩阵（契约门，本批已做/落码批补）

- **docs/08**：C 组候选行已登记（2026-10-03）；本契约为其形状权威，落码批在此追加立项注记。
- **docs/03**：契约索引新增 `model_config`（若 PG 化含表名；本批零迁移则只注记）。
- **docs/73 §10**：1.1 残余追踪——管理面落码后转「已实现」，1.1 判据（prod 真调用）随部署侧走。
- **docs/14**：D14 余部「按租户模型配置」注记——本批取回半边（管理面），其余不解除。
- **docs/00**：文档地图登记 93。
- **handoff/CHANGELOG**：立项批回填。

## 7. 风险与边界

- **密钥安全**：AES-GCM 信封主密钥仍走 `security/bootstrap.py` 启动门（docs/32 T26 既有）；本批不新增密钥，只复用加密工具。**写日志/异常信息时 api_key 必须脱敏**（现状 `_SYSTEM_PROMPT` 与错误信息无明文泄漏，需机检守护）。
- **litellm 读 env 的耦合**：litellm 直连路径自读 `OPENAI_API_KEY`/`OPENAI_BASE_URL`（docs/73 实证）——BYOK 按租户传入时**不能靠 env**，须在调用点显式传 `api_key`/`base_url` 给 `litellm.completion`（现状 decision/condition_classifier 未传，落码批须补）。这是本批**最大的实现风险点**，验收 U1092 专门钉它。
- **单副本约束不变**：不引入新进程/新依赖；配置 store 若 PG 化沿用既有 `PgStore` 模式。
