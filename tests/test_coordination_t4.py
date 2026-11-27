"""T4 任务总线：优先级抢占 + SQLite 跨实例恢复。"""

from __future__ import annotations

from atlas.coordination import TaskStore
from atlas.coordination.sqlite_store import SqliteTaskStore


def _dispatch(store, *, key: str, conflict_key: str, priority: int, run_id: str = "run-1"):
    return store.dispatch(
        run_id=run_id,
        idempotency_key=key,
        trace_id="trace-1",
        graph_version="g@1",
        type="refund.verify",
        assignee="bot.customer",
        payload={"order_id": "T4-1"},
        deadline_ms=1000,
        conflict_key=conflict_key,
        priority=priority,
    )


def test_priority_preempts_lower_priority_active_task():
    store = TaskStore()
    low, created_low = _dispatch(store, key="low", conflict_key="order:T4-1", priority=1)
    high, created_high = _dispatch(store, key="high", conflict_key="order:T4-1", priority=2)
    assert created_low is True
    assert created_high is True
    assert store.get(low.taskId).state == "failed"
    assert store.get(low.taskId).result["error"] == "TASK_PREEMPTED"
    assert store.get(low.taskId).result["preemptedBy"] == high.taskId
    assert high.state == "pending"


def test_lower_priority_is_deferred_to_active_task():
    store = TaskStore()
    active, _ = _dispatch(store, key="high", conflict_key="order:T4-2", priority=5)
    deferred, created = _dispatch(store, key="low", conflict_key="order:T4-2", priority=1)
    assert created is False
    assert deferred.taskId == active.taskId
    assert len(store.list()) == 1


def test_sqlite_task_store_survives_new_instance(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    first = SqliteTaskStore(path, "tenant-t4")
    low, _ = _dispatch(first, key="low", conflict_key="order:T4-3", priority=1)
    high, _ = _dispatch(first, key="high", conflict_key="order:T4-3", priority=2)
    assert first.get(low.taskId).state == "failed"
    assert first.get(high.taskId).state == "pending"

    # 新实例模拟重启：只靠同一个 sqlite 文件恢复台账与抢占结果。
    second = SqliteTaskStore(path, "tenant-t4")
    assert second.get(high.taskId).state == "pending"
    assert second.get(low.taskId).result["error"] == "TASK_PREEMPTED"
    assert [item.taskId for item in second.list()] == [high.taskId, low.taskId]


def test_sqlite_mutations_are_visible_across_instances(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    first = SqliteTaskStore(path, "tenant-t4b")
    task, _ = _dispatch(first, key="k", conflict_key="", priority=0)
    first.accept(task.taskId)
    second = SqliteTaskStore(path, "tenant-t4b")
    assert second.get(task.taskId).state == "accepted"
    second.start(task.taskId)
    second.complete(task.taskId, {"ok": True})
    assert first.get(task.taskId).state == "done"
    assert first.get(task.taskId).result == {"ok": True}
