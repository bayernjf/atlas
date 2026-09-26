# 同步急停句柄＋前端认 `superseded` 帧批（打包 O）v1 批契约设计

> **立项 2026-09-27**：两处工程内可闭环的既有缺陷，一个批次收口。形状权威＝本文。
>
> 零新依赖、**零迁移、零新 REST 端点、零新错误码**；解除缓做 **1** 项（14 D37），**不解除** D19/D20/D27/D31/D32，单副本三道闸一条不撤（本批是"单实例下取消句柄覆盖面的亚型"，不是并发扩容）。

## 0. 已核实缺陷（〔码〕＋〔跑〕）

### O-1 同步 `/run` 在途不可急停，与 worker 数无关

- `POST /api/graphs/{id}/run`（`api/main.py:2974 run_saved_graph`）**不**调 `cancellation_broker.register`、也不给 `run_graph` 传 `is_cancelled`〔码〕；注册只在流式路径 `main.py:3115-3116`，注销在 worker 的 `finally`（`main.py:3252`）。
- 于是 `POST /api/runs/{id}/cancel`（`main.py:3352`）对同步运行恒走"无句柄且 run 存在"分支 ⇒ **409「运行已结束，无法取消」**，而 run 其实正在跑〔码〕。这是与多副本无关的既有缺口。
- 登记在 docs/34 复审更新注记（`:70`，"同步运行从不登记句柄"）与 docs/14（`:133`/`:140` 两条同义注记，未占 D 编号）。

### O-2 前端不认 `superseded` 终帧，把"让位"报成"流坏了"

- 流式路径在认领失败时发 `event: superseded`（`main.py:3288-3301`，载荷 `{type:"superseded", node_id, reason:"resumed_by_other_process"}`）后关流，**不发 result/error**。
- 前端 `frontend/src` 全仓 grep `superseded` **0 命中**〔跑〕：`apiClient.ts:1003-1005` 把它掉进 else 当普通事件转发，循环末 `throw new Error('SSE 流缺少最终运行结果')`（`:1010`）⇒ 用户看到"流坏了"，实际是"本进程已让位、终态归赢家"。
- 登记为 14 **D37**（`:46`）、docs/62 §6 原子⑨（`:142`）与 §8 残余风险第 5 条（`:157`）。

## 1. 决策与形状

### D-1 同步运行在途被急停 ⇒ **HTTP 200 + `status="cancelled"`**（用户已拍板）

`RunGraphResponse(id=graph_id, status="cancelled", outputs={}, trace=[f"{node_id}: cancelled by user"])`；
`run_store.finish(run_id, status="cancelled")` ＋ `monitoring.record_run(..., mode="sync", status="cancelled", ...)`，**不调** `evaluate_after_run`。

**为什么不是另两种**：
- 非 200 / 新错误码：会新增错误码并让现有同步 `/run` 调用方的形状分叉，而"用户主动取消"在流式里本就是正常终止（`main.py:3203-3220`），两条路径没必要给出两种语义；
- 200 + `status="failed"` 并进门控：把用户主动操作算成失败率，会污染 M9 灰度门控的自动回滚判据（流式 cancelled 明确"不进灰度门控、不算失败"，同步若分叉即两条口径自相矛盾）。

**承重细节**：
- `except RunCancelled` 必须排在 `except Exception`（`main.py:3032`）**之前**——`RunCancelled` 是 `Exception` 子类，排错即被吞成 500；
- 句柄在**请求线程**注册（照流式 `:3114` 的注释理由：保证端点可达时句柄已在），`finally: unregister` 覆盖 completed/error/superseded/cancelled 四条出口——少了它，`tests/test_run_cancel.py:486` 的"已结束 409"断言会翻成 200，是**必须守住的不变量**；
- `nodes=[]`：同步路径没有逐节点收集器（只收 `tool_metric`），取消时拿不到部分产出，与现有异常路径（`:3036`）同形；
- 取消在 wait/approval/tool 阻塞中点**不强杀**，下一节点边界才生效（04 §5.x 既有语义，本批不改）。

### D-2 前端把 `superseded` 当成与 `cancelled`/`stopped` 并列的正常终帧

- `apiClient.ts`：新增 `SupersededFrame` 类型并入 `RunEvent` 联合；新增 `RunSupersededError`（照 `RunCancelledError` 形状，`nodeId` 字段）；`streamRun` 认 `payload.type === 'superseded'` 转发给 `onEvent`，循环末在 `!result` 兜底**之前**抛 `RunSupersededError`。
- `Editor.tsx`：事件分支补 `else if (event.type === 'superseded') { setPausedFrame(null) }`；catch 链补 `else if (error instanceof RunSupersededError) appendLog(t('log.superseded', { node: error.nodeId }))`——**不** `setRunError`（不是失败，不该弹错误 toast）。
- i18n：`zh-CN`/`en-US` 的 `editor.json` 在 `log.cancelled` 后各加 `"superseded"` 一键（两档同时落，PARITY 三条守护自动覆盖键集/零汉字/插值）；`i18n.test.ts` 的 `EDITOR_TEMPLATE_KEYS` 补该项。

**为什么不改后端帧形状**：`superseded` 帧已是契约（docs/62 §3、04 §5.6 追加段），改它要同步 03/04/12 与前端，而缺陷纯粹在消费侧。

### 被否 / 非目标

- 不把 superseded 接进录制用例状态（`Editor.tsx:513` 的 `executed.status` 路径不动；superseded 抛错后本就不落录制）；
- 不改 `log.stopped`/`log.cancelled` 现文案、不动流式路径任何一行、不给 `RunGraphResponse.status` 加枚举；
- superseded 后 run 库仍为 suspended（终态归赢家）、前端不轮询补正 —— 属 **D36**，本批不做。

## 2. 测试（U895 起；只许增测）

后端 `tests/test_run_cancel.py` **3** 例（复用现成的 `_wait_graph()` / `_find_run_id` / `TIMEOUT=5`）：

- U895 在途取消同步运行→200 且形状为 cancelled：线程 POST `/run` → 轮询 runId → `sleep(0.4)` → cancel 200（重复幂等仍 200）→ join；断言响应 `status=="cancelled"`、`outputs=={}`、trace 末条含 `cancelled by user`，且 `GET /api/runs/{run_id}` 为 cancelled。
- U896 取消的同步运行**不进**灰度门控：monkeypatch `atlas.api.main.evaluate_after_run` 计数 ⇒ `call_count==0`（对照组：正常完成路径为 1）。
- U897 同步运行落定后再 cancel ⇒ **409**（`finally: unregister` 的常驻守护）。

前端 `frontend/src/lib/__tests__/apiClient.test.ts` **2** 例（照 `:96-112` cancelled 用例风格）：

- U898 `superseded` 终帧转发并以 `RunSupersededError` 拒绝，`error.nodeId==='tool-1'`，事件序列 `[node_start, superseded]`。
- U899 拒绝的 message **不含**「SSE 流缺少最终运行结果」——这条正是原缺陷文案，含之即红。

## 3. 反向门（收口前临时植入、验毕还原，不提交）

| 门 | 植入缺陷 | 必须转红 |
|---|---|---|
| G1 | 删 `is_cancelled=cancel_event.is_set` | U895 跑满 wait 返回 `completed` |
| G2 | 删 `finally: unregister` | `test_run_cancel.py:486` 由 409 变 200 ＋ U897 红 |
| G3 | `except RunCancelled` 移到 `except Exception` 之后 | HTTP 500 ＋ U896 门控计数变 1 |
| G4 | 删前端 superseded 分支 | U898/U899 红（落回误导文案） |
| G5 | 只加 zh 不加 en | `i18n.test.ts` PARITY「identical leaf-key sets」红 |

## 4. 契约同步矩阵（收口回填）

docs/12（sync `/run` 与 `/api/runs/{id}/cancel` 两行补"在途可取消、返回 200 + status=cancelled"；`:932` 那句"前端会落到误导文案/登记为原子⑨"改写为已闭合）、docs/13（U895–U899 登记行）、docs/14（**D37 划销**＋`:133`/`:140` 两条注记标已修）、docs/62（§6 原子⑨、§8 残余风险第 5 条标已闭合）、docs/34（`:70` 缺口注记标已修并指向本文）、docs/08（打包 O 立项条＋收口条）、docs/00（文档地图补行）、handoff（Project documents 区＋Recently shipped＋Quality gate）、CHANGELOG。

## 5. 残余风险

1. **取消窗口仍是"下一节点边界"**：长 wait / 慢工具的阻塞中点不强杀，用户点了取消可能要等若干秒才生效（既有语义，本批不改）。
2. **同步运行的取消依赖另一条请求线程**：本批靠 TestClient 线程并发证明；若将来把同步端点改成 `async def` 且内部阻塞，句柄注册顺序要重新验一次。
3. **superseded 的 UI 面只能手工验**：`frontend/src` 无组件级测试，Editor 的 `log.superseded` 分支不进自动化门，收口时以浏览器实录为准（没有则照实写"未验"）。
4. **多副本仍未解锁**：本批让单实例下的取消覆盖到同步入口，但 D19/D20/D27/D31/D32 与单副本三道闸一条不撤；superseded 之后的"卡住帧人工重放"仍属 D36。

## 6. 落码收口注记

> **已收口（2026-09-27）**。commit 链：`efd8081` fix(api) 同步 `/run` 注册取消句柄＋`except RunCancelled` 前置＋`finally unregister`；`b36d418` test(api) U895–U897；`485cb88` feat(web) 前端认 superseded 帧＋`log.superseded` 两档＋`i18n.test.ts`；docs 原子随收口补。
> 门（先跑后写，数字取实跑）：`pytest` **1942 passed / 119 skipped / 0 failed**（基线 1939/119，净增 3＝U895–U897）；前端 `pnpm test` **738 passed / 2 skipped / 54 文件**（基线 736/2，净增 2＝U898/U899）、`pnpm lint` **0 error / 7 warning**（warning 基线既有）、`pnpm build` ✓。
> **G1–G5 反向门实跑结果**：G1 删 `is_cancelled`⇒U895 跑满 wait 返 `completed` 红；G2 删 `finally: unregister`⇒U897 由 409 变 200 红；G3 `except RunCancelled` 移到 `except Exception` 之后⇒HTTP 500＋U896 门控计数变 1 红；G4 删前端 superseded 分支⇒U898/U899 落回误导文案红；G5 只加 zh 不加 en⇒i18n PARITY「identical leaf-key sets」红。五门均确认红→还原绿。
> **与本文的形状偏差**：`apiClient.test.ts` 首版误把 `await expect(...).rejects.toBeInstanceOf(...)` 当返回错误实例来取 `.message`/`.nodeId`，其实该式返回 void——U898 改用 try/catch 取 `caught`（与 U899 同构）。`Editor` 的 `log.superseded` 分支无组件级测试（项目无组件渲染测试基线），浏览器实测未做，照实记"未验"。
