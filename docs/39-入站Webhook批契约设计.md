# 入站 Webhook 批契约设计

> **立项**：2026-09-23（承接「不用管 git，你推任务」总授权；批选由 AI 判断）。
>
> **定位**：docs/38 真实渠道适配批落了 Shopify **出向** Admin API，但渠道事件入站仍缓做（D22）。M9 已有入站事件分桶 Router（routing 包，event 经登录态 run 载荷进入），但**没有公开免登录的 webhook 接收端**。本批在 T4/channels 之上落首个公开入站触发口：Shopify webhook HMAC 验签 → 按绑定订阅映射到图 → 后台异步触发图运行（经 M9 Router 钉版本）。**零新依赖、零外部资源**；新增 **ADR T29**（公开入站触发形态，10 文档 §4，三处同步 10→02→09）。
>
> **形状权威**：本文；01–08 规格冲突时以规格为准。
>
> **边界**：本批不解除 D22/D24——Shopify 侧 webhook 注册（出向 /admin/api/.../webhooks）仍需真实店铺、v1 仅人工在 Shopify 后台填写本系统 URL；重试调度依赖 Shopify 自身重试，不做 DLQ；Amazon SNS/IM 入站不在本批。

## 1. 范围

### A. 验签与投递内核（`src/atlas/channels/webhooks.py`）

- `verify_shopify_hmac(raw_body: bytes, provided: str | None, client_secret: str) -> bool`：
  `base64(hmac_sha256(client_secret.encode(), raw_body))` 与 `provided` 经 `hmac.compare_digest`；provided 缺失/非 base64 → False；**不抛、不记 body/secret**。
- `WebhookEnvelope`：`{shop_domain, topic, webhook_id, triggered_at, data}`，从 headers＋已解析 JSON body 构造；必需头：`X-Shopify-Shop-Domain`、`X-Shopify-Topic`、`X-Shopify-Webhook-Id`（缺任一 → 调用点 400）。
- `SUPPORTED_TOPICS = {"orders/create", "orders/updated", "refunds/create"}`；
  `build_trigger_event(envelope) -> TriggerEvent`：`channel="webhook"`、payload 严格形状 `{topic, shop, data}`（data 为 Shopify 原始 body，orders/* 为订单对象、refunds/create 为退款对象；不做字段投影）。
- `WebhookDeliverer`：
  - 构造注入：tenant_registry、channel_store、secret_provider、enqueue/run_graph 可注入（默认真实接线）。
  - `deliver(tenant_id, binding, envelope)`：幂等检查（`X-Shopify-Webhook-Id` 每租户进程内 ring 200、惰性剔 1h 以上旧条；重复 → `{duplicate:true}` 不触发不审计）；取绑定的订阅列表，topic 无启用订阅 → `{ignored:true}`；逐条订阅：经 `routing_store.resolve(graph_id, tenant, event)` 钉版本，版本 None → warning 跳过（不造失败 run），有版本 → 后台提交图运行（与 M9 run 的 `event` 入参同形，异步、不 SSE）；投递内任何异常只 warning，调用方响应已先行返回。

### B. 订阅模型

- `WebhookSubscription`：`{topic: str, graph_id: str, enabled: bool = true}`；topic ∈ SUPPORT_TOPICS；同一绑定内 topic+graph_id 唯一；每绑定 ≤10 条。
- 存于 channel_bindings 新增列 `webhook_subscriptions JSONB NOT NULL DEFAULT '[]'::jsonb`（迁移 016，JSON 字符串，与 config 同构）。

### C. REST

| 方法/路径 | 鉴权 | 说明 |
|---|---|---|
| `POST /api/channels/hooks/shopify/{binding_id}` | **公开免登录** | 原始 body 验签入口；见下方状态码；include_in_schema=True 标注 public |
| `GET /api/channels/{binding_id}/webhooks` | read | 订阅列表投影 `{items:[{topic,graphId,enabled}]}` |
| `PUT /api/channels/{binding_id}/webhooks` | **administer** | 全量替换订阅；校验：topic 白名单、graph 属本租户且存在（不存在 → 404，不泄漏细节）、键唯一、≤10、enabled bool；坏形状聚合中文 422 |

公开入口状态码：
- `200 {received:true}` / `{duplicate:true}` / `{ignored:true}`（unsupported topic 或缺订阅均 ignored，防 Shopify 无意义重试）；
- `400` 缺必需头/body 非 JSON 对象；
- `401` HMAC 缺失或不匹配（中文 detail，不区分）；
- `404` binding 不存在（统一文案，不泄漏）；
- `503` 绑定连接 client_secret 信封缺失/解密失败（配置错误，触发 Shopify 重试）；
- 验签通过后**绝不**返回非 2xx（投递失败仅 warning，重试可能造成重复，幂等环兜底）。

### D. PG 持久化（迁移 016）

- `db/migrations/016_channel_webhook_subscriptions.sql`：`ALTER TABLE channel_bindings ADD COLUMN webhook_subscriptions JSONB NOT NULL DEFAULT '[]'::jsonb;`
- PgChannelStore：行读写补该列（get/list/save），save 默认保留原值；Repository/内存 ChannelStore 同步。
- PG 集成测试 +4：016 往返、默认 `[]`、租户隔离、reset 不清。

### E. 前端（Connections 页绑定卡扩展，不新增页面）

- ChannelBindingsCard 操作列加「Webhook」按钮（admin 可见）打开订阅 Modal：
  - 只读 webhook URL `{ATLAS_PUBLIC_URL||location.origin}/api/channels/hooks/shopify/{bindingId}`＋复制按钮；提示「Shopify 后台 → Settings → Notifications → Webhooks 手动创建，事件版本选 2025-01」；
  - 订阅列表行：topic Select（三选项中文标签）、graph Select（listGraphs 本租户图）、enabled Switch、删除；新增行；保存调 PUT；
  - 订阅数据在绑定卡 Table 不新增列（Modal 承载）。
- channels namespace 增补 webhook 段文案（zh 填实、en-US 空 `{}`）。

## 2. 非目标（写入 docs/14）

- Shopify 出向 webhook 注册 API（POST /admin/api/{ver}/webhooks.json）与自动签名 secret 读取——随真实店铺联调；
- 入站重试调度/DLQ/死信重放、投递速率限制（除幂等环）、webhook 投递成功率指标页；
- Amazon SNS/SQS、其他平台签名、IM/短信入站、通用 JSON 签名口；
- 幂等环与订阅 PG 外的持久化（幂等环进程内）；
- 同步等待图运行完成再响应（Shopify 5s 超时，v1 全异步）。

## 3. Schema 契约（03 同步）

- 03 新增 `channel_webhook` 契约：WebhookSubscription、公开入口请求/响应三态、必需请求头与错误码概览（正文权威在本文）。
- channel_binding（docs/38 契约）补字段 `webhook_subscriptions`（默认 `[]`）；引用该结构的位置同步防漂移。

## 4. 运行时语义（06 同步）

- 公开入口：路由取原始 body（`await request.body()`，验签必须在 JSON 解析前完成）；binding→tenant 定位不经 `TenantRegistry.peek` 惰性创建（未知 binding 404，防租户枚举）；client_secret 经绑定连接的 SecretProvider 信封现解密，不缓存。
- 异步投递于独立线程（同 SSE worker 模式），principal/tentant 显式透传，不依赖上下文变量；run 经 M9 Router resolve 后创建，RunRecord 可辨 `event.channel=webhook`。
- 审计：仅验签通过且非 duplicate 落一条 `channel.webhook_received:{topic}`（元数据：binding id/webhook id/shop，无 body）；订阅变更写操作经既有写操作审计中间件。
- 与既有能力门的层级：webhook 触发的图运行内工具调用仍走各自 permission/audit，不因来源公开而绕过。

## 5. 测试矩阵（13 同步，候选 U370 起）

| 文件 | 候选数 | 覆盖 |
|---|---|---|
| tests/test_channels_webhooks.py | ~12 | HMAC 成功/缺失/坏 base64/错 secret（compare_digest 口径）、必需头缺失、topic→event 映射三主题、unknown topic ignored、重复 webhook_id duplicate、resolve 无版本跳过、投递异常不抛（fake run_graph 捕获调用与 event 形状）、审计动作 |
| tests/test_api_channel_webhooks.py | ~8 | 公开入口 401/400/404/503、200 三态、GET 角色（全角色可）、PUT administer（operator/viewer 403）、坏 topic/重复/graph 不存在 422/404、订阅写入后公开入口按图触发 |
| tests/test_channel_bindings_pg_integration.py | +4 | 迁移 016 往返/默认/隔离/reset 不清（skipif 门控） |
| 前端 apiClient channels | +2 | getWebhookSubscriptions/putWebhookSubscriptions |
| 前端 webhook 纯逻辑＋i18n | ~3 | topic 选项/URL 构造、channels webhook 静态键与回退 |

立项基线（只许增测）：后端 **1220 passed/40 skipped**、前端 **577 passed/2 skipped/45 files**。

## 6. 环境与错误码

- 无新增必需环境变量；webhook URL 展示优先 `ATLAS_PUBLIC_URL`。
- 新错误码（HTTP 口径）：WEBHOOK_BAD_SIGNATURE(401)、WEBHOOK_MALFORMED(400)、WEBHOOK_SECRET_UNAVAILABLE(503)；响应体三态 received/duplicate/ignored。

## 7. 原子序

1. docs-only 立项：docs/39＋00/02/03/04/06/08/09/10/12/13/14＋handoff＋CHANGELOG（本步）。
2. `feat(channels): add shopify webhook verification and async delivery`（webhooks.py＋test_channels_webhooks.py）。
3. `feat(api): add public webhook ingress and subscription endpoints`（test_api_channel_webhooks.py）。
4. `feat(storage): add webhook subscriptions migration and pg store`（016＋集成 +4）。
5. `feat(frontend): add webhook subscriptions modal on bindings card`。
6. docs 收口：浏览器/HTTP 冒烟（openssl/hmac 签名真实投递、重复投递、未订阅 ignored；≥3 截图）＋CHANGELOG＋handoff/08/39 落码注记。

每代码原子后跑对应门；后端 `.venv/bin/pytest`，前端 `cd frontend && pnpm lint && pnpm test && pnpm build`。

## 8. 落码收口（2026-09-23）

六原子全部交付（③④实际顺序倒置：先 storage 后 api，零影响）：

| 原子 | 提交 |
|---|---|
| ② feat(channels) | `24076c9` |
| ④ feat(storage) 016 | `8313e7b` |
| ③ feat(api) | `3b19ac9` |
| ⑤ feat(frontend) | `695e5f2` |

实际测试落账：后端 **1252 passed/44 skipped**（1220 + 21 kernel + 11 API；+4 为新增 PG 集成 skip），前端 **579 passed/2 skipped/45 files**（+2 apiClient）；lint/build 通过。

真实 HTTP 冒烟（uvicorn 重启后 httpx 打 localhost:8000）：验签订阅投递 200 `received`、同一 webhook id 重放 200 `duplicate`、未订阅 topic（orders/updated）200 `ignored`、坏签名 401、未知绑定 404；被触发的图 graph-1 后台运行 `completed`。截图：[webhooks-smoke-1-bindings.png](smoke-shots/webhooks-smoke-1-bindings.png)、[webhooks-smoke-2-modal.png](smoke-shots/webhooks-smoke-2-modal.png)、[webhooks-smoke-3-two-rows.png](smoke-shots/webhooks-smoke-3-two-rows.png)。

落地偏差（均在 docs/39 设计内）：①API worker 以 `mode="webhook"` 独立记 run（失败落 failed，不回传 Shopify）；②AuditStore 无 metadata 列，`bindingId/webhookId/shop` 编码进审计 path（不含 body/密钥）；③canary 状态机要求两个发布版，发布即全量的钉版场景由 RoutingStore full 态承载。
