"""ApprovalBroker 单元测试（04 §5.6；13 U21/U22）。"""

from __future__ import annotations

import threading
import time

from atlas.collaboration.approvals import ApprovalBroker


def _request(broker: ApprovalBroker, **overrides) -> str:
    params = {
        "node_id": "human-1",
        "graph_id": "graph-1",
        "summary": "退款审批",
        "approver": "客服主管",
        "timeout_seconds": 5,
    }
    params.update(overrides)
    return broker.request(**params)


def test_resolve_unblocks_waiter_with_decision():
    broker = ApprovalBroker()
    token = _request(broker)
    result: dict = {}

    def waiter() -> None:
        decision = broker.wait(token)
        result["decision"] = decision

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    assert broker.resolve(token, "approved", comment="ok") is True
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert result["decision"] == "approved"
    info = broker.get(token)
    assert info["decision"] == "approved"
    assert info["resolvedBy"] == "human"
    assert info["summary"] == "退款审批"
    assert info["timeoutSeconds"] == 5
    assert info["createdAt"] > 0


def test_first_decision_wins_repeated_resolve_conflicts():
    broker = ApprovalBroker()
    token = _request(broker)
    assert broker.resolve(token, "rejected") is True
    assert broker.resolve(token, "approved") is False
    assert broker.get(token)["decision"] == "rejected"


def test_resolve_unknown_token_is_false():
    assert ApprovalBroker().resolve("nope", "approved") is False
    assert ApprovalBroker().get("nope") is None


def test_wait_timeout_then_complete_timeout_records_timeout_source():
    broker = ApprovalBroker()
    token = _request(broker, timeout_seconds=0)
    assert broker.wait(token) is None
    outcome = broker.complete_timeout(token, "rejected")
    assert outcome == ("rejected", "timeout")
    assert broker.get(token)["resolvedBy"] == "timeout"


def test_complete_timeout_loses_to_concurrent_human_decision():
    broker = ApprovalBroker()
    token = _request(broker, timeout_seconds=0)
    assert broker.wait(token) is None
    assert broker.resolve(token, "approved") is True
    outcome = broker.complete_timeout(token, "rejected")
    assert outcome == ("approved", "human")


def test_list_pending_excludes_decided():
    broker = ApprovalBroker()
    token_a = _request(broker, node_id="human-1")
    token_b = _request(broker, node_id="human-2")
    pending = broker.list_pending()
    assert {item["token"] for item in pending} == {token_a, token_b}
    assert all(item["node_id"] in {"human-1", "human-2"} for item in pending)
    assert all(item["createdAt"] > 0 for item in pending)
    broker.resolve(token_a, "approved")
    remaining = broker.list_pending()
    assert [item["token"] for item in remaining] == [token_b]


def test_reset_clears_and_releases_waiters_as_timeout_reject():
    broker = ApprovalBroker()
    token = _request(broker, timeout_seconds=30)
    result: dict = {}
    thread = threading.Thread(target=lambda: result.update(decision=broker.wait(token)))
    thread.start()
    time.sleep(0.05)
    broker.reset()
    thread.join(timeout=1)
    assert result["decision"] == "rejected"
    assert broker.list_pending() == []
    assert broker.get(token) is None
