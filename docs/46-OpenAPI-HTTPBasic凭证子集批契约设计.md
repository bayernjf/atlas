# OpenAPI HTTP Basic 凭证子集批契约设计（docs/46）

- 立项日期：2026-09-23（dev；AI 判断承接「不用管 git，你推任务」总授权）
- 关联：[docs/44](44-OpenAPI-securitySchemes静态密钥批契约设计.md)（apiKey/Bearer）、[docs/42](42-OpenAPI导入与工具自动生成批契约设计.md)；缓做 [14](14-缓做事项登记表.md) D22
- 结论先行：**同一套 PUT credentials 链路扩展 HTTP Basic**——parser 收录 `type:http, scheme:basic`；凭证值由「单串」扩为「Basic 用 `{username,password}` 对象」，信封明文为 JSON、仍经 SecretProvider 加密；适配器解密后拼 `Authorization: Basic base64(u:p)`。**零新依赖、零迁移、零新 REST 端点/错误码、无 ADR。**

## 1. 范围

### A. 解析（`src/atlas/openapi/parser.py` `_security_schemes`）

- `type: http` 分支：`scheme` 小写为 `bearer`（既有）或 `basic`（本批）；其他 scheme（digest 等）继续忽略。
- Basic 的 `SecurityScheme`：`kind="basic"`、`location=None`、`param="Authorization"`、`prefix="Basic "`。
- 生效要求（OR-of-AND security 组）逻辑不变：basic 与 bearer/apiKey 一样按 scheme 名参与 operation.security 覆盖计算。

### B. 模型（`src/atlas/openapi/models.py`）

- `SecurityScheme.kind`：`Literal["api_key", "bearer", "basic"]`。

### C. 凭证写入（`PUT /api/openapi/imports/{spec_id}/credentials`）

- 请求体 `credentials` 的 value 类型按 scheme kind 分化：
  - api_key/bearer：string（既有语义）；空白串＝删除该信封。
  - basic：对象 `{username: string, password: string}`；两字段均须为非空字符串，否则 422 `OPENAPI_INVALID_CREDENTIAL`（中文 message 指明 Basic 需同时提供用户名与密码）；传 `null` 或空对象＝删除信封。
- 信封明文：basic 为 `json.dumps({"username": ..., "password": ...}, ensure_ascii=False)`；api_key/bearer 仍为裸串。统一经 `_secret_provider.encrypt` 写入 `credential_envelopes[name]`（列形状不变：dict[str,str]，值为密文）。
- 未知 scheme 名仍 422 `OPENAPI_INVALID_CREDENTIAL`；200 响应 `{configured:[name]}` 不变（configured 仅表示该 scheme 有信封，不回显字段）。

### D. 适配器执行（`src/atlas/openapi/adapter.py` `_resolve_credentials`）

- 解密后按 kind 渲染：
  - api_key/bearer：`headers[param] = prefix + value`（既有；api_key+query 进 query）。
  - basic：解密 → `json.loads` 得 `{username,password}` → `base64.b64encode(f"{u}:{p}".encode()).decode("ascii")` → `headers["Authorization"] = "Basic " + token`。
- basic 信封 JSON 损坏/缺字段 → `SECRET_DECRYPT_ERROR`（不发请求，复用既有错误码）。
- 缺信封 fail-closed `OPENAPI_CREDENTIAL_MISSING` 语义不变。

### E. 前端（`frontend/src/pages/OpenApiImports.tsx`，零新依赖）

- 凭证表单按 `security_schemes` 的 kind 渲染：basic 显示「用户名 / 密码」两个输入（密码用 Password），本地状态合并为一个 `{username,password}` 值随 scheme 名提交；apiKey/Bearer 维持单输入。
- 「已配置 n/m」计数口径不变（按信封有无）；viewer 只读不变。

## 2. 非目标（显式不做）

- Digest 等其他 HTTP authentication scheme；cookie 认证（Cookie header / JWT in cookie）；OAuth2 / openIdConnect（token 获取刷新仍随 D22 后续批）。
- 连接测试（test credential）、凭证取回/轮换/过期、SSRF 策略调整、按字段的 secret 引用（`secret://` 进 basic 字段）。
- Basic 用户名/密码的强度策略与审计记录；多实例会话。
- 任何新依赖、新迁移、新 REST 端点或错误码、新 ADR。

## 3. Schema 契约（03 同步）

- `SecurityScheme.kind` 取值集扩为 `api_key | bearer | basic`；basic 行 param=Authorization、prefix="Basic "。
- PUT credentials 请求体值类型：`string | {username:str, password:str} | null`（后两者仅 basic 合法；服务端按该 spec 的 scheme kind 校验）。
- 信封存储形状不变（`credential_envelopes: dict[str,str]` 密文）；无新错误码：沿用 `OPENAPI_INVALID_CREDENTIAL / OPENAPI_CREDENTIAL_MISSING / SECRET_DECRYPT_ERROR`。

## 4. 运行时语义

- Basic 明文（u:p）仅存在于单次适配器执行栈与 `Authorization` 头；不进 last_request / Observation / 录制 / 日志（与 docs/44 脱敏口径一致）。
- base64 仅编码非加密，安全完全依赖 HTTPS：egress 校验与 https 要求沿用现 httpapi 出向策略，本批不改变。
- 回放确定性：信封按调用解密，运行结果与 docs/44 一致；basic 不新增持久化面。

## 5. 测试矩阵（13 落号）

- parser（扩 `tests/test_openapi_parser.py`，~3）：http/basic 收录为 kind=basic/param/prefix；非 bearer 非 basic（digest）忽略；basic scheme 参与 security 组生效。
- API（扩 `tests/test_api_openapi.py`，~4）：basic 写 `{username,password}` 200 且 configured；缺 password/字段非字符串/空对象 422 OPENAPI_INVALID_CREDENTIAL；再传 null 删除信封；basic 与 bearer 混合 spec 按名区分。
- 适配器（扩 `tests/test_openapi_adapter.py`，~3）：配置 basic 后请求头为 `Authorization: Basic base64(u:p)`（固定向量断言）；信封 JSON 损坏 SECRET_DECRYPT_ERROR；缺信封 OPENAPI_CREDENTIAL_MISSING。
- 回归：docs/42/44 既有用例零改动通过；内存/PG 两档 store 同形。
- 前端（扩 OpenApiImports 相关测试，~2）：basic 双输入收集成对象提交、已配置计数不变。
- 浏览器冒烟（≥2 截图）：basic spec 无凭证运行得 OPENAPI_CREDENTIAL_MISSING → PUT 配用户名密码 → 运行 SUCCESS HTTP 200，请求头携带 `Authorization: Basic ...`。

## 6. 原子序

1. docs-only 立项：本文＋03＋12＋13＋14（D22）＋08＋00 地图＋handoff＋CHANGELOG；
2. `feat(openapi): parse http basic security scheme`（parser＋models＋parser 测试）；
3. `feat(openapi): accept and inject basic credentials`（PUT 校验＋适配器渲染＋API/适配器测试；`.venv/bin/pytest`）；
4. `feat(frontend): collect basic username and password inputs`（页面＋前端测试；`pnpm lint && pnpm test && pnpm build`）；
5. docs 收口：浏览器冒烟（截图）＋CHANGELOG＋handoff/08/本文落码注记。
