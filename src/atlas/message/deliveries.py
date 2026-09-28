"""消息投递日志存储抽象（docs/60 §6）。

docs/24 §1.1 原将投递记录视为「服务非存储」，本批演进为：投递记录是可持久化
数据，抽 ``DeliveryStore``（存储），``MessageService`` 仍为服务。

- ``InMemoryDeliveryStore``：deque(maxlen=200) ring，重启/reset 清空（demo/测试默认）；
- ``PgDeliveryStore``：迁移 025 建表，跨重启/跨实例可见，INSERT 后惰性裁到最近 200 条。

注意（落码偏差，收口注记）：群发时一条消息逐目标产生多条 DeliveryRecord，它们共享
同一消息 ``id``（message uuid），故行唯一键不能用 (tenant_id, id)，改用全局单调
``seq``（nextval('storage_id_seq')）作 PRIMARY KEY，``id`` 作为普通列保留、可重复。
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import Engine, text

if TYPE_CHECKING:  # 避免与 service.py 形成运行时环
    from .service import DeliveryRecord

# ring 容量（原 service.DELIVERY_RING_SIZE，落码迁至存储层；service 再 re-import 兼容）
DELIVERY_RING_SIZE = 200


class DeliveryStore(Protocol):
    """投递日志存储：record 追加、list 倒序投影、按 seq 取行、clear 租户清空。"""

    def record(self, rec: DeliveryRecord) -> None: ...

    def list(self, limit: int, *, status: str | None = None) -> list[dict[str, Any]]: ...

    def get(self, seq: int) -> dict[str, Any] | None: ...

    def clear(self) -> None: ...


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), DELIVERY_RING_SIZE))


def _matches_status(row_status: str, status: str | None) -> bool:
    if status is None:
        return True
    if status == "failed":
        return row_status == "failed"
    if status == "delivered":
        return row_status.startswith("delivered:")
    raise ValueError(f"unsupported delivery status filter: {status!r}")


def _project(seq: int, rec: DeliveryRecord) -> dict[str, Any]:
    data = asdict(rec)
    data["seq"] = seq
    return data


class InMemoryDeliveryStore:
    """进程内 ring（deque maxlen=200），倒序、clamp 1-200，与旧 service 语义一致。

    打包 U：deque 内保留 (seq, record)——seq 为实例级单调计数（从 1 起），与 PG
    的全局 storage_id_seq 同职责；ring 淘汰旧行后计数不回退。
    """

    def __init__(self) -> None:
        self._items: deque[tuple[int, DeliveryRecord]] = deque(maxlen=DELIVERY_RING_SIZE)
        self._next_seq = 0

    def record(self, rec: DeliveryRecord) -> None:
        self._next_seq += 1
        self._items.append((self._next_seq, rec))

    def list(self, limit: int, *, status: str | None = None) -> list[dict[str, Any]]:
        bounded = _clamp_limit(limit)
        if status is not None and status not in ("failed", "delivered"):
            raise ValueError(f"unsupported delivery status filter: {status!r}")
        matched = [
            (seq, item) for seq, item in self._items if _matches_status(item.status, status)
        ]
        recent = matched[-bounded:]
        return [_project(seq, item) for seq, item in reversed(recent)]

    def get(self, seq: int) -> dict[str, Any] | None:
        for item_seq, rec in self._items:
            if item_seq == seq:
                return _project(item_seq, rec)
        return None

    def clear(self) -> None:
        self._items.clear()


def _parse_iso(value: str) -> datetime:
    text_value = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text_value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class PgDeliveryStore:
    """投递日志 PG 实现，持有租户 id 与同一连接工厂（与其他 Pg*Store 同形）。"""

    _COLS = (
        "id, seq, channel, to_targets, subject, status, attempts, "
        "elapsed_ms, error_code, error_message, sent_at, body"
    )

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def record(self, rec: DeliveryRecord) -> None:
        sent_at = _parse_iso(rec.sentAt)
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            db.execute(
                text(
                    "INSERT INTO message_deliveries (tenant_id, id, seq, channel, to_targets, "
                    "subject, status, attempts, elapsed_ms, error_code, error_message, sent_at, body) "
                    "VALUES (:tenant_id, :id, :seq, :channel, :to_targets, :subject, :status, "
                    ":attempts, :elapsed_ms, :error_code, :error_message, :sent_at, :body)"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "id": rec.id,
                    "seq": seq,
                    "channel": rec.channel,
                    "to_targets": json.dumps(list(rec.to), ensure_ascii=False),
                    "subject": rec.subject,
                    "status": rec.status,
                    "attempts": int(rec.attempts),
                    "elapsed_ms": int(rec.elapsedMs),
                    "error_code": rec.errorCode,
                    "error_message": rec.errorMessage,
                    "sent_at": sent_at,
                    "body": rec.body,
                },
            )
            # 惰性 ring：删除本租户超出最近 200 条的旧行（与内存 deque maxlen 对齐）。
            db.execute(
                text(
                    "DELETE FROM message_deliveries WHERE tenant_id = :t AND seq IN ("
                    "SELECT seq FROM message_deliveries WHERE tenant_id = :t "
                    "ORDER BY seq DESC OFFSET :keep)"
                ),
                {"t": self._tenant_id, "keep": DELIVERY_RING_SIZE},
            )

    def list(self, limit: int, *, status: str | None = None) -> list[dict[str, Any]]:
        bounded = _clamp_limit(limit)
        where_extra = ""
        params: dict[str, Any] = {"t": self._tenant_id, "limit": bounded}
        if status == "failed":
            where_extra = " AND status = 'failed'"
        elif status == "delivered":
            where_extra = " AND status LIKE 'delivered:%'"
        elif status is not None:
            raise ValueError(f"unsupported delivery status filter: {status!r}")
        sql = (
            f"SELECT {self._COLS} FROM message_deliveries "
            f"WHERE tenant_id = :t{where_extra} ORDER BY seq DESC LIMIT :limit"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), params).all()

        return [self._project_row(row) for row in rows]

    def get(self, seq: int) -> dict[str, Any] | None:
        sql = f"SELECT {self._COLS} FROM message_deliveries WHERE tenant_id = :t AND seq = :seq"
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "seq": int(seq)}).first()
        if row is None:
            return None
        return self._project_row(row)

    @staticmethod
    def _project_row(row: Any) -> dict[str, Any]:
        targets = row[3]
        if isinstance(targets, str):
            targets = json.loads(targets)
        sent_at = row[10]
        if isinstance(sent_at, datetime):
            sent_at = sent_at.isoformat()
        return {
            "seq": row[1],
            "id": row[0],
            "channel": row[2],
            "to": targets or [],
            "subject": row[4],
            "sentAt": sent_at,
            "status": row[5],
            "attempts": row[6],
            "elapsedMs": row[7],
            "errorCode": row[8],
            "errorMessage": row[9],
            "body": row[11],
        }

    def clear(self) -> None:
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM message_deliveries WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )
