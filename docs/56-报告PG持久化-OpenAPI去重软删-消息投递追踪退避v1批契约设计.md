# 回放报告 PG 持久化 / OpenAPI 导入去重软删 / 消息投递追踪退避 v1 批契约设计

> 状态：**已落码收口（打包 C，2026-09-24）**。立项为 docs-only 批（AI 承接「一口气搞了」总授权），部分取回 docs/14 D26（录制回放余部）、D22（OpenAPI 导入余部）、D24（消息渠道余部）。**零新依赖**，新增 2 个迁移（022/023），**不新增 ADR、不解除 D22/D24/D26 缓做条目**。承接 docs/28（录制回放打包）、docs/42/43（OpenAPI 导入与 PG 化）、docs/35/51/52（消息/IM/告警通知）。

## 0. 已核实的现状缺口（2026-09-24 对活代码）

1. **ReleaseReport 未持久化**：`recording/reports.py` 的 `ReportStore` 是进程内 deque ring 100（reset 清空、重启即失），模块 docstring 自述「不进 storage Repository 抽象、不 PG 化（PG 随 D26 持久化批次）」；迁移 007/008 与 `PgRecordingStore` 已把**录制用例** PG 化（含 graph_id/subgraphs 富字段），**报告表不存在**（`grep release_report db/migrations` 无结果）。
2. **OpenAPI 导入无去重、硬删除**：`openapi/store.py`（ImportStore）与 `openapi/pg_store.py`（PgImportStore）的 add 不判重（同一份文档重复导入占名额，上限 5/租户），delete 为物理 DELETE；无 content hash、无软删/恢复。
3. **消息投递无追踪、失败即抛无重试**：`message/service.py` 成功才写 `_messages`，真实投递（smtp/webhook/im）失败直接抛 MessageSendError、不留失败痕迹；无投递历史 ring、无退避重试；陪同排查只能看到成功记录。

## 1. 范围与非目标

**范围（三项）**：

1. ReleaseReport 双档持久化：迁移 022 建 `release_reports` 表 + `PgReportStore`（与 ReportStore 同方法形状），registry PG 档装配；重启后回归报告历史/趋势不丢。
2. OpenAPI 导入内容指纹去重 + 软删除/恢复：迁移 023 加 `content_hash`/`deleted_at`；同租户未删规格指纹相同拒绝导入（409，带 existingSpecId）；DELETE 改软删，新增 restore 端点。
3. 消息投递日志 ring + webhook/im 退避重试：MessageService 记录每次投递（含失败）到 ring，新增只读 GET 端点；webhook/IM 网络类失败按可配延迟重试（默认 2 次），email 不重试、安全/参数错误不重试。

**非目标（仍缓做，不解除 D22/D24/D26）**：

- 录制用例/报告的多租户共享、报告长保留时序/趋势报表、影子模式线上自动旁路（随 D26 余部）。
- OpenAPI YAML 解析、oauth2/openIdConnect 安全方案、cookie 会话管理、真实外部 API 联调、token 轮换/KMS、Amazon 渠道、双向同步（随 D22 余部）。
- 短信渠道、富文本/@人、多 URL 群发、IM 应用 OAuth/入站消费、消息模板系统（多语言/富文本）、投递日志 PG 化与跨实例聚合（随 D24 余部）。
- 不新增前端页面（投递日志 v1 仅 REST；去重 409/恢复经现有 OpenAPI 管理面，前端按钮可后续补）。

## 2. ReleaseReport PG 持久化（迁移 022）

`db/migrations/022_release_reports.sql`：

```sql
CREATE TABLE IF NOT EXISTS release_reports (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    graph_id    TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT 'draft',
    trigger     TEXT NOT NULL,
    total       INTEGER NOT NULL,
    passed      INTEGER NOT NULL,
    failed      INTEGER NOT NULL,
    skipped     BOOLEAN NOT NULL,
    blocked     BOOLEAN NOT NULL,
    pass_rate   DOUBLE PRECISION,
    cases       JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_release_reports_tenant_graph
    ON release_reports (tenant_id, graph_id, seq);
```

- 新模块 `recording/pg_reports.py`：`PgReportStore(engine, tenant_id)`，方法形状与 `ReportStore` 完全一致（record/list_summary/list_all_summary/get/reset）。
- id 用全局 `storage_id_seq` 取 `rr-{seq}`（与内存档 rr-N 同形；同 PgImportStore 先例），数字部分另存 seq 供排序。
- list_summary / list_all_summary 的 SELECT **不取 cases 列**（摘要不含逐例详情）；get 取 cases；跨图/跨租户隔离照 tenant_id 过滤，不属于该图的 get 返 None（API 404，不泄漏）。
- 不做 ring 淘汰（PG 为持久化档，列表端点本就 limit/clamp；与 recordings 持久化语义一致）。
- reset：`DELETE FROM release_reports WHERE tenant_id = :t`（报告是运行产物，reset 清空，对齐内存档 ReportStore.reset；录制用例仍不清）。
- registry：PG 档 TenantServices.report_store 由 `ReportStore()` 换为 `PgReportStore(backend.engine, tenant_id)`；内存档不变。API 层（release-gate/publish-gate 两处 record、四个报告端点/导出）零改动（同形）。

## 3. OpenAPI 导入去重与软删除（迁移 023）

`db/migrations/023_openapi_imports_dedupe_softdelete.sql`：

```sql
ALTER TABLE openapi_imports ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE openapi_imports ADD COLUMN IF NOT EXISTS deleted_at TEXT;
CREATE INDEX IF NOT EXISTS idx_openapi_imports_hash
    ON openapi_imports (tenant_id, content_hash) WHERE deleted_at IS NULL;
```

### 3.1 内容指纹

- `openapi/models.py` 的 ParsedSpec 增 `content_fingerprint() -> str`：对稳定子集（title、base_url、security_schemes 的键与类型、operations 的 method/path/operationId/name/skipped 序列）做 `json.dumps(..., sort_keys=True, ensure_ascii=False)` 后 sha256 hex；不依赖导入时间/生成的 spec_id，同一文档重复解析必得同一指纹。
- ImportedSpec 纯超集增 `content_hash: str = ""`、`deleted_at: str | None = None`。

### 3.2 去重

- add（双档）：先算指纹，若同租户存在 **deleted_at IS NULL 且 content_hash 相同** 的规格，抛 `ImportStoreError("OPENAPI_DUPLICATE", "该 API 规格已导入（{existing_spec_id}：{title}）", status_code=409)`，异常对象带 `existing_spec_id`（新增属性，缺省 None）；API 层折算 409，响应体含 code/message/existingSpecId。
- 名额上限（5/租户、200 operation/份）只统计未删除规格。

### 3.3 软删除/恢复（双档同形）

- delete(spec_id)：未删除 → 置 deleted_at=当前 UTC ISO，返回 True；不存在或已删 → False（API 404）。不再物理删除。
- list/get/put_credentials：默认排除 deleted_at 非空（get 已删返 None→404，凭据不可改）。
- 新增 restore(spec_id) -> tuple[bool, str | None]：已删 → 清 deleted_at；若恢复后与另一**未删除**规格指纹冲突，返回 `(False, "OPENAPI_DUPLICATE")`（API 409 带冲突 existingSpecId）；不存在返 `(False, None)`（404）。
- REST 新增 `POST /api/openapi/imports/{spec_id}/restore`（权限 administer；404/409/200）。v1 不提供 include_deleted 列表与硬删除（保留数据，避免误删）。
- 内存档以字段模拟、PG 档以 deleted_at 列过滤；demo reset 不清本表（照现状先例）。

## 4. 消息投递日志与退避重试（D24，进程内、零迁移）

### 4.1 DeliveryRecord（message/service.py）

- MessageService 增 `_deliveries: deque[dict]`（ring `DELIVERY_RING_SIZE = 200`），记录每次 send 的投递结果：
  `{id, channel, to(数组，与消息记录同形), subject(截断 100), sentAt, status, attempts, elapsedMs, errorCode, errorMessage(截断 300)}`。
- status 取值：`in_process`（未配置真实 sender，demo 落盘）、`delivered:{smtp|webhook|dingtalk|wecom|feishu}`、`failed`。
- 旧契约保持：成功才写 `_messages`；真实投递失败仍抛 MessageSendError、**仍不写 _messages**，但**必写一条 failed DeliveryRecord**（含 attempts/errorCode/elapsedMs），可观测。
- list_deliveries(limit=100) 倒序返回；reset 清空；count 辅助测试。

### 4.2 退避重试（仅 webhook / IM）

- 构造增 `retry_delays: tuple[float, ...] = (0.5, 1.5)` 与可注入 `sleep_func`（测试用假时钟）、`max_attempts` 由 delays 推导（默认共投递 3 次）。
- **重试范围**：仅 webhook 与 dingtalk/wecom/feishu 的发送异常（WEBHOOK_SEND_FAILED/IM_SEND_FAILED，多为网络/超时/非 2xx，群机器人重复一条文本可接受）；`EgressDenied`（EGRESS_DENIED/EGRESS_INVALID_URL）与 MessageSendError 参数类（MISSING/INVALID）**立即抛出不重试**；email（smtp）分支**不重试**（避免重复邮件），失败照常记 failed。
- 每次尝试计入 attempts、累计耗时 elapsedMs；重试间隙调用 sleep_func(delay)；最终仍失败抛原 MessageSendError（契约不变），DeliveryRecord 记最后错误码与总 attempts。
- registry 装配 MessageService 时使用默认重试（email_sender/webhook_sender/im_sender 注入现状不变）；AlertNotifier 经 MessageService.send 透明获得重试，lifecycle 60s 限流仍在 notifier 层（docs/54）。

### 4.3 REST

- 新增 `GET /api/demo/deliveries?limit=`（read 权限，照 `/api/demo/messages` 同段），返回 `{"items": [...]}`，limit clamp 1-200；审计/租户隔离沿用现状。不新增写端点。

## 5. 测试（候选 U580 起，落码后转正式并回填 docs/13）

- PgReportStore（PG 直连集成，ATLAS_RUN_INTEGRATION=1 + DATABASE_URL）：record 落库、list_summary 不含 cases 且倒序、list_all_summary limit/clamp、get 含 cases 与 404/跨图跨租户隔离、reset 删本租户、id 同形 rr-N、total=0 时 pass_rate=None。
- ImportStore 双档：同指纹二次 add 抛 OPENAPI_DUPLICATE(409) 且 existingSpecId 正确、不同文档正常、软删后 list/get 不可见且名额释放、软删后可重新导入同指纹、restore 成功、restore 与未删同指纹冲突 409、删不存在 404；PG 直连验证 content_hash/deleted_at 列与部分唯一索引语义。
- MessageService：成功写 deliveries delivered 与 _messages；webhook 失败重试至次数上限后抛错、attempts/elapsedMs/失败日志、且不写 _messages；第二次尝试成功时 attempts=2 且写 delivered；EgressDenied 与参数错误不重试；email 失败不重试；in_process 记录形状；reset 清空；GET /api/demo/deliveries 权限与 clamp。
- 回归：现有 release-gate/publish-gate、openapi 导入/凭据、IM/webhook/email smoke（docs/51/52）全绿；三道门只许增测。

## 6. 契约同步矩阵（立项原子内完成）

- 03 契约索引：release_report 持久形状与 release_reports 表、openapi_imports 两列与去重/恢复错误码、message delivery 记录形状。
- 04/06：报告存储分层（gate 纯函数不变、record 在 API 层）、消息投递日志与重试语义。
- 09 模块映射：recording/pg_reports、openapi/store·pg_store、message/service 职责（无新包）。
- 12 REST：restore 端点、/api/demo/deliveries、openapi 409 响应体。
- 13 测试用例：U580 起正式编号。
- 14 缓做登记：D22/D24/D26 加「部分取回、不解除」注记。
- 00 文档地图、08 迭代计划、CHANGELOG 各一条。

## 7. 原子提交序（feat 与 docs(handoff) 分开，不 push）

1. `docs(reports): contract for PG release reports, openapi dedupe/soft-delete, delivery log (docs/56)`（本契约 + 矩阵）
2. `feat(recording): persist release reports in PG with migration 022`
3. `feat(openapi): dedupe imports by content fingerprint and soft-delete/restore with migration 023`
4. `feat(message): record delivery log and retry webhook/im sends with backoff`
5. `test(reports): close out docs/56 with PG integration and delivery tests`（收口门 + handoff/CHANGELOG/14）

## 8. 风险与回滚

- **指纹误判**：指纹只取解析后稳定子集，operation 顺序以文档为准（同文档稳定），不含时间/id；以双档单测固化「同文档同指纹、改 base_url/任一 operation 变指纹」。
- **软删/名额**：所有计数与查询显式带 deleted_at IS NULL，避免已删规格继续占名额或被 get 命中；restore 冲突走 409 不制造两条未删同指纹。
- **重试重复投递**：仅对幂等性可接受的群机器人/webhook 文本启用，email 不重试；安全拦截与参数错误立即失败；延迟可注入、默认值保守（0.5/1.5s）。
- **PG 报告与内存 ring 形状漂移**：PgReportStore 严格同形，API 层零改动；以同一组报告断言双跑（内存/PG）。
- 回滚：迁移只加表/加列不删数据；回滚代码后新列空值/新表闲置无害，MessageService 重试默认参数不影响旧调用（失败语义不变）。
