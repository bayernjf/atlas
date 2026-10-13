# Atlas — AI 运营体编排平台

> 非技术人员用自然语言或拖拽定义一条流程，Atlas 把它编译成可执行图，由 AI 运营体去执行、请人来把关、并留下可审计的全过程。

---

## 一、产品

### 是什么

Atlas 是一个**可视化 Agent 工作流（运营体）编排平台**。使用者在画布上把 LLM 决策、工具调用、人工审批、分支/循环/等待/子图/状态机编成一张 Graph，平台编译后在 LangGraph 运行时上执行，并配套发布、灰度、影子对照、监控告警、记忆与知识的全链。

三个基元（定义见 [docs/01](docs/01-PRD-产品需求规格.md)）：

- **Harness**——能力接入层。外部平台（HTTP API、数据库、消息渠道、浏览器、店铺）统一抽象成"可操作对象"，回答"在哪里操作、用什么工具"。
- **Graph**——可执行的因果有向图。业务流程的固化结构，回答"怎么做"。
- **Loop**——感知→规划→执行→反馈的循环。回答"怎么持续运转"。

### 能干什么（当前可达的产品面）

| 能力 | 说明 |
| --- | --- |
| **建流程** | React Flow 画布拖拽；**13 类节点**（trigger／ai_decision／tool_call／condition／loop／parallel／wait／subgraph／human_approval／intent_recognition／info_extraction／content_generation／state_machine）；Schema 驱动的属性面板（嵌套对象、数组判别、条件显隐、自定义控件）；6 作用域变量与 `{{路径}}` 引用；表达式函数库；编译期诊断逐条定位 |
| **四种造图方式** | 自然语言生成草稿（`/api/nl/generate`）；操作录制→回放＋批量回放＋参数化向导；5 个内置模板＋用户自建模板库（版本/搜索/统计/URL 导入）；OpenAPI 导入自动生成工具 |
| **跑流程** | 手动／入站 Webhook／定时 cron 三种触发；SSE 实时进度；灰度分桶＋指标门控自动回滚；影子运行对照；挂起态跨重启恢复；运行中急停 |
| **AI 决策** | 运营在节点里写的 prompt 与 model 真正生效；**置信度低于阈值即挂起转人工**；LLM 语义分支；意图识别／信息抽取／内容生成三类结构化节点 |
| **人机协同** | 审批三渠道（站内／邮件／IM 卡片）；邮件内一键决策（HMAC 签名链接，免登录）；超时默认拒绝；审批历史入库 |
| **诚实性（本项目的取舍）** | prod 档位下没接模型就不许报绿——`ai_decision`／`condition(llm)`／裸工具名一律显式 FAILED，不静默走规则兜底；上下文字段级脱敏，env/secret 明文不出向模型供应商 |
| **记忆与知识** | 长期记忆（fact/preference，向量召回＋自动提取＋衰减）；知识库（faq/sop/manual/rule/case 导入向量化＋按类召回） |
| **运维可视** | 运行列表／指标／span 链路；四内置告警规则＋静默＋升级＋值班轮换；运行报表与版本对比；离线批评估；反思候选＋人工采纳 |
| **接入面** | 渠道 binding 与连通测试；OAuth 授权与令牌轮换；模型配置管理面（平台内置模型＋租户 BYOK）；消息模板系统；MCP server 面；A2A 执行 Agent 面；多租户 RBAC＋审计 |

### 一条能讲清的旗舰场景

订单进来 → LLM 判定（"商品破损/299" ⇒ 直接退款；"不想要了/5000" ⇒ 转人工）→ 人工在邮件或 IM 卡片批准 → 调退款工具 → 发通知 → 监控页与审计留下完整可追溯记录。演示档用内置假店铺，整链已跑通。

### 边界（照实说，别按想象用）

- **只能单副本运行**：一个进程、一台机器。多进程会让同一条挂起审批帧在各进程各自续跑一遍，下游真实副作用被执行两次。容器入口对 `workers>1` 直接拒启，compose 钉 `replicas: 1`（机制见 [docs/62](docs/62-挂起帧一次性认领-单副本硬约束护栏-v1批契约设计.md)）。
- **真外发未验**：真 Shopify 退款、真 SMTP/IM 投递只有代码与演练腿，缺真实凭据（差距追踪见 [docs/73](docs/73-自主上线MVP缺口清单追踪.md)）。**prod 档缺三把必需密钥会拒绝启动，这是设计行为不是 bug**。
- **真浏览器适配器只在演示面注册**：prod 不注册 `web-playwright`——出向闸门挡不住 3xx 跳转后的请求（见 [docs/89](docs/89-项目级代码审计与功能全景.md) §14、docs/14 D52）。
- **认领后崩溃的运行不自动重放**：有人工收敛出口（`POST /api/interruptions/{token}/resolve`），重放属 docs/14 D56。
- **许可**：不开放（proprietary），仓库无 LICENSE 文件。

---

## 二、启动

### 形态 A：Docker 一键（演示／试用，只需 Docker）

```bash
docker compose up --build
# http://localhost:8000  → 登录页：admin-a / admin123（演示企业 A 的管理员）
# 登录后点「打开流程编辑器」；模拟商家控制台在 http://localhost:8000/demo/shop（demo / demo）
```

- 登录档位缺省 `dev` ⇒ 播四个种子账号（`admin-a`/`operator-a`/`viewer-a` 口令 `*123`，另一租户 `admin-b`），演示面全开。
- compose 已钉 `ATLAS_STORAGE_BACKEND: pg` ⇒ 数据落在 named volume `atlas-pgdata`，**重启容器不清零**。回到出厂状态：`docker compose down -v`。
- `/api/demo/reset` 是 admin only（匿名调用 401），陪同人员带凭证调用即可；试用者换一单没跑过的订单更省事。
- 种子客户试用按 [TRIAL.md](TRIAL.md) 一页纸走（三场景＋反馈表）。

要真 LLM 决策（不是规则兜底），在 `.env` 里给三个键，**缺任一都发不出真实调用**：

```bash
LITELLM_MODEL=openai/<model>
OPENAI_API_KEY=...        # 变量名是 OPENAI_*，不是 LITELLM_API_KEY
OPENAI_BASE_URL=...       # 后者只被 litellm 的 proxy 代码读，直连路径不生效
```

### 形态 B：本地双进程（开发）

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"     # 后端环境（Python ≥3.11）
cd frontend && pnpm install && cd ..                               # 前端依赖

cp .env.example .env                       # 首次：按需填 DATABASE_URL 与 LLM 三键
.venv/bin/uvicorn atlas.api.main:app --reload --env-file .env --port 8000
cd frontend && pnpm dev                    # 编辑器 http://localhost:5174（/api 代理到 8000）
```

- 用 PG 档跑集成测试时起一个本地库并应用迁移（`db/migrations/` 48 条，按序号执行或用 `scripts/ops/apply_migrations.py`）：

  ```bash
  docker run -d --name atlas-pg -e POSTGRES_USER=atlas -e POSTGRES_PASSWORD=atlas \
    -e POSTGRES_DB=atlas -p 5432:5432 pgvector/pgvector:pg16
  DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \
    ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration
  ```

- 浏览器用例需要先 `.venv/bin/playwright install chromium`（CI 的 PG 集成 job 已装）。
- `--env-file .env` 是必须的——项目自身不加载 `.env`（litellm 会在 import 时自行读取，别指望后端读）。

### 形态 C：prod 档位（另需三样，缺一即拒启）

```bash
ATLAS_ENV=prod                                   # 大小写不敏感；非法值同样起不来
ATLAS_MASTER_KEY=<base64url，解码后恰 32 字节>    # secrets.token_urlsafe(32)，不要用 48
ATLAS_APPROVAL_HMAC_SECRET=<≥32 字节>
ATLAS_ADMIN_BOOTSTRAP_PASSWORD=<过口令策略>       # prod 只播各租户 ADMIN，首登强制改密
```

- 三把密钥也支持 `<NAME>_FILE` 文件形态注入（`_FILE` 优先；指向不存在的文件即判缺失，**绝不回退环境变量**）。
- prod 档下演示模拟面（`/demo/shop`、`/api/demo/*`）与 `/docs`·`/openapi.json` 一律 404；要对外演示显式设 `ATLAS_ENABLE_DEMO_MOCK=1`（一处开全部，没有按路由细粒度）。
- 网络边界（域名／TLS）由 compose profile `edge` 的 Caddy 提供：先在 `deploy/Caddyfile` 把站点地址改成你的真实域名（DNS 指向本机，Caddy 自动申请/续期证书；本地无域名联调就写 `:80` 仅 HTTP），再 `docker compose --profile edge up -d`。启用后建议去掉 atlas 的 `8000:8000` 映射，只让 Caddy 对外暴露 80/443。可观测面（Prometheus 9090／Grafana 3000）在 profile `observability`。

### 三条运行期须知

- **cron 一律 UTC**：`0 9 * * *` 是北京时间 17:00；**停摆错过的槽位不补跑**（宁漏不重跑）；**只跑已发布钉版**，改草稿不生效。
- **挂起帧只读可见**：`GET /api/interruptions` 或控制台「等待与任务 → 挂起帧」列出 `claimed_suspended`（该看的那种）。这个视图只读，不重放、不清帧；内存档下该表恒空且会自报"看不见"，别把空列表读成"没有卡住的运行"。
- **改动后跑门**：后端 `.venv/bin/pytest`；前端 `cd frontend && pnpm test && pnpm build`。分层测试与 PG 直连命令见 [docs/13](docs/13-测试用例清单.md)；**最近一次门读数与推送状态以 [handoff.md](handoff.md) 为准**，本文件不复制会随下次提交过期的数字。

---

## 三、架构概览

```
┌─ 前端 frontend/（React 19 + TS + @xyflow/react 12 + Zustand 5 + AntD 6，17 个页面）
│    画布 / 属性面板 / 变量面板 / 调试台 / 审批 / 监控 / 记忆·知识 / 模板 / 渠道 / 模型 / 定时 / 审计 …
│    lib/ 为纯逻辑（scope·forms·schemas·recordings·shadow·reflection…），中英双档 locales
└─ /api 代理 → FastAPI 单进程（api/main.py，约 170 条路由装饰器；prod 档 SPA 同端口直出）
      │
      ├─ 编排层   graph/（DSL 契约＋编译器＋插值＋输出单源投影）→ engine/ → nodes/ → LangGraph 运行
      ├─ 能力层   harness/（registry＋runtime 装配）＋ 各适配器包：
      │            httpapi / database / message / memory / shop / channels / connections / openapi / web
      ├─ 运行面   storage/（Repository 协议＋memory 与 pg 两档实现＋中断帧序列化与恢复扫描）
      ├─ 控制面   iam/ RBAC＋租户分区 · security/（档位读取、密钥注入、信封加密、脱敏、出向准入）
      │            versioning/ 不可变发布 · routing/ 灰度与门控 · recording/ 录制回放 · debug/ 断点
      │            monitoring/ 指标告警 · scheduling/ cron 调度 · reflection/ 反思候选 · evaluation/ 批评估
      │            collaboration/ 审批 broker · coordination/ 任务信封 · skills/ · a2a/ · mcp/ · tracing/
      └─ 数据面   PostgreSQL + pgvector（48 条迁移，SQL 在 db/migrations/）；演示档可回退进程内与内置 SQLite
```

要点：

- **一份图两种执行**。`POST /api/graphs/{id}/run` 与 `/run/stream`（SSE）走同一 loader；影子运行是旁路执行同一张图做产出比对，不参与主链。
- **档位是唯一判定源**。`security/bootstrap.demo_surface_enabled()` 一处决定演示面、演示适配器、裸工具名、prod fail-closed 四件事——不各写各的 `os.getenv`。
- **持久化两档同契约**。`ATLAS_STORAGE_BACKEND` 决定 memory 或 pg；挂起帧、审批历史、调度槽位、报告、影子、评估、模型配置只在 pg 档入库，内存档必须自报"看不见"而不是返回空表。
- **调度槽位一次性认领**（`schedule_fires` 主键 `(schedule_id, slot_utc)`＋`ON CONFLICT DO NOTHING`），同分钟两进程只触发一次，这是"不补跑"的另一面。
- **Loop 现状要读准**：`src/atlas/engine/` 的 OODA 循环目前是独立骨架（仅被测试与基准引用），生产形态＝**事件／定时驱动的图执行**；把它接进运行期是 docs/14 **D69**（排期见 docs/127 里程碑 C）。
- **对外协议面**：MCP server 是 stdio＋**只读工具面**（写能力仅 docs-only 预备契约，docs/14 D49）；A2A 提供 Agent Card 与任务端点。

技术选型收口于 2026-09-13（T1–T5），后续选型见 [docs/10](docs/10-技术选型决策记录.md)。模块职责与目录权威表在 [docs/09](docs/09-工程骨架与目录结构.md)；数据契约索引 [docs/03](docs/03-数据模型与Schema-契约索引.md)；接口清单 [docs/12](docs/12-API与模块接口清单.md)。

---

接手本仓库先读 [handoff.md](handoff.md)（状态＋待办＋文档索引），写代码前读 [AGENTS.md](AGENTS.md)（治理规则与技术约束）。
