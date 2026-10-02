# -*- coding: utf-8 -*-
"""定时调度的 REST 面与发布派生（docs/68 §4 U887–U891，打包 N）。

这里测的是"发布之后运营者看得见什么、改得动什么"：登记来自发布（不是另填一张表）、
重发布换版本但保留开关、`run-now` 不占槽位、越权与跨租户按现有 capability 口径。
派发判定本身（同槽一次/不补跑/重叠不消耗认领）在 `test_scheduling_engine.py`，
两档一致性在 `test_scheduling_pg_integration.py`。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app, schedule_store
from atlas.iam.deps import session_store, tenant_registry

client = TestClient(app)


def _auth(username: str, password: str) -> dict[str, str]:
    from atlas.iam.principals import authenticate

    principal = authenticate(username, password)
    assert principal is not None
    return {"Authorization": f"Bearer {session_store.issue(principal)}"}


ADMIN_A = _auth("admin-a", "admin123")
OPERATOR_A = _auth("operator-a", "operator123")
VIEWER_A = _auth("viewer-a", "viewer123")
ADMIN_B = _auth("admin-b", "admin123")

GRAPH_NAME = "定时调度 REST 冒烟"
ONE_MINUTE = timedelta(minutes=1)
FIVE_MINUTES = timedelta(minutes=5)


def _graph(cron: str = "*/5 * * * *", *, trigger_type: str = "schedule", name: str = GRAPH_NAME,
           timezone: str | None = None, catch_up_minutes: int | None = None,
           overlap_policy: str | None = None) -> dict:
    trigger_config: dict = {"triggerType": trigger_type, "cron": cron}
    if timezone is not None:
        trigger_config["timezone"] = timezone
    if catch_up_minutes is not None:
        trigger_config["catchUpMinutes"] = catch_up_minutes
    if overlap_policy is not None:
        trigger_config["overlapPolicy"] = overlap_policy
    return {
        "version": 1,
        "name": name,
        "nodes": [
            {"id": "t1n", "type": "trigger", "name": "定时",
             "position": {"x": 0, "y": 0},
             "config": trigger_config},
            {"id": "a1", "type": "ai_decision", "name": "决策",
             "position": {"x": 1, "y": 0}, "config": {"promptTemplate": "要不要退款"}},
        ],
        "edges": [{"id": "e1", "source": "t1n", "target": "a1"}],
    }


def _publish(body: dict) -> tuple[str, int]:
    created = client.post("/api/graphs", json=body, headers=ADMIN_A)
    assert created.status_code == 200, created.text
    graph_id = created.json()["id"]
    published = client.post(f"/api/graphs/{graph_id}/publish", headers=ADMIN_A)
    assert published.status_code == 200, published.text
    return graph_id, int(published.json()["releaseVersion"])


@pytest.fixture(autouse=True)
def _clean_schedules():
    """store 是全局的（不按租户装配的理由见 `scheduling/store.py`），用例之间必须归零。"""
    yield
    schedule_store().reset_tenant("t1")
    schedule_store().reset_tenant("t2")


@pytest.fixture()
def schedule_graph():
    graph_id, version = _publish(_graph())
    return graph_id, version


def _find(graph_id: str, headers=None, action: str = "run") -> dict | None:
    """取该图某动作的卡片。打包 ZH 起同图有 `run` 与 `reflect` 两条，必须点名动作。"""
    items = client.get("/api/schedules", headers=headers or ADMIN_A).json()["items"]
    return next(
        (item for item in items if item["graphId"] == graph_id and item["action"] == action), None
    )


def _await_run_terminal(run_id: str, *, timeout: float = 5.0) -> dict:
    """等 run-now 起的异步 run 离开 running 再读，消除"快照 vs 卡片"两次读之间的竞态。

    `run_store.get()` 返回的是**副本**，而卡片是端点里另一次 `run_store.list()` 现读；
    run 从 running 走到终态若发生在这两次读之间，两次读就会各说各话（曾导致 CI 偶发红）。
    等到终态后 run 不再变化，先读 run、再读卡片即确定一致。
    """
    store = tenant_registry.get("t1").run_store
    deadline = time.monotonic() + timeout
    run = store.get(run_id)
    while run is not None and run["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
        run = store.get(run_id)
    assert run is not None, "run-now 返回了 runId 但没有对应的 run"
    assert run["status"] != "running", "run 未在超时内离开 running"
    return run


# --- U887 发布派生 --------------------------------------------------------

def test_u887_publishing_a_scheduled_graph_registers_it_with_the_publish_version(schedule_graph):
    graph_id, version = schedule_graph
    item = _find(graph_id)
    assert item is not None, "发布后 GET /api/schedules 里没有这条调度"
    assert item["version"] == version
    assert item["cron"] == "*/5 * * * *"
    assert item["enabled"] is True
    assert item["skipCount"] == 0 and item["lastFiredAt"] is None
    # 下次触发时刻是算出来的，不是配置项：`*/5` 在 `*/5` 的格点上，且不早于现在
    upcoming = datetime.fromisoformat(item["nextFireAt"])
    now = datetime.now(timezone.utc)
    assert upcoming.tzinfo is not None
    assert now - ONE_MINUTE <= upcoming <= now + FIVE_MINUTES * 2
    assert upcoming.minute % 5 == 0 and upcoming.second == 0


def test_u887b_publishing_a_graph_without_a_schedule_trigger_registers_nothing():
    graph_id, _ = _publish(_graph(trigger_type="manual"))
    assert _find(graph_id) is None


def test_u887c_republishing_without_the_trigger_revokes_the_schedule(schedule_graph):
    graph_id, _ = schedule_graph
    assert _find(graph_id) is not None
    body = _graph(trigger_type="manual")
    assert client.put(f"/api/graphs/{graph_id}", json=body, headers=ADMIN_A).status_code == 200
    assert client.post(f"/api/graphs/{graph_id}/publish", headers=ADMIN_A).status_code == 200
    assert _find(graph_id) is None, "定时节点已被撤掉，调度还挂在册上会对着老 cron 空跑"


# --- U888 重发布换版本、保留开关 ------------------------------------------

def test_u888_republish_updates_version_and_cron_but_keeps_the_switch(schedule_graph):
    graph_id, _ = schedule_graph
    assert client.post(
        f"/api/schedules/{graph_id}/enabled",
        json={"enabled": False, "schedule_id": "t1n"}, headers=OPERATOR_A
    ).status_code == 200

    body = _graph(cron="15 3 * * *")
    assert client.put(f"/api/graphs/{graph_id}", json=body, headers=ADMIN_A).status_code == 200
    published = client.post(f"/api/graphs/{graph_id}/publish", headers=ADMIN_A)
    version = int(published.json()["releaseVersion"])

    item = _find(graph_id)
    assert item["version"] == version and version >= 2
    assert item["cron"] == "15 3 * * *"
    assert item["enabled"] is False, "重发布把运营者的开关翻回来了"


# --- U889 run-now 不占槽位 ------------------------------------------------

def test_u889_run_now_starts_a_pinned_run_without_consuming_the_slot_claim(schedule_graph):
    graph_id, version = schedule_graph
    response = client.post(f"/api/schedules/{graph_id}/run-now", headers=OPERATOR_A)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["graphId"] == graph_id and payload["version"] == version
    assert payload["runId"]

    services = tenant_registry.get("t1")
    run = services.run_store.get(payload["runId"])
    assert run is not None, "run-now 返回了 runId 但没有对应的 run"

    # 认领槽位必须还是空的：run-now 是"额外跑一次"，不是替本分钟交差。
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    assert schedule_store().claim("t1", graph_id, "t1n", now) is True
    assert schedule_store().claim("t1", graph_id, "t1n", now) is False, "认领表幂等性破了"


def test_u889d_schedule_card_carries_the_latest_run_status(schedule_graph):
    """docs/77 R7：卡片原先只有 lastFiredAt/lastSkippedAt，看不出"最近一次跑成没成"。

    handler 层 enrich 最近一次 run（按 graph_id 匹配，不区分 schedule/manual）；
    run-now 也是 run，所以它之后卡片必须带 lastRunStatus。
    """
    graph_id, _ = schedule_graph

    # 还没跑过：不该凭空造出 run 字段（否则前端无法区分"没跑过"与"跑了"）。
    before = _find(graph_id)
    assert before is not None
    assert "lastRunStatus" not in before and "lastRunId" not in before
    assert before["nextFireAt"], "enrich 不能把原有投影键挤掉"

    response = client.post(f"/api/schedules/{graph_id}/run-now", headers=OPERATOR_A)
    assert response.status_code == 200, response.text
    # run-now 起的 run 是异步的：先等它到终态，再读卡片比对（否则两次读之间 run 变状态会偶发红）。
    run = _await_run_terminal(response.json()["runId"])

    after = _find(graph_id)
    assert after["lastRunId"] == run["runId"]
    assert after["lastRunStatus"] == run["status"]
    assert after["lastRunStartedAt"] == run["startedAt"]


def test_u889b_run_now_is_409_while_the_graph_still_has_a_live_run(schedule_graph):
    graph_id, _ = schedule_graph
    services = tenant_registry.get("t1")
    services.run_store.begin(run_id="run-hanging", graph_id=graph_id, mode="schedule")
    try:
        response = client.post(f"/api/schedules/{graph_id}/run-now", headers=OPERATOR_A)
        assert response.status_code == 409
        assert "执行中" in response.json()["detail"]
    finally:
        services.run_store.finish(run_id="run-hanging", status="completed")


def test_u889c_unknown_graph_and_missing_schedule_are_404():
    assert client.post("/api/schedules/no-such-graph/run-now", headers=OPERATOR_A).status_code == 404
    assert client.post(
        "/api/schedules/no-such-graph/enabled",
        json={"enabled": True, "schedule_id": "t1n"}, headers=OPERATOR_A
    ).status_code == 404


# --- U890 权限与租户分区 --------------------------------------------------

def test_u890_anonymous_is_401_viewer_cannot_mutate_and_tenant_b_sees_nothing(schedule_graph):
    graph_id, _ = schedule_graph
    assert client.get("/api/schedules").status_code == 401
    assert client.get("/api/schedules", headers=VIEWER_A).status_code == 200
    assert client.post(
        f"/api/schedules/{graph_id}/enabled", json={"enabled": False}, headers=VIEWER_A
    ).status_code == 403
    assert client.post(
        f"/api/schedules/{graph_id}/run-now", headers=VIEWER_A
    ).status_code == 403

    assert client.get("/api/schedules", headers=ADMIN_B).json()["items"] == []
    assert client.post(
        f"/api/schedules/{graph_id}/run-now", headers=ADMIN_B
    ).status_code == 404, "跨租户要按不存在处理（404），而不是把别人的图跑起来"


def test_u890b_demo_reset_clears_the_tenant_schedules(schedule_graph):
    graph_id, _ = schedule_graph
    assert _find(graph_id) is not None
    assert client.post("/api/demo/reset", headers=ADMIN_A).status_code == 200
    assert _find(graph_id) is None


# --- U891 保存期校验（与既有 REQUIRED 并存） -------------------------------

@pytest.mark.parametrize("cron,code", [
    ("", "NODE_TRIGGER_CRON_REQUIRED"),
    ("5/2 * * * *", "NODE_TRIGGER_CRON_INVALID"),
    ("0 0 32 * *", "NODE_TRIGGER_CRON_INVALID"),
    ("0 0 * * MON", "NODE_TRIGGER_CRON_INVALID"),
    ("0 0 30 2 *", "NODE_TRIGGER_CRON_INVALID"),
])
def test_u891_bad_cron_is_refused_at_save_time_with_a_precise_code(cron, code):
    response = client.post("/api/graphs", json=_graph(cron=cron), headers=ADMIN_A)
    assert response.status_code == 422, response.text
    body = response.json()
    assert code in body["codes"], body
    assert any("\u4e00" <= char <= "\u9fff" for char in "".join(body["detail"])), body


def test_u891b_a_valid_cron_still_saves():
    graph_id, _ = _publish(_graph(cron="0 9 * * 1-5"))
    assert _find(graph_id)["cron"] == "0 9 * * 1-5"


# --- 打包 ZL（docs/08 打包 ZL 立项块）：tz 保存期校验 U1059 ＋ 预演按 tz（U1057 的 REST 面） --

@pytest.mark.parametrize("timezone", [
    "Mars/Olympus",
    "Not/AZone",
    "Asia/ShanghaiX",
])
def test_u1059_unknown_timezone_is_refused_at_save_time_with_a_precise_code(timezone):
    response = client.post("/api/graphs", json=_graph(timezone=timezone), headers=ADMIN_A)
    assert response.status_code == 422, response.text
    body = response.json()
    assert "NODE_TRIGGER_TZ_INVALID" in body["codes"], body
    assert any("\u4e00" <= char <= "\u9fff" for char in "".join(body["detail"])), body


def test_u1059_utc_and_empty_timezone_still_save():
    for tz_value in (None, "UTC"):
        graph_id, _ = _publish(_graph(timezone=tz_value))
        assert _find(graph_id)["timeZone"] == "UTC"


def test_u1059_publish_derives_the_named_timezone_into_both_rows():
    graph_id, _ = _publish(_graph(cron="0 9 * * *", timezone="Asia/Shanghai", catch_up_minutes=15))
    items = client.get("/api/schedules", headers=VIEWER_A).json()["items"]
    for action in ("run", "reflect"):
        row = next(item for item in items if item["graphId"] == graph_id and item["action"] == action)
        assert row["timeZone"] == "Asia/Shanghai"
        assert row["catchUpMinutes"] == 15
        assert row["nextFireAt"] is not None
        # 上海 09:00 的槽位＝UTC 01:00（U1057 的 REST 面）；nextFireAt 是 UTC ISO
        assert "+00:00" in row["nextFireAt"]


def test_u892c_preview_honors_a_named_timezone_and_echoes_it_back():
    response = client.post("/api/schedules/cron-preview",
                           json={"cron": "0 9 * * *", "timeZone": "Asia/Shanghai"},
                           headers=VIEWER_A)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True, body
    assert body["timeZone"] == "Asia/Shanghai"
    # 上海 09:00 → UTC 01:00（当日）；三个槽严格递增、均为 UTC ISO
    assert "T01:00:00+00:00" in body["nextFireAt"][0], body["nextFireAt"]
    assert len(body["nextFireAt"]) == 3
    assert body["nextFireAt"] == sorted(body["nextFireAt"])


# --- U892–U894 cron 预演端点（步 ⑤ 补的只读口；合法与非法都回 200） ----------

def test_u892_preview_returns_three_increasing_utc_slots_and_viewer_can_read():
    before = client.get("/api/schedules", headers=VIEWER_A).json()["items"]
    response = client.post("/api/schedules/cron-preview", json={"cron": "*/5 * * * *"}, headers=VIEWER_A)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True and body["timeZone"] == "UTC"
    slots = [datetime.fromisoformat(item) for item in body["nextFireAt"]]
    assert len(slots) == 3, "三槽是排障用的：少给一条就等于把判断又丢回给运营"
    assert all(later > earlier for earlier, later in zip(slots, slots[1:])), slots
    assert all(slot.minute % 5 == 0 and slot.tzinfo is not None for slot in slots)
    # 预演不许留下任何痕迹：不建调度、不写认领
    assert client.get("/api/schedules", headers=VIEWER_A).json()["items"] == before


@pytest.mark.parametrize("cron,expect", [
    ("5/2 * * * *", "不在支持的语法内"),
    ("0 0 30 2 *", "没有任何触发时刻"),
    ("0 0 * * MON", "不支持该取值"),
])
def test_u893_an_unusable_expression_is_an_answer_not_an_error(cron, expect):
    response = client.post("/api/schedules/cron-preview", json={"cron": cron}, headers=OPERATOR_A)
    assert response.status_code == 200, f"预演不该用 422 拦字段级问题：{response.text}"
    body = response.json()
    assert body["valid"] is False and body["nextFireAt"] == []
    assert expect in body["message"]


def test_u894_blank_cron_is_422_and_login_is_required():
    assert client.post(
        "/api/schedules/cron-preview", json={"cron": "   "}, headers=OPERATOR_A
    ).status_code == 422
    assert client.post("/api/schedules/cron-preview", json={"cron": "* * * * *"}).status_code == 401


# --- U1031 动作维度：同图两条注册、互不吃认领（打包 ZH，docs/88 §3 P-4；迁移 035） ---


def test_u1031_publish_derives_both_run_and_reflect_rows(schedule_graph):
    """发布派生出两条注册（同一 cron），各自有独立开关；投影多出 `action` 键。

    反思项**必须是发布派生的**：既有调度项全部来自发布，不给它派生就等于没有入口
    （docs/88 §3 决策记录里那条订正说的正是这件事）。
    """
    graph_id, version = schedule_graph
    run_row = _find(graph_id, action="run")
    reflect_row = _find(graph_id, action="reflect")
    assert run_row is not None and reflect_row is not None, "发布只派生了一条注册"
    for row in (run_row, reflect_row):
        assert row["version"] == version and row["cron"] == "*/5 * * * *"
        assert row["enabled"] is True

    # 开关互不影响：关掉跑图不动反思（反之亦然）——共用槽位形状，不共用启用状态。
    assert client.post(
        f"/api/schedules/{graph_id}/enabled",
        json={"enabled": False, "schedule_id": "t1n"},
        headers=OPERATOR_A,
    ).status_code == 200
    assert _find(graph_id, action="run")["enabled"] is False
    assert _find(graph_id, action="reflect")["enabled"] is True

    # 缺省动作逐字不变：不带 action 的请求仍然打在 run 项上（U888 的既有语义）。
    assert client.post(
        f"/api/schedules/{graph_id}/enabled",
        json={"enabled": True, "schedule_id": "t1n"}, headers=OPERATOR_A
    ).status_code == 200
    assert _find(graph_id, action="run")["enabled"] is True
    assert _find(graph_id, action="reflect")["enabled"] is True


def test_u1031_missing_action_is_404_and_the_two_actions_do_not_share_a_claim(schedule_graph):
    """两条注册的认领互不吞并：同一个槽位，run 与 reflect 各认各的（迁移 035 换键的理由）。"""
    graph_id, _ = schedule_graph
    # 不存在的动作组合按不存在处理（此处 t2 没有任何注册，借它验 404 文案带动作）。
    assert client.post(
        f"/api/schedules/{graph_id}/run-now",
        json={"schedule_id": "__reflect__"},
        headers=ADMIN_B,
    ).status_code == 404

    store = schedule_store()
    moment = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    assert store.claim("t1", graph_id, "t1n", moment) is True
    assert store.claim("t1", graph_id, "__reflect__", moment, "reflect") is True, \
        "反思被跑图的认领吃掉了"
    assert store.claim("t1", graph_id, "t1n", moment) is False
    assert store.claim("t1", graph_id, "__reflect__", moment, "reflect") is False


# --- U1032 `action="reflect"` 的派发与 run-now（打包 ZH） -------------------


def test_u1032_reflect_tick_runs_a_reflection_pass_instead_of_a_graph_run(schedule_graph):
    """`action="reflect"` 的 tick 走反思 pass：不产 run、只留收尾报告。"""
    from atlas.api.main import _schedule_claim, _schedule_dispatch, _schedule_is_busy
    from atlas.scheduling.engine import ACTION_FIRED, tick

    graph_id, _ = schedule_graph
    records = [
        row for row in schedule_store().list_tenant("t1")
        if row.graph_id == graph_id and row.action == "reflect"
    ]
    assert records, "发布没有派生反思调度项"

    moment = datetime(2026, 10, 1, 10, 5, 30, tzinfo=timezone.utc)
    outcomes = tick(moment, records, _schedule_claim, _schedule_dispatch, busy=_schedule_is_busy)
    assert [(o.action, o.schedule_action) for o in outcomes] == [(ACTION_FIRED, "reflect")]

    services = tenant_registry.get("t1")
    assert [
        run for run in services.run_store.list(status=None, limit=200)
        if run.get("graphId") == graph_id
    ] == [], "反思派发起了图运行（它不该写 run_store）"
    reports = services.reflection_store.list_reports(graph_id=graph_id)
    assert len(reports) == 1, "反思 pass 没留下收尾报告"
    assert reports[0]["status"] == "no_evidence", "这张图没跑过，证据应为空（确定性、不依赖 LLM）"
    assert _find(graph_id, action="reflect")["lastFiredAt"] is not None


def test_u1032_run_now_reflect_returns_a_report_and_leaves_the_slot_unclaimed(schedule_graph):
    """`run-now {action:"reflect"}` 立刻跑一次反思并回报告摘要；**不占槽位**（照 U889 同纪律）。"""
    graph_id, version = schedule_graph
    response = client.post(
        f"/api/schedules/{graph_id}/run-now",
        json={"schedule_id": "__reflect__"}, headers=OPERATOR_A
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["graphId"] == graph_id and payload["baseVersion"] == version
    assert payload["candidateId"] is None, "零证据的图不该凭空产出候选"
    assert payload["status"] in ("ok", "no_evidence")

    # 认领槽位必须还是空的：run-now 是"额外跑一次"，不是替本分钟交差。
    moment = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    assert schedule_store().claim("t1", graph_id, "__reflect__", moment, "reflect") is True

    # 反思项不吃跑图的 busy 判定：图在跑也能反思（它不产运行、不争用任何东西）。
    services = tenant_registry.get("t1")
    services.run_store.begin(run_id="run-busy", graph_id=graph_id, mode="schedule")
    try:
        assert client.post(
            f"/api/schedules/{graph_id}/run-now",
            json={"schedule_id": "__reflect__"}, headers=OPERATOR_A
        ).status_code == 200
        assert client.post(
            f"/api/schedules/{graph_id}/run-now", headers=OPERATOR_A
        ).status_code == 409, "跑图档的 busy 判定必须原样保留"
    finally:
        services.run_store.finish(run_id="run-busy", status="completed")
