# 打包 U 契约：出站消息 DLQ——失败投递可查可重放（D24 余部切片）

> 形状权威＝本文。落码与本文冲突时先改本文再改代码；只许增测，不改既有契约语义。
> 范围一句话：出站消息（email/webhook/IM）投递失败后，运营能**列出死信、按原内容人工重放**；
> 不做定时扫描、不做模板系统、不做自动重放调度。

## 0. 已核实前提（回代码，不是转抄）

- `MessageService`（`src/atlas/message/service.py`）：webhook/IM 网络类错误按 0.5/1.5s 退避重试共 3 次（`_RETRYABLE_CODES`），失败也落 `DeliveryRecord(status="failed")`；群发逐目标各一条失败记录（`to=[target]`）。**失败不写 `_messages`**。
- `DeliveryRecord`（service.py:38）字段：id/channel/to/subject/sentAt/status/attempts/elapsedMs/errorCode/errorMessage——**无 body**。`PgDeliveryStore`（`message/deliveries.py:69`）表 `message_deliveries`（迁移 025）同样无 body 列 ⇒ 失败通知今天**内容不可恢复**，没有重放入口。
- 现有读面：`GET /api/demo/deliveries`（`api/main.py:4394`，read，limit clamp 1–200，倒序）；`MessageService.list_deliveries` 委托 store.list。前端**无**投递日志页（仅 API 消费）。
- 注意别搞混：docs/40（迁移 017 `webhook_deliveries`）的"死信捕获＋人工一键重放"是**入站订阅**面；本批是**出站通知**面，两者表、服务、语义均不同。
- 迁移最新 **031**（rollout_states）；下一号 **032**。`002_storage.sql` 冻结基线不回登（docs/81 同口径）。
- D24 触发条件＝"出现真实通知/触达业务"。本批取回的是其中**不依赖真实渠道也成立**的半边（失败可观测/可恢复的运行时机制，demo sender 即可验）；模板系统、定时 DLQ 扫描、入站通用消费、IM 应用 OAuth 仍缓做，**D24 整体不解除**。

## 1. 决策（六条；改任何一条先改本文）

- **D-1 迁移 032 给 `message_deliveries` 加列 `body TEXT`（可空；`ADD COLUMN IF NOT EXISTS` 幂等）**。历史失败行 body 为 NULL：**不猜测、不用 subject 顶替**——对其重放明确失败（见 D-4），这是"冻结基线/存量数据"必须如实暴露的边界。
- **D-2 `DeliveryRecord` 增 `body: str = ""`（dataclass 带缺省，位置追加在末，兼容既有构造点）**；两档 store 持久化并读回 body；list 投影新增 `body` 字段（失败行即原通知正文，长度不截断——subject 有 100 上限、error 有 300 上限，body 是重放唯一权威副本，截断会让重放内容失真）。
- **D-3 store 增两个查询，两档同形**：①`list(limit, *, status: str | None = None)`——status 非空时按前缀/精确过滤：`failed`＝`status='failed'`；`delivered`＝`status LIKE 'delivered:%'`；None＝全部（现语义）。投影同时补 `seq`（重放端点入参，此前 PG 查而不投影）。②`get(seq) -> dict | None`——`WHERE tenant_id=:t AND seq=:seq`（**任意 status**；service 据此区分「无行→404」与「非 failed→409」，单一 `get_failed` 无法分辨这两态）。内存档在 deque 上同规则过滤，不靠 seq 之外的字段。
- **D-4 `MessageService.replay_failed(seq) -> dict`**：经 store.get 取行；**无行（含跨租户）返 None**，API 映射 404；行在但 `status != 'failed'` → 抛 `MessageSendError("DLQ_NOT_FAILED", ...)`（API → 409）；行 body 为 None/空 → 抛 `MessageSendError("DLQ_BODY_UNAVAILABLE", ...)`（API → 422）。重放＝以存储的 channel/to/subject/body 走**现有 send 全路径**（校验、重试、逐目标投递日志全部生效），产生**新 message_id 与新投递行**；原 failed 行**原样保留不可变**（死信历史不就地改写）。返回 `{replay_of: seq, deliveries: [...]}`（本次重放新产生的投递行投影，倒序）。
- **D-5 REST 变化两处，零新错误码体系（复用结构化 code 字符串）**：①`GET /api/demo/deliveries` 增可选 `status: str | None = None`（只接受 failed/delivered；其他值 → 422 `INVALID_PARAMETER`；鉴权仍 read）。②新 `POST /api/demo/deliveries/{seq}/replay`（鉴权 **operate**）——seq 跨租户/无此行 → **404**（不泄漏）；该行非 failed → **409** `DLQ_NOT_FAILED`；body NULL → **422** `DLQ_BODY_UNAVAILABLE`。**不设重放幂等键**：每按一次真发一次（与现 send 语义一致），文档明示，防呆只靠 UI/调用方。
- **D-6 零新依赖／无新 ADR**（沿用 send 既有机制与 per-tenant store 模式）；前端本批**不动**（无现成投递页，API 先立；控制台页随未来真实运营需求，登 14 余部）；reset/ring 200 裁剪语义不变；不解除单副本三道闸与任何缓做。

## 2. 形状

- 迁移 `db/migrations/032_message_delivery_body.sql`：首行注释块（成因/可空/存量 NULL 语义/幂等）＋`ALTER TABLE ... ADD COLUMN IF NOT EXISTS body TEXT`＋列 COMMENT。
- `message/deliveries.py`：`InMemoryDeliveryStore.list(limit, *, status=None)`＋`get(seq)`（任意 status；内存 deque 需保留 seq——内存记录现无 seq，补一条仅内存用的实例计数，从 1 起单调）；`PgDeliveryStore` 同两方法，`_COLS`/INSERT/_project 补 body，list 投影补 seq。
- `message/service.py`：`DeliveryRecord.body`；`list_deliveries(limit, *, status=None)` 透传；`replay_failed(seq)`。
- `api/main.py`：`demo_deliveries(..., status=None)`；新 `demo_replay_delivery(seq)`。

## 3. 契约同步矩阵（收口时逐项回填）

- [ ] `docs/03`：投递记录投影补 `body`；DLQ 两端点登记。
- [ ] `docs/09`：无新文件（模块内演演进），必要时补注记。
- [ ] `docs/12`：REST 表 deliveries 行更新＋replay 行（鉴权/404/409/422）。
- [ ] `docs/13`：U958–U963 登记。
- [ ] `docs/14 D24`：追记「2026-09-29 取回出站 DLQ 半边（不解除本条）」。
- [ ] `docs/08 §八`：C 组 D24 行更新＋立项/收口注记。
- [ ] `docs/00` 文档地图：docs/82 行。
- [ ] handoff（Project documents＋Recently shipped，旧条目滚入归档）、CHANGELOG。

## 4. 测试与验收（U958 起；落常跑，失败行用假 sender 制造；PG 部分另挂 integration）

- **U958** 两档 store：失败行 body 持久化/读回；list 的 status 过滤（failed 只含 failed、delivered 只含 delivered:* 、None 全含）；非法 status 由 API 层 422。
- **U959** replay 成功路径：sender 先失败（failed 行），换可成功 sender 调 replay_failed ⇒ 新 delivered 行、原行仍 failed；新行 subject/body/to 与原行一致。
- **U960** 错误映射：seq 不存在 → 404、跨租户 → 404；非 failed 行 replay → 409；历史 NULL body → 422（service 抛 DLQ_BODY_UNAVAILABLE）。
- **U961** PG 集成：真 PG 下 body 落列，新连接 get_failed/重放后读到新行；原行不可变。
- **U962** 端点权限与过滤：viewer 可 GET（含 status 过滤）、POST replay → 403；operator 可重放；非法 status → 422。
- **U963** 群发失败重放：构造 2 目标群发、目标各自 failed（to 单元素），分别 replay 只发给该目标，不波及其余目标。
- 复跑：既有 message 相关测试零改动语义（body 带缺省）；`tests/test_handoff_integrity.py` 绿。

## 5. 原子序

1. `docs(message): specify the outbound dead-letter queue and replay contract`（本文＋03/09/12/13/14/08/00 同步登记）。
2. `feat(message): persist delivery bodies and add dead-letter replay`（迁移 032＋两档 store＋service＋端点）。
3. `test(message): cover dead-letter filtering, replay and error mappings`（U958–U963）。
4. `docs(message): close out the outbound DLQ landing`（收口回填＋handoff＋CHANGELOG）。

## 6. 残余风险（别当成「D24 收干净了」）

- 重放无幂等键：真实渠道下重复按＝重复通知/重复 webhook（下游可能重复入账）；调用方责任，本批不假装解决。
- body 明文落库：通知正文可能含业务信息（仓库无字段级加密约定，随 D22）；不做加密。
- 不做定时扫描/自动重放（D24/D14 余部）：失败恢复仍靠人发现；无前端页，纯 API 消费者。
- 存量失败行 body NULL 永久不可重放（迁移不回填——内容本就不存在）。
