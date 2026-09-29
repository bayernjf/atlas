"""PgRoutingStore：rollout 运行态 PG 落库（打包 T，ADR 无；契约 docs/81、03 `rollout_config`）。

方法面与 `store.RoutingStore` 逐字一致，差异只在并发原语：
  内存＝进程内 Lock＋dict；PG＝`SELECT ... FOR UPDATE` 行锁＋`rollout_states` 表。
无行＝idle 默认态；snapshot 无行不写库，其余写路径与 resolve UPSERT。
本文件不改变任何 409/422/计数语义——分支逻辑与内存实现刻意保持同构。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import json
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .models import RolloutConfig, TriggerEvent
from .router import (
    SEGMENT_UNPUBLISHED,
    resolve_version,
)
from .store import RolloutError, RolloutState, RoutingStore

_COLUMNS = (
    "status", "config", "stable", "candidate", "started_at", "rolled_back_at",
    "rollback_reason", "rollback_actor", "traffic",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PgRoutingStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    # ---------- 配置 ----------

    def configure(self, graph_id: str, config: RolloutConfig) -> RolloutState:
        with self._engine.begin() as conn:
            state = self._lock_or_insert(conn, graph_id)
            state.config = config
            self._write(conn, state)
            return state.model_copy(deep=True)

    def snapshot(self, graph_id: str) -> RolloutState:
        with self._engine.connect() as conn:
            state = self._load(conn, graph_id)
        if state is None:
            return RolloutState(graph_id=graph_id)
        return state.model_copy(deep=True)

    # ---------- 状态机 ----------

    def start(self, graph_id: str, versions: list[int]) -> RolloutState:
        with self._engine.begin() as conn:
            state = self._lock_or_insert(conn, graph_id)
            if state.config is None:
                raise RolloutError("尚未配置灰度规则，无法启动 canary（先 PUT rollout 配置）")
            if state.status != "idle":
                raise RolloutError(f"当前状态 {state.status} 不可启动 canary（仅 idle 可启动）")
            if len(versions) < 2:
                raise RolloutError("发布版本不足 2 个：canary 需要 stable 与 candidate 两个发布版")
            state.stable = versions[-2]
            state.candidate = versions[-1]
            state.status = "canary"
            state.started_at = _now_iso()
            state.rolled_back_at = None
            state.rollback_reason = None
            state.rollback_actor = None
            state.traffic = {
                "stable": 0,
                "candidate": 0,
                "segments": state.traffic["segments"]
                if set(state.traffic.get("segments", {})) >= {"internal", "lowValueBucket", "canary", "full", "fallback"}
                else {key: 0 for key in ("internal", "lowValueBucket", "canary", "full", "fallback")},
            }
            self._write(conn, state)
            return state.model_copy(deep=True)

    def promote(self, graph_id: str) -> RolloutState:
        with self._engine.begin() as conn:
            state = self._lock_or_insert(conn, graph_id)
            if state.status != "canary":
                raise RolloutError(f"当前状态 {state.status} 不可放量（仅 canary 可 promote 到 full）")
            state.status = "full"
            self._write(conn, state)
            return state.model_copy(deep=True)

    def rollback(
        self, graph_id: str, *, actor: str = "manual", reason: str = ""
    ) -> RolloutState:
        with self._engine.begin() as conn:
            state = self._lock_or_insert(conn, graph_id)
            if state.candidate is None or state.status == "idle":
                raise RolloutError("该图尚未启动灰度，无可回滚的 candidate")
            if state.status == "rolled_back":
                return state.model_copy(deep=True)
            state.status = "rolled_back"
            state.rolled_back_at = _now_iso()
            state.rollback_reason = reason or "manual rollback"
            state.rollback_actor = actor
            self._write(conn, state)
            return state.model_copy(deep=True)

    # ---------- 路由 ----------

    def resolve(
        self, graph_id: str, *, tenant: str, event: TriggerEvent
    ) -> tuple[int | None, str]:
        # 与内存 RoutingStore.resolve 同构；差异仅锁来源。resolve 一律落行（计数须存活）。
        with self._engine.begin() as conn:
            state = self._lock_or_insert(conn, graph_id)
            if state.status == "full":
                version, segment = state.candidate, "full"
            elif state.status == "rolled_back":
                version, segment = state.stable, "stable"
            elif state.status == "canary":
                config = state.config
                if config is not None:
                    config = config.model_copy(
                        update={"rules": [rule for rule in config.rules if rule.to != "full"]}  # type: ignore[attr-defined]
                    )
                version, segment = resolve_version(
                    graph_id=graph_id,
                    stable=state.stable,
                    candidate=state.candidate,
                    tenant=tenant,
                    event=event,
                    config=config,
                )
            else:  # idle：未启动，无 candidate
                version, segment = resolve_version(
                    graph_id=graph_id,
                    stable=state.stable,
                    candidate=state.candidate,
                    tenant=tenant,
                    event=event,
                    config=state.config,
                )
                if version is None:
                    segment = SEGMENT_UNPUBLISHED
            RoutingStore._count(state, version, segment)
            self._write(conn, state)
            return version, segment

    # ---------- 管理 ----------

    def reset(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM rollout_states WHERE tenant_id = :tenant"),
                {"tenant": self._tenant_id},
            )

    # ---------- 行映射 ----------

    def _load(self, conn: Any, graph_id: str) -> RolloutState | None:
        row = conn.execute(
            text(
                f"SELECT {', '.join(_COLUMNS)} FROM rollout_states "
                "WHERE tenant_id = :tenant AND graph_id = :graph"
            ),
            {"tenant": self._tenant_id, "graph": graph_id},
        ).first()
        return self._from_row(row, graph_id) if row is not None else None

    def _lock_or_insert(self, conn: Any, graph_id: str) -> RolloutState:
        row = conn.execute(
            text(
                f"SELECT {', '.join(_COLUMNS)} FROM rollout_states "
                "WHERE tenant_id = :tenant AND graph_id = :graph FOR UPDATE"
            ),
            {"tenant": self._tenant_id, "graph": graph_id},
        ).first()
        if row is not None:
            return self._from_row(row, graph_id)
        state = RolloutState(graph_id=graph_id)
        conn.execute(
            text(
                "INSERT INTO rollout_states "
                "(tenant_id, graph_id, status, config, stable, candidate, started_at, "
                "rolled_back_at, rollback_reason, rollback_actor, traffic, updated_at) "
                "VALUES (:tenant, :graph, 'idle', NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
                ":traffic, :now)"
            ),
            {
                "tenant": self._tenant_id, "graph": graph_id,
                "traffic": json.dumps(state.traffic, ensure_ascii=False),
                "now": _now_iso(),
            },
        )
        return state

    def _write(self, conn: Any, state: RolloutState) -> None:
        conn.execute(
            text(
                "UPDATE rollout_states SET status = :status, config = :config, "
                "stable = :stable, candidate = :candidate, started_at = :started_at, "
                "rolled_back_at = :rolled_back_at, rollback_reason = :rollback_reason, "
                "rollback_actor = :rollback_actor, traffic = :traffic, updated_at = :now "
                "WHERE tenant_id = :tenant AND graph_id = :graph"
            ),
            {
                "status": state.status,
                "config": json.dumps(state.config.model_dump(mode="json"), ensure_ascii=False)
                if state.config is not None else None,
                "stable": state.stable,
                "candidate": state.candidate,
                "started_at": state.started_at,
                "rolled_back_at": state.rolled_back_at,
                "rollback_reason": state.rollback_reason,
                "rollback_actor": state.rollback_actor,
                "traffic": json.dumps(state.traffic, ensure_ascii=False),
                "now": _now_iso(),
                "tenant": self._tenant_id,
                "graph": state.graph_id,
            },
        )

    @staticmethod
    def _from_row(row: Any, graph_id: str) -> RolloutState:
        data = dict(zip(_COLUMNS, row))
        return RolloutState(
            graph_id=graph_id,
            status=data["status"],
            config=RolloutConfig.model_validate(data["config"]) if data["config"] is not None else None,
            stable=data["stable"],
            candidate=data["candidate"],
            started_at=data["started_at"],
            rolled_back_at=data["rolled_back_at"],
            rollback_reason=data["rollback_reason"],
            rollback_actor=data["rollback_actor"],
            traffic=data["traffic"],
        )
