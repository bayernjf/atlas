# -*- coding: utf-8 -*-
"""`GET /api/interruptions` 的只读投影（docs/76 打包 Q，U916–U920）。

这一批的全部价值是"崩在续跑途中的 run 终于查得到"，所以测点不在"能不能查出东西"，
而在三件容易被写歪的事：**档位只是描述不是判决**（不许引计时器）、**内存档必须自报
看不见**（空列表会被读成"没有卡住的 run"）、**读路径一个字也不写**。

真库里的租户隔离（那条 `WHERE tenant_id`）不在本文件——常跑测里帧是我自己写的字典，
证明不了 SQL 会不会漏；它在 `tests/test_interruptions_pg_integration.py`（U921，含反向门）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from atlas.api import main as api_main
from atlas.api.main import app
from atlas.iam.deps import session_store, tenant_registry

client = TestClient(app)


def _auth(username: str, password: str) -> dict[str, str]:
    from atlas.iam.principals import authenticate

    principal = authenticate(username, password)
    assert principal is not None
    return {"Authorization": f"Bearer {session_store.issue(principal)}"}


VIEWER_A = _auth("viewer-a", "viewer123")
ADMIN_B = _auth("admin-b", "admin123")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


@pytest.fixture()
def pg_frames(monkeypatch):
    """把端点的数据源换成"读回来的帧"，并记下 SQL 收到的租户参数。

    只替换读函数——投影、档位、鉴权全走真代码。真 SQL 的隔离在 U921。
    """
    calls: list[str] = []
    state: dict[str, list[dict]] = {"frames": []}

    def fake_list_tenant_frames(engine, tenant_id):  # noqa: ARG001
        calls.append(tenant_id)
        return [dict(frame) for frame in state["frames"]]

    monkeypatch.setattr(api_main, "STORAGE_BACKEND", "pg")
    monkeypatch.setattr(api_main, "get_pg_backend", lambda: SimpleNamespace(engine=object()))
    monkeypatch.setattr(api_main, "list_tenant_frames", fake_list_tenant_frames)
    return SimpleNamespace(calls=calls, frames=state["frames"])


def _frame(token: str, run_id: str, *, claimed_at: datetime | None = None) -> dict:
    return {
        "resume_token": token,
        "tenant_id": "t1",
        "run_id": run_id,
        "node_id": "approval-1",
        "kind": "approval",
        "resume_state": {"graph_id": "g-1"},
        "deadline_at": None,
        "resumed_at": claimed_at,
        "resumed_by": "host-a:1111" if claimed_at else None,
    }


def test_u916_memory_backend_says_it_cannot_see_frames() -> None:
    """档位诚实（docs/76 D-4）：内存档根本不写帧表 ⇒ 空列表必须自带解释。"""
    assert api_main.STORAGE_BACKEND != "pg", "本用例的前提就是内存档测试环境"
    body = client.get("/api/interruptions", headers=VIEWER_A).json()
    assert body["backend"] == "memory"
    assert body["visibility"] == "frames-not-persisted"
    assert body["items"] == []


def test_u917_read_tier_no_anonymous_and_no_scoping_parameters() -> None:
    assert client.get("/api/interruptions").status_code == 401
    assert client.get("/api/interruptions", headers=VIEWER_A).status_code == 200
    # D-5：端点不接任何租户/runId 入参 ⇒ 没有可供越权的参数
    params = set(client.get("/api/interruptions", headers=VIEWER_A).json())
    assert params == {"backend", "visibility", "items"}
    import inspect

    names = set(inspect.signature(api_main.list_interruptions).parameters)
    assert names == {"principal"}, f"读端点多了可被操控的入参：{names - {'principal'}}"


def test_u918_states_describe_and_never_judge(monkeypatch, pg_frames) -> None:
    now = datetime.now(timezone.utc)
    runs = tenant_registry.get("t1").run_store
    # 四条 run，分别处在投影会用到的四种状态上
    runs.begin(run_id="r-await", graph_id="g-1", mode="api")
    runs.suspend(run_id="r-await", node_id="approval-1", kind="approval",
                 resume_token="t-await", deadline_at=None)
    runs.begin(run_id="r-exec", graph_id="g-1", mode="api")            # running
    runs.begin(run_id="r-susp", graph_id="g-1", mode="api")
    runs.suspend(run_id="r-susp", node_id="approval-1", kind="approval",
                 resume_token="t-susp", deadline_at=None)              # 认领后仍 suspended＝那颗雷
    runs.begin(run_id="r-fail", graph_id="g-1", mode="api")
    runs.finish(run_id="r-fail", status="failed", error="boom")        # 帧残留（docs/76 P-6）

    pg_frames.frames.extend([
        _frame("t-await", "r-await"),
        _frame("t-exec", "r-exec", claimed_at=now - timedelta(seconds=30)),
        _frame("t-susp", "r-susp", claimed_at=now - timedelta(hours=3)),
        _frame("t-fail", "r-fail", claimed_at=now - timedelta(minutes=5)),
        _frame("t-gone", "r-not-in-store", claimed_at=now - timedelta(minutes=1)),
        _frame("t-other", "r-await", claimed_at=now - timedelta(minutes=2)),
    ])
    # r-await 已挂起但没被认领 ⇒ awaiting；r-other 复用同一条 run 只为看"认领了却还挂起"
    response = client.get("/api/interruptions", headers=VIEWER_A)
    assert response.status_code == 200
    by_token = {row["resumeToken"]: row for row in response.json()["items"]}

    assert by_token["t-await"]["state"] == "awaiting"
    assert by_token["t-await"]["claimedAt"] is None
    assert by_token["t-exec"]["state"] == "claimed_executing"
    assert by_token["t-susp"]["state"] == "claimed_suspended"
    assert by_token["t-fail"]["state"] == "frame_lingering"
    assert by_token["t-gone"]["state"] == "claimed_unknown_run"
    assert by_token["t-gone"]["runStatus"] is None
    # 认领时刻统一走 to_utc_iso（不新造第四种时刻序列化）
    assert by_token["t-exec"]["claimedAt"] == _iso(now - timedelta(seconds=30))
    # 不给秒数加判断，只把时长交出去
    assert 25 <= by_token["t-exec"]["claimedSeconds"] <= 35
    assert 10700 <= by_token["t-susp"]["claimedSeconds"] <= 10900
    assert by_token["t-other"]["state"] == "claimed_suspended"


def test_u919_the_read_path_writes_nothing(monkeypatch, pg_frames) -> None:
    runs = tenant_registry.get("t1").run_store
    runs.begin(run_id="r-keep", graph_id="g-1", mode="api")
    runs.suspend(run_id="r-keep", node_id="approval-1", kind="approval",
                 resume_token="t-keep", deadline_at=None)
    pg_frames.frames.append(_frame("t-keep", "r-keep", claimed_at=datetime.now(timezone.utc)))

    def _forbidden(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("读端点碰了写路径")

    monkeypatch.setattr(api_main, "clear_frame", _forbidden)
    before = dict(runs.get("r-keep"))
    first = client.get("/api/interruptions", headers=VIEWER_A).json()
    second = client.get("/api/interruptions", headers=VIEWER_A).json()
    assert first == second, "同一个只读查询两次结果不同"
    assert dict(runs.get("r-keep")) == before, "run 状态被读路径改动了"
    # 每次请求恰好一次帧查询，且带的是**本租户**
    assert pg_frames.calls == ["t1", "t1"]


def test_u920_the_query_follows_the_principal_not_a_constant(pg_frames) -> None:
    """租户参数只能来自登录态本身（SQL 里那条 WHERE 的真隔离由 U921 集成测守）。"""
    client.get("/api/interruptions", headers=VIEWER_A)
    client.get("/api/interruptions", headers=ADMIN_B)
    assert pg_frames.calls == ["t1", "t2"], "查询参数没有跟着登录租户走＝越权面回来了"


# --- U924：把"删掉 WHERE 就当场红"变成常跑能拦的事（docs/76 §8 偏差①的补口） ----


class _RecordingConnection:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def __enter__(self) -> "_RecordingConnection":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, statement, parameters=None):  # noqa: ANN001 - 只为记录 SQL
        self._calls.append((" ".join(str(statement).split()), parameters))

        class _Empty:
            @staticmethod
            def all() -> list:
                return []

        return _Empty()


class _RecordingEngine:
    """假到只够接住一条 SELECT 的引擎：用途是把**发给库的 SQL 与绑定参数**抓下来。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def connect(self):  # noqa: ANN201
        return _RecordingConnection(self.calls)


def _one_normalized_sql(engine: _RecordingEngine) -> tuple[str, object]:
    assert len(engine.calls) == 1, f"应当只发一条查询，实发 {len(engine.calls)} 条"
    return engine.calls[0]


def test_u924_the_tenant_reader_sends_a_tenant_filter_with_a_bound_value() -> None:
    """读函数**发出去的 SQL** 必须带租户过滤，且值是绑定的（不是拼字符串）。

    集成测（U921）只在 `-m integration` 真跑时才拦得住"删掉 WHERE"，CI 每次跑它是事实，
    但"跑不跑"这件事本身不由代码说话——所以这里把它钉成常跑：任何一次 `pytest` 都会红。
    断的是**编译出来的 SQL** 而不是源码文本，重排/换行不会误红。
    """
    from atlas.storage.recovery import list_tenant_frames

    engine = _RecordingEngine()
    assert list_tenant_frames(engine, "t1") == []
    sql, params = _one_normalized_sql(engine)
    assert "WHERE tenant_id = :tenant_id" in sql, f"租户过滤不在发出去的 SQL 里：{sql}"
    assert params == {"tenant_id": "t1"}, f"租户值是拼进 SQL 的而不是绑定的：{params}"
    assert "t1" not in sql, "租户标识被字面量拼接＝可注入面回来了"


def test_u924_control_the_global_loader_really_has_no_filter() -> None:
    """判别对照（没有它上一条可能是空断言）：全局那条**确实不带**租户过滤。

    这正是 docs/76 D-2 禁止 HTTP 侧用 `load_pending_frames` 的理由，也是"删 WHERE 会红"
    这条守护能红的前提——两半都断，才证明 WHERE 是被要求、而非碰巧写在句子中间。
    """
    from atlas.storage.recovery import load_pending_frames

    engine = _RecordingEngine()
    assert load_pending_frames(engine) == []
    sql, params = _one_normalized_sql(engine)
    assert "WHERE" not in sql, f"全局读函数不知何时被加了过滤，对照失效：{sql}"
    assert params in (None, {}), f"全局读函数不该绑租户参数：{params}"
    assert "tenant_id" not in sql.split("FROM", 1)[-1], "SELECT 之后出现了租户条件＝对照不再成立"
