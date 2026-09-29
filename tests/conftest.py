# -*- coding: utf-8 -*-
"""pytest 全局夹具（04 §5.14）。

既有 API 用例都在单一默认租户下构造数据；鉴权落地后，为两个模块级
TestClient 自动注入 t1 管理员的 Bearer 会话，使用例语义保持零改动。
test_api_auth.py 的未登录/跨角色场景使用自建 TestClient，不受本夹具影响。

另有 `_no_real_llm_provider`：把 litellm import 期 load_dotenv() 带进来的真实 LLM
凭证清出 os.environ，保证单元测试不出网（见该夹具上方注释）。
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

# litellm 在 **import 时**自己调 load_dotenv()（litellm/__init__.py:27），把仓库 `.env`
# 里的 LITELLM_MODEL / OPENAI_API_KEY 灌进 os.environ。后果不是"多读了几个变量"：套件里
# 只要有任何一个用例先 import 到 litellm，其后所有用例的 get_decision_client() 都会返回
# LiteLLMDecisionClient——test_random_seed_replay 的 ai_decision 节点于是真的出网，拿到低
# 置信结论后转人工，挂在 ApprovalBroker.wait()（_DECISION_ESCALATION_TIMEOUT_SECONDS=3600）
# 上，整个套件看起来是"卡死"而非失败。
# 单元测试不出网：每个用例开始前清掉模型开关与供应商凭证。要模型的用例自行 monkeypatch
# 打开（既有用例都是这么做的），因此本夹具对它们无影响。不还原——本进程的环境在套件结束
# 后即废弃，留一个真凭证在 os.environ 里没有任何好处。
_LLM_PROVIDER_ENV_VARS = ("LITELLM_MODEL", "OPENAI_API_KEY")


@pytest.fixture(autouse=True)
def _no_real_llm_provider():
    for name in _LLM_PROVIDER_ENV_VARS:
        os.environ.pop(name, None)


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
