# 外部 agent 经 REST 驱动 Atlas 集成说明

**定位**：本文是**调用者视角**的接入说明——把「一个外部智能体（Hermes／Claude 一类 agent）如何用 REST 把 Atlas 当工具面用」讲清楚，重点是三件最容易踩的事：**挂起审批会阻塞你的 HTTP 请求**、**运行提交今天没有幂等键**、**错误码分四套口径**。

**这不是新契约**。数据结构与接口签名以 03／12 为准，运行时语义以 04／06／24 为准；本文只归纳既有契约、不新增能力。与正文冲突时以正文为准。

**为什么写**：登记项 [14 D45](14-缓做事项登记表.md) 记录「外部系统经 REST、入站 webhook、OpenAPI 导入成图内工具三条路今天已经能接，缺的是协议标准化与工具自描述」。本文补的是**接入说明**那一半；真正的 MCP server／A2A 是**选型变更**，须先记 [10 §4](10-技术选型决策记录.md) 的 ADR，不在本文范围。

---

## 1. 认证与租户分区

### 1.1 登录换 token

```
POST /api/auth/login
Content-Type: application/json

{"username": "operator-a", "password": "operator123"}
```

成功 → 200：

```json
{"token": "sess-3f2a...", "principal": {"tenant_id": "t1", "tenant_name": "演示企业 A",
  "username": "operator-a", "display_name": "A 企业运营", "role": "operator"}}
```

此后所有请求带 `Authorization: Bearer sess-3f2a...`。`GET /api/auth/me` 回同一形状（`token` 为你带来的那个），`POST /api/auth/logout` 吊销当前 token（`{"logged_out": true}`）。

### 1.2 会话事实（别按 JWT 假设它）

| 事实 | 值 | 含义 |
|---|---|---|
| token 形态 | `sess-` + uuid4 hex，**不透明** | 不是 JWT，**不可解析、无声明、不能本地验签** |
| 存储 | 进程内 `SessionStore`（内存档）／PG 档落 `iam_sessions` | 内存档**重启即失**，PG 档随库 |
| TTL | 默认 12h，`ATLAS_SESSION_TTL_HOURS` 可设 **1–168** | 绝对过期，不滑动续期 |
| 失效 | 过期 token 惰性删除 | 过期后请求 → 401 `AUTH_UNAUTHENTICATED` |

**给 agent 的建议**：把 token 当黑盒，401 就重新登录；**不要**缓存超过 TTL，也不要指望续期——今天没有 refresh 端点。

### 1.3 角色矩阵

三个角色，端点级白名单（默认拒绝）：

| 角色 | capability | 能做什么 |
|---|---|---|
| `viewer` | `read` | 全部 GET（图／运行／审批列表／监控／录制／调试／任务／告警／模板／连接与渠道列表） |
| `operator` | `operate` | read + 图 CRUD／编译／发布／**运行**／**审批决策**／**等待信号**／取消运行／录制写／调试放行／告警确认关闭／渠道绑定／OpenAPI 导入／OAuth 授权换发 |
| `admin` | `administer` | operate + 监控规则 PUT／`/api/demo/reset`／用户管理／连接（凭证）创建／**审计事件与导出** |

> 注意两处反直觉的：**审计**（`/api/audit/events`、`/api/audit/export`）要 `administer` 不是 `read`；**渠道绑定**（`POST /api/channels`）只要 `operate` 不是 `administer`。判定实现是**等级比较**（`ROLE_RANK[role] >= _CAPABILITY_RANK[cap]`），所以「operator 能做 viewer 的一切」「admin 能做 operator 的一切」是结构保证，不是枚举巧合。

**租户分区**：所有业务对象按 `tenant_id` 隔离，租户由 token 推断、**不从请求体或路径读**。跨租户访问对象 → **404**（不泄漏存在性），角色不足 → **403**。所以 agent 拿 A 租户 token 去取 B 租户的 `graph_id`，看到的是「不存在」，而不是「无权限」——不要据此判断对象是否存在。

### 1.4 登录节流

`600s` 窗口内同一 `username|client_ip` **5 次失败** → 429「登录尝试过于频繁，请稍后再试」（锁定期内**不校验口令**）；成功即清零。计数是**进程内**的（单实例边界），多副本不共享。

---

## 2. 最小闭环：从登录到一次运行

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"operator-a","password":"operator123"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')

# 1) 看有哪些图（本租户）
curl -s localhost:8000/api/graphs -H "Authorization: Bearer $TOKEN"

# 2) 触发一次同步运行
curl -s -X POST localhost:8000/api/graphs/graph-1/run \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"inputs": {"orderId": "A-1001"}}'

# 3) 查这次运行
curl -s localhost:8000/api/runs/<run_id> -H "Authorization: Bearer $TOKEN"
```

### 2.1 运行提交的请求体

`POST /api/graphs/{graph_id}/run`（`operate`）接受可选 JSON body：

| 字段 | 说明 |
|---|---|
| `inputs` | 图输入字典；`inputs.approvals` 可**预置**审批结论（见 §3.4） |
| `event` | 入站事件 `{channel?, payload?}`；经 RoutingStore 解析**钉住的发布版**。与 `releaseVersion` 互斥（同传 422）、与 `debug` 互斥（同传 422）、**无任何发布版可路由 → 409**、非对象 → 422 |
| `releaseVersion` | 手动指定已发布版本；不给则跑草稿 |
| `debug` | 同步入口**不支持**，给了 → 422「单步调试仅支持流式运行 /run/stream」 |

不给 body 等价于 `{}`。`{graph_id}` 支持 `graphId@<releaseVersion>` 形式解析版本；跨租户或不存在 → 404。

### 2.2 运行的生命周期与五种终局

```
running ──► completed     成功
        ├─► failed        异常（同时进门控评估）
        ├─► cancelled     用户急停（**刻意不进**门控评估）
        ├─► interrupted   进程崩溃后被启动恢复标记
        └─► suspended     挂起（等审批／等事件／被另一进程抢走续跑权）
```

`GET /api/runs?status=<s>&limit=<n>` 列出本租户运行（新→旧）：`status` 只接受上面六个值之一，否则 422；`limit` 必须 **1–200**，否则 422。

### 2.3 同步 `/run` 的返回

`RunGraphResponse`：`{id, status, outputs, trace}`。注意 `id` 是**图 id**不是 run id——要拿 run id 走 `GET /api/runs`。

| 场景 | HTTP | `status` |
|---|---|---|
| 跑完 | 200 | `completed` |
| 图内异常 | 5xx | `failed`（异常照抛） |
| 被取消 | 200 | `cancelled` |
| 续跑权被抢 | 200 | `suspended`，`trace` 含 `<node>: superseded by another process` |

---

## 3. 挂起审批：**本说明最重要的一节**

### 3.1 同步 `/run` 会在审批处**阻塞**

图里遇到 `human_approval` 节点时，运行线程会停在 `ApprovalBroker.wait(token)` 上，**一直等到有人决策或节点超时**。这意味着：

- `POST /api/graphs/{id}/run` 的 **HTTP 响应可能挂住整个审批超时时长**；
- 如果你的 HTTP 客户端有 30s 超时，**你会先断开，但服务端那次运行照跑**——断连不是取消；
- 图里 `human_approval` 的 `timeoutSeconds` 到了，按 `onTimeout`（缺省 `reject`）放行，`resolved_by` 记为 `timeout`。

**给 agent 的建议**：要跑含审批的图，**用流式 `/run/stream` 而不是同步 `/run`**，这样你在 `node_start` 事件里拿到 token 后可以立刻去决策，而不用占着一个被阻塞的 HTTP 连接。

### 3.2 拿到 token 的三条路

1. **SSE**（推荐）：`POST /api/graphs/{graph_id}/run/stream`，`human_approval` 节点的 `node_start` 事件**在阻塞前**到达，带 `approval: {token, summary, approver, timeoutSeconds, cardTemplateId?}`。
2. **轮询**：`GET /api/approvals`（`read`）→ `{"items": [...]}`，每条 `{token, node_id, graph_id, summary, approver, timeoutSeconds, createdAt, cardTemplateId?}`。**注意**：这是进程内 broker 的 pending 列表，**重启即失**。
3. **邮件深链**：审批通知邮件里的签名链接（见 §3.8），人工点。

### 3.3 决策

```
POST /api/approvals/{token}/decision     # operate
```

Body 两种形态（`populate_by_name`，两种字段名都收）：

```json
{"decision": "approved", "comment": "同意"}
{"actionId": "approve", "form": {...}}          # 卡片路径
```

语义：

| 情况 | 结果 |
|---|---|
| 未知／跨租户／已清理 token | **404**「审批请求不存在或已清理」 |
| **已有决策，重复提交** | **409**「该审批请求已有决策，重复提交不生效」 |
| 既没 `decision` 也没 `actionId` | 422「请提供 decision（approved/rejected）或卡片 actionId」 |
| 成功 | 200，返回应用后的审批结果 |

**首决生效**：并发两个决策只有一个赢，输家 409——这是**唯一一处天然幂等**的写操作（见 §5）。

### 3.4 预置审批：把人工节点变成无人值守

运行请求体里 `inputs.approvals` 可以**直接给定结论**，跳过等待：

```json
{"inputs": {"approvals": {"approve-order": "approved"}}}
```

图 loader 在节点进入前就 `broker.resolve(token, preset, resolved_by="input")`，`wait()` 立刻返回。**给 agent 的建议**：批量／自动化场景用这条，比「跑起来再决策」可靠得多。

### 3.5 取消运行**不能**打断阻塞中的审批

`POST /api/runs/{run_id}/cancel`（`operate`）是**协作式**急停：置位取消标志，**在下一个节点边界**生效。它**不在** `wait`／`approval`／tool 阻塞中点强杀。所以：

- 取消一个正卡在审批上的 run → 返回 **200** `{"run_id": ..., "cancelled": true}`，但**它仍在等**；
- 重复取消 → 幂等 **200**；
- 已结束（无注册句柄）→ **409**「运行已结束，无法取消」；
- 不存在／跨租户 → **404**。

**要真正放掉一个卡住的审批，正确做法是给它一个决策（§3.3），不是取消运行。**

### 3.6 挂起的 run 没有 REST 续跑端点

今天**没有** `POST /api/runs/{id}/resume` 这类端点。挂起帧（PG 档，migration 029 的 `resumed_at`／`resumed_by`）的续跑权只在这三条路被认领：

1. 进程还活着时，审批决策解除 `broker.wait()` 阻塞（§3.3）；
2. **启动恢复**：PG 档重启时 `recover_pending()` 重放中断帧续跑；
3. **重复提交同一运行**：后到者抢到帧（at-most-once 认领），先到者收到 `RunSuperseded` 并以 `status: "suspended"` 返回。

**所以：不要靠「再 POST 一次 /run」来续跑——你会和原运行抢帧，两者必有一方被 superseded。**

### 3.7 只读观测挂起帧

`GET /api/interruptions`（`read`）是挂起帧的**只读投影**（docs/76 打包 Q）：`{resumeToken, runId, graphId, nodeId, kind, claimedAt, claimedBy, claimedSeconds, deadlineAt, runStatus, state}`。`state` 是描述性档位（`awaiting`／`claimed_executing`／`claimed_suspended`／`frame_lingering`／…），**只描述、不判决、不计时**——本端点不重放、不清帧、不改任何状态。内存档返回 `{"backend": "memory", "visibility": "frames-not-persisted", "items": []}`，**空 ≠ 没有卡住的 run**。

### 3.8 邮件深链：两个**无需登录**的审批端点

审批通知邮件里的链接走这两个端点，**都不带 Bearer**（签名 token 自带授权）：

| 端点 | 作用 |
|---|---|
| `GET /api/approvals/email-view?token=<signed>` | 只读视图，**无副作用**（刻意如此，防邮件客户端预取把审批点掉）。返回 `{status: "pending"｜"resolved", summary, nodeId, graphId, approver, timeoutSeconds, createdAt, remainingSeconds, decision?, resolvedBy?, card?}` |
| `POST /api/approvals/email-decision` | body `{token, decision?, comment?, actionId?, form?}`；与登录态端点**共用同一个决策应用函数**，`resolved_by` 记为 `email-link` |

**统一 404 文案「审批链接无效或已过期」**，且**不区分失败原因**——签名坏、token 过期、审批已清理、以及 **`rcpt` 收件人不匹配**（docs/64 J-1b 的绑定校验：token 载荷里的收件人必须属于该审批的收件人）全部回这一个 404。已决后重复提交仍是 409（同 §3.3）。

**给 agent 的建议**：把这两个端点当**公网匿名面**对待——它们不需要 token 就能被调用，授权只靠签名串。不要在日志里打印 `token` 参数值。

---

## 4. 幂等口径（**诚实版**）

**结论先说：运行提交（`POST /api/graphs/{id}/run` 与 `/run/stream`）今天没有请求级幂等键。**重复 POST 就是**两次运行**，会各建一条 run 记录、各跑一遍副作用。**调用方必须自己做去重。**

平台内确实存在的幂等，都在**别的**地方：

| 位置 | 机制 | 重复提交的结果 |
|---|---|---|
| **运行提交** | **无** | **再跑一次**（无去重、无 `Idempotency-Key` 头） |
| 审批决策 | 首决生效 | 重复 → **409**，第一次的结论保留 |
| 取消运行 | 取消标志置位 | 重复 → **200**（幂等） |
| 等待信号 | 单信号 | 重复 → **409**「该等待已有信号」 |
| 调试放行 | 首决生效 | 重复 → **409**「该调试暂停已恢复」 |
| 入站 webhook | `PK(tenant_id, webhook_id)` + 冲突计数 | 重复投递**仍会重放**，但 `duplicates` 计数 +1，可在 `GET /api/channels/webhooks/metrics` 看到 |
| 定时触发 | 槽位认领（`claim` 跨进程权威 + 进程内 ledger） | 同一槽位不会重复派发；认领失败记 `claim_lost` |
| 协调任务派发 | `idempotencyKey` 命中返首结果（内部 M7 信封，**非公网 REST 面**） | 不重复建任务 |

**给 agent 的落地建议**：

1. 触发运行前，用**你自己的**去重键（业务单号等）落一张表，提交前查、提交后写；
2. 或者用 **`event` 入站路径**（webhook）代替直接 `/run`——那条路有 `webhook_id` 去重计数；
3. 别把「HTTP 超时重试」当成安全操作：**超时后重试 = 大概率第二次运行**。

> 这一条若将来要改（加 `Idempotency-Key`），属于接口契约变更，须走 AGENTS.md 的「改内容流程」并记 08 决策。

---

## 5. 错误码与状态码口径

**四套口径，别混用**：

### 5.1 认证／授权（`AUTH_*`）——结构化

```json
{"detail": {"code": "AUTH_UNAUTHENTICATED", "message": "缺少或无效的登录凭证"}}
```

| HTTP | code | 触发 |
|---|---|---|
| 401 | `AUTH_UNAUTHENTICATED` | 缺 token／token 无效／已吊销／已过期 |
| 401 | `AUTH_INVALID_CREDENTIALS` | 登录用户名或密码错误 |
| 403 | `AUTH_ACCOUNT_DISABLED` | 账号已停用 |
| 403 | `AUTH_SEED_CREDENTIAL` | **prod** 下用种子账号默认口令登录（须先改密） |
| 403 | `AUTH_FORBIDDEN` | 角色 capability 不足 |
| 429 | （纯字符串） | 登录节流锁定 |

### 5.2 图编译／校验——422 信封

`GraphValidationError` 的 422 是**多字段**结构，专为前端本地化设计（下面是一例，**四个数组按下标对齐**）：

```json
{"detail": ["节点 approve 必须填写审批说明（summary）"],
 "codes": ["APR_SUMMARY_REQUIRED"],
 "params": [{"owner": "approve"}],
 "locations": [{"index": 0, "nodeId": "approve", "pointer": "/summary"}]}
```

| 字段 | 含义 |
|---|---|
| `detail` | 中文错误消息数组（**仅作日志／兜底，不要解析**） |
| `codes` | 前端 i18n 契约，与 `detail` **等长、下标对齐** |
| `params` | 插值参数数组，同样等长；节点级错误里会带 `owner`＝节点 id |
| `locations` | **稀疏** sidecar，每项 `{index, nodeId?, pointer?}`；**只给能定位到具体节点的错误**，图级／拓扑级错误**不出条目**，且**整个字段可能缺席** |

**给 agent 的建议**：按 `codes[i]` 判语义、用 `params[i]` 取插值值；`locations` 只当"能不能跳到某个节点"的可选提示，**别假设它和 `detail` 等长**。真实 code 形如 `APR_*`（审批节点）、`COND_*`（条件节点），完整清单在 [04 §6.5](04-组件设计-编辑后台.md)／[06 §6.13](06-运行时与质量保障.md)。

### 5.3 运行时结构化 500

`wait`／条件求值一类运行时失败返回**结构化 500**（`detail` 是对象，不是字符串）：

```json
{"detail": {"code": "COND_...", "message": "...", "params": {...}}}
{"detail": {"code": "...", "message": "...", "nodeId": "n3"}}
```

### 5.4 等待信号——结构化（另一处带 code 的）

`POST /api/waits/{token}/signal` 的失败**带 code**：

| HTTP | code |
|---|---|
| 404 | `WAIT_TOKEN_NOT_FOUND`「等待不存在或已清理」 |
| 409 | `WAIT_ALREADY_SIGNALED`「该等待已有信号，重复提交不生效」 |

### 5.5 其余——纯字符串 detail

`404`「运行不存在」／「审批请求不存在或已清理：{token}」／「调试暂停不存在或已恢复」、`409`「运行已结束，无法取消」／「该审批请求已有决策，重复提交不生效」／「该调试暂停已恢复，重复提交不生效」、`422`「非法的 status 过滤值」／「limit 必须在 1 到 200 之间」／「请提供 decision（approved/rejected）或卡片 actionId」、`429` 节流文案——**都是中文字符串，没有 `code`**。

**给 agent 的建议**：判断语义**只看 HTTP 状态码 + `code` 字段（若有）**，不要 match 中文串——它们会变。

### 5.6 跨租户一律 404

A 租户 token 访问 B 租户对象 → **404**，不是 403。这是**刻意**的（不泄漏存在性）。所以「404」在 Atlas 里同时意味着「不存在」和「不属于你」，**不能用来判断对象是否真实存在**。

---

## 6. 入站触发：webhook 与定时

### 6.1 Shopify webhook（公网、无 Bearer）

```
POST /api/channels/hooks/shopify/{binding_id}
X-Shopify-Hmac-Sha256: <base64(HMAC-SHA256(client_secret, raw_body))>
```

| HTTP | 条件 |
|---|---|
| 404 | 渠道绑定不存在（跨租户统一 404，不泄漏存在性） |
| 503 | 验签密钥暂不可用 |
| 413 | 请求体 > 1MB |
| 401 | 签名校验失败（统一 401，不泄漏原因差异） |
| 400 | body 不是合法 JSON 对象／不是 JSON 对象／头缺失 |

**顺序是固定的**：定位绑定 → 取密钥 → 查大小 → **在原始 body 上验签** → 最后才解析 JSON。所以一个超大 body 会先撞 413，不会先撞 401。

验签用 `hmac.compare_digest`（恒定时间），**不抛异常、不记秘密**。**验签通过后一律 200**，响应里的状态是 `received`／`duplicate`／`ignored` 之一——**重复投递不是错误**。投递记录落 `webhook_deliveries`（`PK(tenant_id, webhook_id)`），重复 `webhook_id` → `duplicates` 计数 +1；可在 `GET /api/channels/webhooks/metrics` 看，失败的在 `GET /api/channels/webhooks/dead-letters`，可 `POST .../{webhook_id}/replay` 重放。

**给 agent 的建议**：这条路的幂等是**可观测的计数**，不是拒绝——收到重复投递仍会重放一次（只多计一个 `duplicates`）。要真去重，用 `webhook_id` 自己拦。

### 6.2 定时触发

`GET/POST /api/schedules`、`POST /api/schedules/{graph_id}/enabled`、`POST /api/schedules/{graph_id}/run-now`、`POST /api/schedules/cron-preview`。调度器**进程内**（`start_scheduler()` 随 lifespan 起），派发权在跨进程的 `claim` 上：同一 `(tenant, graph, slot)` 只会派发一次，认领失败记 `claim_lost`，重叠运行记 `skipped_overlap`。

---

## 7. 只读观测

| 端点 | 鉴权 | 说明 |
|---|---|---|
| `GET /api/health` | **公开** | 存活探针，**不检查依赖**，永远 200 |
| `GET /api/ready` | **公开** | 就绪探针；PG 不可用 → **503** `{"status":"not_ready","storage":"pg","database":"unavailable","error":"..."}` |
| `GET /metrics` | prod 需 Bearer | Prometheus 文本；**prod fail-closed**：未配 `ATLAS_METRICS_TOKEN` → **404**，配了但 token 不对 → **401** |
| `GET /api/runs` `/{run_id}` | `read` | 运行列表／详情 |
| `GET /api/interruptions` | `read` | 挂起帧只读投影（§3.7） |
| `GET /api/waits` | `read` | 本租户 pending 事件等待（进程内，重启即失） |
| `GET /api/approvals` `…/decided` | `read` | 待决／已决审批 |
| `GET /api/approvals/email-view`／`POST /api/approvals/email-decision` | **公开**（签名 token 自带授权） | 邮件深链，**无 Bearer**（§3.8） |
| `GET /api/monitoring/runs` `/{run_id}/trace` `…/metrics` | `read` | 运行监控与轨迹 |
| `GET /api/audit/events` `…/export` | **`administer`** | 审计事件与导出（**不记请求体、不记 Authorization 头**） |

`/api/health` 与 `/api/ready` 的**公开**是刻意的——探针不能要凭证。它们**不暴露任何租户数据**。

---

## 8. 已知边界与非目标（写清楚，省得你猜）

1. **单实例硬约束**：`--workers 1`、`replicas: 1`。会话、节流计数、审批／等待／调试 broker、调度器**全是进程内**的。**多副本今天不支持**（docs/62 L1/L2）。
2. **内存档会「失忆」**：`ATLAS_STORAGE_BACKEND` 非 `pg` 时，重启丢会话、丢挂起帧、丢审批 pending。**挂起帧根本不落表**（`/api/interruptions` 会如实告诉你 `frames-not-persisted`）。
3. **无 CORS 中间件**：浏览器跨源直连会被拦。今天前端由同一服务托管。
4. **无平台级请求体大小限制**：只有 webhook 那一条路自带 1MB 上限。
5. **无速率限制**（登录节流除外）：跑图／查列表不限速。
6. **`/docs`、`/redoc`、`/openapi.json` 在 prod 被 404**（`gate_openapi_surface`：**demo 模拟面关闭时**整片文档面 404，prod 缺省即关闭；`ATLAS_ENABLE_DEMO_MOCK=1` 可显式重开）。dev 可用。所以**别指望在生产环境靠 `/openapi.json` 做运行时发现**——契约要走 [12](12-API与模块接口清单.md)。
7. **无 refresh token、无 SSO、无 JWT**：只有不透明 session token。
8. **不是 MCP、不是 A2A**：没有工具自描述、没有协议级发现。想要那个 = **选型变更**，须先记 [10 §4](10-技术选型决策记录.md) 的 ADR（14 D45）。

---

## 9. 一页速查

| 目的 | 方法 | 鉴权 | 幂等 |
|---|---|---|---|
| 登录 | `POST /api/auth/login` | 公开 | — |
| 当前身份 | `GET /api/auth/me` | Bearer | — |
| 登出 | `POST /api/auth/logout` | Bearer | 幂等 |
| 列图 | `GET /api/graphs` | read | — |
| 跑图（阻塞） | `POST /api/graphs/{id}/run` | operate | **无** |
| 跑图（流式） | `POST /api/graphs/{id}/run/stream` | operate | **无** |
| 查运行 | `GET /api/runs` / `/{run_id}` | read | — |
| 取消运行 | `POST /api/runs/{run_id}/cancel` | operate | 幂等 200 |
| 列待决审批 | `GET /api/approvals` | read | — |
| 审批决策 | `POST /api/approvals/{token}/decision` | operate | **首决生效**，重复 409 |
| 邮件深链审批 | `GET /api/approvals/email-view`／`POST /api/approvals/email-decision` | **公开**（签名） | 首决生效，重复 409 |
| 投等待信号 | `POST /api/waits/{token}/signal` | operate | 重复 409 |
| 挂起帧 | `GET /api/interruptions` | read | — |
| 存活／就绪 | `GET /api/health` / `/api/ready` | 公开 | — |
| 指标 | `GET /metrics` | prod: Bearer | — |
| 入站 webhook | `POST /api/channels/hooks/shopify/{binding_id}` | HMAC | 计数去重 |

---

## 10. 相关文档

- [04 §5.14 多租户与权限 v1](04-组件设计-编辑后台.md) — 认证载体／角色矩阵／404 语义的权威契约
- [12 API 与模块接口清单](12-API与模块接口清单.md) — 端点与签名权威
- [24 M5 持久化与中断恢复契约设计](24-M5持久化与中断恢复契约设计.md) — run 状态机与挂起帧
- [06 §6.12 认证鉴权运行时](06-运行时与质量保障.md) — Depends 链与租户装配
- [14 D45](14-缓做事项登记表.md) — 为什么 MCP／A2A 仍缓做
