"""调度注册表与槽位认领的进程内档（docs/68 §2.2／§2.4，打包 N）。

**为什么是全局 store 而不是每租户一个**：调度器要在一个 tick 里看到所有租户的调度。
每租户 store 由 `TenantRegistry` 惰性装配，那意味着"重启后还没被任何请求触及的租户"
永远不会被调度——定时任务恰恰多在夜里，这个偏差会静默地只跑活跃租户。

认领语义（`claim`）与 PG 档逐键一致：同一个 `(tenant, graph, slot)` 至多成功一次。
内存档**易失**（docs/68 §1 D-7）：重启丢历史认领，同一分钟槽理论上可再派发一次；
生产形态必须 PG 档。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol

from atlas.scheduling.models import ScheduleRecord, slot_key, to_utc_iso

# 认领集合只保留最近这些天的槽位：引擎只看前一分钟，更早的槽永不复看（D-5），
# 再留着就是无界增长（`* * * * *` 一天 1440 条）。
CLAIM_RETENTION = timedelta(days=7)


class ScheduleStore(Protocol):
    """调度注册与槽位认领的两档共同契约（docs/68 §2.2）。"""

    def upsert_published(
        self, *, tenant_id: str, graph_id: str, version: int, cron: str
    ) -> ScheduleRecord:
        """发布派生：更新版本与 cron，**保留** enabled/created_at/跳过统计（D-4）。"""
        ...

    def remove(self, tenant_id: str, graph_id: str) -> None: ...

    def get(self, tenant_id: str, graph_id: str) -> ScheduleRecord | None: ...

    def list_tenant(self, tenant_id: str) -> list[ScheduleRecord]: ...

    def list_all(self) -> list[ScheduleRecord]: ...

    def set_enabled(self, tenant_id: str, graph_id: str, enabled: bool) -> ScheduleRecord | None: ...

    def claim(self, tenant_id: str, graph_id: str, slot: datetime) -> bool:
        """认领这个分钟槽；只有第一次返回 True（派发权唯一来源）。"""
        ...

    def note_fired(self, tenant_id: str, graph_id: str, slot: datetime) -> None: ...

    def note_skipped(self, tenant_id: str, graph_id: str, slot: datetime) -> None: ...

    def reset_tenant(self, tenant_id: str) -> None: ...


class InMemoryScheduleStore:
    """进程内档（demo/测试默认）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_tenant: dict[str, dict[str, ScheduleRecord]] = {}
        self._claims: dict[tuple[str, str, str], datetime] = {}

    def upsert_published(
        self, *, tenant_id: str, graph_id: str, version: int, cron: str
    ) -> ScheduleRecord:
        with self._lock:
            existing = self._by_tenant.setdefault(tenant_id, {}).get(graph_id)
            if existing is None:
                record = ScheduleRecord(
                    tenant_id=tenant_id, graph_id=graph_id, version=version, cron=cron,
                    enabled=True, created_at=to_utc_iso(datetime.now(timezone.utc)),
                )
            else:
                record = existing.model_copy(update={"version": version, "cron": cron})
            self._by_tenant[tenant_id][graph_id] = record
            return record

    def remove(self, tenant_id: str, graph_id: str) -> None:
        with self._lock:
            self._by_tenant.get(tenant_id, {}).pop(graph_id, None)
            for key in [k for k in self._claims if k[0] == tenant_id and k[1] == graph_id]:
                del self._claims[key]

    def get(self, tenant_id: str, graph_id: str) -> ScheduleRecord | None:
        with self._lock:
            record = self._by_tenant.get(tenant_id, {}).get(graph_id)
            return record.model_copy() if record else None

    def list_tenant(self, tenant_id: str) -> list[ScheduleRecord]:
        with self._lock:
            rows = list(self._by_tenant.get(tenant_id, {}).values())
        return [row.model_copy() for row in sorted(rows, key=_sort_key)]

    def list_all(self) -> list[ScheduleRecord]:
        with self._lock:
            rows = [row for tenant in self._by_tenant.values() for row in tenant.values()]
        return [row.model_copy() for row in sorted(rows, key=_sort_key)]

    def set_enabled(
        self, tenant_id: str, graph_id: str, enabled: bool
    ) -> ScheduleRecord | None:
        with self._lock:
            record = self._by_tenant.get(tenant_id, {}).get(graph_id)
            if record is None:
                return None
            record = record.model_copy(update={"enabled": enabled})
            self._by_tenant[tenant_id][graph_id] = record
            return record.model_copy()

    def claim(self, tenant_id: str, graph_id: str, slot: datetime) -> bool:
        key = (tenant_id, graph_id, slot_key(slot))
        with self._lock:
            self._prune_locked(slot)
            if key in self._claims:
                return False
            self._claims[key] = slot
            return True

    def note_fired(self, tenant_id: str, graph_id: str, slot: datetime) -> None:
        self._patch(tenant_id, graph_id, {"last_fired_at": slot_key(slot)})

    def note_skipped(self, tenant_id: str, graph_id: str, slot: datetime) -> None:
        with self._lock:
            record = self._by_tenant.get(tenant_id, {}).get(graph_id)
            if record is None:
                return
            record = record.model_copy(update={
                "last_skipped_at": slot_key(slot),
                "skip_count": record.skip_count + 1,
            })
            self._by_tenant[tenant_id][graph_id] = record

    def reset_tenant(self, tenant_id: str) -> None:
        # 一次取干净，不在持锁期间调 remove（Lock 不可重入，二套锁序会自锁）。
        with self._lock:
            self._by_tenant.pop(tenant_id, None)
            for key in [k for k in self._claims if k[0] == tenant_id]:
                del self._claims[key]

    def _patch(self, tenant_id: str, graph_id: str, update: dict) -> None:
        with self._lock:
            record = self._by_tenant.get(tenant_id, {}).get(graph_id)
            if record is None:
                return
            self._by_tenant[tenant_id][graph_id] = record.model_copy(update=update)

    def _prune_locked(self, now: datetime) -> None:
        cutoff = now - CLAIM_RETENTION
        for key, slot in list(self._claims.items()):
            if slot < cutoff:
                del self._claims[key]


def _sort_key(record: ScheduleRecord) -> tuple[str, str]:
    return (record.tenant_id, record.graph_id)
