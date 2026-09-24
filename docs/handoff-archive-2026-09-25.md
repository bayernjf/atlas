# Handoff 归档 — 2026-09-25

> 主文件「Recently shipped」5 条上限滚动下来的条目，保留**滚出前的原编号**。同批把主文件中 `Recently shipped N` 数字引用统一改为按批次命名，避免再因滚动漂移。

## 滚出自 Recently shipped 的条目

5. ✅ **打包 E 收口（docs/58，2026-09-24，dev 已 push；后端、零迁移）**：E-1 webhook 出站 HMAC-SHA256 签名（X-Atlas-Timestamp/X-Atlas-Signature，基串 {ts}\n{raw}）；E-2 IM markdown 富文本与 @人（钉钉/企微/飞书三家官方报文，mentions 归一化）；E-3 webhook/IM 多 URL 群发（1-20、per-URL 记录、EGRESS fail-fast、全成才写 _messages）。门：后端 1713/69/0、im smoke 27/27。
