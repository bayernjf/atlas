# -*- coding: utf-8 -*-
"""pytest 全局夹具（04 §5.14）。

既有 API 用例都在单一默认租户下构造数据；鉴权落地后，为两个模块级
TestClient 自动注入 t1 管理员的 Bearer 会话，使用例语义保持零改动。
test_api_auth.py 的未登录/跨角色场景使用自建 TestClient，不受本夹具影响。
"""

from __future__ import annotations

import os

import pytest

# docs/68 §2.3：调度线程挂在 lifespan 上，而 test_api_demo 有三处用 `with TestClient(app)`
# ——那会真的跑 startup，起一条跨用例活的后台 tick。实测后果是它能把别的用例的共享审批
# broker 放行掉（test_subgraph_events 的审批用例被吹成 flaky）。测试默认关线程：
# 调度语义由直接调用 tick/run_schedule_tick 的用例覆盖，真起线程的验收在真机端到端冒烟里。
# setdefault 而不是直接赋值：真机冒烟脚本要能显式打开它。
os.environ.setdefault("ATLAS_SCHEDULE_ENABLED", "0")

# 决策/观察线程自建 TestClient 时复用当前 t1 admin 头（每用例刷新）。
DEFAULT_AUTH_HEADER: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _default_t1_admin_session():
    from tests.test_api_demo import client as demo_client
    from tests.test_api_graphs import client as graphs_client
    from tests.test_api_runs import client as runs_client
    from tests.test_api_tasks import client as tasks_client

    from atlas.iam.deps import session_store
    from atlas.iam.principals import authenticate

    principal = authenticate("admin-a", "admin123")
    assert principal is not None
    token = session_store.issue(principal)
    header = f"Bearer {token}"
    demo_client.headers["Authorization"] = header
    graphs_client.headers["Authorization"] = header
    runs_client.headers["Authorization"] = header
    tasks_client.headers["Authorization"] = header
    DEFAULT_AUTH_HEADER["Authorization"] = header
    try:
        yield
    finally:
        demo_client.headers.pop("authorization", None)
        graphs_client.headers.pop("authorization", None)
        runs_client.headers.pop("authorization", None)
        tasks_client.headers.pop("authorization", None)
        DEFAULT_AUTH_HEADER.pop("Authorization", None)
        session_store.revoke(token)
