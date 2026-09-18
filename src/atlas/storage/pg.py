"""PG 存储实现（M5b，docs/24 §5 / 08 M5b 立项条）。

复用 `memory/database.py` 的 psycopg 连接层；每个 store 实例绑定 `tenant_id`
（由 `PgBackend` 工厂产出），行内 `tenant_id` 语句级过滤（不引入 RLS）。
进程内实现（`memory.py`）仍是默认/测试后端；本包经 `ATLAS_STORAGE_BACKEND=pg` 启用。

批 1 边界（08 M5b 立项条）：数据类 store（Graph/Session/Feedback/Recording/
Monitoring）+ 审批帧（approval）落表 + 内存 `threading.Event`（单进程内阻塞语义
不变）。**跨进程恢复（重启后读帧重建 pending + 1s 轮询）在批 2 `recovery.py`；
debug 会话是短命临时态、其帧落库不在 U43–U45 验收，批 1 保持内存实现**。
`runs` 表已建、其 store 在批 3 接线。
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import Engine, text

from atlas.iam.principals import Principal, Role
from atlas.monitoring.alerts import Alert, RuleConfig, rules_from_raw, validate_rules
from atlas.monitoring.records import RunRecord
from atlas.recording.cases import RecordingCase, RecordStep


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(conn: Any, prefix: str) -> str:
    value = conn.execute(text("SELECT nextval('storage_id_seq')")).scalar_one()
    return f"{prefix}-{int(value)}"


class PgBackend:
    """持有 engine 的 PG 后端；`*_store(tenant_id)` 工厂产出 per-tenant 实例。"""

    def __init__(self, engine: Engine):
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine

    def graph_store(self, tenant_id: str) -> "PgGraphStore":
        return PgGraphStore(self._engine, tenant_id)

    def session_store(self) -> "PgSessionStore":
        # SessionStore 是全局单例（不分租户）：token 全局唯一、principal_for_token 跨租户查。
        return PgSessionStore(self._engine)

    def feedback_store(self, tenant_id: str) -> "PgFeedbackStore":
        return PgFeedbackStore(self._engine, tenant_id)

    def recording_store(self, tenant_id: str) -> "PgRecordingStore":
        return PgRecordingStore(self._engine, tenant_id)

    def monitoring_store(self, tenant_id: str) -> "PgMonitoringStore":
        return PgMonitoringStore(self._engine, tenant_id)

    def run_store(self, tenant_id: str) -> "PgRunsStore":
        return PgRunsStore(self._engine, tenant_id)


_pg_backend: PgBackend | None = None
_pg_backend_lock = threading.Lock()


def get_pg_backend() -> PgBackend:
    """惰性单例：首次调用时按 DATABASE_URL 建 engine（缺失时 fail-closed）。"""
    global _pg_backend
    if _pg_backend is None:
        with _pg_backend_lock:
            if _pg_backend is None:
                from atlas.memory.database import create_database_engine

                _pg_backend = PgBackend(create_database_engine())
    return _pg_backend


class PgGraphStore:
    """图定义 PG 实现（latest 草稿 + 已发布版本，M6 版本化）。"""

    def __init__(self, engine: Engine, tenant_id: str):
        self._engine = engine
        self._tenant_id = tenant_id

    def save(self, raw: dict[str, Any]) -> str:
        with self._engine.begin() as conn:
            graph_id = _next_id(conn, "graph")
            conn.execute(
                text(
                    "INSERT INTO graphs (id, tenant_id, definition, node_count, updated_at) "
                    "VALUES (:id, :tenant_id, :definition, :node_count, :updated_at)"
                ),
                {
                    "id": graph_id,
                    "tenant_id": self._tenant_id,
                    "definition": json.dumps(raw, ensure_ascii=False),
                    "node_count": len(raw.get("nodes", [])),
                    "updated_at": _now_iso(),
                },
            )
        return graph_id

    def get(self, graph_id: str, release_version: int | None = None) -> dict[str, Any] | None:
        if release_version is None:
            sql = "SELECT definition FROM graphs WHERE id = :id AND tenant_id = :tenant_id"
            params: dict[str, Any] = {"id": graph_id, "tenant_id": self._tenant_id}
        else:
            sql = (
                "SELECT definition FROM graph_versions "
                "WHERE graph_id = :id AND release_version = :v AND tenant_id = :tenant_id"
            )
            params = {
                "id": graph_id,
                "v": release_version,
                "tenant_id": self._tenant_id,
            }
        with self._engine.connect() as conn:
            row = conn.execute(text(sql), params).first()
        return row[0] if row else None

    def list(self) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, node_count, updated_at FROM graphs "
                    "WHERE tenant_id = :tenant_id ORDER BY updated_at"
                ),
                {"tenant_id": self._tenant_id},
            ).all()
        return [{"id": r[0], "node_count": r[1], "updated_at": r[2]} for r in rows]

    def publish(self, graph_id: str, raw: dict[str, Any]) -> int:
        with self._engine.begin() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM graphs WHERE id = :id AND tenant_id = :tenant_id"),
                {"id": graph_id, "tenant_id": self._tenant_id},
            ).first()
            if exists is None:
                raise KeyError(graph_id)
            version = conn.execute(
                text(
                    "SELECT COALESCE(MAX(release_version), 0) + 1 FROM graph_versions "
                    "WHERE graph_id = :id AND tenant_id = :tenant_id"
                ),
                {"id": graph_id, "tenant_id": self._tenant_id},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO graph_versions "
                    "(graph_id, release_version, tenant_id, definition, created_at) "
                    "VALUES (:id, :v, :tenant_id, :definition, :created_at)"
                ),
                {
                    "id": graph_id,
                    "v": version,
                    "tenant_id": self._tenant_id,
                    "definition": json.dumps({**raw, "releaseVersion": version}, ensure_ascii=False),
                    "created_at": _now_iso(),
                },
            )
        return int(version)

    def list_versions(self, graph_id: str) -> list[int]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT release_version FROM graph_versions "
                    "WHERE graph_id = :id AND tenant_id = :tenant_id ORDER BY release_version"
                ),
                {"id": graph_id, "tenant_id": self._tenant_id},
            ).all()
        return [int(r[0]) for r in rows]

    def clear(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM graphs WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )
            conn.execute(
                text("DELETE FROM graph_versions WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )


class PgSessionStore:
    """登录会话 PG 实现（全局单例：token 全局唯一、跨租户查；Principal 字段展开存行）。"""

    def __init__(self, engine: Engine):
        self._engine = engine

    def issue(self, principal: Principal) -> str:
        token = f"sess-{uuid.uuid4().hex}"
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO iam_sessions (token, tenant_id, username, role, issued_at) "
                    "VALUES (:token, :tenant_id, :username, :role, :issued_at)"
                ),
                {
                    "token": token,
                    "tenant_id": principal.tenant_id,
                    "username": principal.username,
                    "role": principal.role.value,
                    "issued_at": _now_iso(),
                },
            )
        return token

    def principal_for_token(self, token: str | None) -> Principal | None:
        if not token:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT tenant_id, username, role FROM iam_sessions WHERE token = :token"
                ),
                {"token": token},
            ).first()
        if row is None:
            return None
        from atlas.iam.principals import SEED_TENANTS, SEED_USERS

        tenant_id = row[0]
        username = row[1]
        role = Role(row[2])
        user = next((u for u in SEED_USERS if u.username == username), None)
        display_name = user.display_name if user else username
        tenant = SEED_TENANTS.get(tenant_id)
        return Principal(
            tenant_id=tenant_id,
            tenant_name=tenant.name if tenant else tenant_id,
            username=username,
            display_name=display_name,
            role=role,
        )

    def revoke(self, token: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM iam_sessions WHERE token = :token"), {"token": token})

    def reset(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM iam_sessions"))


class PgFeedbackStore:
    """反馈 PG 实现（persistent 档，reset 不清除）。"""

    def __init__(self, engine: Engine, tenant_id: str):
        self._engine = engine
        self._tenant_id = tenant_id

    def add(self, request: Any) -> dict[str, Any]:
        with self._engine.begin() as conn:
            feedback_id = _next_id(conn, "feedback")
            item = {
                "id": feedback_id,
                "type": request.type,
                "content": request.content,
                "contact": request.contact,
                "created_at": _now_iso(),
            }
            conn.execute(
                text(
                    "INSERT INTO feedback (id, tenant_id, type, content, contact, created_at) "
                    "VALUES (:id, :tenant_id, :type, :content, :contact, :created_at)"
                ),
                {**item, "tenant_id": self._tenant_id},
            )
        return item

    def list(self) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, type, content, contact, created_at FROM feedback "
                    "WHERE tenant_id = :tenant_id ORDER BY created_at"
                ),
                {"tenant_id": self._tenant_id},
            ).all()
        return [
            {"id": r[0], "type": r[1], "content": r[2], "contact": r[3], "created_at": r[4]}
            for r in rows
        ]


class PgRecordingStore:
    """录制用例 PG 实现（persistent 档，reset 不清除）。"""

    def __init__(self, engine: Engine, tenant_id: str):
        self._engine = engine
        self._tenant_id = tenant_id

    def add(
        self,
        *,
        name: str,
        graph: dict[str, Any],
        inputs: dict[str, Any] | None,
        steps: list[RecordStep],
        status: str,
    ) -> RecordingCase:
        with self._engine.begin() as conn:
            case_id = _next_id(conn, "rec")
            created_at = _now_iso()
            conn.execute(
                text(
                    "INSERT INTO recordings "
                    "(id, tenant_id, name, graph, inputs, steps, status, created_at) "
                    "VALUES (:id, :tenant_id, :name, :graph, :inputs, :steps, :status, :created_at)"
                ),
                {
                    "id": case_id,
                    "tenant_id": self._tenant_id,
                    "name": name,
                    "graph": json.dumps(graph, ensure_ascii=False),
                    "inputs": json.dumps(inputs, ensure_ascii=False) if inputs is not None else None,
                    "steps": json.dumps([step.model_dump() for step in steps], ensure_ascii=False),
                    "status": status,
                    "created_at": created_at,
                },
            )
        return RecordingCase(
            id=case_id, name=name, graph=graph, inputs=inputs,
            steps=steps, status=status, created_at=created_at,
        )

    @staticmethod
    def _row_to_case(row: Any) -> RecordingCase:
        return RecordingCase(
            id=row[0], name=row[1], graph=row[2], inputs=row[3],
            steps=[RecordStep(**step) for step in row[4]],
            status=row[5], created_at=row[6],
        )

    def list(self) -> list[RecordingCase]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, name, graph, inputs, steps, status, created_at FROM recordings "
                    "WHERE tenant_id = :tenant_id ORDER BY created_at"
                ),
                {"tenant_id": self._tenant_id},
            ).all()
        return [self._row_to_case(r) for r in rows]

    def get(self, case_id: str) -> RecordingCase | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, name, graph, inputs, steps, status, created_at FROM recordings "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": case_id, "tenant_id": self._tenant_id},
            ).first()
        return self._row_to_case(row) if row else None

    def delete(self, case_id: str) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM recordings WHERE id = :id AND tenant_id = :tenant_id"),
                {"id": case_id, "tenant_id": self._tenant_id},
            )
        return result.rowcount > 0


class PgMonitoringStore:
    """监控 PG 实现（运行记录/告警/规则；规则评估复用 alerts 纯函数）。"""

    def __init__(self, engine: Engine, tenant_id: str):
        self._engine = engine
        self._tenant_id = tenant_id

    def record_run(
        self,
        *,
        graph_id: str,
        mode: Literal["sync", "stream"],
        status: Literal["completed", "error"],
        started_at: str,
        duration_ms: float,
        nodes: list,
        error: str | None = None,
        trace_id: str = "",
        resolved_version: int | None = None,
    ) -> RunRecord:
        from atlas.monitoring.alerts import evaluate_rules
        from atlas.monitoring.metrics import is_healthy

        serialized_nodes = [
            node.model_dump() if hasattr(node, "model_dump") else node for node in nodes
        ]
        with self._engine.begin() as conn:
            run_id = _next_id(conn, "run")
            record = RunRecord(
                id=run_id, graph_id=graph_id, mode=mode, status=status,
                started_at=started_at, finished_at=_now_iso(),
                duration_ms=duration_ms, nodes=nodes, error=error,
                trace_id=trace_id, resolved_version=resolved_version,
            )
            conn.execute(
                text(
                    "INSERT INTO monitoring_runs "
                    "(id, tenant_id, graph_id, mode, status, started_at, finished_at, "
                    "duration_ms, nodes, error, trace_id, resolved_version) "
                    "VALUES (:id, :tenant_id, :graph_id, :mode, :status, :started_at, "
                    ":finished_at, :duration_ms, :nodes, :error, :trace_id, :resolved_version)"
                ),
                {
                    "id": run_id,
                    "tenant_id": self._tenant_id,
                    "graph_id": graph_id,
                    "mode": mode,
                    "status": status,
                    "started_at": started_at,
                    "finished_at": record.finished_at,
                    "duration_ms": duration_ms,
                    "nodes": json.dumps(serialized_nodes, ensure_ascii=False),
                    "error": error,
                    "trace_id": trace_id,
                    "resolved_version": resolved_version,
                },
            )
            rules = self._rules_locked(conn)
            healthy = is_healthy(record)
            streak = self._streak_locked(conn, graph_id, healthy)
            recent = self._recent_locked(conn, graph_id)
            events = evaluate_rules(
                record=record, healthy=healthy, recent_by_graph=recent,
                streak=streak, rules=rules,
            )
            for event in events:
                self._raise_or_merge_locked(conn, event, record)
        return record

    def _rules_locked(self, conn: Any) -> RuleConfig:
        row = conn.execute(
            text("SELECT config FROM monitoring_rules WHERE tenant_id = :tenant_id"),
            {"tenant_id": self._tenant_id},
        ).first()
        return RuleConfig(**row[0]) if row else RuleConfig()

    def _streak_locked(self, conn: Any, graph_id: str, healthy: bool) -> int:
        """连续错误计数（与进程内一致：healthy 清零，否则 +1）。"""
        if healthy:
            return 0
        row = conn.execute(
            text(
                "SELECT count(*) FROM monitoring_runs "
                "WHERE tenant_id = :tenant_id AND graph_id = :graph_id AND status = 'error'"
            ),
            {"tenant_id": self._tenant_id, "graph_id": graph_id},
        ).scalar_one()
        return int(row)

    def _recent_locked(self, conn: Any, graph_id: str) -> list[RunRecord]:
        rows = conn.execute(
            text(
                f"SELECT {self._RUN_COLS} FROM monitoring_runs "
                "WHERE tenant_id = :tenant_id AND graph_id = :graph_id "
                "ORDER BY finished_at DESC LIMIT 200"
            ),
            {"tenant_id": self._tenant_id, "graph_id": graph_id},
        ).all()
        return [self._run_from_row(r) for r in rows]

    def _raise_or_merge_locked(self, conn: Any, event: Any, record: RunRecord) -> None:
        row = conn.execute(
            text(
                "SELECT id FROM monitoring_alerts "
                "WHERE tenant_id = :tenant_id AND rule_id = :rule_id AND graph_id = :graph_id "
                "AND status != 'resolved' ORDER BY first_seen DESC LIMIT 1"
            ),
            {
                "tenant_id": self._tenant_id,
                "rule_id": event.rule_id,
                "graph_id": record.graph_id,
            },
        ).first()
        if row is not None:
            conn.execute(
                text(
                    "UPDATE monitoring_alerts SET count = count + 1, last_seen = :last_seen, "
                    "last_run_id = :last_run_id WHERE id = :id"
                ),
                {"last_seen": record.finished_at, "last_run_id": record.id, "id": row[0]},
            )
            return
        alert_id = _next_id(conn, "alt")
        conn.execute(
            text(
                "INSERT INTO monitoring_alerts "
                "(id, tenant_id, rule_id, graph_id, severity, message, status, first_seen, "
                "last_seen, last_run_id, count) "
                "VALUES (:id, :tenant_id, :rule_id, :graph_id, :severity, :message, 'open', "
                ":first_seen, :last_seen, :last_run_id, 1)"
            ),
            {
                "id": alert_id,
                "tenant_id": self._tenant_id,
                "rule_id": event.rule_id,
                "graph_id": record.graph_id,
                "severity": event.severity,
                "message": event.message,
                "first_seen": record.finished_at,
                "last_seen": record.finished_at,
                "last_run_id": record.id,
            },
        )

    def list_runs(self, graph_id: str | None = None, limit: int = 50) -> list[RunRecord]:
        with self._engine.connect() as conn:
            if graph_id:
                rows = conn.execute(
                    text(
                        f"SELECT {self._RUN_COLS} FROM monitoring_runs "
                        "WHERE tenant_id = :tenant_id AND graph_id = :graph_id "
                        "ORDER BY finished_at DESC LIMIT :limit"
                    ),
                    {"tenant_id": self._tenant_id, "graph_id": graph_id, "limit": limit},
                ).all()
            else:
                rows = conn.execute(
                    text(
                        f"SELECT {self._RUN_COLS} FROM monitoring_runs "
                        "WHERE tenant_id = :tenant_id ORDER BY finished_at DESC LIMIT :limit"
                    ),
                    {"tenant_id": self._tenant_id, "limit": limit},
                ).all()
        return [self._run_from_row(r) for r in rows]

    @staticmethod
    def _run_from_row(r: Any) -> RunRecord:
        return RunRecord(
            id=r[0], graph_id=r[1], mode=r[2], status=r[3], started_at=r[4],
            finished_at=r[5], duration_ms=r[6], nodes=r[7], error=r[8],
            trace_id=r[9] or "", resolved_version=r[10],
        )

    _RUN_COLS = (
        "id, graph_id, mode, status, started_at, finished_at, "
        "duration_ms, nodes, error, trace_id, resolved_version"
    )

    @staticmethod
    def _alert_from_row(r: Any) -> Alert:
        return Alert(
            id=r[0], rule_id=r[1], graph_id=r[2], severity=r[3], message=r[4],
            status=r[5], first_seen=r[6], last_seen=r[7], last_run_id=r[8], count=r[9],
        )

    _ALERT_COLS = (
        "id, rule_id, graph_id, severity, message, status, first_seen, "
        "last_seen, last_run_id, count"
    )

    def list_alerts(self, status: str | None = None) -> list[Alert]:
        with self._engine.connect() as conn:
            if status:
                rows = conn.execute(
                    text(
                        f"SELECT {self._ALERT_COLS} FROM monitoring_alerts "
                        "WHERE tenant_id = :tenant_id AND status = :status ORDER BY first_seen DESC"
                    ),
                    {"tenant_id": self._tenant_id, "status": status},
                ).all()
            else:
                rows = conn.execute(
                    text(
                        f"SELECT {self._ALERT_COLS} FROM monitoring_alerts "
                        "WHERE tenant_id = :tenant_id ORDER BY first_seen DESC"
                    ),
                    {"tenant_id": self._tenant_id},
                ).all()
        return [self._alert_from_row(r) for r in rows]

    def get_alert(self, alert_id: str) -> Alert | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT {self._ALERT_COLS} FROM monitoring_alerts "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": alert_id, "tenant_id": self._tenant_id},
            ).first()
        return self._alert_from_row(row) if row else None

    def acknowledge_alert(self, alert_id: str) -> Alert | Literal[False] | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT status FROM monitoring_alerts WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": alert_id, "tenant_id": self._tenant_id},
            ).first()
            if row is None:
                return None
            if row[0] != "open":
                return False
            conn.execute(
                text(
                    "UPDATE monitoring_alerts SET status = 'acknowledged' "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": alert_id, "tenant_id": self._tenant_id},
            )
        return self.get_alert(alert_id)

    def resolve_alert(self, alert_id: str) -> Alert | Literal[False] | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT status FROM monitoring_alerts WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": alert_id, "tenant_id": self._tenant_id},
            ).first()
            if row is None:
                return None
            if row[0] == "resolved":
                return False
            conn.execute(
                text(
                    "UPDATE monitoring_alerts SET status = 'resolved' "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": alert_id, "tenant_id": self._tenant_id},
            )
        return self.get_alert(alert_id)

    def get_rules(self) -> RuleConfig:
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT config FROM monitoring_rules WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            ).first()
        return RuleConfig(**row[0]) if row else RuleConfig()

    def update_rules(self, raw: dict) -> RuleConfig:
        errors = validate_rules(raw)
        if errors:
            raise ValueError("；".join(errors))
        rules = rules_from_raw(raw)
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO monitoring_rules (tenant_id, config) VALUES (:tenant_id, :config) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET config = EXCLUDED.config"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "config": json.dumps(rules.model_dump(), ensure_ascii=False),
                },
            )
        return rules.model_copy(deep=True)

    def snapshot_metrics(self) -> dict:
        from atlas.monitoring.metrics import summarize

        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT {self._RUN_COLS} FROM monitoring_runs WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": self._tenant_id},
            ).all()
        return summarize([self._run_from_row(r) for r in rows])

    def reset(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM monitoring_runs WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )
            conn.execute(
                text("DELETE FROM monitoring_alerts WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )
            conn.execute(
                text("DELETE FROM monitoring_rules WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )


class PgRunsStore:
    """运行生命周期状态 PG 实现（M5b，docs/24 §3.3/§4）。"""

    def __init__(self, engine: Engine, tenant_id: str):
        self._engine = engine
        self._tenant_id = tenant_id

    def begin(self, *, run_id: str, graph_id: str, mode: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO runs (id, tenant_id, graph_id, status, started_at) "
                    "VALUES (:id, :tenant_id, :graph_id, 'running', :now)"
                ),
                {"id": run_id, "tenant_id": self._tenant_id, "graph_id": graph_id, "now": _now_iso()},
            )

    def suspend(
        self, *, run_id: str, node_id: str, kind: str,
        resume_token: str, deadline_at: str | None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE runs SET status = 'suspended', suspended_at = :now, node_id = :node_id, "
                    "kind = :kind, resume_token = :resume_token, deadline_at = :deadline_at "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {
                    "id": run_id, "tenant_id": self._tenant_id, "now": _now_iso(),
                    "node_id": node_id, "kind": kind,
                    "resume_token": resume_token, "deadline_at": deadline_at,
                },
            )

    def finish(
        self, *, run_id: str, status: str, error: str | None = None,
        outputs: dict | None = None, trace: list | None = None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE runs SET status = :status, finished_at = :now, error = :error, "
                    "outputs = :outputs, trace = :trace, kind = NULL, node_id = NULL, "
                    "deadline_at = NULL, resume_token = NULL "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {
                    "id": run_id, "tenant_id": self._tenant_id, "status": status,
                    "now": _now_iso(), "error": error,
                    "outputs": json.dumps(outputs, ensure_ascii=False) if outputs is not None else None,
                    "trace": json.dumps(trace, ensure_ascii=False) if trace is not None else None,
                },
            )

    def get(self, run_id: str) -> dict | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, graph_id, status, started_at, suspended_at, finished_at, error, "
                    "kind, node_id, deadline_at, resume_token, outputs, trace FROM runs "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": run_id, "tenant_id": self._tenant_id},
            ).first()
        if row is None:
            return None
        suspension = None
        if row[7]:
            suspension = {"kind": row[7], "nodeId": row[8], "deadlineAt": row[9], "resumeToken": row[10]}
        return {
            "runId": row[0], "graphId": row[1], "status": row[2], "startedAt": row[3],
            "suspendedAt": row[4], "finishedAt": row[5], "error": row[6],
            "outputs": row[11], "trace": row[12], "suspension": suspension,
        }

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            sql = (
                "SELECT id, graph_id, status, started_at, suspended_at, kind, node_id, "
                "deadline_at, resume_token FROM runs WHERE tenant_id = :tenant_id "
                "AND status = :status ORDER BY started_at DESC LIMIT :limit"
            )
            params: dict = {"tenant_id": self._tenant_id, "status": status, "limit": limit}
        else:
            sql = (
                "SELECT id, graph_id, status, started_at, suspended_at, kind, node_id, "
                "deadline_at, resume_token FROM runs WHERE tenant_id = :tenant_id "
                "ORDER BY started_at DESC LIMIT :limit"
            )
            params = {"tenant_id": self._tenant_id, "limit": limit}
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).all()
        return [
            {
                "runId": r[0], "graphId": r[1], "status": r[2], "startedAt": r[3],
                "suspendedAt": r[4], "kind": r[5], "nodeId": r[6],
                "deadlineAt": r[7], "resumeToken": r[8],
            }
            for r in rows
        ]

    def reset(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM runs WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )
