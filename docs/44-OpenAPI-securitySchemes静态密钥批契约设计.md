# OpenAPI securitySchemes 静态密钥批契约设计

> **立项**：2026-09-23（承接「不用管 git，你推任务」总授权；批选由 AI 判断）。
>
> **定位**：docs/42/43 把「粘贴 OpenAPI → 自动生成工具」打通并已 PG 持久化，但解析器**完全忽略 `securitySchemes` 与 `security`**——而真实世界 API 几乎全部要求 apiKey/Bearer 鉴权。当前用户唯一的办法是把鉴权头当作普通 header 参数逐次手填，且密钥明文出现在图参数与监控记录里。本批接通**静态密钥**（apiKey header/query、HTTP Bearer）：解析有效安全要求 → 导入时填写密钥（经既有 SecretProvider 信封加密，AES-GCM/plain 两档随 `ATLAS_MASTER_KEY`）→ 适配器执行时逐次解密注入；OAuth2/openIdConnect 仍走 connections 路线，不在本批。
>
> **形状权威**：本文；01–08 规格冲突时以规格为准。**零新依赖；不新增 ADR**（复用 ADR T26 已定稿的 SecretProvider 信封与 cryptography）。
>
> **边界**：v1 只做文档内声明的静态方案；密钥为 **spec 级**（同一 scheme 名全文档共享一个值）；不做 http basic、`in: cookie`、oauth2/openIdConnect/mutualTLS；不做连接化 OAuth 托管（docs/35 T4 管 OAuth 授权码流程，二者边界不重叠）。

## 1. 范围

### A. 解析（`src/atlas/openapi/parser.py`、`models.py`）

- 新模型 `SecurityScheme`：
  - `name: str`（securitySchemes 对象的 key）；
  - `kind: "api_key" | "bearer"`；
  - `location: "header" | "query" | None`（apiKey 的 `in`；bearer 固定 header，字段为 None）；
  - `param: str`（实际注入的 header/query 名：apiKey 取 `name` 字段；bearer 固定 `Authorization`）；
  - `prefix: str`（值前缀：bearer 固定 `"Bearer "`；apiKey 为 `""`）。
- 解析 `components.securitySchemes`（支持 `$ref` 内部引用），**只收录支持项**：
  - `type: apiKey` 且 `in: header|query` → 收录；`in: cookie` 不收录；
  - `type: http` 且 `scheme: bearer`（大小写不敏感）→ 收录；`basic` 等其他 scheme 不收录；
  - `type: oauth2 | openIdConnect`、其他 type → 不收录。
- `ParsedSpec` 增字段 `security_schemes: dict[str, SecurityScheme]`（仅支持项）。
- 每个 operation 计算**有效安全要求**：operation 级 `security` 存在则覆盖全局 `security`（标准覆盖语义，不合并），否则用全局。结果存入 `OperationDescriptor.security: list[list[str]]`——OR-of-AND：外层任一组成立即可，内层 scheme 名全部需要；
  - 只保留已收录 scheme 名；引用不支持方案的内层项剔除该名；
  - 源文档空要求 `security: []`（显式匿名）与未声明安全 → descriptor `security = []`（执行端语义同为「无需密钥」）；
  - 源文档空组 `{}`（匿名可选之一）→ 保留为空内层组；
  - 解析不出任何可用组且原要求非空（纯 oauth2 等）→ `security = []`，不导致 operation skipped（preview 以方案清单另行提示「文档含暂不支持的鉴权方式」）。
- operation 既有的 apiKey header 参数与安全方案同名时：安全注入与用户参数都写同一 header，**用户显式参数优先**（不覆盖），文档注记。

### B. 导入规格形状（`store.py` ImportedSpec；`pg_store.py`）

- `ImportedSpec` 增两字段：
  - `security_schemes: dict[str, SecurityScheme] = {}`；
  - `credential_envelopes: dict[str, str] = {}`（scheme 名 → **信封字符串**，任何投影不含明文）。
- 信封由 API 层用模块级 SecretProvider 加密后传入，store（内存/PG）只持久化信封、不持有 provider——沿用 connections 的存储边界。
- 上限（5 spec / 200 ops）、id（`openapi-N`）、错误码、reset 不清等 docs/42/43 既定语义全部不变。

### C. REST（`src/atlas/api/main.py`）

- 模块启动时建一次 `_secret_provider = build_secret_provider_from_env()`（与 `_openapi_egress` 同列；demo 无 master key 时为 PlaintextSecretProvider，行为同 connections）。
- `POST /api/openapi/preview` 响应增字段：
  - `security_schemes: list[...]`：每项 `{name, kind, location, param}`（无任何密钥值）；
  - operations 数组本就逐字段 model_dump，随之带上 `security`。
  - preview **永不接收密钥**。
- `POST /api/openapi/imports` 请求体（在现有 `content|url` 上）增 `credentials: dict[str, str] | None`：
  - key 必须是 preview 中存在的 scheme 名，未知 key → 422 `OPENAPI_INVALID_CREDENTIAL`「未知鉴权方案：{name}」；
  - 值为空串/纯空白视为未提供（忽略）；
  - 非空值经 provider 加密成信封落 store；
  - 允许导入时不填任何密钥（执行该 operation 时 fail-closed，见 D）。
- 新端点 `PUT /api/openapi/imports/{spec_id}/credentials`（require operate）：
  - body `{"credentials": {name: value}}`；未知 scheme 名 422 `OPENAPI_INVALID_CREDENTIAL`；
  - 非空值加密后 upsert；空串删除该 scheme 既有信封；
  - 响应 `{"configured": [name, ...]}`（当前已配置信封的 scheme 名，按声明序；不回值）。
- `GET /api/openapi/imports`、`GET .../{id}` 返回的 ImportedSpec 带 `security_schemes` 与 `credential_envelopes`——**信封字符串不属于秘密值**（AES-GCM 无密钥不可解），沿用 connections 投影口径；任何响应通道均不出现明文密钥。
- 鉴权档位与既有端点一致（preview/import/credentials 需 operate，list/get read，delete administer）。

### D. 适配器执行（`src/atlas/openapi/adapter.py`）

- 构造增 `secret_provider: SecretProvider | None = None`；main.py 发现构建点（main.py:345）传入模块级 `_secret_provider`。
- 执行时按 `descriptor.security` 顺序选**第一个全部成员都有信封的组**：
  - 空组或 `security == []` → 匿名执行；
  - 每个信封**当次解密**（不缓存明文、不记日志），按 scheme 渲染：
    - apiKey header → 并入请求 headers；apiKey query → 并入 URL query；
    - bearer → `Authorization: Bearer {value}`；
  - 与用户显式 header/query 参数冲突时用户参数优先；
  - provider 为 None 却需要信封（理论不可达，防御性 fail-closed）→ `SECRET_UNAVAILABLE`。
- 所有组都缺至少一个信封 → 返回失败结果 `StructuredError("OPENAPI_CREDENTIAL_MISSING", "该接口需要鉴权但未配置密钥（…scheme 名…）")`；**不发出无鉴权请求**（该 operation 在外部系统大概率 401/403，但 fail-fast 避免把缺配置误当业务失败）。
- `observe()` 的 `last_request` 已经 HttpApiClient 走 `redact_headers`，鉴权头天然 `***`；query 中的 apiKey 不在现有脱敏面内——本批在 HttpApiClient last_request/错误信息出口对 query 不回显完整值的做法不变（注记：query apiKey 将出现在 URL 观测里，Demo 接受，平台化随观测脱敏统一处理）。

### E. PG 持久化（迁移 019、`pg_store.py`）

- `db/migrations/019_openapi_auth_columns.sql`：`ALTER TABLE openapi_imports ADD COLUMN IF NOT EXISTS security_schemes JSONB NOT NULL DEFAULT '{}'::jsonb, ADD COLUMN IF NOT EXISTS credential_envelopes JSONB NOT NULL DEFAULT '{}'::jsonb;`（PG 支持单语句多列；手写幂等，不引 Alembic）。
- `_COLS` 扩展，读写与 ImportedSpec 新字段对齐；旧行默认 `{}`，内存档/PG 档逐字段一致。
- 跨重启信封仍可解的前提是 master key 不变（key 轮换语义见 T26：旧 kid 信封需保留旧密钥的 provider；Demo 注记，不做迁移工具）。

### F. 前端（`frontend/src/pages/OpenApiImports.tsx`、`apiClient.ts`；零新依赖）

- apiClient：`OpenApiPreview`/`ImportedSpec`/`OperationDescriptor` 补字段；`importOpenApi(source, credentials?)`；新 `putOpenApiCredentials(specId, credentials)`。
- 导入弹窗：preview 返回后，若存在 security schemes，逐 scheme 渲染 `Input.Password`（label 示 kind＋位置，如「X-API-Key（请求头）」「Authorization: Bearer」）；提交时随 import 发送，空值不发。
- 规格列表/详情：展示鉴权方案 Tag 与「已配置 n/m」状态；不回显密钥；提供「配置密钥」操作打开同一表单（调 PUT）。
- 编辑器、工具选择器、Dashboard 入口零改动；i18n：新文案进 `openapi` namespace（zh 填、en-US 空占位，照既有零依赖 i18n 约定）。

## 2. 非目标

- HTTP Basic（及 `scheme` 其他值）、apiKey `in: cookie`、mutualTLS、OAuth2 / openIdConnect 流程化托管（连接化随 connections，真实联调缓做 D22）。
- 密钥的取回/查看/复制；按 operation 差异化密钥；多环境/多 server 选择；密钥轮换工具与信封批量重加密。
- YAML、Swagger 2.0、multipart、原始文档留存、导入去重（docs/42/43 既有非目标不变）。
- query apiKey 的观测面脱敏（随平台化观测治理）；多实例密钥缓存/广播（读穿透 PG）。

## 3. Schema 契约（03 同步）

- 03 `openapi_import` 契约：ImportedSpec 增补 `security_schemes`、`credential_envelopes`；OperationDescriptor 增补 `security`；形状权威本文。
- 新错误码登记：`OPENAPI_INVALID_CREDENTIAL`（422）、`OPENAPI_CREDENTIAL_MISSING`（执行失败结果码；同步错误码清单）。
- 新 REST 行：`PUT /api/openapi/imports/{spec_id}/credentials`（12 同步）。
- docs/42 §2 非目标「securitySchemes 接线」加注：**2026-09-23 随 docs/44 取回静态密钥子集（apiKey header/query、bearer；oauth2 等仍缓做 D22）**。

## 4. 运行时语义

- 密钥只以信封落库/进进程内状态；明文生命周期仅限单次适配器调用栈；审计/监控/观测不记录明文（headers 已有脱敏）。
- PG 档：重启后密钥信封与方案声明随 openapi_imports 行恢复，执行无需重新配置；master key 变更会使旧信封不可解（fail-closed `SECRET_DECRYPT_ERROR`）。
- 安全要求解析为静态纯数据，不执行文档内任何脚本/表达式。
- 并发：PUT credentials 与执行并发时，信封 dict 读写下以 store 单连接/锁为准（PG 行级 UPDATE；内存档 store 锁），最坏一次调用读到旧信封——Demo 接受。

## 5. 测试矩阵（13 落号）

- 解析单测（扩 `tests/test_openapi_parser.py`，~8）：apiKey header/query 收录、bearer 收录与前缀、cookie/basic/oauth2/openIdConnect 不收录、operation 覆盖全局 security、全局缺省、空组/空要求语义、$ref securityScheme、同名 header 参数不冲突。
- 适配器单测（扩 `tests/test_openapi_adapter.py`，~7）：apiKey header/query 注入、bearer 头、缺密钥 OPENAPI_CREDENTIAL_MISSING 且不发出请求、空组匿名放行、用户参数优先、信封逐次解密（可注入计数 provider）。
- API 测试（扩 openapi API 测试文件，~5）：preview 方案投影无密值、import 带/不带 credentials、未知 scheme 422、PUT upsert/删除/404、所有响应无明文密钥（扫响应文本）。
- PG 集成（扩 `tests/test_openapi_imports_pg_integration.py`，~3）：迁移 019 后新字段往返、重启模拟信封仍在、旧行默认 `{}` 不破坏读取。
- 浏览器冒烟（内存档＋PG 档各一条，≥3 截图）：mock 上游校验 `X-API-Key`/`Authorization`（错值 401）→ 导入填密钥 → 运行 SUCCESS 200；PG 档重启后仍 200；不填密钥运行得到明确中文失败提示。

## 6. 原子序

1. docs-only 立项：本文＋03＋12（REST 行）＋docs/42 §2 注记＋08＋14（D22 尾注）＋00 地图＋handoff＋CHANGELOG；
2. `feat(openapi): parse static security schemes and effective requirements` ＋解析单测；`.venv/bin/pytest`；
3. `feat(openapi): wire credential envelopes into import stores and execution` ＋迁移 019＋两端 store/适配器/PUT 端点＋测试；`.venv/bin/pytest`＋PG 集成；
4. `feat(frontend): collect and configure imported api credentials` ＋i18n；`cd frontend && pnpm lint && pnpm test && pnpm build`；
5. docs 收口：浏览器冒烟（截图）＋CHANGELOG＋handoff/08/本文落码注记。
