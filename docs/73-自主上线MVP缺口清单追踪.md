# Atlas 自主上线 MVP 缺口清单追踪（docs/73）

> **状态**：🟡 追踪中（2026-09-27 立项）。本文件是 `docs/72`（第四次项目级上线复审）的**配套活文档**——docs/72 给出「A 档 MVP 达成 / B 档（自主上线）未达成」的结论，本文把 B 档缺口拆成可追踪的明细项，随落码/落配置更新状态。docs/72 的判定结论与本文冲突时，以 docs/72 的结论为准；本文只 tracker 缺口，不改判定。
>
> **基线（docs/72，2026-09-27）**：A 档＝可演示 / 可陪同试用 MVP ✅ 达成；B 档＝产品核心完全可用的生产 MVP（自主上线）❌ 未达成。已闭合前提：S1–S8 全部 remediation、N1/N2/N4 代码侧闭合、单实例闭环可跑。本文只列「从 A 到 B 还差什么」。

## 0. 追踪口径

- **状态图例**：⬜ 未启动 · 🟡 进行中 · ✅ 完成 · ⛔ 阻塞（附阻塞原因）。
- **证据级**（同 docs/63 §0 / docs/72 §0）：〔跑〕＝本仓库执行过命令；〔码〕＝逐行读码确认；〔勘〕＝子代理勘察未复。状态翻 ✅ 须带证据级。
- **当前门（实跑，2026-09-27）**：后端 `pytest` 1951 passed / 119 skipped / 0 failed；前端 `pnpm test` 738 passed / 2 skipped、`pnpm lint` 0 error / 7 既有 warning、`pnpm build` ✓。
- **刷新规则**：任一项翻 ✅ 时，在同一行补「证据」与「完成日期」，并在文末变更流水追加一行；不删除历史行。

## 1. 缺口总览（按工作流）

| 工作流 | 主题 | 明细项 | 状态汇总 |
|---|---|---|---|
| W1 | 真实外部凭据（硬阻断） | 1.1 真实 LLM 决策 / 1.2 真实 Shopify 退款 / 1.3 真实消息·IM·SMTP·SMS·OAuth | ⬜×3 |
| W2 | N3 范围决策（产品非工程） | 2.1 浏览器自动化进/出 MVP | ⬜ |
| W3 | prod 形态持久化与韧性 | 3.1 调度 store PG 化（D32）/ 3.2 D36 崩溃兜底批 / 3.3 多副本解锁（后置） | ✅×1（3.1 已完成，2026-09-27 更正）／⬜×1（3.2；3.3 后置不计入 B 档） |
| W4 | prod 演练（终门） | 4.1 `ATLAS_ENV=prod` 真机演练 | ⬜ |
| X | 横切前提 | X.1 CI postgres service / X.2 凭据卫生（N5）/ X.3 demo 模拟面 prod 残留闸门（2026-09-27 新增） | ✅×1（X.1 已在跑，2026-09-27 更正）／⬜×2（X.2、X.3） |

## 2. 明细追踪表

| ID | 工作流 | 动作 | 完成判据（Done-when） | 依赖 | 状态 | 证据 | 备注 |
|---|---|---|---|---|---|---|---|
| 1.1 | W1 | 配 `LITELLM_MODEL` + 商业 API key 入 prod 秘钥；`ATLAS_ENV=prod` 时禁用 demo/mock LLM 兜底 | 一个 `condition` 节点（docs/48）及任何 LLM 驱动的运行在 prod 下发出 ≥1 次真实 LiteLLM 调用；prod 不静默走 mock 兜底 | 商业 LLM 账号 + 成本/额度预算 | ⬜ | — | demo 当前靠兜底；prod 必须真决策 |
| 1.2 | W1 | 配真实/沙箱 Shopify 店铺；把 `channel:*` 工具（docs/67）指向真店；用测试订单跑通一次受控退款 | 携带 `order_id`/`amount` 的图打到**真实** Shopify 退款 API 并返回 2xx；docs/67 的「真适配器＋假 HTTP」由真 200 替换 | 真实/合作沙箱 Shopify 店铺；动（测试）款审批 | ⬜ | — | docs/67 以「真适配器＋假 HTTP」闭 N2，此处补全真外发 |
| 1.3 | W1 | 配真实 SMTP/IM webhook/SMS/OAuth 凭据；端到端验证发送路径 | 一条通知经 prod 配置真正投递到真实收件箱/IM 群（非 dev mock）；秘钥由 vault 注入，绝不明文 `.env` | 企业邮件/IM/SMS 供应商；OAuth 应用注册 | ⬜ | — | 见 docs/08 D 组外部通道 |
| 2.1 | W2 | 产品/用户拍板 docs/63 §0A N3（浏览器自动化适配器）是否进 MVP：进→立项批次（新适配器＋工具＋测试，新 D 号＋ADR）；出→显式标记 N3 出 MVP | 决策写入 docs/08 迭代计划 + docs/72 同步注记 | 产品范围会议 | ⬜ | — | 纯产品决策，非工程缺陷 |
| 3.1 | W3 | 把 `schedule_fires`/注册（docs/68 当前进程内）迁 Postgres，跨重启/实例共享 | 调度触发跨进程重启存活**且**第二实例不重复触发（PG 支撑 `ON CONFLICT`）；由对真 postgres 的集成测试覆盖 | CI postgres service（X.1） | ✅ **2026-09-27 更正：本项已完成**〔码〕 | `api/main.py:1059-1063` 在 `ATLAS_STORAGE_BACKEND=pg` 时装 `PgScheduleStore`（迁移 030 建 `schedules`/`schedule_fires`），出厂 `docker-compose.yml:50` 即 pg 档；跨重启存活与"第二实例不重复触发"由 `tests/test_scheduling_pg_integration.py` 5 例覆盖并在 CI `Backend PG integration` 每 PR 真跑 | 即 docs/14 D32；原记 ⬜ 属 docs/72 §1 同源误判，见 §6 |
| 3.2 | W3 | 立项 docs/62 §8 风险5 的 D36 批：单实例崩溃后 reconcile 挂起帧，保 at-most-once 续跑安全 | `kill -9` 挂起中途后重启⇒不重复执行；帧恢复或 fail-safe | 无（工程内可闭环） | ⬜ | — | docs/62 已登记 D36 |
| 3.3 | W3 | **后置**：仅当要「生产级」而非「单实例 MVP」时，再解 docs/62 单副本硬约束；需 D36＋所有挂起/事件/等待态共享 PG | 非 B 档阻塞；单实例 MVP 今日可交付 | 3.2 + 全态 PG 化 | ⬜（后置） | — | HA 显式后置阶段 |
| 4.1 | W4 | 真实凭据的 prod 全新部署；docs/66 引导口令 seed 管理员；首登强改密；跑通一条代表图（审批＋条件＋真 Shopify 退款＋IM 通知）至终态 | 真实基建上整链一次绿；把本次运行作为 〔跑〕 证据写入 docs/72 补记 | 1.1–1.3 全部 live | ⬜ | — | B 档终门 |
| X.1 | X | 启用 CI postgres service（docs/63 结构性缺口：当前 44/1918 integration 永不跑） | CI 中 integration 套件对真 postgres 实际执行；解锁 3.1 的集成证明 | 无（工程内） | ✅ **2026-09-27 更正：已在跑**〔码〕 | `.github/workflows/ci.yml:58-90` 的 `Backend PG integration` job 设 `ATLAS_RUN_INTEGRATION=1` ＋ `DATABASE_URL`，先 `python -m scripts.ops.apply_migrations` 再 `pytest`（打包 J P1-1 落码）；原记 ⬜ 是把 docs/63 当时的结构性缺口当成了现状 | 备注：本项翻 ✅ 直接解锁 3.1 的集成证明 |
| X.2 | X | 凭据卫生：核 N5（`.env` example）已满足，且无明文 prod 凭据入库 | 仓库无明文 prod 秘钥；prod 走 vault/环境变量注入 | 无（工程内） | ⬜ | — | 见 docs/63 §0A N5 |
| X.3 | X | **demo 模拟面 prod 残留闸门收口**（2026-09-27 第五次复审 docs/74 新增）：`_demo_mock_enabled()` 目前只守 3 条 shopify-admin mock 路由（`main.py:4166/4180/4204`），prod 档仍有 5 条匿名/固定口令可达——`POST /api/demo/mock/orders/{order_id}/receipt`（`main.py:4148-4151`，**无鉴权且回显请求体**）、`GET /api/demo/mock/orders`（固定公开串 `X-Demo-Token: demo-token`）、`POST /api/demo/shop/login` ＋ `GET /api/demo/shop/orders`（demo/demo）、`GET /demo/shop` | `ATLAS_ENV=prod` 下逐条真发请求 ⇒ 全部 404；并有一条反向测证明"去掉门就红" | 无（工程内，量小） | ⬜ | — | docs/63 §0A 曾更正 S5 只守 3 条；docs/72 §2 又收回成"全部 remediation"，故本文把它补成必答项 |

## 3. 退出判据（B 档达成）

全部满足时，docs/72 的 B 档判定翻 ✅ 达成：

- [ ] 1.1 真实 LLM 决策 live 且已演练
- [ ] 1.2 真实 Shopify 退款 live 且已演练
- [ ] 1.3 真实消息/IM/SMTP/SMS/OAuth 至少主通道 live 且已演练
- [ ] 2.1 N3 范围决策已记录
- [x] ~~3.1 调度 store PG 化完成~~ **✅ 2026-09-27 更正：立项时已完成**（`api/main.py:1059-1063`＋迁移 030＋compose pg 档；集成测试 5 例在 CI 每 PR 真跑）
- [ ] X.3 demo 模拟面 prod 残留闸门收口（2026-09-27 第五次复审新增，见 docs/74 §3-A）
- [ ] 4.1 `ATLAS_ENV=prod` 真机演练整链绿

（3.2 / 3.3 / X.1 / X.2 为韧性/前置增强：3.2 强烈建议随 B 档同批；3.3 与 X 类为后置或横切，不单独阻断 B 档判定。）

## 4. 同步矩阵

- 本文是 docs/72 的配套追踪器；docs/72 结论权威，本文只 tracker，不改判定。
- 关联：docs/72（B 档判定与缺口总述）、docs/63（评审框架 / S1–S8 / N 系列门 / CI postgres 缺口）、docs/08（B/C 组筛查 + D 组外部通道 + 2.1 决策落点）、docs/62（单副本护栏 / D36）、docs/66（N1 prod 引导口令）、docs/67（N2 渠道通道）、docs/68（N4 调度 / D32）、docs/48（LLM 条件节点）、docs/14（缓做项 D13/D20/D25/D28/D32/D35/D36）。
- 不替代 docs/72；本文状态翻 ✅ 时反向在 docs/72 补记 〔跑〕 证据。

## 5. 变更流水

| 日期 | 动作 | 说明 |
|---|---|---|
| 2026-09-27 | 立项 | 据 docs/72 结论拆 B 档缺口为 10 个明细追踪项（1.1–1.3 / 2.1 / 3.1–3.3 / 4.1 / X.1–X.2），全部 ⬜ 起追踪 |

## 6. 更正注记（2026-09-27，第五次复审 docs/74 带来）

本文首版有两条状态是**沿 docs/72 叙述写的、没回代码核**，本次据 〔码〕 更正（历史行不删，按上文"刷新规则"就地补证）：

1. **3.1「调度 store PG 化」不是待做，是已完成**——`api/main.py:1059-1063` 按 `ATLAS_STORAGE_BACKEND=pg` 装 `PgScheduleStore`（表＝迁移 030 的 `schedules`/`schedule_fires`），出厂 `docker-compose.yml:50` 就是 pg 档；跨重启存活与"第二实例不重复触发同一分钟槽"由 `tests/test_scheduling_pg_integration.py` 5 例钉住。
2. **X.1「CI postgres service」不是待做，是已在跑**——`ci.yml:58-90` 的 `Backend PG integration` job 已设 `ATLAS_RUN_INTEGRATION=1`＋`DATABASE_URL`，先 apply 迁移再 `pytest`。首版那句"44/1918 integration 永不跑"是 docs/63 当时（打包 J 之前）的结构性缺口，被当成现状抄了过来。

同时**新增一项必答**：X.3 demo 模拟面 prod 残留闸门（prod 档仍 5 条匿名/固定口令可达，含一条无鉴权且回显请求体的 POST）。这条的存在也说明本文 §3 的"退出判据"不能只看表格首版——**tracker 的每一项翻状态要回代码，不能沿评审叙述抄**。

| 日期 | 动作 | 说明 |
|---|---|---|
| 2026-09-27 | 更正＋新增 | 第五次复审（docs/74）驱动：3.1 与 X.1 翻 ✅（附 〔码〕 证据与 file:line）；新增 X.3 demo 面 prod 残留闸门；总览两行与 §3 退出判据同步 |
