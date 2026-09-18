"""交互卡片三渠道渲染与动作映射（M8；12 §3.11 / 06 §6.16 权威）。

纯函数、无副作用：不触达 DB、不发送消息（v1 不自动外发，调用方拿到
``to_message_params`` 产物后自行经 message/send 落记录，真实投递随 D20/D24）。

- ``bindings`` 复用 :func:`atlas.graph.interpolation.interpolate` 同一套
  ``{{路径}}`` 插值/缺失（fail-soft，占位符原样保留）语义；渲染上下文为审批
  挂起时的快照（trigger/globals/visibleAt 内 outputs）。
- 邮件/IM 链接指向**前端 GET 落地页**（``/approvals/{token}?actionId=…``），
  GET 不产生决策副作用（防邮件预取误决策）；决策一律走 POST。
- :func:`map_action_output` 是 action.output → 审批 decision/comment 的唯一
  权威，``{{form.<name>}}`` 在此回填。
"""

from __future__ import annotations

import html
import re
from typing import Any, Literal

from atlas.graph.interpolation import interpolate

from .catalog import CardAction, CardTemplate, FieldsSection, FormSection

Channel = Literal["web", "im", "email"]
_CHANNELS: tuple[str, ...] = ("web", "im", "email")
_DECISIONS: tuple[str, ...] = ("approved", "rejected")
_FORM_REF_RE = re.compile(r"\{\{\s*form\.([A-Za-z0-9_]+)\s*\}\}")
_DEFAULT_DETAIL_URL = "/approvals/{token}"


class CardRenderError(ValueError):
    """卡片渲染/动作映射非法（渠道不支持、未知动作、缺必填等）；REST 映射 422。"""


def _detail_url(card: CardTemplate, token: str) -> str:
    template = _DEFAULT_DETAIL_URL
    if card.fallback and card.fallback.im and isinstance(card.fallback.im.get("detailUrl"), str):
        template = card.fallback.im["detailUrl"]
    return template.replace("{token}", token)


def _action_url(detail_url: str, action: CardAction) -> str:
    sep = "&" if "?" in detail_url else "?"
    return f"{detail_url}{sep}actionId={action.id}"


def _fields_rows(card: CardTemplate, context: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for section in card.sections:
        if isinstance(section, FieldsSection):
            for binding in section.bindings:
                rows.append(
                    {"label": binding.label, "value": interpolate(binding.value, context)}
                )
    return rows


def _form_specs(card: CardTemplate) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for section in card.sections:
        if isinstance(section, FormSection):
            specs.append(
                {
                    "type": section.type,
                    "name": section.name,
                    "label": section.label,
                    "required": section.required,
                    "default": section.default,
                }
            )
    return specs


def _render_web(card, context, *, token, approver, timeout_seconds) -> dict[str, Any]:
    return {
        "channel": "web",
        "cardId": card.id,
        "name": card.name,
        "fields": _fields_rows(card, context),
        "form": _form_specs(card),
        "actions": [
            {"id": action.id, "label": action.label, "style": action.style}
            for action in card.actions
        ],
        "token": token,
        "approver": approver,
        "timeoutSeconds": timeout_seconds,
    }


def _render_im(card, context, *, token, approver, timeout_seconds) -> dict[str, Any]:
    detail_url = _detail_url(card, token)
    lines = [card.name, ""]
    lines.extend(f"{row['label']}：{row['value']}" for row in _fields_rows(card, context))
    if approver:
        lines.append(f"审批人：{approver}")
    return {
        "channel": "im",
        "name": card.name,
        "text": "\n".join(lines),
        "buttons": [
            {"id": action.id, "label": action.label, "url": _action_url(detail_url, action)}
            for action in card.actions
        ],
        "detailUrl": detail_url,
    }


def _render_email(card, context, *, token, approver, timeout_seconds) -> dict[str, Any]:
    detail_url = _detail_url(card, token)
    title = html.escape(card.name)
    rows = "".join(
        f"<tr><th style='text-align:left'>{html.escape(row['label'])}</th>"
        f"<td>{html.escape(str(row['value']))}</td></tr>"
        for row in _fields_rows(card, context)
    )
    links = [
        {"id": action.id, "label": action.label, "url": _action_url(detail_url, action)}
        for action in card.actions
    ]
    link_html = " ".join(
        f"<a href='{html.escape(link['url'], quote=True)}'>{html.escape(link['label'])}</a>"
        for link in links
    )
    hint = ""
    show_hint = bool(card.fallback and card.fallback.email and card.fallback.email.get("timeoutHint"))
    if show_hint and timeout_seconds:
        hint = (
            f"<p style='color:#888'>若 {int(timeout_seconds)} 秒内未处理，"
            "将按流程超时策略自动处理。</p>"
        )
    body = (
        f"<h2>{title}</h2>"
        f"<table>{rows}</table>"
        f"<p>{link_html}</p>"
        f"<p style='color:#888'>详情与在线审批：{html.escape(detail_url)}</p>"
        f"{hint}"
    )
    return {
        "channel": "email",
        "subject": f"审批请求：{card.name}",
        "html": body,
        "links": links,
    }


def render_card(
    card: CardTemplate,
    context: dict[str, Any] | None,
    *,
    token: str,
    channel: str,
    approver: str = "",
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """按渠道渲染卡片。``context`` 为审批挂起时快照；非法/不支持渠道抛 CardRenderError。"""

    if channel not in _CHANNELS:
        raise CardRenderError(f"非法渲染渠道：{channel!r}（仅支持 web/im/email）")
    if channel not in card.channels:
        raise CardRenderError(f"卡片 {card.id} 不支持渠道 {channel}")
    ctx = context or {}
    if channel == "web":
        return _render_web(card, ctx, token=token, approver=approver, timeout_seconds=timeout_seconds)
    if channel == "im":
        return _render_im(card, ctx, token=token, approver=approver, timeout_seconds=timeout_seconds)
    return _render_email(card, ctx, token=token, approver=approver, timeout_seconds=timeout_seconds)


def _render_form_refs(template: str, form_values: dict[str, Any]) -> str:
    """把 ``{{form.<name>}}`` 替换为表单值；缺失/为空一律替换为空串（必填另校验）。"""

    def replace(match: re.Match[str]) -> str:
        value = form_values.get(match.group(1), "")
        return "" if value is None else str(value)

    return _FORM_REF_RE.sub(replace, template)


def map_action_output(
    card: CardTemplate, action_id: str, form_values: dict[str, Any] | None
) -> dict[str, Any]:
    """按 ``action.output`` 映射为 ``{decision, comment?}``。

    未知 action、decision 非 approved/rejected、缺必填表单字段均抛
    CardRenderError（REST 映射 422）。``{{form.*}}`` 回填 comment。
    """

    action = next((item for item in card.actions if item.id == action_id), None)
    if action is None:
        raise CardRenderError(f"卡片 {card.id} 未知动作：{action_id!r}")
    output = action.output or {}
    decision = output.get("decision")
    if decision not in _DECISIONS:
        raise CardRenderError(f"卡片动作 {action_id!r} 的 decision 非法：{decision!r}")

    values: dict[str, Any] = form_values or {}
    for section in card.sections:
        if isinstance(section, FormSection) and section.required:
            raw = values.get(section.name)
            if raw is None or not str(raw).strip():
                label = section.label or section.name
                raise CardRenderError(f"请填写必填项：{label}")

    result: dict[str, Any] = {"decision": decision}
    if "comment" in output:
        result["comment"] = _render_form_refs(str(output["comment"]), values)
    return result


def to_message_params(rendered: dict[str, Any], *, to: object) -> dict[str, Any]:
    """把 im/email 渲染产物转为 message/send 入参 ``{channel,to,subject,body}``。

    web 渠道由前端拉取渲染、不走 message/send，调用即抛 CardRenderError。
    v1 不自动外发，本函数只产出参数（``to`` 的合法性由 MessageService 校验）。
    """

    channel = rendered.get("channel")
    if channel == "im":
        body = rendered["text"]
        buttons = rendered.get("buttons") or []
        if buttons:
            body = body + "\n\n" + "\n".join(f"{b['label']}：{b['url']}" for b in buttons)
        return {
            "channel": "im",
            "to": to,
            "subject": rendered.get("name") or rendered.get("subject") or "交互卡片通知",
            "body": body,
        }
    if channel == "email":
        return {
            "channel": "email",
            "to": to,
            "subject": rendered["subject"],
            "body": rendered["html"],
        }
    raise CardRenderError(f"渠道 {channel!r} 不经 message/send 外发（web 由前端拉取渲染）")
