"""P2-1 split: Channel agent roster and agent run history routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
import uuid
from atlas.iam.agents_store import Agent
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from fastapi import Depends, HTTPException
from typing import Any


router: APIRouter = APIRouter()


@router.get("/api/agents")
def list_agents(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    items = [a.to_record() for a in _main.services_for(principal).agents_store.list(principal.tenant_id)]
    return {"items": items}


@router.post("/api/agents")
def create_agent(
    body: dict[str, Any], principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=422, detail="agent name 必填")
    agent_id = body.get("id") or uuid.uuid4().hex
    agent = Agent(
        id=agent_id, tenant_id=principal.tenant_id, name=name.strip(),
        enabled=bool(body.get("enabled", True)),
        skills=body.get("skills") or [], adapter_ids=body.get("adapter_ids") or [],
        coordinator=body.get("coordinator"),
    )
    _main.services_for(principal).agents_store.put(agent)
    return agent.to_record()


@router.put("/api/agents/{agent_id}")
def update_agent(
    agent_id: str, body: dict[str, Any],
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    services = _main.services_for(principal)
    agent = services.agents_store.get(principal.tenant_id, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent 不存在")
    if "name" in body and isinstance(body["name"], str):
        agent.name = body["name"].strip()
    if "enabled" in body:
        agent.enabled = bool(body["enabled"])
    if "skills" in body:
        agent.skills = body.get("skills") or []
    if "adapter_ids" in body:
        agent.adapter_ids = body.get("adapter_ids") or []
    if "coordinator" in body:
        agent.coordinator = body.get("coordinator")
    services.agents_store.put(agent)
    return agent.to_record()


@router.delete("/api/agents/{agent_id}")
def delete_agent(
    agent_id: str, principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    deleted = _main.services_for(principal).agents_store.delete(principal.tenant_id, agent_id)
    return {"deleted": deleted}


@router.get("/api/agents/{agent_id}/runs")
def get_agent_runs(
    agent_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    services = _main.services_for(principal)
    agent = services.agents_store.get(principal.tenant_id, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent 不存在")
    runs = [r for r in services.run_store.list(limit=1000) if r.get("agentId") == agent_id]
    # token 汇总走 AU 的账（docs/129 usage_store；按 runId 归属过滤，不新发明计量）。
    run_ids = {r["runId"] for r in runs}
    usage = [
        u for u in services.usage_store.list_recent(limit=2000)
        if u.get("runId") in run_ids
    ]
    token_summary = {
        "calls": len(usage),
        "promptTokens": sum(u.get("promptTokens", 0) for u in usage),
        "completionTokens": sum(u.get("completionTokens", 0) for u in usage),
        "totalTokens": sum(u.get("totalTokens", 0) for u in usage),
    }
    return {"agent": agent.to_record(), "items": runs, "tokenSummary": token_summary}
