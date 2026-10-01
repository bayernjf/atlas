# A2A 执行 Agent 面（Zeus 协同决策平台 W2 接入）— v1 批契约设计

> 状态：**第一阶段已实施（2026-10-01，ADR T32）**。纯新增、plan-only、零业务链改动；后端新增 13 项单测（`tests/test_a2a_execution_agent.py`，含纯标准 A2A text part 兜底两条）。同日纯标准 A2A 客户端守护真机复验通过（见 §6），并据此补了 text part 超集兜底（commit `255064b`）。**第二阶段第一批已收口（2026-10-01，docs-only，零代码／测试／迁移／依赖改动）**：§7 第 2 项按 §6 实测证据正式勾掉；第 3 项定为**明确沿用单副本内存台账**（决策与理由见 §7，决策记录 docs/08）。**仍开两项**：第 1 项生产部署同口径回归（需真实域名/TLS/网关等外部部署条件）、第 4 项机器账号体系（多租户调用方区分，属产品/契约口径、待拍板）。
> 背景：Zeus 是多 Agent 协同决策平台，已定「执行 Agent 协议 = 标准 A2A + 一层 x-zeus-fealty 契约扩展」；pr-helper 为 W1（已真机定型），loom 为 W2 首个已有 A2A 实现（Q150，2026-10-01 与 Zeus 协议级真机联调通过）。Atlas 与 loom 同波（W2），本批把**同一套已定型协议**第一次落到一个此前零 A2A 代码的产品，验证「从零按协议接入」的可复制性。
> 上游契约事实源：zeus 仓 `docs/design-vassal-protocol.md`（协议）与 loom 仓 `docs/design-a2a-vassal.md` + `backend/app/core/a2a/`（同构参考实现）。本文只写 Atlas 侧落点，不复制协议全文。

## 1. 范围与非目标

**第一阶段做**：

1. 只读公开的 Agent Card 发现端点：`GET /api/a2a/agent-card`、`GET /.well-known/agent-card.json`、`GET /.well-known/agent.json`（三者同卡，与 loom 路径一致）。
2. 任务端点 `POST /api/a2a/tasks`（JSON-RPC：`tasks/send` / `tasks/sendSubscribe` / `tasks/get` / `tasks/cancel`），纯内存任务台账（单进程，30 分钟 TTL，500 上限，同 loom）。
3. 两个 plan-only skill，全部返回**行动方案 artifact**，不起 run、不改图、不调 LLM、不裁决人工审批。
4. Bearer 保护任务端点：`ATLAS_A2A_TASK_TOKEN`，与平台会话（`sess-` 不透明 token）及渠道凭证**物理分离**。

**明确不做（与 loom 第一阶段同口径）**：

- 不通过 A2A 起真实编排运行、不触发 Harness 真实副作用（退款/发邮件/写外部系统）。
- 不绕过任何人工 Gate：图内的 human wait / 审批仍只由平台会话内的 operator/admin 决策。
- plan 不花模型 token（`cost.llmTokens = 0`）。
- 任务不持久化（进程内存；Atlas 本身有单副本硬约束，见 docs/62），不做多副本。
- 不实现 push notification / OAuth；认证仅单 Bearer。

## 2. Agent Card 与注册握手（`src/atlas/a2a/card.py`）

卡片是能力声明的单一事实源，改能力只改本文件。关键字段：

| 字段 | 值 | 说明 |
| --- | --- | --- |
| `name` | `atlas` | Zeus 目录以此为 key |
| `url` | `{ATLAS_PUBLIC_BASE_URL}/api/a2a/tasks` | 留空 env 则为相对路径（同 loom Q238 口径） |
| `preferredTransport` | `JSONRPC` | |
| `capabilities.streaming` | true | sendSubscribe 走 SSE |
| `skills[].id` | `plan-approval-flow` / `diagnose-run` | 均带 `plan-only` tag |
| `authentication.schemes` | `["bearer"]` | |
| `x-zeus-fealty` | 见下 | Zeus 注册闸校验 |

x-zeus-fealty 取值（全部落在 Zeus 受控词表内，否则注册被拒）：

```json
{
  "version": "1",
  "swornTo": "zeus",
  "domain": "ops-orchestration",
  "dataRealms": ["enterprise"],
  "dataPolicy": "read-task-scope",
  "reportBack": true,
  "escalationPolicy": "auto",
  "sla": { "ackSeconds": 10 }
}
```

- `domain: ops-orchestration` 区别于 loom 的 `content-production`，表达 Atlas 的运营体编排能力域；Zeus 按 domain 路由，不预设具体职责。
- `escalationPolicy: auto` 与 Atlas「AI 只产候选、人工 Gate 裁决」天然同构：不可逆动作升级给人。

## 3. plan-only skills（`src/atlas/a2a/skills.py`）

纯函数，无 DB / LLM / 副作用。缺必填参数回 `input-required`（不抛错），未知 skill 回 `failed`。

| skill | 必填参数 | plan 内容 |
| --- | --- | --- |
| `plan-approval-flow` | `tenant_id`, `flow_name` | 固定 OA 审批 Graph 的触发/Schema、节点连线（触发→条件→人工等待→分支）、挂起审批渠道与一次性认领约束（docs/62）、发布前 release gate 清单 |
| `diagnose-run` | `tenant_id`, `run_id` | run 状态/span 定位、suspended 挂起帧投影（claimed_suspended）、等待形态判断、只读结论；**不重放/不续跑**（重放属未决 D36） |

任务结果与 loom 同构：`artifacts[0]` 含 data part（`{mode:"plan", skill, params}`）、text part（编号步骤）、`x-zeus-report`（summary/evidence/cost/followUps）。

## 4. JSON-RPC 与 SSE（`src/atlas/a2a/rpc.py`）

逐字节沿用 loom `backend/app/core/a2a/rpc.py` 已与 Zeus 真机对齐的形状（仅 plan 执行器换成 Atlas skills）：

- 入参（Zeus 原生形态）：`params.message = {role:"user", metadata:{"x-zeus-runId":...}, parts:[{kind:"data", data:{skill, ...params}}]}`。
- **标准 A2A 超集兜底（2026-10-01 守护复验后补，对齐 pr-helper `parseSkillAndParams`）**：message 无 data part 时，回退取第一个非空 text part 的首个空白分词作 skill、参数为空——只懂标准协议的调用方（不发任何 Zeus 扩展）发纯 text 也能被受理；缺必填参数由 skill 层回合法终态 `input-required`、未知 skill 回 `failed`，不在协议层 -32602 拒绝标准调用方。data 与 text 均不可用时才回 -32602。
- `sendSubscribe` SSE：事件帧 `data: {"jsonrpc":"2.0","id","result":<status|artifact event>}`，收尾帧 `data: {"jsonrpc":"2.0","id","result":<task>}`——Zeus `consumeSseStream` 据此区分中间事件与最终快照。
- 生命周期事件：`submitted → working → artifact-update → completed(final)`；缺参终态 `input-required(final)`。
- 错误码：`-32600/-32601/-32602/-32603/-32001/-32002`（get/cancel 语义同 loom）。

## 5. 认证（`src/atlas/a2a/router.py`）

- 卡片三端点公开（只读能力声明，同 loom）。
- 任务端点校验 `Authorization: Bearer <ATLAS_A2A_TASK_TOKEN>`（`hmac.compare_digest` 恒定时间）。
- fail-closed：prod 档未配置 token → 一律 401；dev/test 档未配置 → 放行并打 WARNING（仅限本地联调）。
- 独立凭证，**不复用**平台登录会话与渠道 OAuth 凭证。

## 6. 验证

- 单测：`tests/test_a2a_execution_agent.py` 13 项——卡片契约（注册握手/tag/url）、rpc（completed 报告、缺参 input-required、未知 skill failed/未知方法 -32601、SSE 生命周期、get/cancel、坏 message -32602、**纯 text part 回退首分词→input-required/failed、无 data 且无 text 仍 -32602**）、HTTP（三卡片端点公开、Bearer 401/200、SSE 成帧与收尾快照）。
- 真机协议级联调（2026-10-01）：本机 uvicorn `atlas.api.main:app`（dev，`ATLAS_PUBLIC_BASE_URL=http://127.0.0.1:8933`），Zeus 真实 `VassalRegistry.register` + `sendTaskSubscribe`/`sendTask`：注册握手五字段注册闸全过、卡片 `url` 直接解析任务端点、SSE 四帧正确拆解、`x-zeus-report`（plan，llmTokens=0）回传、缺参 input-required、非流式 send 与流式等价。**Atlas 侧业务链零改动**。
- **纯标准 A2A 客户端守护复验（2026-10-01，Zeus `scripts/acceptance-standard-a2a.mjs`，不认任何 x-zeus-*）**：首跑暴露真实超集缺口——脚本发纯 text part，rpc 只认 data part ⇒ 回 `-32602 message needs a data part`，即「只懂标准协议的调用方不可调用」，违反超集承诺；按 §4 加 text 兜底后复跑 **exit 0**（发现 atlas 2 skill；纯 text `diagnose-run` 被受理并回合法终态 `input-required`，不抛协议错误）。
- 复跑单测：`.venv/bin/pytest tests/test_a2a_execution_agent.py -q`；修复后全量 **2161 passed / 136 skipped / 0 failed**（372s，净增 2）。

## 7. 第二阶段待点工项

> 进度（2026-10-01，第二阶段第一批，docs-only，零代码／测试／迁移／依赖改动）：第 2、3 项已收口（见下）；第 1 项仍开（需真实域名 + TLS + 网关的外部部署条件）；第 4 项仍开但**已从「一句话」推进为「候选 + 建议、只差拍板」**（见下，含代码事实：当前无租户绑定）。

1. ⬜ 生产部署上的同口径回归（真实域名/TLS/网关、`ATLAS_A2A_TASK_TOKEN` fail-closed）。**待外部部署条件**：需先在真实域名 + TLS 终结点 + 网关后部署一套 Atlas（当前仅有本机 `127.0.0.1` 联调与 demo 网关），并在该环境验证 Bearer fail-closed 与三卡片端点公网可达性；本轮无可闭环的工程内动作。
2. ✅ 纯标准 A2A 客户端（不认 `x-zeus-*`）对 Atlas 的超集守护复验（协议第 6 条式）。**已收口**：以 §6 的真机复验证据为准——Zeus `scripts/acceptance-standard-a2a.mjs`（不认任何 `x-zeus-*`）在本机 dev 栈 **exit 0**，发现 atlas 2 个 skill，纯 text `diagnose-run` 被受理并回合法终态 `input-required`（首跑暴露的「只认 data part ⇒ -32602 拒标准调用方」缺口已由 `255064b` 的 text 首分词兜底修复，见 §4）。常跑守护由 `tests/test_a2a_execution_agent.py` 的两条纯 text part 用例（回退首分词→`input-required` / 未知 skill→`failed`）承接。
3. ✅ 任务持久化或明确沿用单副本内存台账。**决策：明确沿用单副本内存台账**（与 `docs/62` 单副本硬约束一致，同 loom Q150 口径）。理由：A2A 任务为 plan-only 咨询性产物（`cost.llmTokens = 0`）、零业务状态零副作用，进程重启丢失至多导致调用方重发一次；若为此引入持久化＋多副本，只为零业务价值付迁移与副本一致性代价。**触发复评**：一旦启用真 LLM / 经 A2A 起真实 run（属独立立项），须连同挂起帧幂等（`docs/62`）一并重新评估持久化。决策记录见 `docs/08`；缓做登记见 `docs/14` D48。
4. ⬜ 是否复用/新建机器账号体系（当前为单 env token，多租户调用方区分未做）。**待拍板**：属产品 / 契约口径（多租户调用方身份、凭证分发与轮换、审计归属），需与平台会话 / 渠道凭证体系一并定调，暂不擅自扩范围。**已给出候选与建议（2026-10-01 回代码核实，仍不擅自拍板）**：
   - **现状（代码事实）**：`a2a/router.py:48-58` 只比对单个 `ATLAS_A2A_TASK_TOKEN`，**不产生任何调用方身份**；而 `tenant_id` 由调用方在 skill 参数里自带、`a2a/skills.py:39-54` 只回显不校验 ⇒ **任何持 token 的调用方都能以任意 `tenant_id` 调用**（无租户绑定）。今天无害（plan-only、零数据、零副作用），但一旦按 **D48** 扩到真 LLM / 真实 run，这就是一条**跨租户读取面** ⇒ item 4 是那次扩展的**前置**，不是可选项。
   - **三个候选（取舍，待你拍）**：① **多 token 标签**（`ATLAS_A2A_TASK_TOKENS=caller:token,…`，caller 同时限定其可用 `tenant_id`）——零迁移、零端点、纯 config 超集、最小可落；代价＝轮换靠改 env、无审计表。② **机器账号表**（迁移＋凭证哈希＋轮换＋审计归属，与平台会话 / 渠道凭证收敛到同一账号体系）——中等批次。③ **OAuth2 client credentials**（标准 A2A 认证方案之一）——互操作性最好，但属**选型变更**，须先立 `docs/10 §4` ADR。
   - **建议（供参考）**：先 ① 作过渡（把「谁在调、能调哪个租户」变成显式且可机检），机器账号与平台账号体系统一立项时再收敛到 ②；③ 留到真有第三方调用方接入时评估。
