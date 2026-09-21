# 29 MVP 上线就绪项目级评审

> **来源与状态（2026-09-21 接力登记）**
>
> 本文是另一个会话对「Atlas 是否达到产品核心完全可用的 MVP」的项目级评审原文留档。该会话实际起了 uvicorn、用真实 HTTP 跑黄金用例、用浏览器点完编辑器闭环，并核了认证/持久化/部署源码。
> 接力时（2026-09-21，dev HEAD `ea29da9`）已对评审的关键事实性断言**重新核实**，结果见文末「事实复核」：六项阻断项的源码证据全部成立，评审结论当前有效。
> 本文是评审留档与生产化缺口的**清单权威**，非契约文档；P0/P1 尚未立项排期，立项时走 docs/08 + 对应契约文档 + 必要 ADR 流程。

## 结论先行

> **一句话：内核已成，外壳未做。**
> 作为「**可演示、可陪同试用的技术验证 MVP**」——**达到，且完成度高**；
> 作为「**客户能自助接入真实店铺、数据不丢、长期稳定运行的生产 MVP**」——**未达到**，差一整层生产化能力。

两个口径必须分开，否则结论会骗人。

## 一、实际验证项（非转述文档）

| 验证项 | 方式 | 结果 |
|---|---|---|
| 后端启动/健康 | 真实 uvicorn :8000 | `/api/health` ok、首页 200 |
| 场景 A 自动退款 | 真实 HTTP 跑模板 `refund-auto` + 订单 12345 | 节点输出 `refunded`，模拟商家后台该单**从待处理消失** |
| 场景 B 敏感转人工 | 订单 12346（¥5000） | 节点输出 `human_review`、离开自动退款队列 |
| 场景 C 自然语言生成 | `/api/nl/generate`（无 key 离线兜底） | 200，生成 3 节点草稿 |
| 编辑器闭环 | 浏览器登录→选模板→点节点→编译运行 | Schema 表单、变量插入、Problems「无问题」、**SSE 10 条事件实时上屏**、结果弹窗 |
| 题二子系统端点 | 15 个 API 实测 | runs/metrics(p50/p95)/告警规则/灰度状态机/审批卡片/版本/反馈全部 200 且有真实结构 |
| 测试基线 | 复跑三道门 | 后端 782 pass、集成 24、前端 498 pass、build 过 |

核心价值闭环（**非技术人选单→AI 按原因/金额决策→限额内自动退、超额转人工→画布实时看得见→模拟后台落账**）在 Demo 环境**确实完全可用**。

## 二、题面六项要求逐条达成度

### 题一·Schema 驱动内核（`frontend/src/lib/`，模块+测试齐全）

| 要求 | 状态 | 落地 |
|---|---|---|
| 嵌套/数组/枚举/条件显示 | ✅ | 8 节点 schema + `forms/uiSchema`（hiddenWhen/group） |
| 自定义控件扩展 | ✅ | `WidgetRegistry`/`resolveWidget`/`defaultRegistry` |
| 变量 `{{}}` 自动补全 | ✅ | `scope.ts`/`useScope.ts` + 表单「插入变量引用」 |
| 跨节点依赖校验+高亮 | ✅ | L2 ScopeIndex 引用校验 + `markers` 定位 |
| 图级校验（不可达/环/缺分支） | ✅ | `validation/l3.ts`（含数据依赖环 GRAPH_DATA_CYCLE） |
| 实时校验/错误定位/性能 | 🟡 全 | 增量脏标记 `dirty.ts`+结构化诊断；**Web Worker 仍缓做（D29）** |

### 题二·端到端（后端模块真实存在）

| 要求 | 沙盘/Demo 语义 | 生产语义 |
|---|---|---|
| NL 生成/技能推荐/自动组合 | ✅ 离线规则+模板 | 真实 LLM 需自带 key，开放质量未验证 |
| 多 Bot 协同（客服/物流） | 🟡 单进程任务信封+幂等/CAS（`coordination/`） | `logistics/adapter.py` 仅 **85 行假 Bot**，非分布式多智能体 |
| 协议/超时/幂等/冲突 | ✅ 任务信封状态机 | NATS/Go 网关缓做（D5/D6） |
| 人机审批+交互模板+渠道 | ✅ 双向卡片 schema + web/im/email 渲染降级 | **不真实投递**（`message` 仅进程内记录，D24） |
| Webhook/IM/嵌入/灰度/回滚 | 🟡 灰度状态机+指标门控自动回滚引擎在（`routing/`） | 无真实 ingress/签名验签/多实例热同步（D32） |
| 可观测（追踪/指标/告警/回放） | ✅ 自研 span+metrics+alerts+录制回放 | OTel/Prometheus/Grafana 正式栈缓做（D11） |

## 三、生产口径阻断项（均经源码核实）

| # | 阻断项 | 证据 | 严重度 |
|---|---|---|---|
| 1 | **交付形态纯内存、重启全丢** | `docker-compose.yml` 只有单 atlas 服务、**不起 PG、无数据卷**；PG 两档代码已就绪但缺省不启用 | P0 |
| 2 | **认证是明文沙盘** | `iam/principals.py` 密码明文硬编码、无哈希；`sessions.py` 进程内 dict、重启全登出、无 JWT/TTL/注册/改密 | P0 |
| 3 | **接不了真实世界** | shop 全模拟；http/database 适配器无凭证加密/OAuth/SSRF 白名单/只读强制/SQL 审查（D22/D23）；邮件/IM/短信不投递；物流是 stub | P0（产品承诺「接真实店铺」无法兑现） |
| 4 | **无部署/运维面** | 仅 `ci.yml`+定时回放，**无 CD**；无 TLS/反代/多实例；中断恢复仅单实例（D19/D20）；**LICENSE 缺失** | P1 |
| 5 | **零真实客户验证** | [docs/18](18-种子客户验证计划.md) §8 写明「**假定 Go（沙盘假设，非实测）**」，§5 的 Go 指标、C1–C5 客户表**全空** | 决策风险 |
| 6 | 核心智能依赖自带 LLM key | 无 key 仅确定性规则（只覆盖退款黄金用例） | P1 |

这些缺口项目自己在 [docs/14](14-缓做事项登记表.md) 里诚实登记了触发条件，不是隐瞒或烂尾——但「已登记」不等于「已具备」，对上线而言仍是硬缺口。

## 四、最终判定

- **「产品核心完全可用」中的「核心」（搭流程→AI 决策→自动退/转人工→实时可观测）：在 Demo/陪同试用环境完全可用**，题一、题二的引擎与编辑/治理能力完成度和测试厚度（后端 15.3k 行/前端 20.2k 行、合计约 1300 测试）在同类原型里属高水准。
- **但「可上线的 MVP」不成立**：当前是一个**高完成度的单机内存原型**，缺持久化交付、生产认证、真实安全接入、真实渠道投递、部署运维、真实客户验证六层。任何一层缺失都意味着不能让真实客户自助、长期、接真实数据使用。

**若目标是给 3–5 家客户做「陪同演示 + 试用反馈」：现在就能上**（docs/18 的 Phase 1 形态，且 `TRIAL.md` runbook 齐备）。
**若目标是「客户注册后自己接店铺、无人值守跑真实退款」：还差一个明确的生产化阶段。**

## 五、上线前最小必做清单（生产口径）

**P0（不做不能面对真实客户）**

1. 交付 compose 启用 PG：数据卷 + 备份/恢复演练。
2. 密码哈希 + 会话持久化/JWT + 账号生命周期。
3. 真实接入安全准入：凭证加密、SSRF egress 白名单、DB 只读、OAuth。
4. 至少打通**一个**真实电商渠道 + 一种审批真实送达（邮件/IM）。

**P1（上线同期必须补齐）**

5. TLS/反代/CD 部署；明确单/多实例 SLA。
6. 可观测导出到正式栈（OTel/Prometheus/Grafana）。
7. License 与数据条款（当前仓库无 LICENSE）。

**验证闸**

8. 用真实 3–5 家种子客户回填 docs/18 §5 指标，替换「假定 Go」。

> 接力处理：P0/P1 目前只作为下一轮立项候选登记在 handoff，未自行开工；拆里程碑时建议 P0 按「持久化交付 → 生产认证 → 真实接入」三个契约批次推进（M5/M11 的 PG 两档代码已就绪，主要缺口是交付形态与真实世界边界）。

## 六、评审信心指数与事实复核

原评审信心指数 **8.5/10**：功能与运行结论来自真实起服务/HTTP/浏览器实测和源码核实（高把握）；扣分点：① 未在本机执行 `docker compose up --build` 全镜像构建（容器路径据 Dockerfile/compose 静态核实）；② 题二「生产语义」部分判断引用 docs/14 的自我登记而非逐一渗透验证。

**2026-09-21 接力复核（dev HEAD `ea29da9`，工作树干净）**：

| 复核项 | 结果 |
|---|---|
| `docker-compose.yml` 单 atlas 服务、无 PG、无数据卷 | ✅ 成立（compose 注释自述「进程内状态随重启清空」） |
| LICENSE 缺失 | ✅ 成立 |
| `iam/principals.py` 明文种子密码、无哈希 | ✅ 成立（`SEED_USERS` 明文 admin123 等，[principals.py](../src/atlas/iam/principals.py)） |
| `iam/sessions.py` 进程内 dict + lock、无持久化 | ✅ 成立（[sessions.py](../src/atlas/iam/sessions.py)） |
| docs/18 §8「假定 Go（沙盘假设，非实测）」 | ✅ 成立（docs/18:110、:114） |
| git 链与 handoff 一致性 | ✅ dev HEAD `ea29da9`，无分叉 |

原评审事实来源：`docker-compose.yml`、`Dockerfile`、`src/atlas/iam/{principals,sessions}.py`、`src/atlas/api/main.py`、`src/atlas/storage/{memory,pg}.py`、docs/18 §5/§8、docs/14、`TRIAL.md`，及该会话实测输出。


---

**2026-09-21 docs/33 打包收口补充复核（dev HEAD `8b29396`）——阻断 #1 的一处更具体代码缺口（新登记，非本包回归）**：

| 复核项 | 结果 |
|---|---|
| PG 整栈 uvicorn 装配缺口：`PgUserStore` 缺 `bind_session_store` | ✅ 成立（源码核实）。[iam/deps.py](../src/atlas/iam/deps.py):39 在装配时**无条件**调用 `user_store.bind_session_store(session_store)`，而该方法仅内存版 `UserStore`（[iam/accounts.py](../src/atlas/iam/accounts.py):44）实现；PG 版 `PgUserStore`（[storage/pg.py](../src/atlas/storage/pg.py):312）无此方法。故设 `ATLAS_STORAGE_BACKEND=pg` 起整栈 uvicorn 时，装配阶段抛 `AttributeError: 'PgUserStore' object has no attribute 'bind_session_store'`，整栈 PG 模式当前不可启动；断言 `STORAGE_BACKEND=='pg''` 的端到端用例 U269 同因失败。 |

- **影响边界**：这是阻断 #1（交付形态纯内存、PG 缺省不启用）之下的一处**具体装配缺口**，不改变原评审六项阻断结论。docs/33 本包（影子/Trace/静默升级值班/i18n）未触碰 iam，非本包引入；本包 PG 行为改由**直连 integration**（`ATLAS_RUN_INTEGRATION=1`＋`DATABASE_URL`，**不设** `ATLAS_STORAGE_BACKEND`，绕过 deps 整栈装配）覆盖，43 passed（含本包 U299 静默升级值班、trace U278 spans 迁移 012、M11 pgvector）。
- **修复去向（建议，不在 docs/33 范围）**：随生产化 P0-1「交付 compose 启用 PG」同批，为 `PgUserStore` 补 `bind_session_store`（并决定会话存储是进程内绑定还是 PG 化，关联阻断 #2 的会话持久化），随后恢复整栈 PG uvicorn 与 U269。
- 同期三道门实测：后端全量 **1051 passed/34 skipped**、前端 vitest/build/oxlint 全绿（oxlint 仅两处既有 warning）、内存档 d26/m11/d28 三 smoke 全过；这些不改变「可上线 MVP 不成立」的判定，仅更新 Demo/陪同试用形态的完成度证据。
