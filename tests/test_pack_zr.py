# -*- coding: utf-8 -*-
"""打包 ZR（P2 工程债）内存档单测（docs/08 打包 ZR 立项块）。

R1/R2：迁移与 retention 的 PG 部分在 test_pack_zr_pg_integration.py（integration 标记）；
本文件覆盖 R2 的 SQL 契约（interruptions 分支已去 ::timestamptz 强转、同族表强转守边界）
与 R3 的 pending 有界性（U1100–U1102）。
"""

from __future__ import annotations

import pytest

from atlas.collaboration.approvals import ApprovalBroker, _MAX_PENDING
from atlas.storage.pg import PgBackend


class _FakeResult:
    rowcount = 3


class _FakeConn:
    """记录 SQL 文本与参数的伪连接（不真执行，PG 语法在 sqlite 上会失败）。"""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def execute(self, stmt, params: dict):
        self.executed.append((str(stmt), params))
        return _FakeResult()


class _FakeCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    def __enter__(self) -> _FakeConn:
        return self.conn

    def __exit__(self, *exc) -> bool:
        return False


class _FakeEngine:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def begin(self) -> _FakeCtx:
        return _FakeCtx(self.conn)


def _make_pending(broker: ApprovalBroker, *, count: int = 1, prefix: str = "n") -> list[str]:
    tokens = []
    for i in range(count):
        tokens.append(
            broker.request(
                node_id=f"{prefix}-node-{i}",
                graph_id="g",
                summary=f"s-{i}",
                approver="alice",
                timeout_seconds=3600,
            )
        )
    return tokens


# ---------- U1099：R2 retention 去强转契约 ----------


def test_prune_expired_interruptions_uses_native_column() -> None:
    """interruptions 清理分支：created_at 已归队 TIMESTAMPTZ，直接原生比较、无 ::timestamptz。

    反向门：把该分支改回 `created_at::timestamptz < ...` 必须红。
    """
    engine = _FakeEngine()
    backend = PgBackend(engine)
    cutoffs = {
        "interruptions": "2026-09-01T00:00:00+00:00",
        "graph_versions": "2026-09-01T00:00:00+00:00",
    }
    backend.prune_expired(cutoffs, session_sweep=False)

    sql_by_table = {stmt: params for stmt, params in engine.conn.executed}
    # 只断言两个表都执行到（session_sweep=False 不产生 iam_sessions）。
    executed_sql = " | ".join(engine.conn.executed and [s for s, _ in engine.conn.executed])
    assert "interruptions" in executed_sql
    assert "graph_versions" in executed_sql

    inter_sql = next(s for s, _ in engine.conn.executed if "FROM interruptions" in s)
    graph_sql = next(s for s, _ in engine.conn.executed if "FROM graph_versions" in s)

    # R2：interruptions 分支去强转（列已对型）；边界：graph_versions 是 TEXT 异类、强转保留。
    assert "created_at::timestamptz" not in inter_sql
    assert "created_at < CAST(:cutoff AS timestamptz)" in inter_sql
    assert "resumed_at IS NOT NULL" in inter_sql
    assert "created_at::timestamptz" in graph_sql


# ---------- U1100：R3 prune 有界性 ----------


def test_pending_pruned_on_new_request() -> None:
    """已决条目随新 request 懒清：dict 大小缩回「未决数＋1」，不再随历史已决无界增长。"""
    broker = ApprovalBroker()
    tokens = _make_pending(broker, count=5)
    for t in tokens[:3]:
        assert broker.resolve(t, "approved", resolved_by="human")
    # 3 条已决 + 2 条未决，dict 现为 5。
    assert len(broker._pending) == 5
    # 新 request 触发 prune：3 条已决清走，剩 2 未决 + 1 新。
    _make_pending(broker, count=1)
    assert len(broker._pending) == 3
    remaining = [t for t, p in broker._pending.items() if p.decision is None]
    assert len(remaining) == 3


def test_pending_pruned_on_list_pending() -> None:
    """list_pending 入口同样懒清：全部已决后调用一次即把 dict 清空（列表只该显示未决）。"""
    broker = ApprovalBroker()
    tokens = _make_pending(broker, count=4)
    for t in tokens:
        broker.resolve(t, "rejected", resolved_by="timeout")
    # list_pending 只返回未决（空），且顺带清走全部已决条目。
    assert broker.list_pending() == []
    assert len(broker._pending) == 0


def test_resolved_pending_kept_until_prune_for_notify() -> None:
    """懒清不破坏邮件决策链路：resolve 后（下一个 prune 前）get_notify_recipients 仍可查。"""
    broker = ApprovalBroker()
    token = broker.request(
        node_id="n",
        graph_id="g",
        summary="s",
        approver="alice",
        timeout_seconds=3600,
        notify_recipients=["alice@example.com", "bob@example.com"],
    )
    broker.resolve(token, "approved", resolved_by="human")
    # resolve 不 pop：邮件链路 resolve 后审计仍能查收件人。
    assert broker.get_notify_recipients(token) == ["alice@example.com", "bob@example.com"]
    # 新 request 懒清后，已决条目消失。
    _make_pending(broker, count=1)
    assert broker.get_notify_recipients(token) == []


def test_wait_reads_decision_after_prune() -> None:
    """wait 持引用读 decision：即使另一线程在新 request 触发 prune 移除 dict 条目，
    已取出的引用仍可读（dict 移除不销毁对象）。"""
    import threading

    broker = ApprovalBroker()
    token = broker.request(
        node_id="n", graph_id="g", summary="s", approver="alice", timeout_seconds=30
    )
    outcome: list = []

    def waiter() -> None:
        decision = broker.wait(token)
        outcome.append(decision)

    t = threading.Thread(target=waiter)
    t.start()
    # 等 wait 拿到引用（event 未 set 前 wait 在阻塞，但锁内 get 已发生）。
    while not outcome:
        broker.resolve(token, "approved", resolved_by="human")
        # 触发 prune 把条目从 dict 移除。
        _make_pending(broker, count=1)
        t.join(timeout=5)
        break
    assert outcome == ["approved"]


# ---------- U1101：R3 上限守卫反向门 ----------


def test_pending_hard_cap_fail_closed(monkeypatch) -> None:
    """硬上限 fail-closed：超过 _MAX_PENDING 拒绝新挂起（monkeypatch 缩小常量以便构造）。"""
    monkeypatch.setattr("atlas.collaboration.approvals._MAX_PENDING", 2)
    broker = ApprovalBroker()
    _make_pending(broker, count=2)
    with pytest.raises(RuntimeError, match="硬上限"):
        _make_pending(broker, count=1)
    # 上限只挡新增，不破坏既有未决。
    assert len(broker._pending) == 2
    # 常量默认值是个有意义的护栏（防常量误删/误改，无功能语义）。
    assert _MAX_PENDING >= 1000
