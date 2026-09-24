# 运行时错误 i18n / OpenAPI 硬删除 / 静默编辑·值班日轮换 / 断点持久化 / 投递日志 PG 化 v1 批契约设计（打包 G）

> 立项时间：2026-09-24。AI 承接「一口气搞了」总授权。docs-only 契约先行，落码按本文 §9 原子序。
> 部分取回 docs/17 §2.4 显式遗留与 docs/14 的 D22 / D24 / D27 / D28 余部；**全部进程内可闭环、零外部资源、零新依赖（不引 PyYAML/oauth2/定时器/OTel），不新增 ADR，不解除任何缓做条目**。
> 本文是打包 G 五项的唯一形状权威；与既有契约冲突以 01–08 规格文档为准，落码偏差在「落码收口注记」回填。

## 0. 已核实的现状缺口（2026-09-24 对活代码）

| 项 | 现状（磁盘核实） | 缺口 |
|---|---|---|
| G1 运行时错误 | `graph/conditions.py:50 ConditionEvalError(Exception)` 仅中文 message，**无 code/params**，约 20 处 raise（语法/参数数/类型/除零等）。`graph/loader.py:95 WaitNodeFailure(node_id, code, message)` **已带 code**（5 码），同步通道 `api/main.py:321` 返 500 `{detail:{code,message,nodeId}}`；但 run failed 结果顶层只有 `error: str(exc)`（loader.py:1112 子图兜底、主图兜底同形），`fail_exit`/loop 的 `expression_errors: list[str]`（loader.py:1391/1427/1466）只有中文字符串。i18n 第二批债已完成**编译期** 422（Issue 四元组、170 码），显式遗留①condition/loop **求值**错误、②运行时 wait 失败 5 码（docs/08 收口条）。 | 运行**终态失败**无结构化 code，英文态无法映射 |
| G2 OpenAPI | 内存 `openapi/store.py` 与 PG `openapi/pg_store.py` 均有 add/list/get/delete（软删置 deleted_at）/restore/put_credentials；迁移 023 加 content_hash/deleted_at。路由 `GET /api/openapi/imports`（1471）、`DELETE`（1490 软删）、`POST /{id}/restore`（1500）。 | 列表无法看已删条目；无法物理删除（含凭证列） |
| G3 静默/值班 | `monitoring/silences.py`：`Silence(id, rule_id?, graph_id?, reason, created_by, created_at, expires_at, suppressed_count)`、`OnCallSchedule(members, index, updated_at?, updated_by?)`。路由 silences 仅 POST/GET/DELETE，on-call GET/PUT/POST rotate。内存 `records.py` OpsStore 与 PG（迁移 024）双档；轮换为手动 `rotate_oncall`，cap100 惰性过期、取模。 | 静默不能改只能删了重建；值班只能手动轮换，无按日自动 |
| G4 调试断点 | `debug/sessions.py:60 BreakpointSpec`（expression/hit_count/log_message/onException），断点仅经 `/run/stream` body `debug.breakpoints` 会话级传入（`api/main.py:2390 _validate_debug`、`:2573 debug_broker.create`），**不落图**。前端 `editorStore.ts:36 Breakpoint`、`breakpoints: Record<nodeId, Breakpoint>`（:50），`graphSerializer.ts SerializedGraph` 节点含 config/retry、**无 debug 字段**。图存储为自由 JSON 透传（`storage/memory.py:42 self._graphs[id]=raw`、`storage/pg.py:604 json.dumps(graph)`）。 | 画布断点刷新/换会话即丢，不能随图共享 |
| G5 投递日志 | `message/service.py:39 DeliveryRecord(id,channel,to,subject,sentAt,status,attempts,elapsedMs,errorCode?,errorMessage?)`；MessageService 内建 `_deliveries: deque(maxlen=200)`（:104），`list_deliveries`/`reset`（:304/312）。registry.py:64 现注释「MessageService 是服务非存储，不进 Repository 抽象（docs/24 §1.1）」；`GET /api/demo/deliveries` 只读。 | 投递记录仅进程内 ring，重启即丢、PG 档不跨实例 |

## 1. 范围与非目标

### 1.1 本批范围（五项）

1. **G1 运行时终态失败错误码化＋英文态 i18n**：ConditionEvalError 加 code/params（归并码族）；run failed 结果与 loop/foreach expression_error 通道并行下发 `errorCode`/`errorParams`（中文 `error`/`expression_errors` 全量保留，向后兼容）；WaitNodeFailure 5 码经运行失败通道结构化；前端运行结果/调试台失败渲染按 code 本地化（zh/en）。
2. **G2 OpenAPI include_deleted 列表＋硬删除**：`GET /api/openapi/imports?include_deleted=true`（administer）；`DELETE /api/openapi/imports/{id}?hard=true`（administer，仅已软删可物理删）；内存/PG 双档各加 `purge`，列表投影补 `deletedAt`。
3. **G3 静默 PUT 编辑＋值班惰性按日轮换**：`PUT /api/monitoring/silences/{id}`（administer，改范围/原因/到期，不改 id/计数）；OnCallSchedule 增 `rotationIntervalDays?`/`lastRotatedAt?`，读路径（get_oncall/current_assignee）惰性判定到期自动取模轮换，**不引定时器/后台线程**；迁移 026 加两列。
4. **G4 断点随 Graph JSON 持久化**：SerializedGraph 增顶层可选 `debugSettings.breakpoints`；画布断点随图保存/加载往返，调试运行路径不变（前端从 store 注入），普通运行不受影响；后端图存储自由透传（零迁移），DSL 编译/校验忽略该顶层字段。
5. **G5 投递日志 PG 化**：抽 `DeliveryStore` 抽象（record/list/clear），内存 `InMemoryDeliveryStore`（搬现 deque 200 语义），`PgDeliveryStore`（迁移 025 `message_deliveries` 表，跨重启保留、reset 清本租户）；MessageService 改注入 store，registry 内存/PG 两档装配；REST 零改动。

### 1.2 非目标（继续缓做，触发条件不变）

- G1：condition llm/rule **分支 fail-safe 诊断**（evaluation 内 result=None 的中文备注，非终态失败）不 i18n；节点目录/模板元数据多语言仍属 D13；不做错误码平台化。
- G2：**YAML 导入**（需 PyYAML＝新依赖＋ADR）、oauth2/openIdConnect 接线、凭证轮换/连接测试、导入历史版本仍缓做。
- G3：静默**模板/循环静默**、多值班组/排班日历/节假日、跨实例分布式锁、真正的定时调度器（cron/线程）仍缓做；日轮换为惰性（读到才转），不保证精确到点。
- G4：断点**团队级权限/审批**、断点模板、跨节点变量血缘、条件断点的 DSL 表达式校验器增强仍缓做；持久化断点不影响普通（非调试）运行。
- G5：短信、IM 应用 OAuth/入站消费、消息模板 CRUD/多语言、DLQ、投递记录长保留/归档、AlertChannel 多目标配置化仍缓做；PG 表沿用 ring 语义（每租户保留最近 N，默认与内存一致 200）。
- 多实例/NATS/Go 网关/真实渠道联调/OTel 正式栈触发条件不变。

## 2. G1 运行时终态失败错误码化契约

### 2.1 ConditionEvalError 升级（`graph/conditions.py`）

```python
class ConditionEvalError(Exception):
    def __init__(self, message: str, *, code: str = "COND_EVAL_FAILED", params: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.params = params or {}
```

归并码族（中文 message 一字不改，仅在 raise 处补 code/params）：

| code | 触发 | 关键 params |
|---|---|---|
| `COND_SYNTAX_UNEXPECTED_CHAR` | 词法意外字符 | `token`, `pos` |
| `COND_SYNTAX_INCOMPLETE` | 表达式不完整 | — |
| `COND_SYNTAX_UNEXPECTED_TOKEN` | 语法意外 token / 缺右括号 / 函数缺右括号 | `token`, `pos` |
| `COND_FUNC_ARITY` | 函数参数个数不符 | `func`, `expected`, `actual` |
| `COND_UNKNOWN_FUNC` | 未知函数（若有） | `func` |
| `COND_TYPE_MISMATCH` | 一元/二元/逻辑运算类型不符、非布尔分支结果 | `op?`, `expected`, `actual` |
| `COND_DIVIDE_BY_ZERO` | 除零 | — |
| `COND_EVAL_FAILED` | 兜底 | `detail?` |

> 落码时按实际 raise 点归并，宁可少码、用 params 区分，也不人为造细碎码；每个码 zh/en 各一条模板，插值参数走 `{name}` 占位。

### 2.2 运行失败结果结构化（`graph/loader.py`）

- WaitNodeFailure 维持 5 码：`WAIT_ABSOLUTE_TIME_INVALID` / `WAIT_DURATION_INVALID` / `WAIT_EVENT_FRAME_INVALID` / `WAIT_EVENT_KEY_INVALID` / `WAIT_TIMEOUT_FAILED`。
- **主图与子图顶层兜底 except**（现 `"status":"failed","error":str(exc)`）在输出 dict 并行加：
  - `errorCode: str`（WaitNodeFailure→`exc.code`；ConditionEvalError 逃逸→`exc.code`；其他异常→`RUNTIME_UNEXPECTED`）
  - `errorParams: dict`（WaitNodeFailure 额外 `nodeId`；ConditionEvalError→`exc.params`）
  - `error` 中文 `str(exc)` **保留不变**（日志/兜底/旧断言零改动）。
- **loop / foreach expression_error 通道**：节点结果在既有 `expression_errors: list[str]` 与 `exitReason` 之外，并行加 `expressionErrorCodes: list[str]`（与 expression_errors 等长、按序对应；非表达式错误的退出位补空串或等长对齐，落码取与编译 Issue 四元组一致的对齐纪律）。`fail_exit(message, reason, code=None)` 增加可选 code，itemsExpression 的 ConditionEvalError 透传 `exc.code`，「遍历对象必须是数组/超长」分别给 `COND_TYPE_MISMATCH`/`LOOP_ITEMS_TOO_LARGE`。
- SSE：run failed 终态帧在现有 `error` 字段外并行下发 `errorCode`/`errorParams`（帧超集，旧字段保留）；同步 500 handler（main.py:321）已带 code，不动。
- 分支 condition（llm/rule 节点）fail-safe 的 evaluation 诊断**不在本批**（见 §1.2）。

### 2.3 前端 i18n

- 新增 locale 命名空间 `runtime.json`（zh-CN / en-US 各一份，键集合一一对齐，en 零汉字），含 `COND_*` 9 码、`WAIT_*` 5 码、`LOOP_ITEMS_TOO_LARGE`、`RUNTIME_UNEXPECTED` 兜底；技术专名不译。
- 运行结果/调试台失败渲染：新增纯函数 `resolveRuntimeError(code, params, fallback)`（有 code 且键存在→本地化模板插值；缺 code/缺键/params 缺失→回退后端中文 `error`，绝不泄漏 i18n key、绝不渲染空白）。
- 消费点：运行失败总览（error 字符串处）、节点结果 expression_errors 展示（按 expressionErrorCodes 逐行映射）。落码时以实际消费组件为准，先 grep `run.error`/`expression_errors`/`exitReason`。
- 缺省纪律与第二批债一致：中文 message 始终是兜底真相，前端只做"能映射则映射"。

## 3. G2 OpenAPI include_deleted / 硬删除契约

### 3.1 Store 演进（内存 + PG 同形）

- `list(self, *, include_deleted: bool = False) -> list[ImportedSpec]`：默认仅未删（零回归）；include_deleted=True 返回全部，投影补 `deletedAt: str | None`（内存从 deleted_at、PG 读列）。
- 新增 `purge(self, spec_id: str) -> bool`：**仅当记录存在且 deleted_at 非空**才物理删除并返回 True；不存在返 False；存在但未软删返一个可区分状态（抛 `OpenApiNotSoftDeleted` 或返三元组，落码与 restore 三元组风格对齐，本文定：**抛业务错误 `OPENAPI_NOT_SOFT_DELETED`**，由路由转 409，提示先 DELETE 软删）。PG `DELETE FROM openapi_imports WHERE tenant_id=%s AND spec_id=%s AND deleted_at IS NOT NULL`（security 两列在同行，天然级联，无独立凭证表）。
- get/restore/put_credentials 维持：get 默认对已软删记录的行为沿用现状（restore 流程需要时按现状，不在本批改）。

### 3.2 REST（`api/main.py`）

| 方法/路径 | 权限 | 语义 |
|---|---|---|
| `GET /api/openapi/imports?include_deleted=true` | **administer**（缺省/false 维持 read，且不返已删） | 列表含已删，条目带 `deletedAt` |
| `DELETE /api/openapi/imports/{spec_id}?hard=true` | **administer** | 物理删除；未软删→409 `OPENAPI_NOT_SOFT_DELETED`；不存在→404；成功 204 |
| `DELETE /api/openapi/imports/{spec_id}`（hard 缺省/false） | administer（现状） | 维持软删，零回归 |

- 名额（limit）统计仍只计未删（docs/56 已立），硬删除不影响名额。
- 审计中间件自动覆盖写方法，无需额外处理。

### 3.3 前端

OpenAPI 管理面板（编辑后台导入列表）：administer 角色可见「显示已删除」开关与已删条目上的「彻底删除」按钮（Popconfirm 二次确认，明确不可恢复）；软删/恢复入口维持。落码以现有 OpenAPI 管理组件为准。

## 4. G3 静默编辑 / 值班惰性日轮换契约

### 4.1 静默 PUT 编辑

- 内存 OpsStore 与 PgMonitoringStore 各加 `update_silence(self, silence_id, *, reason?, rule_id?, graph_id?, expires_at?) -> Silence | None`：仅命中**未过期**静默才可改，更新可变字段，`id/created_by/created_at/suppressed_count` 不可变；不存在返 None，已过期视同不存在（返 None）。PG `UPDATE ... WHERE tenant_id=%s AND id=%s AND expires_at > now`。
- REST `PUT /api/monitoring/silences/{silence_id}`（**administer**）：body 为可变字段子集（至少一项；reason 非空字符串、expires_at 为未来时刻、rule_id/graph_id 同创建校验：不得同时给、id 形态校验沿用）；不存在/已过期→404；校验失败→422 中文聚合（与 POST 同套校验函数复用）。
- 前端静默列表加「编辑」操作（复用新建表单 Drawer/Modal，预填，保存调 PUT）。

### 4.2 值班惰性按日轮换（迁移 026，不引定时器）

- `OnCallSchedule` 增字段（纯超集，默认零回归）：
  - `rotation_interval_days: int | None = None`（None/0＝不自动；允许 1–365）
  - `last_rotated_at: str | None = None`
- 纯函数（`monitoring/silences.py`，单一事实源、可单测）：
  ```python
  def maybe_auto_rotate(schedule: OnCallSchedule, now: datetime) -> OnCallSchedule | None:
      # interval 为空/成员<2/无 last_rotated_at -> None
      # elapsed_days = (now.date() - date(last_rotated_at)).days
      # steps = elapsed_days // interval；steps>=1 -> 新 schedule：
      #   index=(index+steps) % len(members)，last_rotated_at= now（推进到本轮边界，落码定 ISO）
      # 否则 None
  ```
- **读路径惰性触发**：`get_oncall()`（内存 OpsStore / PgMonitoringStore）与告警命中取值班人（`current_assignee` 装配处）先 `maybe_auto_rotate(now)`，返回非 None 则落库（内存改自身、PG UPDATE index/last_rotated_at）再返回；`now` 经模块级可注入时钟（沿用仓库既有 now 注入纪律，保证测试确定性）。手动 `rotate_oncall` 同步刷新 last_rotated_at。
- `PUT /api/monitoring/on-call` body 增可选 `rotationIntervalDays`（1–365 或 null）；设置成员或间隔时 last_rotated_at 置 now。GET 投影回传两新字段。
- 迁移 `026_oncall_auto_rotate.sql`（手写幂等，002_storage.sql 同步）：`monitoring_oncall` 加 `rotation_interval_days INTEGER NULL`、`last_rotated_at TIMESTAMPTZ NULL`（迁移 024 已建该表）。
- 前端值班卡：间隔设置（数字输入/关闭）、展示「下次轮换日期」（只读派生），手动轮换按钮维持。
- 语义边界：惰性＝有读/有告警才轮换，不保证到点即刻；跨实例以 PG 读路径为准（最后一次读到的实例推进），不做分布式锁（非目标）。

## 5. G4 断点随 Graph JSON 持久化契约

### 5.1 序列化形状（前端 `graphSerializer.ts`，03 Schema 超集）

```ts
export type PersistedBreakpoint = {
  nodeId: string
  expression?: string | null      // 条件断点表达式
  hitCount?: number | null        // 命中次数
  logMessage?: string | null      // 不停命中日志
  onException?: boolean           // 异常断点
}
export type SerializedGraph = {
  version: number
  variables: GraphVariable[]
  nodes: [...]                     // 现状不变
  edges: [...]
  debugSettings?: { breakpoints: PersistedBreakpoint[] }  // 新增、可选、顶层
}
```

- `serializeGraph(nodes, edges, variables, breakpoints?)`：第 4 参可选（零回归，既有调用/测试不传则输出不含 debugSettings）；仅当存在至少一个「启用」断点时输出 `debugSettings.breakpoints`，字段做 undefined/默认值裁剪（空断点输出为 `{nodeId}`，与 `_validate_debug` 规范化兼容）。
- `deserializeGraph` 返回值增 `debugSettings?`；`editorStore.loadGraph` 据以还原 `breakpoints: Record<nodeId, Breakpoint>`（节点已不存在的孤儿断点丢弃）。
- Editor.tsx 保存（saveGraph/saveGraphDraft）与自动保存序列化处传入当前 `breakpoints`；加载/草稿恢复处回填。
- 形状与 `api/main.py:2390 _validate_debug` 入参一一对齐（nodeId/expression/hitCount/logMessage/onException），调试运行组装 `debug.breakpoints` 的代码不变（store 已含持久化断点）；**普通运行（body 无 debug）行为完全不变**。

### 5.2 后端

- 图存储自由 JSON 透传已核实（memory raw / PG json.dumps），**零迁移、零新端点**。
- 须验证 DSL 编译/图校验对未知顶层字段 `debugSettings` 容忍（落码加一测试：带 debugSettings 的图编译/保存/运行普通流程不受影响）；若校验严格则在字段白名单放行（只放行该顶层键，不放宽节点 config）。
- 持久化断点不参与服务端执行语义，仅由前端在调试运行时回注。

### 5.3 测试

纯函数往返（serialize→deserialize 断点等价）、默认不输出 debugSettings（旧形状快照）、空/全默认断点不污染、孤儿断点丢弃、普通运行不被断点暂停（后端黑盒）、调试运行仍命中断点（既有调试测试不回归）。

## 6. G5 投递日志 PG 化契约

### 6.1 DeliveryStore 抽象（新模块 `message/deliveries.py`）

```python
class DeliveryStore(Protocol):
    def record(self, rec: DeliveryRecord) -> None: ...
    def list(self, limit: int) -> list[dict]: ...   # 倒序、clamp 1-200
    def clear(self) -> None: ...                    # reset 语义
```

- `InMemoryDeliveryStore`：搬入现 deque(maxlen=200) 语义（ring、倒序、clear），list 投影 dict 与现 `list_deliveries` 完全一致。
- `PgDeliveryStore`：持有 tenant_id 与连接工厂；迁移 025 建表（见 §6.2）；`record` INSERT（seq 走 `storage_id_seq` 先例生成 id 或专用 seq，落码对齐 openapi/recording PG store）；`list` SELECT ... ORDER BY seq/id DESC LIMIT；`clear` DELETE 本租户全部。保留最近 200：INSERT 后惰性删除超出 200 的旧行（ORDER BY seq DESC OFFSET 200 DELETE），与内存 ring 对齐（v1 简单实现：record 内清理）。
- DeliveryRecord 从 service.py 移出或 re-export（保持 `from atlas.message.service import DeliveryRecord` 向后兼容，落码优先 re-export 避免改散 import）。

### 6.2 迁移 025（`025_message_deliveries.sql`，手写幂等；002_storage.sql 同步）

```sql
CREATE TABLE IF NOT EXISTS message_deliveries (
  tenant_id     TEXT NOT NULL,
  id            TEXT NOT NULL,
  seq           BIGINT NOT NULL,
  channel       TEXT NOT NULL,
  to_targets    JSONB NOT NULL,      -- ["url/addr",...]
  subject       TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL,
  attempts      INTEGER NOT NULL,
  elapsed_ms    INTEGER NOT NULL,
  error_code    TEXT NULL,
  error_message TEXT NULL,
  sent_at       TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_message_deliveries_seq
  ON message_deliveries (tenant_id, seq DESC);
```

- reset（租户清库）流程须同步清本表（对齐 024 两表/其他租户表的 reset 清单，落码 grep reset 各表补齐）。

### 6.3 装配与接口

- `MessageService.__init__(..., delivery_store: DeliveryStore | None = None)`：缺省 `InMemoryDeliveryStore()`（测试/旧装配零回归）；内部 `_deliveries` 调用改走 store（record/list/clear），方法签名 `list_deliveries`/`reset` 不变。
- registry：内存档传 InMemoryDeliveryStore；PG 档按租户构造 PgDeliveryStore（tenant_id + 同一连接工厂），与其他 Pg*Store 装配同形。
- **决策演进记录**：docs/24 §1.1 原「投递记录不进 Repository 抽象」在本批演进为「投递记录是可持久化数据，抽 DeliveryStore（存储），MessageService 仍为服务」；本批在 docs/08 立项条与 docs/12 记录此演进，**不新增 ADR**（同一进程内存储抽象、零新外部件，非选型变更）。
- REST `GET /api/demo/deliveries` 零改动；PG 档跨重启/跨实例可见。

## 7. 迁移 / Schema / 接口汇总

- 新增迁移：`025_message_deliveries.sql`（G5）、`026_oncall_auto_rotate.sql`（G3，monitoring_oncall 加 2 列）；`db/002_storage.sql`（新装库建表脚本，以实际文件名为准）同步。
- Graph JSON Schema（03）：顶层增可选 `debugSettings.breakpoints`；节点 config 不变。
- API（12）：新增 `GET /api/openapi/imports?include_deleted=`、`DELETE /api/openapi/imports/{id}?hard=`、`PUT /api/monitoring/silences/{id}`；on-call PUT/GET 增 rotationIntervalDays/lastRotatedAt；run failed 结果/SSE 帧增 errorCode/errorParams、loop 节点结果增 expressionErrorCodes；deliveries 端点不变。
- 错误码新增：`COND_*`（9）、`LOOP_ITEMS_TOO_LARGE`、`RUNTIME_UNEXPECTED`（运行结果通道）、`OPENAPI_NOT_SOFT_DELETED`（409）。WAIT_*（5）已存在仅补运行通道下发。
- 权限：include_deleted/硬删除/静默编辑均 administer；值班轮换间隔设置随 PUT on-call 现权限。

## 8. 测试契约（候选 U663 起，docs/13 收口回填定稿）

- **G1（候选 U663–U682）**：ConditionEvalError 各码族构造/params；run failed 顶层 errorCode/errorParams（wait 5 码各一、condition 逃逸、RUNTIME_UNEXPECTED 兜底）；loop/foreach expressionErrorCodes 等长对齐；SSE failed 帧含 code；前端 resolveRuntimeError 纯函数（命中/缺键回退/params 插值/en 无汉字）＋消费组件断言。
- **G2（候选 U683–U694）**：内存与 PG 各档 include_deleted 投影/deletedAt、purge 成功/未软删 409/不存在 404/凭证随删；权限档（read 不见已删、非 administer 拒）；PG 直连集成。
- **G3（候选 U695–U716）**：maybe_auto_rotate 纯函数（不到期/到期 1 步/多步取模/成员<2/无间隔/时钟确定性）；读路径惰性落库（内存+PG）；手动轮换刷新基准；PUT 静默编辑成功/404 已过期/422 校验/不可变字段；迁移 026 上下 apply；前端间隔设置/下次日期派生。
- **G4（候选 U717–U726）**：序列化往返/默认无 debugSettings/裁剪/孤儿丢弃；后端带 debugSettings 编译保存普通运行不受影响（黑盒）；调试命中不回归。
- **G5（候选 U727–U742）**：InMemory ring 语义不回归；PgDeliveryStore record/list 倒序/clear/ring 200 淘汰/跨"重启"（新 store 实例同库）可见；reset 清表；registry 两档装配；deliveries 端点 PG 档黑盒；迁移 025 上下 apply。
- 前端测试沿用 vitest 纯函数/静态守护纪律（无 jsdom，不引新依赖）；后端 PG 例用 `ATLAS_RUN_INTEGRATION=1` + DATABASE_URL 直连。

## 9. 原子提交序（feat/test/docs 分开，英文 message，不 push）

1. `docs(runtime): propose batch G contract (error i18n/openapi purge/silence edit/breakpoint persist/delivery pg)` — 本文 + docs/08 立项条 + docs/14 注记（部分取回不解除）+ docs/00 地图 + handoff 进度 + CHANGELOG。
2. G1：`feat(graph): add condition eval error codes and structured runtime failure codes` → `test(graph): cover runtime error codes (U663-U6xx)` → `feat(frontend): localize runtime failure errors by code`（含 locale/test）。
3. G2：`feat(openapi): add include_deleted listing and hard purge (memory + pg)`（含 store/路由/迁移?无迁移）→ `test(openapi): cover include_deleted and purge (U6xx)` → `feat(frontend): admin controls for deleted openapi imports`。
4. G3：`feat(monitoring): add silence update and lazy daily on-call rotation`（含迁移 026/002/双档/路由）→ `test(monitoring): cover silence edit and auto rotation (U6xx)` → `feat(frontend): silence editing and rotation interval UI`。
5. G4：`feat(frontend): persist breakpoints in graph debugSettings`（含序列化/store/Editor；后端白名单若需单独 `fix(graph): allow top-level debugSettings passthrough`）→ `test(frontend): breakpoint persistence round-trip (U6xx)`。
6. G5：`feat(message): add delivery store abstraction and postgres backend`（含迁移 025/002/registry/re-set）→ `test(message): cover pg delivery store (U6xx)`。
7. 收口：三道门实跑（后端全量 + PG 直连 + 前端 vitest/build/lint）→ 文档矩阵回填（03/04/06/09/12/13/14/00/08/CHANGELOG/handoff，含门数字）→ `docs(runtime): close out batch G with gate results`。

> handoff 进度可在每个后端原子后按惯例补小 docs 提交（参照打包 F），但不计入功能原子。

## 10. 契约同步矩阵（立项原子内完成骨架，收口回填门数字）

| 文档 | 立项时 | 收口时 |
|---|---|---|
| docs/08 | 加打包 G 立项条（范围/决策演进 G5/测试候选/基线门数字） | 加落码收口条（提交链/门数字/偏差/仍缓做） |
| docs/14 | D22/D24/D27/D28 + docs/17 遗留 加「部分取回、不解除」注记 | 补落码结果与剩余余部 |
| docs/00 | 文档地图加 docs/60 | 状态更新 |
| docs/03 | 记 Graph debugSettings 超集字段（立项占位） | 定稿 |
| docs/12 | 记新端点/参数/运行结果字段/交付 store 演进 | 定稿 |
| docs/04/06/09 | 前端落点、运行时错误通道、模块（message/deliveries.py）骨架 | 定稿 |
| docs/13 | — | §9 落码表回填正式 U 编号与文件 |
| CHANGELOG | [Unreleased] 加打包 G 条目（倒序） | 门数字 |
| handoff.md | Active work 加打包 G、进度 | 归档完成项、Recently shipped、门数字 |

## 11. 风险与回滚

- **运行结果超集字段**：errorCode/expressionErrorCodes 为纯新增，旧中文断言保留；风险在前端等长数组对齐错位——以"等长、缺省空串"纪律 + 单测兜底。
- **惰性轮换时钟**：多实例读到时可能重复推进——v1 接受（取模幂等、lastRotatedAt 取 now，最坏多走一步），分布式锁列非目标；测试用注入时钟固定。
- **断点持久化误暂停普通运行**：普通运行 body 无 debug，后端不读 debugSettings；以黑盒测试锁死。
- **DeliveryStore 重构面**：MessageService 投递路径是告警/通知主链路，必须保证 record 失败不阻断发送（store 异常 fail-safe，try/except 包裹，落码显式）；ring 淘汰 SQL 在高并发下可能短暂超 200，可接受（v1 不做强一致裁剪）。
- **迁移**：025/026 均为新增表/可空列，向前兼容；回滚＝drop 表/列＋回退代码，不影响既有数据。
