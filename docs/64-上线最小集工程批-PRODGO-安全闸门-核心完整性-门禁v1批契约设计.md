# 64 上线最小集工程批（打包 J：安全闸门 / 核心完整性 / 门禁）v1 批契约设计

> **立项 2026-09-25（docs/63 第三次上线复审 §6 PROD GO 最小集；docs/08 §八 A 组 3 项候选；用户「推进任务」拍板）**。零代码批内另立契约，形状权威＝本文。
>
> **判据背景**：docs/63 判定＝技术验证 MVP 达到 / 生产 MVP 未达到（PROD 不 GO）。本文取 §6 中**纯工程可闭环**的三组（P0-1 安全闸门、P0-2 核心完整性工程内两件、P1-1 门禁），外部资源项（真实店铺/SMTP/域名/种子客户/商业 embedding，docs/08 §八 D 组）不进本批。
>
> **基线**：dev 已含打包 H/I 全部提交；三道门基线＝后端 **1811/108/0**、PG 直连 89（`-m integration` 43/1，唯一红＝既存 audit `seq` 投影）、前端 **722/2/53**、oxlint 0/6、build 过。测试编号下一可用 **U812** 起（docs/13 §9 最新 U811）。

## 1. 范围总览（三组十项）

| 组 | 项 | 出处 | 形状落点 |
|---|---|---|---|
| **P0-1 安全闸门** | J-1a `ATLAS_ENV` fail-closed 环境门 | docs/63 S7（根因）、S1 | 新增 `security/bootstrap.py`；api 启动接线；email_token + oauth 两 resolve 收口 |
| | J-1b 邮件决策 token 绑定收件人 | docs/63 S1 | `collaboration/email_token.py` issue/verify + notifications.py + api 两端点 |
| | J-1c prod 不播种种子账号＋种子口令登录拒绝 | docs/63 S2 | `scripts/ops/docker-entrypoint.sh` + `iam/principals.py` authenticate |
| | J-1d 登录查询确定性（ORDER BY ＋ 用户名全局唯一写契约） | docs/63 S3 | `storage/pg.py:410`、`iam/accounts.py:73`、`iam/principals.py:84` |
| **P0-2 核心完整性** | J-2a 决策器运行模式启动打印（不再静默降级） | docs/63 §2.1 | `llm/decision.py` + api 启动 |
| | J-2b 跨 human_approval 真实退款全链 e2e 测试 | docs/63 §2.2 | 新 `tests/test_refund_e2e_full_chain.py`；docs/08 §7.3 criterion 4 口径注记 |
| | J-2c waits/tasks 运行时操作台 UI（**D40 取回、不解除**） | docs/63 §2、14 D40 | 前端新页/面板＋apiClient＋i18n |
| **P1-1 门禁** | J-3a 修 `PgAuditStore.record()` 缺 `seq` 红 ＋ CI 加 postgres service 跑 integration | docs/63 §4、§5.4 | `storage/pg.py:2063`、`.github/workflows/ci.yml` |
| | J-3b 10 处 `limit` 补 `le=` | docs/63 S6 | `api/main.py`（573/1051/1794/1954/2951/2996/3140/3387/3771/3849） |
| | J-3c 三处 `litellm.completion` 加 timeout/max_tokens | docs/63 S6 | `llm/decision.py:66`、`condition_classifier.py:68`、`nl_generate.py:157` |
| | J-3d 入站 webhook 下沉线程池＋body 上限 | docs/63 S4 | `api/main.py:877/957/964` |
| | J-3e `/api/demo/*` 模拟面统一开关 | docs/63 S5 | `api/main.py:3711-3757` 等 demo mock 路由 |

**非目标（明确不做）**：docs/08 §八 D 组外部资源（真实店铺/SMTP/IM/域名/证书/CD/种子客户/商业 embedding）；P1-2 运维批（D38 retention、D39 可观测最小面、备份轮转、`/metrics` 收口、日志 request-id）；D36 卡住帧 reconcile（依赖 A 且另立）；B/C 组候选；`ATLAS_ENV` 只读不改 compose 缺省（compose 仍 dev 语义，prod 形态由用户部署时注入）。

## 2. J-1a `ATLAS_ENV` fail-closed 环境门

- 新增 `src/atlas/security/bootstrap.py` 纯函数模块（零依赖）：
  - `read_env_profile() -> str`：读 `ATLAS_ENV`，合法值 `dev`/`test`/`prod`（缺省 `dev`，非法值 raise `ValueError`）；大小写不敏感。
  - `assert_prod_secrets() -> None`：当 `read_env_profile()=="prod"` 时，`ATLAS_APPROVAL_HMAC_SECRET` 与 `ATLAS_MASTER_KEY` **任一缺失或过短（<32 字节）即 raise `RuntimeError`（fail-closed，拒绝启动）**；非 prod 静默通过。
- 接线：`api/main.py` 模块加载顶部调 `assert_prod_secrets()`（与既有 `_locate_binding` 同文件级位置），prod 缺密钥启动即炸。
- `collaboration/email_token.py:48 resolve_token_secret()` 收口：prod 下（`read_env_profile()=="prod"`）缺密钥 **raise**（不再 warning 返回 `_DEV_TOKEN_SECRET`）；非 prod 保持现状（warning＋dev 密钥，本地可跑）。
- `connections/oauth.py:33/63` 同形 dev state key 收口：`resolve_state_secret()` 改走同一密钥环境（`ATLAS_APPROVAL_HMAC_SECRET`/`ATLAS_MASTER_KEY`），prod 缺 raise、非 prod warning＋dev key。**与 email token 同源修复，不新增第三把密钥**。
- `docker-entrypoint.sh`：`ATLAS_ENV=prod` 时（读 `:${ATLAS_ENV:-dev}`）跳过 `seed_accounts`（见 J-1c）。
- 测试：bootstrap 纯函数 3 测（缺省 dev／prod 缺密钥 raise／prod 双密钥通过）；email_token prod 缺密钥 raise 1 测；oauth prod 缺密钥 raise 1 测。

## 3. J-1b 邮件决策 token 绑定收件人

- `email_token.py TokenIssuer.issue()` 增形参 `recipient: str | None = None`，载荷增 `"rcpt"`（仅非空写入，小写 trim）。`verify()` 返回体自然含 rcpt（旧载荷无 rcpt 字段，`body.get("rcpt")` 为 None）。
- `notifications.py:82 notify_pending`：`recipients` 首元素（`recipients[0]`）传入 `issue(..., recipient=...)`（收件人列表首人；空列表则不写 rcpt——但 notify 调用方保证非空，落码时校验）。
- `api/main.py` 两端点（`email_approval_view` :3252、`email_approval_decision` :3302）：`_resolve_email_signed` 解出 body 后，**若 `body.get("rcpt")` 非空，须 `∈ pending.notify_recipients` 否则 404**（与验签失败同口径，不区分原因）；rcpt 缺失的 token 一律 404（**不向后兼容旧 token**——TTL 仅 1h+grace，风险窗口小，换取更安全）。email-view 与 email-decision 同规则。
- 测试：issue 载荷含 rcpt／verify 回读；端点收件人匹配放行、不匹配 404、旧无 rcpt token 404（3 测，内存 broker）。

## 4. J-1c prod 不播种种子账号＋种子口令登录拒绝

- `docker-entrypoint.sh`：`ATLAS_ENV=prod` 时不执行 `python -m scripts.ops.seed_accounts`（其余迁移照常）。非 prod 行为不变（本地/演示可跑）。
- `iam/principals.py authenticate`：prod（`read_env_profile()=="prod"`）下，若口令命中已知种子口令（`verify_password(password, _seed_hash(username))` 真）→ 拒绝登录（返回 None 或专用错误，端点层统一 403；见下），并 `logger.warning` 提示强制改密；非 prod 行为不变。
- `api/main.py:1258 login`：prod 拒绝种子口令时返回 **403 detail 中文「生产环境禁止使用种子账号口令登录，请联系管理员改密」**（沿用中文 detail 惯例；不新增错误码枚举，HTTP 403 即可）。非 prod 照旧。
- 测试：entrypoint 脚本无 pytest 覆盖（脚本层人工冒烟）；authenticate prod 种子口令拒绝 1 测、非 prod 放行 1 测、prod 普通口令放行 1 测。

## 5. J-1d 登录查询确定性＋用户名全局唯一

- `storage/pg.py:410`（登录查询 `WHERE username = :u`）：改 `WHERE username = :u ORDER BY tenant_id, username`（确定性，跨租户重名时取字典序最小租户——但 J-1d 第二项使重名不可能发生，ORDER BY 仅防御）。
- `iam/accounts.py:73 get_by_username`：改 `min(self._users.values(), key=lambda a: (a.username, a.tenant_id))` 或等价确定性选择（现为生成器 `.next()` 依赖插入序）；同时保持 get() 不变。
- **用户名全局唯一写进契约（03 更新）**：PG 表 `iam_users` PK 仍 `(tenant_id, username)`（不动迁移），但在**账号创建/seed 落点加唯一性校验**——`accounts.py` 创建（:94 附近）与 `principals.py` seed：若 `get_by_username(username)` 已存在且 tenant 不同 → 拒绝（Raise `ValueError`/HTTP 409 由调用方决定，落码统一）。SEED 现无跨租户重名（admin-a/admin-b 不同名），仅防用户自建。
- 测试：PG 登录 ORDER BY 确定性 1 测（构造跨租户重名后登录取确定租户——不，全局唯一后不可构造；改为测 get_by_username 确定性选择 1 测 + 创建重名拒绝 1 测（内存档）；PG 档同形 1 测（integration））。

## 6. J-2a 决策器运行模式启动打印

- `llm/decision.py get_decision_client()`：增模块级惰性日志（`functools.lru_cache` 或 `_logged` 哨兵）——首次调用打印 `LITELLM_MODEL` 配置值与实际决策器类型：配置时 `INFO "decision client: LiteLLM(model=<model>)"`；未配置 `WARNING "ATLAS 运行于规则决策降级模式（LITELLM_MODEL 未配置），无 LLM 语义判断"`（不再静默）。
- `api/main.py` 启动处（lifespan/startup 或模块加载）调 `get_decision_client()` 一次触发打印（或直接打日志函数）。**不动 compose 缺省**（不给假模型，诚实降级+明示）。
- 测试：get_decision_client 降级 warning 1 测（monkeypatch env 空，捕获 caplog）。

## 7. J-2b 跨 human_approval 真实退款全链 e2e

- 新 `tests/test_refund_e2e_full_chain.py`：图含 trigger → ai_decision（规则兜底或直接条件）→ **human_approval 节点**（`approver`, `summary`, `timeoutSeconds`, `approvedTarget`）→ `shop/process_refund` 工具节点；运行 `inputs["approvals"] = {human 节点 id: "approved"}`（`loader.py:989` 既有预置通道）→ 断言 `orders["12345"].status == "refunded"`、run `completed`、approval 决策来源 approved。**一条链内同时存在 human_approval 与真实退款工具**（docs/63 §2.2 验收口径）。
- 顺带修正 docs/08 §7.3 criterion 4 口径注记：原文「自动执行退款**或**触发人工审批」→ 注记改为「同一条测试内同时存在 human_approval 与真实退款工具节点（docs/64 J-2b 已补该测试）」——**先补测试再改口径**（docs/63 §5.2 二选一）。
- 测试：2 测（approved 通过真实退款；rejected 走拒绝分支不落退款）。

## 8. J-2c waits/tasks 运行时操作台 UI（D40 取回、不解除）

- 前端新页 `/waits`（Dashboard 菜单入口「等待与任务」）：两张表——pending waits（`GET /api/waits`：token/eventKey/status/timeout/deadline/remaining）、tasks（`GET /api/tasks`：id/type/status/createdAt）；waits 行内「发信号」按钮（`POST /api/waits/{token}/signal` `{eventKey, payload}`，payload 用 JSON 文本框，预填 pending 的 eventKey）＋广播「发送事件」输入（`POST /api/waits/events` `{eventKey, payload}` 可发给全部同 key）；10s 轮询。
- 后端零改动（五条端点已存在）；`apiClient.ts` 增 `listWaits/signalWait/broadcastWaitEvent/listTasks` 四方法＋类型；`locales/*/monitoring.json` 或新 `waits.json` 双语键（zh/en，en 为成品翻译）；表格用既有 AntD Table。
- **前端 vitest**：apiClient 方法 4 测（mock fetch 形状）；组件级不引 jsdom（沿用无 DOM 惯例，纯函数/方法层断言）。
- 缓做口径：D40 **取回部分**（操作台 UI），「事件等待被真实流程使用」剩余触发条件不解除（docs/14 注记）。

## 9. J-3a audit `seq` 红＋CI postgres service

- `storage/pg.py` `PgAuditStore.record()`（:2063 附近 return 字典）补 `"seq": seq`（`RETURNING seq` 若未取则现取——落码以实际 SQL 为准，定口径：**record 返回与 list 投影逐键一致**，docs/63 §4 与 docs/08 §八 B 组「先定口径」）。
- `.github/workflows/ci.yml` backend job：加 `services: postgres`（`pgvector/pgvector:pg16`，与 compose 同镜像，`ATLAS_DATABASE_URL`/`DATABASE_URL` 用 `postgresql+psycopg://atlas:atlas@localhost:5432/atlas` 形态对齐本地约定）；job env 设 `ATLAS_RUN_INTEGRATION=1`；命令改 `pytest -m integration`（44 条）**单独一步**（主 pytest 全量照跑，integration 步独立 job 或同 job 第二行，落码以 CI 可跑为准——**优先独立 job `integration`**，避免拖慢主门）。
- 测试：audit record 返回含 seq 1 测（integration，PG 直连实测）；CI yaml 无法 pytest 覆盖，收口时本地 `ATLAS_RUN_INTEGRATION=1` 全量 integration 复跑作门。

## 10. J-3b `limit` 补 `le=`

- **2026-09-25 落码勘误**：实测发现本仓全部 10 处 `limit` 端点**均已自带门禁**——7 处 clamp 语义（`max(1, min(limit, N))`：audit 1–500、decided/shadow/deliveries/memories 1–200 等）＋3 处手动 reject（runs/tasks `1 <= limit <= 200`、monitoring_runs `RUN_RING_SIZE`）。统一 `le=` 会把 clamp 语义破坏成 reject（`limit=999999` 由 200 变 422，三测断言 `clamped_not_rejected`/`clamp` 失败），故 **J-3b 以「确认有界」收口**：还原全部裸默认值，不引入 `le=`；越界一律由既有 clamp/reject 门禁处置（文档口径不变）。

## 11. J-3c `litellm.completion` timeout/max_tokens

- 三处 `litellm.completion(` 加 `timeout=` 与 `max_tokens=`：
  - `llm/decision.py:66`：`timeout=30`（env `ATLAS_LLM_TIMEOUT_SECONDS` 可覆盖，下同）、`max_tokens=64`；
  - `llm/condition_classifier.py:68`：`timeout=30`、`max_tokens=16`；
  - `llm/nl_generate.py:157`：`timeout=60`、`max_tokens=512`。
  - 统一经模块级常量（如 `_LLM_TIMEOUT`/`_LLM_MAX_TOKENS`）读 env 缺省默认，避免三处硬编码漂移。
- 测试：mock litellm.completion 断言 kwargs 含 timeout/max_tokens（3 测，monkeypatch）。

## 12. J-3d 入站 webhook 线程池＋body 上限

- `api/main.py` webhook 入站路径（:957 同步 `_locate_binding`、:964 `await http_request.body()` 全量缓冲、:877 裸 `threading.Thread`）：
  - **body 上限**：读 `Content-Length` 或读后 `len(body)`，超上限（常量 `MAX_WEBHOOK_BODY_BYTES = 1_000_000`，env `ATLAS_WEBHOOK_BODY_LIMIT` 可覆盖）→ 413/422；
  - **同步 SQL 下沉**：`_locate_binding` 改 `await asyncio.to_thread(_locate_binding, binding_id)`（不阻塞事件循环）；
  - **并发有界**：裸 `threading.Thread` 改经模块级 `ThreadPoolExecutor(max_workers=8)`（`submit`）；上限常量与 worker 数 env 可覆盖。
- 测试：body 超限 413 1 测、正常投递 1 测（既有 webhook 测试回归即可）。

## 13. J-3e `/api/demo/*` 模拟面统一开关

- 新增 `ATLAS_ENABLE_DEMO_MOCK`（缺省未设＝关）：demo mock 路由组（`/api/demo/mock/*`，含匿名可写 `shopify-admin/webhooks.json` :3711-3757）统一依赖开关——未启用时 404；`/api/demo/orders` 等既有 demo 系统端点保留原守卫（`X-Demo-Token` 不变）。
- 实现：路由级依赖 `require_demo_mock()`（FastAPI `Depends`）或启动时条件注册（落码取其一，尽量少动路由表）；`.env.example` 增注记；`docker-compose.yml` demo profile 可设 `ATLAS_ENABLE_DEMO_MOCK=1`（仅演示形态）。
- 测试：开关关时 mock 端点 404 1 测、开时可用 1 测（monkeypatch env）。

## 14. 非目标复述（docs/08 §八 D 组与 P1-2 不进本批）

真实店铺/SMTP/IM/域名/证书/CD/种子客户/商业 embedding（D 组）；D38 retention／D39 可观测最小面（P1-2 运维批，另立）；D36 reconcile（硬依赖 A 已落、另立）；B/C 组全部候选（改口径先补测试已含 J-2b；superseded 前端认领、sync /run 取消句柄、表达式函数库等仍缓做）；oauth 三件套（OAuth2 真实平台联调）不动。

## 15. 测试候选与原子序

候选区段（落码以 docs/13 定稿为准）：J-1 **U812–U830**（bootstrap 3＋email_token prod 1＋oauth prod 1＋token rcpt 3＋authenticate 3＋确定性 3，共约 14）、J-2 **U831–U850**（降级日志 1＋全链 e2e 2＋apiClient 4，共约 7）、J-3 **U851–U870**（audit seq 1〔integration〕＋limit 3＋litellm kwargs 3＋webhook 2＋demo 开关 2，共约 11）。

原子序（一次一 commit，英文原子）：

1. **J-0** `docs(security): contract for batch J (prod-go minimal engineering set)` — 本文
2. **J-1a** `feat(security): read ATLAS_ENV and fail closed on missing prod secrets`
3. **J-1b** `feat(collaboration): bind email decision token to recipient`
4. **J-1c** `fix(ops): skip seeding and forbid seed credentials in prod`
5. **J-1d** `fix(iam): deterministic login lookup and global username uniqueness`
6. **J-2a** `feat(llm): log decision runtime mode on startup`
7. **J-2b** `test(e2e): full refund chain through human approval`
8. **J-2c** `feat(frontend): waits/tasks runtime console (D40)`
9. **J-3a** `fix(storage): return seq from audit record; add CI postgres service`
10. **J-3b** `fix(api): bound pagination limits with le=`
11. **J-3c** `fix(llm): timeouts and max_tokens on completion calls`
12. **J-3d** `fix(api): bound webhook body and sink sync work off event loop`
13. **J-3e** `fix(api): gate demo mock surface behind env switch`
14. **J-4** `docs(security): close out batch J with gate results` — 三道门实跑＋文档矩阵回填＋收口
15. push dev（「一口气搞了」授权内；合 main 须用户明确授权）

## 16. 同步矩阵（收口回填）

docs/63（P0-1/P0-2/P1-1 判定对应项划 ✅／注记）、docs/08（§八 A 组划掉＋落码条）、docs/03（iam_users 用户名全局唯一契约注记）、docs/12（如端点形状变化——本批预期零新端点零新错误码，仅 demo 开关与 limit 约束，标注即可）、docs/13（候选转正式登记）、docs/14（D40 注记取回部分；D38/D39 不动）、docs/00（地图加 docs/64）、CHANGELOG、handoff。

## 17. 风险与回滚

- **J-1a/J-1b/J-1c 是行为收紧**：非 prod 全路径行为不变（测试锁死）；prod 形态（`ATLAS_ENV=prod`）目前无部署实例，收紧无存量影响。回滚＝单独 revert 对应原子即可（各原子自包含）。
- **J-1b 旧 token 不兼容**：TTL≤1h+300s，窗口小；若需兼容可放宽 rcpt 缺失在非 prod 放行——**本契约选择一律 404（更安全），偏差须注记**。
- **J-3a CI integration**：44 条 integration 需 PG 容器资源，若 CI 时长超标可降级为独立 job 触发（push main/dev 才跑）。`seq` 红修复可能牵动 audit 相关测试断言（record 返回补键为纯超集，既有断言应零改动；若有改断言属测试自误，按惯例改测试不改产品）。
- **J-3e demo 开关**：若既有浏览器冒烟依赖 demo mock 缺省可用，需在开关关时显式设 `ATLAS_ENABLE_DEMO_MOCK=1` 复跑（收口冒烟统一带 env）。
