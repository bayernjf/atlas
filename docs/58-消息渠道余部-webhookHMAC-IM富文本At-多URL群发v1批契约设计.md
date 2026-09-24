# 消息渠道余部：webhook 出站 HMAC 签名 / IM 富文本与 @人 / 多 URL 群发 v1 批契约设计

> 状态：**立项（docs-only，2026-09-24）**。AI 承接「按你建议的一口气搞」总授权（打包 E），部分取回 docs/14 D24（消息适配器真实投递余部）。承接 [docs/51](51-IM机器人消息投递v1批契约设计.md)（IM 群机器人 text v1）、[docs/52](52-监控告警外部通知v1批契约设计.md)（告警外发 v1）、[docs/56](56-报告PG持久化-OpenAPI去重软删-消息投递追踪退避v1批契约设计.md)（投递日志与退避重试）。**零新依赖、零迁移、无 ADR、后端为主**；**不解除 D24**（短信、IM 应用 OAuth/入站、模板 CRUD/多语言、DLQ、投递日志 PG 化仍缓做）。

## 0. 已核实的现状缺口（2026-09-24 对活代码）

1. **webhook 无出站签名**：`message/webhook.py` 的 `DefaultWebhookSender.send(url, payload)` 仅 EgressGuard→httpx.post(json=payload)，无 HMAC；`MessageService._validate_secret` 只允许 dingtalk/feishu 传 secret，**webhook 传 secret 直接 INVALID_PARAMETER**；payload 固定 `{id,channel,subject,body,sent_at}`，httpx `json=` 默认序列化（`, `/`: ` 分隔、ensure_ascii 转义）。
2. **IM 仅 text 单 URL**：`message/im.py` 的 `build_payload` 三家只构造 text；钉钉 URL query HMAC-SHA256 加签、飞书空 key body HMAC 加签已具备（docs/51），企微不加签（webhook URL 自带 key，官方无加签安全设置）；`ImSender.send(channel,url,text,secret)` 无富文本/@人参数。
3. **webhook/IM 强制单 URL**：`MessageService.send` 对这四类渠道收到 list 形态 `to` 一律 INVALID_PARAMETER（adapter input_schema 的 oneOf 已允许数组但运行时拒绝）；仅 email 支持群发。
4. 退避重试（docs/56，0.5/1.5s 共 3 次）挂在单目标 `_transmit` 内；DeliveryRecord ring 200 已具备。**本批不重复做重试**。

## 1. 范围与非目标

**范围（三项）**：

1. **webhook 出站 HMAC-SHA256 签名**：webhook 渠道开放 secret（签名密钥）；发送方确定性序列化 body，附时间戳与签名头，接收方可验签、可防重放。
2. **IM markdown 富文本与 @人**：dingtalk/wecom/feishu 新增 `msgFormat=markdown` 与 `mentions`（userIds/mobiles/atAll）；text 模式同步支持 @人。
3. **多 URL 群发**：webhook 与三类 IM 的 `to` 接受 1–20 个 URL 数组，逐目标独立 SSRF 校验与退避重试，per-URL DeliveryRecord 可见。

**非目标（仍缓做 D24，不解除）**：

- 短信渠道；IM 应用模式（app_id/app_secret OAuth、tenant_access_token）、入站消息消费/订阅型 IM 触发；
- 消息模板 CRUD、模板多语言、DLQ/定时重投调度；DeliveryRecord PG 化与跨实例聚合（docs/56 已注明随持久化批次）；
- 飞书 interactive 卡片（本批飞书富文本走 post 结构化段落，不做卡片 JSON 2.0）；钉钉 ActionCard/FeedCard、企微 template_card/news/image/file/voice；
- AlertChannel 多目标配置化（本批只交付 message/send 能力；监控告警渠道表单是否开放多 URL/富文本/@人随告警渠道产品化批次，不在本批）；
- webhook 签名密钥的 KMS/SecretProvider 管理（沿用现有 secret 运行时参数形态，与 IM 加签密钥同构）。

## 2. webhook 出站 HMAC 签名契约

### 2.1 头与算法（Atlas 出站约定）

- 密钥：`secret` 参数（≤200 字符，沿用 MAX_SECRET_LENGTH），webhook 渠道由「拒绝」改为「允许」；为空则不签名（向后兼容，v1 未签名接收方不受影响）。
- 确定性序列化：`raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")`（紧凑、UTF-8 原生中文；与旧版 httpx 默认序列化的字节不同但 JSON 语义等价，**有意变更**，Content-Type 显式 `application/json; charset=utf-8`）。
- 时间戳：`timestamp = str(int(clock()))`（UTC 秒；clock 可注入，与 IM 同构）。
- 签名基串：`f"{timestamp}\n{raw.decode('utf-8')}"`（GitHub webhook 通行形态），`digest = hmac.new(secret.encode(), base.encode("utf-8"), sha256).hexdigest()`。
- 请求头：
  - `X-Atlas-Timestamp: <timestamp>`
  - `X-Atlas-Signature: sha256=<hexdigest>`
- 无 secret：两个头均不发送。多 URL 群发时同一 payload（含同一消息 id，天然幂等键）对每个目标用同一密钥各算一次签名。
- 验签方实现（文档告知接收方，不在本仓库实现）：以相同方式拼接收原始 body 与时间戳 HMAC 比对，并以时间戳新鲜度窗口防重放（建议 5 分钟，接收方自定）。

### 2.2 接口演进

- `WebhookSender.send(url, payload, secret=None)`（Protocol 与 Default 同步；新增 clock 注入参数，默认 time.time）。
- 发送由 `json=payload` 改为 `content=raw, headers={Content-Type, 签名头...}`，保证「签名字节 == 发送字节」。
- EgressGuard 位置不变（签名前校验 URL）。

## 3. IM 富文本与 @人契约

### 3.1 统一入参（service/adapter 层抽象，渠道无关）

- `msgFormat: "text" | "markdown"`，缺省 `"text"`（完全向后兼容）。
- `mentions`（可选）：`{"userIds": string[], "mobiles": string[], "atAll": boolean}`，缺省 `{}`；userIds/mobiles 各 ≤20（与 MAX_RECIPIENTS 同值），元素去空白、去重保序；`atAll=true` 时 userIds/mobiles 仍允许（平台各自处理，一般 atAll 覆盖）。
  - **userIds 语义随渠道**：钉钉=userId（atUserIds）；企微=企业 userid（mentioned_list/`<@userid>`）；飞书=open_id 或 user_id（机器人 at 标签 user_id 字段，官方两值均接受）。
  - **mobiles 语义**：钉钉 atMobiles、企微 mentioned_mobile_list；**飞书自定义机器人无手机号 @能力，mobiles 在飞书渠道忽略**（不报错：跨渠道统一 schema，调用方无法预知渠道能力，忽略并在交付文档注明）。
  - 飞书 at 展示名：mentions 不携带姓名，post 的 `tag:at.user_name` 与 text 内联标签的展示名统一用 id 本身兜底（官方 user_name 非必填）。

### 3.2 三家消息体（官方文档事实，逐字节落测试向量）

**钉钉**（来源：钉钉开放平台《消息发送与接收类型》open.dingtalk.com/document/development/robot-message-type、《自定义机器人发送群消息》open.dingtalk.com/document/development/custom-robots-send-group-messages）：

- text：`{"msgtype":"text","text":{"content": <subject\n\body 末尾拼 @标识>},"at":{"atMobiles":[...],"atUserIds":[...],"isAtAll":bool}}`
- markdown：`{"msgtype":"markdown","markdown":{"title": <subject>, "text": <body 末尾拼 @标识>},"at":{同上}}`；title 必填（首屏会话透出），取 subject（本渠道 subject 必填，service 已校验）。
- 官方约束：**text 内容里必须出现 `@手机号` 或 `@userId` 才有实际 @效果**（非群成员手机号会被脱敏）；构造时 text/markdown 文本末尾自动拼 `@m1 @m2 @uid1 ...`（atAll 时不拼）。
- markdown 仅支持语法子集（标题/引用/加粗斜体/链接/图片）。

**企业微信**（来源：企业微信开发者中心《群机器人消息推送配置说明》developer.work.weixin.qq.com/document/path/91770）：

- text：`{"msgtype":"text","text":{"content":...,"mentioned_list":[userid...,"@all"?],"mentioned_mobile_list":[mobile...,"@all"?]}}`；atAll 时两列表各追加字符串 `"@all"`（官方 text 参数表）。
- markdown：`{"msgtype":"markdown","markdown":{"content": <body，末尾拼 @标签>}}`；**markdown 消息体没有 mentioned_*字段**，官方明确「text/markdown 类型消息支持在 content 中使用 `<@userid>` 扩展语法来 @群成员（markdown_v2 不支持）」：构造时 content 末尾拼 `<@userId> <@userId>`；atAll 拼 `<@all>`。
  - 置信度说明：`<@userid>` 为官方文档明文；`<@all>` 在官方 markdown 小节未直接列出示例（text 参数表的 `"@all"` 为官方明文），`<@all>` content 写法为社区一致用法（掘金/CSDN 多篇实施文档），测试只断言我方构造字节、不做真实联调；**markdown 模式下 mobiles 无官方字段可用，忽略并记录限制**。
- markdown content ≤4096 字节（官方），本批不做长度截断（超长由平台报错，经 IM_SEND_FAILED 上抛；长度预检缓做）。

**飞书**（来源：飞书开放平台《自定义机器人使用指南》open.feishu.cn/document/client-docs/bot-v3/add-custom-bot）：

- text：`{"msg_type":"text","content":{"text": <subject\n\body + 内联 at 标签>}}`；@人内联 `<at user_id="ou_xxx">ou_xxx</at>`，@所有人 `<at user_id="all">所有人</at>`（官方明文）。
- markdown 模式落 **post 富文本**（非 markdown 语法渲染）：
  ```json
  {"msg_type":"post","content":{"post":{"zh_cn":{"title": <subject>,
    "content": [[{"tag":"text","text": <每行>}], ..., [{"tag":"at","user_id": <id>}, ...]]}}}}
  ```
  - body 按行拆分，每个非空行一个段落 `[{"tag":"text","text": line}]`（空行跳过，避免空段落）；@人追加为最后一个段落的若干 `{"tag":"at","user_id": id}`（user_name 省略）；atAll 追加 `{"tag":"at","user_id":"all","user_name":"所有人"}`。
  - **限制（记录）**：post 富文本不渲染 markdown 语法符号（`**`、`#`、`[ ]()` 会按文本显示）；需要完整 markdown 渲染的渠道呈现应走 interactive 卡片（缓做）。post 是官方「富文本消息」，满足标题/分段/链接（tag:a，本批不转换）/@人。
- 加签不变：secret 存在时顶层 `timestamp`（秒）/`sign`（空 key HMAC，docs/51 已实现），与 msg_type 无关。

### 3.3 接口演进

- `ImSender.send(channel, url, subject, body, secret=None, msg_format="text", mentions=None)`（替换原 `(channel,url,text,secret)`；text 拼接由 builder 内部完成）。
- `build_payload(channel, subject, body, timestamp, secret, msg_format, mentions)` 按 §3.2 构造；新增纯函数 `normalize_mentions(value)`（service 层校验复用）。
- service 层 IM 分支不再预拼 `f"{subject}\n{body}"`，原样传入 builder。

## 4. 多 URL 群发契约

- `to` 为数组时 webhook/IM 放开：1–20 个 URL（沿用 `_normalize_recipients` 与 MAX_RECIPIENTS；单字符串形态不变）。URL 形态校验仍由 EgressGuard 在发送时逐目标执行。
- 执行：
  1. **安全 fail-fast**：逐目标发送，遇 `EgressDenied` 立即上抛（EGRESS_DENIED/EGRESS_INVALID_URL 不重试、不继续后续目标——非法/内网目标不应触发任何外发）。
  2. **投递错误 best-effort**：某目标网络/平台错误（WEBHOOK_SEND_FAILED/IM_SEND_FAILED）在该目标的 `_transmit` 内完成 docs/56 退避重试；仍败则记录该目标失败并继续其余目标（通知应尽可能触达）。
  3. **DeliveryRecord per-URL**：每个目标一条记录（`to=[该url]`、各自 attempts/status/errorCode）；demo/in_process 模式一条聚合记录（to=全部、status=in_process）。
  4. **全部成功**才写 `_messages` 一条（to=全部 URL、delivered=渠道名）并返回；任一投递失败则不写 `_messages`，全部目标尝试完后抛 `MessageSendError(<渠道码>, 聚合错误信息含失败数/URL 数)`；`last_send` 仅全成时更新（现状语义）。
- 群发层不做跨目标整体重试（会对已成功目标重复通知）；重试只发生在单目标 transmit 内（docs/56 机制原样复用）。
- email 群发语义不变（SMTP 一次投递多收件人）。

## 5. Capability 契约（adapter.py input_schema 演进）

- `to` 描述更新：webhook/IM 支持 1–20 个 URL 的数组（群发）；oneOf/maxItems 现状已具备，仅改描述。
- 新增 `msgFormat`：`{"type":"string","enum":["text","markdown"],"description":"IM 渠道消息格式，缺省 text；webhook/email 忽略"}`。
- 新增 `mentions`：
  ```json
  {"type":"object","additionalProperties":false,
   "properties":{"userIds":{"type":"array","items":{"type":"string"},"maxItems":20},
                 "mobiles":{"type":"array","items":{"type":"string"},"maxItems":20},
                 "atAll":{"type":"boolean"}},
   "description":"IM @人：userIds 随渠道（钉钉 userId/企微 userid/飞书 open_id|user_id），mobiles 仅钉钉/企微 text，飞书忽略 mobiles；webhook/email 忽略"}
  ```
- `secret` 描述更新：dingtalk/feishu 为机器人加签密钥、**webhook 为 HMAC 签名密钥**、wecom 不支持。
- output_schema 不变。service.send 签名增 `msg_format=None, mentions=None`（关键字、默认值保证旧调用方零改动；AlertNotifier 等现有调用不传）。

## 6. 测试契约（候选 U626–U645，docs/13 回填）

沿用 docs/51 假机器人注入 post、**逐字节断言请求体/URL/头**，不真实联调：

1. webhook HMAC：固定 clock（如 1700000000）与固定 secret/body 的签名头向量（hexdigest 先离线算准后固化）；无 secret 无头；确定性序列化（紧凑/UTF-8 中文不转义）；签名覆盖字节 == 发送字节（注入 post 捕获 content 重算比对）；多 URL 同 id。
2. 钉钉：markdown title/text/at 三块固定向量；text atMobiles/atUserIds/isAtAll 与正文 @拼串；加签 URL 与 markdown 组合回归。
3. 企微：text mentioned_list/mentioned_mobile_list/`"@all"`；markdown content 内 `<@id>`/`<@all>` 拼串且无 mentioned_*字段；markdown+mobiles 忽略的限制用例。
4. 飞书：text 内联 at 标签（含 all）；post 标题/按行分段/末段 tag:at（含 all/user_name）；secret 加签与 post 组合。
5. mentions 归一化：空白/去重保序/上限 422/非数组 422/atAll 布尔；msgFormat 非法值 422；webhook/email 忽略 msgFormat/mentions 不报错。
6. secret 校验：webhook 允许 secret（原 INVALID 用例改期望）；wecom 传 secret 仍 INVALID；超长 422。
7. 多 URL：2 目标全成（_messages 一条、to 两 URL、两条 delivered record、同 id）；一成一败（不写 _messages、聚合错误码、失败 record attempts=3、成功目标 record 保留、继续发送证据）；EGRESS fail-fast（首目标内网即抛、次目标未发送）；demo 模式数组一条 in_process；email 数组回归。
8. 现有 docs/51/52/56 用例全绿（send 新参数有默认值，旧断言零改动预期；build_payload 签名变化处同步夹具）。
9. HTTP smoke：扩展 `.smoke/im_robot_smoke.py`（git add -f）——Layer A 假机器人逐字节向量新增 markdown/@人/webhook HMAC；Layer B loopback SSRF 护栏与数组/secret 校验回归。

## 7. 门与冒烟

- 后端：`.venv/bin/pytest` 全量（只许增测）；PG 集成本批零迁移不强制（若跑沿用 docs/56 命令）。
- 前端：本批**预期零改动**（schema 驱动表单自动呈现 msgFormat/mentions；若 FormRenderer 对 mentions 嵌套对象渲染失败或报错，再做最小前端修正并补 vitest）；收口跑 vitest+oxlint+build 三道门确认零回归。
- 浏览器冒烟：消息节点工具表单出现 msgFormat/mentions 字段、secret 在 webhook 渠道可填（截图 1–2 张）；不做真实平台联调。

## 8. 契约同步矩阵（立项原子内完成）

- docs/51：顶部加「余部见 docs/58」注记（富文本/@人/多 URL 已由 docs/58 取回）；
- docs/14 D24：行尾加本批部分取回注记（不解除）；
- docs/08：立项条（含 webhook HMAC 为 Atlas 自定义出站约定、企微 markdown `<@all>` 置信度两条记录）；
- docs/00 文档地图加 docs/58；docs/03 核对 message/send schema 条目（以 04/12 正文为权威，若索引了 message 入参则同步）；
- docs/12（API/模块接口清单）：MessageService/ImSender/WebhookSender 签名若被索引则同步；
- docs/13：U626–U645 登记（收口回填实测）；CHANGELOG、handoff（立项+每原子）。

## 9. 原子提交序（feat 与 docs(handoff) 分开，英文 message，不 push）

1. `docs(message): contract webhook HMAC, IM rich-text/mentions and multi-URL fan-out (docs/58)` — 立项（本文件 + 08/14/00/51/03/12 + handoff/CHANGELOG）。
2. `feat(message): sign outbound webhooks with HMAC-SHA256 (docs/58)` — webhook secret 开放、确定性序列化、签名头、clock 注入 + 测试。
3. `feat(message): support IM markdown payloads and mentions (docs/58)` — builder 三家富文本/@人、normalize_mentions、service/adapter 入参 + 测试。
4. `feat(message): fan out webhook and IM sends to multiple URLs (docs/58)` — 数组放开、per-URL 记录、fail-fast/best-effort 语义 + 测试 + smoke 扩展。
5. （条件原子）前端最小修正（仅当 schema 驱动表单不支持新字段）。
6. `docs(message): close out webhook HMAC, IM rich-text and multi-URL fan-out (docs/58)` — 收口回填（13/14/08/00/03/12 + CHANGELOG + handoff）。

## 10. 风险与回滚

- **风险**：webhook 序列化由 httpx 默认改为紧凑 UTF-8，旧接收方若做了「字节级」而非「JSON 语义」比对会感知变化——Demo 期无已知接收方，且仅签名场景（新能力）启用新字节形态；未签名场景同样切换为确定性序列化（全渠道一致，避免同 payload 字节不稳定），JSON 语义等价。
- **风险**：企微 markdown `<@all>` 无官方直接示例——逐字节测试只锁定我方构造，真实效果以客户群实测为准；交付文档（TRIAL/渠道说明）中注明 text 模式的 mentioned_list `"@all"` 是官方明文路径，对 @所有人可靠性要求高的场景建议用 text。
- 回滚：纯后端增量（新参数全有默认值），回滚到上一 commit 即恢复 text/单 URL/无签名形态；无迁移、无数据形态变化。

---

## 落码收口注记（2026-09-24）

E-1/E-2/E-3 全部落码收口（E-3 由另一会话起、本会话接力验证）：

- E-1 webhook HMAC `83d373a`、E-2 IM markdown/@人 `4f2b39a`、E-3 多 URL 群发（feat 原子见 git log）。
- E-3 实现：`MessageService._fan_out(transmit_one, label)`——逐 URL 调单目标 transmit（单目标内退避重试原样复用 docs/56），per-URL DeliveryRecord（`to` 钉该单 URL）；`EGRESS_*` 首目标即抛 fail-fast（次目标不发），投递类错误 best-effort 发完其余、聚合抛「群发部分失败 N/M」；全成才置 delivered 并写 `_messages`，部分失败不写。webhook 同一 payload（含同 id 幂等键）对每目标用同一密钥各签一次。
- 验证：message IM/webhook 两文件 **96 passed**；后端全量内存门 **1713 passed / 69 skipped**（E-2 1709 净增 4，零失败，以收口实跑为准）；`.smoke/im_robot_smoke.py` **ALL PASS: 27 checks**（fan-out 全成/per-URL 记录/部分失败聚合/EGRESS fail-fast ＋ B 层 loopback 拦截/数组 fail-fast/email·unknown 零回归）。前端本批零改动（schema 驱动表单自动呈现 msgFormat/mentions，条件原子未触发）。
- 零新依赖/零迁移/无新 REST·ADR；D24 部分取回、不解除。仍缓做：短信、IM 应用 OAuth/入站、模板系统、群发层整体重试、真实平台联调，触发条件不变。
