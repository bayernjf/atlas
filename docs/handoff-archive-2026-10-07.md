# Handoff Archive — 2026-10-07

> 2026-10-07 从 handoff.md「Recently shipped」滚出（保持最近 5 条上限；逐字搬移、原文不改写）。

## 打包 BH 落码收口

> ✅ **打包 BH 落码收口＝D55 冻结桶第五批，发送三码补译出桶＋WEBHOOK_MALFORMED 跟完（2026-10-05 承接 BG 自推，证据修正 BE 误判；docs/08 打包 BH 块；docs/13 打包 BH 小节；docs/14 D55 15→12；U1160＋LOG_ONLY、新增 U1166）**：`replay_failed` 调真 `send` 全路径被实证——渠道真配时 `SMTP/WEBHOOK/IM_SEND_FAILED` 经 replay 端点结构化出体（409），补 zh/en（65→68 键）＋params（SMTP `{detail}`／聚合 `{channel,failed,total}`）出桶；inner 日志字面量经 `LOG_ONLY_LITERALS`＋U1166 钉住；WEBHOOK_MALFORMED 跟完＝第三态。**桶 15→12，全部第三态等契约决策，D55 工程侧收尽**。门：守护 **15**、相关 **32**、message 族 **103**、PG 集成 **9**、后端 **2367／150／0**（348.49s）、前端 vitest **788／2**＋oxlint 0/0＋build 过＋i18n 71。零新依赖／零迁移／无 ADR。详见 Active #120。

## 打包 BI 落码收口

> ✅ **打包 BI 落码收口＝D54 闭合，condition 表达式诊断补机器可读通道（2026-10-05 承接 BH 收口后复查自推；docs/08 打包 BI 块；docs/13 打包 BI 小节 U1167；docs/14 D54 闭合）**：`_execute_condition` 补 `expressionErrorCodes`/`Params` 平行数组（等长守卫），**零新码**（`ConditionEvalError` 码族＋非布尔复用 `COND_TYPE_MISMATCH`）；分支前缀由前端组装（params 带 `branch`，新键 `log.conditionBranchError`）；Editor condition 分支补本地化渲染（此前 UI 不可见）；schema +3（nodeSchemas 同步）。门：loader+tracing **109**、后端 **2368／150／0**（282.56s）、前端 vitest **788／2**＋oxlint 0/0＋build 过。零新依赖／零迁移／无 ADR。**D54 闭合＝docs/14 工程内可闭缓做清零**。详见 Active #121。

✅ **打包 BJ 落码收口＝D55 冻结桶第六批，渠道/连接错误结构化出体（2026-10-06 用户「继续」自推；docs/08 打包 BJ 块；docs/13 U1168–U1170；docs/14 D55 12→4）**：三个折叠点（`_conn_http_error`／`_channel_http_error`／入站 webhook）`detail` 由中文字符串改 `{code,message[,params]}`——此前结构化异常上的 code 进不了响应体、前端无法本地化。8 条单答案码补 zh/en 出桶（CHANNEL_ALREADY_BOUND／CHANNEL_ALREADY_REGISTERED／CONNECTION_NOT_FOUND／OAUTH_NO_REFRESH_TOKEN／OAUTH_STATE_INVALID／OAUTH_TOKEN_FAILED／OAUTH_REFRESH_FAILED＋WEBHOOK_MALFORMED）；4 条一码多话粗码（CHANNEL_NOT_BOUND／UNAUTHORIZED／UPSTREAM_FAILED／INVALID_RESPONSE，共 15 条答案）code 出体仍留桶、拆码＝**打包 BK**。U1168–U1170 净增 3。门：后端 **2371 passed／150 skipped／0 failed**（217.20s）、前端 **788/2**、oxlint 0/0、build 过。零新依赖／零迁移／无 ADR；HTTP 状态码不变。详见 Active #122。

## 打包 BL 落码收口

> ✅ **打包 BL 落码收口＝认领后崩溃挂起帧的人工收敛（2026-10-06 承接 docs/14 D36；形状权威 docs/96；docs/13 U1171–U1179；docs/14 D36 人工收敛半边收口＋D56 留）**：`POST /api/interruptions/{resume_token}/resolve`（`administer`，v1 仅 `abandon`）**单事务**把「帧已认领 ∧ run 仍 `suspended`」的卡死 run 置 `interrupted` 并删帧，**不重放**（下游非幂等）。互斥由两条谓词承担（帧侧 `resumed_at IS NOT NULL`、run 侧 `status='suspended'`），内存档 409、非法 action 422；前端 Waits 仅 `claimed_suspended`＋admin 渲染「了结」。U1171–U1179（含并发反向门）＋前端 1 例。**偏差照实**：`audit_events` 无 detail 列，runId/reason/claimedBy/claimedAt 不入审计行（token 在 path）。门：后端 **2384／154／0**、前端 **789/2**、oxlint 0/0、build 过。零新依赖／零迁移／无 ADR。详见 Active #124。

> ✅ **打包 A1 落码收口＝D25 模板库产品化收尾三件（2026-10-07 承接用户「那你推进」A 档修正清单；形状权威 docs/97；docs/13 打包 A1 小节；docs/14 D25 部分取回、整体不解除；从 handoff Recently shipped 滚出，2026-10-08）**

✅ **打包 A3 落码收口＝D30 图变量受限来源与字段级可见（2026-10-07，七原子 `aae9fa8`→`d7a5809`；docs/99 §7 注记；后端 2424/166/0、前端 816/2、守护门 16；未 push）**：`source:"env"|"secret"` 受限来源＋编译四码＋运行期 fail-closed＋五通道 `<redacted>` 脱敏＋前端来源 Select/`••••` 占位；`SECRET_UNAVAILABLE` 补文案移出豁免表。详见 Active #127。
