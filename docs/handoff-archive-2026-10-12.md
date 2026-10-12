# handoff 归档（2026-10-12）

> 自 handoff「Recently shipped」5 条上限滚下的条目，原文逐字保留，编号／批次名不改。

## 滚出条目

📝 **打包 AK 立项＝D35 记忆机制四切片（docs/122；2026-10-10，docs-only）**：auto_extract（LLM 结构化提取＋阈值 0.85＋source=auto_extract，新工具 memory/auto_extract，fail-closed 空结果）、ai_decision 注入开关（memoryInjection，缺省关闭）、update_policy（overwrite/version/ask）、recall decay（半衰期加权、now_override 确定、缺省关闭）。四决策全"默认值即现状"；零新端点／1 新错误码／无 ADR；U1332–U1344 预留。D35 不解除。

✅ **打包 AK 落码收口＝D35 记忆机制四切片（2026-10-10 同日立项＋落码；docs/122 §6；U1332–U1344 转正式；后端 2668／179／0、PG 集成 111／1／0、前端 980/2＋oxlint 0/0＋build；未 push）** `ai_decision.memoryInjection` 可选注入（缺省关＝逐字不变，并断言「假记忆库一次都没被调用」）、`update_policy` 三态＋`dedup_keys`（两档共用一份命中判据；AL 的手搓去重已收编）、`recall(decay, now_override)` 半衰期重排（衰减对全候选集做，PG 侧先取有界集再重排）、`memory/auto_extract` 工具（门槛是参数不是常数）。**核心改判**：三种「没拿到记忆」是三个答案（unavailable／error／ok+0），本批因此**零新运行时错误码**。记忆页加来源徽标（自动提炼的结论不能长得像人工确认过的）。已知限制照实：auto_extract 不认租户 BYOK（ActionRequest 不带租户）。详见 Active #151。

✅ **打包 AL 落码收口＝D63 全局异常/自愈/状态机（docs/123；2026-10-10，P-1=C/P-2=B 拍板落码）**：globalErrorHandler 三形态（mark_only/route_to_subgraph/escalate_to_human，P-1=C 复用审批闭环；无 handler fail-fast 语义保留、handler 自身异常 mark_only 兜底；接缝对象＝RunNodeFailed）、selfHealing（triggerOn/diagnoseSubgraphId/maxAttempts 1-3/continueWhen，诊断子图 verdict 两层取值）、state_machine 新节点（P-2=B store=memory 跨 run 持续、事件驱动迁移、StateTransitionRejected 进 retry passthrough、SSE state_changed 帧）；dsl 三编译期校验码＋api 7 处补 memory_repo＋前端 nodeCatalog/图设置面板/serializer 顶层 snake_case roundtrip/4 码 i18n/SSE 三新帧。门：后端全量 **2610/173/0**（test_al_package 17 例 U1345–U1354）、前端 vitest 全过（serializer 13 例含 roundtrip）、oxlint/tsc/build 过。D63 解除；遗留 pg_table 未接线＋审批持久化中断（D20/D21 族）。详见顶部流水条。
