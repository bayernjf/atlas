"""A2A 路由（docs/90）：卡片发现（公开）+ 任务端点（Bearer 保护）。

A-02 起鉴权由 `atlas.a2a.auth` 统一：多 token 标签绑定调用方与租户，旧单 token
仅保留 plan-only。任务端点在进程内执行真实 run，故把同步执行体放到线程池，
避免阻塞事件循环。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from functools import partial
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from atlas.a2a.auth import CallerIdentity, authenticate
from atlas.a2a.card import build_agent_card
from atlas.a2a.rpc import handle_jsonrpc

router = APIRouter(tags=["a2a-execution-agent"])


def _card_response() -> JSONResponse:
    return JSONResponse(build_agent_card(), headers={"Cache-Control": "public, max-age=300"})


@router.get("/api/a2a/agent-card")
async def agent_card() -> JSONResponse:
    return _card_response()


@router.get("/.well-known/agent-card.json")
async def agent_card_well_known() -> JSONResponse:
    return _card_response()


@router.get("/.well-known/agent.json")
async def agent_card_well_known_legacy() -> JSONResponse:
    return _card_response()


@router.post("/api/a2a/tasks")
async def tasks(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Any:
    caller = authenticate(authorization)
    if caller is None:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "unauthorized"}},
            status_code=401,
        )
    payload = await request.json()
    method = payload.get("method")
    if method == "tasks/sendSubscribe":
        return StreamingResponse(
            _subscribe_stream(payload, caller),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    response = await run_in_threadpool(partial(handle_jsonrpc, payload, caller=caller))
    return JSONResponse(response)


async def _subscribe_stream(payload: dict, caller: CallerIdentity) -> AsyncIterator[str]:
    events: list[dict] = []
    response = await run_in_threadpool(
        partial(handle_jsonrpc, payload, on_event=events.append, caller=caller)
    )
    for event in events:
        yield f"data: {json.dumps({'jsonrpc': '2.0', 'id': payload.get('id'), 'result': event})}\n\n"
    yield f"data: {json.dumps(response)}\n\n"
