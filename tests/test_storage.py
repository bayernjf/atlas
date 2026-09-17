"""M5a 存储抽象（docs/24 §1 / 08 M5a 立项条）：Protocol、统一约定与进程内实现。

验收口径：八个进程内 store 满足各自 Repository Protocol；GraphStore/FeedbackStore
自 api/main.py 搬移到 storage/memory.py 后行为不变（id 生成、返回形状、reset 分档）。
"""

import pytest

from atlas.storage.base import (
    RESET_PERSISTENT,
    RESET_RESETTABLE,
    StorageError,
    ApprovalRepository,
    DebugRepository,
    FeedbackRepository,
    GraphRepository,
    MonitoringRepository,
    RecordingRepository,
    SessionRepository,
)
from atlas.storage.memory import (
    ApprovalBroker,
    DebuggerBroker,
    FeedbackStore,
    GraphStore,
    MonitoringStore,
    RecordingStore,
)
from atlas.iam.sessions import SessionStore


@pytest.mark.parametrize(
    ("instance", "protocol"),
    [
        (GraphStore(), GraphRepository),
        (RecordingStore(), RecordingRepository),
        (FeedbackStore(), FeedbackRepository),
        (SessionStore(), SessionRepository),
        (ApprovalBroker(), ApprovalRepository),
        (DebuggerBroker(), DebugRepository),
        (MonitoringStore(), MonitoringRepository),
    ],
)
def test_implementations_satisfy_protocols(instance, protocol):
    assert isinstance(instance, protocol)


def test_reset_tiers_and_storage_error():
    assert RESET_RESETTABLE == "resettable"
    assert RESET_PERSISTENT == "persistent"
    assert issubclass(StorageError, Exception)


def test_graph_store_behaviour_unchanged():
    store = GraphStore()
    raw = {"version": 1, "nodes": [{"id": "trigger-1"}, {"id": "tool_call-1"}]}
    graph_id = store.save(raw)
    assert graph_id == "graph-1"
    assert store.get(graph_id) is raw
    assert store.list() == [{"id": "graph-1", "node_count": 2, "updated_at": store.list()[0]["updated_at"]}]
    store.clear()
    assert store.get(graph_id) is None
    assert store.save(raw) == "graph-1"  # clear 重置计数器（与搬移前一致）


def test_feedback_store_behaviour_unchanged():
    from atlas.storage.memory import FeedbackRequest

    store = FeedbackStore()
    item = store.add(FeedbackRequest(type="bug", content="问题", contact="a@b.c"))
    assert item["id"] == "feedback-1"
    assert item["type"] == "bug"
    assert item["content"] == "问题"
    assert item["contact"] == "a@b.c"
    assert "created_at" in item
    assert store.list() == [item]
