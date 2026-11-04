"""P2-1 split: Model/builtin/BYOK configuration and usage routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from atlas.services.model_config_service import ModelConfigRequest, build_model_config as _build_model_config, masked_view as _masked_view
from fastapi import Depends, HTTPException
from typing import Any
from atlas.api.main import (
    _USAGE_GROUP_BY,
    _effective_model_summary,
    _hub_builtin_models,
    _hub_status,
)


router: APIRouter = APIRouter()


@router.get("/api/models")
def get_models(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    """模型配置总览（read 门可读；api_key 全脱敏）。

    hub 配置时内置区读 hub 投影（只读）；否则保留单条内置（迁移期）。
    """
    from atlas.llm import hub as _hub

    services = _main.services_for(principal)
    store = services.model_config
    byok = store.get_byok(principal.tenant_id)
    hub_status = _hub_status()
    via_hub = _hub.is_hub_configured()
    return {
        "hub": hub_status,
        "builtin": None if via_hub else (
            _masked_view(store.get_builtin()) if store.get_builtin() else None
        ),
        "builtinModels": _hub_builtin_models() if via_hub else [],
        "byok": _masked_view(byok) if byok else None,
        "effective": _effective_model_summary(services, principal.tenant_id),
    }


@router.put("/api/models/builtin")
def put_builtin_model(
    body: ModelConfigRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """W5-4：内置模型改由 hub 投影同步，手工写入停用（410 Gone）。"""
    raise HTTPException(
        status_code=410,
        detail="内置模型已改由 model-hub 投影同步，不再支持手工写入；请在 hub 管理。",
    )


@router.get("/api/models/byok")
def get_byok_model(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    """本租户 BYOK 配置（脱敏）。"""
    services = _main.services_for(principal)
    cfg = services.model_config.get_byok(principal.tenant_id)
    return _masked_view(cfg) if cfg else {"configured": False}


@router.put("/api/models/byok")
def put_byok_model(
    body: ModelConfigRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """租户管理员/operator 维护本租户 BYOK（docs/93 §1 语义约束 2：operate 可配自己的 key）。"""
    services = _main.services_for(principal)
    store = services.model_config
    existing = store.get_byok(principal.tenant_id)
    cfg = _build_model_config("byok", body, existing, updated_by=principal.username)
    return _masked_view(store.set_byok(principal.tenant_id, cfg))


@router.get("/api/usage/summary")
def usage_summary(
    principal: Principal = Depends(require("read")),
    since: str | None = None,
    until: str | None = None,
    group_by: str = "caller",
) -> dict[str, Any]:
    """用量聚合（read 档，只读本租户）：按 caller/model/day 分组的 token 与调用次数。

    **只报 token，不做货币计价**（AU-D1：单价属外部资源，伪造比没有更坏）。
    group_by 非法值 ⇒ 422（照 `_audit_cursor` 先例：查询参数校验不新增错误码，
    本批承诺零新错误码）。
    """
    if group_by not in _USAGE_GROUP_BY:
        raise HTTPException(
            status_code=422,
            detail=[f"group_by 必须是 {'/'.join(_USAGE_GROUP_BY)} 之一，收到：{group_by}"],
        )
    store = _main.services_for(principal).usage_store
    return {
        "groupBy": group_by,
        "since": since,
        "until": until,
        "items": store.summarize(since=since, until=until, group_by=group_by),
    }


@router.get("/api/usage/recent")
def usage_recent(
    principal: Principal = Depends(require("read")),
    limit: int = 50,
) -> dict[str, Any]:
    """最近用量明细（read 档，倒序，limit clamp 1–200）：一次调用一行。"""
    store = _main.services_for(principal).usage_store
    return {"items": store.list_recent(limit=limit)}
