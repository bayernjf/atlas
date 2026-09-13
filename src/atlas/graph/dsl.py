"""Graph DSL — 编辑器 Graph JSON 的后端契约模型（W7-W8）。

输入为前端 `graphSerializer`（version 1）产物，节点字段形状遵循
docs/04 §5.2 node_schema；本模块只负责结构解析与静态校验，
DSL → LangGraph 编译见 `loader.py`。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SUPPORTED_NODE_TYPES = ("trigger", "ai_decision", "tool_call")

NodeType = str


class GraphValidationError(ValueError):
    """DSL 静态校验失败；errors 收集全部错误，便于一次性回显编辑器。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class Position(BaseModel):
    x: float = 0
    y: float = 0


class RetryConfig(BaseModel):
    max_retries: int = 0
    backoff: str = "1s"
    timeout: int = 30
    on_error: Literal["stop", "continue", "jump_to"] = "stop"


class NodeDSL(BaseModel):
    id: str
    type: NodeType
    name: str
    description: str = ""
    position: Position = Position()
    config: dict[str, Any] = Field(default_factory=dict)
    retry: RetryConfig = Field(default_factory=RetryConfig)


class EdgeDSL(BaseModel):
    id: str
    source: str
    target: str


class GraphVariable(BaseModel):
    name: str
    type: str = "string"
    value: Any = None
    scope: Literal["global"] = "global"


class GraphDSL(BaseModel):
    version: int = 1
    variables: list[GraphVariable] = Field(default_factory=list)
    nodes: list[NodeDSL]
    edges: list[EdgeDSL] = Field(default_factory=list)


def parse_graph(raw: dict[str, Any]) -> GraphDSL:
    """解析前端 Graph JSON 并做静态校验，失败抛 GraphValidationError。"""
    try:
        graph = GraphDSL.model_validate(raw)
    except Exception as exc:
        raise GraphValidationError([f"DSL 解析失败：{exc}"]) from exc
    errors = validate_graph(graph)
    if errors:
        raise GraphValidationError(errors)
    return graph


def validate_graph(graph: GraphDSL) -> list[str]:
    """结构 + 节点配置静态校验；返回错误文案列表（空列表表示通过）。"""
    errors: list[str] = []

    if graph.version != 1:
        errors.append(f"不支持的 Graph 版本：{graph.version}（当前支持 1）")

    if not graph.nodes:
        errors.append("Graph 至少需要一个节点")

    node_ids: set[str] = set()
    for node in graph.nodes:
        if node.id in node_ids:
            errors.append(f"节点 id 重复：{node.id}")
        node_ids.add(node.id)
        if not node.name:
            errors.append(f"节点 {node.id} 名称必填")
        if node.type not in SUPPORTED_NODE_TYPES:
            errors.append(
                f"节点 {node.id} 类型暂不支持：{node.type}"
                f"（W7-W8 支持 {', '.join(SUPPORTED_NODE_TYPES)}）"
            )
            continue
        errors.extend(_validate_node_config(node))

    edge_ids: set[str] = set()
    for edge in graph.edges:
        if edge.id in edge_ids:
            errors.append(f"连线 id 重复：{edge.id}")
        edge_ids.add(edge.id)
        if edge.source not in node_ids:
            errors.append(f"连线 {edge.id} 的 source 节点不存在：{edge.source}")
        if edge.target not in node_ids:
            errors.append(f"连线 {edge.id} 的 target 节点不存在：{edge.target}")
        if edge.source == edge.target:
            errors.append(f"连线 {edge.id} 不允许节点自环：{edge.source}")

    var_names: set[str] = set()
    for variable in graph.variables:
        if variable.name in var_names:
            errors.append(f"全局变量名重复：{variable.name}")
        var_names.add(variable.name)

    return errors


def _validate_node_config(node: NodeDSL) -> list[str]:
    config = node.config
    if node.type == "trigger":
        trigger_type = config.get("triggerType")
        if trigger_type == "cron" and not config.get("cron"):
            return ["定时触发必须填写 Cron 表达式"]
        if trigger_type == "webhook" and not config.get("webhookUrl"):
            return ["Webhook 触发必须填写 URL"]
    elif node.type == "ai_decision":
        if not (config.get("promptTemplate") or "").strip():
            return ["AI 决策必须填写提示词模板"]
    elif node.type == "tool_call":
        if not (config.get("tool") or "").strip():
            return ["工具调用必须选择工具"]
    return []
