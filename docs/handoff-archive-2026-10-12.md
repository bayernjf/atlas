# handoff 归档（2026-10-12）

> 自 handoff「Recently shipped」5 条上限滚下的条目，原文逐字保留，编号／批次名不改。

## 滚出条目

📝 **打包 AK 立项＝D35 记忆机制四切片（docs/122；2026-10-10，docs-only）**：auto_extract（LLM 结构化提取＋阈值 0.85＋source=auto_extract，新工具 memory/auto_extract，fail-closed 空结果）、ai_decision 注入开关（memoryInjection，缺省关闭）、update_policy（overwrite/version/ask）、recall decay（半衰期加权、now_override 确定、缺省关闭）。四决策全"默认值即现状"；零新端点／1 新错误码／无 ADR；U1332–U1344 预留。D35 不解除。
