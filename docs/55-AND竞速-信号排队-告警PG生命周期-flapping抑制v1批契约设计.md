# AND 竞速 / 停摆期信号排队 / 告警 PG lifecycle / flapping 抑制 v1 批契约设计

> 状态：**已落码收口（打包 B，2026-09-24）**。立项为 docs-only 批（AI 承接「一口气搞了」总授权），部分取回 docs/14 D19（wait 余部）与 D28（监控告警余部），全部取**进程内 v1 / 现有 PG 两档同形**形态，**零新依赖**，新增 1 个迁移（021），**不新增 ADR、不解除 D19/D28 缓做条目**。承接 docs/47/49/50/53/54（wait 系列）与 docs/33/52/54（告警系列）。

## 0. 已核实的现状缺口（2026-09-24 对活代码）

1. **AND 竞速缺失**：`collaboration/event_waits.py` 的 `request_any`/`signal_key` 只实现 OR 竞速（docs/54），多键必须**全部**命中才放行的 AND 语义不存在；DSL/loader/帧/前端面板均无 mode 概念。
2. **停摆期信号不排队**：`signal_key` 对无 pending 的键直接返回 0，信号丢弃。信号早于登记到达（同进程内竞态、AND 部分键早到）无缓冲。
3. **PG 档告警 lifecycle 缺口（实测）**：
   - `monitoring_alerts` 表（002 建表 + 020 补 action）缺 `rule_name` / `escalated_at` / `assignee` 三列，`PgMonitoringStore._ALERT_COLS`/`_alert_from_row`/INSERT 均不写这三字段（自定义规则名 PG 读回为 None，升级时间与值班人重启丢失，靠进程内 OpsStore 重评/关联）。
   - `PgMonitoringStore.record_run` **没有内存档 docs/54 §6 的 healthy 自动 recovery 段**（内存档 `monitoring/records.py` 一次健康即 resolve 该图 open 内置告警；PG 档只 raise/merge）。
   - PG 档 `_notify_outside_lock(alert, cfg)` 无 transition 参数，merge 不发 lifecycle 通知、recovery 无通知路径；内存档已有 merged/escalated/resolved/recovery 四类 lifecycle 通知 + 60s 限流。
4. **flapping**：内存档 recovery 为「一次健康即恢复」，偶发一次成功就 resolve、紧接着再失败即新开告警并再发一条 new 通知，抖动场景告警/通知噪声大；无恢复冷却窗。

## 1. 范围与非目标

**范围（四项）**：

1. wait event 增 `eventWaitMode: "any" | "all"`（默认 any，旧形状零回归），all＝AND 竞速：登记的全部 eventKeys 都收到信号才放行。
2. broker 进程内 per-key 信号排队 ring（无 pending 的信号暂存，登记时消费），缓解同进程信号早到/AND 部分键早到。
3. PG 档告警 lifecycle 补齐：迁移 021 加三列；rule_name/assignee 落库、escalated_at 惰性升级持久化；record_run 补 recovery；merge/recovery 走 lifecycle 通知（与内存档同构，复用 docs/54 AlertNotifier 60s 限流）。
4. flapping 抑制（两档同形）：规则配置增 `recovery_healthy_streak`（连续健康 N 次才自动恢复，默认 1＝旧行为）与 `recovery_cooldown_minutes`（自动恢复后冷却窗内同 rule+graph 再触发只建/并告警、不发 new 外部通知，默认 None＝关闭）。

**非目标（仍缓做，不解除 D19/D28）**：

- 多实例跨进程信号路由、跨重启信号排队（队列进程内、重启即失；AND 已命中键集合不跨重启，restore 后重新集齐）、AND 跨重启的部分命中持久化。
- 值班表/静默规则本身 PG 化（本批只把告警上的 **assignee 快照**落库；OnCallSchedule/Silence 仍进程内，照 docs/33 §5）。
- OTel/Prometheus 正式栈、长保留时序、告警规则模板市场、通知渠道富文本/@人（随 D24/D28 余部）。
- 冷却窗状态 PG 化（进程内 dict，PgMonitoringStore per-tenant 常驻；重启丢失仅可能导致窗内多一条通知，fail-safe 方向）。
- 新节点类型、DSL 版本号变更（纯超集加字段）、新外部依赖。

## 2. AND 竞速（eventWaitMode=all）

### 2.1 DSL / Schema

- wait 节点 `waitType=event` 配置增可选 `eventWaitMode: "any" | "all"`，缺省 `"any"`。
- `all` 仅在多键（`eventKeys` 2-8 个）时有意义：L1 校验 `all` 模式必须使用 eventKeys 且数量 ≥2（单键 all 报「全部模式至少需要 2 个事件键」）；`any` 模式维持单键/多键均可。
- 前端 `wait.schema.ts` event 段加 mode 字段（Radio：任一事件/全部事件，默认任一）；`WaitConfig.tsx` 多键区按 mode 展示；`nodeCatalog.ts` 输出 schema 同步；L1 新用例。
- 后端 DSL/loader 对缺省 any 的旧图零回归；非法 mode 值按既有 DSL 校验拒绝。

### 2.2 broker（collaboration/event_waits.py）

- `_Pending` 增字段：`mode: str`（"any"|"all"）、`matched: dict[str, dict]`（已命中键 → 该键信号 payload，保序按登记序）。
- `request(event_key=...)` 薄封装传 `mode="any"`；`request_any(..., mode: str = "any")`：
  - any：现状（任一键首决 set，payload 注入 matchedEventKey）。
  - all：每个键首次信号记入 `matched`（重复同键信号不覆盖首条）；当 `set(matched) == set(event_keys)` 时 set Event 放行。
- 放行 payload 形状（all）：
  - `matchedEventKeys`: 按**登记顺序**排列的全部命中键；
  - `matchedPayloads`: `{键: 该键 payload}`；
  - `matchedEventKey`: 末个集齐的键（向后兼容单键字段，列表/详情不致空）。
- `signal_token`（人工直投）：不区分 mode，直接放行（显式人工强制），payload 缺省 matchedEventKey 取首键（现状）。
- all 超时：wait() 返回 None，走 onTimeout；输出附 `receivedKeys`（已命中的键，保序）与 `eventKeys`（全量）。
- `list_pending()`：all 条目附 `eventWaitMode:"all"`、`receivedKeys`；any 条目形状不变。
- `restore(..., mode: str = "any")`：帧重建时传 mode；**matched 集合不跨重启**（v1 限制，见 §1 非目标），恢复后须重新集齐。

### 2.3 loader / 帧 / 输出

- event 分支读 `node.config.get("eventWaitMode", "any")`，request_any 传 mode；wait 中断帧 `wait:{...}` 增 `eventWaitMode`，restore 透传。
- 信号唤醒输出（all）：在现有 event 输出超集上增 `matchedEventKeys`、`matchedPayloads`；`matchedEventKey` 取末集齐键。
- 超时输出（all）：增 `receivedKeys`；any 输出形状不变。
- 预置输入（`trigger_payload.waitEvents`）不登记 broker，形状维持（v1 预置即视为整节点已满足，不分键）。

## 3. 停摆期信号排队（进程内 ring）

- broker 增 `_queue: dict[str, deque[tuple[float, dict]]]`，每键容量 `QUEUE_PER_KEY = 4`、有效期 `QUEUE_TTL_SECONDS = 3600`（monotonic 时间戳，惰性过期）。
- `signal_key(event_key, payload)`：
  - 释放了 ≥1 个 pending → 不入队；
  - 该键无有效 pending（无订阅或订阅均已 signaled）→ 入队（超容淘汰最旧），返回值由 int 改为 `{"released": int, "queued": bool}`（同步更新 REST 调用点与测试）。
- 登记路径（request/request_any/restore）建完 pending 后立即 `_drain_queue_locked(pending)`：
  - 惰性丢弃过期项；any：取任一键队列中最早一条直接命中（set）；all：把各键队列首条依次填入 matched，集齐则 set；消费即出队（每键最多消费一条，余项留给后续 pending）。
- `reset()` 同时清队列。
- 语义边界：队列只在**同一进程生命周期**内缓冲早到信号；进程重启窗口内到达的信号无 broker 可入，仍丢失（跨重启排队需 PG 信号箱，缓做）。REST `POST /api/waits/events` 响应体现 `released/queued`，审计日志不变。

## 4. PG 档告警 lifecycle 补齐（迁移 021）

迁移 `021_monitoring_alerts_lifecycle.sql`（幂等 `ADD COLUMN IF NOT EXISTS`）：

```sql
ALTER TABLE monitoring_alerts ADD COLUMN IF NOT EXISTS rule_name TEXT;
ALTER TABLE monitoring_alerts ADD COLUMN IF NOT EXISTS escalated_at TEXT;
ALTER TABLE monitoring_alerts ADD COLUMN IF NOT EXISTS assignee TEXT;
```

同步把 002_storage.sql 的新装库 `CREATE TABLE monitoring_alerts` 补为含 action/rule_name/escalated_at/assignee（照「002 已含、迁移供旧库」惯例）。

PgMonitoringStore 改造（与内存档同构）：

- `_ALERT_COLS`/`_alert_from_row` 增 rule_name/action/escalated_at/assignee；INSERT 新告警写 rule_name 与 assignee（assignee 取 `OpsStore.remember_assignee` 返回的当前值班人快照；值班表本身仍进程内）。
- 惰性升级：list/get 装饰路径 `apply_escalation` 判定升级时，**UPDATE escalated_at 回库**并锁外发一次 `escalated` lifecycle 通知（内存档已有；PG 档补同等 pending→锁外通知路径），升级幂等（已升级不重复）。
- `record_run` 事务内补 recovery 段：healthy 且满足 flapping 门槛（§5）时，把该图 `status='open' AND rule_id <> 'rollout_gate'` 的告警 UPDATE 为 resolved（last_seen/last_run_id 刷新），收集为 transition="recovery" 的锁外通知；acknowledged 不自动恢复（与内存档一致）。
- `_raise_or_merge_locked` 合并路径返 `(Alert, "merged")`（现状 merge 返 None 不通知）；`_notify_outside_lock(alert, cfg, transition="new")` 支持 new/merged/escalated/recovery，全部经 `AlertNotifier`（new 走 notify、余走 notify_lifecycle，复用 60s 限流）。
- rollout_gate 专用告警（raise_rollout_gate_alert）PG 路径维持 action 列读写，merge 通知行为与内存档对齐。

## 5. flapping 抑制（两档同形）

### 5.1 规则配置（monitoring/alerts.py RuleConfig，纯超集）

- `recovery_healthy_streak: int = 1`：连续健康运行达到该次数才触发自动 recovery；范围 1-20，默认 1＝docs/54 旧行为（一次健康即恢复）。
- `recovery_cooldown_minutes: int | None = None`：自动恢复后，同 (rule_id, graph_id) 在该分钟窗内再次触发时，站内告警照常新建/合并（可观测不丢），但**抑制 new 外部通知**；None/0＝关闭；范围 1-10080。
- `validate_rules` / `rules_from_raw` 同步校验（类型、布尔排除、范围、null 合法）；PUT 规则体旧形状（无两字段）零回归。监控规则前端表单增两个控件（连续健康恢复次数 InputNumber 1-20、冷却分钟 InputNumber 可空 1-10080）。

### 5.2 连续健康计数

- 内存档：新增 `_healthy_streaks: dict[graph_id, int]`，record_run 时 healthy 则 +1、不健康归零（与 `_streaks` 失败连续计数对称）。recovery 段门槛由「if healthy」改为「if healthy and self._healthy_streaks[graph] >= cfg.recovery_healthy_streak」。
- PG 档：复用 record_run 已拉取的该图最近运行（`_recent_locked`，DESC 200 条），在 Python 侧用 `monitoring.metrics.is_healthy` 从最新一条向前数连续健康数，零新增 SQL；门槛同上。
- 门槛未达到：open 告警保持 open，不发 recovery。

### 5.3 恢复冷却窗

- 内存档：`_recovery_cooldown: dict[(rule_id, graph_id), resolved_at_iso]`，仅**自动 recovery** 写入（手动 resolve 不写）。`_raise_or_merge` 新建告警后，若命中未过期冷却窗，返回的 transition 取 `"new_suppressed"`（锁外不投递 new 通知，站内告警已建）；合并（merged）通知不受冷却影响。
- PG 档：同等 dict 挂 PgMonitoringStore 实例（per-tenant 常驻，进程内，重启丢失语义见 §1）；INSERT 新告警后查冷却窗决定 transition。
- 时间比较复用 tz-aware ISO `datetime.fromisoformat`，解析失败 fail-safe（按不在窗内处理，宁发勿漏）。

## 6. REST / 前端 / SSE

- `POST /api/waits/events` 响应改为 `{"released": n, "queued": bool}`（原仅释放数）；`GET /api/waits` 行对 all 条目附 eventWaitMode/receivedKeys；SSE wait 载荷与帧为纯超集。
- 监控规则 GET/PUT（RuleConfig）自动携带两新字段；前端规则表单加控件 + 纯逻辑校验测试。
- 不新增端点；权限/审计/租户隔离沿用现状。

## 7. 测试（候选 U540 起，落码后转正式并回填 docs/13）

- broker（新/扩 test_event_wait_broker）：all 全命中才唤醒与 payload 形状（matchedEventKeys 序/matchedPayloads/末键 matchedEventKey）、部分命中不唤醒、all 超时 receivedKeys、直投不分 mode 即唤醒、any 旧行为零回归；排队：无 pending 信号入队且响应 queued、登记后 any 消费早到信号、all 消费多键早到信号集齐、TTL 过期丢弃、超容淘汰、reset 清队列。
- loader/DSL：all 模式图端到端（信号分键投放后放行）、帧 eventWaitMode 透传、restore mode、输出超集字段、非法 mode/单键 all 的 422/失败路径。
- 监控内存档：streak=2 时一次健康不恢复/两次恢复、streak=1 旧行为；冷却窗内新建告警 suppressed 不发 new、窗外正常、手动 resolve 不写冷却；merged/escalated/recovery lifecycle 通知路径。
- PG 直连集成（ATLAS_RUN_INTEGRATION=1 + DATABASE_URL）：迁移 021 后三列落库读回（rule_name/assignee/escalated_at）、record_run healthy recovery UPDATE 与通知、flapping streak SQL 侧行为、冷却抑制、merge lifecycle 通知。
- 前端：wait.schema/L1 新用例（all 需 ≥2 键、mode 枚举、缺省 any）、规则表单两字段范围校验。
- 回归：docs/54 三 smoke（event_wait/alert_notify/dynamic_wait）随 signal_key 返回值形状变化同步断言并复跑全绿。

## 8. 契约同步矩阵（立项原子内完成）

- 03 契约索引：wait event 配置/输出（eventWaitMode、matchedEventKeys、matchedPayloads、receivedKeys）、waits 信号响应形状、RuleConfig 两字段、monitoring_alerts 三列。
- 04 节点/DSL 契约：wait event 配置与输出超集；05 运行时/Schema：broker 排队与 AND 语义。
- 09 模块映射：collaboration/event_waits、monitoring、storage/pg 职责不变（无新包）。
- 12 REST：waits 响应形状、规则配置字段。
- 13 测试用例：U540 起正式编号。
- 14 缓做登记：D19/D28 加「部分取回、不解除」注记（AND/排队进程内、PG lifecycle 三列、flapping；多实例/跨重启排队/值班表 PG/OTel 仍缓做）。
- 00 文档地图、08 迭代计划、CHANGELOG 各一条。

## 9. 原子提交序（feat 与 docs(handoff) 分开，不 push）

1. `docs(wait): contract for AND fan-in, queued signals, PG alert lifecycle, flapping (docs/55)`（本契约 + 矩阵）
2. `feat(collaboration): support AND fan-in and per-key signal queue in wait broker`
3. `feat(graph): wire all-mode event wait through DSL, loader, frames and outputs`
4. `feat(frontend): add all-mode wait config, signal fields and L1 checks`
5. `feat(monitoring): flapping-aware recovery streak and cooldown in memory store`
6. `feat(storage): persist alert lifecycle in PG and align recovery/lifecycle notify`（迁移 021 + 002 同步）
7. `feat(frontend): expose recovery streak/cooldown in alert rule form`
8. `test(wait): sync docs/54 smokes to signal response shape and close out docs/55`（收口门 + handoff/CHANGELOG/14）

## 10. 风险与回滚

- **AND 唤醒遗漏**：键集合比较必须按登记全集，重复信号不覆盖首条；以 broker 单测穷举（部分/乱序/重复/直投/超时）。
- **排队误投**：只在登记瞬间消费、每键一条，避免旧信号误中新一轮等待；TTL 与 reset 覆盖。
- **PG recovery 双发/漏发**：recovery UPDATE 与通知收集在同一事务内完成、锁外发；lifecycle 60s 限流兜底重复。
- **flapping 配置错误**：默认值严格对齐旧行为（streak=1、cooldown=None），不配置即零行为变化；校验失败 422 中文聚合。
- 回滚：纯超集加字段/加列，回滚代码后新列空值无害、新配置字段被忽略；迁移 021 只加列不删数据。
