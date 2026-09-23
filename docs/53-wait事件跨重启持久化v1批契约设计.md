# wait 事件等待跨重启持久化 v1 批契约设计

> **状态**：2026-09-23 docs-only 立项；D19 再次部分取回、**不解除**（多实例/进程停摆期信号仍缓做）。
> **形状权威**：本文件。落码后在各引用文档回填 commit hash 与收口证据。
> **依据**：docs/47（wait 事件等待进程内 v1，已落码）、docs/24（中断帧契约 + ADR T18-B：自研暂停帧 + 恢复扫描器）、docs/14 D19。
> **零新依赖、零迁移（复用 `interruptions.payload` JSONB）、无新 ADR、无新 REST 端点、前端零改动。**

## 0. 已核实的现状缺口（2026-09-23 对活代码）

1. `graph/loader.py` wait 节点 `waitType=event` 分支（约 407-470 行）：`EventWaitBroker.request()` 后直接阻塞 `wait()`，**全程不调用 frame_sink**——`interruptions` 无帧、`run_store.suspend` 未触发（runs 行停在 running）。
2. `collaboration/event_waits.py` `EventWaitBroker`：**无 `restore()`**；模块 docstring 自述「进程内、重启即失，不写中断帧、不支持多实例」。
3. event 分支**无 `resume_here` 处理**：续跑线程进入尾图的 wait 节点时会重新 `request()` 产生新 token、超时全额重计；对原 token 的信号永远丢失。
4. `api/main.py` `_resume_from_frame()` 只对 approval 帧做 broker restore；`kind=="wait"` 帧无差别只起续跑线程（duration 等待的正确行为，event 等待则缺注册）。

对照：duration 等待（static/dynamic/absolute）自 M5b 起已写 `kind=="wait"` 帧并按 `remaining_seconds(deadline_at)` 续等；human_approval 已写帧 + `ApprovalBroker.restore()`。本批是把同一套 T18-B 机制补齐到 event 等待。

## 1. 范围与非目标

**范围（单实例 PG 后端，重启语义）**：

- event 挂起即落中断帧（含 eventKey/onTimeout/timeoutSeconds），runs 行同步 suspended。
- 服务启动恢复：同 token、同 event_key 在租户 `EventWaitBroker` 重新注册，超时只计剩余（绝对 deadline，跨重启照扣）。
- 重启后 `GET /api/waits` 可见该 pending；`POST /api/waits/events`（按 key 广播）与 `POST /api/waits/{token}/signal`（直投）能释放续跑线程，run 走完尾图、清帧、落终态。
- 超时语义不变：剩余超时到点 → `onTimeout=continue` 输出 `resolvedBy=timeout` 续走；`onTimeout=fail` run failed `WAIT_TIMEOUT_FAILED`。
- 进程内后端（无 frame_sink）行为与 docs/47 完全一致：易失、不写帧。

**非目标（继续缓做，见 §6 同步）**：

- **多实例/跨进程信号路由**：broker 仍是进程内 dict + threading.Event；多实例下信号不跨进程投递、无 PG 轮询/PG advisory 机制。随 D19 余部。
- **进程停摆期间的信号不排队**：没有持久化信号队列；信号必须命中存活进程（单实例重启窗口内调用方收到连接失败，需调用方重试——本批不代缓存）。
- **debug 暂停跨重启**（D27）：DebugSession 仍不发帧、不 restore。
- **公开免登录信号口、event timeoutSeconds 表达式化、多事件竞速取消**：仍随 D19 缓做。

## 2. 中断帧扩展（Schema 契约）

`interruption_frame`（docs/03 §interruption_frame，承载 `storage/frame.py`）新增**可选顶层字段**：

```json
{
  "resume_token": "wait-<hex>",
  "kind": "wait",
  "deadline_at": "<UTC ISO 绝对时刻>",
  "wait": {
    "waitType": "event",
    "eventKey": "order:123:paid",
    "onTimeout": "continue",
    "timeoutSeconds": 3600
  },
  "resume_state": {"graph_id": "...", "inputs": {...}, "outputs": {...}},
  "graph_snapshot": {...}
}
```

- 判别式：`kind=="wait"` **且** `wait.waitType=="event"` = 事件等待帧；duration 帧（static/dynamic/absolute）不带 `wait` 键。
- `resume_token` 与 broker token 一致（`wait-`+uuid4.hex），故直投信号按 token 路由无需额外映射。
- `deadline_at` 为挂起时刻 + timeoutSeconds 的绝对 UTC；恢复注册时 `timeout_seconds = max(0, remaining_seconds(deadline_at))`。
- **零 DDL**：新字段仅存在于 `interruptions.payload` JSONB；列 `kind/deadline_at` 语义不变。
- `build_frame()` 增加可选参数 `wait: dict | None = None`；不提供时帧形状与现状逐字段一致。

## 3. 运行时改动

### 3.1 EventWaitBroker.restore()（新方法）

```python
def restore(self, *, token: str, event_key: str, node_id: str,
            graph_id: str, timeout_seconds: float) -> None
```

- 以帧数据重建 `_Pending`（新 threading.Event、monotonic deadline = now + timeout_seconds、timeout_seconds 取剩余值、payload 为空），并入 `_pending` 与 `_by_key`。
- **幂等**：token 已存在时 no-op（重复启动/重复恢复不重建、不重置已发信号）。
- restore 的 pending 与正常 request 的 pending 在 wait/signal/list_pending 路径上无差别。

### 3.2 loader event 分支

- **正常执行**：`request()` 拿到 token 后、第二个 node_start（wait 载荷）发出前后、阻塞 `wait()` 前，调用扩展后的 `_emit_frame(..., token=token, kind="wait", wait={waitType:event, eventKey, onTimeout, timeoutSeconds})`。嵌套 sink（`api/main.py _frame_sink_for`）随之执行 `run_store.suspend` + PG upsert。
- **resume_here**（帧续跑）：跳过 preset 判定与 `request()`；token 取 `resume["resume_token"]`，eventKey/onTimeout/timeoutSeconds 取 `resume["wait"]`；发第二个 node_start 后直接 `event_wait_broker.wait(token, is_cancelled=...)`；信号/超时/失败的 outputs 与 message 复用现有分支（436-470 行）。
- 防御：`resume_here` 但帧缺 `wait` 字段 → 抛 `WaitNodeFailure(node.id, "WAIT_EVENT_FRAME_INVALID", ...)`，run failed，不静默重发 request。

### 3.3 启动恢复（api/main.py）

`_resume_from_frame(engine, frame)` 增加分支：`kind=="wait"` 且 `frame.get("wait",{}).get("waitType")=="event"` 时：

1. 取该租户 `TenantServices`（经现有 per-tenant registry）；
2. `event_wait_broker.restore(token=..., event_key=frame["wait"]["eventKey"], node_id=frame["node_id"], graph_id=frame["resume_state"]["graph_id"], timeout_seconds=remaining_seconds(frame["deadline_at"]))`；
3. 随后与现状一样起 daemon `_resume_run`（`run_graph(..., resume=frame)`）。

其余帧（approval：restore + daemon；duration wait：仅 daemon）行为不变。恢复失败仍按帧隔离、warning 不阻塞。run 终态后 `clear_frame` 沿用现有逻辑。

## 4. REST / 前端

- **无新端点**。docs/12 在 `/api/waits` 三行补跨重启可用语义：恢复后的 pending 同样可 list/广播/直投；信号在重启窗口内无进程接收时不持久化（调用方重试）。
- **前端零改动**：wait 列表轮询与运行态投影已按现有 token/状态工作。

## 5. 测试（候选编号，落码后转正式并回填 docs/13）

- **U400 broker restore 纯逻辑**：restore 后 list_pending 命中；signal_key 广播与 signal_token 直投释放 wait；超时只计剩余；重复 restore 幂等不覆盖已发信号。
- **U401 loader event 帧与续跑**：正常挂起 sink 收到带 `wait` 字段、kind=wait、token 同 broker token 的帧；构造帧 + restored broker 经 `run_graph(resume=frame)` 尾图运行，信号释放后 outputs `signaled=true/resolvedBy=signal`；onTimeout=fail 剩余超时到点 run failed `WAIT_TIMEOUT_FAILED`；continue 到点 `resolvedBy=timeout`；deadline 已过（remaining≤0）立即按超时路径走；缺 wait 字段 `WAIT_EVENT_FRAME_INVALID`。
- **U402 PG 恢复集成**（PG 直连，非 TestClient）：插 runs(suspended) + interruptions event 帧 → 调用恢复装配 → broker 可见 token → signal_key → 续跑线程跑完 → runs completed、帧已清。
- **HTTP 冒烟**（`.smoke/`，收口证据）：建 event wait 图并运行至挂起 → 重启 uvicorn → `GET /api/waits` 仍见 token → 直投信号 → run completed；另测广播与 onTimeout=fail 重启后超时各一条。

## 6. 契约同步矩阵（立项原子内完成）

| 文档 | 改动 |
|---|---|
| docs/03 | `interruption_frame` 字段概览补可选 `wait{waitType,eventKey,onTimeout,timeoutSeconds}` 与判别语义 |
| docs/04 §5.5 | event 等待段补「跨重启持久化（PG 单实例）：帧 + restore + 剩余超时」收口块（立项先写形状，hash 收口回填） |
| docs/06 | 中断恢复运行时段补 event-wait 恢复分支说明 |
| docs/12 | `/api/waits` 三行补重启后可用/停摆期不排队语义（无新端点） |
| docs/13 | U400-U402 登记 + §9 收口回填 |
| docs/14 D19 | 追加 2026-09-23 event 跨重启部分取回注记；多实例/信号排队仍缓做，触发条件不变 |
| docs/00 | 文档地图新增 docs/53 行 |
| docs/08 | 新增立项 blockquote（零迁移/零依赖/无 ADR） |
| handoff.md / CHANGELOG.md | banner/Active work 新条目 + Unreleased 段 |

## 7. 原子提交序

1. `docs(wait): specify durable event-wait frames across restart contract`（本文件 + §6 全部同步）
2. `feat(collaboration): add restore to event wait broker`（U400）
3. `feat(graph): emit event-wait frames and resume on restored token`（U401）
4. `feat(api): restore event waiters before resuming recovered runs`（U402）
5. `chore(wait): add event wait restart smoke script` + `docs(wait): close out durable event wait v1`（门槛/冒烟/浏览器或 HTTP 证据回填）

全程 `.venv/bin/pytest`、`cd frontend && pnpm build` 防回归；不 push。
