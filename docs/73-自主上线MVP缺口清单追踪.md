# Atlas 自主上线 MVP 缺口清单追踪（docs/73）

> **状态**：🟡 追踪中（2026-09-27 立项）。本文件是 `docs/72`（第四次项目级上线复审）的**配套活文档**——docs/72 给出「A 档 MVP 达成 / B 档（自主上线）未达成」的结论，本文把 B 档缺口拆成可追踪的明细项，随落码/落配置更新状态。docs/72 的判定结论与本文冲突时，以 docs/72 的结论为准；本文只 tracker 缺口，不改判定。
>
> **基线（docs/72，2026-09-27）**：A 档＝可演示 / 可陪同试用 MVP ✅ 达成；B 档＝产品核心完全可用的生产 MVP（自主上线）❌ 未达成。已闭合前提：S1–S8 全部 remediation、N1/N2/N4 代码侧闭合、单实例闭环可跑。本文只列「从 A 到 B 还差什么」。

## 0. 追踪口径

- **状态图例**：⬜ 未启动 · 🟡 进行中 · ✅ 完成 · ⛔ 阻塞（附阻塞原因）。
- **证据级**（同 docs/63 §0 / docs/72 §0）：〔跑〕＝本仓库执行过命令；〔码〕＝逐行读码确认；〔勘〕＝子代理勘察未复。状态翻 ✅ 须带证据级。
- **当前门（实跑，2026-09-27）**：后端 `pytest` 1951 passed / 119 skipped / 0 failed；前端 `pnpm test` 738 passed / 2 skipped、`pnpm lint` 0 error / 7 既有 warning、`pnpm build` ✓。（**该行为 2026-09-27 上午立项时读数，已过期**）
- **当前门（2026-09-27 第六次复审，CI runner 自己读数，SHA `3087495`／run `36306384856`，四 job 全绿）**：后端 `pytest` **1979 passed / 125 skipped / 0 failed**；**PG 直连 `pytest`（CI 开 `ATLAS_RUN_INTEGRATION`）2099 passed / 5 skipped**；前端 **738 passed / 2 skipped**、`pnpm build` ✓；gitleaks ✓。本机同日全量**未出数**——同机 load 一度 229–309，跑到 40 分钟我主动终止，**不拿抖动值也不拿 CI 数冒充本地数**。
- **刷新规则**：任一项翻 ✅ 时，在同一行补「证据」与「完成日期」，并在文末变更流水追加一行；不删除历史行。

## 1. 缺口总览（按工作流）

| 工作流 | 主题 | 明细项 | 状态汇总 |
|---|---|---|---|
| W1 | 真实外部凭据（硬阻断） | 1.1 真实 LLM 决策 / 1.2 真实 Shopify 退款 / 1.3 真实消息·IM·SMTP·SMS·OAuth | ⬜×3 |
| W2 | N3 范围决策（产品非工程） | 2.1 浏览器自动化进/出 MVP | ✅ **2026-09-27 已表态＝不进 MVP（非目标），复开条件已写** |
| W3 | prod 形态持久化与韧性 | 3.1 调度 store PG 化（D32）/ 3.2 D36 崩溃兜底批 / 3.3 多副本解锁（后置） | ✅×1（3.1 已完成，2026-09-27 更正）／⬜×1（3.2；3.3 后置不计入 B 档） |
| W4 | prod 演练（终门） | 4.1 `ATLAS_ENV=prod` 真机演练 | ⬜（**2026-09-27 记部分推进**：prod 档进程第一次被真驱动，演练本身仍未做） |
| W5 | 核心闭环**工程**断口（第六次复审 docs/77 新增） | 5.1 `ai_decision` 真实化（R1）／5.2 prod 形态图内演示面收口（R2）／5.3 假阳性三小项（R6／R7／R8） | ✅×3（**2026-09-27：5.1 已闭合（走真路线，打包 R＝docs/78，R1 关闭）；5.2 已闭合（R2＋R4＋R5，并含 R3）；5.3 已全部闭合（R6／R7 落码＋测试；R8 口径＝prod fail-closed，U939–U943）**。三条都不依赖外部凭据，不补完它们 1.1–1.3 配了凭据也推不绿 B 档） |
| X | 横切前提 | X.1 CI postgres service / X.2 凭据卫生（N5）／**2026-09-27 已闭合** / X.3 demo 模拟面 prod 残留闸门（2026-09-27 新增并当日闭合） | ✅×3（X.1 已在跑；X.2 全史扫描零命中＋示例值有机检；X.3 由打包 P 闭合）／⬜×0 |

## 2. 明细追踪表

| ID | 工作流 | 动作 | 完成判据（Done-when） | 依赖 | 状态 | 证据 | 备注 |
|---|---|---|---|---|---|---|---|
| 1.1 | W1 | 配 `LITELLM_MODEL` + 商业 API key 入 prod 秘钥；`ATLAS_ENV=prod` 时禁用 demo/mock LLM 兜底 | 一个 `condition` 节点（docs/48）及任何 LLM 驱动的运行在 prod 下发出 ≥1 次真实 LiteLLM 调用；prod 不静默走 mock 兜底 | 商业 LLM 账号 + 成本/额度预算 | ⬜ | — | demo 当前靠兜底；prod 必须真决策 |
| 1.2 | W1 | 配真实/沙箱 Shopify 店铺；把 `channel:*` 工具（docs/67）指向真店；用测试订单跑通一次受控退款 | 携带 `order_id`/`amount` 的图打到**真实** Shopify 退款 API 并返回 2xx；docs/67 的「真适配器＋假 HTTP」由真 200 替换 | 真实/合作沙箱 Shopify 店铺；动（测试）款审批 | ⬜ | — | docs/67 以「真适配器＋假 HTTP」闭 N2，此处补全真外发 |
| 1.3 | W1 | 配真实 SMTP/IM webhook/SMS/OAuth 凭据；端到端验证发送路径 | 一条通知经 prod 配置真正投递到真实收件箱/IM 群（非 dev mock）；秘钥由 vault 注入，绝不明文 `.env` | 企业邮件/IM/SMS 供应商；OAuth 应用注册 | ⬜ | — | 见 docs/08 D 组外部通道 |
| 2.1 | W2 | 产品/用户拍板 docs/63 §0A N3（浏览器自动化适配器）是否进 MVP：进→立项批次（新适配器＋工具＋测试，新 D 号＋ADR）；出→显式标记 N3 出 MVP | 决策写入 docs/08 迭代计划 + docs/72 同步注记 | 产品范围会议 | ✅ **2026-09-27 已表态＝不进 MVP（本轮非目标）** | 决策与三条理由记在 docs/63 §0A N3 追记 ＋ docs/08 §八 E 组；**复开条件与三批顺序一并写明** | 纯产品决策，非工程缺陷。附带成果：接线前那条真缺陷（`page.goto()` 无出向校验）已先行修掉＝U925，故复开时是纯加法 |
| 3.1 | W3 | 把 `schedule_fires`/注册（docs/68 当前进程内）迁 Postgres，跨重启/实例共享 | 调度触发跨进程重启存活**且**第二实例不重复触发（PG 支撑 `ON CONFLICT`）；由对真 postgres 的集成测试覆盖 | CI postgres service（X.1） | ✅ **2026-09-27 更正：本项已完成**〔码〕 | `api/main.py:1059-1063` 在 `ATLAS_STORAGE_BACKEND=pg` 时装 `PgScheduleStore`（迁移 030 建 `schedules`/`schedule_fires`），出厂 `docker-compose.yml:50` 即 pg 档；跨重启存活与"第二实例不重复触发"由 `tests/test_scheduling_pg_integration.py` 5 例覆盖并在 CI `Backend PG integration` 每 PR 真跑 | 即 docs/14 D32；原记 ⬜ 属 docs/72 §1 同源误判，见 §6 |
| 3.2 | W3 | 立项 docs/62 §8 风险5 的 D36 批：单实例崩溃后 reconcile 挂起帧，保 at-most-once 续跑安全 | `kill -9` 挂起中途后重启⇒不重复执行；帧恢复或 fail-safe | 无（工程内可闭环） | ⬜ | — | docs/62 已登记 D36 |
| 3.3 | W3 | **后置**：仅当要「生产级」而非「单实例 MVP」时，再解 docs/62 单副本硬约束；需 D36＋所有挂起/事件/等待态共享 PG | 非 B 档阻塞；单实例 MVP 今日可交付 | 3.2 + 全态 PG 化 | ⬜（后置） | — | HA 显式后置阶段 |
| 4.1 | W4 | 真实凭据的 prod 全新部署；docs/66 引导口令 seed 管理员；首登强改密；跑通一条代表图（审批＋条件＋真 Shopify 退款＋IM 通知）至终态 | 真实基建上整链一次绿；把本次运行作为 〔跑〕 证据写入 docs/72 补记 | 1.1–1.3 全部 live | ⬜（**2026-09-27 部分推进，本项仍 ⬜**） | `scripts/dev/prod_surface_probe.py`（打包 P）：本仓第一次有 **prod 档进程被真驱动**——四段全过（prod 404／`ATLAS_ENABLE_DEMO_MOCK=1` 开回／大写 `PROD` 仍 404／非法档位起不来） | B 档终门。**探测≠演练**：没有真 LLM／真 Shopify／真 IM，也没有 Docker 与网络边界，整链一次绿仍未达成 |
| 5.1 | W5 | `ai_decision` 真实化（docs/77 R1）：运行期必须真的消费节点 `promptTemplate`／`model`，并实现置信度闸门（低于阈值 ⇒ 挂起转人工，docs/06 §6.2 那四项）；**或**反向把契约与编辑器降级为"退款专用决策器"、摘掉三个装饰字段。两条路二选一，先立项再落码 | 二选一收口：① 真路线＝prod 配置下"图里改 prompt 能改变模型输出"可被探针测到，且 `confidenceThreshold` 有双向测（低于阈值挂起／高于不挂起）；② 降级路线＝docs/01 §4.3／docs/06 §6.2／`ai_decision.schema.ts` 三处同步为"退款规则决策器"，UI 不再暴露 model 与置信度 | 无（工程内可闭环；真路线的端到端验要 1.1 的 key，**接线本身不要**） | ✅ **2026-09-27 完成（走真路线＝打包 R，docs/78；R1 关闭）** | 运营 `promptTemplate` 经 `interpolate` 渲染后**逐字**作发给模型的 user message（`llm/decision.py`＋`graph/loader.py`）；节点 `model` 覆盖环境默认（`source=llm:{实际 model}`）；`confidence < confidenceThreshold` 时**真挂起**转人工（复用 `ApprovalBroker`，阈值缺省 0.6／非法回退缺省／**边界严格小于**；缺 broker fail-safe 降级 `request_human_approval` 不阻塞）。机检 **U926–U938（13 例**：`tests/test_decision_client.py` +5、新建 `tests/test_ai_decision_realization.py` 8）；全量 `pytest`（本机实跑）**2008 passed／125 skipped／0 failed**，前端 738/2·0 error·build ✓ | 现状〔码〕（**改前**）：`dsl.py:1449` 强制填 prompt、`loader.py:428-438` 只回显、`decision.py:61-68` 用写死的 prompt、`model` 与 `confidenceThreshold` 零读者。**残余照实**（docs/78 §6）：真 LLM 端到端（"模型真按新 prompt 改变判定"）要 1.1 的 key，**不在本批**；低置信挂起为**进程内、不写 interruption 帧**、无跨重启恢复（新增 docs/14 **D44**，随 D20 缓做）；规则兜底（未配 `LITELLM_MODEL`）**不读** prompt/model |
| 5.2 | W5 | prod 形态**图内**演示面收口（docs/77 R2）：把打包 P 的口径从 HTTP 路由扩到运行期适配器注册表——prod 缺 `ATLAS_DATABASE_URL`／缺真店铺时，`database`／`shop` 要么不注册（图里选不到），要么每条结果显式带"演示数据源"标记；顺带 R4（`ATLAS_STORAGE_BACKEND` 走同一档位读取器、非法值拒启）与 R5（`_PassthroughEgress` 在 prod 拒用） | 探针 `scripts/dev/prod_core_loop_probe.py` 第 3 段在 prod 下不再拿到 `sqlite3.` 字样的结果（选不到、或结果里明写演示）；R4／R5 各有一条守护 | 无（工程内） | ✅ **2026-09-27 完成（R2＋R4＋R5，并含 R3）** | 探针 `scripts/dev/prod_core_loop_probe.py` 第 3 段在 prod＋pg 档实跑：`database/query` → `{"result": {"error": "适配器未注册：database", "status": "FAILED"}}`（**不再有 `sqlite3.` 字样**）＋ `GET /api/adapters` 里 `database`／`shop` 投影为 `{}`（图里选不到）；第 2 段旗舰链 run 仍 `completed`（缺适配器不把 run 判失败）；探针退出码 0、探针库 `atlas_review6` 已自删。全量回归 `.venv/bin/pytest` ＝ **1995 passed／125 skipped／0 failed**。守护测试：**R2** `test_r2_prod_without_demo_flag_registers_no_demo_adapters`／`test_r2_demo_surface_registers_shop_and_database`／`test_r2_prod_with_real_database_url_keeps_database_without_shop`；**R4** `test_read_storage_backend_defaults_and_normalizes`／`test_read_storage_backend_rejects_illegal_value`／`test_no_bare_storage_backend_env_read_remains`（全仓扫描旧裸读）；**R5** `test_r5_prod_rejects_the_passthrough_egress_seam`／`test_r5_dev_and_demo_flag_allow_the_seam`；**R3** `test_r3_openapi_surface_is_closed_in_prod`／`test_r3_openapi_surface_stays_open_in_dev`／`test_r3_demo_flag_reopens_the_openapi_surface`／`test_r3_asgi_surface_beyond_apiroute_is_enumerated`（U909 allowlist 同步扩到 `Mount` 与非 `APIRoute` 的 `Route`） | 现状〔跑〕：2026-09-27 探针在 prod＋pg 档实测 `database/query` 返回 `sqlite3.OperationalError`；根因行 `api/main.py:333`／`:338-340`／`:345`／`:467-481`。**收口口径＝不注册（fail-closed）**：把导入期适配器注册拆成两个纯函数 `_resolve_database_client(demo_surface)`／`_build_demo_registry(demo_surface, db_client)`，prod 且未开 demo 面时不注册 shop；`ATLAS_DATABASE_URL` 未配**且非演示面**时 `database` 适配器不注册（不再静默回退内置 SQLite）。`ATLAS_ENABLE_DEMO_MOCK=1` 一处仍同时开回四处（mock 路由／演示适配器／文档面／Shopify Admin 测试缝）。**R4** 把 `ATLAS_STORAGE_BACKEND` 收敛到 `security/bootstrap.read_storage_backend()`（大小写不敏感、非法值拒启），三处裸比较（`iam/registry.py:59`、`iam/deps.py`×2）全部替换。**R5** `build_channel_registry` 开头加 prod 拒用门（`ATLAS_ENABLE_DEMO_MOCK=1` 时放行）。**R3** 补 docs 文档面 prod 门（中间件，`/docs`·`/docs/oauth2-redirect`·`/redoc`·`/openapi.json` → 404），并把这四件套与 SPA `Mount` 纳入 U909 匿名面 allowlist |
| 5.3 | W5 | 假阳性三小项（docs/77 R6／R7／R8）：① `notified` 必须读 `MessageService.send` 的 `delivered`，不是"没抛异常"；② 调度卡片补最近一次 run 与状态；③ 工具名缺 `/` 编译期就拒（或运行期显式 FAILED，而不是 `SIMULATED` 且 run 仍 completed） | 每条一条测试：① 未配 SMTP ⇒ `notified:false` 且 `notifyError` 有文案；② 定时运行失败 ⇒ 卡片显式失败且能跳到那条 run；③ **prod 档下** `tool:"foo"` 跑出 `FAILED`（不再 `SIMULATED`；demo/dev 仍 SIMULATED） | 无（工程内，小批） | ✅ **2026-09-27：R6／R7／R8 全部闭合** | — | ① R6 ✅：`notify_pending` 返回 `bool` 并读 `MessageService.send` 的 `delivered`，demo 回退 `in_process` ⇒ `notified:false`；② R7 ✅：`/api/schedules` handler 层 enrich `lastRunId`／`lastRunStatus`／`lastRunStartedAt`（零迁移、零 store 改动）；两者各带测试。③ R8 ✅（**口径＝prod fail-closed**，非"裸名全局 FAILED"）：`loader.py:_execute_tool` 拆开原混写的 `if "/" not in tool_name or registry is None`——`registry is None` 仍 SIMULATED；**裸名 + 非空 registry** 再走 `security/bootstrap.demo_surface_enabled()`（与 5.2 的演示面闸门**共用同一档位**）：demo/dev 保持 SIMULATED 契约，**prod 且未开 `ATLAS_ENABLE_DEMO_MOCK` 时显式 FAILED**（`result.status=FAILED`＋`action_status=FAILED`＋`error:"工具名缺少 adapter/capability 形状：{name}"`）。守护 U939–U943（`tests/test_tool_name_prod_gate.py` 5 例）；**零既有测试改动**（改动前 5 个锁定 SIMULATED 的测试全在 dev 档，走恒开分支）。原根因行 `loader.py:1809`；思路更正与本表口径见 §7 末两条 |
| 4.2 | W4 | **空卷首启就绪竞态**（docs/77 §8 量出，2026-09-28 演练撞见）：compose 给 `db` 的探针是 `pg_isready`，在 initdb 之后的 fsync／recovery 窗口里判过 healthy ⇒ atlas 被 `depends_on: service_healthy` 放行、应用 import 期连库抛 `FATAL: the database system is in recovery mode` 退出，第二次才起来——**靠的是 `restart: unless-stopped`，应用与 entrypoint 自身零重试** | prod＋空卷首启**一次就到 ready**（不依赖重启策略兜底）：要么 `apply_migrations` 与 app 启动前的连通性各加有界重试，要么把 `db` 探针换成能区分 recovery 的 `SELECT pg_is_in_recovery()`；并补一条 〔跑〕 证据（新卷首启计时） | 无（工程内可闭环） | ⬜ | — | **只在全新卷首启触发**，卷已存在不出现⇒这正是六次复审都没撞见它的原因 |
| X.1 | X | 启用 CI postgres service（docs/63 结构性缺口：当前 44/1918 integration 永不跑） | CI 中 integration 套件对真 postgres 实际执行；解锁 3.1 的集成证明 | 无（工程内） | ✅ **2026-09-27 更正：已在跑**〔码〕 | `.github/workflows/ci.yml:58-90` 的 `Backend PG integration` job 设 `ATLAS_RUN_INTEGRATION=1` ＋ `DATABASE_URL`，先 `python -m scripts.ops.apply_migrations` 再 `pytest`（打包 J P1-1 落码）；原记 ⬜ 是把 docs/63 当时的结构性缺口当成了现状 | 备注：本项翻 ✅ 直接解锁 3.1 的集成证明 |
| X.2 | X | 凭据卫生：核 N5（`.env` example）已满足，且无明文 prod 凭据入库 | 仓库无明文 prod 秘钥；prod 走 vault/环境变量注入 | 无（工程内） | ✅ **2026-09-27 闭合**〔跑〕 | **全史扫描**：`gitleaks` 8.30.1 对本仓 HEAD 全历史跑 `gitleaks detect --source . --redact --log-opts="--all"` ⇒ **755 commits／14.07 MB，no leaks found**；CI 每次 push/PR 同扫（`ci.yml:17-28` gitleaks-action@v2，`fetch-depth: 0`）。**示例值合法**：`.env.example:13` 是 `ATLAS_ENV=dev`（N5 原缺陷＝曾写非法的 `development`），且 `tests/test_prod_bootstrap_admin.py::test_u867_env_example_values_are_valid` 机检"示例文件自身的值必须全部可被代码接受"。**真凭据不落盘**：`.gitignore:41-43` 忽略 `.env`/`.env.*` 并 `!.env.example`，`git ls-files` 里唯一的 env 形态文件就是示例；`docker-compose.yml` 全部走 `${VAR:-}` 注入（第 52/59 行等），无一处内联密钥 | 可复跑命令即本行证据。**边界照实**：① 探测器**不是万能**——正向对照里 gitleaks 认出了 `sk-`／PEM 形态（`leaks found: 1`），却**不认** AWS 官方文档示例对 `AKIAIOSFODNN7EXAMPLE`（那是它自己 allowlist 的教程值），所以"零命中"是关于规则集的事实，不是"绝无明文密钥"的数学证明；② 结论只覆盖到**今日 HEAD 的历史**，真部署后若有人提交真凭据需重扫；③ "prod 走 vault"这一半是**部署实践**，仓库侧只能证明"没把凭据写进代码与配置默认值"。dev 种子口令（`admin123` 等）是**有意的 dev-only 值**，prod 拒绝它们（`security/bootstrap.py`＋docs/66），不属于本项的"明文 prod 凭据" |
| X.3 | X | **demo 模拟面 prod 残留闸门收口**（2026-09-27 第五次复审 docs/74 新增）：`_demo_mock_enabled()` 目前只守 3 条 shopify-admin mock 路由（`main.py:4166/4180/4204`），prod 档仍有 5 条匿名/固定口令可达——`POST /api/demo/mock/orders/{order_id}/receipt`（`main.py:4148-4151`，**无鉴权且回显请求体**）、`GET /api/demo/mock/orders`（固定公开串 `X-Demo-Token: demo-token`）、`POST /api/demo/shop/login` ＋ `GET /api/demo/shop/orders`（demo/demo）、`GET /demo/shop` | `ATLAS_ENV=prod` 下逐条真发请求 ⇒ 全部 404；并有一条反向测证明"去掉门就红" | 无（工程内，量小） | ✅ **2026-09-27 完成**〔跑〕 | `563fbbc` 门收口（含 D-2 那条大写 fail-open）＋`ebcdb2d` U909–U913 九例＋`d9163f3` 真进程四段探测；prod 档下 8 条 demo 面逐条 404、开关一次开全、`ATLAS_ENV=PROD` 大写不再开门、非法档位拒启 | 原由 docs/74 补进清单；S5 至此才算真关。**顺带留下一种防复发机制**：匿名可达面由 U909 机器枚举成 allowlist，新加无鉴权路由会当场红 |

## 3. 退出判据（B 档达成）

全部满足时，docs/72 的 B 档判定翻 ✅ 达成：

- [ ] 1.1 真实 LLM 决策 live 且已演练
- [ ] 1.2 真实 Shopify 退款 live 且已演练
- [ ] 1.3 真实消息/IM/SMTP/SMS/OAuth 至少主通道 live 且已演练
- [x] 2.1 N3 范围决策已记录（✅ 2026-09-27：**不进 MVP**，理由三条＋复开条件与三批顺序见 docs/63 §0A N3 追记）
- [x] ~~3.1 调度 store PG 化完成~~ **✅ 2026-09-27 更正：立项时已完成**（`api/main.py:1059-1063`＋迁移 030＋compose pg 档；集成测试 5 例在 CI 每 PR 真跑）
- [x] X.3 demo 模拟面 prod 残留闸门收口（✅ 2026-09-27 打包 P：`563fbbc`＋U909–U913＋真进程探测；匿名可达面从此由 allowlist 机器守护）
- [ ] 4.1 `ATLAS_ENV=prod` 真机演练整链绿
- [x] **5.1 `ai_decision` 真实化**（docs/77 R1；✅ 2026-09-27 走**真路线**＝打包 R／docs/78：运营 prompt 逐字作 user 消息、节点 model 覆盖默认、`confidence < confidenceThreshold` 真挂起转人工；U926–U938 13 例，全量 2008/125/0；**残余**＝真 LLM 端到端要 1.1 的 key、低置信挂起无跨重启恢复＝docs/14 D44）
- [x] **5.2 prod 形态图内演示面收口**（docs/77 R2＋R4＋R5，并含 R3；✅ 2026-09-27：探针第 3 段在 prod＋pg 档 `database/query` 不再出 `sqlite3.`（`database` 适配器未注册）、`/api/adapters` 里 `database`／`shop` 为空；R2／R4／R5／R3 各带守护测试；全量 1995 passed／0 failed）
- [x] **5.3 R6 通知假阳性**（`notify_pending` 读 `delivered`，未配 SMTP ⇒ `notified:false`；2026-09-27 落码＋测试）
- [x] **5.3 R7 调度卡片缺最近 run 状态**（`/api/schedules` handler 层 enrich `lastRunId`／`lastRunStatus`／`lastRunStartedAt`；2026-09-27 落码＋测试）
- [x] **5.3 R8 无斜杠工具名**（✅ 2026-09-27 口径＝**prod fail-closed**：prod 且未开 demo 面时裸工具名 ⇒ 显式 `FAILED`（不再 `SIMULATED` 且 run 仍 completed），demo/dev 保持既有 `SIMULATED` 契约；守护 U939–U943（`tests/test_tool_name_prod_gate.py`），零既有测试改动；详见明细表 5.3 行与 §7 末条）

> **2026-09-27 第六次复审把 B 档判据改了一条依赖顺序**：docs/74 §5 的"关掉 demo 面闸门 → 配真 LLM 与一条真通道 → 跑一次真 prod 演练"里，第一条已闭合，但**剩下两条不足以推绿 B 档**——凭据到位后旗舰链仍会走规则壳决策、`database` 腿仍会查内置演示库。所以 W5 三条是 1.1–1.3 的**前置**而非并行项，本表按此重排。

（3.2 / 3.3 / X.1 / X.2 为韧性/前置增强：3.2 强烈建议随 B 档同批；3.3 与 X 类为后置或横切，不单独阻断 B 档判定。）

## 4. 同步矩阵

- 本文是评审的配套追踪器，**判定权威随复审链走**：docs/72（第四次）→ docs/74（第五次）→ **docs/77（第六次，2026-09-27 当前权威）**；本文只 tracker，不改判定。
- 关联：docs/72（B 档判定与缺口总述）、docs/63（评审框架 / S1–S8 / N 系列门 / CI postgres 缺口）、docs/08（B/C 组筛查 + D 组外部通道 + 2.1 决策落点）、docs/62（单副本护栏 / D36）、docs/66（N1 prod 引导口令）、docs/67（N2 渠道通道）、docs/68（N4 调度 / D32）、docs/48（LLM 条件节点）、docs/14（缓做项 D13/D20/D25/D28/D32/D35/D36）。
- 不替代复审正文；本文状态翻 ✅ 时反向在**当期权威复审文档**（现为 docs/77）补记 〔跑〕 证据。

## 5. 变更流水

| 日期 | 动作 | 说明 |
|---|---|---|
| 2026-09-27 | 立项 | 据 docs/72 结论拆 B 档缺口为 10 个明细追踪项（1.1–1.3 / 2.1 / 3.1–3.3 / 4.1 / X.1–X.2），全部 ⬜ 起追踪 |

## 6. 更正注记（2026-09-27，第五次复审 docs/74 带来）

本文首版有两条状态是**沿 docs/72 叙述写的、没回代码核**，本次据 〔码〕 更正（历史行不删，按上文"刷新规则"就地补证）：

1. **3.1「调度 store PG 化」不是待做，是已完成**——`api/main.py:1059-1063` 按 `ATLAS_STORAGE_BACKEND=pg` 装 `PgScheduleStore`（表＝迁移 030 的 `schedules`/`schedule_fires`），出厂 `docker-compose.yml:50` 就是 pg 档；跨重启存活与"第二实例不重复触发同一分钟槽"由 `tests/test_scheduling_pg_integration.py` 5 例钉住。
2. **X.1「CI postgres service」不是待做，是已在跑**——`ci.yml:58-90` 的 `Backend PG integration` job 已设 `ATLAS_RUN_INTEGRATION=1`＋`DATABASE_URL`，先 apply 迁移再 `pytest`。首版那句"44/1918 integration 永不跑"是 docs/63 当时（打包 J 之前）的结构性缺口，被当成现状抄了过来。

同时**新增一项必答**：X.3 demo 模拟面 prod 残留闸门（prod 档仍 5 条匿名/固定口令可达，含一条无鉴权且回显请求体的 POST）。这条的存在也说明本文 §3 的"退出判据"不能只看表格首版——**tracker 的每一项翻状态要回代码，不能沿评审叙述抄**。

| 日期 | 动作 | 说明 |
|---|---|---|
| 2026-09-27 | 更正＋新增 | 第五次复审（docs/74）驱动：3.1 与 X.1 翻 ✅（附 〔码〕 证据与 file:line）；新增 X.3 demo 面 prod 残留闸门；总览两行与 §3 退出判据同步 |
| 2026-09-27 | X.3 闭合 | 打包 P 落码收口（`563fbbc` 门＋D-2 大写 fail-open／`ebcdb2d` U909–U913 九例含 allowlist 反向门／`d9163f3` 真进程四段探测）。prod 档 8 条 demo 面逐条 404、开关一次开全、`ATLAS_ENV=PROD` 不再静默开门、非法档位拒启。**匿名可达面从此是机器枚举的 allowlist**，新加无鉴权路由当场红——这条比那 5 行门更耐久。门：后端 1960/119/0、前端 738/2/55、oxlint 0/7、build 未变（本批不碰前端） |
| 2026-09-27 | 4.1 部分推进 | 同批的 `scripts/dev/prod_surface_probe.py` 是本仓第一次有 **prod 档进程被真驱动**（四段全过），故在 4.1 行就地记"部分推进"并把探测证据落到该行备注；**本项仍 ⬜**——没有真凭据、没有 Docker 与网络边界、整链未跑过一次 |
| 2026-09-27 | X.2 闭合 | 凭据卫生做掉：`gitleaks` 8.30.1 全史扫描（**755 commits／14.07 MB ⇒ no leaks found**，命令写在 X.2 行可复跑）＋ CI 每 push/PR 同扫（`ci.yml:17-28`，`fetch-depth: 0`）＋ `.env.example` 示例值合法且由 `test_u867_env_example_values_are_valid` 机检 ＋ `.gitignore:41-43` 挡住真 `.env`、compose 全走 `${VAR:-}` 注入。**正向对照的边界照实写**：同一二进制认得出 `sk-`／PEM（`leaks found: 1`），认不出 AWS 官方教程示例键（被它自己 allowlist），所以"零命中"是规则集事实而非绝对证明；"prod 用 vault"那一半属部署实践，仓库侧只能证明没写进代码与配置默认值 |
| 2026-09-27 | 2.1 闭合 | **N3 表态＝浏览器自动化本轮不进 MVP**（用户「那你搞」采纳建议），三条理由与**复开条件＋三批顺序**记在 docs/63 §0A N3 追记。**接线前先补的前置已顺手修掉**：`web/adapter.py` 的 `navigate` 原来把工具参数 `url` 原样交给 `page.goto()`、**无任何出向校验**（httpx 的 SSRF denylist 管不住真浏览器）；现先过 `EgressGuard`（与 httpapi 同一份策略），守护 U925 含"放行必须真走到 goto"的判别对照与"被拒不碰浏览器"断言。⇒ W2 整组转 ✅，B 档缺口从 4 条降到 **3 条**（真凭据／D36／一次真 prod 演练）。残余边界：只判初始 URL，重定向与子资源需 `page.route()` 逐请求校验，留到真接线那批 |
| 2026-09-27 | **W5-5.2 闭合（R2＋R4＋R5，并含 R3）** | prod 形态图内演示面收口落码。**R2**：导入期适配器注册拆成两个纯函数（`_resolve_database_client`／`_build_demo_registry`），prod 且未开 demo 面时 `shop`／`database` 不注册、`ATLAS_DATABASE_URL` 未配不再静默回退内置 SQLite（口径＝**不注册 fail-closed**，非"结果标演示"）。**R4**：`ATLAS_STORAGE_BACKEND` 收敛到 `security/bootstrap.read_storage_backend()`（非法值拒启），三处裸比较替换。**R5**：`build_channel_registry` prod 拒用 `_PassthroughEgress` 测试缝（`ATLAS_ENABLE_DEMO_MOCK=1` 时放行）。**R3**：补 docs 文档面 prod 门（`/docs`·`/redoc`·`/openapi.json`·`/docs/oauth2-redirect` → 404），并把非 `APIRoute` 面（`Mount` 与 `Route`）纳入 U909 匿名面枚举——**这条正对 §7 第 2 点在 2026-09-27 缩小过的那个缺口，现已补上**。验收：探针 `scripts/dev/prod_core_loop_probe.py` 第 3 段在 prod＋pg 档 `database/query` → `{"result": {"error": "适配器未注册：database", "status": "FAILED"}}`（无 `sqlite3.`）、`/api/adapters` 里 `database`／`shop` 为 `{}`、旗舰链 run 仍 `completed`，退出码 0；全量 **1995 passed／125 skipped／0 failed**（新增 12 例）。`.env.example` 同步：`ATLAS_STORAGE_BACKEND` 文档化 + demo 开关注释扩为"一次管四处" |
| 2026-09-27 | **W5-5.3 ③ R8 闭合（口径＝prod fail-closed）** | `graph/loader.py:_execute_tool` 把原混写的 `if "/" not in tool_name or registry is None` 拆开：`registry is None` 仍 SIMULATED；**裸名 + 非空 registry** 再走 `security/bootstrap.demo_surface_enabled()`（与 5.2 演示面闸门共用同一档位）——demo/dev 保持 SIMULATED，**prod 且未开 `ATLAS_ENABLE_DEMO_MOCK` 时显式 FAILED**（`result.status=FAILED`＋`action_status=FAILED`＋`error:"工具名缺少 adapter/capability 形状：{name}"`）。守护 **U939–U943**（`tests/test_tool_name_prod_gate.py` 5 例：prod 裸名 ⇒ FAILED 且带文案／prod 开关开回 ⇒ SIMULATED／dev 裸名 ⇒ SIMULATED／prod 规范名 `shop/login` ⇒ 仍达注册表正常执行／prod 裸名的 `tool_metric` 记 FAILED 而非 simulated）。**零既有测试改动**（改动前 5 个锁定 SIMULATED 的测试全在 dev 档，走恒开分支）。取舍照实：只收紧 prod，非"裸名全局 FAILED"——demo 画布裸名仍是文档化 SIMULATED |

## 7. 第六次复审注记（2026-09-27，docs/77 带来）

- **判定的两档结论不变（A ✅／B ❌），变的是"差什么"**：docs/74 §5 把 B 档的最小动作集写成"关 demo 面闸门 → 配真 LLM 与一条真通道 → 跑一次真 prod 演练"。第一条已闭合，**后两条经本轮实测被证明不充分**：`ai_decision` 不消费运营写的 prompt（R1），prod＋pg 形态下 `database/query` 实测打到内置 SQLite 演示库（R2）。⇒ 本表新增 W5 三条前置项，**1.1–1.3 排在它们之后而不是并行**。
- **本文首版对 X.3 的表述过宽（本轮更正，不改写历史行）**：打包 P 的 allowlist 守护遍历的是 `app.routes` 里的 `APIRoute`（`tests/test_demo_surface_prod_gate.py:58-60`），而 FastAPI 默认的 `/docs`·`/redoc`·`/openapi.json` 与 `app.mount("/")` 不是 `APIRoute`——它们在 prod 匿名可读，却**不在"多一条少一条都红"的集合内**。X.3 的 ✅ 仍然成立（它当初的判据就是那 5 条 demo 路由），要更正的是"整张匿名面已被机器守护"这句引申。
- **本轮另有两条被我自己证伪的假设**，记在 docs/77 §5（prod 里消息记录"不可见"是错的：`/api/demo/messages` 实测 200；"run completed 掩盖工具失败"也不成立：`monitoring/metrics.is_healthy()` 把含失败节点的运行算不健康）。列在这里是为了让下一个人不必重复猜。

## 8. prod 演练注记（2026-09-28，本表 4.1 第一次真跑 compose prod 形态）

- **4.1 推进但未达成**：`docker compose -p review6`（project 命名空间隔离，自己的卷 `review6_atlas-pgdata`，收尾 `down -v` 只删自己那份）在 **prod＋空卷** 下第一次跑到 `ready=200`，量到迁移 30 条／27 张表／prod 拒明文种子口令而引导口令可登／**`GET /api/adapters` 只回 `http·message·memory`（W5-5.2 的 R2 收口拿到运行级确认）**。但「审批挂起→重启→续跑」与「`pg_dump`→删卷→`restore.sh`」两段**没做完**：这台机器同时段 load **385／427／393**，两个容器的健康探针开始返回 **exit −1（探针自己超时）**、DB 也一并 unhealthy——**环境把容器压住了，不是产品行为**（`status=running`／`OOMKilled=false`），故不记 ✅，需在安静机器或 CI 重跑。
- **由此新增 4.2**：撞出一条只在**全新卷首启**出现的就绪竞态（`pg_isready` 在 PG 的 recovery 窗口里判过 healthy ⇒ 应用 import 期连库即抛退出，第二次才起，靠 `restart: unless-stopped` 兜）。这条本表此前没有，六次复审也没撞见——因为从没在空卷上跑过 prod 形态。全过程与证据在 docs/77 §8。
- **W5-5.2 闭合并把上一条收窄过的缺口补掉（2026-09-27）**：本节第 2 点（X.3 守护只遍历 `APIRoute`）所指向的缺口，已由 W5-5.2 的 R3 一并闭合——U909 匿名面枚举现覆盖整张 ASGI 面（`Mount` 与非 `APIRoute` 的 `Route` 一并在内），`/docs`·`/redoc`·`/openapi.json`·`/docs/oauth2-redirect` 也已在 prod 档 404（中间件门，`ATLAS_ENABLE_DEMO_MOCK=1` 可开回）。R2（prod 不注册 `shop`／`database` 演示适配器）＋R4（`ATLAS_STORAGE_BACKEND` 走校验器）＋R5（prod 拒用 `_PassthroughEgress`）同批落码。证据见 §5 变更流水末行与明细表 5.2 行；全量 1995 passed／0 failed。
- **W5-5.3 落地结果与一处"看起来像假阳性、实为契约冲突"的更正（2026-09-27）**：R6／R7 已落码并各带测试（`notify_pending` 返回 `bool` 并读 `MessageService.send` 的 `delivered`；`/api/schedules` handler 层 enrich `lastRun*`），全量回归 1983 passed／0 failed。**R8 则不能按"顺手修"做**：`SIMULATED` 在本仓是**有定义的契约**——"无 `adapter/capability` 或 registry 缺失的本地构造"（`loader.py:1796`／`monitoring/records.py:40`／`monitoring/metrics.py:87`），裸工具名（不含 `/`）返回 `SIMULATED` 正是该定义的第一款；把它改成 `FAILED` 会打掉 5 个既有测试（`test_monitoring_tool_metrics::test_executor_emits_tool_metric_for_simulated_call`、`test_graph_loader` 的并行三例与另一例），且这些测试锁的是被文档记录的行为。⇒ **R8 的代码改动已回退**，它和 R1 同类：属"改契约"而非"修 bug"，按 AGENTS.md 须先记录决策再落码。**原 docs/77 R8 的表述要按此收窄**：真问题不是"裸名 → SIMULATED"（那是契约），而是"prod 里没有一条把 SIMULATED 与真实执行区分开的上层门"——即 5.2 的演示面标记口径可一并覆盖，不必单独改工具调用契约。
- **R8 收口（2026-09-27，口径＝prod fail-closed，用户拍板"推荐路线"；本文 5.3 ③ 由此转 ✅）**：按上一条自己给出的方向落码——**不加第二套工具调用语义，只给 prod 补那道"上层门"**：`graph/loader.py:_execute_tool` 把原混写的 `if "/" not in tool_name or registry is None` 拆开，裸工具名改判为"**裸名 + 非空 registry**"这一支，再走 `security/bootstrap.demo_surface_enabled()` 同一判定源（与 5.2 的演示面闸门**共用一处档位**）：**demo/dev 档保持 SIMULATED 契约**（既有 5 个锁行为的测试零改动）、**prod 且未开 `ATLAS_ENABLE_DEMO_MOCK` 时返回显式 `FAILED`**（`result.status=FAILED`＋`action_status=FAILED`＋`error:"工具名缺少 adapter/capability 形状：{name}"`）。⇒ 彻底性上的取舍照实：这是**只收紧 prod 的档位门**，不是"裸名全局 FAILED"——demo 画布上裸名仍走文档化的 SIMULATED（那是 demo 语义，监控 `simulated` 计数与影子色可见），而**prod 里"看起来成功、其实没执行"这一假阳性被关掉**。守护 U939–U943（`tests/test_tool_name_prod_gate.py`，5 例：prod 裸名 ⇒ FAILED＋有 `adapter/capability` 文案／prod 开关开回 ⇒ SIMULATED／dev 裸名 ⇒ SIMULATED（契约零变化）／prod 规范名 `shop/login` ⇒ 仍达注册表正常执行／prod 裸名的 `tool_metric` 记 FAILED 而非 simulated）。**零既有测试改动**（改动前的 5 个锁定 SIMULATED 的测试全在 dev 档，走 `demo_surface_enabled()` 恒开分支，行为不变）。
