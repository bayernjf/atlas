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


def test_expired_session_rejected_and_lazily_deleted(backend):
    from datetime import datetime, timedelta, timezone

    from atlas.iam.principals import Principal, Role

    store = backend.session_store()
    principal = Principal(
        tenant_id=TENANT, tenant_name="测试租户", username="expired-user",
        display_name="过期用户", role=Role.VIEWER,
    )
    token = store.issue(principal)
    past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    with backend.engine.begin() as conn:
        conn.execute(
            text("UPDATE iam_sessions SET expires_at = :exp WHERE token = :token"),
            {"exp": past, "token": token},
        )
    assert store.principal_for_token(token) is None
    with backend.engine.connect() as conn:
        remaining = conn.execute(
            text("SELECT 1 FROM iam_sessions WHERE token = :token"), {"token": token}
        ).first()
    assert remaining is None


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


def test_tool_calls_roundtrip(backend):
    """docs/28 §4.1 ⑧：monitoring_runs.tool_calls JSONB（迁移 008）持久化与读回。"""
    store = backend.monitoring_store(TENANT)
    record = store.record_run(
        graph_id="graph-tools", mode="stream", status="completed",
        started_at="2026-09-20T00:00:00+00:00", duration_ms=42.0, nodes=[],
        tool_calls=[
            {"node_id": "t1", "tool": "message/send", "duration_ms": 8.5,
             "action_status": "SUCCESS", "error_code": None},
            {"node_id": "t2", "tool": "http/request", "duration_ms": 3.0,
             "action_status": "FAILED", "error_code": "INVALID_PARAMETER"},
            {"node_id": "t3", "tool": "local-op", "duration_ms": 0.01,
             "action_status": "SIMULATED", "error_code": None},
        ],
    )
    loaded = next(r for r in store.list_runs(graph_id="graph-tools") if r.id == record.id)
    assert [c.tool for c in loaded.tool_calls] == ["message/send", "http/request", "local-op"]
    failed = loaded.tool_calls[1]
    assert failed.action_status == "FAILED" and failed.error_code == "INVALID_PARAMETER"
    assert loaded.tool_calls[2].action_status == "SIMULATED"
    # 历史行/无工具运行读回为空列表（迁移 008 DEFAULT '[]'）
    plain = store.record_run(
        graph_id="graph-tools", mode="sync", status="completed",
        started_at="2026-09-20T01:00:00+00:00", duration_ms=1.0, nodes=[],
    )
    assert next(r for r in store.list_runs(graph_id="graph-tools") if r.id == plain.id).tool_calls == []
    store.reset()


def test_custom_rules_roundtrip(backend):
    """docs/28 §4.2 ⑨：自定义规则随 monitoring_rules.config JSONB 往返（无 DDL），
    custom:{cid} 告警 rule_id 为 TEXT 可落库。"""
    store = backend.monitoring_store(TENANT)
    store.update_rules({
        "run_error": {"enabled": True},
        "node_failed": {"enabled": False},
        "consecutive_failures": {"enabled": True, "threshold": 3},
        "failure_rate": {"enabled": True, "window": 20, "min_samples": 5, "rate": 0.5},
        "custom": [
            {"cid": "cid-1", "name": "错误即告警",
             "expression": "{{status}} == 'error' || {{hasError}}", "severity": "critical"},
        ],
    })
    rules = store.get_rules()
    assert len(rules.custom) == 1
    assert rules.custom[0].cid == "cid-1"
    assert rules.custom[0].expression == "{{status}} == 'error' || {{hasError}}"
    # 触发一次 error 运行 → custom:cid-1 落 monitoring_alerts（rule_id TEXT）
    store.record_run(
        graph_id="graph-custom", mode="sync", status="error",
        started_at="2026-09-20T00:00:00+00:00", duration_ms=10.0, nodes=[],
        error="boom",
    )
    alerts = [a for a in store.list_alerts() if a.rule_id == "custom:cid-1"]
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"
    # 旧配置（无 custom 段）写回后缺省为空，不报错
    store.update_rules({
        "run_error": {"enabled": True}, "node_failed": {"enabled": True},
        "consecutive_failures": {"enabled": True, "threshold": 3},
        "failure_rate": {"enabled": True, "window": 20, "min_samples": 5, "rate": 0.5},
    })
    assert store.get_rules().custom == []
    store.reset()


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



def test_u211_subgraph_upgrade_plan_pg_roundtrip(backend):
    """docs/28 §5.2 ⑪：PG 档发布快照 JSON 往返后升级体检 from→to 正确（只读不产版本）。"""
    from atlas.versioning.publish import publish as publish_version
    from atlas.versioning.upgrades import subgraph_upgrade_plan

    store = backend.graph_store(TENANT)
    store.clear()
    sub_tool = {"id": "s", "type": "tool", "config": {}}
    sub = store.save({"nodes": [sub_tool], "edges": []})
    publish_version(store, sub)  # v1
    store.update_draft(sub, {"nodes": [{"id": "s2", "type": "tool", "config": {}}], "edges": []})
    publish_version(store, sub)  # v2
    parent = store.save(
        {"nodes": [{"id": "n1", "type": "subgraph", "config": {"graphId": sub}}], "edges": []}
    )
    # 父图首次发布前：首次钉版 to=2
    pre = subgraph_upgrade_plan(store, parent)
    assert pre == [
        {"node_id": "n1", "sub_id": sub, "from_version": None,
         "to_version": 2, "first_pin": True}
    ]
    publish_version(store, parent)  # parent v1 钉 sub@2
    # 无变化 → 空
    assert subgraph_upgrade_plan(store, parent) == []
    # 子图发 v3 → 升级 from 2 to 3，且体检不产生任何版本
    store.update_draft(sub, {"nodes": [{"id": "s3", "type": "tool", "config": {}}], "edges": []})
    publish_version(store, sub)  # v3
    versions_before = store.list_versions(parent)
    plan = subgraph_upgrade_plan(store, parent)
    assert plan == [
        {"node_id": "n1", "sub_id": sub, "from_version": 2,
         "to_version": 3, "first_pin": False}
    ]
    assert store.list_versions(parent) == versions_before  # 只读
    # 草稿不存在 → None
    assert subgraph_upgrade_plan(store, "graph-404") is None
    store.clear()


def test_u402_event_wait_recovery_releases_after_restart(backend):
    """docs/53 §5 U402：event 帧 → 恢复装配 restore + 续跑线程 → 信号放行 → run completed、帧清。"""
    import time

    from atlas.api.main import _resume_from_frame
    from atlas.graph.dsl import parse_graph
    from atlas.iam.deps import tenant_registry
    from atlas.storage.frame import build_frame, deadline_iso
    from atlas.storage.recovery import load_pending_frames, make_frame_sink

    engine = backend.engine
    _cleanup(engine)

    # 后继 tool 名不含 "/"，执行走 SIMULATED 兜底（loader 不查 registry）。
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "wait-1", "type": "wait", "name": "等待",
                 "position": {"x": 2, "y": 0},
                 "config": {"waitType": "event", "eventKey": "order_paid",
                            "timeoutSeconds": 30, "onTimeout": "continue"}},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "wait-1"},
                {"id": "e2", "source": "wait-1", "target": "tool-after"},
            ],
        }
    )

    run_id, token = "run-u402", "wait-u402"
    services = tenant_registry.get(TENANT)
    services.event_wait_broker.reset()  # 模拟重启后的空 broker

    # 首进程：run running→suspended，帧落 interruptions。
    services.run_store.begin(run_id=run_id, graph_id="adhoc", mode="run")
    frame = build_frame(
        token=token,
        run_id=run_id,
        node_id="wait-1",
        kind="wait",
        deadline_at=deadline_iso(30),
        graph_snapshot=graph.model_dump(),
        resume_state={"graph_id": "adhoc", "inputs": {},
                      "outputs": {"trigger-1": {"context": {"payload": {}}}}},
        wait={"waitType": "event", "eventKey": "order_paid",
              "onTimeout": "continue", "timeoutSeconds": 30},
    )
    services.run_store.suspend(
        run_id=run_id, node_id="wait-1", kind="wait",
        resume_token=token, deadline_at=frame["deadline_at"],
    )
    make_frame_sink(engine, TENANT, run_id)(frame)
    loaded = load_pending_frames(engine)
    assert len(loaded) == 1

    # 重启恢复装配：restore 同 token/event_key/剩余超时 + 起续跑线程。
    _resume_from_frame(engine, loaded[0])
    broker = services.event_wait_broker
    assert token in [item["token"] for item in broker.list_pending()]

    # 信号广播放行续跑线程。
    assert broker.signal_key("order_paid", {"paidAt": "2026-09-23"}) == {
        "released": 1, "queued": False
    }

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and load_pending_frames(engine):
        time.sleep(0.02)
    assert load_pending_frames(engine) == []
    run = services.run_store.get(run_id)
    assert run["status"] == "completed"
    assert run["outputs"]["wait-1"]["resolvedBy"] == "signal"
    assert run["outputs"]["wait-1"]["signaled"] is True
    assert "tool-after" in run["outputs"]
    assert broker.list_pending() == []


def test_event_wait_multi_key_recovery_releases_on_nonprimary(backend):
    """docs/54（竞速 PG 超集，U530）：多键 eventKeys 帧 → restore 对每个键挂同一 token，
    对**非首键**广播也放行续跑；产出 matchedEventKey=命中的非首键、eventKeys 全量、帧清。"""
    import time

    from atlas.api.main import _resume_from_frame
    from atlas.graph.dsl import parse_graph
    from atlas.iam.deps import tenant_registry
    from atlas.storage.frame import build_frame, deadline_iso
    from atlas.storage.recovery import load_pending_frames, make_frame_sink

    keys = ["order_paid", "order_cancelled", "review_left"]
    engine = backend.engine
    _cleanup(engine)

    # 多键 config（与 eventKey 互斥，只用 eventKeys）；编译期即合法。
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "wait-1", "type": "wait", "name": "等待",
                 "position": {"x": 2, "y": 0},
                 "config": {"waitType": "event", "eventKeys": list(keys),
                            "timeoutSeconds": 30, "onTimeout": "continue"}},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "wait-1"},
                {"id": "e2", "source": "wait-1", "target": "tool-after"},
            ],
        }
    )

    run_id, token = "run-u530mk", "wait-u530mk"
    services = tenant_registry.get(TENANT)
    services.event_wait_broker.reset()  # 模拟重启后的空 broker

    services.run_store.begin(run_id=run_id, graph_id="adhoc", mode="run")
    # 帧始终保留 eventKey=首键，多键另存 eventKeys（loader/frame 契约）。
    frame = build_frame(
        token=token,
        run_id=run_id,
        node_id="wait-1",
        kind="wait",
        deadline_at=deadline_iso(30),
        graph_snapshot=graph.model_dump(),
        resume_state={"graph_id": "adhoc", "inputs": {},
                      "outputs": {"trigger-1": {"context": {"payload": {}}}}},
        wait={"waitType": "event", "eventKey": keys[0], "eventKeys": list(keys),
              "onTimeout": "continue", "timeoutSeconds": 30},
    )
    services.run_store.suspend(
        run_id=run_id, node_id="wait-1", kind="wait",
        resume_token=token, deadline_at=frame["deadline_at"],
    )
    make_frame_sink(engine, TENANT, run_id)(frame)
    loaded = load_pending_frames(engine)
    assert len(loaded) == 1

    _resume_from_frame(engine, loaded[0])
    broker = services.event_wait_broker
    pending = {item["token"]: item for item in broker.list_pending()}
    assert token in pending
    # restore 多键：首键 + 全量 eventKeys。
    assert pending[token]["eventKey"] == keys[0]
    assert pending[token]["eventKeys"] == keys

    # 对非首键广播：每个键都挂了同一 token，应恰好释放 1 条。
    assert broker.signal_key("review_left", {"stars": 5}) == {"released": 1, "queued": False}
    # 余键订阅已清理（首决后不再残留）；docs/55：无消费者信号改入排队 ring。
    assert broker.signal_key("order_paid", {}) == {"released": 0, "queued": True}

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and load_pending_frames(engine):
        time.sleep(0.02)
    assert load_pending_frames(engine) == []
    run = services.run_store.get(run_id)
    assert run["status"] == "completed"
    out = run["outputs"]["wait-1"]
    assert out["resolvedBy"] == "signal"
    assert out["signaled"] is True
    assert out["eventKey"] == keys[0]
    assert out["eventKeys"] == keys
    assert out["matchedEventKey"] == "review_left"
    assert out["payload"]["matchedEventKey"] == "review_left"
    assert "tool-after" in run["outputs"]
    assert broker.list_pending() == []



# --- docs/55：PG 档告警 lifecycle 三列 / recovery streak / merge·resolve 通知 / flapping 冷却 ---
class _SpyLifecycleNotifier:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _delivery():
        from atlas.monitoring.notify import AlertChannelDelivery

        return AlertChannelDelivery(lastNotifiedAt="2026-09-24T00:00:00+00:00")

    def notify(self, alert, cfg):
        self.calls.append(("new", alert.rule_id))
        return self._delivery()

    def notify_lifecycle(self, alert, cfg, *, transition):
        self.calls.append((transition, alert.rule_id))
        return self._delivery()


def _rules(streak=1, cooldown=None, escalation=None, custom=None):
    raw = {
        "run_error": {"enabled": True},
        "node_failed": {"enabled": False},
        "consecutive_failures": {"enabled": True, "threshold": 3},
        "failure_rate": {"enabled": True, "window": 20, "min_samples": 5, "rate": 0.5},
        "recovery_healthy_streak": streak,
        "recovery_cooldown_minutes": cooldown,
    }
    if escalation is not None:
        raw["escalation_ack_minutes"] = escalation
    if custom is not None:
        raw["custom"] = custom
    return raw


def test_U570_pg_alert_rule_name_and_escalated_at_persisted(backend):
    from sqlalchemy import text

    store = backend.monitoring_store(TENANT)
    store.update_rules(_rules(
        escalation=1,
        custom=[{"cid": "c1", "name": "错误即告警",
                 "expression": "{{status}} == 'error'", "severity": "warning"}],
    ))
    store.record_run(graph_id="g-esc", mode="sync", status="error",
                     started_at="2026-09-24T00:00:00+00:00", duration_ms=5.0,
                     nodes=[], error="boom")
    alert = next(a for a in store.list_alerts() if a.rule_id == "custom:c1")
    assert alert.rule_name == "错误即告警"  # rule_name 列往返
    # 把 first_seen 拨到超过升级窗口，再次读列表应惰性升级并回写 escalated_at/severity
    with backend.engine.begin() as conn:
        conn.execute(
            text("UPDATE monitoring_alerts SET first_seen = :old "
                 "WHERE tenant_id = :t AND id = :id"),
            {"old": "2026-09-20T00:00:00+00:00", "t": TENANT, "id": alert.id},
        )
    upgraded = next(a for a in store.list_alerts() if a.id == alert.id)
    assert upgraded.severity == "critical" and upgraded.escalated_at is not None
    with backend.engine.connect() as conn:
        row = conn.execute(
            text("SELECT severity, escalated_at FROM monitoring_alerts WHERE id = :id"),
            {"id": alert.id},
        ).first()
    assert row[0] == "critical" and row[1] is not None  # 已回写 PG
    store.reset()


def test_U571_pg_recovery_requires_consecutive_healthy_streak(backend):
    store = backend.monitoring_store(TENANT)
    store.update_rules(_rules(streak=2))
    store.record_run(graph_id="g-rec", mode="sync", status="error",
                     started_at="2026-09-24T00:00:00+00:00", duration_ms=5.0,
                     nodes=[], error="boom")
    def _open():
        return [a for a in store.list_alerts(status="open") if a.rule_id == "run_error"]
    store.record_run(graph_id="g-rec", mode="sync", status="completed",
                     started_at="2026-09-24T00:01:00+00:00", duration_ms=5.0, nodes=[])
    assert _open()  # 连续健康仅 1，不恢复
    store.record_run(graph_id="g-rec", mode="sync", status="completed",
                     started_at="2026-09-24T00:02:00+00:00", duration_ms=5.0, nodes=[])
    assert not _open()  # 连续健康达 2，自动恢复
    resolved = [a for a in store.list_alerts(status="resolved") if a.rule_id == "run_error"]
    assert resolved
    store.reset()


def test_U572_pg_merge_and_manual_resolve_emit_lifecycle(backend):
    store = backend.monitoring_store(TENANT)
    spy = _SpyLifecycleNotifier()
    store.set_notifier(spy)
    store.update_rules(_rules())
    common = dict(graph_id="g-life", mode="sync", status="error",
                  started_at="2026-09-24T00:00:00+00:00", duration_ms=5.0,
                  nodes=[], error="boom")
    store.record_run(**common)
    store.record_run(**dict(common, started_at="2026-09-24T00:01:00+00:00"))  # merge
    transitions = [c[0] for c in spy.calls if c[1] == "run_error"]
    assert "new" in transitions and "merged" in transitions
    alert = next(a for a in store.list_alerts() if a.rule_id == "run_error")
    assert alert.count == 2
    store.resolve_alert(alert.id)
    assert ("resolved", "run_error") in spy.calls  # 手动 resolve 发 lifecycle
    store.reset()


def test_U573_pg_cooldown_suppresses_new_notify_but_keeps_alert(backend):
    store = backend.monitoring_store(TENANT)
    spy = _SpyLifecycleNotifier()
    store.set_notifier(spy)
    store.update_rules(_rules(cooldown=60))
    err = dict(graph_id="g-cool", mode="sync", status="error",
               started_at="2026-09-24T00:00:00+00:00", duration_ms=5.0,
               nodes=[], error="boom")
    ok = dict(graph_id="g-cool", mode="sync", status="completed",
              started_at="2026-09-24T00:01:00+00:00", duration_ms=5.0, nodes=[])
    store.record_run(**err)   # new 外发
    store.record_run(**ok)    # 自动 recovery，写冷却
    store.record_run(**dict(err, started_at="2026-09-24T00:02:00+00:00"))  # 冷却内 new 抑制
    new_calls = [c for c in spy.calls if c == ("new", "run_error")]
    assert len(new_calls) == 1  # 仅首次 new 外发
    assert [a for a in store.list_alerts(status="open") if a.rule_id == "run_error"]  # 站内照建
    store.reset()


# --- docs/56 §2：发布报告 PG 沉淀（迁移 022，PgReportStore 同形）---
def test_U580_pg_report_store_roundtrip_and_isolation(backend):
    from atlas.recording.pg_reports import PgReportStore

    store = PgReportStore(backend.engine, TENANT)
    other = PgReportStore(backend.engine, "tenant-other")

    gate_a = {
        "target": "draft", "total": 2, "passed": 1, "failed": 1,
        "skipped": False, "blocked": True,
        "cases": [
            {"case_id": "c1", "name": "退款成功", "matches": True,
             "replay_status": "matched", "note": None},
            {"case_id": "c2", "name": "退款失败", "matches": False,
             "replay_status": "mismatch", "note": "金额不符"},
        ],
    }
    r1 = store.record(graph_id="g-rr", trigger="manual", report=gate_a)
    assert r1["id"].startswith("rr-")
    assert r1["pass_rate"] == 0.5 and r1["blocked"] is True
    # total=0（未覆盖）→ pass_rate None、skipped True
    r2 = store.record(graph_id="g-rr", trigger="publish-gate",
                      report={"total": 0, "passed": 0, "failed": 0})
    assert r2["pass_rate"] is None and r2["skipped"] is True
    store.record(graph_id="g-other-graph", trigger="manual", report=gate_a)

    # 摘要倒序、不含 cases 键
    summary = store.list_summary("g-rr")
    assert [r["id"] for r in summary] == [r2["id"], r1["id"]]
    assert all("cases" not in r for r in summary)

    # 跨图摘要 clamp 1-200
    all_summary = store.list_all_summary(limit=1)
    assert len(all_summary) == 1 and "cases" not in all_summary[0]
    assert store.list_all_summary(limit=99999) and len(store.list_all_summary(limit=2)) <= 2

    # 详情含 cases；跨图/不存在 get 返 None
    detail = store.get("g-rr", r1["id"])
    assert detail is not None and len(detail["cases"]) == 2
    assert detail["cases"][1]["note"] == "金额不符"
    assert store.get("g-other-graph", r1["id"]) is None
    assert store.get("g-rr", "rr-does-not-exist") is None

    # 租户隔离：另一租户看不到
    assert other.list_summary("g-rr") == []

    # reset 只删本租户
    store.reset()
    assert store.list_summary("g-rr") == []
    assert other.list_summary("g-rr") == []  # other 本就空
    other.record(graph_id="g-rr", trigger="manual", report=gate_a)
    store.reset()
    assert len(other.list_summary("g-rr")) == 1  # 本租户 reset 不影响他租户
    other.reset()


def test_u809_frame_resume_claim_is_one_time(backend):
    """docs/62 §5 U809：互斥由**真库**裁决——同 token 并发 20 个认领恰一个 True，之后恒 False。

    这条不能由自写假 store 代替：假对象只会重复实现作者已经想到的语义，
    证不了 `UPDATE ... WHERE resumed_at IS NULL` 在 PG 行锁下真的串行（§0 教训）。
    """
    import threading

    from atlas.storage.frame import build_frame, deadline_iso
    from atlas.storage.recovery import (
        PROCESS_IDENTITY,
        claim_frame_for_resume,
        load_pending_frames,
        make_frame_sink,
    )

    engine = backend.engine
    _cleanup(engine)
    frame = build_frame(
        token="tok-claim",
        run_id="run-claim",
        node_id="human-1",
        kind="approval",
        deadline_at=deadline_iso(30),
        graph_snapshot={"version": 1, "nodes": [], "edges": []},
        resume_state={"graph_id": "g-claim", "inputs": {}, "outputs": {}},
        summary="订单退款审批",
        approver="客服主管",
    )
    make_frame_sink(engine, TENANT, "run-claim")(frame)

    # 写帧只落 NULL＝还没人越过这个挂起点（docs/62 §3.4 第 4 行）。
    loaded = load_pending_frames(engine)
    assert len(loaded) == 1
    assert loaded[0]["resumed_at"] is None
    assert loaded[0]["resumed_by"] is None

    won: list[bool] = []
    gate = threading.Event()

    def drive() -> None:
        gate.wait(2)
        won.append(claim_frame_for_resume(engine, "tok-claim"))

    threads = [threading.Thread(target=drive) for _ in range(20)]
    for thread in threads:
        thread.start()
    gate.set()
    for thread in threads:
        thread.join(timeout=5)

    assert len(won) == 20
    assert sum(won) == 1, "恰一个进程可越过挂起点；多一个＝双跑，少一个＝门焊死"

    # 重复认领恒 False（at-most-once：帧一旦被消费，任何进程都不再有权执行其下游）。
    assert claim_frame_for_resume(engine, "tok-claim") is False
    # 不存在的 token 也判 False：缺行＝停止驱动，绝不因"查不到"而放行。
    assert claim_frame_for_resume(engine, "tok-never-written") is False

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT resumed_at, resumed_by FROM interruptions "
                "WHERE resume_token = 'tok-claim'"
            )
        ).one()
    assert row[0] is not None, "DB 时钟落库（不用应用侧 now_iso，docs/62 §2 D-3）"
    assert row[1] == PROCESS_IDENTITY


def test_u809b_recovery_skips_claimed_but_not_unclaimed_frames(backend):
    """docs/62 §8.1 第②种：已认领帧不再重建 pending/起续跑线程；未认领帧必须照旧恢复。

    两条方向都断言，否则"跳过"可能只是门焊死——U810b（重启仍须能续跑）的可执行答辩。
    """
    import time

    from atlas.api.main import _resume_from_frame
    from atlas.graph.dsl import parse_graph
    from atlas.iam.deps import tenant_registry
    from atlas.storage.frame import build_frame, deadline_iso
    from atlas.storage.recovery import (
        claim_frame_for_resume,
        load_pending_frames,
        make_frame_sink,
    )

    engine = backend.engine
    _cleanup(engine)
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "wait-1", "type": "wait", "name": "等待",
                 "position": {"x": 2, "y": 0},
                 "config": {"waitType": "event", "eventKey": "order_paid",
                            "timeoutSeconds": 30, "onTimeout": "continue"}},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "wait-1"},
                {"id": "e2", "source": "wait-1", "target": "tool-after"},
            ],
        }
    )
    services = tenant_registry.get(TENANT)
    services.event_wait_broker.reset()

    def _event_frame(token: str) -> dict:
        return build_frame(
            token=token,
            run_id=f"run-{token}",
            node_id="wait-1",
            kind="wait",
            deadline_at=deadline_iso(30),
            graph_snapshot=graph.model_dump(),
            resume_state={"graph_id": "adhoc", "inputs": {},
                          "outputs": {"trigger-1": {"context": {"payload": {}}}}},
            wait={"waitType": "event", "eventKey": "order_paid",
                  "onTimeout": "continue", "timeoutSeconds": 30},
        )

    for token in ("tok-consumed", "tok-live"):
        frame = _event_frame(token)
        services.run_store.begin(run_id=frame["run_id"], graph_id="adhoc", mode="run")
        services.run_store.suspend(
            run_id=frame["run_id"], node_id="wait-1", kind="wait",
            resume_token=token, deadline_at=frame["deadline_at"],
        )
        make_frame_sink(engine, TENANT, frame["run_id"])(frame)

    # 模拟"另一个进程已越过挂起点后崩溃"：帧已 claimed，行仍在。
    assert claim_frame_for_resume(engine, "tok-consumed") is True
    loaded = {f["resume_token"]: f for f in load_pending_frames(engine)}
    assert set(loaded) == {"tok-consumed", "tok-live"}

    _resume_from_frame(engine, loaded["tok-consumed"])
    pending = [item["token"] for item in services.event_wait_broker.list_pending()]
    assert "tok-consumed" not in pending, "已认领帧不得重建 pending（否则起僵尸续跑线程）"
    assert services.run_store.get("run-tok-consumed")["status"] == "suspended", (
        "卡住是设计选择：不自动重放，也不改判终态（收敛靠 D36）"
    )

    # 反向：未认领帧照旧恢复并跑完（门没有被焊死）。
    _resume_from_frame(engine, loaded["tok-live"])
    pending = [item["token"] for item in services.event_wait_broker.list_pending()]
    assert "tok-live" in pending

    assert services.event_wait_broker.signal_key("order_paid", {"paidAt": "2026-09-25"})[
        "released"
    ] == 1
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and load_pending_frames(engine):
        time.sleep(0.02)
    live = services.run_store.get("run-tok-live")
    assert live["status"] == "completed"
    assert "tool-after" in live["outputs"]
    assert [f["resume_token"] for f in load_pending_frames(engine)] == ["tok-consumed"]
    services.event_wait_broker.reset()


def test_u809c_reverse_control_the_null_predicate_is_what_serializes(backend):
    """反向对照（docs/62 §5）：同一条 UPDATE 去掉 `resumed_at IS NULL` 后每次都命中 1 行。

    不去掉真代码里的谓词（那是拆闸门），而是并排跑两种 SQL 证明：**承重的正是那个谓词**，
    不是 rowcount 的读数方式。少了这条对照，U809 的"恰一个 True"可能只是 rowcount 语义的巧合。
    """
    from sqlalchemy import text

    from atlas.memory.database import create_database_engine
    from atlas.storage.frame import build_frame, deadline_iso
    from atlas.storage.recovery import claim_frame_for_resume, make_frame_sink

    engine = backend.engine
    _cleanup(engine)
    frame = build_frame(
        token="tok-pred",
        run_id="run-pred",
        node_id="human-1",
        kind="approval",
        deadline_at=deadline_iso(30),
        graph_snapshot={"version": 1, "nodes": [], "edges": []},
        resume_state={"graph_id": "g", "inputs": {}, "outputs": {}},
    )
    make_frame_sink(engine, TENANT, "run-pred")(frame)

    # 真函数：第一次 True，其后恒 False。
    assert claim_frame_for_resume(engine, "tok-pred") is True
    assert claim_frame_for_resume(engine, "tok-pred") is False

    # 同一行、同一条语句、只去掉谓词：每次都是 rowcount 1 ＝ 每个进程都自认赢家。
    with engine.begin() as conn:
        first = conn.execute(
            text("UPDATE interruptions SET resumed_at = CURRENT_TIMESTAMP "
                 "WHERE resume_token = 'tok-pred'")
        )
        second = conn.execute(
            text("UPDATE interruptions SET resumed_at = CURRENT_TIMESTAMP "
                 "WHERE resume_token = 'tok-pred'")
        )
    assert first.rowcount == 1 and second.rowcount == 1, (
        "rowcount 本身区分不了赢家；若这里出现 0，说明测试环境不是 PG 行锁语义，U809 的结论不成立"
    )
