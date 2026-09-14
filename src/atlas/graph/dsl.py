"""Graph DSL — 编辑器 Graph JSON 的后端契约模型（W7-W8）。

输入为前端 `graphSerializer`（version 1）产物，节点字段形状遵循
docs/04 §5.2 node_schema；本模块只负责结构解析与静态校验，
DSL → LangGraph 编译见 `loader.py`。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from atlas.graph.conditions import validate_expression

SUPPORTED_NODE_TYPES = ("trigger", "ai_decision", "tool_call", "condition", "loop")

MAX_LOOP_ITERATIONS = 100

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
                f"（当前支持 {', '.join(SUPPORTED_NODE_TYPES)}）"
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

    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.source in node_ids and edge.target in node_ids:
            outgoing.setdefault(edge.source, set()).add(edge.target)
            incoming.setdefault(edge.target, set()).add(edge.source)

    for node in graph.nodes:
        if node.type == "condition":
            errors.extend(_validate_condition_config(node, node_ids, outgoing))

    node_types = {node.id: node.type for node in graph.nodes}
    loop_backedges: set[tuple[str, str]] = set()
    for node in graph.nodes:
        if node.type == "loop":
            loop_errors, backedges = _validate_loop_config(
                node, node_ids, node_types, outgoing, incoming
            )
            errors.extend(loop_errors)
            loop_backedges |= backedges

    errors.extend(_validate_illegal_cycles(graph, loop_backedges))
    errors.extend(_validate_reachability(graph, node_ids, outgoing))

    return errors


def _validate_condition_config(
    node: NodeDSL, node_ids: set[str], outgoing: dict[str, set[str]]
) -> list[str]:
    """condition config 图级校验（契约 04 §5.2）。"""
    errors: list[str] = []
    prefix = f"条件节点 {node.id}"
    config = node.config

    branches = config.get("branches")
    if not isinstance(branches, list) or not branches:
        errors.append(f"{prefix} 至少需要一个分支（branches）")
        branches = []

    default_target = config.get("defaultTarget")
    if not isinstance(default_target, str) or not default_target.strip():
        errors.append(f"{prefix} 必须配置默认分支（defaultTarget）")
        default_target = None

    labels: set[str] = set()
    targets: set[str] = set()
    for index, branch in enumerate(branches):
        if not isinstance(branch, dict):
            errors.append(f"{prefix} 第 {index + 1} 个分支格式不合法")
            continue
        label = branch.get("label")
        expression = branch.get("expression")
        target = branch.get("target")
        if not isinstance(label, str) or not label.strip():
            errors.append(f"{prefix} 第 {index + 1} 个分支名称（label）不能为空")
        elif label in labels:
            errors.append(f"{prefix} 分支名称重复：{label}")
        else:
            labels.add(label)
        if not isinstance(expression, str) or not expression.strip():
            errors.append(f"{prefix} 分支 {label or index + 1} 的表达式不能为空")
        else:
            for expr_error in validate_expression(expression):
                errors.append(f"{prefix} 分支 {label or index + 1} 表达式{expr_error}")
        if not isinstance(target, str) or not target.strip():
            errors.append(f"{prefix} 分支 {label or index + 1} 必须选择目标节点")
        else:
            if target == node.id:
                errors.append(f"{prefix} 分支 {label or index + 1} 不能指向自身")
            elif target not in node_ids:
                errors.append(f"{prefix} 分支 {label or index + 1} 的目标节点不存在：{target}")
            if target in targets:
                errors.append(f"{prefix} 分支目标重复：{target}")
            else:
                targets.add(target)
            if default_target is not None and target == default_target:
                errors.append(f"{prefix} 分支 {label or index + 1} 的目标不能与默认分支相同")

    if default_target is not None:
        if default_target == node.id:
            errors.append(f"{prefix} 默认分支不能指向自身")
        elif default_target not in node_ids:
            errors.append(f"{prefix} 默认分支目标节点不存在：{default_target}")

    edge_targets = outgoing.get(node.id, set())
    if not edge_targets and (branches or default_target):
        errors.append(f"{prefix} 不允许直连结束节点，每个分支都必须有出边")
    for target in targets | ({default_target} if default_target else set()):
        if target in node_ids and target != node.id and target not in edge_targets:
            errors.append(f"{prefix} 缺少到目标节点 {target} 的连线")
    for extra in edge_targets - targets - ({default_target} if default_target else set()):
        errors.append(f"{prefix} 到节点 {extra} 的连线未配置分支（每条出边必须被分支或默认分支覆盖）")

    return errors


def _validate_loop_config(
    node: NodeDSL,
    node_ids: set[str],
    node_types: dict[str, str],
    outgoing: dict[str, set[str]],
    incoming: dict[str, set[str]],
) -> tuple[list[str], set[tuple[str, str]]]:
    """loop config 与拓扑校验（契约 04 §5.3）；返回错误与本节点的合法回边白名单。"""
    errors: list[str] = []
    prefix = f"循环节点 {node.id}"
    config = node.config

    if config.get("mode", "while") != "while":
        errors.append(f"{prefix} v1 仅支持条件循环（mode=while）")

    expression = config.get("continueExpression")
    if not isinstance(expression, str) or not expression.strip():
        errors.append(f"{prefix} 必须填写继续条件表达式（continueExpression）")
    else:
        for expr_error in validate_expression(expression):
            errors.append(f"{prefix} 继续条件表达式{expr_error}")

    max_iterations = config.get("maxIterations")
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        errors.append(f"{prefix} 最大次数（maxIterations）必须是整数")
        max_iterations = None
    elif not 1 <= max_iterations <= MAX_LOOP_ITERATIONS:
        errors.append(f"{prefix} 最大次数需在 1-{MAX_LOOP_ITERATIONS} 之间")

    body_target = config.get("bodyTarget")
    exit_target = config.get("exitTarget")
    if not isinstance(body_target, str) or not body_target.strip():
        errors.append(f"{prefix} 必须选择循环体入口（bodyTarget）")
        body_target = None
    if not isinstance(exit_target, str) or not exit_target.strip():
        errors.append(f"{prefix} 必须选择退出目标（exitTarget）")
        exit_target = None

    if body_target is not None:
        if body_target == node.id:
            errors.append(f"{prefix} 循环体入口不能指向自身")
        elif body_target not in node_ids:
            errors.append(f"{prefix} 循环体入口节点不存在：{body_target}")
    if exit_target is not None:
        if exit_target == node.id:
            errors.append(f"{prefix} 退出目标不能指向自身")
        elif exit_target not in node_ids:
            errors.append(f"{prefix} 退出目标节点不存在：{exit_target}")
    if (
        body_target is not None
        and exit_target is not None
        and body_target in node_ids
        and exit_target in node_ids
        and body_target == exit_target
    ):
        errors.append(f"{prefix} 循环体入口与退出目标不能相同")

    edge_targets = outgoing.get(node.id, set())
    configured = {target for target in (body_target, exit_target) if target in node_ids and target != node.id}
    if not edge_targets and configured:
        errors.append(f"{prefix} 不允许直连结束节点，循环体与退出目标都必须有出边")
    for target in configured:
        if target not in edge_targets:
            errors.append(f"{prefix} 缺少到目标节点 {target} 的连线")
    for extra in edge_targets - configured:
        errors.append(f"{prefix} 到节点 {extra} 的连线未配置（只允许循环体/退出两条出边）")

    backedges: set[tuple[str, str]] = set()
    if body_target in node_ids and body_target != node.id and exit_target not in (None, node.id):
        body = _loop_body_set(body_target, node.id, exit_target, outgoing)

        nested_loops = sorted(member for member in body if node_types.get(member) == "loop")
        for member in nested_loops:
            errors.append(f"{prefix} v1 不支持嵌套循环，循环体内不能包含循环节点：{member}")
        body_triggers = sorted(member for member in body if node_types.get(member) == "trigger")
        for member in body_triggers:
            errors.append(f"{prefix} 循环体内不能包含触发器节点：{member}")

        if exit_target in node_ids and exit_target in _bfs({body_target}, outgoing, stop={node.id}):
            errors.append(f"{prefix} 退出路径只能由循环节点出发，循环体不能直接连到退出目标 {exit_target}")

        returners = _reverse_reachable(node.id, exit_target, incoming)
        stranded = sorted(member for member in body if member not in returners)
        for member in stranded:
            errors.append(f"{prefix} 循环体节点 {member} 没有回到循环节点的路径")

        for member in body:
            if node.id in outgoing.get(member, set()):
                backedges.add((member, node.id))
        if not any(source in body for source in incoming.get(node.id, set())):
            errors.append(f"{prefix} 循环体必须有一条连回循环节点的回边")

    return errors, backedges


def _bfs(start: set[str], outgoing: dict[str, set[str]], *, stop: set[str]) -> set[str]:
    seen: set[str] = set()
    queue = list(start)
    while queue:
        current = queue.pop()
        if current in seen or current in stop:
            continue
        seen.add(current)
        queue.extend(outgoing.get(current, set()) - seen)
    return seen


def _loop_body_set(
    body_target: str, loop_id: str, exit_target: str, outgoing: dict[str, set[str]]
) -> set[str]:
    """循环体：从入口出发、不穿越 loop 节点与退出目标可达的全部节点。"""
    return _bfs({body_target}, outgoing, stop={loop_id, exit_target})


def _reverse_reachable(loop_id: str, exit_target: str, incoming: dict[str, set[str]]) -> set[str]:
    """沿反向边求能回到 loop 节点的节点集合；退出目标不展开，防退出路径被算作回路。"""
    seen = {loop_id}
    queue = [loop_id]
    while queue:
        current = queue.pop()
        if current == exit_target:
            continue
        for predecessor in incoming.get(current, set()):
            if predecessor not in seen:
                seen.add(predecessor)
                queue.append(predecessor)
    return seen


def _validate_illegal_cycles(
    graph: GraphDSL, whitelist: set[tuple[str, str]]
) -> list[str]:
    """U7：移除 loop 白名单回边后，剩余图不允许成环。"""
    adjacency: dict[str, list[str]] = {}
    for edge in graph.edges:
        if (edge.source, edge.target) in whitelist:
            continue
        adjacency.setdefault(edge.source, []).append(edge.target)

    gray: set[str] = set()
    black: set[str] = set()
    cycle_nodes: list[str] = []

    def visit(node_id: str, stack: list[str]) -> bool:
        gray.add(node_id)
        stack.append(node_id)
        for neighbor in adjacency.get(node_id, []):
            if neighbor in black:
                continue
            if neighbor in gray:
                start = stack.index(neighbor)
                cycle_nodes.extend(stack[start:] + [neighbor])
                return True
            if visit(neighbor, stack):
                return True
        stack.pop()
        gray.remove(node_id)
        black.add(node_id)
        return False

    for node in graph.nodes:
        if node.id not in gray and node.id not in black:
            if visit(node.id, []):
                break
    if cycle_nodes:
        return [
            "检测到非法循环依赖（循环只允许经循环节点的循环体回到自身）："
            + " → ".join(cycle_nodes)
        ]
    return []


def _validate_reachability(
    graph: GraphDSL, node_ids: set[str], outgoing: dict[str, set[str]]
) -> list[str]:
    """从 trigger 节点 BFS（04 补充项 1：不可达节点检测）；无 trigger 时跳过。"""
    roots = [node.id for node in graph.nodes if node.type == "trigger" and node.id in node_ids]
    if not roots:
        return []
    reachable: set[str] = set()
    queue = list(roots)
    while queue:
        current = queue.pop()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(outgoing.get(current, set()) - reachable)
    return [f"节点 {node.id} 不可达（没有任何入边路径能到达它）" for node in graph.nodes if node.id not in reachable]


def _validate_node_config(node: NodeDSL) -> list[str]:
    config = node.config
    if node.type == "trigger":
        trigger_type = config.get("triggerType")
        if trigger_type in ("schedule", "cron") and not config.get("cron"):
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
