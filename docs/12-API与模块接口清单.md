# Atlas API 与模块接口清单

> **来源**：06 运行时代码示例（LoopState / HarnessAdapter 接口 / Web 适配器）+ 04 组件设计（节点接口 / 适配器分层 / 注册发现）+ 08 Demo 技术栈（FastAPI 入口）+ 02 架构（gRPC 统一接口）。本文为**接口契约清单**：模块间接口、服务接口、前端 API，供 AI 落码时对齐边界。
> **AI 使用提示**：实现模块时先对齐本清单的接口签名；与 06 代码示例冲突时以 06 原文为准，本清单为汇总视图。

## 1. 引擎内部接口（Python，依据 06 6.1-6.2）

### 1.1 Loop 状态（LoopState，原文 06 6.1）

```python
class LoopState(TypedDict):
    goal: str
    messages: list          # 对话/执行历史
    current_node: str
    observations: list      # 感知结果
    variables: dict         # 会话变量
    status: str             # running/paused/completed/error
    memory_id: str
```

### 1.2 Loop 节点函数签名（原文 06 6.1-6.2）

| 函数 | 签名 | 职责 |
|---|---|---|
| observe_node | `(state: LoopState) -> dict` | 调用 Harness 感知当前状态 |
| orient_node | `(state: LoopState) -> dict` | 分析数据，定位进展 |
| decide_node | `(state: LoopState) -> dict` | LLM 决策下一步；置信度 <0.6 → paused + request_human（原文 6.2） |
| act_node | `(state: LoopState) -> dict` | 执行工具/操作 |
| reflect_node | `(state: LoopState) -> dict` | 阶段性反思 |

### 1.3 LangGraph 主循环（原文 06 6.1）

```
graph = StateGraph(LoopState)
add_node: observe → orient → decide → act → reflect
edges: observe→orient
       orient→(conditional) should_continue
       decide→(conditional) should_act_or_wait
       act→(conditional) should_reflect_or_continue
```

## 2. Harness Gateway 接口（依据 06 6.4，Go 接口定义）

```go
type HarnessAdapter interface {
    ListCapabilities(ctx context.Context) ([]Capability, error)
    Execute(ctx context.Context, req ActionRequest) (ActionResult, error)
    Observe(ctx context.Context, req ObserveRequest) (Observation, error)
}

type ActionRequest struct {
    CapabilityName string
    Parameters     map[string]interface{}
    Context        map[string]interface{}
    Timeout        time.Duration
}

type ActionResult struct {
    Status       string          // SUCCESS / PARTIAL / FAILED
    Output       interface{}
    Screenshots  [][]byte
    Error        *StructuredError
}
```

> 说明：02 架构提到"统一 gRPC 接口"；Demo 阶段（08 7.2）用 Python/FastAPI 实现同构接口，Go 化留待 Phase 2（见 10 T5）。

## 3. 适配器/节点通用接口（依据 04 3.2 / 04 4.3 / 04 4.4）

### 3.1 节点统一接口（04 3.2 node Schema）

```
node:
  id, type, name, description
  config: object          # 节点类型专属配置
  inputs: [{name, source, required, default}]
  outputs: [{name, type}]
  retry: {max_retries, backoff, timeout, on_error}
```

### 3.2 工具注册（04 4.3 tool Schema + 04 4.4 注册发现）

```
注册: 适配器启动 → 向 Harness Gateway 声明能力与工具列表
发现: 编辑后台 → 网关查询可用适配器 → 画布可用
健康: 适配器定期心跳，不可用置灰
tool: {id, name, description, adapter_id, action,
       input_schema, output_schema, permission, timeout, retry_policy, is_idempotent}
注（2026-09-16，04 §4.9）：v1 落码的 Capability（harness/base.py）字段为 name/description/action/
input_schema/output_schema/permission/timeout/is_idempotent（id/retry_policy 未落）；
input_schema/output_schema 为 JSON Schema 子集 dict，Capability 构造期按白名单校验形状（未知 keyword ValueError）。
```

### 3.3 Web 适配器三层定位（06 6.5）

```
click(element_desc, selector=None):
  层1 精确选择器（selector, timeout=3000）
  层2 视觉语义定位（screenshot → omni_parser.parse → llm_match，confidence>0.7 → click）
  层3 LLM 全图推理（vision_llm.analyze → 坐标 → click）
  全部失败 → failed("元素未找到，三层定位均失败")
```

### 3.4 API 适配器（通用 HTTP，04 §4.6 / 06 §6.6）

```python
# src/atlas/httpapi/service.py（进程内，channel 包 httpapi，adapter_id="http"）
class HttpApiClient:
    def __init__(self, base_url="", default_headers=None, client: httpx.Client | None=None)
    @classmethod
    def from_env(cls)  # ATLAS_HTTPAPI_BASE_URL / _HEADERS(JSON) / _TOKEN(Bearer)
    def request(self, method="GET", url="", headers=None, body=None,
                timeout=30.0) -> dict
    # → {"status": int, "headers": dict, "body": object|str}
    # 传输层异常 → StructuredError HTTP_TIMEOUT / HTTP_CONNECT_ERROR（不抛出）

# adapter.py：HttpApiHarnessAdapter(HarnessAdapter)
#   单能力 request（permission=write, is_idempotent=false）
#   节点 config.params 插值后为 JSON：{method,url,headers,body,timeout}
#   缺 url/坏 method/坏 headers/坏 timeout → MISSING_PARAMETER/INVALID_PARAMETER
```

### 3.5 数据适配器 / 消息适配器（04 §4.7/§4.8，06 §6.7）

```python
# src/atlas/database/service.py（进程内，channel 包 database，adapter_id="database"）
class DatabaseAdapterError(Exception):  # .code: DB_NOT_CONFIGURED / DB_SQL_ERROR / MISSING_PARAMETER / INVALID_PARAMETER

class DatabaseClient:
    def __init__(self, engine: Engine, url: str = "", demo: bool = False)
    @classmethod
    def from_env(cls)             # ATLAS_DATABASE_URL；scheme 白名单 postgresql+psycopg/sqlite；未配置返 None
    @staticmethod
    def demo_engine(seed: bool = True)  # sqlite:///:memory: + StaticPool，建 orders 表 seed 两笔
    def query(self, sql, params=None, limit=500) -> dict
    # → {"columns": [...], "rows": [{...}], "row_count": int, "truncated": bool}
    # PG: execution_options(postgresql_readonly=True)，结束 rollback；SQLAlchemyError → DB_SQL_ERROR（URL 脱敏）
    def execute(self, sql, params=None) -> dict   # commit → {"rowcount": int}
    @property
    def masked_url(self) -> str                  # render_as_string(hide_password=True)

# adapter.py：DatabaseHarnessAdapter(HarnessAdapter)
#   双能力 database/query（read, is_idempotent=true）/ database/execute（write）
#   节点 config.params 插值后为 JSON：query {sql,params?,limit?} / execute {sql,params?}

# src/atlas/message/service.py（channel 包 message，adapter_id="message"）
class MessageSendError(Exception):  # .code: MISSING_PARAMETER / INVALID_PARAMETER

class MessageService:
    def send(self, channel: str, to, subject: str, body: str) -> dict
    # to 展平为数组（字符串或数组，上限 20；channel=="email" 须含 @）
    # → {"id": uuid4, "channel", "to": [...], "subject", "body", "sent_at": iso8601}；仅记录，不投递
    def list(self) -> list[dict]
    def reset(self) -> None
    # 进程内列表，重启即失

# adapter.py：MessageHarnessAdapter(HarnessAdapter)
#   单能力 message/send（permission=write, is_idempotent=false）
#   节点 config.params 插值后为 JSON：{channel,to,subject,body}
```

### 3.6 流程模板库（04 §5.10，06 §6.8）

```python
# src/atlas/template/catalog.py（随代码发布的只读内置目录，无状态/无存储）
class TemplateMeta(BaseModel):
    id: str            # kebab-case，目录内唯一
    name: str
    description: str
    tags: list[str]
    graph: dict        # 完整 version 1 Graph JSON（节点 id 固定，加载不重映射）

TEMPLATES: tuple[TemplateMeta, ...]   # v1 恰好 5 个（04 §5.10 清单）

def list_templates() -> list[TemplateMeta]: ...   # 目录常量直接返回
def get_template(template_id: str) -> TemplateMeta | None: ...   # 未知 id 返 None（REST 映射 404）
# graphs.py：refund_template_graph()（退款 golden 图单一事实源，llm/nl_generate.py 导入）
#            + http/sql/审批四个模板图构造函数；每个模板 graph 必须过 parse_graph/validate_graph
```

### 3.7 操作录制与回放（04 §5.11，06 §6.9）

```python
# src/atlas/recording/cases.py
class RecordStep(BaseModel):
    node_id: str
    node_type: str
    output: dict           # 该节点最后一次 node_end 产出

class RecordingCase(BaseModel):
    id: str                # "rec-{自增}"
    name: str              # 1-100 字
    graph_id: str = ""     # M9：所属图 id（创建请求已收，M9 起落库；旧用例为空串，发布门禁不入选）
    graph: dict            # 录制时图快照（graph_definition version 1，冻结）
    inputs: dict | None
    steps: list[RecordStep]
    status: str
    created_at: str        # ISO8601 UTC

class RecordingStore:  # 进程内单例；重启清空（11 S1 持久化），reset 不清除（同 feedback）
    def add(self, *, name, graph_id, graph, inputs, steps, status) -> RecordingCase: ...  # M9 起 graph_id 落库
    def list(self) -> list[RecordingCase]: ...
    def get(self, case_id: str) -> RecordingCase | None: ...
    def delete(self, case_id: str) -> bool: ...

# src/atlas/recording/gate.py（M9，发布前批量回放门禁；04 §5.11 末 / 03 release_gate）
def run_release_gate(*, graph_id: str, draft: dict, cases: list[RecordingCase],
                     services, registry, graph_resolver) -> dict: ...
    # registry / graph_resolver 由 API 层注入（避免 recording → api 反向依赖，与 subgraph
    # 重入同一注入口径）；筛选 case.graph_id == graph_id（旧用例空串不入选）；逐例对 draft 走标准 run_graph
    # （preset_approvals/collect_steps/compare 全部复用 replay.py，无录制专用运行时），
    # 返 GateReport {graph_id, target:"draft", total, passed, failed, skipped, blocked,
    #                cases:[{case_id,name,matches,replay_status,note?}]}；
    # total=0 → skipped=true 不阻塞（明示未覆盖）；total>0 任一不匹配 → blocked=true

# src/atlas/recording/replay.py（纯函数）
def normalize(value, tool: str | None = None): ...
    # 深拷贝后递归剔除易变值：token、sent_at；同层含 sent_at 的 dict 删 uuid id；
    # tool == "http/request" 时 result.headers 删 date
def preset_approvals(steps) -> dict: ...           # {node_id: "approved"|"rejected"}，合并进回放 inputs.approvals
def dedupe_steps(steps) -> list[RecordStep]: ...   # node_id 重复保末（parallel 双 node_end/loop 重访）
def compare(baseline, replay_steps, *, tools_by_node, baseline_status, replay_status) -> dict: ...
    # {matches, baseline_status, replay_status,
    #  steps: [{node_id, match, note, diff_keys?}]}；序列先比对，值按 normalize 后深等
def collect_steps() -> tuple[Callable, Callable]: ...  # 返回 (emit, take_steps)；回放 run_graph 收集 node_end 保末
```

### 3.8 单步调试与断点（04 §5.12，06 §6.10）

```python
# src/atlas/debug/sessions.py
class DebugStopped(Exception):
    node_id: str            # resume action=stop / reset 时，从暂停点冒泡终止 run

class DebugSession:
    session_id: str         # "dbg-<uuid4>"，同时是暂停 token（同会话同时刻仅一个活动暂停）
    graph_id: str
    breakpoints: dict[str, str | None]   # node_id -> 条件表达式（None=无条件断点）
    step_mode: bool         # 初始 True；resume continue 置 False
    cancelled: bool         # resume stop 或 broker.reset 置 True
    last_condition_error: str | None     # 条件断点最近一次求值异常（fail-safe 不命中，不抛出）
    # 暂停原语（会话锁串行化 parallel 同时到达）：
    def request_pause(self, *, node_id, node_type, reason, globals, outputs) -> str: ...  # 深拷贝快照、清 Event、返 token
    def wait(self, timeout: float | None = None) -> str: ...   # 阻塞至 resolve；返 action
    def resolve(self, token: str, action: str) -> bool: ...    # "step"|"continue"|"stop"，首决生效；未知/已决 False
    def get(self, token: str) -> dict | None: ...              # 暂停投影（token/node_id/node_type/reason）

class DebuggerBroker:       # 进程内单例；重启清空（持久化随 11 S1/14 D19/D20）
    def create(self, *, graph_id: str, breakpoints: list[dict]) -> DebugSession: ...
    def list_pending(self) -> list[dict]: ...                  # 当前活动暂停投影（GET /api/debug）
    def get_session(self, token: str) -> DebugSession | None: ...
    def reset(self) -> None: ...                               # 全部会话 cancelled=True + set Event 放行

# src/atlas/debug/controller.py
class DebugController:
    def __init__(self, session: DebugSession, emit: Callable): ...
    def before_node(self, node, state) -> None: ...
    # 统一执行器在 emit(node_start) 之后、节点分派之前调用：
    # cancelled -> raise DebugStopped；step_mode -> reason="step"；
    # 否则查断点表：无表达式 "breakpoint"；有表达式经 evaluate_expression(expr, {"global": globals, **outputs})
    # 求值 True -> "condition"；求值异常记录 last_condition_error 且不暂停（fail-safe）；
    # 命中 -> emit paused 帧（含深拷贝 globals/outputs）-> wait ->
    # step 保持 / continue 清 step_mode / stop 置 cancelled 并 raise DebugStopped；唤醒后复检 cancelled。

# graph/loader.py 注入（默认 None = 零开销，非调试/回放路径不变）
def compile_graph(graph, *, ..., debug_controller=None, tracer=None, graph_version: str | None = None): ...
def run_graph(graph, *, ..., debug_controller=None, tracer=None,
              graph_version: str | None = None, _parent_span=None) -> dict: ...
# M10：未传 tracer 时 run_graph 自建 Tracer（root run span），result 带 traceId/traceTree；
# _execute_subgraph 重入复用同一 tracer、传 _parent_span=subgraph span（子图 span internal）；
# _execute_subgraph 重入不传 debug_controller（子图整段执行，内部不暂停）

# tracing/tracer.py（M10，纯 stdlib；04 §5.15/06 §6.15/03 trace_span）
class Span(BaseModel):            # traceId/spanId/parentSpanId/name/kind/startedAt/durationMs/status/graphVersion/attrs/internal
    def to_dict(self, include_internal: bool = True) -> dict: ...
class Tracer:
    def __init__(self, *, graph_id: str, graph_version: str | None = None): ...   # 建 root run span
    @contextmanager
    def span(self, name: str, *, kind: str, parent: "Span | None" = None,
             internal: bool = False, **attrs) -> Iterator[Span]: ...
    @property
    def trace_id(self) -> str: ...
    def current_context(self) -> dict: ...          # {traceId, spanId, parentSpanId}（供 SSE 帧注入）
    def to_tree(self, include_internal: bool = True) -> dict: ...   # 嵌套父子树；False 折叠 subgraph 内部
# contextvars 记当前 span（同线程节点→工具就近取父）；Tracer 经参数显式透传（后台线程，06 §6.12）
```

### 3.9 基础监控告警（04 §5.13，06 §6.11）

```python
# src/atlas/monitoring/records.py
class NodeResult(BaseModel):
    node_id: str
    node_type: str
    status: Literal["success", "failed"]
    error: str | None = None

class RunRecord(BaseModel):
    id: str                     # "run-N"
    graph_id: str
    mode: Literal["sync", "stream"]
    status: Literal["completed", "error"]
    started_at: str             # ISO 8601 UTC
    finished_at: str
    duration_ms: float
    nodes: list[NodeResult]
    error: str | None = None    # 仅运行级异常
    trace_id: str = ""          # M10：本 run 的 traceId（可空，向后兼容；debug/回放/subgraph 重入不写）
    resolved_version: int | None = None   # M9：入站 event 运行经 Router 钉住的发布版本；手动运行为 None
    business: "BusinessOutcome | None" = None  # M9：业务结果提取（无业务结果时 None）

class MonitoringStore:          # 进程内单例；重启清空（持久化随 11 S1/14 D28）
    def record_run(self, *, graph_id, mode, status, started_at,
                   nodes: list[NodeResult], error: str | None = None,
                   trace_id: str = "",
                   resolved_version: int | None = None,
                   business: dict | None = None) -> RunRecord: ...   # M9 纯超集透传
    # 单锁内：deque(maxlen=200) 追加 → 健康（completed 且无失败节点）判定 →
    # 按图 streak 维护（健康归零）→ evaluate_rules → 同 (rule_id,graph_id)
    # 合并非 resolved 最新告警（count++/last_seen/last_run_id）否则新建
    def list_runs(self, graph_id: str | None = None, limit: int = 50) -> list[RunRecord]: ...
    def list_alerts(self, status: str | None = None) -> list[Alert]: ...     # 新→旧
    def acknowledge_alert(self, alert_id: str) -> Alert | Literal[False] | None: ...
    # None=未知 id；False=非 open（重复迁移）；open→acknowledged
    def resolve_alert(self, alert_id: str) -> Alert | Literal[False] | None: ...
    # None=未知 id；False=已 resolved；open/acknowledged→resolved
    def get_rules(self) -> RuleConfig: ...
    def update_rules(self, raw: dict) -> RuleConfig: ...   # validate_rules 聚合错误，非法 ValueError
    def reset(self) -> None: ...                           # 清空 runs/alerts/streak，规则恢复默认

# src/atlas/monitoring/metrics.py（纯函数）
def extract_node_results(graph: dict, outputs: dict) -> list[NodeResult]: ...
# node_id→type 取自图定义；失败两形状：output.status=="failed"（parallel/subgraph 折叠）
# 或 output.result.status=="FAILED"（ActionResult）；error 取 error/result.message/result.code
def percentile(values: list[float], p: float) -> float | None: ...   # nearest-rank，空集 None
def summarize(runs: list[RunRecord]) -> dict: ...
# {total, healthy, unhealthy, success_rate, p50, p95,
#  per_graph:[{graph_id, ...同口径}], failed_nodes:[{node_id,node_type,count,last_error,last_seen}],
#  M9 增 business:{auto_refund_rate, manual_escalation_rate, refund_amount_diff_rate,
#                  per_graph:[{graph_id, ...三率, samples}],
#                  per_version:[{graph_id, resolved_version, ...三率, samples}]}
#  业务三率分母＝有业务结果的 run，样本 0 为 null}

# src/atlas/monitoring/business.py（M9 纯函数；04 §5.13 末 / 03 business_metrics）
def extract_business(graph: dict, outputs: dict) -> dict | None: ...
# BusinessOutcome {auto_refunded: bool（任一 tool_call result.status=="refunded"）,
#   manual_escalated: bool（=="human_review"）, expected_amount: number?（trigger payload.amount）,
#   refunded_amount: number?（退款 result.amount；demo shop 不回金额→回退 expected）,
#   amount_diff: bool}；无业务结果返 None

# src/atlas/monitoring/alerts.py（纯函数 + 模型）
class RuleConfig(BaseModel):
    run_error:            RuleToggle
    node_failed:          RuleToggle
    consecutive_failures: ConsecutiveRule      # enabled + threshold(1..200, 默认 3)
    failure_rate:         FailureRateRule       # enabled + window(默认 20)/min_samples(默认 5)/rate(默认 0.5)
# M9：rollout_gate 阈值不在全局 RuleConfig，随每图 RolloutConfig.gate（routing 包）

# M9 Alert 纯超集字段（仅 rollout_gate 告警携带，其余规则无此字段）：
#   rule_id 枚举增 "rollout_gate"（critical）；
#   action: {type:"rollback", from_version:int, to_version:int, reason:str, actor:"auto"|"manual"} | None

def validate_rules(raw: dict) -> list[str]: ...           # 中文错误信息（enabled bool、整数 1-200、rate 0-1）
def evaluate_rules(*, record, healthy, recent_by_graph: list[RunRecord],
                   rules: RuleConfig, streak: int) -> list[AlertEvent]: ...
# 触发意图（不含合并）：run_error（status=="error"，critical）；node_failed（有失败节点，warning）；
# consecutive_failures（streak 达 threshold 且规则启用，critical；首达后每次失败继续意图→由 store 合并）；
# failure_rate（该图近 window 次含本次样本≥min_samples 且不健康占比≥rate，critical）
```

埋点只在 `src/atlas/api/main.py`：sync `/run` 包装（异常记录 error 后原样 raise，保持 500），`/run/stream` worker 非 debug 时以 emit 包装收集 node_end output，`__result__` 记 completed、`__error__` 记 error（部分节点）、`__stopped__`（debug）不记。录制 replay 端点与 subgraph 重入零改动。**M9 起两个真实运行入口在 `record_run` 之后调用 `routing.gate.evaluate_after_run(services, record)`（门控自动回滚，见 §3.12）；replay/debug/subgraph 重入同口径不触发。**

### 3.10 多租户与权限（04 §5.14，06 §6.12）

```python
# src/atlas/iam/principals.py
class Role(str, Enum):
    VIEWER = "viewer"; OPERATOR = "operator"; ADMIN = "admin"

class Principal(BaseModel):
    tenant_id: str            # "t1" | "t2"（v1 种子）
    tenant_name: str
    username: str
    display_name: str
    role: Role

SEED_TENANTS: dict[str, Tenant]            # t1 演示企业 A / t2 演示企业 B
SEED_USERS: list[TenantUser]               # admin-a/operator-a/viewer-a/admin-b，明文密码仅 Demo
def authenticate(username: str, password: str) -> Principal | None: ...
def can(role: Role, capability: Literal["read", "operate", "administer"]) -> bool: ...
# viewer: read；operator: read+operate；admin: read+operate+administer；feedback 提交对全部登录者放行

# src/atlas/iam/sessions.py
class SessionStore:                         # 进程内单锁；重启即失
    def issue(self, principal: Principal) -> str: ...          # "sess-"+uuid4().hex
    def principal_for_token(self, token: str) -> Principal | None: ...
    def revoke(self, token: str) -> None: ...
    def reset(self) -> None: ...

# src/atlas/iam/registry.py
class TenantServices(BaseModel):           # 每租户一套进程内实例（id 计数各自从 1 起）
    graph_store: GraphStore
    recording_store: RecordingStore
    feedback_store: FeedbackStore
    message_service: MessageService
    approval_broker: ApprovalBroker
    debug_broker: DebuggerBroker
    monitoring: MonitoringStore
    routing_store: RoutingStore            # M9：每租户一个 rollout/路由状态（进程内，同 TaskStore 先例）

class TenantRegistry:
    def get(self, tenant_id: str) -> TenantServices: ...       # 惰性创建
    def reset_tenant(self, tenant_id: str) -> None: ...
    # graph.clear + broker/debug/monitoring/message/routing reset（规则回默认、计数器不重置）；
    # recording_store/feedback_store 不动（沿用「reset 不清除」）

# src/atlas/iam/deps.py（FastAPI 依赖，非中间件）
def get_principal(request: Request) -> Principal: ...          # Bearer 解析失败 → 401「缺少或无效的登录凭证」
def require(*capabilities) -> Callable: ...                    # 角色不足 → 403「当前角色无权执行此操作」
def services_for(principal: Principal) -> TenantServices: ...  # = TenantRegistry.get(principal.tenant_id)
```

全局单例（不分区，基础设施层）：模板目录、AdapterRegistry 与 demo 适配器实例、HttpApiClient/DatabaseClient 出向连接、DemoShopService、demo SQLite 种子。SSE worker 线程在请求线程内解析 TenantServices 后显式传入，不使用线程上下文变量。

### 3.11 交互卡片（M8 已落码 2026-09-18；04 §5.6 追加段 / 03 `card_template` / 19 §2.2.3/§2.3.2）

```python
# src/atlas/cards/catalog.py（随代码发布的只读内置目录，无状态/无存储，照 template 包）
class FieldBinding(BaseModel):
    label: str
    value: str                       # {{路径}} 模板，复用 graph.loader.interpolate
class FieldsSection(BaseModel):
    type: Literal["fields"]
    bindings: list[FieldBinding]
class FormSection(BaseModel):
    type: Literal["textarea", "input"]
    name: str                        # action.output 以 {{form.<name>}} 引用
    label: str | None = None
    required: bool = False
    default: str = ""
class CardAction(BaseModel):
    id: str                          # approve / reject
    label: str
    style: Literal["primary", "danger", "default"] = "default"
    output: dict                     # {"decision": "approved"|"rejected", "comment"?: "{{form.comment}}"}
    channels: dict | None = None     # 如 {"email": {"render": "link"}}
class CardFallback(BaseModel):
    im: dict | None = None           # {"detailUrl": "/approvals/{token}"}
    email: dict | None = None        # {"timeoutHint": true}
class CardTemplate(BaseModel):
    id: str                          # kebab-case 目录内唯一（v1: refund-approval）
    name: str
    channels: list[Literal["web", "im", "email"]]
    sections: list[FieldsSection | FormSection]
    actions: list[CardAction]
    fallback: CardFallback | None = None

CARDS: tuple[CardTemplate, ...]
def list_cards() -> list[CardTemplate]: ...
def get_card(card_id: str) -> CardTemplate | None: ...   # 未知 id 返 None（REST 404 / 编译 422）

# src/atlas/cards/render.py（纯函数；bindings 复用 graph.loader.interpolate 同一插值/缺失语义）
def render_card(card: CardTemplate, context: dict, *, token: str,
                channel: Literal["web", "im", "email"], approver: str = "",
                timeout_seconds: int | None = None) -> dict: ...
    # context＝审批挂起时快照（trigger/globals/visibleAt 内 outputs）
    # web  ＝ {channel:"web", cardId, name, fields:[{label,value}], form:[{type,name,label,required,default}],
    #          actions:[{id,label,style}], token, approver, timeoutSeconds}（前端 CardRenderer 渲染）
    # im   ＝ {channel:"im", text, buttons:[{id,label,url}], detailUrl}（纯文本+两按钮回调 URL）
    # email＝ {channel:"email", subject, html, links:[{id,label,url}]}（只读 HTML + 带 token 两链接）
    # 链接为前端路由 GET URL（/approvals/{token}?decision=…），GET 不产生决策副作用（防邮件预取）
def map_action_output(card: CardTemplate, action_id: str, form_values: dict) -> dict: ...
    # 按 action.output 映射：{{form.<name>}} 回填；返 {decision, comment?}；
    # 未知 action / decision 非 approved|rejected / 缺必填 form → ValueError（REST 422）
def to_message_params(rendered: dict, *, to) -> dict: ...
    # im/email 渲染产物 → message/send 入参 {channel,to,subject,body}（v1 不自动外发，随 D20/D24）
```

### 3.12 路由与灰度（M9 已落码 2026-09-18 批 1-4＋收口，后端 602/前端 396；04 §5.16 / 06 §6.17 / 03 `rollout_config`·`route_decision` / 19 §2.3.3·§2.5）

```python
# src/atlas/routing/models.py（pydantic；v1 无 when 表达式解析器，字符串条件落为结构化字段）
class TriggerEvent(BaseModel):
    channel: Literal["api", "webhook", "im", "embed"] = "api"
    payload: dict = {}
class InternalRule(BaseModel):  to: Literal["internal"]; tenants: list[str]
class BucketRule(BaseModel):    to: Literal["lowValueBucket"]; field: str; op: Literal["<="];
                                value: float; percent: int = 100
class CanaryRule(BaseModel):    to: Literal["canary"]; percent: int          # 1..100
class FullRule(BaseModel):      to: Literal["full"]
class GateMetric(BaseModel):    id: Literal["run_error_rate", "manual_escalation_rate",
                                            "refund_amount_diff_rate"]
                                threshold: float; compareWith: int | None = None
                                min_samples: int | None = None
class GateConfig(BaseModel):    observe_minutes: int = 60; auto_rollback: bool = True
                                min_samples: int = 3; metrics: list[GateMetric]
class RolloutConfig(BaseModel):
    strategy: Literal["progressive"] = "progressive"
    rules: list[InternalRule | BucketRule | CanaryRule | FullRule]
    gate: GateConfig
    in_flight_policy: Literal["pin-to-version"] = "pin-to-version"

# src/atlas/routing/router.py（纯函数，无 IO；U55 钉死）
def resolve_version(*, graph_id: str, stable: int | None, candidate: int | None,
                    tenant: str, event: TriggerEvent | None,
                    config: RolloutConfig | None) -> tuple[int | None, str]: ...
    # 固定序 internal→lowValueBucket→canary→full；返 (version, segment)
    # canary 桶键 payload.order_id 缺省 payload.id；
    #   int(sha256(f"{graph_id}:{bucket_key}").hexdigest(), 16) % 100 < percent → candidate
    # 桶键缺失/非字符串 fail-safe 落 stable（segment="fallback"）；未配置/无 candidate → stable

# src/atlas/routing/store.py（每租户一个；进程内，reset 清空；多实例同步随 D6/D10b/D32）
class RoutingStore:
    def configure(self, graph_id: str, raw: dict) -> RolloutConfig: ...   # 非法 ValueError（REST 422）
    def start(self, graph_id: str, versions: list[int]) -> dict: ...      # 取最新两版；不足两个 ValueError（409）
    def promote(self, graph_id: str) -> dict: ...                         # canary→full，唯一放量路径（仅手动）
    def rollback(self, graph_id: str, *, reason: str, actor: Literal["auto","manual"]) -> dict: ...
        # 任意态→rolled_back，candidate 撤流 stable 接全量；幂等（重复回滚不报错）
    def resolve(self, graph_id: str, *, tenant: str, event: TriggerEvent) -> tuple[int|None, str]: ...
        # 委托 router.resolve_version 并累加 traffic 计数 {stable,candidate,segments{...}}
    def snapshot(self, graph_id: str) -> dict: ...
        # {status:"idle"|"canary"|"full"|"rolled_back", stable, candidate, started_at,
        #  rolled_back_at, rollback_reason, config, traffic}

# src/atlas/routing/gate.py（API 层在 record_run 之后调用；monitoring 不反向依赖 routing）
def evaluate_after_run(services: TenantServices, record: RunRecord) -> None: ...
    # 仅 record.resolved_version 为某图 candidate 且 status=canary 且 gate.auto_rollback 时求值；
    # 按 finished_at 取 observe_minutes 窗内该图 candidate 运行；样本 < min_samples 不判；
    # run_error_rate 复用 metrics.is_healthy；业务两率取 business 段（样本 0 为 null 不判）；
    # 任一越阈 → routing_store.rollback(actor="auto") +
    #   monitoring 产 rollout_gate critical 告警（Alert.action 携带 from/to/reason/actor）；
    # 无任何自动 promote 代码；回滚只切新流量，不改外部已发生事实。
```

## 4. 记忆检索接口（依据 06 6.2 / 05 2.3）

```python
memory_retriever.query(goal: str, recent_messages: list) -> list
# 依据 memory_config 分层检索：working_memory(Redis) / summary_memory(PostgreSQL)
#   / fact_memory(pgvector, confidence≥0.85) / case_memory(相似度阈值) / preference_memory
```

## 5. 后端服务 API（依据 08 7.2 /api/main.py FastAPI 入口）

> 以下路径为按 Demo 需求推导的 REST 端点清单，字段以 03/04/05 Schema 为准；正式定义待落码时随 OpenAPI 生成。
>
> **鉴权四档（2026-09-16，04 §5.14）**：除标注「公开」者外，端点必须携带 `Authorization: Bearer <sess-token>`，缺失/无效 → 401「缺少或无效的登录凭证」；角色不足 → 403「当前角色无权执行此操作」；访问不存在于本租户的对象（含他租户审批/调试 token）→ 404。**公开**：`POST /api/auth/login`、`GET /api/health`、静态托管、`GET /demo/shop`、`/api/demo/**`（模拟外部系统，沿用 X-Demo-Token/demo 登录自带认证，不经平台鉴权）。**viewer+**（全部登录角色可读）：所有 GET 业务端点（graphs/templates/adapters/recordings/monitoring/alerts/approvals/debug/messages）+ `POST /api/feedback`（人人可提交）。**operator+**：POST/PUT/DELETE/POST 运行类——graphs 保存、compile、run、run/stream、nl/generate、recordings 写/删/replay、approvals 决策、debug resume、alerts acknowledge/resolve、publish 与 release-gate、rollout 配置/start/promote/rollback（M9，对齐发布权限）。**admin only**：`PUT /api/monitoring/rules`、`POST /api/demo/reset`、`GET /api/feedback`。所有业务数据按 token 推断的租户分区（03 各结构租户注记）。

| 方法 | 路径 | 功能 | 关联 |
|---|---|---|---|
| POST | /api/auth/login | 登录换会话（公开）：请求体 `{username, password}`，坏凭证 401「用户名或密码错误」；200 返回 `{token:"sess-<uuid hex>", principal:{tenant_id, tenant_name, username, display_name, role}}`（identity_session） | identity_session |
| GET | /api/auth/me | 回显当前 Bearer 会话的 Principal（viewer+） | identity_session |
| POST | /api/auth/logout | 吊销当前 token（viewer+；幂等，204/200） | identity_session |
| POST | /api/graphs | 保存 Graph 定义（DSL）：总是新建 graph-N（首次建图） | node_schema / graph_definition |
| PUT | /api/graphs/{id} | 【operate，M9】同图迭代覆盖 latest 草稿（`GraphRepository.update_draft`）：body 同 POST 的 SerializedGraph、过 parse_graph 校验，**不新建 id、不动不可变发布版**，刷 updated_at，返 `{id, version}`；图不存在/跨租户 → 404（KeyError）。前端编辑器对同一画布首次 POST 之后的运行/录制/发布统一走此端点（修复早期每次运行新建 graph-N 致录制用例与门禁 graph_id 错位） | graph_definition |
| GET | /api/graphs | 列出已保存图（`{items:[{id, node_count, updated_at}]}`，进程内存储；Phase 2 第六项，供 subgraph 节点选择器） | graph_definition |
| GET | /api/graphs/{id} | 读取 Graph（latest 草稿；`?releaseVersion=N` 读指定发布版本快照，未知/未发布 404） | — |
| POST | /api/graphs/{id}/publish | 【operate】发布当前草稿为不可变版本（M6 立项 2026-09-17，docs/20 §4.1 / ADR T19）：冻结 Graph JSON + subgraph 钉版（递归，子图未发布则先递归发布其草稿为 v1），返回 `{id, releaseVersion}`；releaseVersion 从 1 递增、已发布版本只读。**M9 起请求体从无改为可选 `{gate?: boolean}`**：gate=true 先跑发布门禁（下行 release-gate），blocked → **409 带完整 GateReport 且不产新版本** | graph_definition / release_gate |
| GET | /api/graphs/{id}/versions | 【read】已发布版本列表（M6）：`{items:[<releaseVersion>]}`（升序）；未发布过 → 空列表 | graph_definition |
| POST | /api/graphs/{id}/release-gate | 【operate，M9 已落码 2026-09-18】发布前批量回放门禁（只跑门禁不发布，D26 部分取回）：对当前 latest 草稿逐例重放 `case.graph_id==id` 的录制用例（复用 replay.compare），返回 GateReport `{graph_id, target:"draft", total, passed, failed, skipped, blocked, cases:[{case_id,name,matches,replay_status,note?}]}`；total=0 时 skipped=true 不阻塞（明示未覆盖），total>0 任一不匹配 blocked=true；图不存在 404 | release_gate / recording_case |
| GET | /api/graphs/{id}/rollout | 【read，M9】灰度配置与运行态：`{status, stable, candidate, started_at, rolled_back_at, rollback_reason, config, traffic:{stable,candidate,segments:{internal,lowValueBucket,canary,full,fallback}}}`；从未配置 → status="idle" 空态 | rollout_config |
| PUT | /api/graphs/{id}/rollout | 【operate，M9】存 RolloutConfig（strategy/rules 四段/gate/inFlightPolicy，形状见 §3.12），**只存配置不启动**；非法值（percent 越界、阈值非 0-1、段顺序/形状错）422 中文聚合；图不存在 404 | rollout_config |
| POST | /api/graphs/{id}/rollout/start | 【operate，M9】idle→canary：取最新两个发布版（stable=前一版、candidate=最新版）；发布版不足 2 个 → 409「至少需要两个发布版本才能开始灰度」；未配置 RolloutConfig 409；返回 rollout 快照 | rollout_config |
| POST | /api/graphs/{id}/rollout/promote | 【operate，M9】canary→full：**唯一放量路径，仅手动**（全仓无自动 promote）；状态非 canary 409；返回快照 | rollout_config |
| POST | /api/graphs/{id}/rollout/rollback | 【operate，M9】任意态→rolled_back（candidate 撤流、stable 接全量）；自动门控（actor="auto"）与手动（actor="manual"）同一幂等函数，重复回滚幂等 200；body 可选 `{reason?}`；返回快照 | rollout_config / business_metrics |
| GET | /api/templates | 内置流程模板目录列表（Phase 2 能力项，只读代码常量；返回 `{items:[{id,name,description,tags,node_count}]}`，不含 graph；不受 reset 影响） | template_catalog |
| GET | /api/templates/{id} | 模板详情：完整 TemplateMeta 含 `graph`（可直接载入画布/保存为新图）；未知 id 404 | template_catalog / graph_definition |
| GET | /api/cards | 内置交互卡片目录（M8 已落码 2026-09-18，viewer+；只读代码常量，不受 reset 影响）：`{items:[{id,name,channels,sections,actions,fallback?}]}`（CardTemplate 投影，供配置态 card-select 与运行态渲染） | card_template |
| GET | /api/approvals/{token}/card | 审批卡片按渠道渲染（M8，viewer+）：`?channel=web\|im\|email`（缺省 web），返回 §3.11 对应渠道渲染产物；未知 token 404、该审批未配 cardTemplateId（无卡片）404、非法 channel 422；渲染上下文取挂起时快照（中断恢复后从帧重建）；**只读，不产生决策副作用**（邮件链接为 GET 落地页，决策一律走 POST） | card_template / human_approval |
| POST | /api/recordings | 录制用例入库（Phase 2 能力项）：请求体 `{name(1-100), graph_id, inputs, steps:[{node_id,node_type,output}], status}`，服务端按 graph_id 取已保存图原始 JSON 作**快照**存入（不重新执行；graph 未知 404、steps 形状非法 422、空 steps 422），201 返回完整 RecordingCase；**M9 起 graph_id 一并落库**（纯超集，旧用例该字段为空串） | recording_case |
| GET | /api/recordings | 录制用例列表：`{items:[{id,name,graph_id,node_count,step_count,status,created_at}]}` 投影（M9 起含 graph_id；不含 graph/steps）；进程内存储，reset 不清除 | recording_case |
| GET | /api/recordings/{id} | 录制用例详情：完整 RecordingCase（含 graph 快照与 steps）；未知 id 404 | recording_case |
| DELETE | /api/recordings/{id} | 删除录制用例；未知 id 404 | recording_case |
| POST | /api/recordings/{id}/replay | 回放用例：对快照走标准 run_graph（审批决策从 baseline 抽为 inputs.approvals 预置，不挂起），比对操作序列与逐节点归一化产出；返回 `{matches, baseline_status, replay_status, steps:[{node_id,match,note,diff_keys?}]}`；回放异常折叠 replay_status="failed"/matches=false（不抛 500）；未知用例 404 | recording_case |
| POST | /api/debug/{token}/resume | 恢复调试暂停（Phase 2 能力项）：请求体 `{action:"step"|"continue"|"stop"}`；step=执行当前节点并在下一节点前再停、continue=退出逐节点仅断点停、stop=取消运行（随后收到 stopped 帧、无 result）；首决生效 200，未知 token 404、对已恢复暂停重复提交 409；进程内会话重启即失 | debug_session |
| GET | /api/debug | 列出当前活动调试暂停：`{items:[{token, node_id, node_type, graph_id, reason}]}`（Phase 2 能力项，进程内） | debug_session |
| GET | /api/monitoring/metrics | 监控指标聚合（Phase 2 能力项）：`{total,healthy,unhealthy,success_rate,p50,p95,per_graph:[{graph_id,…}],failed_nodes:[{node_id,node_type,count,last_error,last_seen}]}`；口径=ring 内全部保留运行（≤200），空集 success_rate/p50/p95 为 null；进程内、重启即失。**M9 起增 `business` 段**：`{auto_refund_rate, manual_escalation_rate, refund_amount_diff_rate, per_graph:[{graph_id,…三率,samples}], per_version:[{graph_id,resolved_version,…三率,samples}]}`（分母＝有业务结果的 run，样本 0 为 null；契约 03 `business_metrics`） | monitoring / business_metrics |
| GET | /api/monitoring/runs | 运行记录列表：`?graph_id=&limit=`（默认 50、1-200 截断，新→旧），返回 `{items:[RunRecord]}`；仅真实运行（debug/回放/子图重入不计） | monitoring |
| GET | /api/monitoring/rules | 读取四内置规则当前配置 RuleConfig（默认全开，连续阈值 3、窗口 20/最小样本 5/失败率 0.5） | monitoring |
| PUT | /api/monitoring/rules | 全量替换规则配置；非法值（enabled 非 bool、阈值/窗口/样本非 1-200 整数、rate 非 0-1）422 中文聚合错误；`/api/demo/reset` 恢复默认 | monitoring |
| GET | /api/alerts | 告警列表：`?status=open\|acknowledged\|resolved`（缺省全部），`{items:[Alert]}` 新→旧 | monitoring |
| POST | /api/alerts/{id}/acknowledge | 确认告警（open→acknowledged）；未知 id 404、非 open 状态 409，中文 detail | monitoring |
| POST | /api/alerts/{id}/resolve | 关闭告警（open/acknowledged→resolved）；未知 id 404、已 resolved 409，中文 detail | monitoring |
| POST | /api/graphs/{id}/compile | DSL → LangGraph 编译（08 7.1 W7-W8）；**M6 起请求体可选 `releaseVersion`（缺省 latest）**。静态校验失败 422 体 `{"detail":[中文消息,...]}` 不变；**M2 起（2026-09-16 落码，fbd9f77）并列增机器可读定位侧车** `"locations":[{"index":number,"nodeId"?:"…","pointer"?:"/branches/0/expression"}]`——index 对齐 detail 下标、稀疏，pointer 为 RFC6901 相对该节点 config 根；图级错误无条目；契约见 03 `diagnostic`、06 §6.13，用例 U38。run/run/stream 经同一 compile_graph 路径，形状相同 | 02 Graph DSL |
| POST | /api/graphs/{id}/run | 编译并运行，返回状态/节点产出/执行轨迹；请求体 `{"inputs": {...}}`（M6 起可选 `releaseVersion`，缺省 latest；**M9 起可选 `event:{channel?,payload?}`——带 event 为入站触发，经 Router 三段分桶解析发布版本并按不可变快照运行（pin-to-version），无任何发布版 → 409；不带 event＝编辑器手动运行 latest 草稿，旧行为零回归；RunRecord 记 resolved_version**），inputs 同名键覆盖全局变量且整体作为 trigger 节点 webhook 载荷 `context.payload`（W9-W10 接入真实决策/适配器）；**不支持调试**——请求体含 `debug` 返回 422「单步调试仅支持流式运行 /run/stream」（Phase 2 能力项） | 02 Graph DSL / LoopState / route_decision |
| POST | /api/graphs/{id}/run/stream | SSE 流式运行（W9-W10；M6 起 body 可选 `releaseVersion`，缺省 latest；**M9 起同 sync 行可选 `event:{channel?,payload?}`，Router 分桶 + pin-to-version，无发布版 409，不带 event 走草稿零回归**）：事件 `node_start`/`node_end`/最终 `result`，供画布实时进度（验收标准 5）；**M10 起三帧为 19 §2.3.4 超集——node_start/node_end 另带 `traceId/spanId/parentSpanId`、run_end(result) 另带 `traceId/spanId/graphVersion`（只增字段、不改帧型，前端忽略未知字段零改动，完整 span 树不进 SSE）**；condition 节点的 node_end 事件 data 含 `{branch, target, evaluation, expression_errors}`（契约 04 §5.2），loop 节点含 `{mode, iterations, index, target, exitReason, expression_errors}`（契约 04 §5.3），parallel 节点扇出时 data 为 running 占位、joinTarget 的 node_end 前该产出被覆盖为终态 `{mode, joinStrategy, status, branches, result, joinTarget}`（契约 04 §5.4），wait 节点的 node_end 事件 data 含 `{mode:"wait", waitType:"duration", durationSeconds}`（契约 04 §5.5；node_start 后同步阻塞等待），human_approval 节点的 node_start 事件 data 含 `approval:{token, summary, approver, timeoutSeconds}`、node_end 含 `{mode:"human_approval", decision, target, token, summary, approver, resolvedBy}`（契约 04 §5.6），subgraph 节点 node_end 含 `{mode:"subgraph", graphId, status, error?, outputs, trace}` 但**子图内部不产生事件**（emit=None 重入，契约 04 §5.7）；**Phase 2 第五项起本端点为真流式**——run_graph 在后台线程执行、事件经 queue 实时下发（旧实现先跑完再回放，human_approval 会因收不到 node_start 而死锁）；**Phase 2 能力项（单步调试）**请求体可选 `debug:{breakpoints:[{node_id, expression?}]}`——存在时启动即 step 模式（首个节点 node_start 后、逻辑前发 `paused` 帧），断点会话级、不落 Graph JSON，未知 node_id/表达式校验失败 422（中文聚合）；新增帧 `paused`（`{token, node_id, node_type, reason:"step"|"breakpoint"|"condition", globals, outputs}` 深拷贝只读快照）与 `stopped`（resume action=stop 后 `{node_id, reason:"user_stop"}`，流结束且无 result 帧），恢复走 POST /api/debug/{token}/resume；子图内部不产生 paused（契约 04 §5.12） | 08 7.1 |

| GET | /api/runs | 挂起/运行查询（**M5 契约设计轮 2026-09-17 登记，docs/24 §4，2026-09-17 已随 M5b 落码生效**）：`?status=suspended&graph_id=&limit=`（新→旧，status 缺省=全部），返回 `{items:[{runId, graphId, status, startedAt, suspendedAt, kind?, nodeId?, deadlineAt?}]}`，`kind=approval` 附 `resumeToken`（审批决策本就凭 token 无身份绑定，沿用现状口径）；仅本租户 | runs |
| GET | /api/runs/{run_id} | 单运行详情（同上 M5b 已落码生效）：状态（running/suspended/completed/failed/interrupted）、节点产出摘要、trace、挂起信息 `{runId, graphId, status, outputs, trace, suspension?}`；跨租户/不存在 404（04 §5.14 全分区口径，不泄漏存在性）；**不新增写端点**——审批决策仍 `POST /api/approvals/{token}/decision`、调试恢复仍 `POST /api/debug/{token}/resume`，本端点只承担查询与 SSE 断线重连后的状态确认（执行线程与 SSE 连接解耦，断开不取消执行） | runs |
| GET | /api/tasks | 任务信封查询（**M7 立项 2026-09-17 登记，08 M7 立项条，同日三批落码生效**）：`?state=&assignee=&limit=`（新→旧），返回 `{items:[{taskId, runId, type, assignee, state, deadlineMs, attempt, result?}]}`；仅本租户 | task_envelope |
| GET | /api/tasks/{task_id} | 单任务详情（同上 M7 已落码生效）：`{taskId, runId, idempotencyKey, type, assignee, payload, deadlineMs, state, result, attempt}`；跨租户/不存在 404；**人工升级不新增写端点**——升级复用 `POST /api/approvals/{token}/decision`（升级帧进 human_approval） | task_envelope |

> condition 节点（Phase 2 首版）运行结果写入 `outputs[condition_id] = {branch, target, evaluation:[{label,expression,result}], expression_errors:[string]}`（默认分支 `branch="__default__"`）；执行轨迹 messages 增一行 `condition-x: branch=… → target`。短路求值与 fail-safe 语义见 04 §5.2、06 §6.1。
>
> loop 节点（Phase 2 第二项，v1 仅条件循环）运行结果写入 `outputs[loop_id] = {mode:"while", iterations, index, target, exitReason, expression_errors:[string]}`，节点每轮重入时该产出被覆盖更新；exitReason ∈ `condition_false`/`max_iterations`/`expression_error`/null；trace 增 `loop-x: continue (i/max) → body` 与 `loop-x: exit (reason) after N → exit` 行。循环体内节点可引用 `{{loop-x.index}}`。fail-safe 与 recursion_limit 派生见 04 §5.3、06 §6.1。
>
> parallel 节点（Phase 2 第三项，v1 静态扇出/扇入）运行结果写入 `outputs[parallel_id] = {mode:"parallel", joinStrategy:"all_success"|"all_completed", status:"success"|"failed", branches:[{label,target,status,error}], result:{<分支入口节点id>: <末端节点产出>}, joinTarget}`；入口先写 running 占位，合成网关 `__join__<id>` 汇聚时（joinTarget 执行前）覆盖为终态并以 parallel 节点自身补发第二次 node_end。分支路径上任一节点 `result.status=="FAILED"` 即该分支失败；all_success 下有失败时整体 `status="failed"` 但 joinTarget 照常执行、run 仍 completed（fail-safe）。trace 增 `parallel-x: fork N branches → a, b` 与 `parallel-x: joined (all_success) success` / `parallel-x: joined (all_success) failed: <label>（<error>）` 行。下游引用形如 `{{parallel-x.status}}`、`{{parallel-x.result.tool-a.result.status}}`（result 以入口节点 id 为键）。扇出/barrier/fail-safe 与 outputs 按键合并 reducer 见 04 §5.4、06 §6.1。

> wait 节点（Phase 2 第四项，v1 仅定时等待）运行结果写入 `outputs[wait_id] = {mode:"wait", waitType:"duration", durationSeconds: <int>}`；执行器 node_start 后同步 `time.sleep(durationSeconds)`（1-600 秒整数常量，线程池工作线程内阻塞），到时沿唯一普通边继续。trace 增 `wait-x: waited 5s` 行。下游引用形如 `{{wait-x.durationSeconds}}`。事件等待缓做 14 D19，语义见 04 §5.5、06 §6.1。
>
> human_approval 节点（Phase 2 第五项，v1 进程内审批信号）运行结果写入 `outputs[human_id] = {mode:"human_approval", decision:"approved"|"rejected", target, token, summary, approver, resolvedBy:"human"|"input"|"timeout"}`；执行器在 `ApprovalBroker`（模块级单例，可注入）登记 pending 后阻塞，node_start 携带 `approval` 载荷。决策三来源：REST 人工放行、run inputs 预置 `{"approvals":{"<node-id>":"approved"|"rejected"}}`（非交互/测试）、超时按 onTimeout（10-3600 秒，默认 reject）自动决策；两条出边全 conditional，按 decision 路由 approvedTarget/rejectedTarget。trace 增 `human-x: approved (human) → tool-y` / `… rejected (timeout) → tool-z` 行。下游引用形如 `{{human-x.decision}}`。持久化中断-恢复缓做 14 D20，语义见 04 §5.6、06 §6.1。**M8（已落码 2026-09-18）纯超集**：config 可选 `cardTemplateId`（不填＝上述 summary 旧路径完全不回归；非空未命中内置目录→编译 422）；命中时 node_start 的 `approval` 载荷在 `{token,summary,approver,timeoutSeconds}` 上加 `cardTemplateId`，节点产出在现有字段上加 `comment`（审批意见，三来源缺省空串）与 `card:{templateId,actionId}`；ApprovalBroker pending 携带 card_template_id 与渲染上下文快照，M5b 中断帧带 cardTemplateId、恢复时上下文从帧 `resume_state.outputs`+trigger 重建；卡片渲染与决策端点见 §3.11 与 `/api/cards`、`/api/approvals/{token}/card`、`/api/approvals/{token}/decision`（actionId/form）。
>
> subgraph 节点（Phase 2 第六项，v1 进程内引用式）运行结果写入 `outputs[subgraph_id] = {mode:"subgraph", graphId, status:"success"|"failed", error?, outputs:<子图全部节点 outputs>, trace:[string]}`；执行器经 `compile_graph/run_graph(graph_resolver=<Callable[[str], GraphDSL]>)` 注入的解析器（Demo 为 GraphStore.get）按 config.graphId 取子图，config.inputs（`{<子图入参键>: "<父图 {{路径}}/字面量>"}`）用父图上下文渲染后作为子图 run inputs 重入 run_graph（emit=None，子图事件不外泄；复用同一 registry/decision_client/approval_broker）。编译期递归校验：graphId 可解析、禁自引用与跨图环、嵌套深度 ≤3、子图递归过 validate_graph（错误中文聚合）；运行期任何异常 fail-safe 为 status:"failed"、父 run 仍 completed 沿唯一普通出边继续。trace 增 `subgraph-x: graph-7 success (N nodes)` / `… failed: <error>` 行。下游引用形如 `{{subgraph-x.status}}`、`{{subgraph-x.outputs.<子图节点id>.<键>}}`。版本钉版/子图市场/远程引用缓做 14 D21，语义见 04 §5.7、06 §6.1。
| POST | /api/operators | 创建运营体（镜像） | operator |
| POST | /api/operators/{id}/run | 启动 Loop | LoopState |
| GET | /api/operators/{id}/status | 运行状态/进度（验收标准 5：画布实时显示） | LoopState.status |
| POST | /api/operators/{id}/pause / resume | 暂停/恢复（人机协作） | status: paused |
| GET | /api/adapters | 适配器列表（注册发现；W9-W10 已落码，返回 shop 适配器及其能力/权限/幂等标记；Phase 2 起增加 http（单能力 http/request）、database（database/query 只读幂等 + database/execute 写）、message（单能力 message/send 写）三个适配器。**2026-09-16 起 tools[] 增列 `input_schema`/`output_schema`**：JSON Schema 子集（权威形状 04 §4.9），未声明投影为 `{}`，供前端拓扑作用域/变量插入与 NL 参数填充使用；**M3（2026-09-16 立项）起 input_schema 作为 SchemaRegistry 第二来源驱动 tool_call 的 params 表单（04 §4.10），本响应 wire 形状不变**） | adapter_schema |
| GET | /api/demo/messages | 消息适配器演示查看（Phase 2 第三项）：返回进程内 MessageService 已记录消息 `{items:[{id,channel,to,subject,body,sent_at}]}`，重启/reset 清空，不产生真实投递 | message_send_params |
| GET | /api/demo/mock/orders | API 适配器演示目标（Phase 2 API 适配器）：要求请求头 `X-Demo-Token: demo-token`，缺失/错误 401 JSON；成功返回演示订单数组。进程内无状态 | http_request_params |
| POST | /api/demo/mock/orders/{id}/receipt | API 适配器演示目标：回显 JSON 请求体并返回 `{"received": true}`，供 POST/body/插值端到端验证 | http_request_params |
| POST | /api/adapters/{id}/tools | 工具查询 | tool |
| GET | /api/operations/{id}/log | 执行日志/审计（06 安全清单） | 审计 |
| POST | /api/nl/generate | 自然语言 → 流程草稿（验收标准 6；W9-W10 已落码：LLM 优先、退款规则模板兜底，无法识别 422；2026-09-16 起响应增列 `paramWarnings:string[]`：按九工具 input_schema 对草稿 tool_call 参数做尽力校验的中文警告——必填缺失/顶层浅类型/enum/additionalProperties:false，模板插值值与不可静态判定项放行，非阻塞） | 08 7.2 |
| POST | /api/demo/shop/login | Demo 商家平台登录（demo/demo，W9-W10） | — |
| GET | /api/demo/shop/orders | Demo 待处理退款单（需登录，W9-W10） | — |
| POST | /api/demo/reset | 【admin only】重置**调用方租户**的 Demo 数据（2026-09-16，04 §5.14：角色不足 403；作用域为本租户 graph/approval/debug/monitoring/message，录制与反馈按租户保留不清；跨租户数据不动）：本租户店铺——全局 demo 店铺恢复 5 笔种子退款单、清空已保存图与登录态、清空 pending 审批请求，Phase 1 种子客户体验，W10 后；Phase 2 第三项起清空进程内消息记录并重建内置 SQLite demo 订单库（全局共享模拟基础设施，仍随 reset 重建）——显式 `ATLAS_DATABASE_URL` 配置的外部库不被触碰；内置流程模板目录为代码常量，不受 reset 影响；录制用例为测试资产同样**不被 reset 清除**——其图已快照进用例，GraphStore 清空不影响回放；Phase 2 能力项起 reset 同时把全部**活动调试暂停按 stop 放行**（会话 cancelled + Event set），阻塞在 paused 的运行线程经 DebugStopped 收敛结束，不留悬挂线程；调试会话本身为进程内临时态，不构成需保留的数据；Phase 2 能力项（基础监控告警）起 reset 同时清空**本租户**监控运行记录与告警（ring、streak、Alert 列表）并把规则阈值恢复默认（运行计数不重置；运行时数据，同消息记录；重启本就清空，持久化随 11 S1/14 D28）。**M5 契约设计轮分层语义（2026-09-17，docs/24 §3.4）**：进程内后端行为不变；PG 档（M5b）＝truncate 本租户运行时表（graphs/runs/interruptions/iam_sessions/monitoring_*）并重建种子，`recordings`/`feedback`（persistent 档）保留不清。**M9 起 reset 同时清空本租户 RoutingStore（rollout 配置/状态/流量计数，进程内运行时态，memory/PG 两档同口径）**。 | — |
| GET | /api/approvals | 列出当前 pending 审批请求（`{items:[{token, summary, approver, timeoutSeconds, node_id, graph_id, cardTemplateId?}]}`，M8 起命中卡片附 cardTemplateId；进程内单例，重启即失；Phase 2 第五项） | human_approval |
| POST | /api/approvals/{token}/decision | 人工审批决策，请求体 `{decision: "approved"|"rejected", comment?}`（comment v1 仅接收不展示）；**M8 起纯超集加可选 `{actionId?, form?}`**——命中卡片时前端可提交 `{actionId, form:{<name>:<value>}}`（decision/comment 可省，服务端经 `map_action_output` 按卡片 action.output 映射，`{{form.*}}` 回填 comment），也仍接受旧 `{decision,comment?}`；首决生效，200 返回决策结果；未知 token 404、已决重复提交 409、坏 actionId 或 action.output 缺必填 form 字段 422（中文 detail）；Phase 2 第五项 | human_approval / card_template |
| POST | /api/feedback | 提交种子试用反馈（type=bug/suggestion、content、contact 选填，201；进程内存储，reset 不清除；Phase 1） | feedback_item |
| GET | /api/feedback | 导出反馈（陪同试用收集用，`{items: [...]}`，Phase 1；2026-09-16 起 **admin only** 且只列本租户，04 §5.14） | feedback_item |
| GET | /demo/shop | 模拟商家售后控制台 HTML 页面（W9-W10，自动登录/抓取演示目标系统） | — |
| GET | / 及静态资源 | 生产形态（Docker）FastAPI 同源托管 `frontend/dist` 构建产物（`ATLAS_FRONTEND_DIST` 指向目录时挂载，html=True；dev 仍用 Vite 5174 代理） | — |
| GET | /api/memories/{operator_id} | 记忆配置读取（05 2.4 配置界面） | memory_config |
| PUT | /api/memories/{operator_id} | 记忆配置保存 | memory_config |

## 6. 协同消息协议（依据 05 3.3 collaboration_message）

```yaml
collaboration_message:
  message_id, from_agent_id, to_agent_id, type
  payload, context: {task_id, business_object}
  urgent, timestamp, ttl, idempotency_key
```

## 7. 评估接口（依据 06 9.2 evaluation_task）

```yaml
evaluation_task:
  task_id, description
  test_cases: [{input: {...}, expected: {action, verify}}]
  metrics: [task_success_rate, average_steps, decision_accuracy]
```

## 8. 接口对齐检查表（AI 落码时逐项确认）

- [x] LoopState 字段与 06 6.1 一致（含 memory_id/status）——✅ 2026-09-13 核对通过，实现于 `src/atlas/engine/state.py`（七字段一致；messages/observations 加 add 归约器以支持追加语义）
- [ ] decide_node 置信度阈值 0.6（06 6.2）
- [x] Web 点击三层定位顺序（06 6.5），层2 置信度 >0.7（W3-W4 落码于 `web/location.py`）
- [x] ActionResult.Status 枚举：SUCCESS/PARTIAL/FAILED（W3-W4 落码于 `harness/base.py`）
- [ ] 节点失败处理枚举：stop/continue/jump_to（03 node_schema）
- [x] 工具权限枚举：read/write/delete/financial（03 adapter_schema；W3-W4 落码于 `harness/base.py`）
- [x] 适配器类型枚举：web/api/mobile/desktop/database/iot/message（W3-W4 已用于 `adapter_type` 字段；web 类型已实现；api 类型 2026-09-15 随 `httpapi/` 通用 HTTP 适配器落地，契约 04 §4.6；**database 与 message 类型 2026-09-15 随 `database/`（query/execute 双能力）、`message/`（message/send 进程内 sink）落地，契约 04 §4.7/§4.8**）
- [ ] 记忆检索分层与 memory_config 阈值（05 2.3）
- [ ] 评估指标三元组（06 9.2 metrics）

---

*本文为新增接口汇总文档；所有接口签名均有原文依据（06 代码示例 / 04 组件设计 / 05 Schema），REST 端点路径为按 Demo 需求推导、标注"推导"，落码时以 OpenAPI 正式化为准。*
