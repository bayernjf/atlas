"""M5b PG 存储实现集成测试（integration 标记，docs/24 §6 / 13 §3）。

DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_storage_pg_integration.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run storage PG integration",
    ),
]

TENANT = "pgtest"


def _run_migration(engine) -> None:
    """按序号执行 db/migrations/*.sql（跳过注释行；语句以 ; 结尾）。"""
    migrations_dir = Path(__file__).resolve().parents[1] / "db" / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        statements: list[str] = []
        current: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            current.append(line)
            if stripped.endswith(";"):
                statements.append("\n".join(current))
                current = []
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))


def _cleanup(engine) -> None:
    tables = [
        "graphs", "graph_versions", "runs", "interruptions", "iam_sessions",
        "recordings", "feedback", "monitoring_runs", "monitoring_alerts", "monitoring_rules",
        "memory_items",
    ]
    with engine.begin() as conn:
        for table in tables:
            conn.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": TENANT})


@pytest.fixture(scope="module")
def backend():
    from atlas.memory.database import create_database_engine
    from atlas.storage.pg import PgBackend

    engine = create_database_engine(DATABASE_URL, pool_size=2)
    _run_migration(engine)
    _cleanup(engine)
    yield PgBackend(engine)
    _cleanup(engine)
    engine.dispose()


def test_graph_store_roundtrip_and_versioning(backend):
    store = backend.graph_store(TENANT)
    raw = {"version": 1, "variables": [], "nodes": [{"id": "t", "type": "trigger"}], "edges": []}
    graph_id = store.save(raw)
    assert graph_id.startswith("graph-")
    assert store.get(graph_id) == raw
    assert store.list() == [{"id": graph_id, "node_count": 1, "updated_at": store.list()[0]["updated_at"]}]

    # 版本化（M6）
    assert store.publish(graph_id, raw) == 1
    assert store.publish(graph_id, raw) == 2
    assert store.list_versions(graph_id) == [1, 2]
    assert store.get(graph_id, 1)["releaseVersion"] == 1
    assert "releaseVersion" not in store.get(graph_id)
    # 隔离：改草稿不影响已发布版本
    store.save(raw)
    assert store.get(graph_id, 1)["releaseVersion"] == 1

    store.clear()
    assert store.get(graph_id) is None
    assert store.list_versions(graph_id) == []


def test_session_store_roundtrip(backend):
    from atlas.iam.principals import Principal, Role

    store = backend.session_store()
    principal = Principal(
        tenant_id=TENANT, tenant_name="测试租户", username="admin-a",
        display_name="A 企业管理员", role=Role.ADMIN,
    )
    token = store.issue(principal)
    assert token.startswith("sess-")
    fetched = store.principal_for_token(token)
    assert fetched is not None
    assert fetched.username == "admin-a"
    assert fetched.role == Role.ADMIN
    assert fetched.tenant_id == TENANT
    store.revoke(token)
    assert store.principal_for_token(token) is None
    store.reset()


def test_feedback_and_recording_persistent(backend):
    from atlas.recording.cases import RecordStep
    from atlas.storage.memory import FeedbackRequest

    feedback = backend.feedback_store(TENANT)
    item = feedback.add(FeedbackRequest(type="bug", content="问题", contact="a@b.c"))
    assert item["id"].startswith("feedback-")
    assert feedback.list() == [item]

    recording = backend.recording_store(TENANT)
    case = recording.add(
        name="用例", graph={"version": 1}, inputs={"a": 1},
        steps=[RecordStep(node_id="t", node_type="trigger", output={})], status="ok",
    )
    assert case.id.startswith("rec-")
    assert [c.id for c in recording.list()] == [case.id]
    assert recording.get(case.id) is not None
    assert recording.get(case.id).name == "用例"
    assert recording.delete(case.id) is True
    assert recording.get(case.id) is None


def test_recording_recorded_at_roundtrip(backend):
    # C（docs/27 §2.4）：recorded_at 在 PG 档落库并往返，缺省与 created_at 同刻。
    from atlas.recording.cases import RecordStep

    recording = backend.recording_store(TENANT)
    auto = recording.add(
        name="自动锚点", graph={"version": 1}, inputs=None,
        steps=[RecordStep(node_id="t", node_type="trigger", output={})], status="ok",
    )
    assert auto.recorded_at == auto.created_at
    got = recording.get(auto.id)
    assert got is not None and got.recorded_at == auto.created_at
    assert all(c.recorded_at for c in recording.list())
    fixed = recording.add(
        name="显式锚点", graph={"version": 1}, inputs=None,
        steps=[RecordStep(node_id="t", node_type="trigger", output={})], status="ok",
        recorded_at="2026-01-02T03:04:05+00:00",
    )
    assert recording.get(fixed.id).recorded_at == "2026-01-02T03:04:05+00:00"


def test_recording_graph_id_subgraphs_roundtrip(backend):
    # 批 1 D26①（docs/28 §2.1，迁移 008）：graph_id/subgraphs 富字段 PG 往返，
    # 修复 PG 档「录为用例」500（unexpected keyword argument 'graph_id'）。
    from atlas.recording.cases import RecordStep

    recording = backend.recording_store(TENANT)
    frozen = {"sub-a@3": {"version": 1, "nodes": []}, "sub-b": {"version": 1}}
    rich = recording.add(
        name="富字段", graph={"version": 1}, inputs={"amount": 100},
        steps=[RecordStep(node_id="t", node_type="trigger", output={})],
        status="ok", graph_id="graph-rich", subgraphs=frozen,
    )
    got = recording.get(rich.id)
    assert got is not None
    assert got.graph_id == "graph-rich"
    assert got.subgraphs == frozen
    listed = next(c for c in recording.list() if c.id == rich.id)
    assert listed.graph_id == "graph-rich" and listed.subgraphs == frozen
    # 缺省兼容：旧形状用例 graph_id 空串、subgraphs 空 dict
    legacy = recording.add(
        name="旧形状", graph={"version": 1}, inputs=None,
        steps=[RecordStep(node_id="t", node_type="trigger", output={})], status="ok",
    )
    legacy_got = recording.get(legacy.id)
    assert legacy_got.graph_id == "" and legacy_got.subgraphs == {}


def test_recording_update_meta_roundtrip(backend):
    # 批 1 D26③（docs/28 §2.3）：PUT 仅改 name/inputs，PG 往返且录制事实不动。
    from atlas.recording.cases import RecordStep

    recording = backend.recording_store(TENANT)
    case = recording.add(
        name="原名", graph={"version": 1}, inputs={"x": 1},
        steps=[RecordStep(node_id="t", node_type="trigger", output={"a": 1})],
        status="completed", graph_id="g-upd",
        subgraphs={"s@1": {"version": 1}},
    )
    updated = recording.update_meta(case.id, name="新名", inputs={"x": 9})
    assert updated is not None
    assert updated.name == "新名" and updated.inputs == {"x": 9}
    got = recording.get(case.id)
    assert got.name == "新名" and got.inputs == {"x": 9}
    # 录制事实不动
    assert got.graph_id == "g-upd" and got.subgraphs == {"s@1": {"version": 1}}
    assert len(got.steps) == 1 and got.graph == {"version": 1}
    # 不存在 → None
    assert recording.update_meta("rec-nope", name="x") is None


def test_monitoring_roundtrip(backend):
    store = backend.monitoring_store(TENANT)
    record = store.record_run(
        graph_id="graph-1", mode="sync", status="completed",
        started_at="2026-09-17T00:00:00+00:00", duration_ms=10.0, nodes=[],
    )
    assert record.id.startswith("run-")
    runs = store.list_runs()
    assert [r.id for r in runs] == [record.id]
    # 规则默认值可读、可写（合法四段格式，同 test_monitoring.py）
    rules = store.get_rules()
    assert rules is not None
    store.update_rules({
        "run_error": {"enabled": True},
        "node_failed": {"enabled": False},
        "consecutive_failures": {"enabled": True, "threshold": 2},
        "failure_rate": {"enabled": True, "window": 10, "min_samples": 3, "rate": 0.8},
    })
    assert store.get_rules().consecutive_failures.threshold == 2
    store.reset()
    assert store.list_runs() == []
    assert store.get_rules().node_failed.enabled


def test_interruption_frame_roundtrip(backend):
    from atlas.storage.frame import build_frame, deadline_iso
    from atlas.storage.recovery import clear_frame, load_pending_frames, make_frame_sink

    engine = backend.engine
    frame = build_frame(
        token="tok-1", run_id="run-1", node_id="human-1", kind="approval",
        deadline_at=deadline_iso(30),
        graph_snapshot={"version": 1, "nodes": [], "edges": []},
        resume_state={"graph_id": "graph-1", "inputs": {"order_id": "X"},
                      "outputs": {"trigger-1": {}}},
        summary="订单 X 退款审批", approver="主管",
    )
    make_frame_sink(engine, TENANT, "run-1")(frame)

    frames = load_pending_frames(engine)
    assert len(frames) == 1
    loaded = frames[0]
    assert loaded["resume_token"] == "tok-1"
    assert loaded["tenant_id"] == TENANT
    assert loaded["run_id"] == "run-1"
    assert loaded["node_id"] == "human-1"
    assert loaded["kind"] == "approval"
    assert loaded["summary"] == "订单 X 退款审批"
    assert loaded["resume_state"]["inputs"]["order_id"] == "X"
    assert loaded["graph_snapshot"]["nodes"] == []

    # 幂等：同 token 重写不新增行
    make_frame_sink(engine, TENANT, "run-1")(frame)
    assert len(load_pending_frames(engine)) == 1

    clear_frame(engine, "tok-1")
    assert load_pending_frames(engine) == []
