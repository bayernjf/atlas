"""中断帧（interruption_frame，docs/24 §2.3）——挂起点的可序列化产物。

进程内后端存内存 dict、PG 后端存 `interruptions` 行 jsonb，两后端共用同一帧结构。
帧由 loader 在挂起前经 `frame_sink` 回调产生，帧存储/恢复由外部注入（API 层）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

FrameKind = Literal["approval", "debug", "wait"]


def build_frame(
    *,
    token: str,
    run_id: str,
    node_id: str,
    kind: FrameKind,
    deadline_at: str | None,
    graph_snapshot: dict[str, Any],
    resume_state: dict[str, Any],
    summary: str = "",
    approver: str = "",
    card_template_id: str = "",
    wait: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造一帧；`resume_state` 含 `inputs` 与截至挂起点的已完成节点 `outputs`。

    `summary`/`approver` 仅 approval 帧有意义（恢复扫描器 restore 重建 pending 用）；
    `card_template_id`（M8）命中交互卡片时记录卡片 id，卡片渲染上下文不单独序列化，
    恢复时由 :func:`card_context_from_frame` 从 graph_snapshot 变量 + resume_state 重建。
    """
    return {
        "resume_token": token,
        "run_id": run_id,
        "node_id": node_id,
        "kind": kind,
        "deadline_at": deadline_at,
        "graph_snapshot": graph_snapshot,
        "resume_state": resume_state,
        "summary": summary,
        "approver": approver,
        "card_template_id": card_template_id,
        "wait": wait,
    }


def card_context_from_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """从挂起帧重建审批卡片渲染上下文（M8，不额外序列化上下文快照）。

    与运行时节点上下文同构（``{global, **节点 outputs}``，见 loader.compile_graph）：
    global 由 graph_snapshot 变量默认值 + resume_state.inputs 同名覆盖复刻
    initial_state 口径（排除预置 approvals）；其余取截至挂起点的节点 outputs
    （含 trigger 节点的 ``context.payload``）。
    """
    snapshot = frame.get("graph_snapshot") or {}
    state = frame.get("resume_state") or {}
    inputs = state.get("inputs") or {}
    outputs = state.get("outputs") or {}

    global_vars: dict[str, Any] = {}
    for variable in snapshot.get("variables", []) or []:
        if isinstance(variable, dict) and "name" in variable:
            global_vars[variable["name"]] = variable.get("value")
    if isinstance(inputs, dict):
        overrides = {key: value for key, value in inputs.items() if key != "approvals"}
        global_vars = {**global_vars, **overrides}
    return {"global": global_vars, **(outputs if isinstance(outputs, dict) else {})}


def deadline_iso(timeout_seconds: float) -> str:
    """绝对 deadline（docs/24 §3.1：超时计时＝绝对时刻，重启消耗照扣）。"""
    return (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat()


def remaining_seconds(deadline_at: str | None) -> float:
    """帧 deadline 距现在的剩余秒数（≤0 表示已到点）；缺省/非法返回 0。"""
    if not deadline_at:
        return 0.0
    try:
        deadline = datetime.fromisoformat(deadline_at)
    except ValueError:
        return 0.0
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return max((deadline - datetime.now(timezone.utc)).total_seconds(), 0.0)
