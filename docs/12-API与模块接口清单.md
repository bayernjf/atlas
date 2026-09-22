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
    def __init__(self, base_url="", default_headers=None, client: httpx.Client | None=None,
                 *, egress=None, resolver=None, retry=None, breaker=None, secret_provider=None)
    #   egress=EgressGuard（security/egress.py）；retry=RetryPolicy、breaker=CircuitBreaker（httpapi/resilience.py）；
    #   secret_provider=SecretProvider（security/secrets.py）
    @classmethod
    def from_env(cls)  # ATLAS_HTTPAPI_BASE_URL/_HEADERS(JSON)/_TOKEN(Bearer)
                       # ＋ATLAS_HTTP_EGRESS_ALLOWLIST、ATLAS_HTTP_MAX_ATTEMPTS/_RETRY_BASE_DELAY/
                       #   _CIRCUIT_FAIL_THRESHOLD/_CIRCUIT_COOLDOWN、ATLAS_MASTER_KEY(_ID)/ATLAS_SECRETS
    def request(self, method="GET", url="", headers=None, body=None,
                timeout=30.0, idempotent=False) -> dict
    # 管线：凭证解析(secret://、enc$) → 拼绝对URL → egress 校验 → 熔断 before_call → 重试包传输 → 成败记账
    # → {"status": int, "headers": dict(敏感头已 ***), "body": object|str}
    # 拿到任何 HTTP 响应即 SUCCESS；折叠错误码（HttpApiCallError.code → StructuredError）：
    #   EGRESS_DENIED / EGRESS_INVALID_URL（私网/元数据/非 http(s)，零外呼、不重试、不计熔断）
    #   SECRET_UNAVAILABLE / SECRET_DECRYPT_ERROR（凭证缺失/解密失败，零外呼）
    #   HTTP_CIRCUIT_OPEN（熔断开闸，请求不发出）
    #   HTTP_TIMEOUT / HTTP_CONNECT_ERROR（传输层，重试用尽后）；4xx/5xx 不报错（按 status 分支）

# adapter.py：HttpApiHarnessAdapter(HarnessAdapter)
#   单能力 request（permission=write, is_idempotent=false）
#   节点 config.params 插值后为 JSON：{method,url,headers,body,timeout}
#   缺 url/坏 method/坏 headers/坏 timeout → MISSING_PARAMETER/INVALID_PARAMETER
```

### 3.5 数据适配器 / 消息适配器（04 §4.7/§4.8，06 §6.7）

```python
# src/atlas/database/service.py（进程内，channel 包 database，adapter_id="database"）
class DatabaseAdapterError(Exception):  # .code: DB_NOT_CONFIGURED / DB_SQL_ERROR / MISSING_PARAMETER / INVALID_PARAMETER
                                            #         DB_SQL_NOT_READ_ONLY（query 非单条只读 SELECT）/ DB_WRITE_FORBIDDEN（外部只读连接 execute）

class DatabaseClient:
    def __init__(self, engine: Engine, url: str = "", demo: bool = False, read_only: bool | None = None)
    #   read_only=None → 解析为 not demo（外部 from_env 连接 True，demo engine False）
    @classmethod
    def from_env(cls)             # ATLAS_DATABASE_URL；scheme 白名单 postgresql+psycopg/sqlite；未配置返 None（外部连接 read_only=True）
    @staticmethod
    def demo_engine(seed: bool = True)  # sqlite:///:memory: + StaticPool，建 orders 表 seed 两笔（engine 直连，不经适配器 execute）
    def query(self, sql, params=None, limit=500) -> dict
    # → {"columns": [...], "rows": [{...}], "row_count": int, "truncated": bool}
    # 触库前 assert_read_only_sql（database/guard.py，两档强制）：去注释/拒多语句/只放单条 SELECT·WITH…SELECT
    #   违例 → DB_SQL_NOT_READ_ONLY；PG 再 execution_options(postgresql_readonly=True)，结束 rollback；
    #   SQLAlchemyError → DB_SQL_ERROR（URL 脱敏）
    def execute(self, sql, params=None) -> dict   # read_only 客户端触库前直接 DB_WRITE_FORBIDDEN；否则 commit → {"rowcount": int}
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
    # 返 GateReport {id?,graph_id, target:"draft", total, passed, failed, skipped, blocked,
    #                cases:[{case_id,name,matches,replay_status,note?}]}；
    # total=0 → skipped=true 不阻塞（明示未覆盖）；total>0 任一不匹配 → blocked=true
    # D26 报告 v1（2026-09-18 已落码）：gate.py 仍为纯函数；API 层每次跑完沉淀 ReleaseReport
    #   （release-gate→manual，publish gate→publish-gate，含 skipped/blocked），响应纯超集加 id

# src/atlas/recording/reports.py（D26 报告 v1，2026-09-18 已落码；04 §5.11 末 / 03 release_report）
class ReportStore:                                  # 进程内 per-tenant（挂 TenantServices.report_store），单锁 + deque(maxlen=100)
    def record(self, *, graph_id: str, trigger: str, report: dict) -> str: ...   # → rr-N；pass_rate total=0 为 None
    def list_summary(self, graph_id: str) -> list[dict]: ...                     # 倒序摘要，不含 cases
    def get(self, graph_id: str, rid: str) -> dict | None: ...                   # 详情含 cases；不属于该图 → None（API 404）
    def reset(self) -> None: ...                    # /api/demo/reset 清空（录制用例不清）

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
    # docs/28 批2：before_node 先 mark_node（自上次暂停起经过节点）；暂停成立前 snapshot_change 产变量 history；resume globals 写回后 seed_baseline（手动改写不入变化）；
    # on_exception(node, exc, state) 命中该节点 onException 断点时发 reason="exception"+error 帧后 wait（stop 抛 DebugStopped，step/continue 返回后由执行器原样重抛）；
    # 子图重入经 push/pop_namespaced_emit 给子层 paused/debug_log 附 subgraphPath（白名单放行，终帧仍吞）。

# graph/loader.py 注入（默认 None = 零开销，非调试/回放路径不变）
def compile_graph(graph, *, ..., debug_controller=None, tracer=None, graph_version: str | None = None): ...
def run_graph(graph, *, ..., debug_controller=None, tracer=None,
              graph_version: str | None = None, _parent_span=None) -> dict: ...
# M10：未传 tracer 时 run_graph 自建 Tracer（root run span），result 带 traceId/traceTree；
# _execute_subgraph 重入复用同一 tracer、传 _parent_span=subgraph span（子图 span internal）；
# docs/28 批2（f6f7d30）起 _execute_subgraph 重入透传同一 debug_controller/session（子层可逐帧暂停/断点/logpoint，子层 paused/debug_log 附 subgraphPath、终帧仍吞，子层 stop 穿透 DebugStopped）
# docs/33 §3 ④（cea7111）run_graph 增内部关键字 shadow: bool=False：影子运行时 loader 依编译期 adapter/capability→permission 表短路（permission!=READ 不调 adapter.execute、返 SHADOW_DRY_RUN 意图），全链透传含子图；不写 run_store、不调 evaluate_after_run、不发 tool_metric，human_approval 用独立 broker 预置 approved；配合独立 tracer、emit（仅收顶层 node_end）由 POST /graphs/{id}/shadow-runs 装配，普通运行零改动

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
    status: Literal["completed", "error", "cancelled"]   # B 包(f9a1301)协作式急停=cancelled
    started_at: str             # ISO 8601 UTC
    finished_at: str
    duration_ms: float
    nodes: list[NodeResult]
    error: str | None = None    # 仅运行级异常
    trace_id: str = ""          # M10：本 run 的 traceId（可空，向后兼容；debug/回放/subgraph 重入不写）
    resolved_version: int | None = None   # M9：入站 event 运行经 Router 钉住的发布版本；手动运行为 None
    business: "BusinessOutcome | None" = None  # M9：业务结果提取（无业务结果时 None）
    tool_calls: list["ToolCallMetric"] = []  # docs/28 §4.1 ⑧(a318d97)：真实工具调用埋点，纯超集缺省 []

# docs/28 §4.1 ⑧：ToolCallMetric {node_id:str, tool:str, duration_ms:float,
#   action_status:Literal["SUCCESS","FAILED","SIMULATED"], error_code:str|None=None}

class MonitoringStore:          # 进程内单例；重启清空（持久化随 11 S1/14 D28）
    def record_run(self, *, graph_id, mode, status, started_at,
                   nodes: list[NodeResult], error: str | None = None,
                   trace_id: str = "",
                   resolved_version: int | None = None,
                   business: dict | None = None,
                   tool_calls: list | None = None) -> RunRecord: ...   # M9/⑧ 纯超集透传（⑧ a318d97）
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
#  业务三率分母＝有业务结果的 run，样本 0 为 null；
#  docs/28 §4.1 ⑧ 增 tools:[{tool, calls, failed, simulated, error_codes:{code:n}, p50, p95}]
#    （summarize_tools；SIMULATED 纳 calls/simulated 不纳 failed 与延迟分位，真实样本 0 分位为 null）}
def summarize_tools(runs: list[RunRecord]) -> list[dict]: ...   # ⑧(a318d97)：按 tool 聚合

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
    custom: list["CustomRule"] = []             # docs/28 §4.2 ⑨(11b1ba7)：纯超集缺省 []，旧配置/PG JSONB 不 422
# M9：rollout_gate 阈值不在全局 RuleConfig，随每图 RolloutConfig.gate（routing 包）
# ⑨ CustomRule {cid:str(非空/唯一/≤64), name:str(非空/≤50), enabled:bool=True,
#   expression:str（graph.conditions.validate_expression 静态校验、禁 eval、顶层须布尔）,
#   severity:Literal["critical","warning"]="warning"}；RuleId 由内置 Literal 放宽为 str 承载 "custom:{cid}"；
#   Alert/AlertEvent 纯超集 rule_name:str|None=None（进程内落库；PG alerts 表 v1 无该列读回 None）

# M9 Alert 纯超集字段（仅 rollout_gate 告警携带，其余规则无此字段）：
#   rule_id 枚举增 "rollout_gate"（critical）；
#   action: {type:"rollback", from_version:int, to_version:int, reason:str, actor:"auto"|"manual"} | None

def validate_rules(raw: dict) -> list[str]: ...           # 中文错误信息（enabled bool、整数 1-200、rate 0-1）
def evaluate_rules(*, record, healthy, recent_by_graph: list[RunRecord],
                   rules: RuleConfig, streak: int) -> list[AlertEvent]: ...
# 触发意图（不含合并）：run_error（status=="error"，critical）；node_failed（有失败节点，warning）；
# consecutive_failures（streak 达 threshold 且规则启用，critical；首达后每次失败继续意图→由 store 合并）；
# failure_rate（该图近 window 次含本次样本≥min_samples 且不健康占比≥rate，critical）
#
# docs/28 §4.2 ⑨：内置段之后逐条 enabled 自定义规则以扁平白名单上下文
#   {status, durationMs:int(round 毫秒，条件引擎有序比较要求严格同型), failedCount:int, hasError:bool}
#   （不含 graphId，自定义规则对租户全部运行求值）调 graph.conditions.evaluate_expression；
#   返回非 True 不告警，ConditionEvalError/任何异常 fail-safe 不告警不阻塞；
#   命中产 AlertEvent(rule_id=f"custom:{cid}", severity=规则值, rule_name=name)，合并键仍 rule_id+graph_id。
```

埋点只在 `src/atlas/api/main.py`：sync `/run` 包装（异常记录 error 后原样 raise，保持 500），`/run/stream` worker 非 debug 时以 emit 包装收集 node_end output，`__result__` 记 completed、`__error__` 记 error（部分节点）、`__stopped__`（debug）不记。**docs/28 §4.1 ⑧（a318d97）起两入口另以同形 emit 收集无 `subgraphPath` 的顶层 `tool_metric` 事件（executor `graph/loader.py` 对真实工具分支 `time.monotonic()` 计时、经模块级 `_tool_metric_event` 归一后 emit；mock 命中不发、子图内被命名空间白名单吞掉、debug 流发而不采、replay/gate/子图重入不采），completed/cancelled/error 三出口 `record_run(..., tool_calls=)` 传入。**录制 replay 端点与 subgraph 重入零改动。**M9 起两个真实运行入口在 `record_run` 之后调用 `routing.gate.evaluate_after_run(services, record)`（门控自动回滚，见 §3.12）；replay/debug/subgraph 重入同口径不触发。**

### 3.10 多租户与权限（04 §5.14/§5.17，06 §6.12）

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

> **M11 实现边界（2026-09-19 已落码收口；权威＝docs/26、ADR T23）**：下列 `memory_retriever.query` 五层分层检索为**愿景**（working Redis / summary / fact pgvector / case / preference + 决策节点隐式注入），v1 不实现，缓做 14 D35。M11 取回的是下方「4.1 M11 长期记忆最小接口」——统一 memory_item（fact/preference）+ 显式 remember/recall 两工具，**不做决策隐式注入**。

```python
# 【愿景，缓做 D35】决策节点隐式分层记忆检索（06 §6.2 decide 第 1 步）
memory_retriever.query(goal: str, recent_messages: list) -> list
# 依据 memory_config 分层检索：working_memory(Redis) / summary_memory(PostgreSQL)
#   / fact_memory(pgvector, confidence≥0.85) / case_memory(相似度阈值) / preference_memory
```

### 4.1 M11 长期记忆最小接口（docs/26，2026-09-19 已落码 `5441902`/`58d936c`/`73e53bd`）

```python
# src/atlas/memory/embeddings.py（纯 stdlib：re/hashlib/math，不引 numpy；离线、录制回放确定）
EMBED_DIM = 256
class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...        # L2 归一化；空文本返零向量
class LocalDeterministicEmbedder:                         # 沙盘默认：signed hashing trick
    def embed(self, text: str) -> list[float]: ...        # 英文数字 [a-z0-9]+、中文 unigram+bigram，md5 定桶/奇偶定号
def cosine_similarity(a, b) -> float: ...                 # 零向量 → 0
def get_embedding_provider() -> EmbeddingProvider: ...    # 商业 embedding 缓做 D35，仅留 provider 接缝

# src/atlas/storage/base.py：第九个 MemoryRepository（RESET_RESETTABLE；tenant_id 构造期注入，不进方法签名）
class MemoryRepository(Protocol):
    def remember(self, *, kind, content, scope=None, confidence=1.0, source="tool", metadata=None) -> dict: ...
    def recall(self, query, *, kind=None, scope=None, top_k=5, min_score=0.0) -> list[dict]: ...  # 每项=记忆 dict+"score"，降序
    def list(self, *, kind=None, limit=50) -> list[dict]: ...
    def update(self, memory_id: str, **fields) -> dict | None: ...  # docs/28 批 4⑩：白名单字段合并、source 归 manual、content 变重算 embedding，不存在/他租户 None
    def delete(self, id: str) -> bool: ...
    def clear(self) -> None: ...
# 两档实现：memory/items.py MemoryStore（进程内，mem-N 租户计数）；storage/pg.py PgMemoryStore
#   （memory_items 表 vector(256)，embedding <=> :q::vector(256) 余弦、scope @> :scope::jsonb，迁移 006_memory.sql）

# src/atlas/memory/adapter.py：MemoryHarnessAdapter(HarnessAdapter)，adapter_id/type="memory"
#   能力 memory/remember（action=memory_remember，permission=write，非幂等）
#     input  {kind: enum[fact,preference]*（*必填）, content: string 1-2000*, scope?:{str:str},
#             confidence?: number 0-1, metadata?:{str:str}}
#     output {id, kind, content, confidence, source, scope, created_at}
#   能力 memory/recall（action=memory_recall，permission=read，幂等）
#     input  {query: string*, kind?: enum, scope?:{str:str}, top_k?: int 1-20=5, min_score?: number 0-1=0}
#     output {results: [{id, kind, content, score, confidence, scope, created_at}]}  # 无命中 results=[]
#   schema 守 harness Capability keyword 白名单（无 x- 扩展）；observe() 返空 Observation。
#   装配照 message 两段式：全局注册仅供发现，_runtime_registry 按租户克隆注入 services.memory_store。
```

## 5. 后端服务 API（依据 08 7.2 /api/main.py FastAPI 入口）

> 以下路径为按 Demo 需求推导的 REST 端点清单，字段以 03/04/05 Schema 为准；正式定义待落码时随 OpenAPI 生成。
>
> **鉴权四档（2026-09-16，04 §5.14）**：除标注「公开」者外，端点必须携带 `Authorization: Bearer <sess-token>`，缺失/无效 → 401「缺少或无效的登录凭证」；角色不足 → 403「当前角色无权执行此操作」；访问不存在于本租户的对象（含他租户审批/调试 token）→ 404。**公开**：`POST /api/auth/login`、`GET /api/health`、静态托管、`GET /demo/shop`、`/api/demo/**`（模拟外部系统，沿用 X-Demo-Token/demo 登录自带认证，不经平台鉴权）。**viewer+**（全部登录角色可读）：所有 GET 业务端点（graphs/templates/adapters/recordings/monitoring/alerts/approvals/debug/messages）+ `POST /api/feedback`（人人可提交）。**operator+**：POST/PUT/DELETE/POST 运行类——graphs 保存、compile、run、run/stream、nl/generate、recordings 写/删/replay、approvals 决策、debug resume、runs/{id}/cancel 急停、alerts acknowledge/resolve、publish 与 release-gate、rollout 配置/start/promote/rollback（M9，对齐发布权限）。**admin only**：`PUT /api/monitoring/rules`、`POST /api/demo/reset`、`GET /api/feedback`。所有业务数据按 token 推断的租户分区（03 各结构租户注记）。

| 方法 | 路径 | 功能 | 关联 |
|---|---|---|---|
| POST | /api/auth/login | 登录换会话（公开）：请求体 `{username, password}`，坏凭证 401「用户名或密码错误」（未知用户同文案防枚举）、停用账号 403「账号已停用，请联系管理员」、连续失败触发节流 429「登录尝试过于频繁，请稍后再试」（滑动窗口 600s/5 次、键 username|client_ip，docs/31/04 §5.17）；200 返回 `{token:"sess-<uuid hex>", principal:{tenant_id, tenant_name, username, display_name, role}}`（identity_session） | identity_session |
| GET | /api/auth/me | 回显当前 Bearer 会话的 Principal（viewer+） | identity_session |
| POST | /api/auth/logout | 吊销当前 token（viewer+；幂等，204/200） | identity_session |
| POST | /api/auth/change-password | 【viewer+】登录用户改本人密码（docs/31/04 §5.17，已落码）：body `{oldPassword,newPassword}`；旧口令错 400「原密码错误」；新口令不达策略（8-128 位、弱口令黑名单）或与旧口令相同 → 422；成功吊销本人**除当前会话外**全部会话，返 `{changed:true}` | identity_user |
| GET | /api/users | 【admin】列本租户用户（docs/31 §3）：`[{username,displayName,role,status,createdAt,updatedAt}]`，**不含 password_hash** | identity_user |
| POST | /api/users | 【admin】本租户建用户（201）：body `{username,password,displayName,role}`；用户名 3-64 位 `[a-z0-9_.-]`、口令策略不达标、显示名为空 → 422；租户内重名 → 409「用户名已存在」 | identity_user |
| PATCH | /api/users/{username} | 【admin】改本租户用户：body `{displayName?,role?,status?}`（仅传需改字段）；他租户/不存在 → 404「用户不存在」；status 改 disabled 时吊销该用户全部会话 | identity_user |
| POST | /api/users/{username}/reset-password | 【admin】重置本租户用户密码：body `{newPassword}`；404/422 同上；成功吊销该用户全部会话，返 `{reset:true}` | identity_user |
| POST | /api/graphs | 保存 Graph 定义（DSL）：总是新建 graph-N（首次建图） | node_schema / graph_definition |
| PUT | /api/graphs/{id} | 【operate，M9】同图迭代覆盖 latest 草稿（`GraphRepository.update_draft`）：body 同 POST 的 SerializedGraph、过 parse_graph 校验，**不新建 id、不动不可变发布版**，刷 updated_at，返 `{id, version}`；图不存在/跨租户 → 404（KeyError）。前端编辑器对同一画布首次 POST 之后的运行/录制/发布统一走此端点（修复早期每次运行新建 graph-N 致录制用例与门禁 graph_id 错位） | graph_definition |
| GET | /api/graphs | 列出已保存图（`{items:[{id, node_count, updated_at}]}`，进程内存储；Phase 2 第六项，供 subgraph 节点选择器） | graph_definition |
| GET | /api/graphs/{id} | 读取 Graph（latest 草稿；`?releaseVersion=N` 读指定发布版本快照，未知/未发布 404） | — |
| POST | /api/graphs/{id}/publish | 【operate】发布当前草稿为不可变版本（M6 立项 2026-09-17，docs/20 §4.1 / ADR T19）：冻结 Graph JSON + subgraph 钉版（递归，子图未发布则先递归发布其草稿为 v1），返回 `{id, releaseVersion}`；releaseVersion 从 1 递增、已发布版本只读。**M9 起请求体从无改为可选 `{gate?: boolean}`**：gate=true 先跑发布门禁（下行 release-gate），blocked → **409 带完整 GateReport 且不产新版本** | graph_definition / release_gate |
| GET | /api/graphs/{id}/versions | 【read】已发布版本列表（M6）：`{items:[<releaseVersion>]}`（升序）；未发布过 → 空列表 | graph_definition |
| GET | /api/graphs/{id}/subgraph-upgrades | 【read，docs/28 批 4⑪ 2026-09-20 `ba97088`】发布前子图版本升级体检（**纯只读、不产版本**）：返 `{items:[{node_id, sub_id, from_version:int\|null, to_version:int, first_pin:bool}]}`，只扫父图草稿顶层 subgraph 节点，列首次钉版或 from≠to 升级；草稿不存在 → 404；不阻断发布 | graph_definition |
| POST | /api/graphs/{id}/release-gate | 【operate，M9 已落码 2026-09-18】发布前批量回放门禁（只跑门禁不发布，D26 部分取回）：对当前 latest 草稿逐例重放 `case.graph_id==id` 的录制用例（复用 replay.compare），返回 GateReport（D26 报告 v1 起纯超集加 `id`＝沉淀报告 rr-N）`{id,graph_id, target:"draft", total, passed, failed, skipped, blocked, cases:[{case_id,name,matches,replay_status,note?,clock_note?}]}`（**C 包 `3415377` 起每例回放冻结到该例 recorded_at〔回退 created_at〕，缺锚点的用例在该 case 项附 `clock_note`**）；total=0 时 skipped=true 不阻塞（明示未覆盖），total>0 任一不匹配 blocked=true；图不存在 404。**D26 报告 v1（2026-09-18 已落码）起每次运行沉淀一条 ReleaseReport（trigger=manual，含 skipped）**；publish `{gate:true}` 另沉淀 trigger=publish-gate（通过/blocked 均沉淀，409 报告体带 id） | release_gate / release_report / recording_case |
| GET | /api/graphs/{id}/release-reports | 【read，D26 报告 v1 已落码 2026-09-18】本图批量回放报告历史（倒序摘要）：`{items:[{id,graph_id,trigger,total,passed,failed,skipped,blocked,pass_rate,created_at}]}`，不含 cases；图不存在 404，跨租户 404 | release_report |
| GET | /api/graphs/{id}/release-reports/{rid} | 【read，D26 报告 v1 已落码 2026-09-18】报告详情（含 cases 逐例 ✓/✗/note）；报告不属于该图或不存在 404，跨租户不泄漏存在性 | release_report |
| GET | /api/graphs/{id}/release-reports/{rid}/export | 【read，D26 收尾批已落码 2026-09-19，`8a37b4e`】导出报告下载，query `format=csv|json`（默认 csv，非法值 422）：JSON 为完整报告 attachment（`rr-N.json`），CSV 为元信息行+空行+逐 case 行（`text/csv; charset=utf-8`、带 UTF-8 BOM 供 Excel，`rr-N.csv`）；`Content-Disposition: attachment`，图不存在/报告跨图或不存在 404 照详情；前端经 Bearer fetch→blob→`a[download]` | release_report |
| GET | /api/release-reports | 【read，docs/28 §2.4 已落码 2026-09-20，`89e21fc`】**跨图**用例集报告看板：query `limit`（默认 100、clamp 1-200、非整数 422），返回 `{items:[{id,graph_id,trigger,total,passed,failed,skipped,blocked,pass_rate,created_at}]}` 跨本租户全部图倒序、不含 cases；进程内 ReportStore.list_all_summary（ring 倒序切片），不进 Repository/不 PG 化；viewer 可读 | release_report |
| GET | /api/graphs/{id}/rollout | 【read，M9】灰度配置与运行态：`{status, stable, candidate, started_at, rolled_back_at, rollback_reason, config, traffic:{stable,candidate,segments:{internal,lowValueBucket,canary,full,fallback}}}`；从未配置 → status="idle" 空态 | rollout_config |
| PUT | /api/graphs/{id}/rollout | 【operate，M9】存 RolloutConfig（strategy/rules 四段/gate/inFlightPolicy，形状见 §3.12），**只存配置不启动**；非法值（percent 越界、阈值非 0-1、段顺序/形状错）422 中文聚合；图不存在 404 | rollout_config |
| POST | /api/graphs/{id}/rollout/start | 【operate，M9】idle→canary：取最新两个发布版（stable=前一版、candidate=最新版）；发布版不足 2 个 → 409「至少需要两个发布版本才能开始灰度」；未配置 RolloutConfig 409；返回 rollout 快照 | rollout_config |
| POST | /api/graphs/{id}/rollout/promote | 【operate，M9】canary→full：**唯一放量路径，仅手动**（全仓无自动 promote）；状态非 canary 409；返回快照 | rollout_config |
| POST | /api/graphs/{id}/rollout/rollback | 【operate，M9】任意态→rolled_back（candidate 撤流、stable 接全量）；自动门控（actor="auto"）与手动（actor="manual"）同一幂等函数，重复回滚幂等 200；body 可选 `{reason?}`；返回快照 | rollout_config / business_metrics |
| GET | /api/templates | 内置流程模板目录列表（Phase 2 能力项，只读代码常量；返回 `{items:[{id,name,description,tags,node_count}]}`，不含 graph；不受 reset 影响） | template_catalog |
| GET | /api/templates/{id} | 模板详情：完整 TemplateMeta 含 `graph`（可直接载入画布/保存为新图）；未知 id 404 | template_catalog / graph_definition |
| GET | /api/cards | 内置交互卡片目录（M8 已落码 2026-09-18，viewer+；只读代码常量，不受 reset 影响）：`{items:[{id,name,channels,sections,actions,fallback?}]}`（CardTemplate 投影，供配置态 card-select 与运行态渲染） | card_template |
| GET | /api/approvals/{token}/card | 审批卡片按渠道渲染（M8，viewer+）：`?channel=web\|im\|email`（缺省 web），返回 §3.11 对应渠道渲染产物；未知 token 404、该审批未配 cardTemplateId（无卡片）404、非法 channel 422；渲染上下文取挂起时快照（中断恢复后从帧重建）；**只读，不产生决策副作用**（邮件链接为 GET 落地页，决策一律走 POST） | card_template / human_approval |
| POST | /api/recordings | 录制用例入库（Phase 2 能力项）：请求体 `{name(1-100), graph_id, inputs, steps:[{node_id,node_type,output}], status}`，服务端按 graph_id 取已保存图原始 JSON 作**快照**存入（不重新执行；graph 未知 404、steps 形状非法 422、空 steps 422），201 返回完整 RecordingCase；**M9 起 graph_id 一并落库**（纯超集，旧用例该字段为空串）；**D26-b（2026-09-19，`29bb3d9`）起录制创建时递归收集父图及嵌套子图 raw 入纯超集 `subgraphs:{graphId:raw}`**（深度≤3、visited 防环、引用缺失不阻断录制），单用例 replay 内联优先解析、发布门禁仍实时解析（见 04 §5.11）；**C 包 `3415377` 起入库即写 `recorded_at`（UTC ISO，与 created_at 同 stamp，回放冻结时钟锚点；端点不重跑 baseline）** | recording_case |
| GET | /api/recordings | 录制用例列表：`{items:[{id,name,graph_id,node_count,step_count,status,created_at}]}` 投影（M9 起含 graph_id；不含 graph/steps）；进程内存储，reset 不清除 | recording_case |
| GET | /api/recordings/{id} | 录制用例详情：完整 RecordingCase（含 graph 快照与 steps、C 包起含 `recorded_at`，旧用例为 null）；未知 id 404 | recording_case |
| DELETE | /api/recordings/{id} | 删除录制用例；未知 id 404 | recording_case |
| PUT | /api/recordings/{id} | 【operate，docs/28 §2.3 已落码 2026-09-20，`89e21fc`】编辑用例元信息/入参：请求体 `{name?: 1-100字, inputs?: object}`（可只给其一部分；空体/无有效字段 422、空名/超长 422、inputs 非对象 422），仅 name/inputs 可改，steps/graph/subgraphs/graph_id/时间戳为录制事实不可改（请重新录制）；进程内原地 model_copy 保序、PG 动态 UPDATE，不存在 404，返回完整 RecordingCase；viewer 403 | recording_case |
| POST | /api/recordings/{id}/replay | 回放用例：对快照走标准 run_graph（审批决策从 baseline 抽为 inputs.approvals 预置，不挂起），比对操作序列与逐节点归一化产出；返回 `{matches, baseline_status, replay_status, steps:[{node_id,match,note,diff_keys?}]}`；回放异常折叠 replay_status="failed"/matches=false（不抛 500）；未知用例 404。**C 包 `3415377` 起回放把 run_graph 时钟冻结到 `clock_anchor(case)`（recorded_at→created_at→真实时钟），含 today()/now() 的分支与录制时确定可比；用例两时间戳皆缺时响应附顶层 `clock_note`（中文，提示时间分支可能漂移）**。**docs/28 §2.2（2026-09-20，`c7bf138`）起请求体从无改为可选 `{mock_tools?: boolean, inputs_override?: object}`**：`mock_tools=true` 时顶层工具节点命中录制输出桩（不触达适配器 registry、不发 tool span/tool_metric；子图内工具不桩），响应纯超集加 `mocked_tools: string[]`（被桩节点 id，缺省 []）；`inputs_override` 顶层键浅合并进用例 inputs（仅本次回放、不落库），非对象 422；**发布门禁刻意不接 mock**（gate 仍实时跑真实适配器） | recording_case |
| POST | /api/debug/{token}/resume | 恢复调试暂停（Phase 2 能力项）：请求体 `{action:"step"|"continue"|"stop", globals?{...}}`；step=执行当前节点并在下一节点前再停、continue=退出逐节点仅断点停、stop=取消运行（随后收到 stopped 帧、无 result）；**B 包（f9a1301）起 step/continue 可带 `globals`：global 顶层键浅合并覆盖（dict 值整体替换、不深 merge、不删未给键），键名须 `^[A-Za-z_][A-Za-z0-9_]*$`、值 JSON 可序列化，非法 422 中文，stop 忽略 globals**；首决生效 200，未知 token 404、对已恢复暂停重复提交 409；进程内会话重启即失 | debug_session |
| GET | /api/debug | 列出当前活动调试暂停：`{items:[{token, node_id, node_type, graph_id, reason}]}`（Phase 2 能力项，进程内） | debug_session |
| GET | /api/monitoring/metrics | 监控指标聚合（Phase 2 能力项）：`{total,healthy,unhealthy,success_rate,p50,p95,per_graph:[{graph_id,…}],failed_nodes:[{node_id,node_type,count,last_error,last_seen}]}`；口径=ring 内全部保留运行（≤200），空集 success_rate/p50/p95 为 null；进程内、重启即失。**M9 起增 `business` 段**：`{auto_refund_rate, manual_escalation_rate, refund_amount_diff_rate, per_graph:[{graph_id,…三率,samples}], per_version:[{graph_id,resolved_version,…三率,samples}]}`（分母＝有业务结果的 run，样本 0 为 null；契约 03 `business_metrics`）；**docs/28 §4.1 ⑧（a318d97）起纯超集增 `tools:[{tool,calls,failed,simulated,error_codes:{code:n},p50,p95}]`**（按工具聚合真实适配器调用，SIMULATED 不纳 failed/延迟分位，样本 0 为 []/分位 null） | monitoring / business_metrics |
| GET | /api/monitoring/runs | 运行记录列表：`?graph_id=&limit=`（默认 50、1-200 截断，新→旧），返回 `{items:[RunRecord]}`，每条纯超集含 `tool_calls:[ToolCallMetric]`（⑧，缺省 []）；仅真实运行（debug/回放/子图重入不计） | monitoring |
| GET | /api/monitoring/rules | 读取规则配置 RuleConfig：四内置规则（默认全开，连续阈值 3、窗口 20/最小样本 5/失败率 0.5）＋纯超集 `custom:[CustomRule]`（⑨，缺省 []） | monitoring |
| PUT | /api/monitoring/rules | 全量替换规则配置；非法值（enabled 非 bool、阈值/窗口/样本非 1-200 整数、rate 非 0-1）422 中文聚合错误；**⑨ 起 `custom` 段同校验（cid 非空/同配置唯一/≤64、name 非空/≤50、severity 枚举、expression 经安全条件引擎静态校验且顶层须布尔，错误中文聚合 `custom[i].xxx：…`），旧配置无 custom 不 422**；`/api/demo/reset` 恢复默认 | monitoring |
| GET | /api/alerts | 告警列表：`?status=open\|acknowledged\|resolved`（缺省全部），`{items:[Alert]}` 新→旧；**⑨ 起 `rule_id` 为 string（内置五条或 `custom:{cid}`）、纯超集 `rule_name`（自定义规则名；进程内档有值，PG 档 v1 读回 null，前端回退 rule_id）** | monitoring |
| POST | /api/alerts/{id}/acknowledge | 确认告警（open→acknowledged）；未知 id 404、非 open 状态 409，中文 detail | monitoring |
| POST | /api/alerts/{id}/resolve | 关闭告警（open/acknowledged→resolved）；未知 id 404、已 resolved 409，中文 detail | monitoring |
| GET | /api/monitoring/runs/{run_id}/trace | **docs/33 §4 ⑤（4579843，read）**：单运行 span 树懒加载，返 `{id, trace_id, spans}`（spans＝tracer.to_tree() 根 dict，kind=run、DFS 含 node/tool）；列表端点不返 spans；历史/debug/回放无 spans 记录返 `spans:null`；运行不存在/跨租户 404 | monitoring / trace_span |
| POST | /api/graphs/{graph_id}/shadow-runs | **docs/33 §3 ④（cea7111，operate，201）**：影子运行，旁路跑完整决策链；body `{inputs?, human_outcome?}`。READ 能力透传真实执行、WRITE/DELETE/FINANCIAL 短路返 SHADOW_DRY_RUN；预置全部 human_approval approved 秒过（独立 broker）；不写 run_store/RunRecord、不触发 evaluate_after_run/告警/灰度门控、不发 tool_metric；独立 tracer；异常也沉淀 status=error。图不存在 404。返 ShadowRun | recording/shadow |
| GET | /api/shadow-runs | **docs/33 §3 ④（read）**：本租户影子运行倒序，`?graph_id=&limit=`（默认 50、1-200 截断），`{items:[ShadowRun]}` | recording/shadow |
| GET | /api/shadow-runs/{sid} | **docs/33 §3 ④（read）**：影子运行详情（含 decisions/tool_intents/comparison）；不存在/跨租户 404 中文 detail | recording/shadow |
| POST | /api/shadow-runs/{sid}/compare | **docs/33 §3 ④（operate）**：补录/覆盖人工实际处理并重算对比；body `{human_outcome:{action(非空),note?}}`，action 空 422、sid 不存在 404；返更新后的 ShadowRun（comparison.match/auto_action/human_action/diffs） | recording/shadow |
| POST | /api/monitoring/silences | **docs/33 §5 ⑩（00bba8c，administer，201）**：建静默；body `{rule_id:str\|null, graph_id:str\|null, duration_minutes:1-10080 int, reason:非空≤200}`，duration 越界/非 int（bool 拒）/reason 空或超长 → 422 中文聚合；返 Silence（含 active=true）。上限 100、惰性清过期 | monitoring/silences |
| GET | /api/monitoring/silences | **docs/33 §5 ⑩（read）**：静默列表 `{items:[Silence+active]}`；可选 `?active=true\|false`（其他值 422），active 为按 expires_at 的计算字段 | monitoring/silences |
| DELETE | /api/monitoring/silences/{silence_id} | **docs/33 §5 ⑩（administer）**：提前解除，返 `{id,deleted:true}`；不存在 404 中文 detail；viewer 403 | monitoring/silences |
| GET | /api/monitoring/on-call | **docs/33 §5 ⑩（read）**：值班表，返 OnCallSchedule + 计算字段 `current`（空表为 null） | monitoring/silences |
| PUT | /api/monitoring/on-call | **docs/33 §5 ⑩（administer）**：设置轮值表；body `{members:[1-20 个非空串]}`（去重保序、重置 index=0），数量/元素非法 422；返更新后 schedule + current | monitoring/silences |
| POST | /api/monitoring/on-call/rotate | **docs/33 §5 ⑩（administer）**：手动轮换推进 index，返 schedule + current；值班表为空 409（OnCallEmpty 中文 detail）；viewer 403 | monitoring/silences |
| POST | /api/graphs/{id}/compile | DSL → LangGraph 编译（08 7.1 W7-W8）；**M6 起请求体可选 `releaseVersion`（缺省 latest）**。静态校验失败 422 体 `{"detail":[中文消息,...]}` 不变；**M2 起（2026-09-16 落码，fbd9f77）并列增机器可读定位侧车** `"locations":[{"index":number,"nodeId"?:"…","pointer"?:"/branches/0/expression"}]`——index 对齐 detail 下标、稀疏，pointer 为 RFC6901 相对该节点 config 根；图级错误无条目；契约见 03 `diagnostic`、06 §6.13，用例 U38。run/run/stream 经同一 compile_graph 路径，形状相同 | 02 Graph DSL |
| POST | /api/graphs/{id}/run | 编译并运行，返回状态/节点产出/执行轨迹；请求体 `{"inputs": {...}}`（M6 起可选 `releaseVersion`，缺省 latest；**M9 起可选 `event:{channel?,payload?}`——带 event 为入站触发，经 Router 三段分桶解析发布版本并按不可变快照运行（pin-to-version），无任何发布版 → 409；不带 event＝编辑器手动运行 latest 草稿，旧行为零回归；RunRecord 记 resolved_version**），inputs 同名键覆盖全局变量且整体作为 trigger 节点 webhook 载荷 `context.payload`（W9-W10 接入真实决策/适配器）；**不支持调试**——请求体含 `debug` 返回 422「单步调试仅支持流式运行 /run/stream」（Phase 2 能力项） | 02 Graph DSL / LoopState / route_decision |
| POST | /api/graphs/{id}/run/stream | SSE 流式运行（W9-W10；M6 起 body 可选 `releaseVersion`，缺省 latest；**M9 起同 sync 行可选 `event:{channel?,payload?}`，Router 分桶 + pin-to-version，无发布版 409，不带 event 走草稿零回归**）：事件 `node_start`/`node_end`/最终 `result`，供画布实时进度（验收标准 5）；**M10 起三帧为 19 §2.3.4 超集——node_start/node_end 另带 `traceId/spanId/parentSpanId`、run_end(result) 另带 `traceId/spanId/graphVersion`（只增字段、不改帧型，前端忽略未知字段零改动，完整 span 树不进 SSE）**；condition 节点的 node_end 事件 data 含 `{branch, target, evaluation, expression_errors}`（契约 04 §5.2），loop 节点含 `{mode, iterations, index, target, exitReason, expression_errors}`（契约 04 §5.3），parallel 节点扇出时 data 为 running 占位、joinTarget 的 node_end 前该产出被覆盖为终态 `{mode, joinStrategy, status, branches, result, joinTarget}`（契约 04 §5.4），wait 节点的 node_end 事件 data 含 `{mode:"wait", waitType:"duration", durationSeconds}`（契约 04 §5.5；node_start 后同步阻塞等待），human_approval 节点的 node_start 事件 data 含 `approval:{token, summary, approver, timeoutSeconds}`、node_end 含 `{mode:"human_approval", decision, target, token, summary, approver, resolvedBy}`（契约 04 §5.6），subgraph 节点自身 node_end 含 `{mode:"subgraph", graphId, status, error?, outputs, trace}`（无 subgraphPath）；**A 包（2026-09-19 `e594a4b`，docs/27 §3）起子图内部 `node_start`/`node_end` 以可选 `subgraphPath:string[]`（每层父图 subgraph 节点 id，顶层缺省）上 SSE**——只转发节点级事件、吞掉子层 run_end/result（整图仅一个 run_end），子图内 human_approval 的 approval 载荷随内部 node_start 上屏、可经共享 broker 与既有决策端点交互（无新端点，契约 04 §5.7）；**Phase 2 第五项起本端点为真流式**——run_graph 在后台线程执行、事件经 queue 实时下发（旧实现先跑完再回放，human_approval 会因收不到 node_start 而死锁）；**Phase 2 能力项（单步调试）**请求体可选 `debug:{breakpoints:[{node_id, expression?, hitCount?, logMessage?, onException?}]}`（**B 包 f9a1301：hitCount 正整数 N＝每第 N 次命中暂停 hits%N==0、logMessage 非空＝日志断点命中只发 debug_log 不暂停、消息原样不插值，非正整数/布尔/非串 422 中文聚合**）——存在时启动即 step 模式（首个节点 node_start 后、逻辑前发 `paused` 帧），断点会话级、不落 Graph JSON，未知 node_id/表达式校验失败 422（中文聚合）；新增帧 `paused`（`{token, node_id, node_type, reason:"step"|"breakpoint"|"condition"|"exception", globals, outputs, history?, error?, subgraphPath?}` 深拷贝只读快照；**docs/28 批2（038dcaa/9b378c2/f6f7d30）** reason 增 `exception`，帧纯超集增 `history`（变量变化历史，易失上限 50）、`error`（`{type,message}`，仅 exception）、`subgraphPath`（子图层路径，仅子层帧）；断点入参 `onException:true`＝异常断点，命中异常先暂停、resume 原样重抛不忽略）与 `stopped`（resume action=stop 后 `{node_id, reason:"user_stop"}`，流结束且无 result 帧；**调试流中点急停也折叠为 stopped，不另发 cancelled**）、`debug_log`（**B 包 f9a1301** logpoint 帧 `{node_id,hits,message}`，命中不暂停）；**普通（非调试）流急停另发 `cancelled`（`{node_id,reason:"user_cancel"}`，协作式取消、wait/approval/tool 阻塞中点不强杀、下一节点边界生效）**，恢复走 POST /api/debug/{token}/resume（B 包起 step/continue 可带 globals 浅合并改写），急停走 POST /api/runs/{id}/cancel；**docs/28 批2（f6f7d30）起子图内部可产生 paused/debug_log（子层帧带 subgraphPath、整图终帧仍唯一，契约 04 §5.12）**；**docs/28 §4.1 ⑧（a318d97）起真实工具节点结束另发内部事件 `tool_metric`（`{node_id,tool,duration_ms,action_status:"SUCCESS"|"FAILED"|"SIMULATED",error_code}`，顶层无 subgraphPath；mock 命中不发、子图内被吞、仅监控采集不驱动画布，前端 RunEvent 联合含该型并忽略展示）** | 08 7.1 |

| GET | /api/runs | 挂起/运行查询（**M5 契约设计轮 2026-09-17 登记，docs/24 §4，2026-09-17 已随 M5b 落码生效**）：`?status=suspended&graph_id=&limit=`（新→旧，status 缺省=全部），返回 `{items:[{runId, graphId, status, startedAt, suspendedAt, kind?, nodeId?, deadlineAt?}]}`，`kind=approval` 附 `resumeToken`（审批决策本就凭 token 无身份绑定，沿用现状口径）；仅本租户 | runs |
| GET | /api/runs/{run_id} | 单运行详情（同上 M5b 已落码生效）：状态（running/suspended/completed/failed/cancelled/interrupted，**B 包 f9a1301 增 cancelled**）、节点产出摘要、trace、挂起信息 `{runId, graphId, status, outputs, trace, suspension?}`；跨租户/不存在 404（04 §5.14 全分区口径，不泄漏存在性）；**不新增写端点**——审批决策仍 `POST /api/approvals/{token}/decision`、调试恢复仍 `POST /api/debug/{token}/resume`，本端点只承担查询与 SSE 断线重连后的状态确认（执行线程与 SSE 连接解耦，断开不取消执行） | runs |
| POST | /api/runs/{run_id}/cancel | **B 包（f9a1301）新增**：协作式急停（operate；viewer 403）。置位本租户该 run 的取消事件，下一节点边界抛 RunCancelled 收敛——run_store/monitoring 记 `status=cancelled`（不触发 evaluate_after_run、不刷 streak/告警），SSE 发 `cancelled` 帧后关流。响应 `{run_id,cancelled:true}`：窗口内命中（含重复取消）200 幂等；run 不存在/跨租户 404；run 已结束、broker 无注册句柄 409。worker 在请求线程 register/finally unregister；wait 同步 sleep 中 cancel 返 200 但于 sleep 结束后下一节点边界才生效 | runs / debug_session |
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
> subgraph 节点（Phase 2 第六项，v1 进程内引用式）运行结果写入 `outputs[subgraph_id] = {mode:"subgraph", graphId, status:"success"|"failed", error?, outputs:<子图全部节点 outputs>, trace:[string]}`；执行器经 `compile_graph/run_graph(graph_resolver=<Callable[[str], GraphDSL]>)` 注入的解析器（Demo 为 GraphStore.get）按 config.graphId 取子图，config.inputs（`{<子图入参键>: "<父图 {{路径}}/字面量>"}`）用父图上下文渲染后作为子图 run inputs 重入 run_graph（**A 包 `e594a4b` 起经命名空间 emit 把子图内部 node_start/node_end 以 subgraphPath 上屏、吞子层 run_end**，此前 v1 为 emit=None；复用同一 registry/decision_client/approval_broker）。编译期递归校验：graphId 可解析、禁自引用与跨图环、嵌套深度 ≤3、子图递归过 validate_graph（错误中文聚合）；运行期任何异常 fail-safe 为 status:"failed"、父 run 仍 completed 沿唯一普通出边继续。trace 增 `subgraph-x: graph-7 success (N nodes)` / `… failed: <error>` 行。下游引用形如 `{{subgraph-x.status}}`、`{{subgraph-x.outputs.<子图节点id>.<键>}}`。版本钉版/子图市场/远程引用缓做 14 D21，语义见 04 §5.7、06 §6.1。
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
| GET | /api/memories | 【viewer+，M11 已落码 `58d936c`】列出本租户记忆，query `?kind=fact|preference&limit=`（默认 50、上限 200），返 `{items:[memory_item…]}`，不含 embedding | memory_item |
| GET | /api/memories/search | 【viewer+，M11】语义检索，query `?q=&kind=&top_k=&min_score=`；q 空白 → 422；返 `{results:[{…memory_item, score}]}` 按 score 降序 | memory_item |
| DELETE | /api/memories/{id} | 【**admin only**，M11】删除一条记忆；他租户/不存在 → 404 | memory_item |
| POST | /api/memories | 【**operate**，docs/28 批 4⑩ 2026-09-20 `ec0fd81`】手动新建记忆，body `{kind, content, scope?:{str:str}, confidence?:0-1, metadata?:{str:str}}`；**source 固定 manual 不接受入参**（extra=forbid，传 source/id → 422），201 返 memory_item，校验失败 422 中文 | memory_item |
| PUT | /api/memories/{id} | 【**operate**，批 4⑩】编辑白名单字段任意子集（exclude_unset，空体 422）；source 归 manual、content 变才重算 embedding、id/created_at 不变；不存在/他租户 → 404，校验失败 422 | memory_item |
| POST | /api/connections | 【**administer**，docs/35 T4 2026-09-22 `a57164d`，201】建 OAuth2 连接（provider/displayName/authUrl/tokenUrl/clientId 必填，创建即对 authUrl/tokenUrl 过 EgressGuard 真实 DNS，不可解析 400）；secret 一律信封存储，返 public_view（无任何信封/明文） | connection |
| GET | /api/connections | 【**read**，T4】列出本租户连接 `{items:[connection 投影…]}`（不含秘密） | connection |
| GET | /api/connections/{id} | 【**read**，T4】取单连接投影；他租户/不存在 → 404 | connection |
| PUT | /api/connections/{id} | 【**administer**，T4】更新连接；clientSecret 缺省/空串保留原信封；他租户/不存在 → 404 | connection |
| DELETE | /api/connections/{id} | 【**administer**，T4】删除，返 `{deleted:true}`；reset 不清连接 | connection |
| POST | /api/connections/{id}/authorize | 【**operate**，T4】签发授权：返 `{authorizeUrl,state,expiresIn:600}`（HMAC 自签名 state 防 CSRF，本步不触网） | connection |
| POST | /api/connections/{id}/exchange | 【**operate**，T4】body `{code,state}`，校验 state 后换 token 并加密落库、置 connected；state 非法 400 不改状态，token 端点异常置 error+last_error 抛 502 | connection |
| POST | /api/connections/{id}/refresh | 【**operate**，T4】用 refresh_token 刷新访问令牌（过期前 60s 视为过期）；异常置 error 抛 502 | connection |
| POST | /api/connections/{id}/test | 【**operate**，T4】仅验证令牌状态（过期先刷新；draft/error 返 `{ok:false,reason}`，**不调真实业务 API**） | connection |
| GET | /connections/callback | 【**无鉴权**，T4】OAuth 提供方回调落地静态 HTML 页（include_in_schema=False，注册于 StaticFiles 挂载前优先匹配；纯静态引导用户回填 code/state，无副作用、不读 query 外秘密） | — |
| GET | /api/audit/events | 【**administer**，docs/35 T6 `f243e04`】分页查本租户审计事件（query limit/offset/actor/action），仅 8 元数据字段、绝无请求体/凭据 | audit_event |
| GET | /api/audit/export | 【**administer**，T6】`?format=jsonl` 导出审计（StreamingResponse 附件，逐行 JSON）；reset 不清审计 | audit_event |

> **M11 记忆端点口径订正（2026-09-19，docs/26；批 4⑩ 2026-09-20 修订）**：上表取代原愿景 `GET/PUT /api/memories/{operator_id}`（memory_config 配置读写，05 §2.4）——五层策略配置随 D35 缓做，operator 维度降为记忆条目 `scope.user_id`，租户由会话 Principal 定。**初版 M11 写入只走图工具 `memory/remember`（手动造数走 `scripts/dev/m11_seed.py`）；docs/28 批 4⑩（`ec0fd81`）起补开 `POST/PUT /api/memories`（operate，source 固定 manual）承担运营手动新建/编辑**——图工具仍是运行时自动写入主路径，REST 为手动补录/纠错通道，删除仍仅 admin。

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
- [ ] 记忆检索分层与 memory_config 阈值（05 2.3）——**愿景，M11 不实现、缓做 D35**（working/summary/case/决策隐式注入）
- [x] **M11（2026-09-19 四批落码收口 `5441902`/`58d936c`/`73e53bd`/`75c4150`，docs/26/ADR T23；U72–U99 转正式）**：MemoryItem fact/preference 字段与 EMBED_DIM=256 迁移严格一致；EmbeddingProvider 本地确定性（纯 stdlib、录制回放确定）；MemoryRepository 第九个两档（进程内 / pgvector）remember/recall/list/**update**/delete/clear；memory/remember(write)·memory/recall(read) 两能力 schema 过 Capability 白名单；REST `GET /api/memories`、`GET /api/memories/search`（viewer+）+ `DELETE /api/memories/{id}`（admin）+ docs/28 批 4⑩ `POST/PUT /api/memories`（operate，source=manual，U192–U201）；批 4⑪ `GET /api/graphs/{id}/subgraph-upgrades`（read，U202–U211）；reset 清空、跨租户 404
- [ ] 评估指标三元组（06 9.2 metrics）

---

*本文为新增接口汇总文档；所有接口签名均有原文依据（06 代码示例 / 04 组件设计 / 05 Schema），REST 端点路径为按 Demo 需求推导、标注"推导"，落码时以 OpenAPI 正式化为准。*
