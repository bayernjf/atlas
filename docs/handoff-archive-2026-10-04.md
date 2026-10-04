# Handoff 归档 — 2026-10-04

本文件归档从主 `handoff.md`「Recently shipped（只留最近 5 条）」滚出的完成项，按时间倒序。当前状态与活跃待办仍以主 handoff 为准。

## 滚出条目

✅ **打包 ZO 落码收口＝prod 凭据注入：文件型密钥 `<NAME>_FILE`（承接 [docs/73](73-自主上线MVP缺口清单追踪.md) 1.1 残余的工程半边，2026-10-03；契约 docs/08 打包 ZO 立项块，`0e1c7421` 立项；自推）**：`read_secret()` 让每把密钥多一条**文件**注入路径（docker secret／vault 渲染落盘），`_FILE` 读不到即缺失且**绝不回退 env**；五个读取点（启动门两把＋引导口令＋邮件签名＋OAuth state＋信封主密钥）全走它，LLM key 因 litellm 自读 env 用 `hydrate_file_secrets()` 补水且不覆盖 env。门：后端 **2236/136/0**（净增 7）、守护门 58、真进程探测五段全过、两条反向门植缺陷转红后还原；docs/08/13/15/73/.env.example/CHANGELOG 回填。**B 档判定不变**，1.1 残余收窄为「凭据本体」，4.1 仍 ⬜。
