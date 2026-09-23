# wait 时长放开 / jitter / 多事件竞速 + 告警恢复通知 / lifecycle 限流 v1 批契约设计

> 状态：2026-09-23 docs-only 立项（D19 wait 余部 + D28 告警 lifecycle 余部，部分取回、**不解除缓做条目**；零新依赖 / 零迁移 / 无 ADR）。
> 上游契约：04 §5.5（wait 节点）、docs/47（event 等待进程内）、docs/49（动态时长）、docs/50（到点时刻）、docs/53（跨重启持久化）、docs/52（外部通知 v1）、docs/33 §5（静默/升级/值班）。
> 缓做依据：docs/14 D19「>3600 长时刻、jitter、多事件竞速取消、多实例跨进程路由仍缓做」、D28 B1 注记「PG 档 lifecycle、恢复（recovery）通知、lifecycle 限流退避仍缓做」。

## 0. 已核实的现状缺口（2026-09-23 对活代码）

- `graph/dsl.py`：`MAX_WAIT_SECONDS=600`（duration static）、`MAX_EVENT_WAIT_SECONDS=3600`（event）；`_validate_wait_config`（L725）无 jitter、无多事件字段。
- `graph/loader.py`：duration static/dynamic/absolute 运行时统一卡 1-600（`_resolve_wait_expression` 传 `MAX_WAIT_SECONDS`，`_resolve_absolute_wait` L131 硬编码 `1 <= round(delta) <= 600`）；event 卡 1-3600；duration 整段 `time.sleep(seconds)`（L606），无抖动；event 单 `eventKey` 经 `broker.request(event_key=...)`。
- `collaboration/event_waits.py`：`_Pending.event_key` 单键；`request(event_key=...)`、`restore(event_key=...)`、`signal_key` 广播、`wait` 返回 payload；`_by_key[event_key]->set(token)`。
- 跨重启帧（loader `_emit_frame kind="wait"`）：`wait={waitType,eventKey,onTimeout,timeoutSeconds}`；`api/main.py` L184 `restore(event_key=wait_payload["eventKey"], ...)`。
- `monitoring/records.py`：`record_run` 仅在异常时 `_raise_or_merge`（new/merged）；**健康运行不产生任何状态变化**，无自动恢复；`resolve_alert` 仅手动。
- `monitoring/notify.py`：`LIFECYCLE_TITLES={merged,escalated,resolved}`；`notify_lifecycle` 每次调用都投递，**无节流**，merged 高频可风暴。
- Alert 状态机：`open | acknowledged | resolved`；`rollout_gate` 为发布门禁专用 rule_id；Alert 无 resolved_at 字段（PG 档不为此加列）。

## 1. 范围与非目标

本批五小项，全部进程内、零新依赖、零迁移、零新 REST 端点：

1. **时长上限放开**：duration 600→3600；event 超时 3600→86400（24h）。
2. **duration jitter 抖动**：可选 `jitterSeconds`（0-300），实际等待叠加均匀随机抖动。
3. **event 多事件竞速**：可选 `eventKeys`（1-8），任一信号命中即继续并清理其余订阅。
4. **告警自动恢复（recovery）通知**：健康运行自动 resolve 该图 open 内置告警并发 recovery 通知。
5. **lifecycle 限流退避**：同告警同 lifecycle 转换默认 60s 内不重复投递。

**非目标（仍缓做，不解除 D19/D28）**：

- 多实例跨进程事件路由、停摆期信号排队、公开免登录信号口（留 D5/S1）。
- jitter 的分布选择（仅均匀 randint）、event 超时 jitter、周期时刻（cron）。
- 多事件 AND（全部命中）语义、按 key 分流不同出边/不同 payload 通道。
- 告警 PG 档 lifecycle 字段（escalated_at/assignee/recovery 持久化随告警 PG 化）、OTel、规则模板市场。
- recovery 的连续 N 次健康判定、flapping 抑制（v1 一次健康即恢复）。

## 2. 时长上限放开

| 常量 | 旧 | 新 | 适用 |
|---|---|---|---|
| `MAX_WAIT_SECONDS` | 600 | **3600** | duration static/dynamic/absolute |
| `MAX_EVENT_WAIT_SECONDS` | 3600 | **86400** | event static/expression 超时 |

- 依据：duration 为同步 `time.sleep` 占用 Starlette 工作线程，1h 为可接受上限，更长等待应改用可中断/可跨重启的 event；event 经 broker 0.2s 切片轮询，支持协作取消与 docs/53 跨重启恢复，长挂起 24h 安全。
- 同步面：`dsl._validate_wait_config`（区间随常量）、`loader._resolve_wait_expression`（hi 随常量）、`loader._resolve_absolute_wait`（硬编码 600 改 `MAX_WAIT_SECONDS`，错误消息随常量）、前端 `l1.ts` 常量与提示文案、`wait.schema.ts` maximum、`WaitConfig.tsx` InputNumber max 与提示。
- 旧图零回归：仅放宽区间，不收紧任何既有合法值。

## 3. duration jitter 抖动

- DSL：duration config 新增可选 `jitterSeconds`：整数、`0 <= jitterSeconds <= MAX_JITTER_SECONDS(=300)`，缺省 0；bool 拒绝；仅 waitType=duration（三模式通用）。event 配置出现 jitterSeconds 不报 DSL 错但忽略（严格起见：DSL 对 event 下的 jitterSeconds 不加校验，loader 不读取）。
- 运行时（loader duration 分支）：
  - `planned = seconds`（static/dynamic/absolute 解析所得）；`jitter = int(config.get("jitterSeconds", 0))`；
  - `actual = planned + rng.randint(0, jitter)`（jitter=0 时 actual=planned，确定性不变）；
  - 中断帧 `timeout_seconds=actual`（deadline 含抖动，续跑按剩余，不二次抖动）；`time.sleep(max(actual,0))`；
  - output：`{mode:"wait", waitType:"duration", durationSeconds:actual, plannedDurationSeconds:planned, jitterSeconds:jitter}`（dynamic/absolute 原有 mode/expression/absoluteTime 字段保留）。
- 随机源可注入：内部执行函数新增参数 `jitter_rng: random.Random | None = None`，None 时用模块级 `random.randint`；测试传入 `random.Random(seed)` 保证确定性。`run_graph`/`compile_and_run` 透传，默认 None（REST 路径真随机）。
- event 超时不支持 jitter：超时是保护性边界，随机延后无业务意义。

## 4. event 多事件竞速

### 4.1 DSL

- event config 二选一：`eventKey`（string，现有）**或** `eventKeys`（array，1-8 个非空字符串）；二者互斥、至少其一。
- `eventKeys` 每个元素沿用 eventKey 规则：长度 ≤ `MAX_EVENT_KEY_LENGTH`、静态部分过 `EVENT_KEY_STATIC_RE`；数组元素去重校验（重复静态部分报问题）。
- 新增常量 `MAX_EVENT_KEYS = 8`。
- 兼容：仅 eventKey 的旧图行为不变。

### 4.2 broker（event_waits.py）

- `_Pending` 增 `event_keys: list[str]`（保留 `event_key` 只读属性 = `event_keys[0]`，供 list_pending/日志向后兼容）。
- 新方法 `request_any(*, event_keys, node_id, graph_id, timeout_seconds) -> token`：登记一个 pending，在 `_by_key` 的**每个** key 上挂同一 token。
- `request(event_key=...)` 保留为 `request_any(event_keys=[event_key], ...)` 的薄封装。
- `signal_key(key, payload)`：命中 pending 时，若 payload 未带 `matchedEventKey`，写入 `matchedEventKey=key`，再 set event（首决生效，其余 key 信号不覆盖）。
- `wait` 返回 payload（含 matchedEventKey）；唤醒/超时/取消的 `_remove_locked` 遍历 `event_keys` 逐个摘除并清空空 set。
- `restore` 增 `event_keys: list[str] | None = None`（与 event_key 二选一，内部归一为列表）；帧恢复多键。
- `list_pending` 每项增 `eventKeys: list[str]`（eventKey 保留为首键）。

### 4.3 loader / 帧 / 输出

- event 分支：解析 `keys`——eventKeys 存在则逐个 `interpolate` + `valid_event_key`（任一非法 `WAIT_EVENT_KEY_INVALID`），否则单键；调 `request_any(event_keys=keys)`。
- 中断帧 `wait={waitType:"event", eventKeys:keys, eventKey:keys[0], onTimeout, timeoutSeconds}`（保留 eventKey 首键供旧恢复逻辑/展示）。
- 信号命中 output 增 `matchedEventKey`（取自 payload.matchedEventKey）；超时 output `signaled:false`、无 matchedEventKey；preset/resume 路径同样透传 eventKeys。
- `api/main.py` 启动恢复：`restore(event_keys=wait_payload.get("eventKeys") or [wait_payload["eventKey"]], ...)`。
- 信号 REST（按 key 广播 / 按 token 直投）不变：竞速 pending 挂在多个 key 上，任一 key 广播自然命中。

## 5. 告警自动恢复（recovery）

- 触发：`record_run` 中 `healthy=True`（is_healthy）且本次未产新告警事件时，在锁内找出该 graph 下全部满足 `status=="open"` 且 `rule_id != "rollout_gate"` 的告警：
  - 置 `status="resolved"`、`last_seen=record.finished_at`、`last_run_id=record.id`（不新增字段、PG 档兼容）；
  - 收集到锁外通知队列，`transition="recovery"`。
- 不自动恢复：`acknowledged`（人工处理中，留给手动 resolve）、`rollout_gate`（发布门禁回滚状态须人工确认）。
- 幂等：已 resolved 不重复处理、不重复通知。
- notify：`LIFECYCLE_TITLES["recovery"]="告警已自动恢复"`；`build_lifecycle_subject/body` 无需特化（复用通用 lifecycle 形状，标题取自 LIFECYCLE_TITLES）。
- 语义说明：v1 一次健康运行即恢复（不做连续 N 次 / flapping 抑制，列入非目标）。

## 6. lifecycle 限流退避

- `AlertNotifier.__init__(message_service, *, clock=None, lifecycle_min_interval_seconds=60, time_func=None)`：
  - 内部 `_last_lifecycle: dict[tuple[str,str], float]`，key=(alert.id, transition)；
  - `notify_lifecycle` 先按 `time_func()`（默认 `time.monotonic`）判定：距上次同 key 投递 < interval 则**跳过**，返回空 `AlertChannelDelivery()`（lastNotifiedAt=None，不更新投递状态、不记 last_ts）；
  - 实际投递成功才记 time；投递失败也记 time（避免失败重试在锁外紧密循环；失败仍落 errorCode/errorMessage）。
- `notify`（new 首次）**不限流**——首条必达。
- merged 高频最受益；escalated/resolved/recovery 天然单次幂等，限流为兜底。
- 节流状态进程内、重启即失；不持久化。
- registry 装配维持 `AlertNotifier(services.message_service)`（默认 60s），不改装配签名。

## 7. REST / 前端

- 零新 REST 端点；现有 wait 信号/列表、告警 lifecycle 端点形状不变（list_pending 纯超集增 eventKeys）。
- 前端：
  - `l1.ts`：常量 MAX_WAIT_SECONDS=3600、MAX_EVENT_WAIT_SECONDS=86400；新增 MAX_JITTER_SECONDS=300、MAX_EVENT_KEYS=8；wait duration 增 jitterSeconds 校验、event 增 eventKeys 校验（与 eventKey 互斥、1-8、逐键静态校验）；提示文案区间随常量。
  - `wait.schema.ts`：durationSeconds maximum 3600、timeoutSeconds maximum 86400；duration oneOf 增 jitterSeconds；event oneOf 增 eventKeys(array)；x-outputSchema 增 plannedDurationSeconds/jitterSeconds/matchedEventKey/eventKeys。
  - `WaitConfig.tsx`：duration 区增「抖动上限（秒，0-300，可选）」InputNumber；event 区在单 eventKey 之外增「多事件竞速」切换——勾选后用动态列表（1-8 个 Input，增删按钮）编辑 eventKeys，与单 eventKey 互斥切换；超时 InputNumber max 随常量。
  - nodeCatalog.ts NodeConfig 增 `jitterSeconds?`、`eventKeys?: string[]`。
  - i18n：新文案走 editor namespace（zh-CN 填实、en-US `{}`），沿用既有零依赖 i18n。

## 8. 测试（候选 U510 起，落码后转正式并回填 docs/13）

- DSL：duration 3600 合法 / >3600 拒绝；event 86400 合法 / >86400 拒绝；jitterSeconds 越界/非整/bool 拒绝；eventKeys 与 eventKey 互斥、0/9 个拒绝、逐键白名单、重复拒绝。
- loader duration：jitter 注入固定 rng 断言 actual=planned+randint、jitter=0 确定性、帧 timeout=actual、output 三字段；dynamic/absolute 上限随 3600。
- loader event：eventKeys 任一信号命中即继续 + matchedEventKey + 其余 key 订阅已清理；超时清理全部 key；取消清理全部 key；非法渲染键 WAIT_EVENT_KEY_INVALID。
- broker：request_any 多键挂载、signal 任一键首决、signal 第二键不覆盖、wait 后 _by_key 全清、restore 多键幂等、list_pending.eventKeys。
- 跨重启：eventKeys 中断帧 → restore 多键 → 任一键信号恢复续跑（HTTP/单测层）。
- 告警 recovery：失败建 open 告警→健康运行自动 resolved + recovery 通知一次；acknowledged 不自动恢复；rollout_gate 不自动恢复；连续健康不重复通知。
- lifecycle 限流：同 alert 同 transition 60s 内第二次跳过（空 delivery）、超间隔放行、new 不限流、注入 time_func 确定性。
- 前端：l1 校验用例、schema 快照、WaitConfig 交互（vitest）。

## 9. 契约同步矩阵（立项原子内完成）

- docs/04 §5.5：wait config 字段表增 jitterSeconds/eventKeys、区间更新（落码原子回填精确行）。
- docs/03：node config / wait 帧 / list_pending 输出纯超集注记。
- docs/09：无新模块（event_waits/loader/notify/records 既有）。
- docs/12：wait 帧 eventKeys、list_pending eventKeys、lifecycle recovery 注记。
- docs/13：U510 起转正式。
- docs/14：D19/D28 行加「2026-09-23 部分取回：时长放开/jitter/竞速/recovery/lifecycle 限流；其余仍缓做」，**不解除条目**。
- docs/08：立项条 + 决策（上限取值、jitter 仅 duration、竞速 OR 语义、recovery 一次健康、限流 60s）。
- docs/00：文档地图加 docs/54 行。
- CHANGELOG.md：Unreleased 加条。
- handoff.md：L3 时间块 + Active work 新条目（📋 → 收口 ✅）。

## 10. 原子提交序（feat 与 docs(handoff) 分开，不 push）

1. `docs(wait): specify raised limits, jitter and event fan-in`（docs/54 + 08/14/00/CHANGELOG/handoff 立项）
2. `feat(wait): raise duration/event limits and add duration jitter`（dsl/loader/常量 + 后端测）
3. `feat(wait): support multi-event fan-in wait and durable restore`（broker/loader/帧/main restore + 测）
4. `feat(monitoring): notify on auto recovery and throttle lifecycle sends`（notify/records + 测）
5. `feat(frontend): expose wait jitter and multi-event keys in config panel`（l1/schema/WaitConfig/i18n + vitest）
6. `docs(wait): close out wait limits/jitter/fan-in and alert recovery`（收口：04/03/12/13/14/00/08/CHANGELOG/handoff + 门数字）

## 11. 风险与回滚

- 长 event 等待占用 broker 挂起条目：有超时上限 24h 与取消/恢复兜底，进程重启经 docs/53 恢复（仅 PG）；内存档重启即失为既有语义。
- 自动恢复可能误判抖动恢复：v1 明确一次健康即恢复，flapping 抑制留后续；acknowledged/rollout_gate 不自动恢复已规避高风险场景。
- 竞速多键增加 _by_key 挂载数：上限 8，清理路径全覆盖（信号/超时/取消/restore 幂等）。
- 回滚：均为纯超集 + 常量放宽，revert 各 feat 原子即可，无迁移、无存量数据改写。
