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
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from atlas.monitoring.alerts import (
    Alert,
    RuleConfig,
    apply_escalation,
    rules_from_raw,
    validate_rules,
)
from atlas.monitoring.silences import (
    _UNSET,
    OnCallEmpty,
    OnCallSchedule,
    Silence,
    current_assignee,
    is_silence_active,
    maybe_auto_rotate,
    silence_matches,
)
from atlas.monitoring.notify import (
    AlertChannel,
    AlertChannelDelivery,
    alert_channel_from_raw,
    validate_alert_channel,
)
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

    def user_store(self) -> "PgUserStore":
        return PgUserStore(self._engine, self)

    def feedback_store(self, tenant_id: str) -> "PgFeedbackStore":
        return PgFeedbackStore(self._engine, tenant_id)

    def recording_store(self, tenant_id: str) -> "PgRecordingStore":
        return PgRecordingStore(self._engine, tenant_id)

    def monitoring_store(self, tenant_id: str) -> "PgMonitoringStore":
        return PgMonitoringStore(self._engine, tenant_id)

    def run_store(self, tenant_id: str) -> "PgRunsStore":
        return PgRunsStore(self._engine, tenant_id)

    def memory_store(self, tenant_id: str) -> "PgMemoryStore":
        return PgMemoryStore(self._engine, tenant_id)

    def audit_store(self, tenant_id: str) -> "PgAuditStore":
        return PgAuditStore(self._engine, tenant_id)

    def connection_store(self, tenant_id: str) -> "PgConnectionStore":
        return PgConnectionStore(self._engine, tenant_id)

    def channel_store(self, tenant_id: str):
        from atlas.channels.pg import PgChannelStore

        return PgChannelStore(self._engine, tenant_id)


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

    def update_draft(self, graph_id: str, raw: dict[str, Any]) -> None:
        """覆盖 latest 草稿（M9）；行不存在（含跨租户）抛 KeyError，已发布版本不变。"""
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE graphs SET definition = :definition, node_count = :node_count, "
                    "updated_at = :updated_at WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {
                    "id": graph_id,
                    "tenant_id": self._tenant_id,
                    "definition": json.dumps(raw, ensure_ascii=False),
                    "node_count": len(raw.get("nodes", [])),
                    "updated_at": _now_iso(),
                },
            )
            if result.rowcount == 0:
                raise KeyError(graph_id)

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
        now = datetime.now(timezone.utc)
        from atlas.iam.sessions import session_ttl_seconds

        expires_at = (now + timedelta(seconds=session_ttl_seconds())).isoformat()
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO iam_sessions "
                    "(token, tenant_id, username, role, issued_at, expires_at) "
                    "VALUES (:token, :tenant_id, :username, :role, :issued_at, :expires_at)"
                ),
                {
                    "token": token,
                    "tenant_id": principal.tenant_id,
                    "username": principal.username,
                    "role": principal.role.value,
                    "issued_at": now.isoformat(),
                    "expires_at": expires_at,
                },
            )
        return token

    def principal_for_token(self, token: str | None) -> Principal | None:
        if not token:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT tenant_id, username, role, expires_at FROM iam_sessions "
                    "WHERE token = :token"
                ),
                {"token": token},
            ).first()
        if row is None:
            return None
        tenant_id = row[0]
        username = row[1]
        expires_at_raw = row[3]
        if expires_at_raw is not None:
            try:
                expires_at = datetime.fromisoformat(expires_at_raw)
            except ValueError:
                expires_at = None
            if expires_at is not None and expires_at <= datetime.now(timezone.utc):
                self.revoke(token)
                return None
        from atlas.iam.principals import SEED_TENANTS, SEED_USERS, Principal, Role

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

    def revoke_for_user(self, tenant_id: str, username: str, *, keep_token: str | None = None) -> None:
        sql = "DELETE FROM iam_sessions WHERE tenant_id = :tenant_id AND username = :username"
        params: dict[str, Any] = {"tenant_id": tenant_id, "username": username}
        if keep_token is not None:
            sql += " AND token != :keep_token"
            params["keep_token"] = keep_token
        with self._engine.begin() as conn:
            conn.execute(text(sql), params)

    def reset(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM iam_sessions"))


class PgUserStore:
    """账号 PG 实现（全局单例：username 登录全局查；会话吊销经 PgBackend.session_store）。"""

    def __init__(self, engine: Engine, backend: "PgBackend"):
        self._engine = engine
        self._backend = backend

    def bind_session_store(self, session_store: Any) -> None:
        """与内存档 ``UserStore`` 装配接口同构（``iam/deps.py`` 模块级统一调用）。

        PG 档会话吊销在 :meth:`update`/:meth:`set_password` 内经由
        ``PgBackend.session_store()`` 单例现取（操作同一张 ``iam_sessions`` 表），
        无需像内存档那样外部持有会话存储引用，故此处显式 no-op。会话 PG 化本身由
        :class:`PgSessionStore` 承担（docs/30 §4），不在此重复绑定。
        """
        return None

    def _row_to_account(self, row: Any) -> "UserAccount":
        from atlas.iam.accounts import UserAccount
        from atlas.iam.principals import Role

        return UserAccount(
            tenant_id=row[0],
            username=row[1],
            password_hash=row[2],
            display_name=row[3],
            role=Role(row[4]),
            status=row[5],
            created_at=row[6],
            updated_at=row[7],
        )

    _COLUMNS = "tenant_id, username, password_hash, display_name, role, status, created_at, updated_at"

    def seed(self, users: list | None = None) -> int:
        from atlas.iam.accounts import SEED_USERS as _default
        from atlas.iam.passwords import hash_password

        inserted = 0
        for user in users or _default:
            now = _now_iso()
            with self._engine.begin() as conn:
                clash = conn.execute(
                    text(
                        "SELECT tenant_id FROM iam_users WHERE username = :u "
                        "ORDER BY tenant_id LIMIT 1"
                    ),
                    {"u": user.username},
                ).first()
                if clash is not None and clash[0] != user.tenant_id:
                    raise ValueError(
                        f"用户名 {user.username!r} 已被租户 {clash[0]} 占用，"
                        "用户名全局唯一（docs/64 J-1d）"
                    )
                result = conn.execute(
                    text(
                        "INSERT INTO iam_users (tenant_id, username, password_hash, display_name, "
                        "role, status, created_at, updated_at) "
                        "VALUES (:tenant_id, :username, :password_hash, :display_name, :role, "
                        "'active', :created_at, :updated_at) ON CONFLICT DO NOTHING"
                    ),
                    {
                        "tenant_id": user.tenant_id,
                        "username": user.username,
                        "password_hash": hash_password(user.password),
                        "display_name": user.display_name,
                        "role": user.role.value,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                inserted += result.rowcount
        return inserted

    def get(self, tenant_id: str, username: str) -> "UserAccount | None":
        with self._engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT {self._COLUMNS} FROM iam_users WHERE tenant_id = :t AND username = :u"),
                {"t": tenant_id, "u": username},
            ).first()
        return self._row_to_account(row) if row else None

    def get_by_username(self, username: str) -> "UserAccount | None":
        with self._engine.connect() as conn:
            # docs/64 J-1d：确定性排序（字典序最小租户），不再依赖物理行序。
            row = conn.execute(
                text(
                    f"SELECT {self._COLUMNS} FROM iam_users "
                    "WHERE username = :u ORDER BY tenant_id, username"
                ),
                {"u": username},
            ).first()
        return self._row_to_account(row) if row else None

    def list(self, tenant_id: str) -> list["UserAccount"]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT {self._COLUMNS} FROM iam_users WHERE tenant_id = :t ORDER BY username"
                ),
                {"t": tenant_id},
            ).all()
        return [self._row_to_account(row) for row in rows]

    def create(
        self,
        *,
        tenant_id: str,
        username: str,
        password: str,
        display_name: str,
        role: Role,
    ) -> "UserAccount":
        from atlas.iam.accounts import UserAccount, UserExists
        from atlas.iam.passwords import hash_password

        now = _now_iso()
        try:
            with self._engine.begin() as conn:
                clash = conn.execute(
                    text(
                        "SELECT tenant_id FROM iam_users WHERE username = :u "
                        "ORDER BY tenant_id LIMIT 1"
                    ),
                    {"u": username},
                ).first()
                if clash is not None and clash[0] != tenant_id:
                    raise UserExists(username)
                conn.execute(
                    text(
                        "INSERT INTO iam_users (tenant_id, username, password_hash, display_name, "
                        "role, status, created_at, updated_at) "
                        "VALUES (:tenant_id, :username, :password_hash, :display_name, :role, "
                        "'active', :created_at, :updated_at)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "username": username,
                        "password_hash": hash_password(password),
                        "display_name": display_name,
                        "role": role.value,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
        except IntegrityError:
            raise UserExists(username)
        account = self.get(tenant_id, username)
        assert account is not None
        return account

    def update(
        self,
        tenant_id: str,
        username: str,
        *,
        display_name: str | None = None,
        role: Role | None = None,
        status: str | None = None,
    ) -> "UserAccount | None":
        fields: dict[str, Any] = {}
        if display_name is not None:
            fields["display_name"] = display_name
        if role is not None:
            fields["role"] = role.value
        if status is not None:
            fields["status"] = status
        if not fields:
            return self.get(tenant_id, username)
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        fields.update({"t": tenant_id, "u": username, "updated_at": _now_iso()})
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    f"UPDATE iam_users SET {assignments}, updated_at = :updated_at "
                    "WHERE tenant_id = :t AND username = :u"
                ),
                fields,
            )
            if result.rowcount == 0:
                return None
        if status == "disabled":
            self._backend.session_store().revoke_for_user(tenant_id, username)
        return self.get(tenant_id, username)

    def set_password(
        self,
        tenant_id: str,
        username: str,
        password: str,
        *,
        keep_token: str | None = None,
    ) -> "UserAccount | None":
        from atlas.iam.passwords import hash_password

        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE iam_users SET password_hash = :password_hash, updated_at = :updated_at "
                    "WHERE tenant_id = :t AND username = :u"
                ),
                {
                    "password_hash": hash_password(password),
                    "updated_at": _now_iso(),
                    "t": tenant_id,
                    "u": username,
                },
            )
            if result.rowcount == 0:
                return None
        self._backend.session_store().revoke_for_user(
            tenant_id, username, keep_token=keep_token
        )
        return self.get(tenant_id, username)


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
        graph_id: str = "",
        subgraphs: dict[str, dict[str, Any]] | None = None,
        recorded_at: str | None = None,
    ) -> RecordingCase:
        with self._engine.begin() as conn:
            case_id = _next_id(conn, "rec")
            created_at = _now_iso()
            recorded = recorded_at or created_at
            frozen_subgraphs = subgraphs or {}
            conn.execute(
                text(
                    "INSERT INTO recordings "
                    "(id, tenant_id, name, graph_id, graph, inputs, steps, status, "
                    "created_at, recorded_at, subgraphs) "
                    "VALUES (:id, :tenant_id, :name, :graph_id, :graph, :inputs, :steps, "
                    ":status, :created_at, :recorded_at, :subgraphs)"
                ),
                {
                    "id": case_id,
                    "tenant_id": self._tenant_id,
                    "name": name,
                    "graph_id": graph_id,
                    "graph": json.dumps(graph, ensure_ascii=False),
                    "inputs": json.dumps(inputs, ensure_ascii=False) if inputs is not None else None,
                    "steps": json.dumps([step.model_dump() for step in steps], ensure_ascii=False),
                    "status": status,
                    "created_at": created_at,
                    "recorded_at": recorded,
                    "subgraphs": json.dumps(frozen_subgraphs, ensure_ascii=False),
                },
            )
        return RecordingCase(
            id=case_id, name=name, graph_id=graph_id, graph=graph, inputs=inputs,
            steps=steps, status=status, created_at=created_at, recorded_at=recorded,
            subgraphs=frozen_subgraphs,
        )

    @staticmethod
    def _row_to_case(row: Any) -> RecordingCase:
        return RecordingCase(
            id=row[0], name=row[1], graph=row[2], inputs=row[3],
            steps=[RecordStep(**step) for step in row[4]],
            status=row[5], created_at=row[6], recorded_at=row[7],
            graph_id=row[8] or "", subgraphs=row[9] or {},
        )

    _SELECT_COLS = (
        "SELECT id, name, graph, inputs, steps, status, created_at, recorded_at, "
        "graph_id, subgraphs FROM recordings "
    )

    def list(self) -> list[RecordingCase]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    self._SELECT_COLS
                    + "WHERE tenant_id = :tenant_id ORDER BY created_at"
                ),
                {"tenant_id": self._tenant_id},
            ).all()
        return [self._row_to_case(r) for r in rows]

    def get(self, case_id: str) -> RecordingCase | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    self._SELECT_COLS
                    + "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": case_id, "tenant_id": self._tenant_id},
            ).first()
        return self._row_to_case(row) if row else None

    def update_meta(
        self,
        case_id: str,
        *,
        name: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> RecordingCase | None:
        """仅改 name/inputs（docs/28 §2.3）；不存在返 None，无变更字段时回读原样返回。"""
        sets: list[str] = []
        params: dict[str, Any] = {}
        if name is not None:
            sets.append("name = :name")
            params["name"] = name
        if inputs is not None:
            sets.append("inputs = :inputs")
            params["inputs"] = json.dumps(inputs, ensure_ascii=False)
        if sets:
            params.update({"id": case_id, "tenant_id": self._tenant_id})
            with self._engine.begin() as conn:
                result = conn.execute(
                    text(
                        "UPDATE recordings SET " + ", ".join(sets)
                        + " WHERE id = :id AND tenant_id = :tenant_id"
                    ),
                    params,
                )
            if result.rowcount == 0:
                return None
        return self.get(case_id)

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
        # docs/59 F-2：静默/值班/assignee 落 PG（monitoring_silences/monitoring_oncall），
        # 跨重启/跨实例保留；内存档仍用 OpsStore（monitoring/records.py），本类不再持有。
        self._notifier = None
        # docs/55 flapping：自动 recovery 冷却 (rule_id, graph_id)→UTC 时刻（进程内，不持久化）
        self._recovery_cooldown: dict[tuple[str, str], datetime] = {}

    def record_run(
        self,
        *,
        graph_id: str,
        mode: Literal["sync", "stream"],
        status: Literal["completed", "error", "cancelled"],
        started_at: str,
        duration_ms: float,
        nodes: list,
        error: str | None = None,
        trace_id: str = "",
        resolved_version: int | None = None,
        business=None,
        tool_calls: list | None = None,
        spans: dict | None = None,
    ) -> RunRecord:
        from atlas.monitoring.alerts import evaluate_rules
        from atlas.monitoring.metrics import is_healthy

        serialized_nodes = [
            node.model_dump() if hasattr(node, "model_dump") else node for node in nodes
        ]
        pending: list[tuple[Alert, AlertChannel, str]] = []
        with self._engine.begin() as conn:
            run_id = _next_id(conn, "run")
            record = RunRecord(
                id=run_id, graph_id=graph_id, mode=mode, status=status,
                started_at=started_at, finished_at=_now_iso(),
                duration_ms=duration_ms, nodes=nodes, error=error,
                trace_id=trace_id, resolved_version=resolved_version,
                business=business, tool_calls=tool_calls or [], spans=spans,
            )
            conn.execute(
                text(
                    "INSERT INTO monitoring_runs "
                    "(id, tenant_id, graph_id, mode, status, started_at, finished_at, "
                    "duration_ms, nodes, error, trace_id, resolved_version, business, tool_calls, spans) "
                    "VALUES (:id, :tenant_id, :graph_id, :mode, :status, :started_at, "
                    ":finished_at, :duration_ms, :nodes, :error, :trace_id, :resolved_version, "
                    ":business, :tool_calls, :spans)"
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
                    "business": json.dumps(business.model_dump(), ensure_ascii=False) if business is not None else None,
                    "tool_calls": json.dumps(
                        [m.model_dump() if hasattr(m, "model_dump") else m for m in record.tool_calls],
                        ensure_ascii=False,
                    ),
                    "spans": json.dumps(spans, ensure_ascii=False) if spans else "{}",
                },
            )
            rules = self._rules_locked(conn)
            healthy = is_healthy(record)
            streak = self._streak_locked(conn, graph_id, healthy)
            recent = self._recent_locked(conn, graph_id)
            # docs/55：recent 新→旧，连续健康达阈值才允许自动 recovery（flapping）
            healthy_streak = self._healthy_streak_locked(recent) if healthy else 0
            events = evaluate_rules(
                record=record, healthy=healthy, recent_by_graph=recent,
                streak=streak, rules=rules,
            )
            for event in events:
                # docs/33 §5.1：命中活跃静默则压下（不 INSERT/不合并/不升级）
                if self._suppress_if_matched_locked(
                    conn, event.rule_id, record.graph_id, _now_iso()
                ):
                    continue
                raised = self._raise_or_merge_locked(conn, event, record)
                if raised is not None:
                    new_alert, transition = raised
                    # docs/55：自动 recovery 冷却窗内，新告警站内照建、抑制 new 外部通知
                    if transition == "new" and self._in_recovery_cooldown(
                        event.rule_id, record.graph_id, rules
                    ):
                        pass
                    else:
                        pending.append(
                            (new_alert, self._channel_locked(conn), transition)
                        )
            # docs/54 §6 + docs/55：连续健康达标，自动恢复该图仍 open 的内置告警
            # （acknowledged 与 rollout_gate 不自动恢复），并写冷却起点；锁外发 recovery。
            if healthy and healthy_streak >= rules.recovery_healthy_streak:
                open_rows = conn.execute(
                    text(
                        f"SELECT {self._ALERT_COLS} FROM monitoring_alerts "
                        "WHERE tenant_id = :tenant_id AND graph_id = :graph_id "
                        "AND status = 'open' AND rule_id != 'rollout_gate'"
                    ),
                    {"tenant_id": self._tenant_id, "graph_id": graph_id},
                ).all()
                if open_rows:
                    recovered_at = datetime.now(timezone.utc)
                    cfg = self._channel_locked(conn)
                    for row in open_rows:
                        rec_alert = self._alert_from_row(row)
                        conn.execute(
                            text(
                                "UPDATE monitoring_alerts SET status = 'resolved', "
                                "last_seen = :last_seen, last_run_id = :last_run_id "
                                "WHERE id = :id AND tenant_id = :tenant_id"
                            ),
                            {
                                "last_seen": record.finished_at,
                                "last_run_id": record.id,
                                "id": rec_alert.id,
                                "tenant_id": self._tenant_id,
                            },
                        )
                        rec_alert.status = "resolved"
                        rec_alert.last_seen = record.finished_at
                        rec_alert.last_run_id = record.id
                        self._recovery_cooldown[
                            (rec_alert.rule_id, graph_id)
                        ] = recovered_at
                        pending.append((rec_alert, cfg, "recovery"))
        for alert, cfg, transition in pending:
            self._notify_outside_lock(alert, cfg, transition=transition)
        return record

    def _channel_locked(self, conn: Any) -> AlertChannel:
        row = conn.execute(
            text(
                "SELECT enabled, channel, to_addr, secret, min_severity, updated_at "
                "FROM alert_notify_settings WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": self._tenant_id},
        ).first()
        if not row:
            return AlertChannel()
        return AlertChannel(
            enabled=row[0], channel=row[1], to=row[2], secret=row[3],
            minSeverity=row[4], updatedAt=row[5],
        )

    def _notify_outside_lock(
        self, alert: Alert, cfg: AlertChannel, *, transition: str = "new"
    ) -> None:
        if self._notifier is None:
            return
        if transition == "new":
            delivery = self._notifier.notify(alert, cfg)
        else:
            delivery = self._notifier.notify_lifecycle(
                alert, cfg, transition=transition
            )
        if delivery.lastNotifiedAt is not None:
            self.record_alert_channel_delivery(delivery)

    def set_notifier(self, notifier: object | None) -> None:
        self._notifier = notifier

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
                f"SELECT {self._RUN_LIST_COLS} FROM monitoring_runs "
                "WHERE tenant_id = :tenant_id AND graph_id = :graph_id "
                "ORDER BY finished_at DESC LIMIT 200"
            ),
            {"tenant_id": self._tenant_id, "graph_id": graph_id},
        ).all()
        return [self._run_list_from_row(r) for r in rows]

    def _raise_or_merge_locked(self, conn: Any, event: Any, record: RunRecord):
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
            # docs/55：merge 也走 lifecycle 通知（对齐内存档）；读回最新投影。
            merged_row = conn.execute(
                text(
                    f"SELECT {self._ALERT_COLS} FROM monitoring_alerts "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": row[0], "tenant_id": self._tenant_id},
            ).first()
            merged_alert = self._alert_from_row(merged_row)
            # docs/59 F-2：assignee 以 PG 列为唯一权威（新建时已写值班人），不再进程内兜底。
            return merged_alert, "merged"
        alert_id = _next_id(conn, "alt")
        assignee = self._current_assignee_locked(conn)
        conn.execute(
            text(
                "INSERT INTO monitoring_alerts "
                "(id, tenant_id, rule_id, graph_id, severity, message, status, first_seen, "
                "last_seen, last_run_id, count, rule_name, assignee) "
                "VALUES (:id, :tenant_id, :rule_id, :graph_id, :severity, :message, 'open', "
                ":first_seen, :last_seen, :last_run_id, 1, :rule_name, :assignee)"
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
                "rule_name": event.rule_name,
                "assignee": assignee,
            },
        )
        return Alert(
            id=alert_id,
            rule_id=event.rule_id,
            graph_id=record.graph_id,
            severity=event.severity,
            message=event.message,
            first_seen=record.finished_at,
            last_seen=record.finished_at,
            last_run_id=record.id,
            rule_name=event.rule_name,
            assignee=assignee,
        ), "new"

    def list_runs(self, graph_id: str | None = None, limit: int = 50) -> list[RunRecord]:
        with self._engine.connect() as conn:
            if graph_id:
                rows = conn.execute(
                    text(
                        f"SELECT {self._RUN_LIST_COLS} FROM monitoring_runs "
                        "WHERE tenant_id = :tenant_id AND graph_id = :graph_id "
                        "ORDER BY finished_at DESC LIMIT :limit"
                    ),
                    {"tenant_id": self._tenant_id, "graph_id": graph_id, "limit": limit},
                ).all()
            else:
                rows = conn.execute(
                    text(
                        f"SELECT {self._RUN_LIST_COLS} FROM monitoring_runs "
                        "WHERE tenant_id = :tenant_id ORDER BY finished_at DESC LIMIT :limit"
                    ),
                    {"tenant_id": self._tenant_id, "limit": limit},
                ).all()
        return [self._run_list_from_row(r) for r in rows]

    def get_run(self, run_id: str) -> RunRecord | None:
        """docs/33 §4：按 id 取单条运行（含 spans），供 trace 钻取；跨租户/不存在返 None。"""
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT {self._RUN_COLS} FROM monitoring_runs "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": self._tenant_id, "id": run_id},
            ).first()
        return self._run_from_row(row) if row else None

    @staticmethod
    def _run_from_row(r: Any) -> RunRecord:
        return RunRecord(
            id=r[0], graph_id=r[1], mode=r[2], status=r[3], started_at=r[4],
            finished_at=r[5], duration_ms=r[6], nodes=r[7], error=r[8],
            trace_id=r[9] or "", resolved_version=r[10], business=r[11],
            tool_calls=r[12] or [], spans=(r[13] if r[13] else None),
        )

    @staticmethod
    def _run_list_from_row(r: Any) -> RunRecord:
        # 列表/告警评估投影不含 spans（大 payload，仅 trace 端点按需取）。
        return RunRecord(
            id=r[0], graph_id=r[1], mode=r[2], status=r[3], started_at=r[4],
            finished_at=r[5], duration_ms=r[6], nodes=r[7], error=r[8],
            trace_id=r[9] or "", resolved_version=r[10], business=r[11],
            tool_calls=r[12] or [], spans=None,
        )

    _RUN_LIST_COLS = (
        "id, graph_id, mode, status, started_at, finished_at, "
        "duration_ms, nodes, error, trace_id, resolved_version, business, tool_calls"
    )

    _RUN_COLS = (
        "id, graph_id, mode, status, started_at, finished_at, "
        "duration_ms, nodes, error, trace_id, resolved_version, business, tool_calls, spans"
    )

    @staticmethod
    def _alert_from_row(r: Any) -> Alert:
        return Alert(
            id=r[0], rule_id=r[1], graph_id=r[2], severity=r[3], message=r[4],
            status=r[5], first_seen=r[6], last_seen=r[7], last_run_id=r[8], count=r[9],
            action=r[10], rule_name=r[11], escalated_at=r[12], assignee=r[13],
        )

    _ALERT_COLS = (
        "id, rule_id, graph_id, severity, message, status, first_seen, "
        "last_seen, last_run_id, count, action, rule_name, escalated_at, assignee"
    )

    def _in_recovery_cooldown(self, rule_id: str, graph_id: str, rules: RuleConfig) -> bool:
        """docs/55：自动 recovery 冷却窗内同 rule+graph 新告警抑制外部通知（进程内）。"""
        minutes = rules.recovery_cooldown_minutes
        if not minutes:
            return False
        last = self._recovery_cooldown.get((rule_id, graph_id))
        if last is None:
            return False
        return (datetime.now(timezone.utc) - last).total_seconds() < minutes * 60

    def _healthy_streak_locked(self, recent_desc: list[RunRecord]) -> int:
        """docs/55：recent 为新→旧，从头数连续健康运行条数（含本次）。"""
        from atlas.monitoring.metrics import is_healthy

        streak = 0
        for run in recent_desc:
            if is_healthy(run):
                streak += 1
            else:
                break
        return streak

    def _decorate_alerts_locked(
        self, conn: Any, rows: list[Any], rules: RuleConfig, now: str
    ) -> tuple[list[Alert], list[Alert]]:
        """docs/33 §5.2/§5.3 + docs/55：assignee PG 列优先（兜底进程内值班），
        惰性升级 warning→critical 并回写 severity/escalated_at；返回 (alerts, escalated)。"""
        alerts: list[Alert] = []
        escalated: list[Alert] = []
        for r in rows:
            alert = self._alert_from_row(r)
            upgraded = apply_escalation(alert, rules, now)
            if upgraded is not alert:
                alert.severity = upgraded.severity
                alert.escalated_at = upgraded.escalated_at
                conn.execute(
                    text(
                        "UPDATE monitoring_alerts SET severity = :severity, "
                        "escalated_at = :escalated_at WHERE id = :id AND tenant_id = :tenant_id"
                    ),
                    {
                        "severity": alert.severity,
                        "escalated_at": alert.escalated_at,
                        "id": alert.id,
                        "tenant_id": self._tenant_id,
                    },
                )
                escalated.append(alert)
            alerts.append(alert)
        return alerts, escalated

    def list_alerts(self, status: str | None = None) -> list[Alert]:
        with self._engine.begin() as conn:
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
            rules = self._rules_locked(conn)
            now = datetime.now(timezone.utc).isoformat()
            alerts, escalated = self._decorate_alerts_locked(conn, rows, rules, now)
            cfg = self._channel_locked(conn) if escalated else AlertChannel()
        for alert in escalated:
            self._notify_outside_lock(alert, cfg, transition="escalated")
        return alerts

    def get_alert(self, alert_id: str) -> Alert | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    f"SELECT {self._ALERT_COLS} FROM monitoring_alerts "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": alert_id, "tenant_id": self._tenant_id},
            ).first()
            if not row:
                return None
            rules = self._rules_locked(conn)
            now = datetime.now(timezone.utc).isoformat()
            alerts, escalated = self._decorate_alerts_locked(conn, [row], rules, now)
            cfg = self._channel_locked(conn) if escalated else AlertChannel()
        for alert in escalated:
            self._notify_outside_lock(alert, cfg, transition="escalated")
        return alerts[0]

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
                    f"SELECT {self._ALERT_COLS} FROM monitoring_alerts "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": alert_id, "tenant_id": self._tenant_id},
            ).first()
            if row is None:
                return None
            if row[5] == "resolved":
                return False
            conn.execute(
                text(
                    "UPDATE monitoring_alerts SET status = 'resolved' "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": alert_id, "tenant_id": self._tenant_id},
            )
            resolved_alert = self._alert_from_row(row)
            resolved_alert.status = "resolved"
            # docs/59 F-2：assignee 以 PG 列为唯一权威，不再进程内兜底。
            cfg = self._channel_locked(conn)
        # docs/55：手动 resolve 发 resolved lifecycle 通知（不写 recovery 冷却）
        self._notify_outside_lock(resolved_alert, cfg, transition="resolved")
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

    def get_alert_channel(self) -> AlertChannel:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT enabled, channel, to_addr, secret, min_severity, updated_at "
                    "FROM alert_notify_settings WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": self._tenant_id},
            ).first()
        if not row:
            return AlertChannel()
        return AlertChannel(
            enabled=row[0], channel=row[1], to=row[2], secret=row[3],
            minSeverity=row[4], updatedAt=row[5],
        )

    def update_alert_channel(self, raw: dict) -> AlertChannel:
        errors = validate_alert_channel(raw)
        if errors:
            raise ValueError("；".join(errors))
        channel = alert_channel_from_raw(raw)
        channel.updatedAt = _now_iso()
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO alert_notify_settings "
                    "(tenant_id, enabled, channel, to_addr, secret, min_severity, updated_at) "
                    "VALUES (:tenant_id, :enabled, :channel, :to_addr, :secret, "
                    ":min_severity, :updated_at) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET enabled = EXCLUDED.enabled, "
                    "channel = EXCLUDED.channel, to_addr = EXCLUDED.to_addr, "
                    "secret = EXCLUDED.secret, min_severity = EXCLUDED.min_severity, "
                    "updated_at = EXCLUDED.updated_at"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "enabled": channel.enabled,
                    "channel": channel.channel,
                    "to_addr": channel.to,
                    "secret": channel.secret,
                    "min_severity": channel.minSeverity,
                    "updated_at": channel.updatedAt,
                },
            )
        return channel.model_copy(deep=True)

    def get_alert_channel_delivery(self) -> AlertChannelDelivery:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT last_notified_at, last_error_code, last_error_message "
                    "FROM alert_notify_settings WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": self._tenant_id},
            ).first()
        if not row:
            return AlertChannelDelivery()
        return AlertChannelDelivery(
            lastNotifiedAt=row[0], errorCode=row[1], errorMessage=row[2],
        )

    def record_alert_channel_delivery(self, delivery: AlertChannelDelivery) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO alert_notify_settings (tenant_id, last_notified_at, "
                    "last_error_code, last_error_message) "
                    "VALUES (:tenant_id, :last_notified_at, :last_error_code, "
                    ":last_error_message) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET "
                    "last_notified_at = EXCLUDED.last_notified_at, "
                    "last_error_code = EXCLUDED.last_error_code, "
                    "last_error_message = EXCLUDED.last_error_message"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "last_notified_at": delivery.lastNotifiedAt,
                    "last_error_code": delivery.errorCode,
                    "last_error_message": delivery.errorMessage,
                },
            )

    def raise_rollout_gate_alert(
        self, *, graph_id: str, message: str, action: dict, last_run_id: str = "",
    ) -> Alert:
        now = _now_iso()
        created = False
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT id FROM monitoring_alerts WHERE tenant_id = :tenant_id "
                    "AND rule_id = 'rollout_gate' AND graph_id = :graph_id "
                    "AND status != 'resolved' ORDER BY first_seen DESC LIMIT 1"
                ),
                {"tenant_id": self._tenant_id, "graph_id": graph_id},
            ).first()
            if row is not None:
                conn.execute(
                    text(
                        "UPDATE monitoring_alerts SET count = count + 1, last_seen = :now, "
                        "last_run_id = :last_run_id WHERE id = :id"
                    ),
                    {"now": now, "last_run_id": last_run_id, "id": row[0]},
                )
                alert_id = row[0]
            else:
                alert_id = _next_id(conn, "alt")
                conn.execute(
                    text(
                        "INSERT INTO monitoring_alerts "
                        "(id, tenant_id, rule_id, graph_id, severity, message, status, "
                        "first_seen, last_seen, last_run_id, count, action) "
                        "VALUES (:id, :tenant_id, 'rollout_gate', :graph_id, 'critical', "
                        ":message, 'open', :now, :now, :last_run_id, 1, :action)"
                    ),
                    {
                        "id": alert_id, "tenant_id": self._tenant_id,
                        "graph_id": graph_id, "message": message, "now": now,
                        "last_run_id": last_run_id,
                        "action": json.dumps(action, ensure_ascii=False),
                    },
                )
                # docs/59 F-2：rollout_gate 门禁告警不指派值班人，assignee 保持 NULL。
                created = True
            if created:
                cfg = self._channel_locked(conn)
        if created:
            alerted = self.get_alert(alert_id)
            if alerted is not None:
                self._notify_outside_lock(alerted, cfg)
        alert = self.get_alert(alert_id)
        return alert if alert is not None else Alert(
            id=alert_id, rule_id="rollout_gate", graph_id=graph_id, severity="critical",
            message=message, first_seen=now, last_seen=now, last_run_id=last_run_id,
            action=action,
        )

    # docs/59 F-2：静默 / 值班 / assignee（PG 持久化；命中/轮换判定复用 silences 纯函数）

    @staticmethod
    def _silence_from_row(r: Any) -> Silence:
        return Silence(
            id=r[0], rule_id=r[1], graph_id=r[2], reason=r[3], created_by=r[4],
            created_at=r[5], expires_at=r[6], suppressed_count=r[7] or 0,
        )

    _SILENCE_COLS = (
        "id, rule_id, graph_id, reason, created_by, created_at, expires_at, suppressed_count"
    )

    def create_silence(
        self, *, rule_id: str | None, graph_id: str | None, duration_minutes: int,
        reason: str, created_by: str,
    ) -> Silence:
        with self._engine.begin() as conn:
            now = _now_iso()
            # 惰性清过期（对齐内存档 _purge_expired_locked）
            conn.execute(
                text(
                    "DELETE FROM monitoring_silences "
                    "WHERE tenant_id = :tenant_id AND expires_at <= :now"
                ),
                {"tenant_id": self._tenant_id, "now": now},
            )
            silence_id = _next_id(conn, "sil")
            expires = (
                datetime.fromisoformat(now) + timedelta(minutes=duration_minutes)
            ).isoformat()
            conn.execute(
                text(
                    "INSERT INTO monitoring_silences "
                    "(id, tenant_id, rule_id, graph_id, reason, created_by, "
                    "created_at, expires_at, suppressed_count) "
                    "VALUES (:id, :tenant_id, :rule_id, :graph_id, :reason, :created_by, "
                    ":created_at, :expires_at, 0)"
                ),
                {
                    "id": silence_id, "tenant_id": self._tenant_id,
                    "rule_id": rule_id, "graph_id": graph_id, "reason": reason,
                    "created_by": created_by, "created_at": now, "expires_at": expires,
                },
            )
            # cap 100：惰性清理后仍超额则淘汰最旧（保留最新 100 条）
            conn.execute(
                text(
                    "DELETE FROM monitoring_silences WHERE tenant_id = :tenant_id AND id IN "
                    "(SELECT id FROM monitoring_silences WHERE tenant_id = :tenant_id "
                    "ORDER BY created_at ASC, id ASC LIMIT GREATEST("
                    "(SELECT COUNT(*) FROM monitoring_silences WHERE tenant_id = :tenant_id) "
                    "- 100, 0))"
                ),
                {"tenant_id": self._tenant_id},
            )
            row = conn.execute(
                text(
                    f"SELECT {self._SILENCE_COLS} FROM monitoring_silences "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": self._tenant_id, "id": silence_id},
            ).first()
        return self._silence_from_row(row)

    def _suppress_if_matched_locked(
        self, conn: Any, rule_id: str, graph_id: str, now: str
    ) -> bool:
        """告警产生路径（在外层事务内）：命中首条活跃静默则 suppressed_count +1 并压下。"""
        rows = conn.execute(
            text(
                f"SELECT {self._SILENCE_COLS} FROM monitoring_silences "
                "WHERE tenant_id = :tenant_id AND expires_at > :now "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"tenant_id": self._tenant_id, "now": now},
        ).all()
        for r in rows:
            silence = self._silence_from_row(r)
            if silence_matches(silence, rule_id, graph_id, now):
                conn.execute(
                    text(
                        "UPDATE monitoring_silences SET suppressed_count = suppressed_count + 1 "
                        "WHERE tenant_id = :tenant_id AND id = :id"
                    ),
                    {"tenant_id": self._tenant_id, "id": silence.id},
                )
                return True
        return False

    def list_silences(self, active: bool | None = None) -> list[Silence]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT {self._SILENCE_COLS} FROM monitoring_silences "
                    "WHERE tenant_id = :tenant_id ORDER BY created_at ASC, id ASC"
                ),
                {"tenant_id": self._tenant_id},
            ).all()
        items = [self._silence_from_row(r) for r in rows]
        if active is None:
            return items
        now = _now_iso()
        return [s for s in items if is_silence_active(s, now) is active]

    def delete_silence(self, silence_id: str) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM monitoring_silences "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": self._tenant_id, "id": silence_id},
            )
            return result.rowcount > 0

    @staticmethod
    def _oncall_from_row(r: Any | None) -> OnCallSchedule:
        if r is None:
            return OnCallSchedule()
        members = r[1] if isinstance(r[1], list) else list(r[1] or [])
        return OnCallSchedule(
            members=members,
            index=r[2] or 0,
            updated_at=r[3],
            updated_by=r[4],
            rotation_interval_days=r[5],
            last_rotated_at=r[6],
        )

    def _oncall_row_locked(self, conn: Any) -> Any | None:
        return conn.execute(
            text(
                "SELECT tenant_id, members, rot_index, updated_at, updated_by, "
                "rotation_interval_days, last_rotated_at "
                "FROM monitoring_oncall WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": self._tenant_id},
        ).first()

    def _auto_rotate_oncall_locked(self, conn: Any, now_dt: datetime) -> OnCallSchedule | None:
        """docs/60 §4.2：读路径惰性按日轮换，推进则 UPDATE index/last_rotated_at。"""
        schedule = self._oncall_from_row(self._oncall_row_locked(conn))
        rotated = maybe_auto_rotate(schedule, now_dt)
        if rotated is not None:
            conn.execute(
                text(
                    "UPDATE monitoring_oncall SET rot_index = :rot_index, "
                    "last_rotated_at = :last_rotated_at WHERE tenant_id = :tenant_id"
                ),
                {
                    "rot_index": rotated.index,
                    "last_rotated_at": rotated.last_rotated_at,
                    "tenant_id": self._tenant_id,
                },
            )
        return rotated

    def get_oncall(self) -> OnCallSchedule:
        now_dt = datetime.now(timezone.utc)
        with self._engine.begin() as conn:
            self._auto_rotate_oncall_locked(conn, now_dt)
            row = self._oncall_row_locked(conn)
        return self._oncall_from_row(row)

    def _current_assignee_locked(self, conn: Any) -> str | None:
        # 告警产生路径（外层写事务内）同样惰性轮换
        self._auto_rotate_oncall_locked(conn, datetime.now(timezone.utc))
        return current_assignee(self._oncall_from_row(self._oncall_row_locked(conn)))

    def set_oncall(
        self, *, members: list[str], updated_by: str,
        rotation_interval_days: int | None = None,
    ) -> OnCallSchedule:
        # 去重保序、丢弃空白串、重置 index=0（与 OpsStore.set_oncall 同一规整逻辑）
        deduped = list(dict.fromkeys(m.strip() for m in members if m.strip()))
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        interval = rotation_interval_days if (
            isinstance(rotation_interval_days, int)
            and not isinstance(rotation_interval_days, bool)
            and 1 <= rotation_interval_days <= 365
        ) else None
        last_rotated = now_dt.date().isoformat() if interval is not None else None
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO monitoring_oncall "
                    "(tenant_id, members, rot_index, updated_at, updated_by, "
                    "rotation_interval_days, last_rotated_at) "
                    "VALUES (:tenant_id, :members, 0, :updated_at, :updated_by, "
                    ":interval, :last_rotated) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET "
                    "members = EXCLUDED.members, rot_index = 0, "
                    "updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by, "
                    "rotation_interval_days = EXCLUDED.rotation_interval_days, "
                    "last_rotated_at = EXCLUDED.last_rotated_at"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "members": json.dumps(deduped, ensure_ascii=False),
                    "updated_at": now,
                    "updated_by": updated_by,
                    "interval": interval,
                    "last_rotated": last_rotated,
                },
            )
            row = self._oncall_row_locked(conn)
        return self._oncall_from_row(row)

    def rotate_oncall(self, *, updated_by: str) -> OnCallSchedule:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self._engine.begin() as conn:
            schedule = self._oncall_from_row(self._oncall_row_locked(conn))
            total = len(schedule.members)
            if total == 0:
                raise OnCallEmpty("值班表为空，无法轮换")
            new_index = (schedule.index + 1) % total
            # docs/60 §4.2：手动轮换同步刷新按日轮换基准（仅开启自动间隔时）
            last_rotated = (
                now_dt.date().isoformat()
                if schedule.rotation_interval_days is not None
                else schedule.last_rotated_at
            )
            conn.execute(
                text(
                    "UPDATE monitoring_oncall SET rot_index = :rot_index, "
                    "updated_at = :updated_at, updated_by = :updated_by, "
                    "last_rotated_at = :last_rotated_at "
                    "WHERE tenant_id = :tenant_id"
                ),
                {
                    "rot_index": new_index, "updated_at": now,
                    "updated_by": updated_by, "last_rotated_at": last_rotated,
                    "tenant_id": self._tenant_id,
                },
            )
            row = self._oncall_row_locked(conn)
        return self._oncall_from_row(row)

    def update_silence(
        self, silence_id: str, *,
        reason: object = _UNSET, rule_id: object = _UNSET,
        graph_id: object = _UNSET, expires_at: object = _UNSET,
    ) -> Silence | None:
        """docs/60 §4.1：仅命中未过期静默可改（UPDATE ... expires_at > now）；否则 None。"""
        now = _now_iso()
        provided = {
            key: val
            for key, val in (
                ("reason", reason),
                ("rule_id", rule_id),
                ("graph_id", graph_id),
                ("expires_at", expires_at),
            )
            if val is not _UNSET
        }
        with self._engine.begin() as conn:
            if provided:
                assignments = ", ".join(f"{key} = :{key}" for key in provided)
                params = {
                    "tenant_id": self._tenant_id, "id": silence_id, "now": now, **provided
                }
                result = conn.execute(
                    text(
                        f"UPDATE monitoring_silences SET {assignments} "
                        "WHERE tenant_id = :tenant_id AND id = :id AND expires_at > :now"
                    ),
                    params,
                )
                if result.rowcount == 0:
                    return None
            row = conn.execute(
                text(
                    f"SELECT {self._SILENCE_COLS} FROM monitoring_silences "
                    "WHERE tenant_id = :tenant_id AND id = :id AND expires_at > :now"
                ),
                {"tenant_id": self._tenant_id, "id": silence_id, "now": now},
            ).first()
        return self._silence_from_row(row) if row is not None else None

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
            conn.execute(
                text("DELETE FROM alert_notify_settings WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )
            conn.execute(
                text("DELETE FROM monitoring_silences WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )
            conn.execute(
                text("DELETE FROM monitoring_oncall WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )
        self._recovery_cooldown.clear()


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


class PgMemoryStore:
    """长期记忆 PG/pgvector 实现（M11，docs/26 §4.3）。

    embedding 由 Python 端 LocalDeterministicEmbedder 计算后写 vector 列（两档同一
    provider/同一 dim，保证对拍一致；pgvector 只负责存与余弦距离）。scope 子集匹配用
    JSONB @>；行内 tenant_id 过滤（不用 RLS）；不返回 embedding。
    """

    def __init__(self, engine: Engine, tenant_id: str, provider: Any | None = None):
        self._engine = engine
        self._tenant_id = tenant_id
        if provider is None:
            from atlas.memory.embeddings import get_embedding_provider

            provider = get_embedding_provider()
        self._provider = provider

    @staticmethod
    def _vector_literal(vector: list[float]) -> str:
        return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"

    @staticmethod
    def _row_to_public(row: Any) -> dict[str, Any]:
        return {
            "id": row[0],
            "kind": row[1],
            "content": row[2],
            "scope": row[3] or {},
            "confidence": float(row[4]),
            "source": row[5],
            "metadata": row[6] or {},
            "created_at": row[7],
        }

    _PUBLIC_COLS = (
        "id, kind, content, scope, confidence, source, meta, created_at"
    )

    def remember(
        self,
        *,
        kind: str,
        content: str,
        scope: dict[str, str] | None = None,
        confidence: float = 1.0,
        source: str = "tool",
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from atlas.memory.models import validate_remember_params

        params = validate_remember_params(
            kind=kind, content=content, scope=scope, confidence=confidence,
            source=source, metadata=metadata,
        )
        vector = self._provider.embed([params["content"]])[0]
        with self._engine.begin() as conn:
            memory_id = _next_id(conn, "mem")
            created_at = _now_iso()
            conn.execute(
                text(
                    "INSERT INTO memory_items (id, tenant_id, kind, content, scope, embedding, "
                    "confidence, source, meta, created_at) VALUES (:id, :tenant_id, :kind, :content, "
                    "CAST(:scope AS jsonb), CAST(:embedding AS vector(256)), :confidence, :source, CAST(:meta AS jsonb), :created_at)"
                ),
                {
                    "id": memory_id,
                    "tenant_id": self._tenant_id,
                    "kind": params["kind"],
                    "content": params["content"],
                    "scope": json.dumps(params["scope"], ensure_ascii=False),
                    "embedding": self._vector_literal(vector),
                    "confidence": params["confidence"],
                    "source": params["source"],
                    "meta": json.dumps(params["metadata"], ensure_ascii=False),
                    "created_at": created_at,
                },
            )
        return {
            "id": memory_id,
            "kind": params["kind"],
            "content": params["content"],
            "scope": params["scope"],
            "confidence": params["confidence"],
            "source": params["source"],
            "metadata": params["metadata"],
            "created_at": created_at,
        }

    def recall(
        self,
        query: str,
        *,
        kind: str | None = None,
        scope: dict[str, str] | None = None,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        from atlas.memory.models import validate_recall_params

        params = validate_recall_params(
            query=query, kind=kind, scope=scope, top_k=top_k, min_score=min_score
        )
        query_vector = self._provider.embed([params["query"]])[0]
        clauses = ["tenant_id = :tenant_id", "scope @> CAST(:scope AS jsonb)"]
        args: dict[str, Any] = {
            "tenant_id": self._tenant_id,
            "scope": json.dumps(params["scope"], ensure_ascii=False),
            "q": self._vector_literal(query_vector),
            "top_k": params["top_k"],
            "min_score": params["min_score"],
        }
        if params["kind"] is not None:
            clauses.append("kind = :kind")
            args["kind"] = params["kind"]
        where = " AND ".join(clauses)
        sql = (
            f"SELECT {self._PUBLIC_COLS}, 1.0 - (embedding <=> CAST(:q AS vector(256))) AS score "
            f"FROM memory_items WHERE {where} "
            "AND (1.0 - (embedding <=> CAST(:q AS vector(256)))) >= :min_score "
            "ORDER BY embedding <=> CAST(:q AS vector(256)) LIMIT :top_k"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), args).all()
        results: list[dict[str, Any]] = []
        for row in rows:
            public = self._row_to_public(row)
            public["score"] = round(float(row[8]), 6)
            results.append(public)
        return results

    def list(self, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit 必须是正整数")
        if kind is not None and kind not in ("fact", "preference"):
            raise ValueError("kind 必须是 fact 或 preference")
        sql = (
            f"SELECT {self._PUBLIC_COLS} FROM memory_items WHERE tenant_id = :tenant_id "
            "{kind_clause} ORDER BY created_at DESC LIMIT :limit"
        )
        args: dict[str, Any] = {"tenant_id": self._tenant_id, "limit": limit}
        kind_clause = ""
        if kind is not None:
            kind_clause = "AND kind = :kind"
            args["kind"] = kind
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql.format(kind_clause=kind_clause)), args).all()
        return [self._row_to_public(row) for row in rows]

    def update(self, memory_id: str, **fields: Any) -> dict[str, Any] | None:
        """手动编辑白名单字段（docs/28 §5.1）；SELECT 旧行→合并校验→content 变重算
        embedding→动态 UPDATE；不存在返回 None，id/created_at 不变，source 置 manual。
        """
        from atlas.memory.models import merge_manual_update

        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    f"SELECT {self._PUBLIC_COLS} FROM memory_items "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": memory_id, "tenant_id": self._tenant_id},
            ).first()
            if row is None:
                return None
            old = self._row_to_public(row)
            params, content_changed = merge_manual_update(old, fields)
            sets = (
                "kind = :kind, content = :content, scope = CAST(:scope AS jsonb), "
                "confidence = :confidence, source = 'manual', meta = CAST(:meta AS jsonb)"
            )
            args: dict[str, Any] = {
                "id": memory_id,
                "tenant_id": self._tenant_id,
                "kind": params["kind"],
                "content": params["content"],
                "scope": json.dumps(params["scope"], ensure_ascii=False),
                "confidence": params["confidence"],
                "meta": json.dumps(params["metadata"], ensure_ascii=False),
            }
            if content_changed:
                vector = self._provider.embed([params["content"]])[0]
                sets += ", embedding = CAST(:embedding AS vector(256))"
                args["embedding"] = self._vector_literal(vector)
            conn.execute(
                text(
                    f"UPDATE memory_items SET {sets} "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                args,
            )
        return {
            "id": old["id"],
            "kind": params["kind"],
            "content": params["content"],
            "scope": params["scope"],
            "confidence": params["confidence"],
            "source": "manual",
            "metadata": params["metadata"],
            "created_at": old["created_at"],
        }

    def delete(self, memory_id: str) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM memory_items WHERE id = :id AND tenant_id = :tenant_id"),
                {"id": memory_id, "tenant_id": self._tenant_id},
            )
            return result.rowcount == 1

    def clear(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM memory_items WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )

class PgAuditStore:
    """审计事件 PG 实现（docs/35 §6，T6）。

    仅写操作元数据；id 用全局 storage_id_seq（aud-N），seq 存数字部分供同租户排序。
    行内 tenant_id 过滤（不用 RLS）；demo reset 不清理本表。
    """

    def __init__(self, engine: Engine, tenant_id: str):
        self._engine = engine
        self._tenant_id = tenant_id

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row[0],
            "tenantId": row[1],
            "actor": row[2],
            "action": row[3],
            "statusCode": int(row[4]),
            "path": row[5],
            "ip": row[6] or "",
            "at": row[7],
            # docs/61 §4.1：游标分页用 seq（本表早有该列），与内存档新字段同形。
            "seq": int(row[8]),
        }

    _COLS = "id, tenant_id, actor, action, status_code, path, ip, at, seq"

    def record(
        self,
        *,
        tenant_id: str,
        actor: str,
        action: str,
        status_code: int,
        path: str,
        ip: str,
    ) -> dict[str, Any]:
        from atlas.observability.audit import now_iso

        with self._engine.begin() as conn:
            seq = int(conn.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            audit_id = f"aud-{seq}"
            at = now_iso()
            conn.execute(
                text(
                    "INSERT INTO audit_events (id, tenant_id, seq, actor, action, status_code, path, ip, at) "
                    "VALUES (:id, :tenant_id, :seq, :actor, :action, :status_code, :path, :ip, :at)"
                ),
                {
                    "id": audit_id,
                    "tenant_id": tenant_id,
                    "seq": seq,
                    "actor": actor or "anonymous",
                    "action": action,
                    "status_code": int(status_code),
                    "path": path,
                    "ip": ip or "",
                    "at": at,
                },
            )
        return {
            "id": audit_id,
            "tenantId": tenant_id,
            "actor": actor or "anonymous",
            "action": action,
            "statusCode": int(status_code),
            "path": path,
            "ip": ip or "",
            "at": at,
        }

    @staticmethod
    def _escape_prefix(prefix: str) -> str:
        return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _where(
        self,
        args: dict[str, Any],
        action_prefix: str | None,
        *,
        actor: str | None = None,
        since: str | None = None,
        until: str | None = None,
        cursor: int | None = None,
    ) -> str:
        from atlas.observability.audit import parse_bound

        clauses = ["tenant_id = :tenant_id"]
        if action_prefix:
            clauses.append("action LIKE :prefix ESCAPE '\\'")
            args["prefix"] = self._escape_prefix(action_prefix) + "%"
        if actor:
            clauses.append("actor = :actor")
            args["actor"] = actor
        if cursor is not None:
            clauses.append("seq < :cursor")
            args["cursor"] = int(cursor)
        # 时刻比较走 timestamptz 语义（列是 TEXT，但值一律由 now_iso() 写入）；
        # 绑参必须写 CAST(:x AS timestamptz)，:x::timestamptz 会被 SQLAlchemy 当参数名吃掉。
        if since:
            clauses.append("at::timestamptz >= CAST(:since AS timestamptz)")
            args["since"] = parse_bound(since).isoformat()
        if until:
            clauses.append("at::timestamptz <= CAST(:until AS timestamptz)")
            args["until"] = parse_bound(until).isoformat()
        return " AND ".join(clauses)

    def list(  # noqa: C901 - 五个可选过滤拼进同一 _where
        self,
        *,
        limit: int = 100,
        action_prefix: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        until: str | None = None,
        cursor: int | None = None,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        args: dict[str, Any] = {"tenant_id": self._tenant_id, "limit": bounded}
        where = self._where(
            args, action_prefix, actor=actor, since=since, until=until, cursor=cursor
        )
        sql = f"SELECT {self._COLS} FROM audit_events WHERE {where} ORDER BY seq DESC LIMIT :limit"
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), args).all()
        return [self._row_to_dict(row) for row in rows]

    def export_jsonl(
        self,
        *,
        action_prefix: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> str:
        args: dict[str, Any] = {"tenant_id": self._tenant_id}
        where = self._where(args, action_prefix, actor=actor, since=since, until=until)
        sql = f"SELECT {self._COLS} FROM audit_events WHERE {where} ORDER BY seq ASC"
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), args).all()
        return "\n".join(
            json.dumps(self._row_to_dict(row), ensure_ascii=False) for row in rows
        )

    def clear(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM audit_events WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )


class PgConnectionStore:
    """OAuth2 连接 PG 实现（docs/35 §4，T4；generic OAuth2，平台无关）。

    秘密字段存 SecretProvider 信封 TEXT；id 用全局 storage_id_seq（conn-N），
    seq 存数字部分供同租户排序；行内 tenant_id 过滤；demo reset 不清本表。
    """

    _COLS = (
        "id, tenant_id, seq, provider, display_name, auth_url, token_url, client_id, "
        "client_secret_envelope, scopes, redirect_uri, status, access_token_envelope, "
        "refresh_token_envelope, token_type, expires_at, last_error, created_by, "
        "created_at, updated_at"
    )

    def __init__(self, engine: Engine, tenant_id: str):
        self._engine = engine
        self._tenant_id = tenant_id

    @staticmethod
    def _row_to_conn(row: Any):
        from atlas.connections.models import Connection

        # 列序：0 id,1 tenant_id,2 seq,3 provider,4 display_name,5 auth_url,6 token_url,
        # 7 client_id,8 client_secret_envelope,9 scopes,10 redirect_uri,11 status,
        # 12 access_token_envelope,13 refresh_token_envelope,14 token_type,15 expires_at,
        # 16 last_error,17 created_by,18 created_at,19 updated_at
        try:
            scopes = json.loads(row[9]) if row[9] else []
        except (TypeError, ValueError):
            scopes = []
        return Connection(
            id=row[0],
            tenant_id=row[1],
            provider=row[3],
            display_name=row[4],
            auth_url=row[5],
            token_url=row[6],
            client_id=row[7],
            client_secret_envelope=row[8],
            scopes=scopes if isinstance(scopes, list) else [],
            redirect_uri=row[10] or "",
            status=row[11],
            access_token_envelope=row[12],
            refresh_token_envelope=row[13],
            token_type=row[14],
            expires_at=row[15],
            last_error=row[16],
            created_by=row[17],
            created_at=row[18],
            updated_at=row[19],
        )

    def create(self, conn: Any):
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            conn.id = f"conn-{seq}"
            now = _now_iso()
            conn.created_at = now
            conn.updated_at = now
            db.execute(
                text(
                    "INSERT INTO oauth_connections (id, tenant_id, seq, provider, display_name, "
                    "auth_url, token_url, client_id, client_secret_envelope, scopes, redirect_uri, "
                    "status, access_token_envelope, refresh_token_envelope, token_type, expires_at, "
                    "last_error, created_by, created_at, updated_at) VALUES (:id, :tenant_id, :seq, "
                    ":provider, :display_name, :auth_url, :token_url, :client_id, :cse, :scopes, "
                    ":redirect_uri, :status, :ate, :rte, :token_type, :expires_at, :last_error, "
                    ":created_by, :created_at, :updated_at)"
                ),
                {
                    "id": conn.id,
                    "tenant_id": self._tenant_id,
                    "seq": seq,
                    "provider": conn.provider,
                    "display_name": conn.display_name,
                    "auth_url": conn.auth_url,
                    "token_url": conn.token_url,
                    "client_id": conn.client_id,
                    "cse": conn.client_secret_envelope,
                    "scopes": json.dumps(conn.scopes, ensure_ascii=False),
                    "redirect_uri": conn.redirect_uri,
                    "status": conn.status,
                    "ate": conn.access_token_envelope,
                    "rte": conn.refresh_token_envelope,
                    "token_type": conn.token_type,
                    "expires_at": conn.expires_at,
                    "last_error": conn.last_error,
                    "created_by": conn.created_by,
                    "created_at": conn.created_at,
                    "updated_at": conn.updated_at,
                },
            )
        return conn

    def get(self, conn_id: str):
        sql = f"SELECT {self._COLS} FROM oauth_connections WHERE tenant_id = :t AND id = :id"
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "id": conn_id}).first()
        return self._row_to_conn(row) if row else None

    def list(self):
        sql = f"SELECT {self._COLS} FROM oauth_connections WHERE tenant_id = :t ORDER BY seq"
        with self._engine.connect() as db:
            rows = db.execute(text(sql), {"t": self._tenant_id}).all()
        return [self._row_to_conn(row) for row in rows]

    def save(self, conn: Any):
        conn.updated_at = _now_iso()
        with self._engine.begin() as db:
            db.execute(
                text(
                    "UPDATE oauth_connections SET provider = :provider, display_name = :display_name, "
                    "auth_url = :auth_url, token_url = :token_url, client_id = :client_id, "
                    "client_secret_envelope = :cse, scopes = :scopes, redirect_uri = :redirect_uri, "
                    "status = :status, access_token_envelope = :ate, refresh_token_envelope = :rte, "
                    "token_type = :token_type, expires_at = :expires_at, last_error = :last_error, "
                    "updated_at = :updated_at WHERE tenant_id = :t AND id = :id"
                ),
                {
                    "provider": conn.provider,
                    "display_name": conn.display_name,
                    "auth_url": conn.auth_url,
                    "token_url": conn.token_url,
                    "client_id": conn.client_id,
                    "cse": conn.client_secret_envelope,
                    "scopes": json.dumps(conn.scopes, ensure_ascii=False),
                    "redirect_uri": conn.redirect_uri,
                    "status": conn.status,
                    "ate": conn.access_token_envelope,
                    "rte": conn.refresh_token_envelope,
                    "token_type": conn.token_type,
                    "expires_at": conn.expires_at,
                    "last_error": conn.last_error,
                    "updated_at": conn.updated_at,
                    "t": self._tenant_id,
                    "id": conn.id,
                },
            )
        return conn

    def delete(self, conn_id: str) -> bool:
        with self._engine.begin() as db:
            result = db.execute(
                text("DELETE FROM oauth_connections WHERE tenant_id = :t AND id = :id"),
                {"t": self._tenant_id, "id": conn_id},
            )
            return bool(result.rowcount)

    def reset(self) -> None:
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM oauth_connections WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )
