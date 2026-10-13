# docs/129 · 打包 AU＝LLM 用量与成本计量（D70，里程碑 B）v1 批契约设计

> **形状权威＝本文**。批次＝**打包 AU**（排期见 [docs/127](127-能力迭代里程碑计划-D68D77排期-打包AT起-v1.md) 里程碑 B；需求来源与对账见 [docs/126](126-能力对齐清单与迭代待办池-面试材料功能来源-v1.md) §4/§5）。
> **范围**：D70——LLM token 用量计量（六个调用点 → 一条记录链 → 两档存储 → 只读投影）。**docs-only 立项原子**，落码另批（§6 原子序）。
> **基线**：dev HEAD `aafdd4f`（D78 修复后）。门读数沿用 AT 收口四门（后端 **2765／189／0**、前端 **1010／2**、oxlint **0/0**、build ✓；其后仅 `bd2f522` U1406 测试修正与 D78 两例＋一行注入，`tests/test_api_graphs.py` 实跑 6 passed）。
> **为什么是 B 不是别的**：D70 是 D69（Loop 预算闸门）／D74（智能体成本面板）／D75（候选成本对比）／D77（A/B 指标口径）的**硬前置**——四个都要"每次 LLM 调用花了多少 token"这个数据源，而它今天不存在（§1 零命中实核）。顺序按可推进性排（docs/127 §0），B 是唯一"无待拍板阻塞且解锁后面三批"的干净候选。

## 1. 基线实测（为什么这不算"已经做了"）

〔码〕逐条对当前 HEAD 实核，不是引旧文档：

1. **`prompt_tokens`／`completion_tokens`／`total_tokens`／`usage` 在 `src/atlas/`＋`tests/` 全零命中**（`usage` 唯一命中是 `POST /api/templates/{id}/usage` 的路由名，与 LLM 无关）。⇒ 平台今天**没有任何一处**读模型回包的用量字段。
2. **六个 `litellm.completion` 调用点**，全部把回包的 `usage` 丢掉（只取 `choices[0].message.content`）：
   - `llm/decision.py:184`（`ai_decision` 节点）；
   - `llm/condition_classifier.py:116`（`condition` 节点 mode=llm）；
   - `llm/structured.py:83`（`_call_llm`，被**四个**消费方共用：`classify_intent`／`extract_fields`／`generate_content` 三节点〔打包 AB〕＋`extract_memories` 记忆自动提取〔打包 AK〕）；
   - `llm/nl_generate.py:214`（NL 草稿生成端点，**不在图运行内**，是端点级调用）；
   - `reflection/adapter.py:100`（反思 L2 摘要器，由调度 action=reflect 触发）。
3. **运行侧没有 run→LLM 的归属通道**：`run_graph` 签名无 `run_id`（run_id 由 API 层 `uuid4().hex` 生成后只在 `run_store.begin/finish` 与 frame sink 使用）；`decision_client.decide_refund`／`classifier.classify` 等 Protocol **不带** node_id/run_id（`classify` 带 `node_id` 是打包 AE 为阈值诊断加的，仅条件分类器有）。⇒ 用量要挂到"哪条 run 的哪个节点"，今天没有现成管道。
4. **存储先例已齐**：两档 store（内存 ring／PG 表）＋`TenantServices` 按租户装配＋`reset` 同清的模式在 `report_store`（docs/56）／`shadow_store`（docs/61 H4）／`reflection_store`（docs/88/ZU）／`model_config`（docs/93，reset **不清**）四处可抄；迁移现最大 **048**，用例现最大 **U1410**（均已实测）。

## 2. 决策（七条，落码不得偏离；改任何一条先改本文）

- **AU-D1 计量对象＝token 用量，不做货币计价**。记录 `prompt_tokens`／`completion_tokens`／`total_tokens`＋`model`＋caller＋归属维度；**单价表不建**——账号合同价属外部资源（docs/73 同族：不给外部数据预铺代码），伪造单价比没有更坏。计价界面留触发条件（拿到真实账单口径时）再立批，记录形状不变（加一列单价即可派生）。
- **AU-D2 落 PG 新表＋内存档有界 ring（B-P1 按推荐拍板）**：迁移 **049** 建 `llm_usage` 表（tenant_id, ts, caller, node_id, graph_id, run_id, model, prompt_tokens, completion_tokens, total_tokens, latency_ms, status）；PG 档 `PgUsageStore(engine, tenant_id)` 跨重启存活，内存档 `UsageStore` 进程内 **ring 2000**（照 `monitoring/records.py` ring 200 与 `reflection` ring 100 先例；超出覆盖最旧，**不静默扩容**）。理由：成本账跨重启不可丢，且 **C（BN）的 Loop 预算闸门要读历史窗口**——进程内档会让 C 被迫重做一遍。**反悔成本声明**：若用户要内存档，只需删原子③并把装配换内存档，§3 形状与其余原子不变。
- **AU-D3 六个调用点走同一个同源 helper**：新 `llm/metering.py` 提供 `complete_with_usage(kwargs, *, caller, model)`——封装 `litellm.completion`、usage 提取、计时、落记录，返回 `(content, usage)`。六个点全部改走它，**recording/api/reflection 侧禁写第二份**（打包 AE 脱敏"一件事一个实现"的同款纪律；docs/66 D66 口径延伸到用量面）。
- **AU-D4 归属用 contextvar，不改六个 Protocol 签名**：`llm/metering.py` 持 `_usage_context`（tenant_id/run_id/graph_id/node_id/**store 引用**），contextmanager `usage_context(...)` 供调用方设置。设置点三处：① API 层 run 入口（sync／stream／background worker／resume）在调 `run_graph` 前设 tenant/run/graph＋本租户 store（run_id 现成，不用改 `run_graph` 签名）；② loader 节点执行器在 LLM 类节点（ai_decision／condition llm／三结构化节点）执行处包 `node_id`；③ `nl_generate` 端点与反思调度触发处设 tenant（无 run/node，字段留空）。**纪律同 tracing**：contextvars 不跨线程继承（06 §6.12），SSE/后台 worker 在**本线程内**显式 set——设置点全在 worker 线程自己身上，不依赖继承。**无上下文 ⇒ 不记录**（loader 直跑与既有单测不计，契约明写，避免测试环境里出现来源不明的记录）。
- **AU-D5 失败调用也记一条**：`litellm.completion` 抛异常（超时/供应商错/解析前任何错）记 `status="error"` 的零 token 条——"花了时间没拿到 token"本身就是成本信号（J-3c 的超时档尤其）。**不记 prompt/response 正文**（隐私与体积；脱敏面不新增）。
- **AU-D6 只读投影两个端点＋一页**：`GET /api/usage/summary?from&to&group_by=caller|model|day`（read 档，租户分区，返回分组 token 合计＋调用次数）与 `GET /api/usage/recent?limit=50`（read 档，明细倒序）；前端独立页 `Usage.tsx`（照 `Models.tsx` 路由/导航/i18n 三件套先例）：聚合表（按 caller／按 model 两张）＋明细表。**D74 的"智能体成本面板"不做**（那是按 agentId 归因的另一维度，本批只到 caller/model/day）。
- **AU-D7 不解除、不触碰**：D70 在 docs/14 的行**翻 🟡 不翻 ✅**——计价、Loop 预算闸门（BN）、agent 归因（D74）、retention 接入（usage 表本批**不挂** `ATLAS_RETENTION_DAYS`，登记余部）都不在范围内。不动六个 Protocol 的既有签名语义（只改内部实现走 helper），不动 run_store/monitoring 形状，零新错误码（计量失败不许炸运行：记录失败降级为 warning，见 §3.4）。

## 3. 契约形状

### 3.1 `llm_usage` 记录（迁移 049）

```
llm_usage(
  tenant_id      TEXT NOT NULL,
  ts             TEXT NOT NULL,          -- ISO8601 UTC，与 runs.started_at 同族
  caller         TEXT NOT NULL,          -- ai_decision|condition_llm|intent_recognition|
                                         -- info_extraction|content_generation|memory_auto_extract|
                                         -- nl_generate|reflection_summarize
  node_id        TEXT NOT NULL DEFAULT '',
  graph_id       TEXT NOT NULL DEFAULT '',
  run_id         TEXT NOT NULL DEFAULT '',
  model          TEXT NOT NULL,          -- 实际发出的模型名（含节点 model 覆盖后的）
  prompt_tokens     INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens      INTEGER NOT NULL DEFAULT 0,
  latency_ms     REAL NOT NULL DEFAULT 0,
  status         TEXT NOT NULL           -- ok|error
)
```
索引：`(tenant_id, ts)` 供时间窗查询（照 audit seq 先例做游标不必要——本批 limit 封顶 200，分页缓做）。

### 3.2 `llm/metering.py`（新文件，纯新增）

- `UsageRecord` dataclass（字段同上；`public_view()` 投影 camelCase 供 REST）。
- `UsageStore` Protocol：`record(rec)`／`list_recent(tenant_id, limit)`／`summarize(tenant_id, since, until, group_by) -> list[dict]`。
- `InMemoryUsageStore`：进程内 dict per tenant＋`collections.deque(maxlen=2000)`，一把锁（照 monitoring/records.py）。
- `PgUsageStore(engine, tenant_id)`：INSERT＋`WHERE tenant_id` 查询（**跨租户反向门**：摘掉 tenant 过滤必须红，同 U916 先例）；聚合用 SQL `GROUP BY`（caller/model/date(ts) 三档）。
- `complete_with_usage(kwargs, *, caller, model) -> tuple[str, dict]`：唯一调 `litellm.completion` 的封装；usage 缺失（供应商不回）时 token 记 0 并 `status="ok"`＋warning（**不许因为没有 usage 就把整条记录丢掉**——调用发生过是事实）；异常路径记 `status="error"` 后**原样抛出**（调用方的 fail-safe 语义不变：decision 转人工／condition 走 default／structured 本地兜底，本批一律不吞不改）。
- `usage_context(tenant_id, run_id="", graph_id="", node_id="", store=None)` contextmanager；无 store ⇒ 空操作（不记录）。

### 3.3 接线点（六处调用点＋三处上下文）

| 调用点 | caller | node_id 来源 |
|---|---|---|
| `decision.py:184` | `ai_decision` | loader 节点执行处（AU-D4②） |
| `condition_classifier.py:116` | `condition_llm` | 同上 |
| `structured.py:83`（四消费方） | `intent_recognition`／`info_extraction`／`content_generation`／`memory_auto_extract` | 同上（`extract_memories` 在 memory 工具执行处） |
| `nl_generate.py:214` | `nl_generate` | 无（端点级，run/graph/node 留空） |
| `reflection/adapter.py:100` | `reflection_summarize` | 无（调度触发，tenant 由触发方设） |

**装配**：`TenantServices` 加 `usage_store` 字段（内存/PG 两档，照 report_store 装配位），`reset` 同清（运行数据，与 model_config 的"不清"区分——口径：配置跨重启保留、运行计量随 reset 清）。

### 3.4 错误码／迁移／依赖／ADR

- **零新错误码**：计量是旁路，任何计量自身失败（store 写炸等）只 `logger.warning`，**绝不影响运行终态**（反方向守卫进验收：store 抛异常时 run 仍 completed）。
- 迁移 **049**（`db/migrations/049_llm_usage.sql`，遵守迁移头约定：>029 首行注释）。
- 零新依赖；无 ADR（不涉选型变更，存储两档模式是既有 ADR T24 谱系）。

## 4. 验收（用例号预留 **U1411–U1426**，docs/13 立"打包 AU"小节）

**正向**

- **U1411** 六个调用点各记一条：mock litellm 回包带 usage ⇒ 记录 token 数与回包逐字相等、caller/model/latency 正确。
- **U1412** 节点 `model` 覆盖后记录里的 `model` 是**覆盖后的**实际发出名（打包 Y 的显式传参不被计量层抹掉）。
- **U1413** BYOK/内置显式 key 路径同样计量（api_key 只进 litellm，不进记录——记录无 key 字段，机检）。
- **U1414** run 归属：走 `POST /api/graphs/{id}/run` 真跑一条含 ai_decision 的图 ⇒ 记录带正确 tenant/run_id/graph_id/node_id（run_id 与 run_store 里那条一致）。
- **U1415** 失败调用记 `status="error"` 零 token 条（mock litellm 抛超时类异常）。
- **U1416** 供应商不回 usage ⇒ token 记 0、`status="ok"`、warning 一条、记录不丢。
- **U1417** 内存档 ring 2000 有界：灌 2500 条 ⇒ 存 2000、最旧的 500 不在。
- **U1418** PG 档跨重启存活（一次性 pgvector 容器：写后新连接可读）。
- **U1419** `GET /api/usage/summary` 三档 group_by 数字与明细求和**对账相等**；时间窗过滤生效。
- **U1420** `GET /api/usage/recent` 倒序＋limit 封顶；匿名 401（进 U909 匿名面 allowlist 反面校验）。
- **U1421** 前端 Usage.tsx：两张聚合表＋明细表渲染（vitest＋i18n 两档 parity）。
- **U1422** reset 后 usage 清空（内存档演示口径）而 model_config 不清。

**反向门（各自植缺陷实跑转红）**

- **U1423** 跨租户：摘 `PgUsageStore` 的 `WHERE tenant_id` ⇒ 租户 B 读到租户 A 用量，必须红（同 U916 形状）。
- **U1424** 摘 API 入口的 `usage_context` 设置 ⇒ U1414 立刻红（证明归属不是永远成立的）。
- **U1425** store.record 抛异常 ⇒ run 仍 completed（计量旁路性守卫）。
- **U1426** 四道门零回归（后端默认＋PG 集成＋前端 vitest/build＋oxlint），并**一次真机端到端**：真模型跑一笔退款 ⇒ `GET /api/usage/summary` 出非零 token（不许拿单测代替，docs/16 教训同源；无真凭据时跑 `scripts/dev/` 探针记 BLOCKED 并点名缺什么）。

## 5. 不解除、不触碰

D70 行翻 🟡（计价/预算闸门/agent 归因/retention 四项余部照旧）；不触碰 D69（Loop）／D74（团队面）／D75／D77；不动六个 Protocol 的对外语义；不动 `run_store`／`monitoring`／`tracing` 形状；usage 表不挂 retention（登记余部）；零新错误码/零新依赖/无 ADR。

## 6. 落码原子序（另批执行；顺序本身是验收项）

① docs-only 立项（本提交：docs/129＋docs/14 D70 行翻 🟡＋docs/08 §八立项块＋docs/00 地图＋handoff＋CHANGELOG）→ ② `llm/metering.py`（helper＋contextvar＋`InMemoryUsageStore`＋单测）→ ③ 迁移 049＋`PgUsageStore`＋`TenantServices.usage_store` 装配与 reset → ④ 六个调用点改走 `complete_with_usage`（decision → condition → structured 四处 → nl_generate → reflection）→ ⑤ 三处上下文设置（API run 入口／loader LLM 节点执行处／nl_generate 与反思触发处）→ ⑥ REST 两端点 → ⑦ 前端 Usage.tsx＋i18n＋路由导航 → ⑧ 测试（U1411–U1425，反向门各自植缺陷）→ ⑨ 收口（四道门＋真机端到端 U1426＋本文 §7 收口注记＋同步矩阵回填）。

## 7. 变更记录

| 日期 | 动作 | 说明 |
|---|---|---|
| 2026-10-14 | 立本文（docs-only 立项） | 基线实核四条（§1）：usage 全零命中、六个 litellm.completion 丢 usage、run→LLM 无归属通道、存储先例已齐。七条决策 AU-D1…D7，其中 **AU-D2 按 docs/127 B-P1 推荐拍板（落 PG 表＋内存 ring 2000）**；验收预留 U1411–U1426；零新错误码/零新依赖/无 ADR。同步：docs/14 D70 行翻 🟡、docs/08 §八立项块、docs/00 地图、handoff、CHANGELOG |
