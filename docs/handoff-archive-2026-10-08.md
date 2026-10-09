# Handoff Archive — 2026-10-08

> 2026-10-09 从 handoff.md「Recently shipped」滚出（保持最近 5 条上限；逐字搬移、原文不改写）。

## 打包 A4 落码收口

✅ **打包 A4 落码收口＝D20 动态审批人与审批指派校验（2026-10-08，五原子 `d2d5e2a`→`7ebd2c9`；docs/100 §7 注记；后端 2436/166/0、前端 820/2、oxlint 0/0、build 过；A 档四打包 A1–A4 全部收口）**：approver `{{变量}}` 插值两翼（编译 422 `APPROVER_REF_UNRESOLVED`＋运行期残留→空串＋告警）＋决策端点指派校验（`user:<username>` 匹配 principal、邮箱恒 403 因 iam 无 email、空 approver 任何人可决）＋前端表单提示与错误码 i18n（U1205）；两处偏差照实见 docs/100 §7；零迁移、无 ADR。〔**2026-10-08 接力追加**：本批自报缺的**浏览器冒烟已补**——一次性卷的独占 compose 项目＋真 Chromium，拖入「人机协作」后节点数 3→4、属性面板渲染出 A4 新提示，10 项全 PASS；脚本 `scripts/dev/approver_hint_smoke.py`＋截图 `docs/screenshots/a4-approver-hint.png`，证据细节见 docs/100 §7 追加段〕
