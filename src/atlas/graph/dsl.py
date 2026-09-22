"""Graph DSL — 编辑器 Graph JSON 的后端契约模型（W7-W8）。

输入为前端 `graphSerializer`（version 1）产物，节点字段形状遵循
docs/04 §5.2 node_schema；本模块只负责结构解析与静态校验，
DSL → LangGraph 编译见 `loader.py`。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from atlas.cards.catalog import get_card
from atlas.graph.conditions import validate_expression

SUPPORTED_NODE_TYPES = (
    "trigger",
    "ai_decision",
    "tool_call",
    "condition",
    "loop",
    "parallel",
    "wait",
    "subgraph",
    "human_approval",
)

MAX_LOOP_ITERATIONS = 100
MIN_PARALLEL_BRANCHES = 2
MAX_PARALLEL_BRANCHES = 10
PARALLEL_JOIN_STRATEGIES = ("all_success", "all_completed", "any_success")
MIN_WAIT_SECONDS = 1
MAX_WAIT_SECONDS = 600
MAX_SUBGRAPH_DEPTH = 3
MIN_APPROVAL_TIMEOUT = 10
MAX_APPROVAL_TIMEOUT = 3600
APPROVAL_TIMEOUT_ACTIONS = ("approve", "reject")
MAX_NOTIFY_EMAILS = 5  # docs/35 §2.1：单个审批节点通知邮箱上限

NodeType = str

# (中文文案, 定位)；定位 None 表示图级/拓扑级错误，不出 locations 条目。
Issue = tuple[str, dict[str, Any] | None]


def _escape_pointer(token: str) -> str:
    """RFC 6901 token 转义：~ → ~0，/ → ~1。"""
    return token.replace("~", "~0").replace("/", "~1")


def _loc(node_id: str | None = None, pointer: str | None = None) -> dict[str, Any] | None:
    location: dict[str, Any] = {}
    if node_id is not None:
        location["nodeId"] = node_id
    if pointer is not None:
        location["pointer"] = pointer
    return location or None


class GraphValidationError(ValueError):
    """DSL 静态校验失败；errors 收集全部错误，便于一次性回显编辑器。

    locations 为 422 稀疏侧车（04 §6.5/06 §6.13）：每项 ``{"index", nodeId?, pointer?}``，
    index 对齐 errors 下标；图级错误无条目。
    """

    def __init__(self, errors: list[str], locations: list[dict[str, Any]] | None = None):
        self.errors = errors
        self.locations = locations or []
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


class _Issues:
    """收集 (文案, 定位) 并在追加时把定位换算成 index 对齐的稀疏侧车。"""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.locations: list[dict[str, Any]] = []

    def add(
        self,
        message: str,
        *,
        node_id: str | None = None,
        pointer: str | None = None,
    ) -> None:
        location = _loc(node_id, pointer)
        if location is not None:
            self.locations.append({"index": len(self.messages), **location})
        self.messages.append(message)

    def extend(self, issues: list[Issue]) -> None:
        for message, location in issues:
            if location is not None:
                self.locations.append({"index": len(self.messages), **location})
            self.messages.append(message)


def parse_graph(raw: dict[str, Any]) -> GraphDSL:
    """解析前端 Graph JSON 并做静态校验，失败抛 GraphValidationError。"""
    try:
        graph = GraphDSL.model_validate(raw)
    except Exception as exc:
        raise GraphValidationError([f"DSL 解析失败：{exc}"]) from exc
    errors, locations = validate_graph_report(graph)
    if errors:
        raise GraphValidationError(errors, locations)
    return graph


def validate_graph(
    graph: GraphDSL,
    tool_output_schemas: dict[str, dict[str, Any]] | None = None,
    *,
    check_refs: bool = False,
) -> list[str]:
    """结构 + 节点配置静态校验；返回错误文案列表（空列表表示通过）。

    tool_output_schemas 为 ``<adapter_id>/<tool> -> output_schema`` 表，
    来自适配器发现（04 §4.9）；缺省时工具深层路径一律放行。
    L2 模板引用规则（04 §6.5，与前端 lib/scope.ts 同构）仅在编译期
    check_refs=True 时启用；解析/保存期不复查，运行期缺失保留原样。
    """
    messages, _ = validate_graph_report(graph, tool_output_schemas, check_refs=check_refs)
    return messages


def validate_graph_report(
    graph: GraphDSL,
    tool_output_schemas: dict[str, dict[str, Any]] | None = None,
    *,
    check_refs: bool = False,
    subgraph_index: dict[str, set[str]] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """同 validate_graph，另返回 index 对齐的稀疏 locations 侧车（06 §6.13）。"""
    issues = _Issues()

    if graph.version != 1:
        issues.add(f"不支持的 Graph 版本：{graph.version}（当前支持 1）")

    if not graph.nodes:
        issues.add("Graph 至少需要一个节点")

    node_ids: set[str] = set()
    for node in graph.nodes:
        if node.id in node_ids:
            issues.add(f"节点 id 重复：{node.id}")
        node_ids.add(node.id)
        if not node.name:
            issues.add(f"节点 {node.id} 名称必填", node_id=node.id)
        if node.type not in SUPPORTED_NODE_TYPES:
            issues.add(
                f"节点 {node.id} 类型暂不支持：{node.type}"
                f"（当前支持 {', '.join(SUPPORTED_NODE_TYPES)}）",
                node_id=node.id,
            )
            continue
        issues.extend(_validate_node_config(node))

    edge_ids: set[str] = set()
    for edge in graph.edges:
        if edge.id in edge_ids:
            issues.add(f"连线 id 重复：{edge.id}")
        edge_ids.add(edge.id)
        if edge.source not in node_ids:
            issues.add(f"连线 {edge.id} 的 source 节点不存在：{edge.source}")
        if edge.target not in node_ids:
            issues.add(f"连线 {edge.id} 的 target 节点不存在：{edge.target}")
        if edge.source == edge.target:
            issues.add(f"连线 {edge.id} 不允许节点自环：{edge.source}")

    var_names: set[str] = set()
    for variable in graph.variables:
        if variable.name in var_names:
            issues.add(f"全局变量名重复：{variable.name}")
        var_names.add(variable.name)

    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.source in node_ids and edge.target in node_ids:
            outgoing.setdefault(edge.source, set()).add(edge.target)
            incoming.setdefault(edge.target, set()).add(edge.source)

    for node in graph.nodes:
        if node.type == "condition":
            issues.extend(_validate_condition_config(node, node_ids, outgoing))

    node_types = {node.id: node.type for node in graph.nodes}
    loop_backedges: set[tuple[str, str]] = set()
    for node in graph.nodes:
        if node.type == "loop":
            loop_issues, backedges = _validate_loop_config(
                node, node_ids, node_types, outgoing, incoming
            )
            issues.extend(loop_issues)
            loop_backedges |= backedges

    for node in graph.nodes:
        if node.type == "parallel":
            issues.extend(
                _validate_parallel_config(node, node_ids, node_types, outgoing, incoming)
            )

    for node in graph.nodes:
        if node.type == "wait":
            issues.extend(_validate_wait_config(node, node_ids, outgoing))

    for node in graph.nodes:
        if node.type == "human_approval":
            issues.extend(_validate_human_approval_config(node, node_ids, outgoing))

    for node in graph.nodes:
        if node.type == "subgraph":
            issues.extend(_validate_subgraph_config(node, node_ids, outgoing))

    for message in _validate_illegal_cycles(graph, loop_backedges):
        issues.add(message)
    for message in _validate_reachability(graph, node_ids, outgoing):
        issues.add(message)
    if check_refs:
        ref_issues, data_edges = _validate_template_refs(
            graph,
            node_ids=node_ids,
            node_types=node_types,
            incoming=incoming,
            outgoing=outgoing,
            global_names=var_names,
            tool_output_schemas=tool_output_schemas or {},
            subgraph_index=subgraph_index or {},
        )
        issues.extend(ref_issues)
        issues.extend(_validate_data_dependency_cycles(data_edges))

    return issues.messages, issues.locations


def _validate_condition_config(
    node: NodeDSL, node_ids: set[str], outgoing: dict[str, set[str]]
) -> list[Issue]:
    """condition config 图级校验（契约 04 §5.2）。"""
    issues: list[Issue] = []
    prefix = f"条件节点 {node.id}"
    config = node.config

    def add(message: str, pointer: str | None = None) -> None:
        issues.append((message, _loc(node.id, pointer)))

    def add_graph(message: str) -> None:
        # 节点级拓扑错误（出边/回路/区域）：挂 nodeId、无字段 pointer（06 §6.13）。
        issues.append((message, _loc(node.id)))

    branches = config.get("branches")
    if not isinstance(branches, list) or not branches:
        add(f"{prefix} 至少需要一个分支（branches）", "/branches")
        branches = []

    default_target = config.get("defaultTarget")
    if not isinstance(default_target, str) or not default_target.strip():
        add(f"{prefix} 必须配置默认分支（defaultTarget）", "/defaultTarget")
        default_target = None

    labels: set[str] = set()
    targets: set[str] = set()
    for index, branch in enumerate(branches):
        if not isinstance(branch, dict):
            add(f"{prefix} 第 {index + 1} 个分支格式不合法", f"/branches/{index}")
            continue
        label = branch.get("label")
        expression = branch.get("expression")
        target = branch.get("target")
        label_pointer = f"/branches/{index}/label"
        expression_pointer = f"/branches/{index}/expression"
        target_pointer = f"/branches/{index}/target"
        if not isinstance(label, str) or not label.strip():
            add(f"{prefix} 第 {index + 1} 个分支名称（label）不能为空", label_pointer)
        elif label in labels:
            add(f"{prefix} 分支名称重复：{label}", label_pointer)
        else:
            labels.add(label)
        if not isinstance(expression, str) or not expression.strip():
            add(f"{prefix} 分支 {label or index + 1} 的表达式不能为空", expression_pointer)
        else:
            for expr_error in validate_expression(expression):
                add(f"{prefix} 分支 {label or index + 1} 表达式{expr_error}", expression_pointer)
        if not isinstance(target, str) or not target.strip():
            add(f"{prefix} 分支 {label or index + 1} 必须选择目标节点", target_pointer)
        else:
            if target == node.id:
                add(f"{prefix} 分支 {label or index + 1} 不能指向自身", target_pointer)
            elif target not in node_ids:
                add(
                    f"{prefix} 分支 {label or index + 1} 的目标节点不存在：{target}",
                    target_pointer,
                )
            if target in targets:
                add(f"{prefix} 分支目标重复：{target}", target_pointer)
            else:
                targets.add(target)
            if default_target is not None and target == default_target:
                add(
                    f"{prefix} 分支 {label or index + 1} 的目标不能与默认分支相同",
                    target_pointer,
                )

    if default_target is not None:
        if default_target == node.id:
            add(f"{prefix} 默认分支不能指向自身", "/defaultTarget")
        elif default_target not in node_ids:
            add(f"{prefix} 默认分支目标节点不存在：{default_target}", "/defaultTarget")

    edge_targets = outgoing.get(node.id, set())
    if not edge_targets and (branches or default_target):
        add_graph(f"{prefix} 不允许直连结束节点，每个分支都必须有出边")
    for target in targets | ({default_target} if default_target else set()):
        if target in node_ids and target != node.id and target not in edge_targets:
            add_graph(f"{prefix} 缺少到目标节点 {target} 的连线")
    for extra in edge_targets - targets - ({default_target} if default_target else set()):
        add_graph(f"{prefix} 到节点 {extra} 的连线未配置分支（每条出边必须被分支或默认分支覆盖）")

    return issues


def _validate_loop_config(
    node: NodeDSL,
    node_ids: set[str],
    node_types: dict[str, str],
    outgoing: dict[str, set[str]],
    incoming: dict[str, set[str]],
) -> tuple[list[Issue], set[tuple[str, str]]]:
    """loop config 与拓扑校验（契约 04 §5.3）；返回错误与本节点的合法回边白名单。"""
    issues: list[Issue] = []
    prefix = f"循环节点 {node.id}"
    config = node.config

    def add(message: str, pointer: str | None = None) -> None:
        issues.append((message, _loc(node.id, pointer)))

    def add_graph(message: str) -> None:
        # 节点级拓扑错误（出边/回路/区域）：挂 nodeId、无字段 pointer（06 §6.13）。
        issues.append((message, _loc(node.id)))

    if config.get("mode", "while") != "while":
        add(f"{prefix} v1 仅支持条件循环（mode=while）", "/mode")

    expression = config.get("continueExpression")
    if not isinstance(expression, str) or not expression.strip():
        add(f"{prefix} 必须填写继续条件表达式（continueExpression）", "/continueExpression")
    else:
        for expr_error in validate_expression(expression):
            add(f"{prefix} 继续条件表达式{expr_error}", "/continueExpression")

    max_iterations = config.get("maxIterations")
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        add(f"{prefix} 最大次数（maxIterations）必须是整数", "/maxIterations")
        max_iterations = None
    elif not 1 <= max_iterations <= MAX_LOOP_ITERATIONS:
        add(
            f"{prefix} 最大次数需在 1-{MAX_LOOP_ITERATIONS} 之间",
            "/maxIterations",
        )

    body_target = config.get("bodyTarget")
    exit_target = config.get("exitTarget")
    if not isinstance(body_target, str) or not body_target.strip():
        add(f"{prefix} 必须选择循环体入口（bodyTarget）", "/bodyTarget")
        body_target = None
    if not isinstance(exit_target, str) or not exit_target.strip():
        add(f"{prefix} 必须选择退出目标（exitTarget）", "/exitTarget")
        exit_target = None

    if body_target is not None:
        if body_target == node.id:
            add(f"{prefix} 循环体入口不能指向自身", "/bodyTarget")
        elif body_target not in node_ids:
            add(f"{prefix} 循环体入口节点不存在：{body_target}", "/bodyTarget")
    if exit_target is not None:
        if exit_target == node.id:
            add(f"{prefix} 退出目标不能指向自身", "/exitTarget")
        elif exit_target not in node_ids:
            add(f"{prefix} 退出目标节点不存在：{exit_target}", "/exitTarget")
    if (
        body_target is not None
        and exit_target is not None
        and body_target in node_ids
        and exit_target in node_ids
        and body_target == exit_target
    ):
        add(f"{prefix} 循环体入口与退出目标不能相同", "/bodyTarget")

    edge_targets = outgoing.get(node.id, set())
    configured = {target for target in (body_target, exit_target) if target in node_ids and target != node.id}
    if not edge_targets and configured:
        add_graph(f"{prefix} 不允许直连结束节点，循环体与退出目标都必须有出边")
    for target in configured:
        if target not in edge_targets:
            add_graph(f"{prefix} 缺少到目标节点 {target} 的连线")
    for extra in edge_targets - configured:
        add_graph(f"{prefix} 到节点 {extra} 的连线未配置（只允许循环体/退出两条出边）")

    backedges: set[tuple[str, str]] = set()
    if body_target in node_ids and body_target != node.id and exit_target not in (None, node.id):
        body = _loop_body_set(body_target, node.id, exit_target, outgoing)

        nested_loops = sorted(member for member in body if node_types.get(member) == "loop")
        for member in nested_loops:
            add_graph(f"{prefix} v1 不支持嵌套循环，循环体内不能包含循环节点：{member}")
        body_triggers = sorted(member for member in body if node_types.get(member) == "trigger")
        for member in body_triggers:
            add_graph(f"{prefix} 循环体内不能包含触发器节点：{member}")

        # D17/A2 break：允许循环体内 condition 节点经其分支直连 exit_target（break 出口）。
        break_sources = {
            member
            for member in body
            if node_types.get(member) == "condition"
            and exit_target in outgoing.get(member, set())
        }
        # 其余体内节点（非 condition）直连退出目标仍属非法逃逸（break 须经 condition 分支）。
        for member in sorted(body):
            if exit_target in outgoing.get(member, set()) and member not in break_sources:
                add_graph(
                    f"{prefix} 退出路径只能由循环节点或体内 condition 的 break 分支出发，"
                    f"循环体节点 {member} 不能直接连到退出目标 {exit_target}"
                )

        returners = _reverse_reachable(node.id, exit_target, incoming)
        # 能沿体内反向走到 break 出口的节点，同样有合法终止路径，不报 stranded。
        break_reachable: set[str] = set()
        if break_sources:
            frontier = set(break_sources)
            while frontier:
                current = frontier.pop()
                if current in break_reachable or current in (exit_target, node.id):
                    continue
                break_reachable.add(current)
                for predecessor in incoming.get(current, set()):
                    if predecessor in body and predecessor not in break_reachable:
                        frontier.add(predecessor)
        stranded = sorted(
            member
            for member in body
            if member not in returners and member not in break_reachable
        )
        for member in stranded:
            add_graph(
                f"{prefix} 循环体节点 {member} 没有回到循环节点或 break 出口的路径"
            )

        for member in body:
            if node.id in outgoing.get(member, set()):
                backedges.add((member, node.id))
        if (
            not any(source in body for source in incoming.get(node.id, set()))
            and not break_sources
        ):
            add_graph(f"{prefix} 循环体必须有一条连回循环节点的回边（或一个 break 出口）")

    return issues, backedges


def _validate_parallel_config(
    node: NodeDSL,
    node_ids: set[str],
    node_types: dict[str, str],
    outgoing: dict[str, set[str]],
    incoming: dict[str, set[str]],
) -> list[Issue]:
    """parallel config 与扇出/汇聚拓扑校验（契约 04 §5.4）。"""
    issues: list[Issue] = []
    prefix = f"并行节点 {node.id}"
    config = node.config

    def add(message: str, pointer: str | None = None) -> None:
        issues.append((message, _loc(node.id, pointer)))

    def add_graph(message: str) -> None:
        # 节点级拓扑错误（出边/回路/区域）：挂 nodeId、无字段 pointer（06 §6.13）。
        issues.append((message, _loc(node.id)))

    strategy = config.get("joinStrategy")
    if strategy not in PARALLEL_JOIN_STRATEGIES:
        add(
            f"{prefix} 合并策略（joinStrategy）必须是 "
            f"{' 或 '.join(PARALLEL_JOIN_STRATEGIES)}",
            "/joinStrategy",
        )

    join_target = config.get("joinTarget")
    if not isinstance(join_target, str) or not join_target.strip():
        add(f"{prefix} 必须选择汇聚目标（joinTarget）", "/joinTarget")
        join_target = None
    elif join_target == node.id:
        add(f"{prefix} 汇聚目标不能指向自身", "/joinTarget")
    elif join_target not in node_ids:
        add(f"{prefix} 汇聚目标节点不存在：{join_target}", "/joinTarget")

    branches = config.get("branches")
    if not isinstance(branches, list):
        add(f"{prefix} 分支列表（branches）格式不合法", "/branches")
        branches = []
    elif not MIN_PARALLEL_BRANCHES <= len(branches) <= MAX_PARALLEL_BRANCHES:
        add(
            f"{prefix} 分支数需在 {MIN_PARALLEL_BRANCHES}-{MAX_PARALLEL_BRANCHES} 个之间"
            f"（当前 {len(branches)} 个）",
            "/branches",
        )

    labels: set[str] = set()
    targets: set[str] = set()
    valid_entries: set[str] = set()
    for index, branch in enumerate(branches):
        if not isinstance(branch, dict):
            add(f"{prefix} 第 {index + 1} 个分支格式不合法", f"/branches/{index}")
            continue
        label = branch.get("label")
        target = branch.get("target")
        label_pointer = f"/branches/{index}/label"
        target_pointer = f"/branches/{index}/target"
        if not isinstance(label, str) or not label.strip():
            add(f"{prefix} 第 {index + 1} 个分支名称（label）不能为空", label_pointer)
        elif label in labels:
            add(f"{prefix} 分支名称重复：{label}", label_pointer)
        else:
            labels.add(label)
        if not isinstance(target, str) or not target.strip():
            add(f"{prefix} 分支 {label or index + 1} 必须选择目标节点", target_pointer)
            continue
        if target == node.id:
            add(f"{prefix} 分支 {label or index + 1} 不能指向自身", target_pointer)
        elif target not in node_ids:
            add(f"{prefix} 分支 {label or index + 1} 的目标节点不存在：{target}", target_pointer)
        if target in targets:
            add(f"{prefix} 分支目标重复：{target}", target_pointer)
        else:
            targets.add(target)
        if join_target is not None and target == join_target:
            add(f"{prefix} 分支 {label or index + 1} 的目标不能与汇聚目标相同", target_pointer)
        if target in node_ids and target != node.id:
            valid_entries.add(target)

    edge_targets = outgoing.get(node.id, set())
    configured = {target for target in targets if target in node_ids and target != node.id}
    if not edge_targets and configured:
        add_graph(f"{prefix} 不允许直连结束节点，每个分支都必须有出边")
    for target in configured:
        if target not in edge_targets:
            add_graph(f"{prefix} 缺少到分支节点 {target} 的连线")
    for extra in edge_targets - configured:
        add_graph(f"{prefix} 到节点 {extra} 的连线未配置分支（出边数必须等于分支数）")

    if (
        valid_entries
        and join_target is not None
        and join_target in node_ids
        and join_target != node.id
    ):
        region = _bfs(valid_entries, outgoing, stop={node.id, join_target})

        nested = sorted(member for member in region if node_types.get(member) == "parallel")
        for member in nested:
            add_graph(f"{prefix} v1 不支持嵌套并行，分支区域内不能包含并行节点：{member}")
        region_triggers = sorted(member for member in region if node_types.get(member) == "trigger")
        for member in region_triggers:
            add_graph(f"{prefix} 分支区域内不能包含触发器节点：{member}")

        for member in sorted(region):
            for leak in outgoing.get(member, set()) - region - {join_target}:
                add_graph(
                    f"{prefix} 分支不得交叉或外泄：区域内节点 {member} 连到了区域外节点 {leak}"
                )

        for entry in sorted(valid_entries):
            reachable = _bfs({entry}, outgoing, stop={node.id})
            if join_target not in reachable:
                add_graph(f"{prefix} 分支 {entry} 不存在到达汇聚目标 {join_target} 的路径")

        outside = sorted(
            source
            for source in incoming.get(join_target, set())
            if source not in region and source != node.id
        )
        for source in outside:
            add_graph(
                f"{prefix} 汇聚目标 {join_target} 只能接收分支区域内的连线：{source} 不在区域内"
            )

    return issues


def _validate_wait_config(
    node: NodeDSL, node_ids: set[str], outgoing: dict[str, set[str]]
) -> list[Issue]:
    """wait config 与单出边拓扑校验（契约 04 §5.5）。"""
    issues: list[Issue] = []
    prefix = f"等待节点 {node.id}"
    config = node.config

    def add(message: str, pointer: str | None = None) -> None:
        issues.append((message, _loc(node.id, pointer)))

    def add_graph(message: str) -> None:
        # 节点级拓扑错误（出边/回路/区域）：挂 nodeId、无字段 pointer（06 §6.13）。
        issues.append((message, _loc(node.id)))

    wait_type = config.get("waitType")
    if wait_type != "duration":
        if wait_type == "event":
            add(f"{prefix} 事件等待（event）暂不支持，v1 仅支持定时等待（duration）", "/waitType")
        else:
            add(f"{prefix} 等待类型（waitType）必须是 duration", "/waitType")

    seconds = config.get("durationSeconds")
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        add(f"{prefix} 等待时长（durationSeconds）必须是整数秒", "/durationSeconds")
    elif not MIN_WAIT_SECONDS <= seconds <= MAX_WAIT_SECONDS:
        add(
            f"{prefix} 等待时长需在 {MIN_WAIT_SECONDS}-{MAX_WAIT_SECONDS} 秒之间"
            f"（当前 {seconds}）",
            "/durationSeconds",
        )

    targets = outgoing.get(node.id, set())
    if len(targets) != 1:
        add_graph(f"{prefix} 必须恰好配置 1 条出边（当前 {len(targets)} 条），且不能直连结束")
    else:
        target = next(iter(targets))
        if target == node.id:
            add_graph(f"{prefix} 出边不能指向自身")
        elif target not in node_ids:
            add_graph(f"{prefix} 后继节点不存在：{target}")

    return issues


def _validate_subgraph_config(
    node: NodeDSL, node_ids: set[str], outgoing: dict[str, set[str]]
) -> list[Issue]:
    """subgraph config 与单出边拓扑校验（契约 04 §5.7）；跨图引用校验在 loader 编译期。"""
    issues: list[Issue] = []
    prefix = f"子图节点 {node.id}"
    config = node.config

    def add(message: str, pointer: str | None = None) -> None:
        issues.append((message, _loc(node.id, pointer)))

    def add_graph(message: str) -> None:
        # 节点级拓扑错误（出边/回路/区域）：挂 nodeId、无字段 pointer（06 §6.13）。
        issues.append((message, _loc(node.id)))

    graph_id = config.get("graphId")
    if not isinstance(graph_id, str) or not graph_id.strip():
        add(f"{prefix} 必须选择引用的已保存子图（graphId）", "/graphId")

    inputs = config.get("inputs", {})
    if not isinstance(inputs, dict):
        add(f"{prefix} 子图入参映射（inputs）必须是对象", "/inputs")
    else:
        input_keys: set[str] = set()
        for key, value in inputs.items():
            if not isinstance(key, str) or not key.strip():
                add(f"{prefix} 入参键名不能为空", "/inputs")
            elif key in input_keys:
                add(f"{prefix} 入参键名重复：{key}", f"/inputs/{_escape_pointer(key)}")
            else:
                input_keys.add(key)
            if not isinstance(value, str) or not value.strip():
                add(
                    f"{prefix} 入参 {key} 的映射值必须是非空文本（父图 {{路径}} 或字面量）",
                    f"/inputs/{_escape_pointer(key)}" if isinstance(key, str) and key else "/inputs",
                )

    targets = outgoing.get(node.id, set())
    if len(targets) != 1:
        add_graph(f"{prefix} 必须恰好配置 1 条出边（当前 {len(targets)} 条），且不能直连结束")
    else:
        target = next(iter(targets))
        if target == node.id:
            add_graph(f"{prefix} 出边不能指向自身")
        elif target not in node_ids:
            add_graph(f"{prefix} 后继节点不存在：{target}")

    return issues


def _validate_human_approval_config(
    node: NodeDSL, node_ids: set[str], outgoing: dict[str, set[str]]
) -> list[Issue]:
    """human_approval config 与双出边拓扑校验（契约 04 §5.6）。"""
    issues: list[Issue] = []
    prefix = f"人机协作节点 {node.id}"
    config = node.config

    def add(message: str, pointer: str | None = None) -> None:
        issues.append((message, _loc(node.id, pointer)))

    def add_graph(message: str) -> None:
        # 节点级拓扑错误（出边/回路/区域）：挂 nodeId、无字段 pointer（06 §6.13）。
        issues.append((message, _loc(node.id)))

    summary = config.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        add(f"{prefix} 必须填写审批说明（summary）", "/summary")

    approver = config.get("approver", "")
    if approver != "" and not isinstance(approver, str):
        add(f"{prefix} 审批人（approver）必须是文本", "/approver")

    # docs/35 §2.1：可选通知邮箱 notifyEmails（string[]，≤5；支持 {{路径}} 插值）。
    notify_emails = config.get("notifyEmails", [])
    if notify_emails is None:
        notify_emails = []
    if not isinstance(notify_emails, list):
        add(f"{prefix} 通知邮箱（notifyEmails）必须是数组", "/notifyEmails")
    else:
        if len(notify_emails) > MAX_NOTIFY_EMAILS:
            add(
                f"{prefix} 通知邮箱（notifyEmails）最多 {MAX_NOTIFY_EMAILS} 个（当前 {len(notify_emails)}）",
                "/notifyEmails",
            )
        for item in notify_emails:
            if not isinstance(item, str):
                add(f"{prefix} 通知邮箱（notifyEmails）每一项必须是文本", "/notifyEmails")
                break
            stripped = item.strip()
            has_placeholder = "{{" in item and "}}" in item
            if not has_placeholder:
                # 静态字面值：空或不含 @ 编译期即非法；含插值的项运行时再过滤。
                if not stripped:
                    add(f"{prefix} 通知邮箱（notifyEmails）不能包含空地址", "/notifyEmails")
                    break
                if "@" not in stripped:
                    add(
                        f"{prefix} 通知邮箱（notifyEmails）不是合法邮箱地址（缺少 @）：{stripped}",
                        "/notifyEmails",
                    )

    card_template_id = config.get("cardTemplateId")
    if card_template_id is not None and card_template_id != "":
        # M8：可选内置卡片 id；不填走 summary 旧路径，非空但目录未命中→编译 422。
        if not isinstance(card_template_id, str):
            add(f"{prefix} 交互卡片（cardTemplateId）必须是卡片 id 文本", "/cardTemplateId")
        elif get_card(card_template_id) is None:
            add(
                f"{prefix} 配置的交互卡片不存在：{card_template_id}",
                "/cardTemplateId",
            )

    seconds = config.get("timeoutSeconds")
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        add(f"{prefix} 超时时长（timeoutSeconds）必须是整数秒", "/timeoutSeconds")
    elif not MIN_APPROVAL_TIMEOUT <= seconds <= MAX_APPROVAL_TIMEOUT:
        add(
            f"{prefix} 超时时长需在 {MIN_APPROVAL_TIMEOUT}-{MAX_APPROVAL_TIMEOUT} 秒之间"
            f"（当前 {seconds}）",
            "/timeoutSeconds",
        )

    on_timeout = config.get("onTimeout", "reject")
    if on_timeout not in APPROVAL_TIMEOUT_ACTIONS:
        add(
            f"{prefix} 超时策略（onTimeout）必须是 "
            f"{' 或 '.join(APPROVAL_TIMEOUT_ACTIONS)}",
            "/onTimeout",
        )

    approved_target = config.get("approvedTarget")
    rejected_target = config.get("rejectedTarget")
    if not isinstance(approved_target, str) or not approved_target.strip():
        add(f"{prefix} 必须选择通过目标（approvedTarget）", "/approvedTarget")
        approved_target = None
    if not isinstance(rejected_target, str) or not rejected_target.strip():
        add(f"{prefix} 必须选择拒绝目标（rejectedTarget）", "/rejectedTarget")
        rejected_target = None

    for label, target, pointer in (
        ("通过", approved_target, "/approvedTarget"),
        ("拒绝", rejected_target, "/rejectedTarget"),
    ):
        if target is not None:
            if target == node.id:
                add(f"{prefix} {label}目标不能指向自身", pointer)
            elif target not in node_ids:
                add(f"{prefix} {label}目标节点不存在：{target}", pointer)
    if (
        approved_target is not None
        and rejected_target is not None
        and approved_target in node_ids
        and rejected_target in node_ids
        and approved_target == rejected_target
    ):
        add(f"{prefix} 通过目标与拒绝目标不能相同", "/approvedTarget")

    edge_targets = outgoing.get(node.id, set())
    configured = {
        target
        for target in (approved_target, rejected_target)
        if target in node_ids and target != node.id
    }
    if len(edge_targets) != 2:
        add_graph(
            f"{prefix} 必须恰好配置 2 条出边（当前 {len(edge_targets)} 条），且不能直连结束"
        )
    elif configured:
        for target in configured:
            if target not in edge_targets:
                add_graph(f"{prefix} 缺少到目标节点 {target} 的连线")
        for extra in edge_targets - configured:
            add_graph(f"{prefix} 到节点 {extra} 的连线未配置（只允许通过/拒绝两条出边）")

    return issues


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


def _validate_node_config(node: NodeDSL) -> list[Issue]:
    config = node.config

    def add(message: str, pointer: str) -> Issue:
        return message, _loc(node.id, pointer)

    if node.type == "trigger":
        trigger_type = config.get("triggerType")
        if trigger_type in ("schedule", "cron") and not config.get("cron"):
            return [add("定时触发必须填写 Cron 表达式", "/cron")]
        if trigger_type == "webhook" and not config.get("webhookUrl"):
            return [add("Webhook 触发必须填写 URL", "/webhookUrl")]
    elif node.type == "ai_decision":
        if not (config.get("promptTemplate") or "").strip():
            return [add("AI 决策必须填写提示词模板", "/promptTemplate")]
    elif node.type == "tool_call":
        if not (config.get("tool") or "").strip():
            return [add("工具调用必须选择工具", "/tool")]
    return []


# --- 04 §6.5 L2 模板引用编译期复查（与前端 lib/scope.ts 同构） --------------

_REF_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

_STATIC_OUTPUT_KEYS: dict[str, tuple[str, ...]] = {
    "ai_decision": ("decision", "prompt_rendered"),
    "condition": ("branch", "target"),
    "loop": ("index", "iterations"),
    "parallel": ("status", "branches", "joinStrategy", "joinTarget"),
    "wait": ("mode", "waitType", "durationSeconds"),
    "subgraph": ("status", "outputs"),
    "human_approval": ("decision", "target", "summary", "approver", "resolvedBy", "comment", "card"),
}

_TRIGGER_CONTEXT_KEYS = ("triggerType", "cron", "webhookUrl", "payload")


def _template_fields(node: NodeDSL) -> list[tuple[str, str]]:
    """节点 config 中可能含 {{路径}} 的字符串字段（04 §6.5）；返回 (RFC6901 pointer, 文本)。"""
    config = node.config
    fields: list[tuple[str, str]] = []

    def push(pointer: str, value: Any) -> None:
        if isinstance(value, str):
            fields.append((pointer, value))

    if node.type == "ai_decision":
        push("/promptTemplate", config.get("promptTemplate"))
    elif node.type == "tool_call":
        push("/params", config.get("params"))
    elif node.type == "condition":
        branches = config.get("branches")
        if isinstance(branches, list):
            for index, branch in enumerate(branches):
                if isinstance(branch, dict):
                    push(f"/branches/{index}/expression", branch.get("expression"))
    elif node.type == "loop":
        push("/continueExpression", config.get("continueExpression"))
    elif node.type == "human_approval":
        push("/summary", config.get("summary"))
        # M8：命中卡片的只读字段 bindings 与 summary 同走本节点作用域 L2 复查
        # （可见集＝该审批节点 visibleAt；action.output 的 {{form.*}} 不经节点作用域）。
        card = get_card(config.get("cardTemplateId") or "")
        if card is not None:
            for section in card.sections:
                if section.type == "fields":
                    for binding in section.bindings:
                        push("/cardTemplateId", binding.value)
    elif node.type == "subgraph":
        inputs = config.get("inputs")
        if isinstance(inputs, dict):
            for key, value in inputs.items():
                push(f"/inputs/{_escape_pointer(key)}", value)
    return fields


def _reverse_bfs(start: str, incoming: dict[str, set[str]]) -> set[str]:
    seen = {start}
    queue = [start]
    while queue:
        current = queue.pop()
        for predecessor in incoming.get(current, set()):
            if predecessor not in seen:
                seen.add(predecessor)
                queue.append(predecessor)
    seen.discard(start)
    return seen


def _schema_has_path(schema: dict[str, Any] | None, segments: list[str]) -> bool:
    """output_schema 深层路径存在性；无 schema/开放对象/oneOf 无法静态判定时放行。"""
    if not schema or "oneOf" in schema:
        return True
    if not segments:
        return True
    head, *rest = segments
    if schema.get("type") == "array" or "items" in schema:
        if head.isdigit():
            items = schema.get("items")
            return _schema_has_path(items if isinstance(items, dict) else None, rest)
        return "items" not in schema
    if "additionalProperties" in schema:
        properties = schema.get("properties")
        if isinstance(properties, dict) and head in properties:
            return _schema_has_path(properties[head], rest)
        additional = schema["additionalProperties"]
        if additional is True:
            return True
        if isinstance(additional, dict):
            return _schema_has_path(additional, rest)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        if head in properties:
            return _schema_has_path(properties[head], rest)
        return False
    return "type" not in schema


def _validate_template_refs(
    graph: GraphDSL,
    *,
    node_ids: set[str],
    node_types: dict[str, str],
    incoming: dict[str, set[str]],
    outgoing: dict[str, set[str]],
    global_names: set[str],
    tool_output_schemas: dict[str, dict[str, Any]],
    subgraph_index: dict[str, set[str]] | None = None,
) -> tuple[list[Issue], list[tuple[str, str, str]]]:
    issues: list[Issue] = []
    # 通过可见性判定的数据依赖边 (引用方 viewer -> 被引节点 provider, 模板字段 pointer)；
    # 节点不存在/不可见/路径非法的引用不纳边（由各自诊断承接）。loop 自身 index/iterations
    # 引用是运行时循环计数、不依赖节点配置产出，不纳边（否则每个 loop 都成自环）。
    edges: list[tuple[str, str, str]] = []
    trigger_ids = {nid for nid in node_ids if node_types.get(nid) == "trigger"}

    loop_bodies: dict[str, set[str]] = {}
    for node in graph.nodes:
        if node.type != "loop":
            continue
        body_target = node.config.get("bodyTarget")
        exit_target = node.config.get("exitTarget")
        if not isinstance(body_target, str) or body_target not in node_ids:
            continue
        stop = {node.id}
        if isinstance(exit_target, str):
            stop.add(exit_target)
        loop_bodies[node.id] = _bfs({body_target}, outgoing, stop=stop)

    # D30/B1：parallel 汇聚区域（同构 loader._parallel_meta）——result.<入口> 仅汇聚点之后可见。
    # entries=branches 目标；region=各入口沿出边 BFS、止于 parallel 自身与 joinTarget（不含二者）。
    parallel_meta: dict[str, tuple[set[str], set[str]]] = {}
    for candidate in graph.nodes:
        if candidate.type != "parallel":
            continue
        cfg = candidate.config
        join_target = cfg.get("joinTarget")
        entries = {
            branch.get("target")
            for branch in cfg.get("branches", [])
            if isinstance(branch, dict) and branch.get("target") in node_ids
        }
        stop = {candidate.id}
        if isinstance(join_target, str):
            stop.add(join_target)
        region: set[str] = set()
        for entry in entries:
            region |= _bfs({entry}, outgoing, stop=stop)
        parallel_meta[candidate.id] = (entries, region)

    visible_cache: dict[str, set[str]] = {}

    def visible_at(viewer: str) -> set[str]:
        if viewer not in visible_cache:
            visible = _reverse_bfs(viewer, incoming)
            visible |= trigger_ids
            visible.discard(viewer)
            visible_cache[viewer] = visible
        return visible_cache[viewer]

    for node in graph.nodes:
        if node.type not in _TEMPLATE_FIELDS_TYPES:
            continue
        fields = _template_fields(node)
        if not fields:
            continue
        prefix = f"节点 {node.id}"
        visible = visible_at(node.id)

        def add(message: str, pointer: str) -> None:
            issues.append((message, _loc(node.id, pointer)))

        for pointer, text in fields:
            for match in _REF_RE.finditer(text):
                path = match.group(1)
                segments = [segment for segment in path.split(".") if segment]
                if not segments:
                    continue
                head = segments[0]
                display = "{{" + path + "}}"

                if head == "global":
                    name = segments[1] if len(segments) > 1 else ""
                    if name not in global_names:
                        add(
                            f"{prefix} 模板引用未声明的全局变量（REF_NODE_NOT_FOUND）：{display}",
                            pointer,
                        )
                    continue

                if head not in node_ids:
                    add(
                        f"{prefix} 模板引用的节点不存在（REF_NODE_NOT_FOUND）：{display}",
                        pointer,
                    )
                    continue

                ref_type = node_types[head]
                loop_self_index = (
                    ref_type == "loop"
                    and head == node.id
                    and len(segments) > 1
                    and segments[1] in ("index", "iterations")
                )
                loop_blocked = (
                    ref_type == "loop"
                    and head != node.id
                    and node.id not in loop_bodies.get(head, set())
                )
                if (
                    (head == node.id and not loop_self_index)
                    or loop_blocked
                    or (head not in visible and not loop_self_index)
                ):
                    add(
                        f"{prefix} 模板引用不可见（REF_NOT_IN_SCOPE：非上游或循环变量越出循环体）："
                        f"{display}",
                        pointer,
                    )
                    continue

                # 通过存在性与可见性判定：记录数据依赖边（loop 自身 index 自引用除外）。
                if not loop_self_index:
                    edges.append((node.id, head, pointer))

                tail = segments[1:]
                if not tail:
                    add(
                        f"{prefix} 模板引用缺少输出字段（REF_PATH_NOT_FOUND，"
                        f"应写 {{{head}.<字段>}}）：{display}",
                        pointer,
                    )
                    continue

                if ref_type == "trigger":
                    root = tail[0]
                    key = tail[1] if len(tail) > 1 else ""
                    if root != "context" or key not in _TRIGGER_CONTEXT_KEYS:
                        add(
                            f"{prefix} 触发器输出路径不存在（REF_PATH_NOT_FOUND，"
                            f"context 下仅 {'/'.join(_TRIGGER_CONTEXT_KEYS)}）：{display}",
                            pointer,
                        )
                    continue

                if ref_type == "tool_call":
                    root = tail[0]
                    if root != "result":
                        add(
                            f"{prefix} 工具节点仅暴露 result 输出（REF_PATH_NOT_FOUND）：{display}",
                            pointer,
                        )
                        continue
                    tool = ""
                    ref_node = next((candidate for candidate in graph.nodes if candidate.id == head), None)
                    if ref_node is not None and isinstance(ref_node.config.get("tool"), str):
                        tool = ref_node.config["tool"]
                    schema = tool_output_schemas.get(tool)
                    if not _schema_has_path(schema, tail[1:]):
                        add(
                            f"{prefix} 工具 {tool or '未选择'} 的输出中不存在该路径"
                            f"（REF_PATH_NOT_FOUND）：{display}",
                            pointer,
                        )
                    continue

                if ref_type == "parallel":
                    root, *rest = tail
                    if root == "result":
                        entries, region = parallel_meta.get(head, (set(), set()))
                        # B1：result 是汇聚产出，分支区域内（汇聚点之前）尚未产出，不可见。
                        if node.id in region:
                            add(
                                f"{prefix} 并行结果在汇聚后才可用（REF_NOT_IN_SCOPE："
                                f"分支区域内尚未汇聚）：{display}",
                                pointer,
                            )
                            continue
                        # result.<入口id>：入口须为 branches 目标；其下深层为分支产出，动态放行。
                        # branches 未配置（entries 空）时降级，配置缺失归 L1/拓扑校验，不双重报错。
                        if entries and rest and rest[0] not in entries:
                            add(
                                f"{prefix} 并行结果入口不存在（REF_PATH_NOT_FOUND，"
                                f"result 下须为 branches 目标节点 id，合法入口：{sorted(entries)}）：{display}",
                                pointer,
                            )
                        continue
                    if root not in _STATIC_OUTPUT_KEYS["parallel"] or rest:
                        add(
                            f"{prefix} 并行节点输出路径不存在（REF_PATH_NOT_FOUND，"
                            f"仅 status/branches/joinStrategy/joinTarget，result 为动态入口）：{display}",
                            pointer,
                        )
                    continue

                if ref_type == "subgraph":
                    root, *rest = tail
                    if root == "outputs":
                        # D30/B2：解析到子图结构时校验 outputs.<内部节点id> 存在性（其后深层为内部
                        # 节点产出形状，跨图不展开、放行）；解析不到（无 resolver/子图缺失/钉版）降级，
                        # 仅放行 outputs 根，与 outputSchema 缺省同构。
                        inner = (subgraph_index or {}).get(head)
                        if inner is not None and rest and rest[0] not in inner:
                            add(
                                f"{prefix} 子图输出中不存在该内部节点（REF_PATH_NOT_FOUND，"
                                f"outputs 下须为子图内节点 id，合法：{sorted(inner)}）：{display}",
                                pointer,
                            )
                        continue
                    if root not in _STATIC_OUTPUT_KEYS["subgraph"] or rest:
                        add(
                            f"{prefix} 子图节点输出路径不存在（REF_PATH_NOT_FOUND，仅 status/outputs 根）："
                            f"{display}",
                            pointer,
                        )
                    continue

                if ref_type == "ai_decision":
                    # decision 为 loader 固定产出结构 {action, reason, confidence, source?}，
                    # 放行其一层白名单子键（M8 审批卡需引用 decision.reason）；prompt_rendered 为标量。
                    root, *rest = tail
                    if root == "decision":
                        # decision 根对象本身合法，或其一层固定子键 action/reason/confidence/source
                        path_ok = len(rest) == 0 or (
                            len(rest) == 1
                            and rest[0] in ("action", "reason", "confidence", "source")
                        )
                    else:
                        path_ok = root == "prompt_rendered" and not rest
                    if not path_ok:
                        add(
                            f"{prefix} 节点 {head} 的输出中不存在该路径（REF_PATH_NOT_FOUND）：{display}",
                            pointer,
                        )
                    continue

                static_keys = _STATIC_OUTPUT_KEYS.get(ref_type)
                if static_keys is not None:
                    root, *rest = tail
                    if root not in static_keys or rest:
                        add(
                            f"{prefix} 节点 {head} 的输出中不存在该路径（REF_PATH_NOT_FOUND）：{display}",
                            pointer,
                        )

    return issues, edges


def _validate_data_dependency_cycles(
    edges: list[tuple[str, str, str]],
) -> list[Issue]:
    """数据依赖环（GRAPH_DATA_CYCLE；19 §1.4.3/§1.5.2）：与控制流拓扑环分开建图。

    边为「通过可见性判定的模板引用」viewer→provider（节点不存在/不可见/路径非法
    的引用不纳边），DFS 三色报首个环，定位挂在回边引用方的模板字段。拓扑无环图上
    可见边必为 DAG（引用只能朝上游），故本规则新增诊断集中在 loop 白名单回边区：
    loop 的 continueExpression 引用体内节点、体内节点又引用 loop.index 时成环
    （首轮条件求值时体内尚无输出），拓扑环检测豁免回边会漏，本规则补判。
    """
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for viewer, provider, pointer in edges:
        adjacency.setdefault(viewer, []).append((provider, pointer))

    white, gray, black = 0, 1, 2
    color: dict[str, int] = {}
    stack: list[str] = []
    found: tuple[list[str], str, str] | None = None

    def dfs(node: str) -> bool:
        nonlocal found
        color[node] = gray
        stack.append(node)
        for provider, pointer in adjacency.get(node, []):
            state = color.get(provider, white)
            if state == gray:
                start = stack.index(provider)
                found = (stack[start:] + [provider], node, pointer)
                return True
            if state == white and dfs(provider):
                return True
        stack.pop()
        color[node] = black
        return False

    for node in adjacency:
        if color.get(node, white) == white and dfs(node):
            break

    if found is None:
        return []
    cycle, back_node, back_pointer = found
    chain = " → ".join(cycle)
    return [(
        "检测到变量数据依赖环（GRAPH_DATA_CYCLE：节点配置中的模板引用相互依赖；"
        "常见于循环节点的 continueExpression 引用了循环体内节点、而体内节点又引用"
        "循环变量，首轮条件求值时体内尚无输出）：" + chain,
        _loc(back_node, back_pointer),
    )]


_TEMPLATE_FIELDS_TYPES = {
    "ai_decision",
    "tool_call",
    "condition",
    "loop",
    "human_approval",
    "subgraph",
}
