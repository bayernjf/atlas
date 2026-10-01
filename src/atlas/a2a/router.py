"""A2A 路由（docs/90）：卡片发现（公开）+ 任务端点（Bearer 保护）。

第一阶段任务层纯内存、无 DB，故任务端点不落业务库、不接审计中间件；
鉴权用独立 env token（ATLAS_A2A_TASK_TOKEN），与平台会话/渠道凭证分离。
dev 档未配置 token 时放行并记录 WARNING（仅限本地联调）；prod 档 fail-closed。
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from atlas.a2a.card import build_agent_card
from atlas.a2a.rpc import handle_jsonrpc
from atlas.security.bootstrap import read_env_profile

logger = logging.getLogger("atlas.a2a")

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


def _task_auth_ok(authorization: str | None) -> bool:
    token = os.environ.get("ATLAS_A2A_TASK_TOKEN", "").strip()
    if token:
        expected = f"Bearer {token}"
        return bool(authorization) and hmac.compare_digest(authorization, expected)
    # No token configured: prod stays closed; dev/test allows local integration.
    if read_env_profile() == "prod":
        logger.warning("ATLAS_A2A_TASK_TOKEN 未配置：/api/a2a/tasks 已 fail-closed 关闭（prod）")
        return False
    logger.warning("ATLAS_A2A_TASK_TOKEN 未配置：/api/a2a/tasks 在非 prod 档无鉴权放行（仅限本地联调）")
    return True


@router.post("/api/a2a/tasks")
async def tasks(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Any:
    if not _task_auth_ok(authorization):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "unauthorized"}},
            status_code=401,
        )
    payload = await request.json()
    method = payload.get("method")
    if method == "tasks/sendSubscribe":
        return StreamingResponse(
            _subscribe_stream(payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    return JSONResponse(handle_jsonrpc(payload))


async def _subscribe_stream(payload: dict) -> AsyncIterator[str]:
    events: list[dict] = []
    response = handle_jsonrpc(payload, on_event=events.append)
    for event in events:
        yield f"data: {json.dumps({'jsonrpc': '2.0', 'id': payload.get('id'), 'result': event})}\n\n"
    yield f"data: {json.dumps(response)}\n\n"
