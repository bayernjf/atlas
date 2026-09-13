"""DSL → LangGraph 编译器（08 §7.1 W7-W8）。

将编辑器 Graph JSON（docs/04 §5.2 node_schema）编译为 LangGraph
StateGraph：每个 DSL 节点成为一个图节点，边按 DSL 原样装配，
无前驱节点接 START、无后继节点接 END。

W7-W8 三类节点（trigger/ai_decision/tool_call）的执行器为确定性占位，
不调 LLM/Harness（随 T1 LiteLLM 接入替换 ai_decision、随适配器注册
联动替换 tool_call），但变量插值、节点产出与执行轨迹均真实运行。
"""

from __future__ import annotations

import operator
import re
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .dsl import GraphDSL, NodeDSL

_TEMPLATE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_PATH_SEGMENT_RE = re.compile(r"[^.[\]]+|\[\d+\]")


class GraphState(TypedDict):
    variables: dict
    outputs: dict[str, dict[str, Any]]
    messages: Annotated[list[str], operator.add]
    status: str


def interpolate(template: str, context: dict[str, Any]) -> str:
    """渲染 04 §6.3 {{路径}}；路径缺失时占位符原样保留（与前端一致）。"""

    def replace(match: re.Match[str]) -> str:
        value = resolve_path(match.group(1), context)
        return match.group(0) if value is None else str(value)

    return _TEMPLATE_RE.sub(replace, template)


def resolve_path(path: str, context: dict[str, Any]) -> Any:
    current: Any = context
    for segment in _PATH_SEGMENT_RE.findall(path):
        if segment.startswith("["):
            index = int(segment[1:-1])
            if not isinstance(current, list) or index >= len(current):
                return None
            current = current[index]
        else:
            if not isinstance(current, dict) or segment not in current:
                return None
            current = current[segment]
    return current


def _seed_variables(graph: GraphDSL) -> dict[str, Any]:
    return {"global": {variable.name: variable.value for variable in graph.variables}}


def _make_executor(node: NodeDSL):
    def execute(state: GraphState) -> dict:
        context = {"global": state["variables"].get("global", {}), **state["outputs"]}
        if node.type == "trigger":
            output = {
                "context": {
                    "triggerType": node.config.get("triggerType", "manual"),
                    "cron": node.config.get("cron", ""),
                    "webhookUrl": node.config.get("webhookUrl", ""),
                }
            }
        elif node.type == "ai_decision":
            prompt = interpolate(node.config.get("promptTemplate", ""), context)
            output = {
                "decision": "auto_approve",
                "confidence": float(node.config.get("confidenceThreshold", 0.6)),
                "model": node.config.get("model", ""),
                "prompt_rendered": prompt,
            }
        else:
            params = interpolate(node.config.get("params", ""), context)
            output = {
                "result": {"status": "SUCCESS", "tool": node.config.get("tool", "")},
                "params_rendered": params,
            }
        outputs = {**state["outputs"], node.id: output}
        return {
            "variables": state["variables"],
            "outputs": outputs,
            "status": "running",
            "messages": [f"{node.id}({node.type}): executed"],
        }

    return execute


def compile_graph(graph: GraphDSL):
    """校验由 parse_graph 完成；本函数只负责装配并返回 compiled graph。"""
    builder = StateGraph(GraphState)
    for node in graph.nodes:
        builder.add_node(node.id, _make_executor(node))

    incoming = {edge.target for edge in graph.edges}
    for node in graph.nodes:
        if node.id not in incoming:
            builder.add_edge(START, node.id)

    outgoing: dict[str, list[str]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, []).append(edge.target)
        builder.add_edge(edge.source, edge.target)

    for node in graph.nodes:
        if node.id not in outgoing:
            builder.add_edge(node.id, END)

    return builder.compile()


def initial_state(graph: GraphDSL, *, inputs: dict[str, Any] | None = None) -> GraphState:
    variables = _seed_variables(graph)
    if inputs:
        variables["global"].update(inputs)
    return {"variables": variables, "outputs": {}, "messages": [], "status": "running"}


def run_graph(graph: GraphDSL, *, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    """编译并执行，返回状态/节点产出/轨迹（W7-W8 占位执行器）。"""
    compiled = compile_graph(graph)
    final_state = compiled.invoke(initial_state(graph, inputs=inputs))
    return {
        "status": "completed",
        "outputs": final_state["outputs"],
        "trace": final_state["messages"],
    }
