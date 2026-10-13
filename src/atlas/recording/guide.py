"""业务操作指引：把冻结图快照渲染成运营读得懂的步骤树（docs/128 §3.3，打包 AT＝D72）。

数据源只有三份（AT-D6 的"不新增明文面"条款）：图声明、`steps[].output`、入参声明。
其中 output 在 run 的三个出口已被 `_redact_outputs` 换成占位（`loader.py:3924/4230/4305`），
所以本模块**不再自己脱敏，也不许去读 globals 或解析 env/secret**——它没有那个信息，
装作有就是凭空造一份第二真相。

分支从**节点 config** 取，不从边取：`EdgeDSL` 只有 `{id, source, target}`（`dsl.py:140-144`），
条件语义全在 `condition.branches[].expression` 与 `defaultTarget` 上。表达式一律原文照录——
翻译它就是制造一份和流程图不一致的假文档。
"""

from __future__ import annotations

import json
from typing import Any

from atlas.recording.cases import RecordingCase
from atlas.recording.promote import inputs_declaration

# 节点类型 → 步骤种类（五档；界面按 kind 上色，i18n 只管按钮与标题）
_KIND_BY_TYPE: dict[str, str] = {
    "trigger": "entry",
    "ai_decision": "decision",
    "intent_recognition": "decision",
    "info_extraction": "decision",
    "content_generation": "decision",
    "condition": "branch",
    "human_approval": "approval",
    "wait": "wait",
    "tool_call": "action",
    "loop": "control",
    "parallel": "control",
    "subgraph": "control",
    "state_machine": "control",
}
DEFAULT_KIND = "action"
_BRANCH_TYPES = ("condition",)
_MAX_OUTPUT_CHARS = 160

_LABELS: dict[str, dict[str, str]] = {
    "zh": {
        "title": "操作指引",
        "case": "用例",
        "source_recorded": "依据一次真实运行整理",
        "source_graph": "依据流程结构整理（该用例没有运行产出）",
        "inputs_head": "入参",
        "inputs_none": "该流程未声明入参",
        "steps_head": "步骤",
        "linear": "线性流程，无分支",
        "branch_prefix": "若",
        "default_branch": "否则",
        "then": "→ 转到步骤",
        "next": "→ 接续步骤",
        "end": "流程结束",
        "output_prefix": "这一步实际产出",
    },
    "en": {
        "title": "Operation guide",
        "case": "Case",
        "source_recorded": "Derived from a recorded run",
        "source_graph": "Derived from the flow structure (this case has no recorded outputs)",
        "inputs_head": "Inputs",
        "inputs_none": "This flow declares no inputs",
        "steps_head": "Steps",
        "linear": "Linear flow, no branching",
        "branch_prefix": "If",
        "default_branch": "Otherwise",
        "then": "→ go to step",
        "next": "→ continue to step",
        "end": "End of flow",
        "output_prefix": "Recorded output",
    },
}

# 两档键集必须逐字相同：少任一键，指引就会在一种语言下漏整段（U1398 钉这条）。
LABEL_KEYS = sorted(_LABELS["zh"])


def labels_for(lang: str) -> dict[str, str]:
    return _LABELS["en"] if lang == "en" else _LABELS["zh"]


def _index_nodes(graph: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    ordered: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        node_id = str(node["id"])
        ordered.append(node_id)
        by_id[node_id] = node
    return ordered, by_id


def _successors(graph: dict[str, Any], node_id: str) -> list[str]:
    return [
        str(edge.get("target"))
        for edge in (graph.get("edges") or [])
        if isinstance(edge, dict)
        and str(edge.get("source")) == node_id
        and edge.get("target")
    ]


def _branches(node: dict[str, Any]) -> list[dict[str, str]]:
    """分支行：`when` 是表达式原文（没有表达式时退到标签），`next` 是目标节点 id。"""
    config = node.get("config") or {}
    if not isinstance(config, dict):
        return []
    rows: list[dict[str, str]] = []
    for branch in config.get("branches") or []:
        if not isinstance(branch, dict):
            continue
        label = str(branch.get("label") or "")
        expression = str(branch.get("expression") or "")
        rows.append(
            {"when": expression or label, "label": label, "next": str(branch.get("target") or "")}
        )
    default_target = config.get("defaultTarget")
    if default_target:
        rows.append({"when": "", "label": "default", "next": str(default_target)})
    return rows


def _output_summary(outputs_by_node: dict[str, Any], node_id: str) -> str:
    output = outputs_by_node.get(node_id)
    if not output:
        return ""
    try:
        rendered = json.dumps(output, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        rendered = str(output)
    rendered = rendered.replace("\n", " ").strip()
    if len(rendered) > _MAX_OUTPUT_CHARS:
        return rendered[:_MAX_OUTPUT_CHARS] + "…"
    return rendered


def _outline(graph: dict[str, Any], outputs_by_node: dict[str, Any]) -> list[dict[str, Any]]:
    """从 trigger 起沿边广度走；分支节点把每条 when→next 列进 branches。

    访问集防环（loop/回连线是合法画法）。不可达节点不进指引——指引是给人照着做的，
    不是图的清单；不可达由编辑器诊断负责，不在这份产物里重复一份真相。
    """
    ordered_ids, by_id = _index_nodes(graph)
    start = next(
        (node_id for node_id in ordered_ids if by_id[node_id].get("type") == "trigger"),
        ordered_ids[0] if ordered_ids else None,
    )
    if start is None:
        return []

    outline: list[dict[str, Any]] = []
    order_of: dict[str, int] = {}
    queue = [start]
    visited: set[str] = set()
    while queue:
        node_id = queue.pop(0)
        if node_id in visited or node_id not in by_id:
            continue
        visited.add(node_id)
        node = by_id[node_id]
        node_type = str(node.get("type") or "")
        entry: dict[str, Any] = {
            "nodeId": node_id,
            "title": str(node.get("name") or node_id),
            "kind": _KIND_BY_TYPE.get(node_type, DEFAULT_KIND),
            "nodeType": node_type or DEFAULT_KIND,
            "branches": [],
            "next": [],
            "output": _output_summary(outputs_by_node, node_id),
        }
        targets: list[str] = []
        if node_type in _BRANCH_TYPES:
            for branch in _branches(node):
                entry["branches"].append(dict(branch))
                targets.append(branch["next"])
        else:
            targets = _successors(graph, node_id)
        entry["next"] = [target for target in targets if target in by_id]
        for target in entry["next"]:
            if target not in visited:
                queue.append(target)
        order_of[node_id] = len(outline) + 1
        outline.append(entry)

    # 分支目标常排在后面，第二遍才能知道序号。
    for entry in outline:
        for branch in entry["branches"]:
            branch["nextOrder"] = order_of.get(branch["next"])
        entry["nextOrders"] = [order_of.get(target) for target in entry["next"]]
    return outline


def render_guide(case: RecordingCase, lang: str = "zh") -> dict[str, Any]:
    """产出指引：`markdown` 给人读，`outline` 给界面渲染，两者同源一次计算。"""
    labels = labels_for(lang)
    graph = case.graph if isinstance(case.graph, dict) else {}
    outputs_by_node = {step.node_id: step.output for step in case.steps}
    outline = _outline(graph, outputs_by_node)
    inputs = inputs_declaration(case)
    has_outputs = any(entry["output"] for entry in outline)

    lines: list[str] = [
        f"# {labels['title']} — {case.name}",
        "",
        f"{labels['case']}: `{case.id}`",
        "",
        labels["source_recorded"] if has_outputs else labels["source_graph"],
        "",
        f"## {labels['inputs_head']}",
        "",
    ]
    if inputs:
        lines += ["| key | type |", "| --- | --- |"]
        lines += [f"| `{row['key']}` | {row['type']} |" for row in inputs]
    else:
        lines.append(labels["inputs_none"])
    lines += ["", f"## {labels['steps_head']}", ""]

    if not any(entry["branches"] for entry in outline):
        lines += [f"> {labels['linear']}", ""]

    for position, entry in enumerate(outline, start=1):
        lines.append(f"{position}. **{entry['title']}** · `{entry['kind']}`")
        if entry["branches"]:
            for branch in entry["branches"]:
                arrow = (
                    f" {labels['then']} {branch['nextOrder']}" if branch.get("nextOrder") else ""
                )
                if branch["when"] and branch["label"] == "default":
                    lines.append(f"   - {labels['default_branch']}{arrow}")
                elif branch["when"]:
                    lines.append(f"   - {labels['branch_prefix']} `{branch['when']}`{arrow}")
                else:
                    lines.append(f"   - {labels['default_branch']}{arrow}")
        elif entry["next"]:
            arrows = "、".join(
                f"{labels['next']} {order}" for order in entry["nextOrders"] if order
            )
            lines.append(f"   - {arrows}" if arrows else f"   - {labels['end']}")
        else:
            lines.append(f"   - {labels['end']}")
        if entry["output"]:
            lines.append(f"   - {labels['output_prefix']}: `{entry['output']}`")

    return {
        "lang": "en" if lang == "en" else "zh",
        "markdown": "\n".join(lines),
        "outline": outline,
        "inputs": inputs,
        "fromRecordedPath": has_outputs,
        "caseId": case.id,
    }
