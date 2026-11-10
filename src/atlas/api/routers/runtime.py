"""P2-1 split: Compile/run endpoints, runs, tasks, waits, approvals runtime, debug.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
import asyncio
from atlas.collaboration.event_waits import WaitAlreadySignaled, WaitTokenNotFound
from atlas.cycles.state import cycle_registry
from atlas.graph.loader import compile_graph
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from atlas.routing import evaluate_after_run
from atlas.services import run_stream
from atlas.storage.recovery import RESOLVE_FRAME_NOT_CLAIMED, RESOLVE_FRAME_NOT_FOUND, RESOLVE_OK, interruption_view
from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Any
from atlas.api.main import (
    CompileResponse,
    InterruptionResolveRequest,
    ResumeDebugRequest,
    RunGraphResponse,
    _PUBLIC_URL,
    _SYNC_RUN_MAX_CONCURRENT,
    _SYNC_RUN_POOL,
    _cycle_session_or_404,
    _graph_version,
    _load_graph_or_404,
    _parse_wait_event_key,
    _parse_wait_payload,
    _record_audit,
    _resolve_event_version,
    _run_saved_graph_impl,
    _runtime_registry,
    _secret_provider,
    _sync_run_slots,
    _tenant_graph_resolver,
    _tool_call_from_event,
    _validate_debug,
)


router: APIRouter = APIRouter()


@router.get("/api/runs/{run_id}/cycle")
def get_run_cycle(
    run_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """圈会话投影（read 档，只读）：圈序号/累计 token/累计墙钟/终止原因/各圈 run。

    非圈运行（`cycleGroupId` 为空）或会话已被 ring 淘汰 ⇒ 404 `CYCLE_NOT_FOUND`。
    **零新错误码**：404 走既有纯文本 detail 惯例（同 `/api/runs/{id}/trace` 的 404 族）。
    """
    session = _cycle_session_or_404(run_id, principal.tenant_id)
    return session.public_view()


@router.post("/api/runs/{run_id}/cycle/stop")
def stop_run_cycle(
    run_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """停**整条**圈会话（operate；C-D8）：已终态 ⇒ 200 幂等（状态照实回，不覆写历史）。

    与 `POST /api/runs/{id}/cancel` 的分工：cancel 停**这一圈**，本端点停**整条会话**
    （此后不再开新圈；已在跑的那一圈由 cancel 收尾）。
    """
    session = _cycle_session_or_404(run_id, principal.tenant_id)
    stopped = cycle_registry().stop(session.group_id) or session
    return stopped.public_view()


@router.post("/api/graphs/{graph_id}/compile", response_model=CompileResponse)
def compile_saved_graph(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> CompileResponse:
    services = _main.services_for(principal)
    graph = _load_graph_or_404(services, graph_id, (payload or {}).get("releaseVersion"))
    # D78：compile 端点与 run 端点同构注入 registry——prod 且演示面关闭时
    # `_resolve_registry(None)` 会 raise（loader.py:409-412），缺注入即 500。
    compile_graph(
        graph,
        graph_id=graph_id,
        graph_resolver=_tenant_graph_resolver(services),
        registry=_runtime_registry(services),
    )

    incoming = {edge.target for edge in graph.edges}
    outgoing = {edge.source for edge in graph.edges}
    return CompileResponse(
        id=graph_id,
        nodes=[{"id": node.id, "type": node.type, "name": node.name} for node in graph.nodes],
        edges=[{"id": edge.id, "source": edge.source, "target": edge.target} for edge in graph.edges],
        entrypoints=[node.id for node in graph.nodes if node.id not in incoming],
        terminals=[node.id for node in graph.nodes if node.id not in outgoing],
    )


@router.post("/api/graphs/{graph_id}/run", response_model=RunGraphResponse)
async def run_saved_graph(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> RunGraphResponse:
    """同步跑一次图（O-5 彻底解耦：async 端点 + 专用 run 线程池，挂起不占 uvicorn
    线程池；信号量保留 429 语义；逻辑在 `_run_saved_graph_impl`）。"""
    if not _sync_run_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=f"同步运行并发已达上限 {_SYNC_RUN_MAX_CONCURRENT}：请稍后重试或改用 /run/stream（SSE）",
        )
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _SYNC_RUN_POOL,
            _run_saved_graph_impl,
            graph_id,
            payload,
            principal,
        )
    finally:
        _sync_run_slots.release()


@router.post("/api/graphs/{graph_id}/run/stream")
async def run_saved_graph_stream(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> StreamingResponse:
    """SSE：node_start/node_end/run_end 实时推送到画布（08 §7.3 验收 5）。

    run_graph 在后台 daemon 线程执行、帧经有界 SSEBridge 实时下发（真流式；端点
    async 化，等待不占工作线程，#2/#4）；human_approval 节点依赖 node_start 在阻塞前
    到达，前端凭 token 调决策端点放行。租户服务 bundle 在请求线程解析后显式透传
    worker（06 §6.12，无线程上下文变量）。worker 暴毙或收尾二次异常时由生成器合成
    安全 error 终帧，连接不悬挂（#1，docs/125 §3.1）；断连只关桥接、不取消 run_graph。
    """
    # 语义在 atlas.services.run_stream（service layer batch 2）：本路由只做 HTTP
    # 解析、debug 422 门与依赖束装配；预装配与帧生成器在 service 层。docstring
    # 逐字保持——FastAPI 会把它编进 OpenAPI，快照哈希随之变化。
    services = _main.services_for(principal)
    body = payload or {}
    # M9：event 在请求线程解析（Principal 不进 worker），钉住的版本随闭包透传后台线程。
    release_version, resolved_version = _resolve_event_version(services, principal, graph_id, body)
    graph = _load_graph_or_404(services, graph_id, release_version)
    event_payload = (body.get("event") or {}).get("payload") or {}
    inputs = body.get("inputs")
    debug = body.get("debug")
    debug_session = None
    if debug is not None:
        breakpoints = _validate_debug(graph, debug)
        debug_session = services.debug_broker.create(graph_id=graph_id, breakpoints=breakpoints)
        if any(
            node.type == "wait" and node.config.get("waitType") == "event"
            for node in graph.nodes
        ):
            raise HTTPException(status_code=422, detail="事件等待不支持单步调试")

    ctx = run_stream.prepare_stream_run(
        principal,
        services,
        graph_id=graph_id,
        graph=graph,
        event_payload=event_payload,
        inputs=inputs,
        resolved_version=resolved_version,
        debug_session=debug_session,
        deps=run_stream.StreamRunDeps(
            run_graph=_main.run_graph,
            frame_sink_for=_main._frame_sink_for,
            resume_claim_for=_main._resume_claim_for,
            evaluate_after_run=evaluate_after_run,
            runtime_registry_of=_runtime_registry,
            tenant_graph_resolver_of=_tenant_graph_resolver,
            tool_call_from_event=_tool_call_from_event,
            graph_version=_graph_version,
            secret_provider=_secret_provider,
            storage_backend=_main.STORAGE_BACKEND,
            public_url=_PUBLIC_URL,
            heartbeat_seconds=_main.SSE_HEARTBEAT_SECONDS,
        ),
    )
    return StreamingResponse(
        run_stream.stream_saved_graph_events(ctx), media_type="text/event-stream"
    )


@router.get("/api/runs")
def list_runs(
    status: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """本租户运行列表（新→旧；status 缺省=全部）（M5b，docs/24 §4）。"""
    if status is not None and status not in {
        "running", "suspended", "completed", "failed", "interrupted", "cancelled",
    }:
        raise HTTPException(status_code=422, detail="非法的 status 过滤值")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 200 之间")
    return {"items": _main.services_for(principal).run_store.list(status=status, limit=limit)}


@router.get("/api/runs/{run_id}")
def get_run(
    run_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """单运行详情：状态/产出/轨迹/挂起信息；跨租户或不存在 → 404（docs/24 §4）。"""
    run = _main.services_for(principal).run_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    return run


@router.post("/api/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """协作式急停（docs/27 §4.1）：置位本租户该 run 的取消事件，下一节点边界生效，
    不在 wait/approval/tool 阻塞中点强杀。运行中（含调试流）200，重复取消幂等 200；
    已结束且无注册句柄 409；run 不存在/跨租户 404。"""
    services = _main.services_for(principal)
    if services.cancellation_broker.cancel(run_id):
        return {"run_id": run_id, "cancelled": True}
    if services.run_store.get(run_id) is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    raise HTTPException(status_code=409, detail="运行已结束，无法取消")


@router.get("/api/tasks")
def list_tasks(
    state: str | None = None,
    assignee: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """本租户任务信封列表（新→旧；state/assignee 可过滤）（M7，docs/20 §4.2 / 12 任务端点）。"""
    if state is not None and state not in {
        "pending", "accepted", "running", "done", "failed", "timeout",
    }:
        raise HTTPException(status_code=422, detail="非法的 state 过滤值")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 200 之间")
    tasks = _main.services_for(principal).task_store.list(state=state)
    if assignee:
        tasks = [task for task in tasks if task.assignee == assignee]
    return {"items": [task.model_dump() for task in tasks[:limit]]}


@router.get("/api/tasks/{task_id}")
def get_task(
    task_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """单任务信封详情；跨租户或不存在 → 404（M7）。"""
    task = _main.services_for(principal).task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task.model_dump()


@router.get("/api/waits")
def list_waits(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出当前租户 pending 的事件等待（进程内 broker，重启即失，docs/47 §4）。"""
    return {"items": _main.services_for(principal).event_wait_broker.list_pending()}


@router.get("/api/interruptions")
def list_interruptions(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """本租户挂起帧的只读投影（docs/76 打包 Q，解 14 D42）。

    存在的理由：`resumed_at` 此前只在启动恢复路径被读一次，崩在续跑途中的 run 除了重启
    翻日志无处可查。**本端点不重放、不清帧、不改任何状态**——`claimed_suspended` 只是把
    D36 那颗雷显形，怎么处理仍未决定。

    投影本体在 `atlas.storage.recovery.interruption_view`（MCP 面 `atlas_list_interruptions`
    锚同一份，只差 resumeToken 收窄）。
    """
    run_store = _main.services_for(principal).run_store
    return interruption_view(
        backend=_main.STORAGE_BACKEND,
        tenant_id=principal.tenant_id,
        engine=_main.get_pg_backend().engine if _main.STORAGE_BACKEND == "pg" else None,
        run_status_of=lambda run_id: (run_store.get(run_id) or {}).get("status"),
        include_resume_token=True,
    )


@router.post("/api/interruptions/{resume_token}/resolve")
def resolve_interruption(
    resume_token: str,
    body: InterruptionResolveRequest,
    http_request: Request,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """人工认领后崩溃帧的收敛（docs/96 打包 BL，解 docs/14 D36 人工收敛半边）。

    唯一动作 abandon：把「帧已认领、run 却永久停在 suspended」的卡死运行在一个
    PG 事务内置 `interrupted` 终态并删帧。**不重放**——下游 create_refund 等非
    幂等，安全重放要幂等键＋逐节点检查点（docs/14 D56）。承重的是事务谓词，
    下面的折算只读状态给错误码，不承担互斥。
    """
    if body.action != "abandon":
        _record_audit(_main.services_for(principal), principal, http_request,
                      "interruption.resolve", 422)
        raise HTTPException(
            status_code=422,
            detail={"code": "INTERRUPTION_ACTION_UNSUPPORTED",
                    "message": f"不支持的收敛动作：{body.action}（v1 仅支持 abandon）",
                    "params": {"action": body.action}},
        )
    if _main.STORAGE_BACKEND != "pg":
        _record_audit(_main.services_for(principal), principal, http_request,
                      "interruption.resolve", 409)
        raise HTTPException(
            status_code=409,
            detail={"code": "INTERRUPTIONS_NOT_PERSISTED",
                    "message": "内存档挂起帧不跨进程持久化，无人工收敛能力"},
        )
    code, info = _main.abandon_claimed_suspended_frame(
        _main.get_pg_backend().engine,
        tenant_id=principal.tenant_id,
        token=resume_token,
    )
    status_code = 200
    if code == RESOLVE_OK:
        payload = {"resumeToken": resume_token, "runId": info.get("run_id"),
                   "resolved": "interrupted", "frameDeleted": True}
    elif code == RESOLVE_FRAME_NOT_FOUND:
        status_code = 404
        payload = {"code": "INTERRUPTION_FRAME_NOT_FOUND",
                   "message": f"挂起帧不存在或不属于当前租户：{resume_token}"}
    elif code == RESOLVE_FRAME_NOT_CLAIMED:
        status_code = 409
        payload = {"code": "INTERRUPTION_FRAME_NOT_CLAIMED",
                   "message": "该帧尚未被认领，重启恢复仍可能驱动它，不能人工了结"}
    else:
        status_code = 409
        payload = {"code": "INTERRUPTION_RUN_NOT_SUSPENDED",
                   "message": "该帧已认领但其运行不处于 suspended 状态",
                   "params": {"status": info.get("status")}}
    _record_audit(_main.services_for(principal), principal, http_request,
                  "interruption.resolve", status_code)
    if code != RESOLVE_OK:
        raise HTTPException(status_code=status_code, detail=payload)
    return payload


@router.post("/api/waits/events")
def signal_wait_event(
    http_request: Request,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """按 eventKey 广播信号，释放同 key 全部等待；无 pending 则入排队 ring（docs/55）。

    返回 released=本次释放等待数、queued=是否因无消费者进入 per-key 排队。
    """
    data = body or {}
    event_key = _parse_wait_event_key(data)
    payload = _parse_wait_payload(data)
    services = _main.services_for(principal)
    result = services.event_wait_broker.signal_key(event_key, payload)
    _record_audit(services, principal, http_request, "wait.signal_event", 200)
    return {"released": result["released"], "queued": result["queued"]}


@router.post("/api/waits/{token}/signal")
def signal_wait_token(
    token: str,
    http_request: Request,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """按 token 直投信号：未知/已取走 404，重复信号 409。"""
    data = body or {}
    payload = _parse_wait_payload(data)
    services = _main.services_for(principal)
    try:
        services.event_wait_broker.signal_token(token, payload)
    except WaitTokenNotFound:
        _record_audit(services, principal, http_request, "wait.signal_token", 404)
        raise HTTPException(
            status_code=404,
            detail={"code": "WAIT_TOKEN_NOT_FOUND",
                    "message": f"等待不存在或已清理：{token}"},
        )
    except WaitAlreadySignaled:
        _record_audit(services, principal, http_request, "wait.signal_token", 409)
        raise HTTPException(
            status_code=409,
            detail={"code": "WAIT_ALREADY_SIGNALED",
                    "message": "该等待已有信号，重复提交不生效"},
        )
    _record_audit(services, principal, http_request, "wait.signal_token", 200)
    return {"token": token, "released": True}


@router.get("/api/debug")
def list_debug_pauses(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出当前租户活动调试暂停（04 §5.12；进程内，重启即失）。"""
    return {"items": _main.services_for(principal).debug_broker.list_pending()}


@router.post("/api/debug/{token}/resume")
def resume_debug(
    token: str,
    request: ResumeDebugRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, str]:
    broker = _main.services_for(principal).debug_broker
    session = broker.get_session(token)
    if session is None:
        raise HTTPException(status_code=404, detail=f"调试暂停不存在或已恢复：{token}")
    # 变量覆盖在 resolve（首决）前校验暂存；action=stop 忽略覆盖（§4.3）。
    if request.globals is not None and request.action != "stop":
        try:
            session.apply_overrides(token, request.globals)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not session.resolve(token, request.action):
        raise HTTPException(status_code=409, detail="该调试暂停已恢复，重复提交不生效")
    return {"token": token, "action": request.action}
