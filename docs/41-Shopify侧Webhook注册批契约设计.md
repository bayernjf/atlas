# Shopify 侧 Webhook 注册批契约设计

> **立项**：2026-09-23（承接「不用管 git，你推任务」总授权；批选由 AI 判断）。
>
> **定位**：[docs/39](39-入站Webhook批契约设计.md) 落了公开入站触发口，[docs/40](40-入站可靠性补强批契约设计.md) 补了可靠性，但 Shopify **店铺侧**的 webhook 注册仍靠人工复制 URL 到 Shopify 后台——docs/39/40 明确登记的缓做项。本批复用既有 `ShopifyChannelClient`（Admin API）与绑定访问令牌，补「注册 / 查询 / 取消注册」三个动作，用户在订阅弹窗内一键把回调地址注册到 Shopify。**零新依赖、零迁移、零外部资源、无选型变更（不新增 ADR）**；D22 再次部分取回、不解除（Amazon/通用渠道、OpenAPI 导入、真实店铺联调仍缓做）。
>
> **形状权威**：本文；01–08 规格冲突时以规格为准。
>
> **边界**：只做 Shopify Admin REST `webhooks` 资源（JSON 格式）；不做 webhook 签名密钥轮换、fields/include_filter、私有 app/API version 迁移；不自动同步远端与本地订阅（两个动作各自显式触发）；不改公开入口状态码契约（docs/39 §C 原样）。

## 1. 范围

### A. Client 扩展（`src/atlas/channels/shopify.py`）

- 新增方法（均经既有 `_request`，fake transport 可测、零触网）：
  - `list_registered_webhooks(limit=250) -> list[dict]`：`GET /webhooks.json`，投影 `{remoteId, topic, address}`（remoteId 取 `id` 字符串）；响应缺 `webhooks` 列表 → `CHANNEL_INVALID_RESPONSE`。
  - `register_webhook(*, topic, address) -> dict`：`POST /webhooks.json`，body `{"webhook": {"topic": topic, "address": address, "format": "json"}}`；投影 `{remoteId, topic, address}`。
  - `delete_registered_webhook(remote_id) -> bool`：`DELETE /webhooks/{id}.json` 成功 → True。
- 入参校验（先于出向调用）：
  - `topic` 必须属于 `SUPPORTED_TOPICS`（`orders/create`、`orders/updated`、`refunds/create`，与入站口同源常量），否则 `CHANNEL_INVALID_PARAMETER`。
  - `address` 必须是 `https://` 绝对 URL 且 path 精确等于 `/api/channels/hooks/shopify/{binding_id}`（host 不校验由 client 层做——client 不持 binding id，故地址形状校验在 registry 层，client 仅校验 https 与非空）。
- 状态码映射细化：`_request` 现把 ≥400 一律折 `CHANNEL_UPSTREAM_FAILED`；本批对 **422** 单独折 `CHANNEL_ALREADY_REGISTERED`（Shopify 对「同 topic+address 已存在」回 422；endpoint 映射 409）；401/403 仍 `CHANNEL_UNAUTHORIZED`。
- 构造新增可选 `base_url: str | None = None`：显式传入时覆盖默认 `https://{shop}.myshopify.com/admin/api/{ver}`——**仅开发/测试缝**（见 §D mock 冒烟），生产路径恒为默认。

### B. Registry 扩展（`src/atlas/channels/registry.py`）

- `remote_webhooks(binding_id) -> list[dict]`：取 client 调 `list_registered_webhooks`。
- `register_remote(binding_id, topic) -> dict`：
  1. `_require` 绑定，`topic` ∈ `SUPPORTED_TOPICS`；
  2. 地址由服务端拼装：`{ATLAS_PUBLIC_URL}{path}`（path＝`/api/channels/hooks/shopify/{binding_id}`；`ATLAS_PUBLIC_URL` 默认 `http://localhost:5174`，与审批通知同变量）；
  3. **地址钉版**：public_url 非 https 时拒绝（Shopify 要求 HTTPS）并 `CHANNEL_INVALID_PARAMETER`「回调地址必须为 HTTPS，请配置 ATLAS_PUBLIC_URL」——即开发默认值下注册会被明确挡下，不产生含糊上游错误；
  4. client.register_webhook，返回 `{remoteId, topic, address}`；
  5. `CHANNEL_UNAUTHORIZED` 时照 test() 先例落 binding error 态（status/last_error）。
- `unregister_remote(binding_id, topic) -> bool`：列出远端，按 **topic＋address 双键**找到本行注册项 → delete；找不到 → False（不依赖本地缓存的 remote id）。
- 绑删除（`delete`）不联动远端取消（非目标，文档提示先取消注册）。

### C. REST

| 方法/路径 | 鉴权 | 说明 |
|---|---|---|
| `GET /api/channels/{binding_id}/remote-webhooks` | read | `{items:[{remoteId,topic,address}]}`；绑定不存在 404；鉴权失败折 200？不——透传错误码（401 渠道令牌 → 200 `{items:[],error:"CHANNEL_UNAUTHORIZED"}`？见下注） |
| `POST /api/channels/{binding_id}/remote-webhooks` | operate | body `{topic}`；201 `{remoteId,topic,address}`；已注册 409 `CHANNEL_ALREADY_REGISTERED`；topic 非法 422；public_url 非 https 422；绑定不存在 404 |
| `DELETE /api/channels/{binding_id}/remote-webhooks/{topic}` | operate | 200 `{deleted:bool}`（未注册 false，幂等）；绑定不存在 404 |

- 注：GET 的渠道鉴权失败（店铺令牌失效）**不污染端点契约**——返回 200 `{items:[], "error":"CHANNEL_UNAUTHORIZED"}`，前端以 Alert 展示；绑定本身的 error 态同时由 registry 落库。
- 三端点经既有写操作审计中间件（POST/DELETE 自动落审计，path 模板）；公开入口与本地订阅端点零改动。

### D. Demo mock 缝（仅为离线/浏览器冒烟，不进生产路径）

- 新增同进程模拟外部系统端点（随 `/api/demo/**` 豁免平台鉴权）：
  - `GET/POST /api/demo/mock/shopify-admin/webhooks.json`、`DELETE /api/demo/mock/shopify-admin/webhooks/{id}.json`；内存 store（模块级 dict，demo reset 清空），POST 校验 `webhook.topic/address/format`、重复 topic+address 回 **422**，行为照 Shopify 契约裁剪。
- `build_channel_registry` 读 env `ATLAS_SHOPIFY_ADMIN_BASE_URL`：设置时 client 的 `base_url` 覆盖为该值，且传输用**不带 EgressGuard 的 HttpChannelTransport**（env allowlist 在私网恒拦之后判定，localhost 无法被放行，故只能显式绕守卫——与 egress `permit_cidrs` 同类的进程内测试缝）；未设置时生产路径原样（守卫在、默认 base）。
- `.env.example` 补注释样例；该变量不得在真实部署设置（doc 明示）。

### E. 前端（订阅弹窗内加段，不新增页面）

- `WebhookSubscriptionsModal` 增「Shopify 店铺侧注册」区：
  - 列出远端注册项（topic Tag＋address 截断），加载自 GET remote-webhooks；`error` 字段存在时 Alert；
  - 每个 `SUPPORTED_TOPICS` 行：已注册显示「已注册」Tag＋`取消注册`（operate）；未注册显示 `注册到 Shopify`（operate）；viewer 只读无按钮；
  - 不与本地订阅表自动联动（文案说明：先本地订阅 topic→graph，再注册到店铺）。
- apiClient：`listRemoteWebhooks/registerRemoteWebhook/unregisterRemoteWebhook` ＋类型；+3 vitest（URL/方法/body、409 不吞、DELETE 幂等返参）。
- i18n：channels namespace 增 `remoteWebhooks` 段（标题/列/按钮/错误提示/HTTPS 提示；zh 填实、en-US 保持 `{}`）。

## 2. 非目标（写入 docs/14 D22 注记）

- 真实 Shopify 店铺联调（注册后真实投递验证仍需真实店铺与公网 HTTPS 域名）；
- Amazon/其他平台注册、通用 webhook 注册器抽象；
- 注册/订阅自动双向同步、注册模板批量勾选、fields/过滤器配置；
- webhook 签名密钥在 Shopify 侧的轮换接口；
- 本地订阅 topic 集与远端注册集一致性巡检/告警（随 D28 告警）。

## 3. Schema 契约（03 同步）

- 03 新增 `shopify_remote_webhook` 契约：`{remoteId, topic, address}`、POST 请求 `{topic}`、422/409 口径、GET 的 `error` 可选字段（正文权威在本文）。
- 不改 `webhook_subscription`、`webhook_delivery` 既有结构；无 DB 迁移。

## 4. 运行时语义

- 注册调用发生在请求线程（同步 httpx，既有超时），无后台线程；失败不写任何本地状态（除 binding error 态）。
- 注册成功不触发任何图运行——仅 Shopify 侧开始向 address 投递；后续入站路径完全沿用 docs/39/40。
- 取消注册按 topic+address 双键匹配，删除其他工具/手工注册的同名项不受影响。

## 5. 测试矩阵（13 同步，候选 U380 起）

| 文件 | 候选数 | 覆盖 |
|---|---|---|
| tests/test_channels_shopify.py | +6 | fake transport：list 投影、register body/投影、delete、422→ALREADY_REGISTERED、topic 非法、非 https address |
| tests/test_api_channel_remote_webhooks.py | ~9 | GET 列表/error 字段、POST 201/409/422(topic)/422(https)/404、角色（viewer 403/operator 201）、DELETE 幂等 false→true、地址钉版 path |
| 前端 apiClient | +3 | GET/POST/DELETE URL、方法、body 与返参 |

立项基线（只许增测）：后端 **1276 passed/48 skipped**、前端 **581 passed/2 skipped/45 files**。

## 6. 原子序

1. docs-only 立项：docs/41＋00/03/12/13/14/08＋handoff＋CHANGELOG（本步）。
2. `feat(channels): support shopify admin webhook registration methods`（shopify.py 客户端方法＋内核测试）。
3. `feat(api): add remote webhook registration endpoints`（registry 方法＋REST＋demo mock 缝＋API 测试）。
4. `feat(frontend): add shopify registration controls to subscription modal`（apiClient＋UI＋i18n＋测试）。
5. docs 收口：mock 缝浏览器冒烟（≥3 截图）＋CHANGELOG＋handoff/08/41 落码注记。

每代码原子后跑对应门；后端 `.venv/bin/pytest`，前端 `cd frontend && pnpm lint && pnpm test && pnpm build`。
