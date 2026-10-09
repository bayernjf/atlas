# -*- coding: utf-8 -*-
"""打包 AC（docs/110）：AI 评估 Harness v1（离线批评估）契约测试。

U1278 模型校验 / U1279 runner 决策动作匹配三分支 / U1280 verify 三态 /
U1281 metrics 计算（含分母 0 → null）/ U1282 REST 端点（含 403/404/422）/
U1283 PG 持久化（迁移 047 + 两档 store + reset 清空，integration）/
U1284 黄金用例端到端（refund-auto 5 例，summary 手算对账）。

运行：
- 常规（内存档）：.venv/bin/pytest -q tests/test_evaluation.py
- PG 集成：DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5433/atlas \\
    ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_evaluation.py
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from atlas.api.main import app
from atlas.evaluation.models import (
    EvaluationRun,
    EvaluationSummary,
    EvaluationTask,
    ExpectedAction,
    TestCase,
)
from atlas.evaluation.runner import EvaluationGraphNotFound, run_task
from atlas.evaluation.store import EvaluationStore
from atlas.iam.deps import tenant_registry
from atlas.iam.principals import SEED_TENANTS
from atlas.storage.migrations import applied_versions, apply_pending, default_migrations_dir
from atlas.template.catalog import TEMPLATES

from atlas.iam.deps import session_store
from atlas.iam.principals import authenticate

from tests.conftest import DEFAULT_AUTH_HEADER

client = TestClient(app)
_admin = authenticate("admin-a", "admin123")
assert _admin is not None
client.headers["Authorization"] = f"Bearer {session_store.issue(_admin)}"
DEFAULT_AUTH_HEADER.update(client.headers)

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run PG integration",
    ),
]


def _tenant_id() -> str:
    return next(iter(SEED_TENANTS))


def _refund_graph_id() -> str:
    """把内置 refund-auto 模板图存入默认租户 graph_store，返回 graph_id。"""
    services = tenant_registry.get(_tenant_id())
    tpl = next(t for t in TEMPLATES if t.id == "refund-auto")
    return services.graph_store.save(tpl.graph)


# --- U1278 模型校验 -----------------------------------------------------------


class TestEvaluationModels:
    """U1278：task 形状/枚举/边界（≥1 case、≤200、metrics 枚举、expected 至少一项）。"""

    def test_task_requires_test_cases(self):
        with pytest.raises(ValidationError):
            EvaluationTask(task_id="t", description="d", test_cases=[])

    def test_task_caps_test_cases_at_200(self):
        with pytest.raises(ValidationError):
            EvaluationTask(
                task_id="t",
                test_cases=[
                    TestCase(name=f"c{i}", inputs={}, expected=ExpectedAction(action="approve_refund"))
                    for i in range(201)
                ],
            )

    def test_metrics_enum_rejects_unknown(self):
        with pytest.raises(ValidationError):
            EvaluationTask(
                task_id="t",
                test_cases=[TestCase(inputs={}, expected=ExpectedAction(action="approve_refund"))],
                metrics=["task_success_rate", "nope"],
            )

    def test_expected_requires_action_or_verify(self):
        with pytest.raises(ValidationError):
            TestCase(name="空期望", inputs={}, expected=ExpectedAction())

    def test_case_id_auto_name(self):
        task = EvaluationTask(
            task_id="t",
            test_cases=[TestCase(inputs={}, expected=ExpectedAction(action="approve_refund"))],
        )
        # 自动命名由 runner 侧 case-<n> 兜底（契约 §2.1），模型层允许 name=None。
        assert task.test_cases[0].name is None


# --- U1279/U1280/U1281 runner ---------------------------------------------------


class TestEvaluationRunner:
    """U1279 决策动作匹配三分支；U1280 verify 三态；U1281 metrics 计算。"""

    def test_action_match_hit_and_miss_and_undeclared(self):
        gid = _refund_graph_id()
        task = EvaluationTask(
            task_id="u1279",
            test_cases=[
                TestCase(name="命中", inputs={"order_id": "o-1", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(action="approve_refund")),
                TestCase(name="不命中", inputs={"order_id": "o-2", "reason": "不想要了", "amount": 100},
                         expected=ExpectedAction(action="approve_refund")),
                TestCase(name="未声明", inputs={"order_id": "o-3", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(verify="{{outputs.condition-1.target}} == 'tool_call-1'")),
            ],
        )
        run = run_task(tenant_registry.get(_tenant_id()).graph_store, gid, task)
        by_name = {c.name: c for c in run.cases}
        assert by_name["命中"].decision_matched is True
        assert by_name["不命中"].decision_matched is False
        assert by_name["未声明"].decision_matched is None
        # decision_accuracy 分子只数声明 action 的 case
        assert run.summary.decision_accuracy == 0.5

    def test_verify_true_false_illegal(self):
        gid = _refund_graph_id()
        task = EvaluationTask(
            task_id="u1280",
            test_cases=[
                TestCase(name="verify真", inputs={"order_id": "o-1", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(verify="{{outputs.condition-1.target}} == 'tool_call-1'")),
                TestCase(name="verify假", inputs={"order_id": "o-1", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(verify="{{outputs.condition-1.target}} == 'human_approval-1'")),
                TestCase(name="verify非法", inputs={"order_id": "o-1", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(verify="1 +")),
            ],
        )
        run = run_task(tenant_registry.get(_tenant_id()).graph_store, gid, task)
        by_name = {c.name: c for c in run.cases}
        assert by_name["verify真"].passed is True
        assert by_name["verify真"].error is None
        assert by_name["verify假"].passed is False
        assert by_name["verify假"].error is None
        assert by_name["verify非法"].passed is False
        assert by_name["verify非法"].error  # case 级 error，不使整批失败

    def test_metrics_roundtrip(self):
        gid = _refund_graph_id()
        task = EvaluationTask(
            task_id="u1281",
            test_cases=[
                TestCase(name="a", inputs={"order_id": "o-1", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(action="approve_refund")),
                TestCase(name="b", inputs={"order_id": "o-2", "reason": "不想要了", "amount": 100},
                         expected=ExpectedAction(action="request_human_approval")),
                TestCase(name="c", inputs={"order_id": "o-3", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(verify="{{outputs.condition-1.target}} == 'tool_call-1'")),
            ],
        )
        run = run_task(tenant_registry.get(_tenant_id()).graph_store, gid, task)
        assert run.summary.task_success_rate == 1.0
        assert run.summary.decision_accuracy == 1.0  # 分子只数声明 action 的 a/b
        assert run.summary.average_steps > 0
        # 全程无 action 期望 → decision_accuracy 分母 0 → null
        task2 = EvaluationTask(
            task_id="u1281-null",
            test_cases=[
                TestCase(name="x", inputs={"order_id": "o-1", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(verify="{{outputs.condition-1.target}} == 'tool_call-1'")),
            ],
        )
        run2 = run_task(tenant_registry.get(_tenant_id()).graph_store, gid, task2)
        assert run2.summary.decision_accuracy is None

    def test_case_level_error_does_not_fail_batch(self):
        gid = _refund_graph_id()
        task = EvaluationTask(
            task_id="u1281-err",
            test_cases=[
                TestCase(name="好用例", inputs={"order_id": "o-1", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(action="approve_refund")),
                TestCase(name="异常输入", inputs={"order_id": "o-2", "reason": "商品破损", "amount": "abc"},
                         expected=ExpectedAction(action="approve_refund")),
            ],
        )
        run = run_task(tenant_registry.get(_tenant_id()).graph_store, gid, task)
        by_name = {c.name: c for c in run.cases}
        assert by_name["异常输入"].passed is False
        assert "float" in (by_name["异常输入"].error or "")
        assert by_name["好用例"].passed is True
        assert run.summary.task_success_rate == 0.5

    def test_graph_not_found_raises(self):
        with pytest.raises(EvaluationGraphNotFound):
            run_task(tenant_registry.get(_tenant_id()).graph_store, "no-such-graph", EvaluationTask(
                task_id="u1279-404",
                test_cases=[TestCase(inputs={}, expected=ExpectedAction(action="approve_refund"))],
            ))

    def test_approvals_preset_seconds_through(self):
        """U1282（runner 侧）：审批预置秒过不挂起，approval 帧照发、语义动作照收。"""
        gid = _refund_graph_id()
        task = EvaluationTask(
            task_id="u1282",
            test_cases=[
                TestCase(name="转人工语义", inputs={"order_id": "o-1", "reason": "不想要了", "amount": 100},
                         expected=ExpectedAction(action="request_human_approval")),
            ],
        )
        run = run_task(tenant_registry.get(_tenant_id()).graph_store, gid, task)
        c = run.cases[0]
        assert c.passed is True
        assert c.decision_matched is True
        assert c.error is None


# --- 内存 store --------------------------------------------------------------


class TestEvaluationStore:
    """U1283（内存档）：save/list/clear，最新在前，capacity 上限。"""

    def test_save_list_clear(self):
        store = EvaluationStore()
        task = EvaluationTask(
            task_id="st",
            test_cases=[TestCase(inputs={}, expected=ExpectedAction(action="approve_refund"))],
        )
        run = EvaluationRun(
            id="ev-test-1", task_id="st", graph_id="g",
            summary=EvaluationSummary(task_success_rate=1.0, average_steps=1.0),
            cases=[],
            created_at="2026-10-09T00:00:00Z",
        )
        assert store.list() == []
        store.save(run)
        assert len(store.list()) == 1
        assert store.list()[0]["id"] == "ev-test-1"
        store.clear()
        assert store.list() == []


# --- U1282 REST 端点 -----------------------------------------------------------


class TestEvaluationApi:
    """U1282：POST 201/404/422/403；GET 列表。"""

    def _save_graph(self) -> str:
        tpl = next(t for t in TEMPLATES if t.id == "refund-auto")
        resp = client.post("/api/graphs", json=tpl.graph)
        assert resp.status_code in (200, 201)
        return resp.json()["id"]

    def test_post_and_get(self):
        gid = self._save_graph()
        body = {
            "graph_id": gid,
            "task": {
                "task_id": "api-1",
                "test_cases": [
                    {"name": "a", "inputs": {"order_id": "o-1", "reason": "商品破损", "amount": 100},
                     "expected": {"action": "approve_refund"}},
                ],
            },
        }
        resp = client.post("/api/evaluations", json=body)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["task_id"] == "api-1"
        assert data["summary"]["task_success_rate"] == 1.0
        assert data["cases"][0]["passed"] is True
        # GET 历史列表最新在前
        resp2 = client.get("/api/evaluations?limit=10")
        assert resp2.status_code == 200
        items = resp2.json()["items"]
        assert items and items[0]["id"] == data["id"]

    def test_post_404_graph_not_found(self):
        resp = client.post("/api/evaluations", json={
            "graph_id": "no-such",
            "task": {"task_id": "x", "test_cases": [
                {"inputs": {}, "expected": {"action": "approve_refund"}}]},
        })
        assert resp.status_code == 404

    def test_post_422_invalid_task(self):
        gid = self._save_graph()
        resp = client.post("/api/evaluations", json={
            "graph_id": gid,
            "task": {"task_id": "x", "test_cases": []},
        })
        assert resp.status_code == 422

    def test_post_403_non_admin(self):
        from atlas.iam.deps import session_store
        from atlas.iam.principals import authenticate

        principal = authenticate("operator-a", "operator123")
        assert principal is not None
        token = session_store.issue(principal)
        gid = self._save_graph()
        resp = client.post(
            "/api/evaluations",
            headers={"Authorization": f"Bearer {token}"},
            json={"graph_id": gid, "task": {"task_id": "x", "test_cases": [
                {"inputs": {}, "expected": {"action": "approve_refund"}}]}},
        )
        assert resp.status_code == 403


# --- U1284 黄金用例端到端 --------------------------------------------------------


class TestGoldenRun:
    """U1284：refund-auto 5 例任务，summary 数值手算对账。"""

    def test_golden_five_case_roundtrip(self):
        gid = _refund_graph_id()
        task = EvaluationTask(
            task_id="golden",
            description="电商退款黄金测试集（docs/09 §9.3 至少 5 用例）",
            test_cases=[
                TestCase(name="质量低额→自动退款", inputs={"order_id": "o-1", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(action="approve_refund")),
                TestCase(name="非质量→转人工", inputs={"order_id": "o-2", "reason": "不想要了", "amount": 100},
                         expected=ExpectedAction(action="request_human_approval")),
                TestCase(name="质量超限→转人工", inputs={"order_id": "o-3", "reason": "商品破损", "amount": 999999},
                         expected=ExpectedAction(action="request_human_approval")),
                TestCase(name="verify 分流断言", inputs={"order_id": "o-4", "reason": "商品破损", "amount": 100},
                         expected=ExpectedAction(verify="{{outputs.condition-1.target}} == 'tool_call-1'")),
                TestCase(name="异常输入→case 级 error", inputs={"order_id": "o-5", "reason": "商品破损", "amount": "abc"},
                         expected=ExpectedAction(action="approve_refund")),
            ],
        )
        run = run_task(tenant_registry.get(_tenant_id()).graph_store, gid, task)
        # 手算：4/5 verify 通过 → 0.8；4 个声明 action 中 3 个匹配 → 0.75
        assert run.summary.task_success_rate == 0.8
        assert run.summary.decision_accuracy == 0.75
        assert run.summary.average_steps > 0
        assert len(run.cases) == 5
        assert run.task_id == "golden"


# --- U1283 PG 持久化（integration） ----------------------------------------------


@pytest.fixture()
def temp_database_url():
    base = make_url(DATABASE_URL)
    admin_engine = create_engine(base, isolation_level="AUTOCOMMIT")
    db = f"atlas_eval_test_{uuid.uuid4().hex[:8]}"
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db}"'))
    url = base.set(database=db)
    try:
        yield url
    finally:
        admin_engine.dispose()
        with create_engine(base, isolation_level="AUTOCOMMIT").connect() as conn:
            conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :db"),
                {"db": db},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db}"'))


@pytest.mark.integration
@pytest.mark.skipif(
    not RUN_INTEGRATION or not DATABASE_URL,
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run PG integration",
)
def test_pg_store_roundtrip_and_reset(temp_database_url):
    """U1283：迁移 047 可应用且幂等；PgEvaluationStore 写读、最新在前、reset 清空。"""
    from atlas.evaluation.pg_store import PgEvaluationStore

    engine = create_engine(temp_database_url)
    apply_pending(engine, migrations_dir=default_migrations_dir())
    applied = applied_versions(engine)
    assert "047_evaluations.sql" in applied
    # 二次 apply 幂等
    apply_pending(engine, migrations_dir=default_migrations_dir())
    assert applied_versions(engine) == applied

    store = PgEvaluationStore(engine, "t1")
    task = EvaluationTask(
        task_id="pg-1",
        test_cases=[TestCase(inputs={}, expected=ExpectedAction(action="approve_refund"))],
    )
    run1 = EvaluationRun(
        id="ev-pg-1", task_id="pg-1", graph_id="g",
        summary=EvaluationSummary(task_success_rate=1.0, average_steps=2.0),
        cases=[],
        created_at="2026-10-09T00:00:00Z",
    )
    run2 = EvaluationRun(
        id="ev-pg-2", task_id="pg-1", graph_id="g",
        summary=EvaluationSummary(task_success_rate=0.5, average_steps=1.0),
        cases=[],
        created_at="2026-10-09T00:00:01Z",
    )
    store.save(run1)
    store.save(run2)
    rows = store.list()
    assert [r["id"] for r in rows] == ["ev-pg-2", "ev-pg-1"]  # 最新在前
    # 跨租户隔离
    other = PgEvaluationStore(engine, "t2").list()
    assert other == []
    # reset 同清（registry reset 语义：evaluation_store.clear()）
    store.clear()
    assert store.list() == []
    engine.dispose()
