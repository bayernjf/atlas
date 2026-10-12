# 打包 AR：项目级审查 14 项修复 — SSE 健壮性 / 安全脱敏 / 并发与 CI 门（v1 批契约设计）

> 号段：U1373–U1392（docs/13 预留）｜承接 2026-10-13 全仓项目级只读审查（基点 dev `4977840`，14 条发现经两轮在当前 HEAD 复核 14/14 成立）｜2026-10-13 docs-only 立项
> 状态：⬜ 未落码。选型裁决见 §2；**#2 拟记 ADR T36**（落码原子同步 docs/10 §4 → docs/02 → docs/09，三处同步纪律不变）
> 形状权威＝本文。落码与本文的偏差在收口注记中记录。

## 1. 背景与问题

2026-10-13 对全仓做了一次项目级只读代码审查（用户选项 A：修复全部发现）。14 条问题均在当前 HEAD 两轮取证成立，按面归四簇：**SSE 健壮性（#1/#2/#4）、安全（#3/#5/#7）、并发正确性（#8/#9/#10/#11/#12）、前端与门（#6/#13/#14）**。

| # | 严重度 | 面 | 问题 | 证据（file::symbol） |
|---|---|---|---|---|
| 1 | 高 | SSE | worker 收尾在保护外：shadow 流 `extract_shadow_events`/`shadow_store.add` 在 try **外**，抛异常则线程死亡、终帧永不发；run 流各 except 分支里的 `finish`/`record_run`/`evaluate_after_run` 二次抛异常时 `__error__` 等终帧不发；两处生成器都在无超时 `queue.get()` 上**永久挂死**（线程暴毙＝连接悬挂到 TCP 超时） | `api/main.py::create_shadow_run_stream`、`create_run_stream`（SSE 段） |
| 2 | 中 | SSE/部署 | 两端点都是同步 `def`，跑在 anyio 默认 40 worker 的线程池里；同步 SSE 生成器整条流生命周期占一个 worker。约 40 条长流（含 wait/审批，可阻塞数小时）即耗尽线程池，**连同步 `def /api/health` 一起被拖死** | 同上；线程池为 anyio 默认值，全仓未调 |
| 3 | 严重 | 安全 | 钉钉/飞书加签 `secret` 经 HTTP **全量明文出体**：GET 仅需 read（viewer 可读），PUT 回显也带它 | `api/main.py::_alert_channel_payload`；模型 `monitoring/notify.py::AlertChannel.secret` |
| 4 | 中 | SSE/内存 | 两处 SSE 用**无界** `queue.Queue`（全仓仅这两处）。docs/24 §3、docs/80 §3.5 已裁决「断连不是取消」，断连后 worker 继续灌帧、无人消费 → 内存无界增长直到运行结束 | 同 #1 两处 `events: queue.Queue()` |
| 5 | 严重 | 安全/数据 | openapi 导入 content_hash 去重竞态：迁移 023 只建了**非唯一**部分索引；`PgImportStore.add`/`restore` 事务内 SELECT→INSERT 存在 TOCTOU，并发同形导入可双写，去重承诺可被打破 | `db/migrations/023_openapi_imports_dedupe_softdelete.sql`、`openapi/pg_store.py::PgImportStore.add/restore` |
| 6 | 中 | 前端 | `streamRun` 用裸 fetch，无 `AbortController`/signal；Editor 里旧运行的迟到 SSE 帧会写进新运行的节点状态 | `frontend/src/lib/apiClient.ts::streamRun`、Editor 运行回调 |
| 7 | 中 | 契约/i18n | 裸中文 detail 被前端当控制流分支：改密旧口令错抛 `detail="原密码错误"`；模板重名两处抛 `detail=f"消息模板名称已存在：{...}"`——端点 docstring 已承诺 `MESSAGE_TEMPLATE_NAME_CONFLICT`，实现漂移。前端只能按中文字符串判分支 | `api/main.py::change_password`、`create_message_template`、`update_message_template` |
| 8 | 中 | 并发 | 内存档 RunStore 的 `_runs`/`_order` 无锁；`begin` 不裁剪、`list` 只截返回值不裁内存，长跑 demo 内存无界；begin/finish/list 并发可竞态 | `storage/memory.py` RunStore |
| 9 | 中 | 并发/性能 | `memory/items.py::update` 持有 `self._lock` 时在锁内调用 embedding provider（网络 IO），并发更新被一次远程 embed 全程串行化 | `memory/items.py::update` |
| 10 | 中 | demo 数据 | `demo_engine()` 为 `sqlite:///:memory:` ＋ `StaticPool` 单连接 ＋ `check_same_thread=False`：SSE worker 线程与请求线程共用同一连接/事务，并发写无隔离、可互相看到未提交态 | `database/service.py::demo_engine` |
| 11 | 中 | PG 并发 | `publish` 用裸 `SELECT COALESCE(MAX(release_version),0)+1` 再 INSERT，无 FOR UPDATE/ON CONFLICT；同图并发发布可撞复合主键 (graph_id, release_version)（迁移 002）而 500 | `storage/pg.py` publish 路径 |
| 12 | 低 | 可观测 | `message/service.py::_safe_record` 裸 `except Exception: pass`，消息投递落库失败无任何日志；文件顶无 logger | `message/service.py::_safe_record` |
| 13 | 中 | CI | CI 无 Chromium 安装步骤；浏览器集成测试 4 例在 `backend-integration` job 内因「launch 失败 pytest.skip」**恒 skip**，双层 skipif（模块级 ATLAS_RUN_INTEGRATION＋fixture launch）等于永不实跑 | `.github/workflows/ci.yml`、`tests/test_web_browser_integration.py` |
| 14 | 中 | 安全面 | `POST /api/demo/reset` 只有 administer 鉴权、**无环境 gate**：prod 且 demo 模拟面关闭时仍可调，与同族 mock 路由的 fail-closed 404（`_demo_mock_enabled()`）不一致 | `api/main.py::demo_reset` |

不在本批：`/api/audit/export` 的 StreamingResponse 是一次性 `iter([body])` 短下载，不占长生命周期线程，明确排除（见 §4）。

## 2. 选型裁决

四处需要拍板，其余 10 项修法机械（见 §3）。

### 2.1 #2 SSE 线程模型 — 拟记 ADR T36

- **a. worker 保持同步 daemon 线程跑 `run_graph`；帧经事件循环桥接；端点改 `async def`＋async 生成器**（采纳）
  - 请求线程（进入端点时即事件循环线程）解析租户/建 broker 等全部准备工作不变（06 §6.12 无线程上下文变量的纪律保留）；`loop = asyncio.get_running_loop()` 随闭包传入 worker。
  - worker 经 `loop.call_soon_threadsafe(bridge.offer, event)` 投帧，**不碰任何 asyncio 对象**；桥接器本身即 §2.2 的有界实现。
  - 生成器 `await asyncio.wait_for(bridge.get(), timeout=...)`：等待期间不占任何工作线程；超时发 SSE 注释心跳并复检线程存活（见 §3.1）。
- b. 把 `run_graph` 全链异步化（否决：引擎/工具/记忆全链同步，涟漪覆盖整个 `src/atlas`，远超审查修复范围；收益不付成本）。
- c. SSE 拆独立 ASGI app/端口配大线程池（否决：部署/鉴权/路由全部复制一份，是隔离症状不是治病）。
- d. 仅调大 anyio 线程池上限（否决：死线延后；同步 `/api/health` 仍可被任意多条长流饿死；无界资源本身错误）。

**代价（诚实登记）**：两个端点变 async 后，端点体内与 async 生成器内**不得新增任何阻塞调用**（既有准备逻辑在起 worker 前都是短路径，保持同步调用但须快速返回；租户服务解析仍是普通函数，允许）。worker 一侧零变化（仍是同步函数）。心跳为 SSE 注释帧（`: keepalive`），下游 EventSource 自动忽略，帧协议不新增事件类型。

### 2.2 #4 有界队列满策略 — 终帧必达、中间帧可丢、断连不取消

- **a. 有界桥接＋丢最旧中间帧＋终帧独立槽＋断连 close 变 no-op**（采纳）
  - 中间帧 deque 上限常量 `SSE_BRIDGE_MAXSIZE = 1024`（单图帧量是 O(节点数)，1024 远在正常形态之上；只有病态图或消费极慢才触顶）；满时弹最旧**非终帧**、丢帧计数＋1，达到阈值（如 100）记一次 warning 日志，不抛异常、不阻塞 worker。
  - 五类终帧哨兵（`__result__`/`__stopped__`/`__superseded__`/`__cancelled__`/`__error__`）入独立 terminal 槽：桥接器满也必收（必要时清空中间帧腾位），且终帧后再有 offer 一律忽略（恰好一次）。
  - 断连时 async 生成器被 aclose，`finally` 调 `bridge.close()`：此后 worker 的 offer 是廉价 no-op（内存占用归零），**worker 与 run_graph 不取消、不中断**——docs/24 §3 / docs/80 §3.5 决策原样保留。
- b. 无界队列＋满时阻塞 worker 做背压（否决：慢/死消费者能把引擎线程永久卡死；断连后必死锁）。
- c. 帧合并/coalesce（v1 不做：node_start/node_end 有配对语义，合错会让前端状态机错乱；登记重开，见 §8）。
- d. 借队列满或断连取消运行（否决：直接违反 docs/24 §3 显式决策；该决策的重开判据 docs/24 §66＝跨实例迁移，本批不触及）。

### 2.3 #10 demo SQLite 并发形态

- **a. 每 engine 独立临时文件库 ＋ WAL ＋ 多连接池**（采纳）
  - `tempfile.mkdtemp(prefix="atlas-demo-db-")` 下放 sqlite 文件；URL 指向该文件，**不再用 `:memory:`**。每次 `demo_engine()` 仍得全新独立库（现语义不变）。
  - 去掉 `StaticPool` 与 `check_same_thread=False`，用 SQLAlchemy 默认 QueuePool（多连接＝真隔离）；`connect_args={"timeout": 30}`（SQLite busy_timeout 语义，写串行时等待而非立刻 `database is locked`）。
  - engine 连上后 event listener 执行 `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`（建表/播种走 engine 直连的现路径不变，listener 对所有连接生效）。
  - 仍只依赖 stdlib sqlite3，「零依赖离线 demo/单测」定位不变；提供配套 dispose/清理（临时目录随 engine 生命周期清掉：应用 shutdown 与测试 fixture 各有落点，落码时按现有 fixture 形状接线）。
- b. 保 StaticPool 单连接＋外面套写锁（否决：单连接共享事务的无隔离本质不变，锁只会把串用藏得更深）。
- c. demo 直接上 PostgreSQL（否决：违背零依赖内存库定位，拖垮离线 demo 与无 PG 开发机）。
- d. `file:...?mode=memory&cache=shared` 共享缓存多连接（否决：最后一个连接关闭即删库、共享缓存自带表级锁语义差，连接生命周期脆弱）。

**代价**：相对 :memory: 有少量磁盘 IO（demo 数据量可忽略）；新增临时目录清理纪律（漏清是磁盘垃圾，不是正确性问题）；`reseed_demo`/`/api/demo/reset` 接线不改。

### 2.4 #13 CI 浏览器链路

- **a. 在既有 `backend-integration` job 加装 `playwright install --with-deps chromium` 一步**（采纳；playwright>=1.62 已在 `[dev]` 依赖，无需新依赖）。4 个浏览器用例从「launch 失败 skip」变实跑，CI 日志须能看到它们非 skip。
- b. 新开独立 browser job（否决：4 用例不值一套新 job 矩阵；用例数量增长后重评，见 §8）。
- c. 维持 skip、在 docs/13/docs/14 登记硬约束门 D67（否决：自动化测试永不进 CI 等于放任腐烂；双层 skipif 本来就是为「装上浏览器就能跑」预留的）。

**代价**：CI 每轮多一次浏览器下载安装（分钟级）。若实跑暴露 flaky，带失败证据回到本文记录并修测试，**不得回退为静默 skip**。

## 3. v1 范围（各项落码形状）

### 3.1 SSE 桥接器与两条流（#1/#2/#4）

新模块 `src/atlas/api/sse.py`（内聚、可脱离 FastAPI 单测；docs/09 模块表同步登记）：

- `SSEBridge`（命名落码时可调整，形状冻结）：
  - 构造：`SSEBridge(loop, maxsize=SSE_BRIDGE_MAXSIZE)`；内部＝deque（中间帧）＋terminal 槽（单值）＋`asyncio.Event`＋closed 标志＋dropped 计数；所有状态只在事件循环线程触碰。
  - `offer(event)`：**经 `call_soon_threadsafe` 调用**，故函数本身运行在 loop 线程，无需自己加锁；closed→直接丢弃；含终帧哨兵键→置 terminal 槽（已有关终帧则忽略）；deque 满→popleft 丢最旧、计数＋日志阈值告警；入帧后 set event。
  - `close()`：loop 线程调用（生成器 `finally`），置 closed、set event（唤醒可能在等的生成器）。
  - `async get()`：等 event；优先吐 deque 中间帧；deque 空后吐 terminal 并标记结束（由调用方 break）。提供「无中间帧且有终帧/已关闭」的判定供生成器退出。
- 生成器统一形态（shadow/run 各一份，终帧分派逻辑保持现状五类/一类）：

  ```text
  while True:
      try:
          event = await asyncio.wait_for(bridge.get(), SSE_HEARTBEAT_SECONDS)
      except TimeoutError:
          if not thread.is_alive() and bridge 无中间帧无终帧:
              合成 __error__ 终帧（见下）→ 发帧 → break
          yield ": keepalive\n\n"; continue
      按现有协议分派；终帧 break
  finally:
      bridge.close()
  ```

  心跳间隔取常量（拟 15 秒，落码时确认常见反代空闲超时量级；常量在 sse.py 顶部）。
- **线程暴毙兜底**（#1 的悬挂修复）：worker 死因不明且未留终帧时，run 流合成 `__error__` 帧，形状沿用 `runtime_error_meta`/`_safe_run_error` 纪律——固定 code `RUNTIME_UNEXPECTED`、固定安全中文 message（不拼内部异常串），细节由 worker 侧 `logger.exception` 落日志；shadow 流无 result 可发时合成 `event: error`（结构化 detail 同族），**不伪造 ShadowRun**。这是基础设施级错误通道，与 docs/114「影子运行异常沉淀 status=error 记录、HTTP 不报错、不设 event:error」不冲突——那条约束针对**运行业务异常且已沉淀**的路径；worker 暴毙＝连沉淀都没发生，收口时在 docs/114 补一行边界说明。
- **worker 收尾兜底**（#1）：
  - shadow：`extract_shadow_events` 与 `shadow_store.add` 整体纳入 try；`finally` 保证恰好一次终帧投桥（成功 result／异常时尽力沉淀 error record；连沉淀都失败走暴毙路径由生成器合成）；收尾内二次异常只 `logger.exception`，不再让线程裸死。
  - run：`finish`/`record_run`/`evaluate_after_run` 包保护——它们失败时落日志并转 `__error__` 终帧（safe message），不让异常逃出 worker；`cancellation_broker.unregister` 保持在最内层 finally；任何执行路径恰好一个终帧。
  - 现 run worker 的「异常→finish/record 又异常」嵌套结构改为：收尾集中到一个 `_finalize_run(...)` 内聚函数，各异常分支只决定终帧种类。

### 3.2 告警渠道 secret 投影脱敏（#3）

- 只改 HTTP 投影，不改模型、不改存储、不动加签发送路径（`notify.py` 出向用明文 secret 不变）。
- `_alert_channel_payload`：`secret` 非空 → `f"***{secret[-4:]}"`（与 `_masked_view` 同族同形）；空串仍回 `""`；键保留、其余字段不动。
- GET（read 权限）与 PUT 回显走同一投影，天然一致。
- **PUT 占位保留**：`update_alert_channel` 是整体替换（raw 的 secret 直接落库），脱敏后前端回显会把 `***1234` 提交回来。PUT 端点在校验/落库前归一化：提交值等于当前 GET 投影值（含仅 `"***"` 的坏信封形态）→ 替换为库内现存 secret 后再走原流程；空串语义不变（清空）。不采用 `startswith("***")` 宽匹配，只认与当前投影逐字相等，避免误伤真以此开头的新密钥。
- f4 落码时核对前端告警设置表单：现状若直接回填 secret 输入框，靠后端占位保留即零破坏；不额外改前端（若发现表单另有清空/必填逻辑再按最小改动调）。

### 3.3 openapi imports 唯一约束迁移 048（#5）

- 新迁移 `db/migrations/048_openapi_imports_hash_unique.sql`（幂等，仿既有迁移可重复执行）：
  1. 存量去重：对每个 `(tenant_id, content_hash)` 中 `deleted_at IS NULL` 的多行，**保留 seq 最小行**，其余置 `deleted_at`（UTC ISO 文本，与软删列现状同族；不 DELETE，保住历史/审计语义）；content_hash='' 的存量行（023 前老数据默认值）不参与——只去重非空指纹。
  2. `DROP INDEX IF EXISTS idx_openapi_imports_hash;`
  3. `CREATE UNIQUE INDEX IF NOT EXISTS idx_openapi_imports_hash ON openapi_imports (tenant_id, content_hash) WHERE deleted_at IS NULL AND content_hash <> '';`（落码校正：谓词必须排除 `''`——若库内存在多个 023 前 `''` 老行，不带该条件唯一索引直接创建失败；023 后新指纹恒为 sha256 hex，不受影响。）
  4. 幂等写法：去重用 CTE＋ROW_NUMBER，可重复执行（二跑零行受影响）。
- `PgImportStore.add`：保留 SELECT 预检（给出既有的友好 409 形状），另捕获 INSERT 上的 `IntegrityError` → rollback 后折同形 409 `OPENAPI_SPEC_ALREADY_IMPORTED`（code/message/params 与预检路径一致）；`restore` 同补（restore 的唯一冲突＝恢复一个与现存未删行同指纹的规格）。
- 内存档 store 若有同构去重路径，落码时核对行为一致性（内存档线程模型不同，不强行加竞态测试）。

### 3.4 前端 SSE 可中止与运行分代（#6）

- `streamRun` 增可选 `signal?: AbortSignal` 参数透传 fetch；abort 时关闭连接、清理回调，不把 abort 当业务错误弹框（既有调用点不传＝行为不变）。
- Editor 运行侧加**分代守门**：发起运行时 `generation += 1`，闭包捕获当次 generation；所有 SSE 帧回调先比对当前 generation，旧代帧直接丢弃（`setNodeStatus` 等 editorStore 签名不动，守门放 Editor 回调层）。
- 影子流封装（apiClient 内 shadow stream 调用）同步评估：同样接 signal／分代或在组件侧守门，落码时以实际消费点为准，原则同一条：迟到帧不得写新运行。
- 不做「abort＝通知后端取消运行」：断连不取消（docs/24 §3）；已有专门的取消端点承担取消语义，signal 只管前端连接生命周期。

### 3.5 结构化 detail 替代中文控制流（#7）

- 后端三处改 `detail={"code","message","params"}`（全仓既有 12 处同形先例）：
  - 改密旧口令错：400，新码 **`AUTH_OLD_PASSWORD_INVALID`**（登记 docs/03 错误码族；message 用稳定中文文案，不含账户是否存在等额外信息）。
  - 模板重名 create/update：409，`MESSAGE_TEMPLATE_NAME_CONFLICT`，`params={"name": ...}`（补齐 docstring 已承诺的契约）。
- 前端错误对象携带 code：`requestError` 现只取 message，扩为解析结构化 detail 并挂 `code`（不识别结构时回退现状 message，零破坏）；`MessageTemplates.tsx` 与 `UserBadge.tsx` 的分支从匹配中文串改为判 `error.code`。
- i18n：`nameConflict`/`oldPasswordWrong` 键已存在，**预计 runtime.json 零新增键**；落码实跑确认，若确需新增按 docs/17 流程走。
- 同端点的 422「新密码不能与原密码相同」、模板 404 文案**不在本批**（#7 取证未列，避免搭车扩面）。

### 3.6 后端并发/日志小项（#8/#9/#11/#12）

- **#8 RunStore**：`_runs`/`_order` 所有读写经一把 `threading.Lock`；有界化 `_order`——只裁**终态**记录并保留最新 N（常量落码时与 list 上限族对齐，拟 200）；`running`/`suspended` 状态记录不参与淘汰（它们要等终帧，裁掉会丢状态机）；list 返回切片不再是唯一防线。
- **#9 embedding 移出锁**：`update` 改为「短锁取 target＋判定 content 是否变化 → 锁外调 embed → 再短锁写入，写入时二次确认版本/内容未变」；内容未变跳过 embed（现语义若已有短路则保留）。
- **#11 publish 原子化**：INSERT 捕获 `(graph_id, release_version)` 唯一冲突 → rollback → 重查 MAX ＋1 重试，有限次数（拟 3）后仍失败才抛（带日志）；不取 advisory lock（避免持锁跨外部调用与锁泄漏）。重试路径在 PG 集成门实跑并发对。
- **#12 _safe_record**：文件顶加 `import logging`＋`logger = logging.getLogger(__name__)`；裸 except 改 `logger.exception(...)`（保留不阻断主流程的语义，函数名与调用点不动）。

### 3.7 CI 与 demo reset 门（#13/#14）

- **#13**：`backend-integration` job 在 `Install package` 后加 `Playwright install chromium`（`playwright install --with-deps chromium`）；不新增 job、不新增依赖。
- **#14**：`demo_reset` 入口加同族 gate——`if not _demo_mock_enabled(): raise HTTPException(404, "Not Found")`（与 5805-5808 mock 路由逐字同形）。鉴权顺序仍是先 administer 后 gate。
  - **兼容性铁证**：约 25 处既有测试经 TestClient 调 reset（`tests/test_api_demo.py` 等），默认 profile 下 demo 面开，行为必须不变；f8 落码后全量 pytest 零改动通过即为验收。
  - 与 docs/89 A-9（reset 清全局 `_MOCK_SHOPIFY_WEBHOOKS`，已文档化 by-design）是两件事，本项不碰 A-9。

## 4. 非目标（明确不做）

- 不做 `run_graph` 异步化（§2.1 b）；不拆 SSE 独立服务（§2.1 c）；不调线程池上限糊脸（§2.1 d）。
- 不改「断连不是取消」语义（docs/24 §3、docs/80 §3.5）；不做跨实例 SSE 中继（docs/24 §66 判据未触发）。
- 不做中间帧合并/coalesce（§2.2 c）。
- 不改告警渠道模型、不做 secret at-rest 加密（本批只修 HTTP 投影泄漏；钉钉/飞书加签 secret 存配置明文是另一个议题，如需另立）。
- 不动 `/api/audit/export` 的一次性 StreamingResponse（非长流）。
- 不清理 #7 范围外的其他中文 detail（改密 422 同密码、模板 404 等）。
- demo SQLite 不换 PG、不用 shared-cache（§2.3）。
- 不新增任何运行时/CI 依赖；不加 git hook。
- 不借本批重构 SSE 帧协议：终帧五类（result/stopped/superseded/cancelled/error）与哨兵键不变，心跳是 SSE 注释而非新事件。

## 5. 受影响面清单与契约同步矩阵

| 面 | 文件 | 改动 |
|---|---|---|
| SSE 基础设施 | `src/atlas/api/sse.py`（新） | `SSEBridge`、心跳/暴毙兜底常量；纯 Python 可单测 |
| SSE 端点 | `src/atlas/api/main.py` | shadow/run 两端点 async 化、worker 收尾兜底、`_finalize_run` 内聚 |
| 告警渠道 | `src/atlas/api/main.py`（投影＋PUT 归一化） | secret 脱敏、占位保留；模型/发送不动 |
| openapi 去重 | `db/migrations/048_*.sql`（新）、`src/atlas/openapi/pg_store.py` | 唯一部分索引、存量去重、IntegrityError 折 409 |
| 前端 SSE | `frontend/src/lib/apiClient.ts`、Editor 运行组件、shadow 流消费点 | signal 透传、分代守门 |
| 错误契约 | `src/atlas/api/main.py`（3 处）、`apiClient.ts`、`MessageTemplates.tsx`、`UserBadge.tsx` | 结构化 detail、ApiError.code、分支改读 code |
| 内存档 | `src/atlas/storage/memory.py`、`src/atlas/memory/items.py` | RunStore 锁/有界；embed 移出 update 锁 |
| demo 数据 | `src/atlas/database/service.py` | 临时文件 WAL 库＋dispose 清理接线 |
| PG 发布 | `src/atlas/storage/pg.py` | publish 版本号冲突有限重试 |
| 消息 | `src/atlas/message/service.py` | logger.exception |
| CI | `.github/workflows/ci.yml` | backend-integration 加装 Chromium |
| demo 门 | `src/atlas/api/main.py::demo_reset` | demo 面 gate |
| 测试 | tests/ 对应新增＋前端 vitest 对应新增 | 见 §7 |

文档同步（落码收口批逐份走改内容流程）：

- **docs/03**：登记新错误码 `AUTH_OLD_PASSWORD_INVALID`（错误码族）；`MESSAGE_TEMPLATE_NAME_CONFLICT` 补实现一致声明；SSE 终帧协议段补「keepalive 注释帧」与暴毙 error 合成路径（若该段在 03）。
- **docs/12**：迁移台账登记 **048**（文件名、去重与唯一索引语义、可重入）。
- **docs/17 §2.4**：结构化 detail 族加 AUTH_OLD_PASSWORD_INVALID；确认前端 i18n 零新增键（若实跑有新增按 i18n 流程登记）。
- **docs/10 §4**：ADR **T36**（§2.1 全文）；随之 **docs/02**（架构选型总览 SSE 线程模型表述）、**docs/09**（新增 `api/sse.py` 模块行＋模块→文档映射）三处同步。
- **docs/24 §3**：补一行「有界桥接、断连 close 后 offer no-op」是对断连不取消决策的**实现细化**，决策本身不改；**docs/80 §3.5** 同向注记。
- **docs/114**：影子 worker 暴毙（沉淀未发生）走基础设施 error 帧的边界说明。
- **docs/89**：demo reset 新增环境 gate 的台账条（区别于已闭合 A-9）。
- docs/13（U1373–U1392 转正式）、docs/14（本批注记；#13 采方案 a 后不新增 D67）、docs/00 地图、CHANGELOG、handoff 收口同步。

## 6. 原子提交计划

每原子独立过门；英文 message、`fix/feat/refactor/test/chore/docs(scope): subject`，不 push、不加 co-author。

1. `feat(api)`：新增 `api/sse.py`（SSEBridge＋常量）及其脱离 FastAPI 的单测（满队丢帧计数、终帧必达、close 后 no-op、终帧恰好一次）。
2. `fix(api)`：shadow 流 worker 收尾兜底＋端点 async 化接桥（#1/#2/#4 shadow 半）。
3. `fix(api)`：run 流 `_finalize_run` 收尾兜底＋端点 async 化接桥（#1/#2/#4 run 半）。
4. `test(api)`：SSE 回归——40+ 并发长流下同步健康检查仍 200（线程池不饿死）；断连后桥接 close 不积压且运行不取消；worker 暴毙必达 error 终帧且不挂死。
5. `fix(api)`：告警渠道 secret GET/PUT 脱敏＋PUT 占位保留（#3），含 viewer 视角测试与回显不改密钥测试。
6. `chore(db)`：迁移 048（#5 结构）；`fix(openapi)`：add/restore 捕获 IntegrityError 折 409（#5 代码）＋并发/重入测试。
7. `fix(api)`：三处结构化 detail（#7 后端）＋对应后端测试。
8. `feat(frontend)`：ApiError 携带 code＋两处消费改读 code（#7 前端）。
9. `fix(frontend)`：streamRun signal＋Editor 分代守门＋影子流守门（#6）。
10. `fix(memory)`：RunStore 锁与有界 _order（#8）；`fix(memory)`：embed 移出 update 锁（#9）——分两原子。
11. `fix(database)`：demo SQLite 临时文件 WAL 形态＋清理接线（#10）。
12. `fix(storage)`：publish 版本号有限重试（#11）。
13. `fix(message)`：_safe_record 改 logger.exception（#12）。
14. `ci`：backend-integration 加装 Chromium（#13），提交说明写明 4 用例转实跑。
15. `fix(api)`：demo reset 环境 gate（#14）＋gate 测试（prod＋面关→404；默认 profile 既有测试零改动）。
16. `docs`：§5 契约矩阵全部收口（03/12/17/10 T36/02/09/24/80/114/89/13/14/00/CHANGELOG/handoff）。

落码顺序原则：基础设施先行（原子 1），两条流随后；安全项（5/6/7）独立可回溯；并发小项彼此无耦合可串跑；docs 收口最后一个原子。

### 6.1 落码进展（2026-10-13，docs-only 进展同步，非收口）

落码中途的如实回填，不改变 §6 计划与 §8 重开判据。下列 16 个 commit 均在 dev、**未 push**（origin/dev 仍为 `e44361b`）；本批全量门未跑（U1392 待验），**不构成收口**。原子 1-12 已落（原子 10 拆 10a/10b 两个 commit），原子 14-15 未落；**原子 13（#12 `_safe_record` 补日志）代码已在工作树、未提交**（2026-10-13 05:11 复核：`src/atlas/message/service.py` 一文件在途、暂无随附测试，不计为已落）。

已落 commit（时序哈希链）：

| 原子 | commit | 说明 | 对应用例 |
| --- | --- | --- | --- |
| 立项 | `fb0ef17` | docs(plan) 批立项（docs-only） | — |
| 1 | `1e70dcd` | feat(api) 新增 `api/sse.py` SSEBridge（deque 1024 有界＋终帧独立槽）＋脱离 FastAPI 单测 | U1377 |
| 2 | `79a9b00` | fix(api) shadow 流 worker 收尾兜底＋端点 async 化接桥 | U1373 |
| 3 | `d800a9b` | fix(api) run 流 `_finalize_run` 收尾兜底＋async 接桥（本原子无自带测试，用例随原子 4） | U1374 等 |
| 4 | `241c69d` | test(api) `tests/test_sse_stream_resilience.py` SSE 回归（终帧/心跳/池不饿死/断连） | U1374/U1375/U1376/U1378 |
| ADR | `4617a74` | docs(adr) T36 daemon 线程＋`call_soon_threadsafe` 投帧模型，同步 02/09/10 | — |
| 5 | `da025b3` | fix(api) dingtalk/feishu 加签 secret HTTP 投影脱敏＋PUT 占位逐字相等不覆盖 | U1379 |
| 6 | `132c3d9`＋`d3db0ae` | chore(db) 迁移 048（去重＋DROP 旧索引＋带 `content_hash <> ''` 谓词的 UNIQUE 部分索引，幂等）；fix(openapi) add/restore 捕 IntegrityError 折同形 409 | U1380/U1381 |
| 7 | `898f9ce` | fix(api) 三处结构化 detail（旧口令 400／模板撞名 409×2）＋后端测试 | U1383 |
| 8 | `898f9ce`＋`3d3a417` | fix/feat(frontend) 管线＋消费端：见下「原子 8 落点」 | U1384 |
| 9 | `ce1023b` | fix(frontend) `RunGeneration` 分代守门＋两流可选 signal＋Editor/ShadowRunModal 全链守门（commit 另含 apiClient signal 测试 94 行） | U1382 |
| 10a | `ea2e490` | fix(memory) RunStore 一把 `threading.Lock` 包全部读写＋终态记录有界 N=200（`_evict_terminal_locked` 只裁终态，running/suspended 保留） | U1385 |
| 10b | `2176327` | fix(memory) embed 移出 update 锁：短锁取快照→锁外 embed→短锁乐观二次确认（冲突按新快照重试一轮，仍冲突抛 StorageError，锁外被删返 None），content 未变短路零 embed；3 文件 +160/-11 | U1386 |
| 11 | `9b0a52c` | fix(database) demo SQLite 弃 `:memory:`＋StaticPool，改 mkdtemp 临时文件库＋默认 QueuePool 多连接隔离＋busy timeout 30s＋connect 监听器 WAL/NORMAL；`dispose_demo_engine` 连池带目录释放，`DatabaseClient.close` 委托、lifespan 关闭、atexit 兜底；3 文件 +152/-7，新增 3 用例 | U1387 |
| 12 | `372d1d4` | fix(storage) `PgGraphStore.publish` 捕 SQLSTATE 23505：整事务回滚、重查 MAX+1 重试最多 3 次＋指数抖动退避（非 23505 即抛、不取 advisory lock）；2 文件 +137/-29，新增 2 个 PG 并发例 | U1388 |

原子 8 落点（跨两个 commit）：`898f9ce` 落管线半边——`AUTH_ERROR_KEYS` 加映射、runtime.json zh/en 新键、U1162 http-facing 集合守护；`3d3a417` 落消费端半边——新增 `ApiError`（携带 code/params，裸字符串 detail 回退为无 code 的 ApiError），`UserBadge.tsx`、`MessageTemplates.tsx` 改为 `instanceof ApiError` 按 `error.code` 分支，不再匹配中文文案，并补 `frontend/src/lib/__tests__/apiClient.test.ts` 三个用例（结构化 code/params、AUTH 码映射、裸串回退）。

偏差与校正（照实记录）：

1. **i18n 键数偏差**：立项预测「runtime.json 零新增键，以实跑为准」（§7 U1384、§5 矩阵 L192 预留）。实跑新增 **1 键** `MESSAGE_TEMPLATE_NAME_CONFLICT`（zh/en runtime.json，96→97 键，支持 `{{name}}` 插值）；`AUTH_OLD_PASSWORD_INVALID` **零新增**，经 `AUTH_ERROR_KEYS` 映射复用 users 命名空间既有键 `users.error.oldPasswordWrong`。已按 docs/17 §2.4 流程登记。
2. **前端实现路径**：未新造错误通道，复用既有 `resolveErrorMessage` 管线（AUTH_ERROR_KEYS 映射＋runtime catalog 动态成员）。与 §6 原子 8「ApiError 携带 code＋两处消费改读 code」目标一致，区别仅在错误解析复用既有管线而非另立。
3. **catalog 路径校正**：`898f9ce` 初版把 AUTH 码映射到 `error.auth.oldPasswordWrong`（common ns，实际无此键）；`3d3a417` 校正为文案唯一定义所在的 `users.error.oldPasswordWrong`，并由前端测试钉死该路径（错误路径会令 i18next 回退为键串本身）。

新落三原子（2026-10-13 05:11 复核；均由用户按原子规则自行入库；原子 13 代码在工作树在途，见下「未落／在途」）：

- **原子 10 第二半（#9 memory embed 移出 update 锁，U1386）已入库 `2176327`**（2026-10-13 04:56:15，3 文件 +160/-11）：
  - `src/atlas/memory/items.py`（+47/-11）：`MemoryStore.update` 重构为乐观并发——短锁取 target 快照并 `merge_manual_update`；content 未变沿用短路（锁内一次写完、零 embed）；content 变化则**锁外**调 `self._provider.embed(...)`，再持短锁二次确认 `public_memory(target)` 与基线未被并发改写后写入；期间被并发改写则丢弃过期向量、基于最新快照整体重试一轮（共 2 轮）；锁外等待期间记录被删则返回 `None`（与「不存在」同形）；两轮均冲突抛 `StorageError`（绝不写入与 content 不对应的过期向量）。docstring 自引用「项目级审查发现 #9（docs/125 §3.6，U1386）」。
  - `src/atlas/storage/base.py`（+2/-1）：`StorageError` docstring 补「进程内实现仅在极端乐观并发冲突时抛」。
  - `tests/test_memory_manual_write.py`（+121）：新增 4 个 U1386 用例——锁外 embed 断言（instrumented embedder 以 `acquire(blocking=False)` 反证持锁；content 不变零 embed）、embed 期间并发改写字段触发按新快照重试且并发修改保留（embed 恰 2 次、落第二次向量）、embed 期间被删返回 None 且不再 embed、持续冲突抛 `StorageError`。
- **原子 11（#10 demo SQLite 临时文件库 WAL，U1387）已入库 `9b0a52c`**（2026-10-13 05:00:23，3 文件 +152/-7）：`src/atlas/database/service.py`（+73/-7）`demo_engine` 弃 `sqlite:///:memory:`＋StaticPool，改为 `tempfile.mkdtemp(prefix="atlas-demo-db-")` 内独立文件库＋默认 QueuePool（多连接真隔离）＋`timeout=30`（busy_timeout 30s）＋connect 监听器对每条连接开 `PRAGMA journal_mode=WAL`/`synchronous=NORMAL`；`check_same_thread=False` 保留（QueuePool 串行复用连接，池保证同一时刻独占）；新增 `dispose_demo_engine`（连池 dispose＋临时目录 rmtree，非临时库只 dispose）、`DatabaseClient.close` 委托；`src/atlas/api/main.py`（+3）lifespan shutdown 关闭 demo client；模块级 atexit 兜底清理未显式释放的临时目录。`tests/test_database_adapter.py`（+83/-0）新增 3 个 U1387 用例：临时文件＋WAL pragma 实证、多 engine 独立/重播种仅本库可见、多线程并发写无 `database is locked`。
- **原子 12（#11 graph publish 唯一冲突重试，U1388）已入库 `372d1d4`**（2026-10-13 05:07:15，2 文件 +137/-29）：`src/atlas/storage/pg.py` `PgGraphStore.publish` 原单事务裸 MAX+1，并发撞 `graph_versions` 主键出 500；改为捕 `IntegrityError` 且 pgcode＝23505（psycopg3 `sqlstate`/psycopg2 `pgcode` 双驱动识别，非 23505 立即抛）则整事务 rollback、重查 MAX+1 重试，`_PUBLISH_MAX_ATTEMPTS=3`，指数退避＋抖动（`0.005*attempt + random*0.01*attempt` 秒），耗尽时 `logger.exception` 后 re-raise；缺图仍立即抛，不取 advisory lock（注释明示避免持锁跨外部调用与锁泄漏）。`tests/test_storage_pg_integration.py`（+68）新增 2 个 U1388 PG 例：barrier 同刻并发发版版本号唯一连续；3 线程各连发 4 次共 12 版恰 1..12、无 500。**PG 集成门未见实跑记录**（需 `ATLAS_RUN_INTEGRATION=1`＋`DATABASE_URL`）。
- **局部验证（非 U1392 全量门，05:00 复跑）**：`pytest tests/test_memory_manual_write.py` **13 passed**（9 既有＋4 新增 U1386）；`pytest tests/test_database_adapter.py` **46 passed**（含 3 个新增 U1387）。后端全量 pytest 未跑。
- 前序在途的原子 10 第一半（#8 RunStore 锁与终态有界）已于 2026-10-13 04:50 入库 `ea2e490`（见上表）。

未落／在途（经代码实证，截至 2026-10-13 05:11）：

- 原子 10（#8/#9）：**两半均已入库**——#8 `ea2e490`（U1385）、#9 `2176327`（U1386），均随转正式。
- 原子 11（#10）：**已入库** `9b0a52c`（demo SQLite mkdtemp 临时文件库＋WAL/QueuePool/timeout＋dispose/atexit 清理，U1387 随转正式）。
- 原子 12（#11）：**已入库 `372d1d4`**（2026-10-13 05:07:15，U1388 随转正式）——实现要点见上「新落三原子」小节：最多 3 次尝试（`_PUBLISH_MAX_ATTEMPTS=3`）、捕 `IntegrityError` 且 pgcode＝23505（psycopg3 `sqlstate`/psycopg2 `pgcode` 双驱动识别，非 23505 立即抛）、整事务回滚重查 MAX+1、指数退避＋抖动（`0.005*attempt + random*0.01*attempt` 秒）、耗尽 `logger.exception` 后 re-raise、缺图立即抛、不取 advisory lock；2 个 U1388 PG 例（barrier 并发、3×4 连发）。**PG 集成门未见实跑记录**（需 `ATLAS_RUN_INTEGRATION=1`＋`DATABASE_URL`）。
- 原子 13（#12）：**代码在工作树、未提交、不计已落**（2026-10-13 05:11 复核，单文件 `src/atlas/message/service.py`、暂无随附测试）——`_safe_record` 已加 `import logging` 与模块级 `logger = logging.getLogger(__name__)`，docstring 注记「审查发现 #12（docs/125 §3.6，U1389）：失败必须留可观测痕迹，不再裸 ``except: pass`` 静默吞掉」，except 分支改调 `logger.exception("delivery record persistence failed (non-blocking): id=%s channel=%s status=%s", rec.id, rec.channel, rec.status)`。
- 原子 14（#13）：`.github/workflows/ci.yml` 未装 Playwright/Chromium（仅 L60 注释提及）。
- 原子 15（#14）：`POST /api/demo/reset`（`api/main.py` L6334-6354）函数体仍无 `_demo_mock_enabled()` 入面 gate（周边 demo 端点均有）。
- 原子 16（§5 契约矩阵收口）：本次仅进展同步；待原子 13-15 落完、U1392 全量门绿后再收口。

## 7. 验收用例（U1373–U1392 预留，落码时转正式）

- **U1373** shadow worker 收尾异常（`shadow_store.add` 抛错注入）仍终帧可达、连接不悬挂、线程不裸死。
- **U1374** run worker 在 except 分支内 `finish`/`record_run` 二次抛错时仍发出且只发一个 `__error__` 终帧；`unregister` 必执行。
- **U1375** 生成器存活机制：心跳注释帧按间隔下发；worker 暴毙且队列空时合成 error 终帧并正常关闭流（无永久挂死）。
- **U1376** 线程池不饿死：≥40 条长生存 SSE 流（wait/审批阻塞）并发期间，同步 `GET /api/health` 持续快速 200。
- **U1377** 有界桥接：生产超过 1024 中间帧时丢最旧帧、计数与告警日志可见；终帧在满载下仍必达且恰好一次。
- **U1378** 断连不取消：客户端断开后桥接 close、后续 offer no-op（内存不增长可观测），后台 run_graph 继续至结束（run_store/帧路径照常）。
- **U1379** secret 脱敏：viewer GET 与 administer PUT 回显均为 `***`＋尾 4（空 secret 回空串、键保留）；PUT 提交脱敏占位不覆盖原密钥；出向加签仍用明文原值。
- **U1380** 迁移 048：库内重复指纹去重保留最小 seq（余软删）、旧索引 DROP、UNIQUE 部分索引建立；脚本二跑幂等零改动。
- **U1381** add/restore 竞态：并发同形导入其中一路折 `OPENAPI_SPEC_ALREADY_IMPORTED` 同形 409（code/message/params 与预检一致），无双写；restore 撞现存同形同样 409。
- **U1382** 前端分代：旧运行迟到帧（含终帧）不写入新运行节点状态；abort 旧 run 不弹业务错误；不传 signal 的既有调用点行为不变。
- **U1383** 结构化 detail：旧口令错 400 带 code `AUTH_OLD_PASSWORD_INVALID`；模板 create/update 撞名 409 带 `MESSAGE_TEMPLATE_NAME_CONFLICT` 与 params.name。
- **U1384** 前端按 `error.code` 分支命中既有 i18n 文案（runtime.json 零新增键，以实跑为准）；非结构化错误回退 message 不破坏。
- **U1385** RunStore：begin/finish/list 并发无竞态；终态超 N 条内存有界（running/suspended 不被裁）。
- **U1386** update embed 不持锁：embed 慢调用期间其他 key 的更新/读取不被阻塞；写入二次确认防并发覆盖。
- **U1387** demo SQLite：多线程并发写 orders/重播种互不串事务、无 `database is locked` 失败；engine 间相互独立；临时目录随清理释放。
- **U1388** publish 并发：同图多线程发布版本号连续唯一、无 500（PG 集成门实跑）。
- **U1389** _safe_record 落库失败时 `logger.exception` 有日志且主流程不被阻断。
- **U1390** CI `backend-integration`：4 个浏览器用例实跑非 skip（CI 日志可核验），失败可见不回退静默 skip。
- **U1391** reset gate：prod 且 demo 面关 → 404；面开或非 prod → 现状行为；默认 profile 下既有约 25 处 reset 测试零改动通过。
- **U1392** 全量门零回归：后端全量 pytest（含 PG 集成 ATLAS_RUN_INTEGRATION=1）、前端 vitest＋oxlint＋tsc＋build 全绿；零新增依赖。

## 8. 重开判据

- **run_graph 异步化（§2.1 b）**：引擎核心依赖（工具调用/记忆/LLM 客户端）全面 async 化，且同步线程＋桥接被实测证明为瓶颈时，携全链异步设计另立 ADR。
- **帧合并/coalesce（§2.2 c）**：真实大图出现中间帧触顶丢弃并造成前端状态可见错乱时，设计带版本号的状态帧压缩（node_start/end 配对语义必须先有会话校正机制）。
- **跨实例 SSE（docs/24 §66）**：运行迁移到多实例/跨进程接管落地时，本批进程内桥接整体让位于中继方案，有界策略在中继层重新评估。
- **CI 独立 browser job（§2.4 b）**：浏览器用例增长（跨浏览器矩阵、多页面录制等）使 integration job 时长不可接受时拆分。
- **告警 secret at-rest 加密**：威胁模型从「HTTP 投影越权可见」扩展到配置存储落盘时另立；本批不预设。
- **demo 数据库**：离线 demo 出现 SQLite 语义与 PG 不一致的真实缺陷（而非假想）时，重评 demo 数据档形态。
