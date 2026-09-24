"""已决审批历史存储抽象（docs/61 §3，打包 H H2；D20 部分取回、不解除）。

只接管「已决」这一段历史：pending 挂起与中断帧仍走
`storage/recovery.py` + `interruptions` 表，本模块不参与。

- `InMemoryApprovalHistoryStore`：deque(maxlen=200) ring，reset/重启清空（demo/测试默认）；
- `PgApprovalHistoryStore`：迁移 027 建表，跨重启/跨实例可见，record 后惰性裁到最近 200 行。

时间列统一 UTC ISO-8601 TEXT（对齐 002/024 主约定，不采 025 `sent_at TIMESTAMPTZ` 例外）：
`_Pending` 内存态仍是 `time.time()` float epoch，仅在写历史/出投影时经 `epoch_to_iso` 转换。
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import Engine, text

HISTORY_RING_SIZE = 200

_COLS = (
    "token, node_id, graph_id, summary, approver, decision, resolved_by, "
    "comment, card_template_id, created_at, resolved_at"
)


def epoch_to_iso(ts: object) -> str:
    """`time.time()` 秒 → UTC ISO-8601；非法/缺失时刻回落 epoch 零点而非抛。"""
    try:
        seconds = float(ts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds != seconds or seconds in (float("inf"), float("-inf")) or seconds < 0:
        seconds = 0.0
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


@dataclass
class ApprovalHistoryEntry:
    """一条已决审批（不含 card_context/notify_recipients/action_id——沿用 docs/37 不外泄口径）。"""

    token: str
    node_id: str
    graph_id: str
    summary: str
    approver: str
    decision: str
    resolved_by: str
    comment: str
    created_at: str
    resolved_at: str
    card_template_id: str | None = None

    def projection(self) -> dict[str, Any]:
        """REST 投影；两档 store 出同一形状，`createdAt` 为 ISO 串（docs/61 §3.3）。"""
        result: dict[str, Any] = {
            "token": self.token,
            "node_id": self.node_id,
            "graph_id": self.graph_id,
            "summary": self.summary,
            "approver": self.approver,
            "createdAt": self.created_at,
            "resolvedAt": self.resolved_at,
            "decision": self.decision,
            "resolvedBy": self.resolved_by,
            "comment": self.comment,
        }
        if self.card_template_id:
            result["cardTemplateId"] = self.card_template_id
        return result


class ApprovalHistoryStore(Protocol):
    """历史存储：record 追加、list 倒序投影、clear 本租户清空。"""

    def record(self, entry: ApprovalHistoryEntry) -> None: ...

    def list(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def clear(self) -> None: ...


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), HISTORY_RING_SIZE))


class InMemoryApprovalHistoryStore:
    """进程内 ring（deque maxlen=200），倒序、clamp 1-200。"""

    def __init__(self) -> None:
        self._items: deque[ApprovalHistoryEntry] = deque(maxlen=HISTORY_RING_SIZE)
        self._lock = threading.Lock()

    def record(self, entry: ApprovalHistoryEntry) -> None:
        with self._lock:
            self._items.append(entry)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = _clamp_limit(limit)
        with self._lock:
            recent = list(self._items)[-bounded:]
        return [entry.projection() for entry in reversed(recent)]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class PgApprovalHistoryStore:
    """PG 实现，持有 engine 与 tenant_id（与其他 Pg*Store 同形）。"""

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def record(self, entry: ApprovalHistoryEntry) -> None:
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            db.execute(
                text(
                    "INSERT INTO approval_history (tenant_id, token, seq, node_id, graph_id, "
                    "summary, approver, decision, resolved_by, comment, card_template_id, "
                    "created_at, resolved_at) VALUES (:tenant_id, :token, :seq, :node_id, "
                    ":graph_id, :summary, :approver, :decision, :resolved_by, :comment, "
                    ":card_template_id, :created_at, :resolved_at) "
                    "ON CONFLICT (tenant_id, token) DO UPDATE SET "
                    "decision = EXCLUDED.decision, resolved_by = EXCLUDED.resolved_by, "
                    "comment = EXCLUDED.comment, resolved_at = EXCLUDED.resolved_at, "
                    "seq = EXCLUDED.seq"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "token": entry.token,
                    "seq": seq,
                    "node_id": entry.node_id,
                    "graph_id": entry.graph_id,
                    "summary": entry.summary,
                    "approver": entry.approver,
                    "decision": entry.decision,
                    "resolved_by": entry.resolved_by,
                    "comment": entry.comment,
                    "card_template_id": entry.card_template_id,
                    "created_at": entry.created_at,
                    "resolved_at": entry.resolved_at,
                },
            )
            # 惰性 ring：删除本租户超出最近 200 行的旧行（与内存 deque maxlen 对齐）。
            db.execute(
                text(
                    "DELETE FROM approval_history WHERE tenant_id = :t AND seq IN ("
                    "SELECT seq FROM approval_history WHERE tenant_id = :t "
                    "ORDER BY seq DESC OFFSET :keep)"
                ),
                {"t": self._tenant_id, "keep": HISTORY_RING_SIZE},
            )

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = _clamp_limit(limit)
        sql = (
            f"SELECT {_COLS} FROM approval_history "
            "WHERE tenant_id = :t ORDER BY seq DESC LIMIT :limit"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), {"t": self._tenant_id, "limit": bounded}).all()

        def _project(row: Any) -> dict[str, Any]:
            result: dict[str, Any] = {
                "token": row[0],
                "node_id": row[1],
                "graph_id": row[2],
                "summary": row[3],
                "approver": row[4],
                "createdAt": row[9],
                "resolvedAt": row[10],
                "decision": row[5],
                "resolvedBy": row[6],
                "comment": row[7],
            }
            if row[8]:
                result["cardTemplateId"] = row[8]
            return result

        return [_project(row) for row in rows]

    def clear(self) -> None:
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM approval_history WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )
