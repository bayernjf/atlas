"""P2-1 split: Embed token configuration and public embed routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
from atlas.embeds.token import EmbedTokenError, EmbedTokenIssuer
from atlas.iam.deps import require, tenant_registry
from atlas.iam.principals import Principal
from fastapi import Depends, HTTPException, Request
from typing import Any
from atlas.api.main import (
    EmbedConfigRequest,
    EmbedTokenRequest,
    _EMAIL_LINK_INVALID,
    _PUBLIC_URL,
    _record_audit,
)


router: APIRouter = APIRouter()


@router.get("/api/embeds/config")
def list_embed_configs(principal: Principal = Depends(require("administer"))) -> dict[str, Any]:
    """嵌入配置列表（第七类 SchemaRegistry 实体；docs/133 §2.1）。

    **必须先于 ``/api/embeds/{token}`` 注册**（静态段不被路径参数吞掉）。
    """
    services = _main.services_for(principal)
    return {"items": services.embed_store.list()}


@router.put("/api/embeds/config")
def upsert_embed_config(
    http_request: Request,
    request: EmbedConfigRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """开启/调整某图的只读嵌入（enabled 缺省 false＝fail-closed；ttl 1–86400 门控）。"""
    services = _main.services_for(principal)
    config = services.embed_store.upsert(
        request.graph_id,
        enabled=request.enabled,
        token_ttl_seconds=request.token_ttl_seconds,
    )
    _record_audit(services, principal, http_request, "embed.config", 200)
    return config


@router.post("/api/embeds", status_code=201)
def create_embed_token(
    http_request: Request,
    request: EmbedTokenRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """签发嵌入短链（docs/133 BQ-D1：同形 HMAC，不新建鉴权形态）。图未启用嵌入 → 422。"""
    services = _main.services_for(principal)
    config = services.embed_store.get(request.graph_id)
    if config is None or not config["enabled"]:
        raise HTTPException(
            status_code=422,
            detail=f"Graph {request.graph_id} 未启用只读嵌入，请先通过 PUT /api/embeds/config 开启",
        )
    issuer = EmbedTokenIssuer()
    token = issuer.issue(
        principal.tenant_id, request.graph_id, ttl_seconds=config["token_ttl_seconds"]
    )
    _record_audit(services, principal, http_request, "embed.token", 201)
    return {"token": token, "tokenUrl": f"{_PUBLIC_URL}/embed/{token}"}


@router.get("/api/embeds/{token}")
def embed_readonly_view(token: str) -> dict[str, Any]:
    """公开只读嵌入面（docs/133 BQ-D2：无登录态、无副作用）。

    验签失败统一 404（不区分 malformed/bad signature/expired，照邮件深链纪律）。
    投影＝图元信息＋最近运行摘要；**不含** 密钥/决策/草稿键。
    """
    issuer = EmbedTokenIssuer()
    try:
        body = issuer.verify(token)
    except EmbedTokenError as exc:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID) from exc
    tenant_id = str(body.get("tenant", ""))
    graph_id = str(body.get("graph", ""))
    services = tenant_registry.peek(tenant_id)
    if services is None:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID)
    raw = services.graph_store.get(graph_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    runs = [
        r
        for r in services.run_store.list(status=None, limit=20)
        if r.get("graphId") == graph_id
    ]
    last = runs[0] if runs else None
    return {
        "graphId": graph_id,
        "name": raw.get("name", ""),
        "nodeCount": len(raw.get("nodes") or []),
        "variables": [
            {"name": v.get("name"), "value": v.get("value")}
            for v in (raw.get("variables") or [])
        ],
        "lastRun": (
            {
                "runId": last.get("runId"),
                "status": last.get("status"),
                "startedAt": last.get("startedAt"),
            }
            if last is not None
            else None
        ),
    }
