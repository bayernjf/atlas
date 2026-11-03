# Atlas 深度只读代码评审 — 2026-11-02

> 评审性质：仓库 owner 授权的只读评审。评审期间未修改任何源码/配置/测试，未执行 git 写操作。
> 评审基线：工作区 = `dev` 分支 HEAD `41cb42b`（2026-11-02）+ 两份未提交改动
> （`M src/atlas/api/main.py`、`?? src/atlas/services/webhook_runtime.py`，属 main.py 拆 service 层的在途工作）。

---

## 一、仓库定位与规模概览

Atlas 是 AI 运营体（Agent 工作流）编排平台：React Flow 画布编图 → 编译为可执行图 → LangGraph 运行时执行，配套发布/灰度/影子/审批/监控/记忆全链。形态为 **FastAPI 单进程后端 + React 19 SPA 前端**，prod 档同端口直出前端构建产物，PG+pgvector 持久化（内存档为演示/测试档）。

实测规模（`git ls-files` + `wc -l`）：

| 维度 | 数值 |
| --- | --- |
| 后端源码 | 220 个 `.py`，51,474 行（`src/atlas/`，40 个子包） |
| 后端测试 | 227 个 `.py`，63,594 行（**测试代码量 > 源码量**） |
| 前端源码 | 276 个文件，52,608 行（`frontend/src/`） |
| DB 迁移 | `db/migrations/` **58** 个 SQL（001–058，序号连续） |
| 文档 | `docs/` 261 个 Markdown（编号治理体系） |
| CI | `.github/workflows/ci.yml`：gitleaks / backend pytest / backend PG 集成 / frontend lint+test+build |

治理基线：`pyproject.toml`（uv.lock 钉版）、`frontend/package.json`（pnpm-lock）、`AGENTS.md` + `handoff.md` + docs 编号文档三层治理。

---

## 二、结论摘要

**总计：P0 = 0，P1 = 1，P2 = 5，P3 = 3。**

整体判断：这是一份**纪律性罕见地强**的代码库——fail-closed 哲学贯穿密钥、档位、出向、会话、审批、迁移各面；安全关键路径（scrypt 口令、HMAC 邮件深链、SSRF egress、只读 SQL guard、AES-GCM 信封、一次性认领）实现质量高且有对应测试；验证门真实有效（实测全绿，详见 §四）。未发现可立即利用的 P0。

最关键的 3 条：

1. **P1 入站消息端点 fail-open**：`POST /api/channels/inbound/{binding_id}` 在 binding 未配 `inbound_secret` 时零验签受理，且 binding id 为可枚举顺序号（`ch-{seq}`），命中 inbound_mappings 即后台触发该租户**已发布图**（payload 攻击者可控）。与本仓自身的 fail-closed 纪律不一致。
2. **P2 验证门结构性缺口**：220 个 Python 文件**零 lint / 零类型检查**（docs/10 的知情取舍，但客观上 CI 只有 pytest 一道静态门）；`api/main.py` 7601 行 / 208 个路由装饰器的 god module 仍在（在拆，未拆完）。
3. **P2 前端产物无代码分割**：单 JS chunk 1,963 kB（gzip 604 kB），17 个页面全量进首屏；构建器已实测告警。

---

## 三、逐条发现（含证据）

### P1 — 严重缺陷

#### P1-1 入站消息端点未配密钥即免验签，且 binding id 可枚举，可远程触发已发布图

- **位置**：
  - `src/atlas/api/main.py:2410-2449`（`POST /api/channels/inbound/{binding_id}`，无认证依赖）
  - `src/atlas/api/main.py:2429-2433`：`secret = (binding.config or {}).get("inbound_secret")`；**`if secret:` 才验签，未配则完全跳过**
  - `src/atlas/channels/memory.py:20`（`binding.id = f"ch-{self._seq}"`）与 `src/atlas/channels/pg.py:62-63`（`SELECT nextval('storage_id_seq')` → `ch-{seq}`）：**顺序号，全网可枚举**
  - `src/atlas/channels/inbound.py:127-148`：命中 `inbound_mappings`（按 `conversation_id` 精确匹配）→ resolve 钉版 → **后台线程触发图运行**，`event.payload` 全量来自请求体
- **影响**：未认证攻击者枚举 `ch-1..ch-N` 定位 binding；若该 binding 未配 `inbound_secret` 且存在已知 `conversation_id` 的入站映射（真实部署里常是 IM 群/会话 id，属半公开标识），即可以任意 payload 触发该租户**已发布图**——payload 经 trigger 节点插值可流入 HTTP/DB/退款等工具参数，造成真实副作用。对照同仓纪律：Shopify webhook 路径强制验签（密钥缺失 503，main.py:2208-2210）、邮件深链 HMAC+收件人绑定、`/metrics` prod 无 token 即 404——唯独此端点 fail-open，且**不受 `demo_surface_enabled()` 门控**，prod 档同样开放（main.py 该路由无任何档位检查，已核实）。
- **修复建议**：① prod 档（或一律）下 binding 无 `inbound_secret` 时该端点拒收（404/503，与 webhook「密钥不可用 503」同口径）；② binding id 改不可枚举（uuid）；③ 把「入站验签」列入 docs/73 上线缺口清单的 prod 准入项。

### P2 — 质量 / 可维护性 / 性能

#### P2-1 `api/main.py` 7601 行、208 个路由装饰器的 god module

- **位置**：`src/atlas/api/main.py`（实测 7601 行；`@app.*` 装饰器 208 个，`Depends(require` 186 处）。
- **影响**：单文件承载全部 REST 面（认证/图/运行/审批/渠道/模板/监控/记忆/评估/demo mock），review 与并发修改成本高；import 期副作用多（模块级装配 session_store/user_store/tenant_registry，iam/deps.py:39-46）。
- **缓解事实**：handoff.md 顶部记录正在拆（`services/schedule_pure.py`、`services/error_safety.py`、在途 `services/webhook_runtime.py`），方向正确。
- **修复建议**：按域拆 APIRouter（channels/webhook、approvals、monitoring、demo 各成一模块），main.py 只留装配与 lifespan；拆分时保持「路由 → require() 能力」映射表逐条核对，防止拆漏鉴权。

#### P2-2 后端零 lint / 零类型检查门

- **位置**：`pyproject.toml` 仅配置 pytest；`.pre-commit-config.yaml:7` 明写「项目未引 ruff/black」（docs/10 知情取舍）；CI `ci.yml` 后端 job 只有 pytest。
- **影响**：5.1 万行 Python 无任何静态分析兜底；与前端（oxlint 116 规则 + tsc -b 双门）形成明显不对称。`pythonpath=["src"]` 的测试布局也意味着类型错配只有跑到才炸。
- **修复建议**：至少引入 ruff（仅启用 F/E9 级）+ 对 `security/`、`iam/`、`storage/` 三个安全敏感包开 mypy strict-optional；成本远低于一次线上事故。

#### P2-3 前端产物无代码分割，单 chunk 1.96 MB

- **位置**：实测 `pnpm build` 输出 `dist/assets/index-*.js` **1,962.95 kB（gzip 604.38 kB）**，构建器告警 "Some chunks are larger than 500 kB"；产物仅 1 个 JS + 1 个 CSS。
- **影响**：登录页也要下载 React+AntD 6+xyflow 全量；首屏 LCP 与缓存利用率受损。
- **修复建议**：17 个页面按路由 `React.lazy` + vendor manualChunks（antd/xyflow/react 分裂），目标首屏 < 400 kB。

#### P2-4 无全局请求体与图规模上限（认证面 DoS 面）

- **位置**：`POST /api/graphs`（main.py:3297）与 `PUT /api/graphs/{id}`（main.py:3326）仅 pydantic 形状校验；`graph/dsl.py` 无节点数/边数上限（仅有 wait 时长、意图数 20、字段数 30 等局部上限，dsl.py:54-67、2033-2044）；uvicorn 启动未设 body 限制（Dockerfile:41）。对比：webhook 入站有 1MB 上限（main.py:1175 `MAX_WEBHOOK_BODY_BYTES`）、模板 URL 导入有 256KB（main.py:2994）。
- **影响**：任一 operate/admin 账号可提交超大 graph JSON（内存放大、编译期 `validate_graph_report` 全图扫描变慢）；`loader.py:3860` 超步上限 `2*len(nodes)` 随节点数线性放大。单租户 blast radius，故定 P2 而非 P1。
- **修复建议**：图节点/边上限（如 500/2000，与 `frontend/bench/` 的 500 节点基准对齐）+ uvicorn/反代层 body 上限。

#### P2-5 uvicorn 未开 `--proxy-headers`，edge 反代形态审计 IP 失真

- **位置**：`Dockerfile:41`（CMD 无该旗标）、`scripts/ops/docker-entrypoint.sh`（未补）；消费点 `src/atlas/api/main.py:6607`（邮件决策审计 `request.client.host`）与 T6 审计中间件（main.py:538+）。
- **影响**：compose profile `edge`（Caddy 终结 TLS）部署时，审计事件与邮件决策日志的 IP 全部是 Caddy 内网地址，追溯价值受损；`security/egress.py:13` 明确「不读 X-Forwarded-For 做网络判定」是对的，但**审计归因**需要可信代理头。
- **修复建议**：entrypoint 在检测到 `ATLAS_BEHIND_PROXY=1`（新增显式开关）时追加 `--proxy-headers`，并在 deploy/README 写明「只在可信反代后开启」。

### P3 — 体验 / 规范

#### P3-1 README 迁移数漂移：写 48，实为 58

- **位置**：`README.md:81`（"db/migrations/ 48 条"）、`README.md:130`（"48 条迁移"）；实测 `db/migrations/` 58 个 SQL（001–058）。
- **修复建议**：改为「按序号执行，数量以目录为准」或直接更正为 58 并加机检（仓库已有 U1157/U1158 这类「代码与部署面漂移机检」先例）。

#### P3-2 可观测栈镜像未钉版

- **位置**：`docker-compose.yml:154`（`prom/prometheus:latest`）、`:170`（`grafana/grafana:latest`）。对照：`pgvector/pgvector:pg16`、`caddy:2` 均已按主版本钉。
- **影响**：observability profile 拉取不可复现；与仓内「uv.lock 唯一事实源 / PR #152 双解漂移根治」的钉版纪律不一致。
- **修复建议**：钉到具体 minor 版本（如 `grafana/grafana:11.x.y`）。

#### P3-3 登录响应体仍回发 session token

- **位置**：`src/atlas/api/main.py:2794-2798`（`LoginResponse` 含 `token` 字段，注释注明「兼容既有客户端与 A2A 调用方」）；cookie 已是 httpOnly+SameSite=Strict+prod Secure（main.py:2786-2793，已核实）。
- **影响**：XSS 一旦得手可在登录响应中读到 token 并离线复用（Bearer 通道仍收，deps.py:116-127）；属于知情兼容取舍，非缺陷，但削弱了 ZQ Q4「前端不再持有 token」的收敛收益。
- **修复建议**：给 SPA 登录路径加 `?cookie_only=1`（或按 Accept 头）不落 token 响应体；A2A 调用方走独立 API token 面。

---

## 四、验证门实际执行结果（2026-11-02 实测）

| 门 | 命令 | 退出码 | 关键输出 |
| --- | --- | --- | --- |
| 后端单测 | `.venv/bin/pytest -q -p no:cacheprovider` | **0** | **2975 passed, 213 skipped, 0 failed**，578.24s；跳过均 integration opt-in（`ATLAS_RUN_INTEGRATION`）/chromium 缺失条件跳，非静默 skip；与 handoff.md 最新门读数逐字一致 |
| 后端集成（PG+浏览器） | 未本地实跑（无本地 PG/Chromium 依赖） | — | CI `backend-integration` job 实跑（pgvector/pgvector:pg16 service + `playwright install --with-deps chromium` + 迁移 + 全量），设计真实 |
| 前端 lint | `pnpm lint`（oxlint） | **0** | `Found 0 warnings and 0 errors`，230 文件 / **116 条规则**（非空配置） |
| 前端单测 | `pnpm vitest run` | **0** | **75 文件 passed（1 skipped），1027 用例 passed（2 skipped）**，31.7s |
| 前端构建 | `pnpm build`（tsc -b + vite build） | **0** | 类型检查通过；产物单 chunk 1,963 kB，构建器 >500 kB 告警（见 P2-3） |
| 锁文件一致性 | `uv lock --check` | **0** | `Resolved 110 packages`，锁与 pyproject 一致 |
| 密钥扫描 | gitleaks（CI job，未本地重跑） | — | `.gitleaks.toml` allowlist 仅测试夹具/示例，配置合理；`.env` 未被 git 跟踪（`git check-ignore` 实测），仅 `.env.example` 入库 |
| 后端 lint / typecheck | **不存在** | — | 见 P2-2 |

**门禁真实性结论**：不存在「形同虚设」的门——skip 全部条件化且 CI 有对应实跑 job；conftest.py 还专门防真 LLM 凭证漏进套件（`_no_real_llm_provider` 夹具）。唯一缺口是后端静态分析门缺失（P2-2）。

---

## 五、分维度评审记录

### 架构与模块边界
分层清晰：`graph/`（DSL+编译器+运行）→ `harness/`+适配器包 → `storage/`（memory/pg 两档同契约）→ `security/`、`iam/` 控制面。多处「唯一判定源」实测成立：`ATLAS_ENV`/`ATLAS_STORAGE_BACKEND` 全仓仅 `security/bootstrap.py:70/85` 两处直读（grep 核实）；litellm 调用仅 `llm/metering.py` 一个出口；SSRF 校验仅 `security/egress.py`。`engine/` OODA 骨架 203 行、零生产 import（grep 核实），与 README「独立骨架、D69 缓做」的声明一致——文档诚实。

### 正确性
- 挂起帧一次性认领用 `UPDATE ... WHERE resumed_at IS NULL` 的 rowcount 做互斥（`storage/recovery.py:34-52`），不引锁，正确且简洁；DB 不可达时按「未认领」fail-safe。
- 审批 broker 首决生效、pending 有界（`_MAX_PENDING=5000` 硬上限 + 懒 prune，`collaboration/approvals.py:73-111`）。
- 调度槽位 `(schedule_id, slot_utc)` 主键 + `ON CONFLICT DO NOTHING`（README 所述，migration 030/037 同构）。
- SSE 桥有界缓冲 + 终帧槽保证恰好一次（`api/sse.py`）。

### 安全（已核实为**强**的面）
- 口令：scrypt（N=2^14,r=8,p=1）+ 恒定时间比较 + 弱口令黑名单 + prod 首登强制改密（`iam/passwords.py`、`iam/deps.py:66-80`）。
- 会话：PG 档落 `iam_sessions` 表；cookie httpOnly+SameSite=Strict+prod Secure；改密/停用吊销其他会话。
- 登录节流：进程内滑动窗口 600s/5 次（`iam/throttle.py`）。
- 邮件深链：HMAC 签名 + 收件人绑定 + 统一 404（`api/main.py:6465-6491`）。
- SSRF：`security/egress.py` 私网/元数据恒拦、userinfo 拒、严格域名、白名单 fail-closed、不自动跟 302（`httpapi/service.py:230`）；DNS rebinding/TOCTOU 已在模块注释中诚实标注为已知残留。
- SQL：全部命名参数绑定（grep f-string SQL 抽查 20 处均为列名常量拼接）；只读通道有独立静态审查（`database/guard.py`）。
- 密钥：AES-256-GCM 信封、kid 绑定 AAD、prod 拒解 plain 信封、`_FILE` 注入不回退 env（`security/secrets.py`、`bootstrap.py:41-65`）。
- 注入面：无 pickle/subprocess/eval/__import__（grep 全仓为 0）。
- 前端：无 dangerouslySetInnerHTML/innerHTML（grep 为 0）；不落盘 token。

### 数据模型与迁移一致性
58 条迁移序号连续；runner 按文件名序、单事务、失败回滚不登记（`storage/migrations.py`）；容器入口先迁移后启动（`docker-entrypoint.sh:57`）。MIGRATION_CONVENTION.md 存在且迁移均为新增不覆写。唯一不一致是 README 计数漂移（P3-1）。

### 错误处理与失败模式
fail-closed 成立且一致：prod 缺三密钥拒启（bootstrap.py:174-189）、prod 误配 memory 档拒启（bootstrap.py:196-221）、未捕获异常统一固定 500 文案（`services/error_handlers.py:143-147`）、审计/计量/通知全部旁路 fail-safe（失败只 warning 不阻断主流程）。49 处 `except Exception` 抽查均有明确注释说明为何吞（抽样 10 处，全部合理）。

### 依赖与构建健康
关键依赖新鲜且钉锁：fastapi 0.143.1、pydantic 2.14.1、SQLAlchemy 2.1.4、litellm 1.105.1、langgraph 1.2.15、cryptography 50.0.2；`model-hub-client` 以 git **tag** v0.2.0 钉版（pyproject.toml:39）。无弃用告警。唯一镜像钉版缺口见 P3-2。

### 性能
- P2-3 前端单 chunk。
- `must_change_password` 每请求一次主键 SELECT（deps.py:66-80，注释已权衡，PG 主键查询成本可接受）。
- 后端重负载批处理端点有独立信号量（`_HEAVY_BATCH_MAX_CONCURRENT=8`，main.py:493-515）。
- 无 N+1 热点证据；监控/usage 均 ring 有界。

### 文档与实现一致性
抽查 12 项 README 声明，11 项与代码逐字一致（单副本硬约束、prod 三钥拒启、演示面 404、engine/ 骨架状态、cron UTC 不补跑、`_FILE` 注入语义、engine OODA 零生产引用、MCP 只读面等）；1 项漂移（迁移数，P3-1）。handoff.md 最新门读数与本次实测逐字一致（2975/213/0）。

---

## 六、修复优先级建议

| 序 | 条目 | 级别 | 理由 |
| --- | --- | --- | --- |
| 1 | P1-1 入站端点 fail-closed 化 + binding id 去枚举 | P1 | 唯一的未认证写路径，与全仓安全纪律不一致；prod 接真渠道前必须收口 |
| 2 | P2-4 图规模/请求体上限 | P2 | 成本极低（dsl 加常量 + 校验），消除认证面内存放大 |
| 3 | P2-2 引入 ruff（最小规则集） | P2 | 一次配置长久受益；与现有测试门互补 |
| 4 | P2-5 proxy-headers 显式开关 | P2 | 审计归因完整性，一行旗标 + 文档 |
| 5 | P2-3 前端代码分割 | P2 | 收益明确但工程量大（17 页懒加载 + vendor 分裂），可排期 |
| 6 | P2-1 main.py 续拆 | P2 | 已在进行，保持「能力映射逐条核对」即可 |
| 7 | P3-1/P3-2/P3-3 | P3 | 顺手修 |

---

*评审执行：只读静态评审 + 本地实跑验证门；PG 集成与浏览器用例依赖外部服务，以 CI job 设计为准未本地复跑（本机无 PG/Chromium 依赖环境）。*
