# OpenAPI 导入与工具自动生成批契约设计

> **立项**：2026-09-23（承接「不用管 git，你推任务」总授权；批选由 AI 判断）。
>
> **定位**：04 §4.5「方式 1：API 导入——粘贴 OpenAPI/Swagger 文档链接或 JSON，自动生成适配器和工具」、§4.2 表「OpenAPI/Swagger，自动生成工具定义，参数 Schema 化」。v1 的 `http/request`（§4.6）是通用手工形态；本批补**文档驱动的自动形态**：解析 OpenAPI 3.x JSON，按 operation 生成 Capability（input_schema 自动派生），导入后即经 `/api/adapters` 发现、在编辑器工具选择器可见、参数表单由 M3 内核自动渲染——**零编辑器改动**。**零新依赖、零迁移、零外部资源、无选型变更（不新增 ADR）**；D22 再次部分取回、不解除（真实外部系统联调、YAML、安全方案接线、PG 持久化仍缓做）。
>
> **形状权威**：本文；01–08 规格冲突时以规格为准。
>
> **边界**：v1 仅 OpenAPI **3.0/3.1 JSON**（YAML 明确拒绝并提示转 JSON）；仅解析 path/query/header 参数与 `application/json` 请求体；不执行响应校验、不接 `securitySchemes`（目标接口若需鉴权，v1 由用户在公共 headers 手动携带，随 connections 打通后再自动接线）；`$ref` 仅解析本文档内 `#/components/...`，外部引用不抓取。

## 1. 范围

### A. 解析内核（新包 `src/atlas/openapi/`）

- `parser.py`（纯函数，零触网零 IO）：
  - `parse_document(raw_text: str) -> ParsedSpec`：`json.loads`；失败（含检测到 YAML 特征）→ `OpenApiError("OPENAPI_INVALID_DOCUMENT", "仅支持 OpenAPI 3.x JSON 文档，请先将 YAML 转为 JSON")`。
  - 版本：`openapi` 字段以 `3.` 开头，否则 `OPENAPI_UNSUPPORTED_VERSION`（Swagger 2.0 不支持，提示）。
  - `base_url`：取 `servers[0].url`；缺失 → `OPENAPI_INVALID_DOCUMENT「文档缺少 servers，无法确定服务地址」`；相对 servers URL（`/v1` 类）同样拒绝（无 Host 可拼）。
  - `title`：`info.title`（缺省回退「未命名 API」）。
  - 逐 `paths` × 方法（get/post/put/patch/delete/head/options）产出 **OperationDescriptor**；method 级 `parameters` 覆盖 path 级同名参数。
- `schema.py`：OpenAPI Schema → Capability JSON Schema **子集**转换（`_validate_schema_subset` 白名单为准）：
  - 类型/枚举/必填/最小最大/长度/正则/数组 items/对象 properties 直通（`exclusiveMinimum/Maximum` 布尔旧形态不支持，遇操作级跳过）；
  - `$ref: "#/components/schemas/X..."`：递归内联解析，**深度上限 8、引用环守卫**（撞限该 operation 标记 skipped，非整文档失败）；非本文档引用（URL/无 `#/` 前缀）→ skipped；
  - `nullable`/`allOf,oneOf,anyOf`：`allOf` 合并对象属性（浅合并、required 并集）；`oneOf/anyOf` v1 不支持 → skipped；`nullable:true` 并入 type 双形（子集不支持 type 数组——v1 取主类型、忽略 nullable）；
  - operation 不可转换时给出 `skip_reason`（如「不支持的关键字 oneOf」「$ref 深度超限」），preview/导入结果均显式列出。
- 描述符形状（pydantic，`models.py`）：
  - `ParsedSpec {title, version, base_url, operations: list[OperationDescriptor]}`
  - `OperationDescriptor {name, method, path, summary, description, permission, idempotent, input_schema, skipped: bool, skip_reason}`
  - `name` 生成规则：优先 `operationId`，清洗为 `^[a-z][a-z0-9_]{0,63}$`；缺失/冲突时由 `method + path` 段合成（如 `get_pets_by_id`），同 spec 内唯一（尾缀 `_2` 消歧）。
  - 权限推断：GET/HEAD → `read` 且 `idempotent=true`；其余 → `write`；**绝不推断 financial/delete**（保守口径，资金操作不自动生成）。
  - `input_schema` 由 path/query/header 参数（`required` 透传、description/default 透传——default 不进 Capability schema 时忽略）＋ `requestBody.content."application/json".schema` 合成：参数对象 properties 以参数名入键，path 参数统一 `in: "path"` 分组？不——v1 扁平结构：`{...path/query/header 参数..., "body": <请求体 schema>}`，required 并集；无 requestBody 时无 body 键。`multipart/form-data`、`application/x-www-form-urlencoded` 不支持（该 operation skipped）。

### B. 导入存储与执行（同包）

- `store.py`：`ImportStore` **进程内 per-tenant**（随 TenantServices 两档装配；照 connections 先例 **reset 不清**）；保存 `ImportedSpec {spec_id, title, base_url, created_at, operations: [OperationDescriptor 仅成功项]}`；list 投影不回 input_schema 全量？v1 回传全量（规模小），上限 **5 specs / 租户、200 operations / spec**，超限 `OPENAPI_LIMIT_EXCEEDED` 422。
- `adapter.py`：`ImportedApiHarnessAdapter(HarnessAdapter)`：
  - `adapter_id = f"openapi:{spec_id}"`、`adapter_type = "api"`；
  - capabilities 由 OperationDescriptor 构造（name/description/input_schema/permission/idempotent）；
  - `_execute`：按 descriptor 的 method/path 渲染请求——**path 参数**替换 `{id}` 模板段（缺失 → `OPENAPI_INVALID_PARAMETER`），query/header 参数分组发送，body 取参数 `body`；
  - 传输复用 httpapi 机制：以导入 `base_url` 构造 `HttpApiClient`（或等价 request 函数），**出向过 EgressGuard**（导入不代表绕过 SSRF 准入；`EGRESS_DENIED` 原样透传为失败 Observation），任何 HTTP 响应均 SUCCESS（与 §4.6 一致），传输层错误折 `HTTP_TIMEOUT/HTTP_CONNECT_ERROR`；
  - 鉴权 headers：v1 无 securitySchemes 自动接线；execute 接受参数中 `headers`（descriptor 显式声明的 header 参数），不接受任意覆盖之外的字段。

### C. REST

| 方法/路径 | 鉴权 | 说明 |
|---|---|---|
| `POST /api/openapi/preview` | operate | body `{content?: str, url?: str}`（二选一）；不落库。返回 `{title, base_url, operations: [...], imported_count, skipped_count}`。`url` 时服务端经 **egress 守卫 + HttpChannelTransport** 抓取（10s、不跟重定向），`EGRESS_DENIED`/取数失败 → 422 `OPENAPI_FETCH_FAILED` |
| `POST /api/openapi/imports` | operate | 同上入参；201 返回持久化 `ImportedSpec`（仅成功 operations）；全部 operation 均 skipped → 422 `OPENAPI_NO_IMPORTABLE_OPERATION` |
| `GET /api/openapi/imports` | read | 本租户导入列表（含全量 operations） |
| `GET /api/openapi/imports/{spec_id}` | read | 单项；不存在 404 |
| `DELETE /api/openapi/imports/{spec_id}` | administer | 200 `{deleted: true}`；同步从运行时注册表摘除；不存在 404 |

- 预览/导入对同一文档两次跑 parser 结果须一致（parser 纯函数）。
- `/api/adapters`：`_runtime_registry` 在全局基础设施＋本租户渠道适配器之外，**合并本租户全部 ImportedApiHarnessAdapter**（照 docs/38 channel 动态适配器同款装配点）；工具全名 `openapi:{spec_id}/{operation}`。
- POST/DELETE 经既有写操作审计中间件（path 模板；动作落审计，不记文档内容——文档可能含内部 URL，audit 只记 8 元数据）。

### D. 前端（新页面，编辑器零改动）

- apiClient：`previewOpenApi/importOpenApi/listOpenApiImports/getOpenApiImport/deleteOpenApiImport` ＋类型（ImportedSpec/OperationDescriptor/PreviewResult）；+5 vitest（URL/方法/body、201 投影、error 不吞）。
- `pages/OpenApiImports.tsx`：
  - 导入卡：Tab「粘贴 JSON」/「从 URL 获取」→ `预览`结果表（method Tag、name、path、summary、权限；skipped 行灰色＋skip_reason 文字提示）→ `导入`（operate）成功后刷新列表；
  - 已导入列表：title/base_url/操作数/创建时间，展开看 operations；删除 Popconfirm（admin）；空态说明文案；
  - viewer 只读（无导入/预览/删除控件——preview 需 operate，viewer 仅可看列表）。
- Dashboard 加入口「API 导入」（operator+；viewer 不可见），App 切页。
- i18n：新 `openapi` namespace（zh 填实；en-US 空 `{}`；method/专名不译，skip_reason 技术原样展示）。
- 编辑器：tool_call 节点工具选择器自动出现 `openapi:*` 适配器、参数表单自动按 input_schema 渲染（M3），无需改动；冒烟验证闭环。

## 2. 非目标（写入 docs/14 D22 注记）

- YAML/Swagger 2.0 文档支持（零新依赖约束下不引 PyYAML；触发＝真实接入需要时评估）；
- `securitySchemes`（oauth2/apiKey/bearer）自动接线——后续与 connections 实体绑定联动；**2026-09-23 注：静态密钥子集（apiKey header/query、HTTP Bearer）随 [docs/44](44-OpenAPI-securitySchemes静态密钥批契约设计.md) 取回（信封加密、缺密钥 fail-closed）；oauth2/openIdConnect/basic/cookie 仍缓做 D22**；
- `multipart/form-data`、`oneOf/anyOf`、回调/webhooks、外部 `$ref` 抓取、响应 schema 校验与结果类型化；
- 导入规格 PG 持久化/多实例共享（v1 进程内，重启后需重新导入；**2026-09-23 PG 档已随 docs/43 取回——迁移 018 openapi_imports＋PgImportStore，内存档保留**）；
- 按 operation 的重试策略/熔断差异化配置（现走 httpapi 统一默认）；
- OpenAPI 文档版本升级 diff、导入市场共享、批量导出。

## 3. Schema 契约（03 同步）

- 03 新增 `openapi_import` 契约：`ImportedSpec`、`OperationDescriptor`（含 skipped/skip_reason 可选）、preview/import 请求与响应字段概览、错误码列表（正文权威在本文）。
- 不改 `http_request` 既有契约；无 DB 迁移。
- 错误码：`OPENAPI_INVALID_DOCUMENT`（422）、`OPENAPI_UNSUPPORTED_VERSION`（422）、`OPENAPI_FETCH_FAILED`（422）、`OPENAPI_NO_IMPORTABLE_OPERATION`（422）、`OPENAPI_LIMIT_EXCEEDED`（422）、`OPENAPI_INVALID_PARAMETER`（运行期，失败 Observation）。

## 4. 运行时语义

- parser 为纯函数：同输入恒定输出，不读环境、不发请求；URL 抓取在 API 层（preview/import 端点）完成后把文本喂给 parser。
- 导入不触发任何出向调用；出向只发生在图运行执行导入工具时，且受 EgressGuard 约束（与真实接入安全准入 docs/32 一致）。
- 删除导入规格后：运行时注册表立即摘除；**已保存图中对该适配器工具的引用不被改写**（照 subgraph 升级体检口径，后续运行报 UNKNOWN_CAPABILITY，由用户自行处理；文档明示）。
- 权限：自动生成工具最高仅 write；FINANCIAL 永不自动产生（资金面只允许显式人工定义的适配器）。

## 5. 测试矩阵（候选 U390 起，13 落号）

- 单元（parser/schema 纯逻辑，~26）：petstore 规范全量解析（操作数/名字合成/参数分组/requestBody schema）；operationId 清洗与消歧；坏 JSON/YAML 拒绝；版本/Swagger2 拒绝；缺 servers；$ref 内联/深度/环/外部引用；allOf 合并；oneOf skipped；form-data skipped；权限与幂等推断；path 参数渲染、query/header/body 组装；EGRESS_DENIED 透传；4xx/5xx 仍 SUCCESS。
- API（~10）：preview 粘贴/URL（fake transport）、skipped 投影、全 skipped 422；import 201＋limit；list/get 404/跨租户 404；DELETE 摘除后 `/api/adapters` 不可见；viewer 403、admin 删除；审计落痕。
- 前端（~8）：apiClient 五函数；页面结构测试（预览表 skipped 行、导入后列表、viewer 只读）。
- 浏览器冒烟（≥3 截图）：导入 petstore（粘贴）→ 工具选择器出现 `openapi:*` → 配置并真实运行命中 demo mock 服务（200）→ 删除后工具消失；控制台零错误。

## 6. 原子序

1. docs-only 立项：本文＋08＋14（D22 尾注）＋00 地图＋handoff＋CHANGELOG；
2. `feat(openapi): parse openapi 3 documents into operation descriptors` ＋parser/schema/models 测试；`.venv/bin/pytest`；
3. `feat(openapi): add in-process import store and executable imported adapter` ＋执行测试；
4. `feat(api): add openapi preview/import endpoints and merge into discovery` ＋API 测试；
5. `feat(frontend): add openapi import page with preview and spec list` ＋apiClient/i18n/测试；`pnpm lint && pnpm test && pnpm build`；
6. docs 收口：浏览器冒烟（≥3 截图）＋CHANGELOG＋handoff/08/42 落码注记。

## 7. 落码注记（2026-09-23 收口）

- 六原子序全部完成：①docs 立项 `8c9c512` → ②parser 内核 `139848a` → ③store＋执行适配器 `90b8179` → ④API 端点＋发现合并 `712f28a` → ⑤前端页面 `3f40b10` → ⑥docs 收口（本步）。均在 dev、未 push。
- 收口门：后端 **1352 passed / 48 skipped**（立项基线 1293/48，净增 59 常跑测、零回归）；前端 **591 passed / 2 skipped / 46 files**（立项基线 584/2/45，净增 7）；`pnpm lint` 与 `pnpm build` 过（仅既有 chunk-size 提示）。
- 浏览器冒烟全绿（4 截图 docs/smoke-shots/openapi-smoke-*）：粘贴 petstore 预览（3 operations、oneOf 行 operation 级 skipped 灰显）→ 导入后列表展开可见 2 工具 → 编辑器工具选择器出现 `openapi:openapi-1/*`、`limit` 参数由 M3 Schema 内核自动渲染（零编辑器改动）→ 配置 limit=10 编译并运行，`tool_call-1` **SUCCESS（HTTP 200）**，body 为 mock 返回 `{"pets":[{"id":1,"name":"Rex"}]}`。删除语义：operator DELETE 403、admin 删除 `{deleted:true}`，删除后 `/api/adapters` 不再含该适配器、spec GET 404。
- 冒烟缝为临时启动器（假 DNS＋httpx MockTransport，仓库外 /tmp，未提交）；收口后已恢复普通 uvicorn 启动，真实后端保持 egress 全量约束。
- 运行期注记：出向连接由导入 `base_url` 构造的 HttpApiClient 执行，`EGRESS_DENIED`/解析失败原样折为失败 Observation；删除导入后已保存图中的旧引用不改写，后续运行报 UNKNOWN_CAPABILITY（§4 已明示）。
- **D22 部分取回、不解除**：YAML/Swagger2、securitySchemes 自动接线、multipart、PG 持久化、真实外部 API 联调仍缓做。
