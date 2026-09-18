"""内置交互卡片目录与三渠道渲染测试（M8；12 §3.11 / 06 §6.16 / 13 U53）。

纯逻辑、无接线：目录不变量、web/im/email 渲染、插值 fail-soft 与 HTML 转义、
map_action_output 映射/校验、to_message_params → MessageService.send 落记录。
端到端接线（编译 422、REST 端点、中断恢复、前端）见 U54。
"""

from __future__ import annotations

import re

import pytest

from atlas.cards import (
    CARDS,
    CardAction,
    CardFallback,
    CardRenderError,
    CardTemplate,
    FieldBinding,
    FieldsSection,
    FormSection,
    get_card,
    list_cards,
    map_action_output,
    render_card,
    to_message_params,
)
from atlas.cards.catalog import REFUND_APPROVAL
from atlas.message.service import MessageService

CONTEXT = {
    "global": {"approval_limit": "500"},
    "trigger-1": {
        "context": {
            "payload": {"order_id": "R-1001", "amount": 899, "reason": "商品破损"}
        }
    },
    "ai_decision-1": {
        "decision": {
            "action": "request_human_approval",
            "reason": "非质量原因，转人工审批",
            "confidence": 1.0,
        }
    },
}

TOKEN = "tok-1"


# --------------------------------------------------------------------------- #
# 目录不变量
# --------------------------------------------------------------------------- #
def test_catalog_has_unique_kebab_ids_and_refund_card():
    ids = [card.id for card in CARDS]
    assert REFUND_APPROVAL.id == "refund-approval"
    assert get_card("refund-approval") is REFUND_APPROVAL
    assert len(ids) >= 1
    assert len(set(ids)) == len(ids)
    for card_id in ids:
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", card_id)


def test_catalog_accessors_return_copies_and_unknown_is_none():
    cards = list_cards()
    assert [card.id for card in cards] == [card.id for card in CARDS]
    assert cards is not CARDS
    assert get_card("nope") is None


def test_every_card_is_well_formed():
    for card in CARDS:
        assert card.name.strip()
        assert set(card.channels) <= {"web", "im", "email"} and card.channels
        assert card.actions, card.id
        action_ids = [action.id for action in card.actions]
        assert len(set(action_ids)) == len(action_ids), card.id
        form_names = [
            section.name
            for section in card.sections
            if isinstance(section, FormSection)
        ]
        assert len(set(form_names)) == len(form_names), card.id
        for action in card.actions:
            assert action.output.get("decision") in ("approved", "rejected"), card.id
            comment = action.output.get("comment")
            if comment is not None:
                refs = re.findall(r"\{\{\s*form\.([A-Za-z0-9_]+)\s*\}\}", comment)
                assert set(refs) <= set(form_names), (card.id, refs)


def test_sections_discriminate_by_type():
    fields_sections = [s for s in REFUND_APPROVAL.sections if isinstance(s, FieldsSection)]
    form_sections = [s for s in REFUND_APPROVAL.sections if isinstance(s, FormSection)]
    assert len(fields_sections) == 1 and fields_sections[0].bindings
    assert len(form_sections) == 1 and form_sections[0].name == "comment"


# --------------------------------------------------------------------------- #
# web 渲染
# --------------------------------------------------------------------------- #
def test_render_web_projects_fields_form_actions():
    rendered = render_card(
        REFUND_APPROVAL, CONTEXT, token=TOKEN, channel="web",
        approver="operator-a", timeout_seconds=300,
    )
    assert rendered["channel"] == "web"
    assert rendered["cardId"] == "refund-approval"
    assert rendered["token"] == TOKEN
    assert rendered["approver"] == "operator-a"
    assert rendered["timeoutSeconds"] == 300
    fields = {row["label"]: row["value"] for row in rendered["fields"]}
    assert fields["订单号"] == "R-1001"
    assert fields["退款金额"] == "899"
    assert fields["退款原因"] == "商品破损"
    assert fields["AI 建议"] == "非质量原因，转人工审批"
    assert fields["审批限额"] == "500"
    form_names = [spec["name"] for spec in rendered["form"]]
    assert form_names == ["comment"]
    assert rendered["form"][0]["type"] == "textarea"
    assert {a["id"] for a in rendered["actions"]} == {"approve", "reject"}
    styles = {a["id"]: a["style"] for a in rendered["actions"]}
    assert styles["approve"] == "primary" and styles["reject"] == "danger"


def test_render_web_keeps_missing_placeholders_soft():
    rendered = render_card(REFUND_APPROVAL, {}, token=TOKEN, channel="web")
    values = " ".join(row["value"] for row in rendered["fields"])
    assert "{{trigger-1.context.payload.order_id}}" in values
    assert "{{global.approval_limit}}" in values


def test_render_web_does_not_html_escape_values():
    ctx = {"trigger-1": {"context": {"payload": {"reason": "<b>x</b>"}}}}
    card = CardTemplate(
        id="t",
        name="t",
        channels=["web"],
        sections=[
            FieldsSection(type="fields", bindings=[FieldBinding(label="r", value="{{trigger-1.context.payload.reason}}")])
        ],
        actions=[CardAction(id="approve", label="ok", output={"decision": "approved"})],
    )
    rendered = render_card(card, ctx, token=TOKEN, channel="web")
    assert rendered["fields"][0]["value"] == "<b>x</b>"  # 前端转义，后端原样


# --------------------------------------------------------------------------- #
# im 渲染
# --------------------------------------------------------------------------- #
def test_render_im_text_buttons_and_detail_url():
    rendered = render_card(REFUND_APPROVAL, CONTEXT, token=TOKEN, channel="im")
    assert rendered["channel"] == "im"
    assert "退款审批卡片" in rendered["text"]
    assert "订单号：R-1001" in rendered["text"]
    assert rendered["detailUrl"] == f"/approvals/{TOKEN}"
    buttons = {b["id"]: b["url"] for b in rendered["buttons"]}
    assert buttons["approve"] == f"/approvals/{TOKEN}?actionId=approve"
    assert buttons["reject"] == f"/approvals/{TOKEN}?actionId=reject"


def test_render_im_uses_custom_detail_url_with_token():
    card = REFUND_APPROVAL.model_copy(
        update={"fallback": CardFallback(im={"detailUrl": "/x/{token}?src=im"})}
    )
    rendered = render_card(card, CONTEXT, token=TOKEN, channel="im")
    assert rendered["detailUrl"] == f"/x/{TOKEN}?src=im"
    urls = {b["id"]: b["url"] for b in rendered["buttons"]}
    assert urls["approve"] == f"/x/{TOKEN}?src=im&actionId=approve"


# --------------------------------------------------------------------------- #
# email 渲染
# --------------------------------------------------------------------------- #
def test_render_email_subject_html_links_and_timeout_hint():
    rendered = render_card(
        REFUND_APPROVAL, CONTEXT, token=TOKEN, channel="email", timeout_seconds=300
    )
    assert rendered["channel"] == "email"
    assert rendered["subject"] == "审批请求：退款审批卡片"
    assert "R-1001" in rendered["html"]
    assert "300" in rendered["html"]  # 超时提示
    links = {link["id"]: link["url"] for link in rendered["links"]}
    assert links["approve"] == f"/approvals/{TOKEN}?actionId=approve"


def test_render_email_escapes_interpolated_values():
    ctx = {"trigger-1": {"context": {"payload": {"reason": "<script>x</script>"}}}}
    card = CardTemplate(
        id="t",
        name="<t>",
        channels=["email"],
        sections=[
            FieldsSection(type="fields", bindings=[FieldBinding(label="r", value="{{trigger-1.context.payload.reason}}")])
        ],
        actions=[CardAction(id="approve", label="ok", output={"decision": "approved"})],
    )
    rendered = render_card(card, ctx, token=TOKEN, channel="email")
    assert "<script>" not in rendered["html"]
    assert "&lt;script&gt;" in rendered["html"]
    assert "&lt;t&gt;" in rendered["html"]


# --------------------------------------------------------------------------- #
# 渠道非法 / 不支持
# --------------------------------------------------------------------------- #
def test_render_rejects_unknown_and_unsupported_channel():
    with pytest.raises(CardRenderError):
        render_card(REFUND_APPROVAL, CONTEXT, token=TOKEN, channel="sms")
    web_only = CardTemplate(
        id="web-only",
        name="web only",
        channels=["web"],
        sections=[],
        actions=[CardAction(id="approve", label="ok", output={"decision": "approved"})],
    )
    with pytest.raises(CardRenderError):
        render_card(web_only, CONTEXT, token=TOKEN, channel="im")


# --------------------------------------------------------------------------- #
# map_action_output
# --------------------------------------------------------------------------- #
def test_map_action_output_maps_decision_and_form_comment():
    assert map_action_output(REFUND_APPROVAL, "approve", {"comment": "同意退款"}) == {
        "decision": "approved",
        "comment": "同意退款",
    }
    assert map_action_output(REFUND_APPROVAL, "reject", {"comment": ""}) == {
        "decision": "rejected",
        "comment": "",
    }
    assert map_action_output(REFUND_APPROVAL, "reject", None) == {
        "decision": "rejected",
        "comment": "",
    }


def test_map_action_output_unknown_action_raises():
    with pytest.raises(CardRenderError):
        map_action_output(REFUND_APPROVAL, "discard", {})


def test_map_action_output_illegal_decision_raises():
    card = CardTemplate(
        id="bad",
        name="bad",
        channels=["web"],
        sections=[],
        actions=[CardAction(id="go", label="go", output={"decision": "maybe"})],
    )
    with pytest.raises(CardRenderError):
        map_action_output(card, "go", {})


def test_map_action_output_required_field_enforced():
    card = CardTemplate(
        id="req",
        name="req",
        channels=["web"],
        sections=[
            FormSection(type="input", name="reason", label="理由", required=True),
        ],
        actions=[
            CardAction(
                id="approve",
                label="ok",
                output={"decision": "approved", "comment": "{{form.reason}}"},
            )
        ],
    )
    with pytest.raises(CardRenderError):
        map_action_output(card, "approve", {})
    with pytest.raises(CardRenderError):
        map_action_output(card, "approve", {"reason": "   "})
    assert map_action_output(card, "approve", {"reason": "已核实"}) == {
        "decision": "approved",
        "comment": "已核实",
    }


def test_map_action_output_without_comment_key_returns_decision_only():
    card = CardTemplate(
        id="noc",
        name="noc",
        channels=["web"],
        sections=[],
        actions=[CardAction(id="approve", label="ok", output={"decision": "approved"})],
    )
    assert map_action_output(card, "approve", {}) == {"decision": "approved"}


# --------------------------------------------------------------------------- #
# to_message_params → MessageService.send 落记录（v1 不自动外发）
# --------------------------------------------------------------------------- #
def test_to_message_params_im_records_via_message_service():
    rendered = render_card(REFUND_APPROVAL, CONTEXT, token=TOKEN, channel="im")
    params = to_message_params(rendered, to="im-user-1")
    assert params["channel"] == "im"
    assert params["to"] == "im-user-1"
    assert params["subject"]
    assert TOKEN in params["body"] and "actionId=approve" in params["body"]

    service = MessageService()
    record = service.send(**params)
    assert service.count == 1
    assert record["channel"] == "im" and record["to"] == ["im-user-1"]


def test_to_message_params_email_records_via_message_service():
    rendered = render_card(REFUND_APPROVAL, CONTEXT, token=TOKEN, channel="email")
    params = to_message_params(rendered, to=["m@example.com"])
    assert params["channel"] == "email"
    assert params["body"] == rendered["html"]

    service = MessageService()
    record = service.send(**params)
    assert service.count == 1
    assert record["subject"] == rendered["subject"]


def test_to_message_params_web_rejected():
    rendered = render_card(REFUND_APPROVAL, CONTEXT, token=TOKEN, channel="web")
    with pytest.raises(CardRenderError):
        to_message_params(rendered, to="x@example.com")
