# 打包 T 契约：RoutingStore PG 化（D32 余部切片）

> 形状权威＝本文。落码与本文冲突时先改本文再改代码；只许增测，不改既有契约语义。
> 范围一句话：灰度配置与 rollout 状态机在 PG 档**跨重启存活、跨连接一致**；不新增端点、不自动放量、不解锁多副本。

## 0. 已核实前提（回代码，不是转抄）

- `RoutingStore`（`src/atlas/routing/store.py:63`）：进程内、`threading.Lock` 单锁，`_states: dict[str, RolloutState]`；方法面 `configure / snapshot / start / promote / rollback / resolve / reset`。两档装配**都**是进程内实例——`iam/registry.py:145`（pg 分支）与 `:173`（memory 分支）。
- 存储抽象**不是未落地**：`src/atlas/storage/pg.py` 的 `PgBackend`＋二十余个 per-tenant PG store（`PgRunsStore` 等），pg 分支装配已是成熟模式；`docs/08 §八 C 组` 与 `docs/14 D32` 旧注记说「PG 化依赖 11 S1 存储抽象」**已过期**（11 S1 指多实例存储层重构，不是 per-tenant PG 落库能力）。
- API 调用面：`api/main.py` 六处——rollout GET/PUT/start/promote/rollback（:2557–2608）与两处入站 `resolve`（:995／:2957）；REST 形状与 409/422 映射不动。
- 迁移最新 **030**（`db/migrations/030_scheduled_triggers.sql`）；下一号 **031**。全量新装 schema 在 `db/migrations/002_storage.sql`，新表必须两处都登记。
- D32 触发条件原文要求「真实生产发布或多实例部署」。本批取回的是其中**单实例也成立**的半边（配置/状态跨重启不丢），与打包 N（调度 PG store）同构；**多实例状态同步、热推送、图版本冻结的自动 pin 全部不在本批**，D32 整体不解除。

## 1. 决策（六条；改任何一条先改本文）

- **D-1 新表 `rollout_states`，租户复合键 PK (tenant_id, graph_id)**，不引入代理键（同 schedules/shadow_runs 先例）。列＝RolloutState 的持久化投影：`status TEXT NOT NULL DEFAULT 'idle'`、`config JSONB NULL`、`stable INTEGER NULL`、`candidate INTEGER NULL`、`started_at TEXT NULL`、`rolled_back_at TEXT NULL`、`rollback_reason TEXT NULL`、`rollback_actor TEXT NULL`、`traffic JSONB NOT NULL`、`updated_at TEXT NOT NULL`。时间列取 **TEXT ISO-8601**（仓库主约定，runs 表同款；这些列不参与认领键/唯一性，不需要 TIMESTAMPTZ），读回投影与内存档逐键同型（字符串）。
- **D-2 新件 `src/atlas/routing/pg_store.py: PgRoutingStore`**，构造 `(engine, tenant_id)`，方法面与 `RoutingStore` 完全一致（含 `reset`）；返回值仍是 pydantic `RolloutState` 深拷贝，API 层零改动。装配：仅 `iam/registry.py` pg 分支换成 `PgRoutingStore(backend.engine, tenant_id)`；memory 分支与所有测试默认行为不变。不进 `PgBackend` 工厂（routing 包不反向依赖 storage 包；在 registry 里直接构造，与 PgReportStore 等并列）。
- **D-3 行锁串行化，不靠应用锁**：状态机写操作（configure/start/promote/rollback）与 resolve 的读改写一律 `SELECT ... FROM rollout_states WHERE tenant_id=:t AND graph_id=:g FOR UPDATE`（无行则在事务内插入 idle 默认行后再锁，或 UPSERT 后重取），事务提交即释放。⇒ 同图并发 start/resolve 由 DB 串行，计数不丢；跨实例虽然也被行锁串行，但**本批不据此声明多实例可用**（帧恢复/D20 等闸口未动）。
- **D-4 无行＝idle 默认态，惰性落行**：`snapshot` 无行返默认 `RolloutState(graph_id)` 且**不写库**（与内存档一致）；`resolve` 在 idle 无行时只做解析、计数要落（因计数是有价值的观测）——**口径：resolve 一律 UPSERT 保证计数存活**，但 id 语义（无配置未启动）不变。configure/start/promote/rollback 均 UPSERT。`rolled_back` 态及原因/actor 持久保留（重启后新流量仍走 stable）。`reset`＝`DELETE WHERE tenant_id=:t`（对齐现 reset 清空语义）。
- **D-5 traffic 计数语义逐字不变**：candidate 命中计 candidate、其余有版本计 stable、segment 按 `_SEGMENT_KEYS` 计；jsonb 读改写在同一行锁事务内完成。`config` 存 `model_dump(mode='json')`，canary 期 full 段剔除等运行期逻辑仍在 store 代码里、不固化进表（存的是配置本体）。
- **D-6 零新端点／零新错误码／零新依赖／无新 ADR**（沿用 per-tenant PG 落库既定模式，非选型变更）；`.env.example` 不动；`prune_expired` **不**纳入 rollout_states（配置型数据，不该按保留期自动删）。不解除单副本三道闸与任何缓做。

## 2. 形状

- 迁移 `db/migrations/031_rollout_states.sql`：`CREATE TABLE IF NOT EXISTS`＋列 COMMENT（状态机语义、行锁口径、updated_at 用途）；首行按迁移约定带注释块（成因/键形态/时间列口径/幂等）。
- `db/migrations/002_storage.sql`：补同一建表（全量新装不依赖增量迁移）。
- `src/atlas/routing/pg_store.py`：`PgRoutingStore`；私有辅助 `_load_for_update(conn) -> RolloutState | None`、`_write(conn, state)`、`_insert_idle(conn)`；公共方法各开一个短事务。
- `src/atlas/iam/registry.py:145`：pg 分支 `routing_store=PgRoutingStore(backend.engine, tenant_id)`（import 走函数内惰性）。
- `src/atlas/routing/__init__.py`：导出 `PgRoutingStore`。

## 3. 契约同步矩阵（收口时逐项回填）

- [ ] `docs/03` `rollout_config` 段：补「PG 档运行态落 `rollout_states` 表、跨重启存活；投影字段不变」＋表登记到 Schema 所在位置。
- [ ] `docs/09` 目录树/模块映射：routing 补 `pg_store.py`。
- [ ] `docs/13`：U951–U957 登记。
- [ ] `docs/14 D32`：追记「2026-09-29 取回 RoutingStore PG 化半边（不解除本条）」。
- [ ] `docs/08 §八`：C 组 D32 行更新＋收口注记；新决策无（不触发 ADR）。
- [ ] `docs/00` 文档地图：docs/81 行。
- [ ] handoff（Project documents＋Recently shipped，旧条目滚入归档）、CHANGELOG。

## 4. 测试与验收（U951 起；新建 `tests/test_routing_pg_integration.py`，只许增测）

- **U951** configure/snapshot：配置落库，新连接（同 engine）读到一致内容；从未配置图仍返 idle 默认。
- **U952** 状态机全序跨连接：start（不足两版/未配置仍 409）→ canary → promote → full；每一步换连接复核状态与 stable/candidate。
- **U953** rollback 幂等与持久：canary/full 回滚后状态/原因/actor/rolled_back_at 存活；重复回滚不覆盖首因；idle 未启动仍 409。
- **U954** resolve 计数存活与语义：canary/full/rolled_back/idle 各态解析后计数落表且分段正确；新连接读到累计值；candidate/stable 归属与内存档一致。
- **U955** 跨租户隔离：两租户同 graph_id 各自行互不相见（另一租户 snapshot 为 idle）；reset 只删本租户。
- **U956** 真·跨 engine 存活：dispose engine 后按同 DATABASE_URL 重建，状态与计数仍在（模拟重启的核心条款）。
- **U957** 并发不丢数：同图多线程并发 resolve（真 PG），最终 traffic 总数＝调用数（行锁串行的直接证据）。
- 集成门控沿用既有 `ATLAS_RUN_INTEGRATION` 模式；无 PG 环境 skip（不冒充通过）。
- 复跑：内存档相关测试（`tests/test_routing_store.py` 等）零改动零回归；`tests/test_handoff_integrity.py` 绿。

## 5. 原子序

1. `docs(routing): specify the PG-backed rollout state store contract`（本文＋03／09／13／14／08／00 同步登记）。
2. `feat(routing): add PG-backed rollout state store with row-level locking`（迁移 031＋002＋pg_store＋registry 接线）。
3. `test(routing): cover the PG rollout store across restarts, tenants and concurrency`（U951–U957）。
4. `docs(routing): close out the PG rollout state landing`（收口回填＋handoff＋CHANGELOG）。

## 6. 残余风险（别当成「D32 收干净了」）

- 多实例下的灰度语义并未全部成立：本批只保证状态/计数有共享落点；审批帧恢复（D20/D44）、事件总线（D5）、网关选型（D6）未动，**单副本三道闸不撤**。
- resolve 从纯内存读改写变为每请求一次行锁事务：性能特征改变（沙盘无真实流量，量不出影响）；热点图同槽竞争是真实代价，靠行本身短事务缓解。
- 图被删后 `rollout_states` 无外键联动（全仓无此外键约定），残留行不暴露也不影响其他图；不做回收（无对应读路径）。
- 不做自动 promote、不做配置型表的 retention；reconcile/人工重放仍随 D36 触发条件。
