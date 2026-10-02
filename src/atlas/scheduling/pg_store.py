"""调度注册表与槽位认领的 PG 档（docs/68 §2.2，打包 N；迁移 030／035／037）。

与其余 `Pg*Store` 不同：**不绑 tenant_id**。调度器要在一个 tick 里看到所有租户的注册项
（理由见 store.py 模块头），租户只是每行的第一列。

认领是这条语句的全部意义：`INSERT ... ON CONFLICT DO NOTHING` 的 rowcount 才是互斥，
不读后写、不"先查再插"（docs/62 L2 的同一条纪律——判定与写入必须落在同一条语句里）。

**身份维度（打包 ZN，2026-10-03；迁移 037；D41 ③）**：行键与认领键都含 `schedule_id`
（run＝定时触发节点 id、reflect＝`__reflect__`），`action` 退为列属性；同一张图的多条
调度各认各的槽，互不吞并。`overlap_policy`（skip/allow）随发布派生写入。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as _dt_timezone
from typing import Any

from sqlalchemy import Engine, text

from atlas.scheduling.models import (
    DEFAULT_OVERLAP_POLICY,
    DEFAULT_SCHEDULE_ACTION,
    OverlapPolicy,
    ScheduleAction,
    ScheduleRecord,
    to_utc_iso,
)

# 认领行的保留窗口：只影响表大小，不影响语义（引擎只看前一分钟，旧槽永不复看）。
FIRE_RETENTION = timedelta(days=7)

_COLUMNS = (
    "tenant_id, graph_id, schedule_id, action, version, cron, timezone, catch_up_minutes, "
    "overlap_policy, enabled, created_at, last_fired_at, last_skipped_at, skip_count"
)


def _to_record(row: Any) -> ScheduleRecord:
    return ScheduleRecord(
        tenant_id=row[0],
        graph_id=row[1],
        schedule_id=row[2],
        action=row[3],
        version=int(row[4]),
        cron=row[5],
        timezone=row[6],
        catch_up_minutes=int(row[7] or 0),
        overlap_policy=row[8] if row[8] in ("skip", "allow") else DEFAULT_OVERLAP_POLICY,
        enabled=bool(row[9]),
        created_at=to_utc_iso(row[10]),
        last_fired_at=to_utc_iso(row[11]) if row[11] is not None else None,
        last_skipped_at=to_utc_iso(row[12]) if row[12] is not None else None,
        skip_count=int(row[13] or 0),
    )


class PgScheduleStore:
    """`schedules` ＋ `schedule_fires`（迁移 030／035／037）实现。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert_published(
        self,
        *,
        tenant_id: str,
        graph_id: str,
        schedule_id: str,
        version: int,
        cron: str,
        action: ScheduleAction = DEFAULT_SCHEDULE_ACTION,
        timezone: str = "UTC",
        catch_up_minutes: int = 0,
        overlap_policy: OverlapPolicy = DEFAULT_OVERLAP_POLICY,
    ) -> ScheduleRecord:
        with self._engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO schedules "
                    "(tenant_id, graph_id, schedule_id, action, version, cron, timezone, "
                    "catch_up_minutes, overlap_policy) "
                    "VALUES (:tenant_id, :graph_id, :schedule_id, :action, :version, :cron, "
                    ":timezone, :catch_up_minutes, :overlap_policy) "
                    "ON CONFLICT (tenant_id, graph_id, schedule_id) DO UPDATE SET "
                    "action = EXCLUDED.action, version = EXCLUDED.version, cron = EXCLUDED.cron, "
                    "timezone = EXCLUDED.timezone, catch_up_minutes = EXCLUDED.catch_up_minutes, "
                    "overlap_policy = EXCLUDED.overlap_policy"
                ),
                {
                    "tenant_id": tenant_id,
                    "graph_id": graph_id,
                    "schedule_id": schedule_id,
                    "action": action,
                    "version": version,
                    "cron": cron,
                    "timezone": timezone,
                    "catch_up_minutes": catch_up_minutes,
                    "overlap_policy": overlap_policy,
                },
            )
        record = self.get(tenant_id, graph_id, schedule_id)
        if record is None:  # 走到这里只剩"刚 upsert 完就被人删掉"这一种竞态
            raise RuntimeError(
                f"调度注册写入后读不到：{tenant_id}/{graph_id}/{schedule_id}"
            )
        return record

    def remove(self, tenant_id: str, graph_id: str) -> None:
        """撤销该图的**全部调度**（不加 schedule_id 过滤，见 store.py 同名方法说明）。"""
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM schedules WHERE tenant_id = :t AND graph_id = :g"),
                {"t": tenant_id, "g": graph_id},
            )
            db.execute(
                text("DELETE FROM schedule_fires WHERE tenant_id = :t AND graph_id = :g"),
                {"t": tenant_id, "g": graph_id},
            )

    def remove_schedule(
        self, tenant_id: str, graph_id: str, schedule_id: str
    ) -> None:
        """撤销该图的**单条**调度（发布 reconcile 差集，见 store.py 同名方法说明）。"""
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM schedules "
                     "WHERE tenant_id = :t AND graph_id = :g AND schedule_id = :s"),
                {"t": tenant_id, "g": graph_id, "s": schedule_id},
            )
            db.execute(
                text("DELETE FROM schedule_fires "
                     "WHERE tenant_id = :t AND graph_id = :g AND schedule_id = :s"),
                {"t": tenant_id, "g": graph_id, "s": schedule_id},
            )

    def get(
        self, tenant_id: str, graph_id: str, schedule_id: str
    ) -> ScheduleRecord | None:
        with self._engine.connect() as db:
            row = db.execute(
                text(f"SELECT {_COLUMNS} FROM schedules "
                     "WHERE tenant_id = :t AND graph_id = :g AND schedule_id = :s"),
                {"t": tenant_id, "g": graph_id, "s": schedule_id},
            ).first()
        return _to_record(row) if row is not None else None

    def list_tenant(self, tenant_id: str) -> list[ScheduleRecord]:
        return self._select("WHERE tenant_id = :t", {"t": tenant_id})

    def list_all(self) -> list[ScheduleRecord]:
        return self._select("", {})

    def set_enabled(
        self,
        tenant_id: str,
        graph_id: str,
        schedule_id: str,
        enabled: bool,
    ) -> ScheduleRecord | None:
        with self._engine.begin() as db:
            row = db.execute(
                text(
                    "UPDATE schedules SET enabled = :enabled "
                    "WHERE tenant_id = :t AND graph_id = :g AND schedule_id = :s "
                    "RETURNING graph_id"
                ),
                {"enabled": enabled, "t": tenant_id, "g": graph_id, "s": schedule_id},
            ).first()
        return self.get(tenant_id, graph_id, schedule_id) if row is not None else None

    def claim(
        self,
        tenant_id: str,
        graph_id: str,
        schedule_id: str,
        slot: datetime,
        action: ScheduleAction = DEFAULT_SCHEDULE_ACTION,
    ) -> bool:
        with self._engine.begin() as db:
            won = db.execute(
                text(
                    "INSERT INTO schedule_fires (tenant_id, graph_id, schedule_id, action, slot_utc) "
                    "VALUES (:t, :g, :s, :a, :slot) ON CONFLICT DO NOTHING"
                ),
                {
                    "t": tenant_id, "g": graph_id, "s": schedule_id,
                    "a": action, "slot": _as_utc(slot),
                },
            ).rowcount
            db.execute(
                text(
                    "DELETE FROM schedule_fires "
                    "WHERE tenant_id = :t AND graph_id = :g AND schedule_id = :s "
                    "AND slot_utc < :cutoff"
                ),
                {
                    "t": tenant_id, "g": graph_id, "s": schedule_id,
                    "cutoff": _as_utc(slot) - FIRE_RETENTION,
                },
            )
        return int(won or 0) == 1

    def note_fired(
        self,
        tenant_id: str,
        graph_id: str,
        schedule_id: str,
        slot: datetime,
    ) -> None:
        with self._engine.begin() as db:
            db.execute(
                text("UPDATE schedules SET last_fired_at = :slot "
                     "WHERE tenant_id = :t AND graph_id = :g AND schedule_id = :s"),
                {"slot": _as_utc(slot), "t": tenant_id, "g": graph_id, "s": schedule_id},
            )

    def note_skipped(
        self,
        tenant_id: str,
        graph_id: str,
        schedule_id: str,
        slot: datetime,
    ) -> None:
        with self._engine.begin() as db:
            db.execute(
                text("UPDATE schedules SET last_skipped_at = :slot, "
                     "skip_count = skip_count + 1 "
                     "WHERE tenant_id = :t AND graph_id = :g AND schedule_id = :s"),
                {"slot": _as_utc(slot), "t": tenant_id, "g": graph_id, "s": schedule_id},
            )

    def reset_tenant(self, tenant_id: str) -> None:
        with self._engine.begin() as db:
            db.execute(text("DELETE FROM schedules WHERE tenant_id = :t"), {"t": tenant_id})
            db.execute(
                text("DELETE FROM schedule_fires WHERE tenant_id = :t"), {"t": tenant_id}
            )

    def _select(self, where: str, params: dict) -> list[ScheduleRecord]:
        with self._engine.connect() as db:
            rows = db.execute(
                text(
                    f"SELECT {_COLUMNS} FROM schedules {where} "
                    "ORDER BY tenant_id, graph_id, schedule_id"
                ),
                params,
            ).all()
        return [_to_record(row) for row in rows]


def _as_utc(moment: datetime) -> datetime:
    """键列一律 TIMESTAMPTZ：naive 输入按 UTC 解释，绝不把本地偏移交给 DB 猜。"""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=_dt_timezone.utc)
    return moment.astimezone(_dt_timezone.utc)
