"""打包 AL（docs/123）：全局异常降级 + selfHealing 自愈 + state_machine 状态机（U1345–U1356）。

基线：D63 三块此前仅在 DSL 声明、loader 从未读取（docs/08 打包 AL 立项条）——
本套件钉住 globalErrorHandler 三形态（mark_only/route_to_subgraph/escalate_to_human，
P-1 拍板 C）、selfHealing 诊断子图自愈、state_machine 事件驱动迁移（P-2 拍板 B：
memory 档跨 run 持续）、控制流穿透与编译期五码校验。
"""

from __future__ import annotations

import threading
import time

import pytest

from atlas.graph.dsl import (
    GraphDSL,
    GraphValidationError,
    parse_graph,
    validate_graph_report,
)
from atlas.graph.loader import (
    RunNodeFailed,
    StateTransitionRejected,
    run_graph,
)
from atlas.harness.base import ActionResult, ActionStatus, Capability
from atlas.harness.registry import AdapterRegistry
from atlas.memory.items import MemoryStore


class _FailAdapter:
    adapter_id = "x"
    adapter_type = "test"
    calls = 0

    def list_capabilities(self):
        return [Capability(name="y", description="d", action="act")]

    def execute(self, req):
        _FailAdapter.calls += 1
        return ActionResult(status=ActionStatus.FAILED, output={}, error=None)


class _CrashAdapter:
    """执行即抛未预期异常（全局 handler 兜底路径）。"""

    adapter_id = "crash"
    adapter_type = "test"
    calls = 0

    def list_capabilities(self):
        return [Capability(name="y", description="d", action="act")]

    def execute(self, req):
        _CrashAdapter.calls += 1
        raise RuntimeError("boom")


class _OkAdapter:
    """成功产出 dict（verdict 载体：诊断子图节点产出 {"verdict": ...}）。"""

    adapter_id = "ok"
    adapter_type = "test"

    def __init__(self, verdict=True):
        self.verdict = verdict

    def list_capabilities(self):
        return [Capability(name="y", description="d", action="act")]

    def execute(self, req):
        return ActionResult(
            status=ActionStatus.SUCCESS,
            output={"verdict": self.verdict},
            error=None,
        )


def _registry(*adapters) -> AdapterRegistry:
    registry = AdapterRegistry()
    for adapter in adapters:
        registry.register(adapter)
    return registry


def _graph(handler=None, *, heal=None, nodes_extra=None, edges_extra=None):
    """通用图：t → a（tool x/y，on_error=stop）→ b（tool x/y，continue）。

    handler/selfHealing 为顶层配置；nodes_extra/edges_extra 供测试加诊断子图等。
    """
    nodes = [
        {"id": "t", "type": "trigger", "name": "t",
         "config": {"triggerType": "manual"}},
        {"id": "a", "type": "tool_call", "name": "a",
         "config": {"tool": "x/y"}, "retry": {"on_error": "stop"}},
        {"id": "b", "type": "tool_call", "name": "b",
         "config": {"tool": "x/y"}, "retry": {"on_error": "continue"}},
    ]
    edges = [
        {"id": "e1", "source": "t", "target": "a"},
        {"id": "e2", "source": "a", "target": "b"},
    ]
    if nodes_extra:
        nodes.extend(nodes_extra)
    if edges_extra:
        edges.extend(edges_extra)
    graph: dict[str, object] = {
        "version": 1,
        "variables": [],
        "nodes": nodes,
        "edges": edges,
    }
    if handler is not None:
        graph["globalErrorHandler"] = handler
    if heal is not None:
        graph["selfHealing"] = heal
    return parse_graph(graph)


# --- U1345 mark_only -----------------------------------------------------------


def test_u1345_mark_only_fails_run_with_structured_global_error():
    _FailAdapter.calls = 0
    graph = _graph({"onUncaughtError": "mark_only"})
    result = run_graph(graph, registry=_registry(_FailAdapter()))
    assert result["status"] == "failed"
    assert "global_error" in result
    assert result["global_error"]["code"] == "NODE_EXECUTION_FAILED"
    assert result["global_error"]["nodeId"] == "a"
    # 节点级 stop 失败被图级 handler 接管：outputs 不含 a（fail 语义由 global_error 表达）。
    assert _FailAdapter.calls == 1


# --- U1349 控制流异常不触发 -----------------------------------------------------

def test_u1349_control_flow_exception_not_swallowed():
    """无 handler 时 stop 依旧原样抛 RunNodeFailed（历史语义不回退）；
    有 handler 时 RunNodeFailed 才被图级接管。"""
    _FailAdapter.calls = 0
    graph = _graph(None)
    with pytest.raises(RunNodeFailed) as excinfo:
        run_graph(graph, registry=_registry(_FailAdapter()))
    assert excinfo.value.node_id == "a"


# --- U1346 route_to_subgraph ---------------------------------------------------

def _diag_graph(verdict=True):
    """含诊断子图节点 s（subgraph→g），以及被引用子图 g 的单节点 verdict 图。"""
    return _graph(
        {"onUncaughtError": "route_to_subgraph", "errorSubgraphId": "s"},
        nodes_extra=[
            {"id": "s", "type": "subgraph", "name": "s",
             "config": {"graphId": "g"}},
        ],
        edges_extra=[
            {"id": "e3", "source": "t", "target": "s"},
            # 子图节点须恰好 1 条出边（DSL 校验）；handler 直接执行 s 不走图边，
            # 出边仅满足编译期约束，指向无实义下游。
            {"id": "e4", "source": "s", "target": "b"},
        ],
    ), {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "v", "type": "tool_call", "name": "v",
             "config": {"tool": "ok/y"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "v"}],
    }


def test_u1346_route_to_subgraph_verdict_true_completes_with_healed_by():
    _FailAdapter.calls = 0
    graph, sub = _diag_graph(verdict=True)
    registry = _registry(_FailAdapter(), _OkAdapter(verdict=True))

    def resolver(graph_id: str):
        if graph_id == "g":
            return GraphDSL.model_validate(sub)
        raise KeyError(graph_id)

    result = run_graph(graph, registry=registry, graph_resolver=resolver)
    assert result["status"] == "completed"
    assert result["healed_by"] == "s"
    assert result["verdict"] is True
    assert result["global_error"]["code"] == "NODE_EXECUTION_FAILED"


def test_u1346_route_to_subgraph_verdict_false_fails():
    _FailAdapter.calls = 0
    graph, sub = _diag_graph(verdict=False)
    registry = _registry(_FailAdapter(), _OkAdapter(verdict=False))

    def resolver(graph_id: str):
        if graph_id == "g":
            return GraphDSL.model_validate(sub)
        raise KeyError(graph_id)

    result = run_graph(graph, registry=registry, graph_resolver=resolver)
    assert result["status"] == "failed"
    assert result["global_error"]["code"] == "NODE_EXECUTION_FAILED"


# --- U1347 escalate_to_human ---------------------------------------------------

def test_u1347_escalate_approved_completes_run():
    _CrashAdapter.calls = 0
    graph = _graph(
        {
            "onUncaughtError": "escalate_to_human",
            "escalation": {"channel": "approval", "recipients": ["ops@x.com"]},
        }
    )
    broker = _ResolvableBroker("approved")
    result = run_graph(
        graph,
        registry=_registry(_CrashAdapter()),
        approval_broker=broker,
    )
    assert result["status"] == "completed"
    assert result["escalated"] is True
    assert result["global_error"]["code"] == "NODE_EXECUTION_FAILED"


def test_u1347_escalate_rejected_fails_run():
    _CrashAdapter.calls = 0
    graph = _graph(
        {
            "onUncaughtError": "escalate_to_human",
            "escalation": {"channel": "approval", "recipients": ["ops@x.com"]},
        }
    )
    result = run_graph(
        graph,
        registry=_registry(_CrashAdapter()),
        approval_broker=_ResolvableBroker("rejected"),
    )
    assert result["status"] == "failed"
    assert result["escalated"] is True


class _ResolvableBroker:
    """审批 broker stub：request 记录 token，wait 立即返回预置决策。"""

    def __init__(self, decision: str):
        self._decision = decision
        self.tokens: list[str] = []
        self.pending: dict[str, str] = {}

    def request(self, **kwargs) -> str:
        token = f"tok-{len(self.tokens)}"
        self.tokens.append(token)
        self.pending[token] = "pending"
        return token

    def wait(self, token: str):
        self.pending[token] = self._decision
        return self._decision

    def complete_timeout(self, token: str, decision: str):
        self.pending[token] = decision
        return decision, "timeout"


# --- U1348 handler 自身异常兜底 -------------------------------------------------

def test_u1348_handler_own_error_falls_back_to_mark_only():
    class _BrokenBroker(_ResolvableBroker):
        def request(self, **kwargs) -> str:
            raise RuntimeError("broker down")

    _CrashAdapter.calls = 0
    graph = _graph(
        {
            "onUncaughtError": "escalate_to_human",
            "escalation": {"channel": "approval"},
        }
    )
    result = run_graph(
        graph,
        registry=_registry(_CrashAdapter()),
        approval_broker=_BrokenBroker("approved"),
    )
    assert result["status"] == "failed"
    assert result["global_error"]["code"] == "NODE_EXECUTION_FAILED"
    assert "handler_error" in result


# --- U1350/1351 selfHealing -----------------------------------------------------

def _heal_graph(*, max_attempts=1, continue_when="", verdict=True, diag_fail=False):
    """图：t → a（crash）→ b（continue）；selfHealing 指向诊断子图节点 s。"""
    nodes = [
        {"id": "t", "type": "trigger", "name": "t",
         "config": {"triggerType": "manual"}},
        {"id": "a", "type": "tool_call", "name": "a",
         "config": {"tool": "crash/y"}, "retry": {"on_error": "stop"}},
        {"id": "b", "type": "tool_call", "name": "b",
         "config": {"tool": "x/y"}, "retry": {"on_error": "continue"}},
        {"id": "s", "type": "subgraph", "name": "s",
         "config": {"graphId": "g"}},
    ]
    edges = [
        {"id": "e1", "source": "t", "target": "a"},
        {"id": "e2", "source": "a", "target": "b"},
        {"id": "e3", "source": "t", "target": "s"},
        {"id": "e4", "source": "s", "target": "b"},
    ]
    healing = {
        "triggerOn": ["node_failed"],
        "diagnoseSubgraphId": "s",
        "maxAttempts": max_attempts,
        "continueWhen": continue_when,
    }
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": nodes,
            "edges": edges,
            "selfHealing": healing,
        }
    )
    sub = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "v", "type": "tool_call", "name": "v",
             "config": {"tool": "ok/y"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "v"}],
    }
    if diag_fail:
        sub["nodes"][1]["config"]["tool"] = "x/y"
    return graph, sub


def test_u1350_self_healing_continue_true_marks_healed_and_continues():
    _CrashAdapter.calls = 0
    graph, sub = _heal_graph(continue_when="true", verdict=True)
    registry = _registry(_CrashAdapter(), _OkAdapter(verdict=True))

    def resolver(graph_id: str):
        if graph_id == "g":
            return GraphDSL.model_validate(sub)
        raise KeyError(graph_id)

    result = run_graph(graph, registry=registry, graph_resolver=resolver)
    assert result["status"] == "completed"
    healed = result["outputs"]["a"]
    assert healed["healed_by"] == "s"
    assert healed["result"]["status"] == "HEALED"
    # 诊断产出并入 outputs（命名空间 node:healed:key）
    assert any(k.startswith("a:healed:") for k in result["outputs"])


def test_u1350_self_healing_continue_false_falls_through_to_global_handler():
    _CrashAdapter.calls = 0
    graph, sub = _heal_graph(continue_when="false", verdict=True)
    registry = _registry(_CrashAdapter(), _OkAdapter(verdict=True))

    def resolver(graph_id: str):
        if graph_id == "g":
            return GraphDSL.model_validate(sub)
        raise KeyError(graph_id)

    # 无全局 handler：自愈判定假 → 重抛 RunNodeFailed。
    with pytest.raises(RunNodeFailed):
        run_graph(graph, registry=registry, graph_resolver=resolver)


def test_u1351_max_attempts_exhausted_healing_fails_through():
    _CrashAdapter.calls = 0
    graph, sub = _heal_graph(max_attempts=2, continue_when="false", verdict=False)
    registry = _registry(_CrashAdapter(), _OkAdapter(verdict=False))

    def resolver(graph_id: str):
        if graph_id == "g":
            return GraphDSL.model_validate(sub)
        raise KeyError(graph_id)

    with pytest.raises(RunNodeFailed):
        run_graph(graph, registry=registry, graph_resolver=resolver)


# --- U1352/1353 state_machine ---------------------------------------------------

def _state_graph(*, store="memory", event="start", transitions=None):
    if transitions is None:
        transitions = [
            {"event": "start", "from": "pending", "to": "active"},
        ]
    nodes = [
        {"id": "t", "type": "trigger", "name": "t",
         "config": {"triggerType": "manual"}},
        {"id": "sm", "type": "state_machine", "name": "sm",
         "config": {
             "objectRef": "order-1",
             "event": event,
             "states": ["pending", "active", "closed"],
             "initial": "pending",
             "store": store,
             "transitions": transitions,
         }},
    ]
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": nodes,
            "edges": [{"id": "e1", "source": "t", "target": "sm"}],
        }
    )


def test_u1352_state_machine_valid_transition_memory_store():
    graph = _state_graph(event="start")
    memory = MemoryStore()
    events: list[dict] = []
    result = run_graph(graph, registry=_registry(), memory_repo=memory, emit=events.append)
    assert result["status"] == "completed"
    out = result["outputs"]["sm"]
    assert out["state"] == "active"
    assert out["from"] == "pending"
    assert out["to"] == "active"
    # SSE state_changed 帧
    frames = [e for e in events if e.get("type") == "state_changed"]
    assert len(frames) == 1
    assert frames[0]["objectRef"] == "order-1"
    assert frames[0]["from"] == "pending"
    assert frames[0]["to"] == "active"
    # memory 档持久化（P-2=B）
    items = memory.list(kind="fact", limit=200)
    assert any(item["scope"].get("object_id") == "order-1" for item in items)


def test_u1352_state_machine_cross_run_persists_memory():
    """memory 档跨 run 持续：第一次 start→active，第二次 advance→closed。"""
    graph1 = _state_graph(event="start")
    graph2 = _state_graph(
        event="advance",
        transitions=[
            {"event": "advance", "from": "active", "to": "closed"},
        ],
    )
    memory = MemoryStore()
    run_graph(graph1, registry=_registry(), memory_repo=memory)
    result2 = run_graph(graph2, registry=_registry(), memory_repo=memory)
    assert result2["outputs"]["sm"]["state"] == "closed"
    assert result2["outputs"]["sm"]["from"] == "active"


def test_u1353_state_machine_invalid_transition_rejected():
    graph = _state_graph(
        event="advance",
        transitions=[{"event": "advance", "from": "active", "to": "closed"}],
    )
    # 初始 pending，事件 advance 无匹配 → StateTransitionRejected
    with pytest.raises(StateTransitionRejected) as excinfo:
        run_graph(_state_graph(event="advance"), registry=_registry(), memory_repo=MemoryStore())
    assert excinfo.value.code == "STATE_TRANSITION_REJECTED"
    assert excinfo.value.from_state == "pending"
    assert excinfo.value.event == "advance"


# --- U1354 编译期五类非法 → 422 --------------------------------------------------

def test_u1354_invalid_global_error_strategy_rejected():
    with pytest.raises(GraphValidationError) as excinfo:
        _graph({"onUncaughtError": "nope"})
    assert "GLOBAL_ERROR_HANDLER_INVALID" in excinfo.value.codes


def test_u1354_route_to_subgraph_requires_existing_subgraph():
    with pytest.raises(GraphValidationError) as excinfo:
        _graph({"onUncaughtError": "route_to_subgraph", "errorSubgraphId": "ghost"})
    assert "GLOBAL_ERROR_HANDLER_INVALID" in excinfo.value.codes


def test_u1354_self_healing_requires_subgraph_diagnose():
    with pytest.raises(GraphValidationError) as excinfo:
        parse_graph(
            {
                "version": 1,
                "variables": [],
                "nodes": [
                    {"id": "t", "type": "trigger", "name": "t",
                     "config": {"triggerType": "manual"}},
                ],
                "edges": [],
                "selfHealing": {"diagnoseSubgraphId": "ghost"},
            }
        )
    assert "SELF_HEALING_INVALID" in excinfo.value.codes


def test_u1354_state_machine_invalid_config_rejected():
    with pytest.raises(GraphValidationError) as excinfo:
        parse_graph(
            {
                "version": 1,
                "variables": [],
                "nodes": [
                    {"id": "t", "type": "trigger", "name": "t",
                     "config": {"triggerType": "manual"}},
                    {"id": "sm", "type": "state_machine", "name": "sm",
                     "config": {"objectRef": "o", "event": "e"}},
                ],
                "edges": [{"id": "e1", "source": "t", "target": "sm"}],
            }
        )
    assert "STATE_MACHINE_INVALID" in excinfo.value.codes
