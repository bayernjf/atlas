"""发布前批量回放门禁（M9；D26 部分取回）。

契约：04 §5.11 末（发布前批量门禁段）、03 `release_gate`、12 §3.7、docs/20 §4.4。

对当前 latest 草稿（publish 冻结物）逐例重跑 ``graph_id`` 匹配的录制用例，
复用 ``recording.replay`` 的 preset_approvals/collect_steps/compare，
**无录制专用运行时**——每例都是一次标准 ``run_graph``。

分层（06 §6.11）：执行期 ``AdapterRegistry`` 与 subgraph resolver 在 API 层
装配（含租户分区的 message 适配器），由调用方注入；recording 不反向依赖
api/iam。``services`` 仅用于取审批 broker（预置决策秒回，同现有 replay 端点）。
"""

from typing import Any

from ..graph.dsl import parse_graph
from ..graph.loader import run_graph
from .cases import RecordingCase
from .replay import (
    clock_anchor,
    collect_steps,
    compare,
    preset_approvals,
    preset_wait_events,
    seed_anchor,
)


def _replay_one(
    *,
    graph,
    case: RecordingCase,
    services,
    registry,
    graph_resolver,
) -> dict[str, Any]:
    """对草稿重跑单用例，返回 compare 报告；执行异常折叠为 failed/matches=false。"""
    try:
        emit, take_steps = collect_steps()
        anchor, clock_note = clock_anchor(case)
        seed, rng_note = seed_anchor(case)
        inputs = dict(case.inputs or {})
        presets = preset_approvals(case.steps, subgraphs=case.subgraphs)
        if presets:
            approvals = dict(inputs.get("approvals") or {})
            approvals.update(presets)
            inputs["approvals"] = approvals
        # 打包 ZJ（D47）：event wait 预置跨边界（子图内路径限定键），门禁回放不真挂起。
        wait_presets = preset_wait_events(case.steps, subgraphs=case.subgraphs)
        if wait_presets:
            wait_events = dict(inputs.get("waitEvents") or {})
            wait_events.update(wait_presets)
            inputs["waitEvents"] = wait_events
        result = run_graph(
            graph,
            inputs=inputs,
            registry=registry,
            approval_broker=services.approval_broker,
            graph_id=f"gate-{case.id}",
            emit=emit,
            graph_resolver=graph_resolver,
            now_override=anchor,
            rng_seed=seed,
        )
        replay_steps = take_steps()
        tools_by_node = {
            node.id: (node.config.get("tool") if node.type == "tool_call" else None)
            for node in graph.nodes
        }
        report = compare(
            case.steps,
            replay_steps,
            tools_by_node=tools_by_node,
            baseline_status=case.status,
            replay_status=result["status"],
            subgraphs=case.subgraphs,
        )
        if clock_note:
            report["clock_note"] = clock_note
        if rng_note:
            report["rng_seed_note"] = rng_note
        return report
    except Exception as exc:  # 回放失败折叠为不匹配，不抛 500（对齐 replay 端点，06 §6.9）
        return {
            "matches": False,
            "baseline_status": case.status,
            "replay_status": "failed",
            "steps": [
                {
                    "node_id": step.node_id,
                    "match": False,
                    "note": f"门禁回放执行异常：{type(exc).__name__}: {exc}",
                }
                for step in case.steps
            ],
        }


def _case_note(report: dict[str, Any]) -> str:
    """从 compare 逐节点报告提炼单行门禁结论。"""
    if report["matches"]:
        return "全部节点一致"
    failed_notes = [row["note"] for row in report["steps"] if not row["match"]]
    if not failed_notes:
        return f"终态不一致（baseline={report.get('baseline_status')}，replay={report.get('replay_status')}）"
    head = failed_notes[0]
    more = len(failed_notes) - 1
    return head + (f"；另有 {more} 处不一致" if more else "")


def run_release_gate(
    *,
    graph_id: str,
    draft: dict[str, Any],
    cases: list[RecordingCase],
    services,
    registry,
    graph_resolver,
) -> dict[str, Any]:
    """对 latest 草稿批量回放关联用例，产 GateReport（03 `release_gate`）。

    筛选 ``case.graph_id == graph_id`` 且非空（旧用例 graph_id="" 不入选）。
    total=0 → skipped=true/blocked=false（明示未覆盖，不假装通过、不阻塞发布）；
    total>0 且任一不匹配 → blocked=true。
    """
    selected = [case for case in cases if case.graph_id and case.graph_id == graph_id]
    if not selected:
        return {
            "graph_id": graph_id,
            "target": "draft",
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": True,
            "blocked": False,
            "cases": [],
        }

    graph = parse_graph(draft)
    rows: list[dict[str, Any]] = []
    passed = 0
    for case in selected:
        report = _replay_one(
            graph=graph,
            case=case,
            services=services,
            registry=registry,
            graph_resolver=graph_resolver,
        )
        matches = bool(report["matches"])
        passed += int(matches)
        rows.append(
            {
                "case_id": case.id,
                "name": case.name,
                "matches": matches,
                "replay_status": report["replay_status"],
                "note": _case_note(report),
                **(
                    {"clock_note": report["clock_note"]}
                    if report.get("clock_note")
                    else {}
                ),
                **(
                    {"rng_seed_note": report["rng_seed_note"]}
                    if report.get("rng_seed_note")
                    else {}
                ),
            }
        )

    total = len(selected)
    failed = total - passed
    return {
        "graph_id": graph_id,
        "target": "draft",
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": False,
        "blocked": failed > 0,
        "cases": rows,
    }
