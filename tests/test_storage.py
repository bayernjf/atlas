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


# --- U1133：monitoring_alerts 的写语句必须逐条带租户条件（docs/89 §15 A-2）------

def _alert_writes_missing_tenant(source: str) -> list[str]:
    """扫源码里所有对 monitoring_alerts 的 UPDATE/DELETE 语句，返回没带 tenant_id 的那些。

    语句以"编译期常量"为粒度取：源码里它们是相邻字面量拼接，`ast` 拿到的就是整条 SQL，
    不必去猜跨行拼接的边界（这类守护一旦靠行数猜，就会随格式漂移）。
    """
    import ast
    import re

    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        sql = node.value
        if not re.search(r"\b(UPDATE|DELETE FROM)\s+monitoring_alerts\b", sql):
            continue
        where = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
        if "tenant_id" not in where:
            offenders.append(" ".join(sql.split())[:80])
    return offenders


def test_u1133_every_monitoring_alert_write_is_tenant_scoped():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src/atlas/storage/pg.py").read_text(
        encoding="utf-8"
    )
    assert _alert_writes_missing_tenant(source) == [], "有告警写语句漏了租户条件"
    # 判别对照：扫描必须真的看见这些语句，否则"零命中"可能只是"一条都没匹配上"。
    assert source.count("UPDATE monitoring_alerts") >= 4, "扫描器看不到预期的语句数，守护已失效"


def test_u1133_reverse_gate_a_missing_tenant_condition_is_caught():
    """反向门：真删掉一处 `AND tenant_id`，守护必须点名那条语句（否则它是永久绿灯）。"""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src/atlas/storage/pg.py").read_text(
        encoding="utf-8"
    )
    mutated = source.replace(
        "SET status = 'acknowledged' \"\n                    \"WHERE id = :id AND tenant_id = :tenant_id",
        "SET status = 'acknowledged' \"\n                    \"WHERE id = :id",
        1,
    )
    assert mutated != source, "变异没落上，锚点已随代码漂移"
    caught = _alert_writes_missing_tenant(mutated)
    assert len(caught) == 1 and "acknowledged" in caught[0], caught
