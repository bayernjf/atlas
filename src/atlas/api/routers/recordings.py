"""P2-1 split: Recording capture, promote, guide and replay routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
from atlas.graph.dsl import parse_graph
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from atlas.recording import PromoteRequest, PromoteRequestError, RecordingCreateRequest, RecordingUpdateRequest, ReplayRequest, apply_promotion, build_condition_script, build_tool_mocks, clock_anchor, collect_steps, collect_subgraph_snapshots, compare as compare_recording, inline_first_resolver, plan_promotion, preset_approvals, preset_wait_events, render_guide, seed_anchor
from fastapi import Depends, HTTPException
from typing import Any
from atlas.api.main import (
    _promote_compile,
    _runtime_registry,
    _secret_provider,
    _tenant_graph_resolver,
    logger,
)


router: APIRouter = APIRouter()


@router.post("/api/recordings", status_code=201)
def create_recording(
    request: RecordingCreateRequest, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """录制用例入库：按 graph_id 取已保存图原始 JSON 作快照，不重新执行（04 §5.11）。"""
    services = _main.services_for(principal)
    raw = services.graph_store.get(request.graph_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{request.graph_id}")

    def _fetch_subgraph_raw(ref: str):
        # 与 _tenant_graph_resolver 同口径：graphId 可为 `graph-7@3` 钉版。
        ref_id, _, version = ref.partition("@")
        release_version = int(version) if version else None
        return services.graph_store.get(ref_id, release_version)

    subgraphs = collect_subgraph_snapshots(raw, fetch_raw=_fetch_subgraph_raw)
    case = services.recording_store.add(
        name=request.name,
        graph_id=request.graph_id,
        graph=raw,
        inputs=request.inputs,
        steps=request.steps,
        status=request.status,
        subgraphs=subgraphs,
        rng_seed=request.rng_seed,
    )
    return case.model_dump()


@router.get("/api/recordings")
def list_recordings(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """录制用例列表投影（不含 graph/steps）；按租户分区（04 §5.14）。"""
    return {
        "items": [
            {
                "id": case.id,
                "name": case.name,
                "graph_id": case.graph_id,
                "node_count": len(case.graph.get("nodes", [])),
                "step_count": len(case.steps),
                "status": case.status,
                "created_at": case.created_at,
            }
            for case in _main.services_for(principal).recording_store.list()
        ]
    }


@router.post("/api/recordings/{case_id}/promote/plan")
def plan_recording_promotion(
    case_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """试教成图的第一阶段（docs/128 §3.1）：只读地提议替换，一个字节都不写。"""
    services = _main.services_for(principal)
    case = services.recording_store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")
    plan = plan_promotion(case)
    probe = apply_promotion(
        case, accept=[item["id"] for item in plan["substitutions"]]
    )["graph"]
    plan["compile"] = _promote_compile(
        services, probe, graph_id=case.graph_id or "recording-draft"
    )
    return plan


@router.post("/api/recordings/{case_id}/promote")
def promote_recording(
    case_id: str,
    request: PromoteRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """试教成图的第二阶段（docs/128 §3.2）：应用人勾选的替换，返回草稿；**不落库**。"""
    services = _main.services_for(principal)
    case = services.recording_store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")
    try:
        result = apply_promotion(
            case, accept=request.accept, prune_to_path=request.pruneToPath
        )
    except PromoteRequestError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RECORDING_PROMOTE_ACCEPT_UNKNOWN",
                "message": str(exc),
                "params": {"unknown": exc.unknown},
            },
        ) from None
    name = request.name or f"{case.name}（来自用例 {case.id}）"
    compile = _promote_compile(
        services, result["graph"], graph_id=case.graph_id or "recording-draft"
    )
    if not compile["ok"]:
        return {"graph": None, "name": name, "applied": result["applied"], "compile": compile}
    return {"graph": result["graph"], "name": name, "applied": result["applied"], "compile": compile}


@router.get("/api/recordings/{case_id}/guide")
def recording_guide(
    case_id: str, lang: str = "zh", principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """业务操作指引（docs/128 §3.3）：只读产物，不落库、不自动进知识库（AT-D6）。"""
    services = _main.services_for(principal)
    case = services.recording_store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")
    return render_guide(case, lang)


@router.get("/api/recordings/{case_id}")
def get_recording(
    case_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    case = _main.services_for(principal).recording_store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")
    return case.model_dump()


@router.put("/api/recordings/{case_id}")
def update_recording(
    case_id: str,
    request: RecordingUpdateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """编辑用例元信息（docs/28 §2.3）：仅 name/inputs 可改，其余录制事实不可改。

    字段缺省不改；name 空串/超长、inputs 非对象 → 422；用例不存在 → 404。
    """
    services = _main.services_for(principal)
    updated = services.recording_store.update_meta(
        case_id, name=request.name, inputs=request.inputs
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")
    return updated.model_dump()


@router.delete("/api/recordings/{case_id}")
def delete_recording(
    case_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, bool]:
    if not _main.services_for(principal).recording_store.delete(case_id):
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")
    return {"deleted": True}


@router.post("/api/recordings/{case_id}/replay")
def replay_recording(
    case_id: str,
    payload: ReplayRequest | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """回放冻结快照：标准 run_graph + 审批决策预置，比对操作序列与逐节点产出。

    可选 body（docs/28 §2.2/§2.3）：``mock_tools=true`` 以录制工具产出作桩，回放不
    触达适配器（隔离外部系统；发布门禁不接 mock）；``inputs_override`` 顶层键浅合并进
    用例 inputs（一次性入参参数化，不落库）。响应纯超集加 ``mocked_tools``（未启用为 []）。

    回放期异常（如快照内 subgraph 引用的 graphId 已被 reset 删除）折叠为
    replay_status="failed"/matches=false，不抛 500（04 §5.11，06 §6.9）。
    """
    services = _main.services_for(principal)
    case = services.recording_store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")

    tool_mocks: dict[str, Any] | None = None
    mocked_tools: list[str] = []
    condition_script: Any | None = None
    mocked_conditions: list[str] = []
    if payload is not None and payload.mock_tools:
        tool_mocks, mocked_tools = build_tool_mocks(case)
        condition_script, mocked_conditions = build_condition_script(case)

    try:
        graph = parse_graph(case.graph)
        emit, take_steps = collect_steps()
        anchor, clock_note = clock_anchor(case)
        seed, rng_note = seed_anchor(case)
        inputs = dict(case.inputs or {})
        if payload is not None and payload.inputs_override:
            # 顶层键浅合并（dict 值整体替换）；一次性覆写，不修改已入库用例。
            inputs = {**inputs, **payload.inputs_override}
        presets = preset_approvals(case.steps, subgraphs=case.subgraphs)
        if presets:
            approvals = dict(inputs.get("approvals") or {})
            approvals.update(presets)
            inputs["approvals"] = approvals
        # 打包 ZJ（D47）：event wait 预置跨边界——从 baseline 抽 {node_id: payload}
        # （子图内路径限定 "sub-1/wait-1"），回放不真挂起；显式 inputs 优先。
        wait_presets = preset_wait_events(case.steps, subgraphs=case.subgraphs)
        if wait_presets:
            wait_events = dict(inputs.get("waitEvents") or {})
            wait_events.update(wait_presets)
            inputs["waitEvents"] = wait_events
        result = _main.run_graph(
            graph,
            inputs=inputs,
            registry=_runtime_registry(services),
            approval_broker=services.approval_broker,
            event_wait_broker=services.event_wait_broker,
            graph_id=f"replay-{case.id}",
            emit=emit,
            graph_resolver=inline_first_resolver(
                case.subgraphs, _tenant_graph_resolver(services)
            ),
            now_override=anchor,
            rng_seed=seed,
            tool_mocks=tool_mocks,
            condition_classifier=condition_script,
            tenant_id=principal.tenant_id,
            secret_provider=_secret_provider,
            memory_repo=services.memory_store,
        )
        replay_steps = take_steps()
        tools_by_node = {
            node.id: (node.config.get("tool") if node.type == "tool_call" else None)
            for node in graph.nodes
        }
        report = compare_recording(
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
        report["mocked_tools"] = mocked_tools
        report["mocked_conditions"] = mocked_conditions
        return report
    except Exception:  # 回放失败折叠为报告而非 500
        # 异常原文只进服务端日志（Tier0 评审 2026-10-30 H2），报告 note 固定泛化。
        logger.exception("recording replay failed for case %s", case_id)
        return {
            "matches": False,
            "baseline_status": case.status,
            "replay_status": "failed",
            "mocked_tools": mocked_tools,
            "mocked_conditions": mocked_conditions,
            "steps": [
                {
                    "node_id": step.node_id,
                    "match": False,
                    "note": "回放执行异常，未取得该节点产出",
                }
                for step in case.steps
            ],
        }
