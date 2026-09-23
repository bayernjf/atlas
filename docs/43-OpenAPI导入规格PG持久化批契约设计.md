# OpenAPI 导入规格 PG 持久化批契约设计

> **立项**：2026-09-23（承接「不用管 git，你推任务」总授权；批选由 AI 判断）。
>
> **定位**：docs/42 落码后 OpenAPI 导入的唯一存储形态是**进程内 per-tenant ImportStore**——重启即失（docs/42 §2 非目标明确列了「导入规格 PG 持久化（v1 进程内，重启后需重新导入）」）。本批照 webhook 订阅（迁移 016）、webhook 投递（017）、channel 绑定（015）的两档先例，把导入规格落到 PG：`ATLAS_STORAGE_BACKEND=pg` 时换 PgImportStore，跨重启/多实例共享；内存档行为完全不变。**零新依赖；REST 形状、前端、编辑器零改动**（发现接口本就按请求从 store.list() 动态构建适配器，main.py:345）。
>
> **形状权威**：本文；01–08 规格冲突时以规格为准。无选型变更（**不新增 ADR**）。
>
> **边界**：只持久化 docs/42 既有的 ImportedSpec 形状；不做原始文档留存、不做导入去重（同文档可重复导入，沿用内存档语义）、不做软删除/审计回收站、不做租户间共享（D3/D25）。

## 1. 范围

### A. 表与迁移（`db/migrations/018_openapi_imports.sql`）

- 新表 `openapi_imports`：
  - `id TEXT NOT NULL`（spec_id，`openapi-{seq}`）、`tenant_id TEXT NOT NULL`、`seq BIGINT NOT NULL`（数字部分，供同租户排序）；
  - `title TEXT NOT NULL`、`base_url TEXT NOT NULL`、`created_at TIMESTAMPTZ NOT NULL`；
  - `operations JSONB NOT NULL`（OperationDescriptor 成功项数组，模型序列化 JSON）；
  - `PRIMARY KEY (tenant_id, id)`。
- id 生成照 oauth_connections 先例：`SELECT nextval('storage_id_seq')`（迁移 002 已建全局序列），**不建 per-tenant 计数表**；排序 `ORDER BY seq`。
- 迁移由 storage/migrations.py 按文件名顺序自动发现执行，手写幂等 SQL（`CREATE TABLE IF NOT EXISTS`），不引入 Alembic。

### B. PgImportStore（`src/atlas/openapi/pg_store.py`）

- 与 `ImportStore`（docs/42 §1 B）**同方法形状**：`add(spec, *, now=None) -> ImportedSpec`、`list() -> list[ImportedSpec]`、`get(spec_id) -> ImportedSpec | None`、`delete(spec_id) -> bool`。
- 构造 `PgImportStore(engine, tenant_id)`：行级 `tenant_id` 过滤；跨租户访问天然返回不存在（404 口径同内存档）。
- `add` 在单事务内：`nextval('storage_id_seq')` → 规格上限校验（`COUNT(*) >= 5` → `OPENAPI_LIMIT_EXCEEDED`「每租户最多导入 5 份 API 规格」）、单 spec 200 operations 上限（`OPENAPI_LIMIT_EXCEEDED`）→ INSERT。上限错误码/文案与 docs/42 内存档完全一致（HTTP 层折算 422 不变）。
- `operations` JSONB 以 `ImportedSpec.model_dump()` JSON 写入；读取经 pydantic 反序列化，返回的 ImportedSpec 与内存档逐字段一致（含 created_at ISO 字符串）。
- `delete` 返回 `rowcount > 0`（幂等；不存在 false → HTTP 404 语义不变）。
- **reset 不清**：照 connections/channel 绑定/审计先例，demo reset 不清除本表（docs/42 §1 B 既定语义；reset_tenant 不触此 store）。

### C. 装配（`iam/registry.py`）

- PG 档 `_create_services`：`openapi_imports=PgImportStore(backend.engine, tenant_id)`（替换 `ImportStore()`）；内存档不动。
- TenantServices 字段类型由具体类 `ImportStore` 放宽为结构类型（`ImportStore | PgImportStore`，或同包 Protocol）；调用方只有 main.py 四个端点与一处发现构建，均只用 add/list/get/delete。
- `/api/adapters` 合并点零改动：每次请求 `services.openapi_imports.list()` → ImportedApiHarnessAdapter 动态注册（main.py:345）。因此 PG 重启后**无需启动预热**，首个发现/运行请求即从 PG 行重建适配器；工具全名仍是 `openapi:{spec_id}/{operation}`。

### D. 不变项（零改动验证）

- REST 五端点路径/鉴权/响应字段（docs/42 §1 C）不变；错误码集合不新增。
- 前端 OpenApiImports 页、apiClient、i18n、Dashboard 入口、编辑器零改动。
- 执行链路（ImportedApiHarnessAdapter → HttpApiClient → EgressGuard）零改动。

## 2. 非目标

- 原始 OpenAPI 文档留存与「重新预览」；导入去重/内容 hash；软删除与回收站；跨租户市场共享（D3/D25）。
- 导入规格的多实例缓存/失效广播（PG 为读穿透，本批不做进程内缓存）。
- YAML、securitySchemes、multipart 等 docs/42 §2 既有非目标（仍缓做 D22）。

## 3. Schema 契约（03 同步）

- 03 `openapi_import` 契约补注：v1 存储两档——内存档进程内、PG 档表 `openapi_imports`（迁移 018）；字段形状不变，正文权威在本文＋docs/42。
- docs/42 §2「导入规格 PG 持久化」非目标行加注：**2026-09-23 随 docs/43 取回（PG 档；内存档保留）**。
- 无 DB 之外的 Schema 变化；无新错误码。

## 4. 运行时语义

- PG 档：跨重启、跨实例（同库）导入规格与工具发现保持；重启后旧图中 `openapi:*` 引用不再因重启失效（删除导入仍报 UNKNOWN_CAPABILITY，docs/42 §4 语义不变）。
- 并发：两实例同时导入各自走 nextval 唯一 id；5 份上限以 COUNT 校验（并发撞限两请求均可能通过 COUNT 而超 1 行——Demo 规模接受，文档注记；严格配额随平台化）。
- 内存档为默认/测试后端，语义与数字完全不变。

## 5. 测试矩阵（13 落号）

- PG 集成（`tests/test_openapi_imports_pg_integration.py`，skipif 无 DATABASE_URL，~7）：add 往返逐字段（JSONB 反序列化）、seq id 与 list 排序、get 跨租户 404、delete 幂等 false→true、5 份与 200 ops 上限 OPENAPI_LIMIT_EXCEEDED、reset_tenant 不清、模拟重启（新 PgImportStore 实例读同库）数据仍在。
- 内存档：docs/42 既有用例全绿即回归保证（1352 基线不变；默认跑新增 PG skip 计数）。
- 浏览器冒烟（PG 档后端，≥1 截图）：导入 → 重启后端 → 列表与工具选择器仍在 → 配置运行仍 SUCCESS（HTTP 200）；控制台无新增错误。

## 6. 原子序

1. docs-only 立项：本文＋03 注记＋docs/42 §2 注记＋08＋14（D22 尾注）＋00 地图＋handoff＋CHANGELOG；
2. `feat(openapi): persist imported specs in postgres with two-tier store` ＋迁移 018＋PgImportStore＋装配＋PG 集成测试；`.venv/bin/pytest`；
3. docs 收口：PG 档浏览器冒烟（≥1 截图）＋CHANGELOG＋handoff/08/43 落码注记。

## 7. 落码注记（2026-09-23 收口）

- 三原子序全部完成：①docs 立项 `f39d86b` → ②迁移 018＋PgImportStore＋PG 档装配 `7bf13f5` → ③docs 收口（本步）。均在 dev、未 push。
- 收口门：后端内存档 **1352 passed / 56 skipped**（立项 48 skip，新增 8 PG 集成在无 DATABASE_URL 时 skip，内存数字零回归）；PG 直连（atlas-pg，ATLAS_RUN_INTEGRATION=1 DATABASE_URL='postgresql+psycopg://atlas:atlas@localhost:5432/atlas' pytest tests/test_openapi_imports_pg_integration.py）**8 passed**：add 往返逐字段（JSONB 反序列化 model_dump 全等）、skipped operations 不入表、seq id 与 list 排序＋模拟重启（新 store 实例读同库）、跨租户 get None、delete false→true、5 份与 200 ops 双上限 OPENAPI_LIMIT_EXCEEDED 422、reset_tenant 不清。
- 浏览器冒烟（PG 档后端，2 截图 docs/smoke-shots/openapi-pg-smoke-*）：API 导入 `openapi-768`（全局 seq 当值 768）→ **重启后端** →「API 导入」页列表仍在、`/api/adapters` 自动重建 `openapi:openapi-768`（无预热）→ 编辑器 tool_call 切 `openapi:openapi-768/list_pets`（read·幂等），limit 参数由 M3 Schema 内核自动渲染，填 10 编译运行：`tool_call-1` **action_status SUCCESS，status 200**，body `{"pets":[{"id":1,"name":"Rex"}]}`。控制台仅一次预期旧 token 401（重登后消失）。
- 冒烟缝（/tmp/atlas_smoke_launcher.py，假 DNS＋httpx MockTransport）在仓库外、未提交；收口后已恢复普通内存档 uvicorn 启动。
- 迁移 018 已对本地 atlas-pg 实跑（apply_migrations.py）；无新依赖、无 ADR、无新错误码；REST/前端/编辑器/执行链路零改动如约。
- **D22 部分取回、不解除**：YAML/Swagger2、securitySchemes 接线、multipart、原始文档留存、导入去重、软删除、跨租户共享、真实外部 API 联调仍缓做。
