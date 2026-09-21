"""告警静默与值班轮换：模型、纯函数与进程内状态（docs/33 §5）。

v1 全部进程内（内存与 PG 两档共用 OpsStore，挂在 per-tenant 常驻 store 实例上）：
- 静默 Silence / 值班 OnCallSchedule 不落库，重启清空；
- Alert.assignee / escalated_at 不写 PG，PG 读回后由 OpsStore 关联 assignee、
  由 apply_escalation 重新惰性评估升级（语义幂等，docs/33 §5.2/§5.3 已注明）。

本模块自带锁，与 MonitoringStore / PgMonitoringStore 的告警写路径串行使用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    # ---------------- on-call ----------------

    def get_oncall(self) -> OnCallSchedule:
        with self._lock:
            return self._schedule.model_copy(deep=True)

    def set_oncall(self, *, members: list[str], updated_by: str) -> OnCallSchedule:
        """去重保序、丢弃空白串；重置 index=0（docs/33 §5.3）。"""
        deduped = list(dict.fromkeys(m.strip() for m in members if m.strip()))
        with self._lock:
            self._schedule = OnCallSchedule(
                members=deduped, index=0, updated_at=_now_iso(), updated_by=updated_by
            )
            return self._schedule.model_copy(deep=True)

    def rotate_oncall(self, *, updated_by: str) -> OnCallSchedule:
        with self._lock:
            total = len(self._schedule.members)
            if total == 0:
                raise OnCallEmpty("值班表为空，无法轮换")
            self._schedule.index = (self._schedule.index + 1) % total
            self._schedule.updated_at = _now_iso()
            self._schedule.updated_by = updated_by
            return self._schedule.model_copy(deep=True)

    def current_assignee(self) -> str | None:
        with self._lock:
            return current_assignee(self._schedule)

    # ---------------- assignee（PG 档进程内关联） ----------------

    def remember_assignee(self, alert_id: str) -> str | None:
        """新告警新建时调用：把当前值班人记到该 alert（合并不调用）。"""
        with self._lock:
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
