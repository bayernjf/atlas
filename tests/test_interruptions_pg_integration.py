# -*- coding: utf-8 -*-
"""挂起帧投影对**真库**的读语义与租户隔离（docs/76 §4 U921，打包 Q）。

常跑那批里帧是我自己写的字典，它证明不了两件本批最要紧的事：
1. SQL 里那条 `WHERE tenant_id` 真的在挡——所以本文件的隔离断言**不许**被 mock 替代；
2. `resumed_at` 是 `TIMESTAMPTZ` 列，经 `to_utc_iso` 之后仍是同一个时刻（驱动/时区任一
   环节出错都会在第二条上现形）。

跑法（与其余 PG 集成用例同）：
    ATLAS_RUN_INTEGRATION=1 DATABASE_URL=postgresql+psycopg://… \\
    .venv/bin/pytest -m integration tests/test_interruptions_pg_integration.py
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from atlas.api import main as api_main
from atlas.api.main import app
from atlas.iam.deps import session_store, tenant_registry
from atlas.storage.recovery import claim_frame_for_resume, load_pending_frames, make_frame_sink

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run interruption PG integration",
    ),
]

client = TestClient(app)


def _auth(username: str, password: str) -> dict[str, str]:
    from atlas.iam.principals import authenticate

    principal = authenticate(username, password)
    assert principal is not None
    return {"Authorization": f"Bearer {session_store.issue(principal)}"}


VIEWER_A = _auth("viewer-a", "viewer123")  # t1
ADMIN_B = _auth("admin-b", "admin123")  # t2


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(os.environ["DATABASE_URL"])
    migrations = Path(__file__).resolve().parents[1] / "db" / "migrations"
    for path in sorted(migrations.glob("*.sql")):
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
        with eng.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    yield eng
    eng.dispose()


@pytest.fixture()
def seeded(engine, monkeypatch):
    """真写帧＋真认领，并把端点的数据源指向这个库（不 mock 读函数）。

    帧按 `u921-<suffix>` 前缀命名，结束按前缀删自己的行——不碰别人的数据，也不靠
    reset_tenant 兜底。
    """
    suffix = uuid.uuid4().hex[:8]
    token_a = f"u921-{suffix}-a"  # t1，未认领
    token_b = f"u921-{suffix}-b"  # t1，已认领（真 claim）
    token_c = f"u921-{suffix}-c"  # t2，未认领
    for tenant, run_id, token in (("t1", f"r-{suffix}-1", token_a),
                                  ("t1", f"r-{suffix}-2", token_b),
                                  ("t2", f"r-{suffix}-9", token_c)):
        make_frame_sink(engine, tenant, run_id)({
            "resume_token": token,
            "node_id": "approval-1",
            "kind": "approval",
            "deadline_at": None,
            "resume_state": {"graph_id": f"g-{suffix}"},
        })
    assert claim_frame_for_resume(engine, token_b, "u921-control") is True

    runs = tenant_registry.get("t1").run_store
    runs.begin(run_id=f"r-{suffix}-2", graph_id=f"g-{suffix}", mode="api")
    runs.suspend(run_id=f"r-{suffix}-2", node_id="approval-1", kind="approval",
                 resume_token=token_b, deadline_at=None)

    monkeypatch.setattr(api_main, "STORAGE_BACKEND", "pg")
    monkeypatch.setattr(api_main, "get_pg_backend", lambda: SimpleEngineBox(engine))
    yield {"suffix": suffix, "tokens": (token_a, token_b, token_c), "runs": runs}
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM interruptions WHERE resume_token LIKE :pattern"),
            {"pattern": f"u921-{suffix}-%"},
        )


class SimpleEngineBox:
    def __init__(self, engine) -> None:
        self.engine = engine


def _rows(headers):
    body = client.get("/api/interruptions", headers=headers).json()
    assert body["backend"] == "pg"
    assert body["visibility"] == "tenant-scoped"
    return body["items"]


def test_u921_only_this_tenants_frames_are_returned(seeded) -> None:
    token_a, token_b, token_c = seeded["tokens"]
    a_rows = {row["resumeToken"] for row in _rows(VIEWER_A)}
    assert {token_a, token_b} <= a_rows
    assert token_c not in a_rows, "t2 的帧漏进了 t1 的投影"

    b_rows = {row["resumeToken"] for row in _rows(ADMIN_B)}
    assert b_rows == {token_c}, "t1 的帧漏进了 t2 的投影"


def test_u921_claimed_frame_on_a_suspended_run_is_the_visible_mine(seeded) -> None:
    token_a, token_b, _ = seeded["tokens"]
    by_token = {row["resumeToken"]: row for row in _rows(VIEWER_A)}
    assert by_token[token_a]["state"] == "awaiting"
    assert by_token[token_b]["state"] == "claimed_suspended"
    assert by_token[token_b]["claimedBy"] == "u921-control"
    # TIMESTAMPTZ 列 → to_utc_iso：时刻不能在读链上被挪走
    claimed_at = datetime.fromisoformat(by_token[token_b]["claimedAt"])
    assert claimed_at.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - claimed_at).total_seconds()) < 120
    assert by_token[token_b]["claimedSeconds"] is not None


def test_u921_reverse_control_the_global_loader_would_have_leaked(engine, seeded) -> None:
    """差分对照：D-2 若被忽略（改用全局 `load_pending_frames`），隔离立刻站不住。

    这里不改生产代码来做反向门——同一批真帧、同一个库，两条查询差一个 `WHERE`：
    全局那条**确实**看得见 t2 的 token，租户那条看不见。两半都断言，才证明前面那条
    绿不是"根本没数据可漏"造成的。
    """
    token_a, token_b, token_c = seeded["tokens"]
    global_tokens = {frame["resume_token"] for frame in load_pending_frames(engine)}
    assert {token_a, token_b, token_c} <= global_tokens, "帧没写进去，前面的断言全是空的"
    t1_rows = {row["resumeToken"] for row in _rows(VIEWER_A)}
    assert token_c not in t1_rows and {token_a, token_b} <= t1_rows


def test_u921_reading_changes_nothing_in_the_table(engine, seeded) -> None:
    """只读性对**真库**成立：投影前后帧的行数与认领值一字不差。"""
    pattern = f"u921-{seeded['suffix']}-%"

    def snapshot() -> list[tuple]:
        with engine.connect() as conn:
            return sorted(
                (row[0], str(row[1]), row[2])
                for row in conn.execute(
                    text("SELECT resume_token, resumed_at, resumed_by FROM interruptions "
                         "WHERE resume_token LIKE :pattern"),
                    {"pattern": pattern},
                ).all()
            )

    before = snapshot()
    _rows(VIEWER_A)
    _rows(ADMIN_B)
    assert snapshot() == before, "读端点改动了帧的认领状态"
