# demo 模拟面 prod 闸门收口批（打包 P）v1 批契约设计

> **立项 2026-09-27**：起因＝docs/74（第五次上线复审）补回的 B 档必答项 **X.3**，用户「好的，开搞」。形状权威＝本文；与 01–08 冲突以规格文档为准，落码偏差在收口注记回填。
>
> **一句话**：docs/72 把 S5「demo 模拟面 prod fail-closed」记成已全 remediation，实况是**只守住了 3 条路由**。本批把剩下 5 条补齐，并且**把"匿名可达面"从 grep 出来的一堆变成机器枚举的一张表**——否则下一批加一条无鉴权路由时，我们还会再漏一次。

## 0. 已核实前提（〔码〕＋一次可复现枚举，不是推断）

| 事实 | 证据 |
|---|---|
| 路由总数与匿名面是**量出来的**，不是感觉出来的 | 枚举 `app.routes` 中 `APIRoute` 共 **138** 条；其中**端点签名不含 `Principal` 依赖且函数体不含 `_demo_mock_enabled`／`x-demo-token`／`read_env_profile`** 的有 **12** 条〔跑，脚本见 §4 U909〕 |
| 12 条里 8 条是**设计如此**的公开面 | `/api/health`、`/api/ready`、`/metrics`（K-C：prod 未配 token→404、配了须 Bearer）、`POST /api/channels/hooks/shopify/{binding_id}`（HMAC 验签在前）、`GET /connections/callback`（OAuth state 校验）、`POST /api/auth/login`、`GET /api/approvals/email-view` 与 `POST /api/approvals/email-decision`（签名 capability token，J-1b） |
| 12 条里 **5 条是漏网的 demo 模拟面** | `POST /api/demo/shop/login`（`main.py:4120`，**demo/demo 固定口令**）、`GET /api/demo/shop/orders`（`:4127`）、`POST /api/demo/mock/orders/{order_id}/receipt`（`:4148`，**无鉴权、无档位门、把请求体原样回显**）、`GET /demo/shop`（`:4415` HTML 控制台），以及 `GET /api/demo/mock/orders`（`:4140`，靠**写进仓库的常量** `X-Demo-Token: demo-token` 守，等于对读过代码的人敞开） |
| 门函数只被 3 条路由调用 | `_demo_mock_enabled()` 定义在 `main.py:4154`，调用点只有 `:4166`/`:4180`/`:4204`（三条 shopify-admin mock 路由）——docs/63 §0A 的更正与此完全一致 |
| 门函数自己有**静默 fail-open** 缺陷 | 现实现是 `os.getenv("ATLAS_ENV", "dev") != "prod"`：**`ATLAS_ENV=PROD`（大写）判定为"非 prod"⇒ 门开着**，而 `read_env_profile()` 是大小写不敏感的（`security/bootstrap.py:30`）。也就是"你以为在 prod，其实匿名面全开"只需要一个大小写 |
| 试用流程不依赖 prod 档 | TRIAL.md:8 与 docs/18:37 的模拟商家后台属 **dev/demo 形态**；compose 出厂 `ATLAS_ENV: ${ATLAS_ENV:-dev}`（`docker-compose.yml:56`）⇒ 加门不影响种子试用 |

⇒ **实况**：这不是"产品核心不可用"，是**上线前该关的匿名面没关严**；而且它已经两次被记成"做完了"。所以本批的第二半是守护，不是那 5 行代码。

## 1. 决策（六条，改任何一条先改本文）

- **D-1 prod 档下 demo 模拟面一律 404**，沿用既有 `_demo_mock_enabled()`（fail-closed，`ATLAS_ENABLE_DEMO_MOCK=1` 是唯一显式开闸方式），**不新造第二套开关**。非 prod 行为逐键不变——种子试用与 Demo 演示形态（TRIAL.md/docs/18/compose 缺省 dev）零影响。
- **D-2 门函数改用 `read_env_profile()` 而不是自己读 `os.getenv`**：① 大小写归一，`ATLAS_ENV=PROD` 不再静默变成"非 prod ⇒ 开门"；② 非法值（`production`/`prod1`）**抛错而不是默认放行**——档位值写错这件事本身就该拦住（同 `.env.example` 犯过的 N5 同源）。本条是"顺带修的那个真缺陷"，不夹带进别的。
- **D-3 覆盖面就是 §0 那 5 条**（含 `/api/demo/mock/orders` 那条常量 token 的：它在 prod 也 404；非 prod 仍保留 X-Demo-Token 检查，两层各有意义）。
- **D-4 匿名可达面变成显式 allowlist 守护**：新测试从 `app.routes` 枚举"无平台鉴权依赖"的路由集合，断言它**恰好等于**一张带理由的公开表白名单。多一条 ⇒ 红；少一条 ⇒ 也红。这是本批真正的产出：把"我们漏了几条"这个问题变成每次跑门都自动回答。
- **D-5 验收必须有一次真进程 prod 档探测**〔跑〕：真起 `ATLAS_ENV=prod` 的 uvicorn（本地一次性随机密钥＋引导口令，**不涉及任何真实外部凭据**），逐条发请求验 404，并验 `ATLAS_ENABLE_DEMO_MOCK=1` 能把面开回来。**TestClient 改环境变量不算**——本批评的就是"档位判定在真进程里到底成不成立"。附带说明：这条**部分**推进 docs/73 的 4.1（第一次有 prod 档进程被真驱动），但**不等于 prod 演练**（无真凭据、无 Docker、无真 LLM/Shopify/IM），4.1 仍留 ⬜。
- **D-6 不改的东西**：不动 compose 缺省档位；不动 `/api/demo/reset`·`/api/demo/messages`·`/api/demo/deliveries`（本就有平台 RBAC）；不动 8 条设计如此公开面；不新增迁移／端点／错误码／依赖；**不解除任何缓做，也不解除单副本约束**。

## 2. 形状

- 只动 `src/atlas/api/main.py`：`_demo_mock_enabled()` 内部改走 `read_env_profile()`；5 个端点各加同一段门（返回 404，与既有 3 条一致，**不给攻击者"存在但关着"的信号**）。
- 新测试文件 `tests/test_demo_surface_prod_gate.py`（U909 起）。
- 新探测脚本 `scripts/dev/prod_surface_probe.py`：自起自杀 uvicorn（只杀自己那个 PID），断言逐条 404／开关能开回／prod 引导口令可登而 `admin123` 被拒（顺带把 N1 在同一进程里再验一遍）。

## 3. 契约同步矩阵（收口时逐项回填）

docs/08（§八 立项条＋A 组新行→收口划销）、docs/12（demo 路由那几条的鉴权档补"prod 404"）、docs/13（U909 起登记）、docs/14（**不新增缓做**；如真起新风险再登）、docs/15 §五（prod 前置补一条"demo 模拟面默认关"）、docs/63 §0A（S5 那条注记补"已收口"，历史正文不改）、docs/72（§7 补记追加一句 S5 已闭合）、docs/73（**X.3 翻 ✅**；4.1 补"已含 prod 档进程探测，但完整演练仍未做"）、docs/74（§4 补本轮全量门实测数字；§3-A 第 1 项标已完成）、`.env.example`（`ATLAS_ENABLE_DEMO_MOCK` 若缺则补注）、README（prod 段一句话）、CHANGELOG、handoff。

## 4. 测试与验收（U909 起；只许增测）

- **U909 枚举守护**：从 `app.routes` 算出"无平台鉴权依赖"的集合，断言恰等于 allowlist（12 条：8 设计公开＋4 demo 面在 prod 有门／非 prod 可访问，需在断言里注明各自归属与理由）。**反向门**：在同文件里临时注册一条无鉴权假路由 ⇒ 该用例必须红。
- **U910 prod 档逐条 404**：`ATLAS_ENV=prod` 下 5 条 demo 路由（含 `/demo/shop` HTML）全部 404；同时 `ATLAS_ENABLE_DEMO_MOCK=1` 全部回到非 prod 行为（200／401 原语义）。
- **U911 非 prod 零变化**：dev／test 档下同样这 5 条行为与改前逐键一致（含 demo/demo 登录成功、X-Demo-Token 仍生效）。
- **U912 档位归一（D-2 的真缺陷）**：`ATLAS_ENV=PROD` 大写 ⇒ 门**关**（改前会开）；`ATLAS_ENV=production` ⇒ 请求处理时抛非法档位，而不是静默当 dev。
- **U913 shopify-admin 三条不回归**：既有 3 条门路由在 prod 仍 404（防止把门函数改坏后只有新面受益）。
- **〔跑〕探测脚本**：`scripts/dev/prod_surface_probe.py` 真进程输出贴进收口注记。

## 5. 原子序

① （本批）docs-only 立项：本文＋docs/08 立项条与 A 组行＋docs/00 地图行＋CHANGELOG＋handoff（Active 新号）。
② `fix(api)`：门函数走 `read_env_profile()` ＋ 5 条路由套门。
③ `test`：U909–U913（含 allowlist 反向门）。
④ `test(dev)`：`scripts/dev/prod_surface_probe.py` ＋ 真进程跑一次并记录输出。
⑤ `docs(ops)` 收口：§3 矩阵逐项回填＋docs/74 §4 补数＋docs/73 X.3 ✅＋CHANGELOG/handoff。
⑥ push 仅在用户明确指示下；合 main 须明确授权。

## 6. 残余风险（写清楚，别当成"安全面收干净了"）

1. **本批只处理"匿名可达"这一类**。138 条里其余 126 条的角色／租户分区不在本批复核范围（既有 RBAC 与 U 系列覆盖），allowlist 守护也只管"有没有鉴权依赖"，**不管鉴权对不对**。
2. **非 prod 的 demo 面照旧全开**——这是演示形态的设计。真正的风险是"把 dev 档挂到公网"，那条靠 docs/15 §五 的档位说明，不靠代码。
3. **`ATLAS_ENABLE_DEMO_MOCK=1` 是一个把门全开的单点开关**：v1 不做按路由细粒度开闸（会引入 5 个新配置项与新的解释负担）；需要细粒度时另批。
4. **prod 探测仍不是"prod 演练"**：没有真 LLM／真 Shopify／真 IM，也没有 Docker 与网络边界。docs/73 的 4.1 只能部分推进，别把它翻成 ✅。
5. **改 `_demo_mock_enabled()` 的档位读取方式会牵动既有 3 条路由**：已由 U913 覆盖，但它属于"共用函数的行为改动"，回归面比新增门略大——这一条写在这里是防止下一位把它当纯增量改动。
