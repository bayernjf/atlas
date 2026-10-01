# MCP server 面 v1 契约设计

> 状态：**契约立项（2026-10-01，docs-only；零代码／测试／迁移／依赖改动）**。ADR **T33** 已拍板（见 [docs/10](10-技术选型决策记录.md) §4 T33）：**①B 引官方 `mcp` Python SDK v2（钉 `mcp>=2,<3`）＋ ②stdio 传输 ＋ ③只读/plan-only 工具面**。本文是**形状权威**——把「已拍板的选型」落成可实施的形状契约（工具集、协议边界、装配与租户绑定、验证计划、落码原子序）。**本文不落码、不解除 14 D45**：拍板 ≠ 代码已有，`src/` 对 `mcp` 仍零命中、`pyproject.toml` 仍不加 `mcp`（依 T31「依赖必须与代码同事实」，依赖与 `src/atlas/mcp/` **同批**落）。
> 背景：MCP server 面是 Atlas 的**第三个对外出入口**——前两个是平台 REST（[docs/80](80-外部agent经REST驱动Atlas集成说明.md) 归纳的调用面）与 A2A 执行 Agent 面（[docs/90](90-A2A执行Agent面-Zeus联邦接入-v1批契约设计.md)，ADR T32）。D45 记「外部系统今天经 REST／入站 webhook／OpenAPI 导入三条路已能接，缺的是**协议标准化与工具自描述**」——REST 半边已由 docs/80 补说明、A2A 半边已由 T32 落码，本面即**剩下的 MCP 半边**。
> 协议事实源：[docs/10](10-技术选型决策记录.md) §4 T33 的协议核实记录（modelcontextprotocol.io 规格 **`2026-07-28`**（current）＋该版 changelog ＋ PyPI `mcp` 2.0.0）。**本文不复制协议全文**；冲突时以协议原文与 T33 为准。
> 结构模板：[docs/90](90-A2A执行Agent面-Zeus联邦接入-v1批契约设计.md)（同族的第二个对外协议入口）。

## 1. 范围与非目标

**v1 做**：

1. 一个 **stdio 子进程**形态的 MCP server（客户端拉起、本地进程、无网络监听面），入口 `python -m atlas.mcp`。
2. 暴露面限 **`server/discover` ＋ `tools/list` ＋ `tools/call`**（现行 revision `2026-07-28` 的**无状态化**形状：无 `initialize` 握手、无协议级会话；新旧两版客户端由**官方 SDK v2** 一个端点同时应答兜住）。
3. 一个**只读**工具面（若干清单/投影类工具，锚定既有只读 REST 投影，见 §3）——不起 run、不改图、不调 LLM（`cost.llmTokens` 不适用，本面**零模型调用**）、不裁决人工审批。
4. **单租户绑定**：进程经 env `ATLAS_MCP_TENANT_ID` 绑定唯一租户；工具**不带** `tenant_id` 入参 ⇒ **结构上不存在跨租户读取面**（呼应 [docs/90](90-A2A执行Agent面-Zeus联邦接入-v1批契约设计.md) §7 第 4 项的教训）。

**明确不做（v1 非目标，与 T33 的「未选面」逐条对应）**：

- **不**实现 ①A「自研最小 MCP 子集」（已弃；选 B 的理由见 T33）。
- **不**做 ②Streamable HTTP 远端传输（v1 仅 stdio；远端＝选型变更，须先记 docs/10 §4）。
- **不**暴露 ③**含副作用**工具（跑图／退款／发消息一律不在此面；若将来进 MCP，必须先与人工 Gate／审批口径对齐、不绕 [docs/62](62-挂起帧一次性认领-单副本硬约束护栏-v1批契约设计.md) 单副本三道闸）。
- **不**实现 `resources`／`prompts`，**不**实现已被官方弃用的 `sampling`／`roots`／`logging`（SEP-2577）。
- **不**复用平台 `sess-` 会话与渠道凭证；**不**自造协议层（握手／会话／`resultType`／`server/discover` 均由 SDK 承担，见 §2）。
- **不**新增 REST 端点、不新增错误码、不改前端、不加迁移。
- **不**解除任何缓做；**D45 仍开**（MCP 面代码仍无）。未选面登记 [docs/14](14-缓做事项登记表.md) **D49**。

## 2. 协议形状（由 SDK 承担，Atlas 不自造）

本面**不自己实现 MCP 协议**——T33 拍板选官方 SDK 的核心理由就是「客户端混版 ＋ 协议季度级破坏性演进下，自造双模协商＋自担 revision 跟踪不划算」。故协议层的形状**不是 Atlas 的契约**，Atlas 只守「用什么 SDK API、暴露什么、怎么装配」：

| 协议事实（revision `2026-07-28`） | 谁承担 | Atlas 侧含义 |
| --- | --- | --- |
| 无 `initialize`／`initialized` 握手、无协议级会话（SEP-2575） | SDK | Atlas 不写握手代码 |
| 每请求在 `_meta` 自带 `protocolVersion`＋`clientInfo`＋`clientCapabilities` | SDK | 不需要 Atlas 读这些字段 |
| `server/discover`（服务端 **MUST** 实现） | SDK | **Atlas 不得关闭该能力**（SDK 默认提供） |
| 所有结果必带 `resultType`（SEP-2322 MRTR） | SDK | Atlas 只需保证工具返回值**可 JSON 序列化** |
| 工具 schema 用**完整 JSON Schema 2020-12** | Atlas（工具定义）＋SDK（协议承载） | 见 §3：工具的 `inputSchema` 由 Atlas 写、须是合法 JSON Schema 2020-12 |
| 旧版客户端 `initialize` 回退（混版） | SDK v2 | Atlas 不做双模协商 |
| `roots`／`sampling`／`logging` 已弃用（SEP-2577） | — | Atlas **不声明**这些能力 |
| Streamable HTTP 的去会话/去 GET 流 | — | 与本面无关（v1 仅 stdio） |

**SDK 具体 API 以落码批核对的 v2 文档为准**（T33 记录 v2 稳定线为 `mcp` 2.0.0、`FastMCP` 已更名 `MCPServer`、Python ≥3.10）；**本文只定 Atlas 侧形状**，不在此钉 SDK 的函数签名——避免把未核实的 SDK API 形状写成契约（T33 初稿曾按旧版协议形状写错并已校正，本节不重蹈）。

## 3. 工具面 v1（只读；`src/atlas/mcp/tools.py`）

工具集**锚定既有只读 REST 投影**（不凭空发明新读面，逐条对应 `src/atlas/api/main.py` 的 `read` 档 GET 端点）。**以下 7 项为 v1 候选集，落码批冻结**（见 §7 第 1 项）：

| 工具名 | 锚定的只读投影 | 入参（JSON Schema 2020-12） | 返回 |
| --- | --- | --- | --- |
| `atlas_list_graphs` | `GET /api/graphs`（main.py:2319） | 无 | `{"items": [...]}`（逐字同 REST 投影） |
| `atlas_get_graph` | `GET /api/graphs/{graph_id}`（:2325） | `graph_id`（string，必填） | 图原文（同 REST）；未知/跨租户 ⇒ 工具级错误 |
| `atlas_list_templates` | `GET /api/templates`（:2766） | 无 | `{"items": [...]}`（列表投影不含 graph） |
| `atlas_get_template` | `GET /api/templates/{template_id}`（:2883） | `template_id`（string，必填） | 模板元数据（含 graph） |
| `atlas_list_runs` | `GET /api/runs`（:3711） | `status`（enum，可选）、`limit`（int 1–200，可选，缺省 50） | `{"items": [...]}`（新→旧） |
| `atlas_list_interruptions` | `GET /api/interruptions`（:3882） | 无 | `{"backend": ..., "visibility": ..., "items": [...]}`（**收窄见下**） |
| `atlas_list_adapters` | `GET /api/adapters`（:2047） | 无 | 适配器清单（含工具声明） |

**只读不变量（铁律）**：

1. 工具实现**只调**只读投影（`store.list()`／`store.get()`／纯投影函数），**不调**任何写路径、不 `save`／`update`／`delete`、不起 run、不改图、不调 LLM、不裁决审批。
2. 工具**不接收** `tenant_id` 入参（§4 绑定）；也**不接收**任何可影响他人数据的 id 以外的写意图参数。
3. 工具**命名**用 `atlas_<verb>_<noun>` snake_case、**不带斜杠**（对齐 docs/77 R8 的「无斜杠工具名」教训，避免落到 `SIMULATED` 类歧义）。

**`.interruptions` 投影的**刻意收窄**（与 REST 面不同，须显式记录）**：REST `/api/interruptions` 的 `items[]` 含 `resumeToken`（[docs/89](89-项目级代码审计与功能全景.md) A-8 已登记该字段随投影外泄的风险）。MCP 是**对外**面，故 `atlas_list_interruptions` 的投影**剔除 `resumeToken`**（保留 `runId`／`graphId`／`nodeId`／`kind`／`state`／`claimedSeconds` 等），使只读面**不携带可用于续跑的令牌**。这是**有意的面间差异**，不是漏字段。

**失败口径**：工具的失败（未知 id ⇒ 404 语义、非法 `status`／越界 `limit` ⇒ 422 语义）一律以**工具级错误**返回（`isError: true` ＋ 人类可读消息），**不抛协议级 JSON-RPC 错误**——协议层只承载「工具不存在／参数不符合 schema」这类形式问题，业务失败在工具结果里表达（同 docs/90 §4 的「不因业务失败在协议层拒标准调用方」同族纪律）。

**返回内容形态（v1）**：每个工具结果只回 **`text` content**（JSON 序列化串）。**v1 不出 `outputSchema`／`structuredContent`**——加 `outputSchema` 是**纯超集**，待确有 typed 结构化消费方时再补（见 §7 第 3 项）。

## 4. 进程与传输模型（`src/atlas/mcp/server.py` ＋ `__main__.py`）

- **传输＝stdio，无网络监听面**。进程由 MCP 客户端拉起（`python -m atlas.mcp`），stdin 收 JSON-RPC、stdout 出 JSON-RPC。
- **stdout 纯净性（硬约束）**：stdio 传输下 **stdout 只能含 JSON-RPC 帧** ⇒ Atlas 侧日志**必须走 stderr**（MCP 进程内不得用默认 stdout 日志 handler；`__main__.py` 显式把日志 handler 指向 `sys.stderr`）。这是 stdio 形态最易踩的坑，列为验收项（§6）。
- **租户绑定（fail-closed）**：进程启动读 `ATLAS_MCP_TENANT_ID`——
  - 缺失／空 ⇒ **拒绝启动**（非零退出，stderr 打明确原因）；
  - 值非已知租户 ⇒ **拒绝启动**（不猜测、不落到默认租户）。
  - 绑定后，进程内构造**只读主体**（`read` 档，复用既有 `iam` 主体/租户注册表语义），**不新增角色、不复用 `sess-` 令牌**。
- **数据访问路径（不拉起 Web 栈）**：复用既有 `atlas.iam.registry` 的 `TenantRegistry` 单例（`main.py:83` 的 `from atlas.iam.registry import STORAGE_BACKEND, TenantServices` 同源）与只读投影函数／store；**明确不 `import atlas.api.main`**——否则会连带拉起 FastAPI app、调度线程与启动恢复扫描，把「一个只读工具进程」变成「第二个后台服务」（也违背单副本纪律）。
- **存储档差异**：default 交付形态是 **PG 档**（[docs/30](30-数据库持久化与迁移运行器-v1批契约设计.md)／T24），MCP 进程读同一 PG，所见与平台一致。**内存档**下 MCP 是一个**进程内独立空实例**（同 REST 侧 `GET /api/interruptions` 的既有处境），故工具结果**不得把「空」读成「没有」**——至少 `atlas_list_interruptions` 沿用既有 `backend`/`visibility` 自报（内存档＝`frames-not-persisted`）；其余列表类工具是否统一带 `backend` 自报见 §7 第 2 项。

## 5. 安全与边界

- **无网络鉴权面**：stdio ＝本地进程、客户端拉起，可整块绕开 OAuth／CIMD／EMA 授权演进（T33 §② 的理由之一）。**代价照实**：谁能拉起该进程，谁就获得绑定租户的**只读**视野——这是本地进程信任边界，v1 不引入额外鉴权（远端面属 D49 未选）。
- **独立凭证**：**不复用**平台登录会话与渠道 OAuth 凭证；进程内不持有任何写凭证。
- **不泄漏秘密**：工具结果与日志不得回显 env（含 `ATLAS_MCP_TENANT_ID`、任何 API key）；日志只到 stderr（§4）。
- **外发面收窄**：`atlas_list_interruptions` 剔除 `resumeToken`（§3）。
- **守住既有硬约束**：只读面**不触碰** [docs/62](62-挂起帧一次性认领-单副本硬约束护栏-v1批契约设计.md) 单副本三道闸（README `replicas: 1`／Dockerfile `--workers 1`／entrypoint fail-closed）与挂起帧幂等——因为**它不写任何状态**；`GET /api/interruptions` 的 `claimed_suspended` 只「显形不处理」（[docs/76](76-挂起点只读投影批-D42收口-v1批契约设计.md)），MCP 面同理。
- **prod 口径**：本面无独立 prod fail-closed 令牌门（无网络面），其「档位」体现在**租户绑定的 fail-closed**（缺失/未知即拒启）与**内存档自报**上；远端 HTTP 面若将来评估，鉴权口径同 T32 的 `ATLAS_A2A_TASK_TOKEN` fail-closed（登记 D49）。

## 6. 验证计划（`tests/test_mcp_server.py`）

**测试编号不占 U 号**（同 A2A／docs/90 先例；U 号留待落码批登记，避免与 [docs/88](88-反思进化模块-v1契约设计.md) §7 预留的 U1033 起冲突）。验收分层：

1. **协议面**：`server/discover` 可用；`tools/list` 返回 §3 全部工具，每个 `inputSchema` 是**合法 JSON Schema 2020-12**（结构断言，含 `type`／`properties`／`required`）。
2. **工具面（每工具一条 happy path）**：`tools/call` 经 SDK 客户端（对 stdio 子进程端到端）取回与 REST 投影一致的形状。
3. **只读不变量**：断言工具 schema **无** `tenant_id` 入参；断言工具实现路径**不命中**任何写方法（以 spy／假 store 断言仅 `list`／`get` 被调）。
4. **失败口径**：未知工具 ⇒ 协议级错误；缺必填参数／`limit` 越界 ⇒ **工具级 `isError`**（不在协议层拒）；未知 id ⇒ 工具级错误。
5. **租户绑定 fail-closed**：未设 `ATLAS_MCP_TENANT_ID` ⇒ 子进程非零退出；设为未知租户 ⇒ 非零退出；（判别对照）设合法租户 ⇒ 正常起并响应 `tools/list`。
6. **stdout 纯净性**：驱动一次 `tools/call` 后断言 **stdout 每行都是可解析 JSON-RPC**（日志若混入 stdout 即红）——stderr 单独断言含预期日志。
7. **外发收窄**：`atlas_list_interruptions` 结果**不含** `resumeToken`（判别对照：REST 端点投影仍含它，证明收窄是本面行为而非全局改动）。
8. **内存档自报**：内存档下 `atlas_list_interruptions` 带 `visibility="frames-not-persisted"`。

常跑守护＝`tests/test_mcp_server.py`；落码批补真机（真实 stdio 客户端拉起）与全量回归数字。**本文档内只描述用例，不写 U 号、不预设通过数**（先跑后写，数字以落码批实跑为准）。

## 7. 待拍板与待点工

> 本节全部为**落码批开工前可决**的小口径，均给推荐项；不擅自拍板（同 docs/90 §7 的双层结构）。

1. ⬜ **工具集是否就锁定 §3 这 7 项**。**推荐：先 7 项**（全部锚既有只读投影、零新读面）。可讨论的增项是 `atlas_get_run`（`GET /api/runs/{run_id}`，单运行详情含挂起信息）——**推荐暂不进 v1**：单运行详情可能含产出/轨迹明细，属较「重」的外发内容，待只读面被真用起来再加（加工具是纯超集）。
2. ⬜ **内存档下列表类工具是否统一带 `backend` 自报**。**推荐：统一带**（`{"backend": "pg"|"memory", "items": [...]}`），理由＝内存档下 MCP 是独立空实例，「空列表」不等于「平台上没有数据」，显式自报避免误读（同 [docs/76](76-挂起点只读投影批-D42收口-v1批契约设计.md) 内存档自报的先例）。
3. ⬜ **是否出 `outputSchema`／`structuredContent`**。**推荐：v1 不出**（只回 text content），待确有 typed 消费方时补——加 `outputSchema` 是纯超集，早加只徒增维护面。
4. ⬜ **入口形态**。**推荐：`python -m atlas.mcp`（`__main__.py`）**，不改 `pyproject.toml` 打包面；若确有客户端偏好 console script（`atlas-mcp`），再另加（超集）。
5. ⬜ **D49 四条未选面的评估时机**：①A 自研子集（已弃，重开需协议面显著收窄）、②Streamable HTTP 远端（出现远端第三方 MCP 客户端需求时评估，鉴权口径同 T32）、③含副作用工具（只读被证明不够用时评估，**必须先对齐人工 Gate**）、④`resources`／`prompts`／扩展（官方 SDK 的 revision/扩展成为必需时评估）。**每次按 AGENTS.md 先记 docs/10 §4 再落码**。

## 8. 落码批原子序（另立批；本批不动代码）

1. `chore(deps)`：`pyproject.toml` 加 `mcp>=2,<3`（＋ `uv.lock` 重生）——**依赖与代码同批**（T31）。
2. `feat(mcp)`：`src/atlas/mcp/`（`__init__.py` 导出／`tools.py` 只读工具定义与实现／`server.py` 注册与装配／`__main__.py` stdio 入口＋stderr 日志＋租户绑定 fail-closed）。
3. `test(mcp)`：`tests/test_mcp_server.py`（§6 分层），按需登记 docs/13 的 U 号（从预留号段起，不撞 docs/88 §7）。
4. `docs(mcp)`：docs/00／09／10 §4 T33／12（非 REST 注记）／13（U 号）／14 D45·D49／08 立项-收口条／handoff／CHANGELOG 同步。
5. 原子提交（docs-only 与代码分开），**不 push**（除非用户明确说）。

**边界**：以上 1–5 全部属**另立批**，本批（本文档）只立形状，不落码、不加依赖、不解除 D45。