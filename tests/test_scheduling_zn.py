# -*- coding: utf-8 -*-
"""打包 ZN（docs/08 打包 ZN 立项块，2026-10-03；D41 ③④）：一图多调度＋调度级并发策略。

U1069–U1076：多定时节点发布派生 N 条 run（schedule_id＝节点 id、各自 cron）＋一条图级
reflect（`__reflect__`）；发布全量 reconcile（删节点即撤销、幂等、钉版稳定）；并发策略
overlap_policy：skip（v1 默认，busy 即跳过）/allow（busy 也并发派发）。端到端走 REST 发布，
allow/skip 的引擎分支走纯 tick（零 IO）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from atlas.api.main import app, schedule_store
from atlas.iam.deps import session_store

client = TestClient(app)


def _auth(username: str, password: str) -> dict[str, str]:
    from atlas.iam.principals import authenticate

    principal = authenticate(username, password)
    assert principal is not None
    return {"Authorization": f"Bearer {session_store.issue(principal)}"}


ADMIN_A = _auth("admin-a", "admin123")
OPERATOR_A = _auth("operator-a", "operator123")


def _graph_multi(
    crons: list[str], *, ids: list[str] | None = None,
    overlaps: list[str] | None = None,
) -> dict:
    ids = ids or [f"trg{i}" for i in range(len(crons))]
    nodes: list[dict] = []
    for i, (cid, cron) in enumerate(zip(ids, crons)):
        cfg: dict = {"triggerType": "schedule", "cron": cron}
        if overlaps and overlaps[i]:
            cfg["overlapPolicy"] = overlaps[i]
        nodes.append({"id": cid, "type": "trigger", "name": cid,
                      "position": {"x": i, "y": 0}, "config": cfg})
    nodes.append({"id": "a1", "type": "ai_decision", "name": "决策",
                  "position": {"x": 0, "y": 1},
                  "config": {"promptTemplate": "要不要退款"}})
    edges = [
        {"id": f"e{i}", "source": cid, "target": "a1"} for i, cid in enumerate(ids)
    ]
    return {"version": 1, "name": "多调度冒烟", "nodes": nodes, "edges": edges}


def _publish(body: dict) -> tuple[str, int]:
    created = client.post("/api/graphs", json=body, headers=ADMIN_A)
    assert created.status_code == 200, created.text
    graph_id = created.json()["id"]
    published = client.post(f"/api/graphs/{graph_id}/publish", headers=ADMIN_A)
    assert published.status_code == 200, published.text
    return graph_id, int(published.json()["releaseVersion"])


def _rows(graph_id: str) -> list[dict]:
    items = client.get("/api/schedules", headers=ADMIN_A).json()["items"]
    return [i for i in items if i["graphId"] == graph_id]


def _republish(graph_id: str, body: dict) -> int:
    assert client.put(f"/api/graphs/{graph_id}", json=body, headers=ADMIN_A).status_code == 200
    published = client.post(f"/api/graphs/{graph_id}/publish", headers=ADMIN_A)
    assert published.status_code == 200, published.text
    return int(published.json()["releaseVersion"])


import pytest


@pytest.fixture(autouse=True)
def _clean():
    yield
    schedule_store().reset_tenant("t1")


# --- U1069 多定时节点派生 N 条 run（各自 cron）＋一条 reflect ----------------

def test_u1069_multiple_trigger_nodes_derive_one_run_each_and_one_reflect():
    graph_id, version = _publish(_graph_multi(["*/5 * * * *", "0 9 * * *"]))
    rows = _rows(graph_id)
    run_rows = [r for r in rows if r["action"] == "run"]
    assert {r["scheduleId"] for r in run_rows} == {"trg0", "trg1"}
    cron_by = {r["scheduleId"]: r["cron"] for r in run_rows}
    assert cron_by == {"trg0": "*/5 * * * *", "trg1": "0 9 * * *"}
    assert all(r["version"] == version for r in run_rows)
    # overlap_policy 缺省 skip（投影带键）
    assert all(r["overlapPolicy"] == "skip" for r in run_rows)

    reflect_rows = [r for r in rows if r["action"] == "reflect"]
    assert len(reflect_rows) == 1, "反思是图级 pass，不随定时节点倍增"
    assert reflect_rows[0]["scheduleId"] == "__reflect__"


# --- U1070 删节点重发布：对应 schedule_id 被 reconcile 撤销 ------------------

def test_u1070_removing_a_trigger_node_revokes_only_that_schedule():
    graph_id, _ = _publish(_graph_multi(["*/5 * * * *", "0 9 * * *"]))
    assert {r["scheduleId"] for r in _rows(graph_id) if r["action"] == "run"} == {
        "trg0", "trg1"
    }
    # 更新成只含 trg0（trg1 节点被删）
    _republish(graph_id, _graph_multi(["*/5 * * * *"], ids=["trg0"]))
    run_ids = {r["scheduleId"] for r in _rows(graph_id) if r["action"] == "run"}
    assert run_ids == {"trg0"}, "删掉的定时节点对应调度必须撤销，否则对老 cron 空跑"
    assert len([r for r in _rows(graph_id) if r["action"] == "reflect"]) == 1


# --- U1071 reconcile 幂等：重复发布不增不减、开关保留 -----------------------

def test_u1071_republishing_the_same_shape_is_idempotent_and_keeps_switches():
    graph_id, _ = _publish(_graph_multi(["*/5 * * * *", "0 9 * * *"]))
    # 关掉 trg0
    assert client.post(
        f"/api/schedules/{graph_id}/enabled",
        json={"enabled": False, "schedule_id": "trg0"}, headers=OPERATOR_A,
    ).status_code == 200
    # 原样重发布
    _republish(graph_id, _graph_multi(["*/5 * * * *", "0 9 * * *"]))
    rows = _rows(graph_id)
    assert {r["scheduleId"] for r in rows if r["action"] == "run"} == {"trg0", "trg1"}
    trg0 = next(r for r in rows if r["scheduleId"] == "trg0")
    assert trg0["enabled"] is False, "reconcile 把运营者的开关翻回来了"


# --- U1072 schedule_id 随图钉版稳定（节点 id 不变、版本号更新） -------------

def test_u1072_schedule_id_stays_stable_across_versions_while_version_advances():
    graph_id, v1 = _publish(_graph_multi(["*/5 * * * *"]))
    v2 = _republish(graph_id, _graph_multi(["15 3 * * *"], ids=["trg0"]))
    assert v2 > v1
    rows = _rows(graph_id)
    run_rows = [r for r in rows if r["action"] == "run"]
    assert len(run_rows) == 1
    assert run_rows[0]["scheduleId"] == "trg0", "节点 id 不变，schedule_id 必须稳定"
    assert run_rows[0]["version"] == v2 and run_rows[0]["cron"] == "15 3 * * *"


# --- U1073/U1074 overlap_policy：allow 并发派发、skip 默认跳过（引擎级） ----

def _engine_record(overlap: str):
    from atlas.scheduling.models import ScheduleRecord
    return ScheduleRecord(
        tenant_id="t1", graph_id="g1", schedule_id="trg0",
        version=2, cron="*/5 * * * *", overlap_policy=overlap,
        created_at="2026-09-26T00:00:00+00:00",
    )


class _Recorder:
    def __init__(self) -> None:
        self.claims: list[tuple] = []
        self.dispatched = 0

    def claim(self, record, slot) -> bool:
        key = (record.tenant_id, record.graph_id, record.schedule_id,
               slot.replace(second=0, microsecond=0).isoformat())
        if key in self.claims:
            return False
        self.claims.append(key)
        return True

    def dispatch(self, record, slot) -> None:
        self.dispatched += 1


def test_u1073_allow_policy_dispatches_even_while_busy():
    from atlas.scheduling.engine import ACTION_FIRED, tick
    slot = datetime(2026, 9, 26, 10, 5, 30, tzinfo=timezone.utc)
    recorder = _Recorder()
    out = tick(slot, [_engine_record("allow")], recorder.claim, recorder.dispatch,
               busy=lambda _: True)
    assert [o.action for o in out] == [ACTION_FIRED], "allow 必须无视 busy 并发派发"
    assert recorder.dispatched == 1 and len(recorder.claims) == 1


def test_u1074_skip_default_still_skips_while_busy():
    from atlas.scheduling.engine import ACTION_SKIPPED_OVERLAP, tick
    slot = datetime(2026, 9, 26, 10, 5, 30, tzinfo=timezone.utc)
    recorder = _Recorder()
    out = tick(slot, [_engine_record("skip")], recorder.claim, recorder.dispatch,
               busy=lambda _: True)
    assert [o.action for o in out] == [ACTION_SKIPPED_OVERLAP]
    assert recorder.dispatched == 0 and recorder.claims == []


# --- U1075 多节点也只一条 reflect ------------------------------------------

def test_u1075_three_triggers_still_yield_a_single_reflect_row():
    graph_id, _ = _publish(_graph_multi(["*/5 * * * *", "0 9 * * *", "30 18 * * *"]))
    reflect_rows = [r for r in _rows(graph_id) if r["action"] == "reflect"]
    assert len(reflect_rows) == 1 and reflect_rows[0]["scheduleId"] == "__reflect__"


# --- U1076 认领键不互吞：同图 N 条 run＋reflect 各认各槽 --------------------

def test_u1076_the_schedules_claim_the_same_slot_independently():
    graph_id, _ = _publish(_graph_multi(["*/5 * * * *", "0 9 * * *"]))
    store = schedule_store()
    moment = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    assert store.claim("t1", graph_id, "trg0", moment) is True
    assert store.claim("t1", graph_id, "trg1", moment) is True, "两条 run 互吃认领"
    assert store.claim("t1", graph_id, "__reflect__", moment, "reflect") is True, \
        "反思被 run 的认领吃掉"
    assert store.claim("t1", graph_id, "trg0", moment) is False
    assert store.claim("t1", graph_id, "trg1", moment) is False
    assert store.claim("t1", graph_id, "__reflect__", moment, "reflect") is False


# --- 边界：trigger 节点 id 撞保留 __ 命名空间则跳过，不建调度 ---------------

def test_trigger_node_id_in_reserved_namespace_is_skipped():
    graph_id, _ = _publish(_graph_multi(["*/5 * * * *"], ids=["__bad__"]))
    assert _rows(graph_id) == [], "撞保留命名空间的节点不应建任何调度（也不阻断发布）"
