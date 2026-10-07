# Handoff Archive — 2026-10-07

> 2026-10-07 从 handoff.md「Recently shipped」滚出（保持最近 5 条上限；逐字搬移、原文不改写）。

## 打包 BH 落码收口

> ✅ **打包 BH 落码收口＝D55 冻结桶第五批，发送三码补译出桶＋WEBHOOK_MALFORMED 跟完（2026-10-05 承接 BG 自推，证据修正 BE 误判；docs/08 打包 BH 块；docs/13 打包 BH 小节；docs/14 D55 15→12；U1160＋LOG_ONLY、新增 U1166）**：`replay_failed` 调真 `send` 全路径被实证——渠道真配时 `SMTP/WEBHOOK/IM_SEND_FAILED` 经 replay 端点结构化出体（409），补 zh/en（65→68 键）＋params（SMTP `{detail}`／聚合 `{channel,failed,total}`）出桶；inner 日志字面量经 `LOG_ONLY_LITERALS`＋U1166 钉住；WEBHOOK_MALFORMED 跟完＝第三态。**桶 15→12，全部第三态等契约决策，D55 工程侧收尽**。门：守护 **15**、相关 **32**、message 族 **103**、PG 集成 **9**、后端 **2367／150／0**（348.49s）、前端 vitest **788／2**＋oxlint 0/0＋build 过＋i18n 71。零新依赖／零迁移／无 ADR。详见 Active #120。

## 打包 BI 落码收口

> ✅ **打包 BI 落码收口＝D54 闭合，condition 表达式诊断补机器可读通道（2026-10-05 承接 BH 收口后复查自推；docs/08 打包 BI 块；docs/13 打包 BI 小节 U1167；docs/14 D54 闭合）**：`_execute_condition` 补 `expressionErrorCodes`/`Params` 平行数组（等长守卫），**零新码**（`ConditionEvalError` 码族＋非布尔复用 `COND_TYPE_MISMATCH`）；分支前缀由前端组装（params 带 `branch`，新键 `log.conditionBranchError`）；Editor condition 分支补本地化渲染（此前 UI 不可见）；schema +3（nodeSchemas 同步）。门：loader+tracing **109**、后端 **2368／150／0**（282.56s）、前端 vitest **788／2**＋oxlint 0/0＋build 过。零新依赖／零迁移／无 ADR。**D54 闭合＝docs/14 工程内可闭缓做清零**。详见 Active #121。
