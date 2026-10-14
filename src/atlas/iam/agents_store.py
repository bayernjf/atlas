# -*- coding: utf-8 -*-
"""Agent 配置存储（打包 BO，docs/131 D74）：run/节点带 agent 维度后的「可管理 agent 实体」。

复用 channels/deliveries 的范式（内存/PG 两档）。agent 是 Bot 升格的可管理实例
（docs/131 D-D6）：配置（name/enabled/skills/adapter_ids/coordinator）落库，运行数据
从 runs（按 agent_id 过滤）聚合，不在此存。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Agent:
    id: str
    tenant_id: str
    name: str
    enabled: bool = True
    skills: list[str] = field(default_factory=list)
    adapter_ids: list[str] = field(default_factory=list)
    coordinator: dict[str, Any] | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "enabled": self.enabled,
            "skills": list(self.skills), "adapter_ids": list(self.adapter_ids),
            "coordinator": self.coordinator,
            "createdAt": self.created_at, "updatedAt": self.updated_at,
        }


def _copy(a: Agent) -> dict[str, Any]:
    return {
        "id": a.id, "tenant_id": a.tenant_id, "name": a.name, "enabled": a.enabled,
        "skills": list(a.skills), "adapter_ids": list(a.adapter_ids),
        "coordinator": a.coordinator, "created_at": a.created_at, "updated_at": a.updated_at,
    }


class InMemoryAgentStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: dict[str, Agent] = {}

    def put(self, agent: Agent) -> Agent:
        with self._lock:
            agent.updated_at = _now_iso()
            self._agents[agent.id] = agent
            return agent

    def get(self, tenant_id: str, agent_id: str) -> Agent | None:
        with self._lock:
            a = self._agents.get(agent_id)
            return Agent(**_copy(a)) if a is not None else None

    def list(self, tenant_id: str) -> list[Agent]:
        with self._lock:
            return [Agent(**_copy(a)) for a in self._agents.values()]

    def delete(self, tenant_id: str, agent_id: str) -> bool:
        with self._lock:
            return self._agents.pop(agent_id, None) is not None


class PgAgentStore:
    _COLS = (
        "id, tenant_id, name, enabled, skills, adapter_ids, coordinator, created_at, updated_at"
    )

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def put(self, agent: Agent) -> Agent:
        now = _now_iso()
        with self._engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO agents (id, tenant_id, name, enabled, skills, adapter_ids, "
                    "coordinator, created_at, updated_at) VALUES (:id, :t, :name, :enabled, "
                    "CAST(:skills AS jsonb), CAST(:adapters AS jsonb), CAST(:coord AS jsonb), "
                    ":created, :updated) "
                    "ON CONFLICT (tenant_id, id) DO UPDATE SET name = :name, enabled = :enabled, "
                    "skills = CAST(:skills AS jsonb), adapter_ids = CAST(:adapters AS jsonb), "
                    "coordinator = CAST(:coord AS jsonb), updated_at = :updated"
                ),
                {
                    "id": agent.id, "t": agent.tenant_id, "name": agent.name,
                    "enabled": agent.enabled,
                    "skills": json.dumps(agent.skills, ensure_ascii=False),
                    "adapters": json.dumps(agent.adapter_ids, ensure_ascii=False),
                    "coord": json.dumps(agent.coordinator or {}, ensure_ascii=False),
                    "created": agent.created_at or now, "updated": now,
                },
            )
        agent.updated_at = now
        return agent

    def get(self, tenant_id: str, agent_id: str) -> Agent | None:
        with self._engine.connect() as db:
            row = db.execute(
                text(
                    f"SELECT {self._COLS} FROM agents WHERE tenant_id = :t AND id = :id"
                ),
                {"t": tenant_id, "id": agent_id},
            ).first()
        return self._row_to_agent(row) if row is not None else None

    def list(self, tenant_id: str) -> list[Agent]:
        with self._engine.connect() as db:
            rows = db.execute(
                text(
                    f"SELECT {self._COLS} FROM agents WHERE tenant_id = :t ORDER BY created_at"
                ),
                {"t": tenant_id},
            ).all()
        return [self._row_to_agent(r) for r in rows]

    def delete(self, tenant_id: str, agent_id: str) -> bool:
        with self._engine.begin() as db:
            result = db.execute(
                text("DELETE FROM agents WHERE tenant_id = :t AND id = :id"),
                {"t": tenant_id, "id": agent_id},
            )
        return result.rowcount > 0

    def _row_to_agent(self, row) -> Agent:
        return Agent(
            id=row[0], tenant_id=row[1], name=row[2], enabled=bool(row[3]),
            skills=row[4] or [], adapter_ids=row[5] or [], coordinator=row[6],
            created_at=row[7], updated_at=row[8],
        )
