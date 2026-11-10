# -*- coding: utf-8 -*-
"""Fast unit tests for atlas.services.approval_decision.

不启 TestClient：wait 事件载荷校验、登录态/邮件深链共用的决策应用函数
（docs/36 §3）与邮件 token 收件人归属校验（docs/64 J-1b）的等价行为。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from atlas.collaboration.email_token import EmailTokenError
from atlas.services import approval_decision


# --- parse_wait_event_key / parse_wait_payload --------------------------------

def test_parse_wait_event_key_accepts_valid_and_rejects_invalid() -> None:
    assert approval_decision.parse_wait_event_key({"eventKey": "order:refund_1"}) == "order:refund_1"
    with pytest.raises(HTTPException) as exc:
        approval_decision.parse_wait_event_key({"eventKey": "bad key!"})
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "WAIT_EVENT_KEY_INVALID"


def test_parse_wait_payload_bounds_and_passthrough() -> None:
    assert approval_decision.parse_wait_payload({"payload": None}) == {}
    raw = {"order": "123", "reason": "破损"}
    assert approval_decision.parse_wait_payload({"payload": raw}) == raw

    with pytest.raises(HTTPException) as not_obj:
        approval_decision.parse_wait_payload({"payload": ["x"]})
    assert not_obj.value.detail["code"] == "WAIT_EVENT_PAYLOAD_NOT_OBJECT"

    too_many = {"payload": {f"k{i}": 1 for i in range(approval_decision.MAX_WAIT_PAYLOAD_KEYS + 1)}}
    with pytest.raises(HTTPException) as many:
        approval_decision.parse_wait_payload(too_many)
    assert many.value.detail["code"] == "WAIT_EVENT_PAYLOAD_TOO_MANY_KEYS"
    assert many.value.detail["params"]["max"] == approval_decision.MAX_WAIT_PAYLOAD_KEYS

    too_big = {"payload": {"blob": "x" * (approval_decision.MAX_WAIT_PAYLOAD_BYTES + 1)}}
    with pytest.raises(HTTPException) as big:
        approval_decision.parse_wait_payload(too_big)
    assert big.value.detail["code"] == "WAIT_EVENT_PAYLOAD_TOO_LARGE"


# --- apply_approval_decision ---------------------------------------------------

class _Broker:
    def __init__(self, resolve_ok=True, recipients=("a@example.com",)):
        self.resolve_ok = resolve_ok
        self.recipients = list(recipients)
        self.resolved: dict | None = None

    def resolve(self, token, decision, *, comment=None, action_id=None):
        if not self.resolve_ok:
            return False
        self.resolved = {"token": token, "decision": decision, "comment": comment, "action_id": action_id}
        return True

    def get_notify_recipients(self, token):
        return self.recipients


def test_apply_decision_rejects_repeat_and_missing_decision() -> None:
    broker = _Broker()
    with pytest.raises(HTTPException) as repeat:
        approval_decision.apply_approval_decision(
            broker, {"decision": "approved"}, "tok",
            decision="rejected", comment="", action_id=None, form=None,
        )
    assert repeat.value.status_code == 409
    assert "重复提交不生效" in repeat.value.detail

    with pytest.raises(HTTPException) as missing:
        approval_decision.apply_approval_decision(
            _Broker(), {}, "tok",
            decision=None, comment="", action_id=None, form=None,
        )
    assert missing.value.status_code == 422
    assert "请提供 decision" in missing.value.detail


def test_apply_decision_resolves_and_records_action_id() -> None:
    broker = _Broker()
    result = approval_decision.apply_approval_decision(
        broker, {"graph_id": "g1", "node_id": "n1"}, "tok",
        decision="approved", comment="同意", action_id=None, form=None,
    )
    assert result == {"token": "tok", "decision": "approved", "resolvedBy": "human"}
    assert broker.resolved == {"token": "tok", "decision": "approved", "comment": "同意", "action_id": None}


def test_apply_decision_conflict_when_broker_refuses() -> None:
    with pytest.raises(HTTPException) as exc:
        approval_decision.apply_approval_decision(
            _Broker(resolve_ok=False), {}, "tok",
            decision="approved", comment="", action_id=None, form=None,
        )
    assert exc.value.status_code == 409


def test_apply_decision_notifier_failure_is_fail_safe(caplog) -> None:
    class BoomNotifier:
        def notify_decided(self, **kwargs):
            raise RuntimeError("smtp down")

    broker = _Broker()
    token = "ap-" + "q" * 28
    with caplog.at_level(logging.WARNING, logger="atlas.services.approval_decision"):
        result = approval_decision.apply_approval_decision(
            broker, {"graph_id": "g1", "node_id": "human-1"}, token,
            decision="approved", comment="同意", action_id=None, form=None,
            notifier=BoomNotifier(),
        )
    assert result["decision"] == "approved"  # 通知失败不改决策事实
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "smtp down" in joined
    assert token not in joined  # 全量 token 不得进日志
    assert token[:8] in joined  # 8 位可读引用必须在


def test_apply_decision_without_notifier_skips_recipient_lookup() -> None:
    class _StrictBroker(_Broker):
        def get_notify_recipients(self, token):
            raise AssertionError("no notifier ⇒ no recipient lookup")

    result = approval_decision.apply_approval_decision(
        _StrictBroker(), {}, "tok",
        decision="rejected", comment="", action_id=None, form=None,
    )
    assert result["decision"] == "rejected"


# --- resolve_email_signed / assert_email_token_recipient ----------------------

class _Issuer:
    def __init__(self, payload=None, error=False):
        self._payload = payload or {}
        self._error = error

    def verify(self, signed):
        if self._error:
            raise EmailTokenError("bad signature")
        return self._payload


def test_resolve_email_signed_all_failures_are_uniform_404() -> None:
    link_invalid = approval_decision.EMAIL_LINK_INVALID
    with pytest.raises(HTTPException) as bad_sig:
        approval_decision.resolve_email_signed(
            "x", token_issuer=_Issuer(error=True), peek_registry=lambda t: None
        )
    assert bad_sig.value.status_code == 404 and bad_sig.value.detail == link_invalid

    with pytest.raises(HTTPException) as unknown_tenant:
        approval_decision.resolve_email_signed(
            "x", token_issuer=_Issuer({"tenant": "t1", "rcpt": "a@x.com", "at": "tok"}),
            peek_registry=lambda t: None,
        )
    assert unknown_tenant.value.status_code == 404 and unknown_tenant.value.detail == link_invalid

    # docs/64 J-1b：无 rcpt 的旧式 token 一律失效
    with pytest.raises(HTTPException) as legacy:
        approval_decision.resolve_email_signed(
            "x", token_issuer=_Issuer({"tenant": "t1", "at": "tok"}),
            peek_registry=lambda t: SimpleNamespace(),
        )
    assert legacy.value.status_code == 404 and legacy.value.detail == link_invalid


def test_resolve_email_signed_returns_services_token_and_tenant() -> None:
    services = SimpleNamespace(marker="svc")
    issuer = _Issuer({"tenant": "t9", "rcpt": "a@x.com", "at": "approval-tok"})
    got, approval_token, tenant_id = approval_decision.resolve_email_signed(
        "signed", token_issuer=issuer, peek_registry=lambda t: services if t == "t9" else None
    )
    assert got is services and approval_token == "approval-tok" and tenant_id == "t9"


def test_assert_email_token_recipient_enforces_membership() -> None:
    broker = _Broker(recipients=["A@Example.com"])
    issuer = _Issuer({"rcpt": "a@example.com"})
    approval_decision.assert_email_token_recipient(
        "signed", "tok", broker, token_issuer=issuer
    )  # 大小写归一后命中，不抛

    with pytest.raises(HTTPException) as exc:
        approval_decision.assert_email_token_recipient(
            "signed", "tok", broker, token_issuer=_Issuer({"rcpt": "b@example.com"})
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == approval_decision.EMAIL_LINK_INVALID
