"""告警静默与值班轮换：模型、纯函数与进程内状态（docs/33 §5）。

v1 全部进程内（内存与 PG 两档共用 OpsStore，挂在 per-tenant 常驻 store 实例上）：
- 静默 Silence / 值班 OnCallSchedule 不落库，重启清空；
- Alert.assignee / escalated_at 不写 PG，PG 读回后由 OpsStore 关联 assignee、
  由 apply_escalation 重新惰性评估升级（语义幂等，docs/33 §5.2/§5.3 已注明）。

本模块自带锁，与 MonitoringStore / PgMonitoringStore 的告警写路径串行使用。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from threading import Lock

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return _now_dt().isoformat()


def _now_dt() -> datetime:
    # 模块级时钟（docs/60 §4.2）：惰性按日轮换的确定性测试可 monkeypatch 本函数。
    return datetime.now(timezone.utc)


# update_silence 区分「未提供」与「显式置 null（rule_id/graph_id）」的哨兵
_UNSET = object()


def _normalize_interval(value: object) -> int | None:
    """rotation_interval_days：None/0/非正＝不自动（None）；1-365 整数保留；其余视为 None。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= 365 else None


class Silence(BaseModel):
    id: str  # sil-N
    # 皆为 None 表示全局静默（v1 允许，UI 二次确认警示）
    rule_id: str | None = None
    graph_id: str | None = None
    reason: str
    created_by: str
    created_at: str
    expires_at: str
    suppressed_count: int = 0


class OnCallSchedule(BaseModel):
    members: list[str] = Field(default_factory=list)
    index: int = 0
    updated_at: str | None = None
    updated_by: str | None = None
    # docs/60 §4.2：惰性按日自动轮换（纯超集，None/0＝不自动）
    rotation_interval_days: int | None = None
    last_rotated_at: str | None = None  # date ISO（YYYY-MM-DD，UTC 日界）


class OnCallEmpty(RuntimeError):
    """值班表为空时轮换（409）。"""


def silence_matches(silence: Silence, rule_id: str, graph_id: str, now: str) -> bool:
    """docs/33 §5.1：now < expires_at 且 rule_id/graph_id 为空或相等才命中。"""
    try:
        if datetime.fromisoformat(now) >= datetime.fromisoformat(silence.expires_at):
            return False
    except ValueError:
        return False
    if silence.rule_id is not None and silence.rule_id != rule_id:
        return False
    if silence.graph_id is not None and silence.graph_id != graph_id:
        return False
    return True


def is_silence_active(silence: Silence, now: str | None = None) -> bool:
    moment = now or _now_iso()
    try:
        return datetime.fromisoformat(moment) < datetime.fromisoformat(silence.expires_at)
    except ValueError:
        return False


def current_assignee(schedule: OnCallSchedule) -> str | None:
    """docs/33 §5.3：空表 None；否则 members[index % len]。"""
    if not schedule.members:
        return None
    return schedule.members[schedule.index % len(schedule.members)]


def maybe_auto_rotate(
    schedule: OnCallSchedule, now: datetime
) -> OnCallSchedule | None:
    """docs/60 §4.2：惰性按日轮换纯函数。

    - interval 为空/成员 < 2/无 last_rotated_at -> None（不动）；
    - elapsed_days = (now.date() - date(last_rotated_at)).days；
    - steps = elapsed_days // interval；steps >= 1 才推进：
      index=(index+steps) % len(members)，last_rotated_at 推进到本轮边界日期；
    - 时钟由调用方注入（now），保证测试确定性；不引定时器、不做分布式锁。
    """
    interval = _normalize_interval(schedule.rotation_interval_days)
    if interval is None or len(schedule.members) < 2 or not schedule.last_rotated_at:
        return None
    try:
        last_date = date.fromisoformat(schedule.last_rotated_at[:10])
    except ValueError:
        return None
    elapsed_days = (now.date() - last_date).days
    if elapsed_days < 0:
        return None
    steps = elapsed_days // interval
    if steps < 1:
        return None
    total = len(schedule.members)
    new_index = (schedule.index + steps) % total
    new_boundary = (last_date + timedelta(days=steps * interval)).isoformat()
    return schedule.model_copy(
        update={"index": new_index, "last_rotated_at": new_boundary}
    )


class OpsStore:
    """静默 / 值班 / assignee 的进程内状态（两档共享，单锁）。"""

    SILENCE_LIMIT = 100

    def __init__(self) -> None:
        self._lock = Lock()
        self._silences: list[Silence] = []
        self._schedule = OnCallSchedule()
        self._silence_counter = 0
        # PG 档告警在库、assignee 在进程内：alert_id -> assignee
        self._assignees: dict[str, str] = {}

    # ---------------- silences ----------------

    def _purge_expired_locked(self, now: str) -> None:
        self._silences = [s for s in self._silences if datetime.fromisoformat(now) < datetime.fromisoformat(s.expires_at)]

    def create_silence(
        self,
        *,
        rule_id: str | None,
        graph_id: str | None,
        duration_minutes: int,
        reason: str,
        created_by: str,
    ) -> Silence:
        with self._lock:
            now = _now_iso()
            self._purge_expired_locked(now)
            self._silence_counter += 1
            expires = (datetime.fromisoformat(now) + timedelta(minutes=duration_minutes)).isoformat()
            silence = Silence(
                id=f"sil-{self._silence_counter}",
                rule_id=rule_id,
                graph_id=graph_id,
                reason=reason,
                created_by=created_by,
                created_at=now,
                expires_at=expires,
            )
            self._silences.append(silence)
            # 超容淘汰最旧（惰性清理后仍超 100 才裁，保留最新 100 条）
            if len(self._silences) > self.SILENCE_LIMIT:
                self._silences = self._silences[-self.SILENCE_LIMIT :]
            return silence.model_copy(deep=True)

    def suppress_if_matched(self, rule_id: str, graph_id: str) -> bool:
        """告警产生路径调用：命中任一活跃静默则压下并给该静默计数，返回 True。"""
        with self._lock:
            now = _now_iso()
            for silence in self._silences:
                if silence_matches(silence, rule_id, graph_id, now):
                    silence.suppressed_count += 1
                    return True
            return False

    def list_silences(self, active: bool | None = None) -> list[Silence]:
        with self._lock:
            now = _now_iso()
            items = [s.model_copy(deep=True) for s in self._silences]
        if active is None:
            return items
        return [s for s in items if is_silence_active(s, now) is active]

    def delete_silence(self, silence_id: str) -> bool:
        """提前解除；不存在返回 False（API 映射 404）。"""
        with self._lock:
            for index, silence in enumerate(self._silences):
                if silence.id == silence_id:
                    del self._silences[index]
                    return True
            return False

    def update_silence(
        self,
        silence_id: str,
        *,
        reason: object = _UNSET,
        rule_id: object = _UNSET,
        graph_id: object = _UNSET,
        expires_at: object = _UNSET,
    ) -> Silence | None:
        """docs/60 §4.1：仅命中**未过期**静默可改；不存在/已过期返 None。

        id/created_by/created_at/suppressed_count 不可变；rule_id/graph_id 显式 None
        经哨兵区分于「未提供」。字段合法性（reason 非空、expires 未来、互斥）由路由
        统一校验，存储层只负责命中与应用。
        """
        with self._lock:
            now = _now_iso()
            for silence in self._silences:
                if silence.id != silence_id:
                    continue
                if not is_silence_active(silence, now):
                    return None
                updates: dict[str, object] = {}
                if reason is not _UNSET:
                    updates["reason"] = reason
                if rule_id is not _UNSET:
                    updates["rule_id"] = rule_id
                if graph_id is not _UNSET:
                    updates["graph_id"] = graph_id
                if expires_at is not _UNSET:
                    updates["expires_at"] = expires_at
                if updates:
                    # 字段合法性由路由统一校验，存储层信任并应用（model_copy 不重校验）
                    updated = silence.model_copy(update=updates)
                    index = self._silences.index(silence)
                    self._silences[index] = updated
                    return updated.model_copy(deep=True)
                return silence.model_copy(deep=True)
            return None

    # ---------------- on-call ----------------

    def _auto_rotate_locked(self, now_dt: datetime) -> OnCallSchedule | None:
        """读路径惰性轮换：非 None 则落库（改自身），返回推进后的表（docs/60 §4.2）。"""
        rotated = maybe_auto_rotate(self._schedule, now_dt)
        if rotated is not None:
            self._schedule = rotated
        return rotated

    def get_oncall(self) -> OnCallSchedule:
        with self._lock:
            self._auto_rotate_locked(_now_dt())
            return self._schedule.model_copy(deep=True)

    def set_oncall(
        self,
        *,
        members: list[str],
        updated_by: str,
        rotation_interval_days: int | None = None,
    ) -> OnCallSchedule:
        """去重保序、丢弃空白串；重置 index=0（docs/33 §5.3）；docs/60 §4.2 记间隔。"""
        deduped = list(dict.fromkeys(m.strip() for m in members if m.strip()))
        interval = _normalize_interval(rotation_interval_days)
        now_dt = _now_dt()
        with self._lock:
            self._schedule = OnCallSchedule(
                members=deduped,
                index=0,
                updated_at=now_dt.isoformat(),
                updated_by=updated_by,
                rotation_interval_days=interval,
                last_rotated_at=now_dt.date().isoformat() if interval is not None else None,
            )
            return self._schedule.model_copy(deep=True)

    def rotate_oncall(self, *, updated_by: str) -> OnCallSchedule:
        with self._lock:
            total = len(self._schedule.members)
            if total == 0:
                raise OnCallEmpty("值班表为空，无法轮换")
            now_dt = _now_dt()
            self._schedule.index = (self._schedule.index + 1) % total
            self._schedule.updated_at = now_dt.isoformat()
            self._schedule.updated_by = updated_by
            # docs/60 §4.2：手动轮换同步刷新按日轮换基准（仅在开启自动间隔时）
            if self._schedule.rotation_interval_days is not None:
                self._schedule.last_rotated_at = now_dt.date().isoformat()
            return self._schedule.model_copy(deep=True)

    def current_assignee(self) -> str | None:
        with self._lock:
            self._auto_rotate_locked(_now_dt())
            return current_assignee(self._schedule)

    # ---------------- assignee（PG 档进程内关联） ----------------

    def remember_assignee(self, alert_id: str) -> str | None:
        """新告警新建时调用：把当前值班人记到该 alert（合并不调用）。"""
        with self._lock:
            self._auto_rotate_locked(_now_dt())
            assignee = current_assignee(self._schedule)
            if assignee is not None:
                self._assignees[alert_id] = assignee
            return assignee

    def assignee_of(self, alert_id: str) -> str | None:
        with self._lock:
            return self._assignees.get(alert_id)

    # ---------------- reset ----------------

    def reset(self) -> None:
        with self._lock:
            self._silences.clear()
            self._schedule = OnCallSchedule()
            self._silence_counter = 0
            self._assignees.clear()
