# A2A 执行 Agent 面（Zeus 联邦 W2 接入）— v1 批契约设计

> 状态：**第一阶段已实施（2026-10-01，ADR T32）**。纯新增、plan-only、零业务链改动；后端新增 11 项单测（`tests/test_a2a_vassal.py`）。第二阶段（真 LLM / 起真实 run / 任务持久化 / 生产部署）须另点工。
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

## 2. Agent Card 与 fealty（`src/atlas/a2a/card.py`）

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

fealty 取值（全部落在 Zeus 受控词表内，否则注册被拒）：

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

- 入参：`params.message = {role:"user", metadata:{"x-zeus-runId":...}, parts:[{kind:"data", data:{skill, ...params}}]}`。
- `sendSubscribe` SSE：事件帧 `data: {"jsonrpc":"2.0","id","result":<status|artifact event>}`，收尾帧 `data: {"jsonrpc":"2.0","id","result":<task>}`——Zeus `consumeSseStream` 据此区分中间事件与最终快照。
- 生命周期事件：`submitted → working → artifact-update → completed(final)`；缺参终态 `input-required(final)`。
- 错误码：`-32600/-32601/-32602/-32603/-32001/-32002`（get/cancel 语义同 loom）。

## 5. 认证（`src/atlas/a2a/router.py`）

- 卡片三端点公开（只读能力声明，同 loom）。
- 任务端点校验 `Authorization: Bearer <ATLAS_A2A_TASK_TOKEN>`（`hmac.compare_digest` 恒定时间）。
- fail-closed：prod 档未配置 token → 一律 401；dev/test 档未配置 → 放行并打 WARNING（仅限本地联调）。
- 独立凭证，**不复用**平台登录会话与渠道 OAuth 凭证。

## 6. 验证

- 单测：`tests/test_a2a_vassal.py` 11 项——卡片契约（fealty/tag/url）、rpc（completed 报告、缺参 input-required、未知 skill failed/未知方法 -32601、SSE 生命周期、get/cancel、坏 message -32602）、HTTP（三卡片端点公开、Bearer 401/200、SSE 成帧与收尾快照）。
- 真机协议级联调（2026-10-01）：本机 uvicorn `atlas.api.main:app`（dev，`ATLAS_PUBLIC_BASE_URL=http://127.0.0.1:8933`），Zeus 真实 `VassalRegistry.register` + `sendTaskSubscribe`/`sendTask`：fealty 五字段注册闸全过、卡片 `url` 直接解析任务端点、SSE 四帧正确拆解、`x-zeus-report`（plan，llmTokens=0）回传、缺参 input-required、非流式 send 与流式等价。**Atlas 侧业务链零改动**。
- 复跑单测：`.venv/bin/pytest tests/test_a2a_vassal.py -q`。

## 7. 第二阶段待点工项

1. 生产部署上的同口径回归（真实域名/TLS/网关、`ATLAS_A2A_TASK_TOKEN` fail-closed）。
2. 纯标准 A2A 客户端（不认 `x-zeus-*`）对 Atlas 的超集守护复验（协议第 6 条式）。
3. 任务持久化或明确沿用单副本内存台账；真 LLM / 起真实 run 属独立立项，需先解决单副本与挂起帧幂等（docs/62）。
4. 是否复用/新建机器账号体系（当前为单 env token，多租户调用方区分未做）。
