"""打包 AB（docs/109）：意图识别 / 信息抽取 / 内容生成三节点共用 LLM 结构化客户端。

三个纯函数客户端（classify_intent / extract_fields / generate_content）走 model_config
per-tenant 解析（第 5 个消费点，docs/93 打包 Y 先例），与 decision.py 同构：
- 无可用模型（未配 LITELLM_MODEL 且无租户 BYOK/内置）→ 抛 :class:`LLMStructuredUnavailable`
  （loader 显式 FAILED；三节点无规则兜底，demo 面也不豁免，docs/109 §2.2）；
- LLM 返回非法形状 → 本地兜底（intent=None / fields={} / text 兜底文案），记 log，不 FAILED；
- BYOK/内置显式传 key/base_url；None 时 litellm 走 env。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from atlas.llm.metering import complete_with_usage

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_PROMPT = (
    "你是结构化输出助手。严格按用户指令返回 JSON，不要输出 JSON 之外的任何内容。"
)


class LLMStructuredUnavailable(Exception):
    """执行三节点却没有可用的 LLM 模型（docs/109 §2.2）。

    与 AiDecisionUnavailable 同形：离线档不是"模型这次调用失败"而是"根本没接模型"，
    此时静默给空结果会伪造下游行为；run 显式 FAILED 带机器码。
    """

    code = "LLM_STRUCTURED_UNAVAILABLE"

    def __init__(self, node_id: str, message: str) -> None:
        super().__init__(f"{self.code}: {message}")
        self.node_id = node_id


def _resolve_llm_config(tenant_id: str | None) -> tuple[str, str | None, str | None] | None:
    """按优先级解析模型（docs/93 §1 语义约束 3）：租户 BYOK > 内置 > env。"""
    if tenant_id is None:
        return None
    from atlas.connections.service import get_secret_provider
    from atlas.llm.config import get_model_config_store, resolve_default_model

    cfg = resolve_default_model(get_model_config_store(), tenant_id)
    if cfg is None:
        return None
    api_key = get_secret_provider().decrypt(cfg.api_key_enc) if cfg.api_key_enc else None
    return cfg.model, api_key, cfg.base_url


def _call_llm(
    prompt: str,
    *,
    model: str,
    api_key: str | None,
    base_url: str | None,
    caller: str,
    max_tokens: int = 800,
    temperature: float = 0,
) -> str:
    """三节点与 auto_extract 共用的唯一 LLM 出口（docs/129 打包 AU：usage 计量旁路）。

    `caller` 由四个消费方各自传入（intent_recognition/info_extraction/
    content_generation/memory_auto_extract）——共用出口不共用身份。
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        # J-3c 同款：出向 LLM 必须带超时与输出上限
        "timeout": float(os.getenv("ATLAS_LLM_TIMEOUT_SECONDS", "60")),
        "max_tokens": max_tokens,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    content, _usage = complete_with_usage(kwargs, caller=caller, model=model)
    return content


def call_structured_json(
    prompt: str,
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    caller: str,
    max_tokens: int = 800,
    temperature: float = 0,
) -> str:
    """**通用**结构化 JSON 出口（打包 BN 的圈终止判定用它，docs/130 C-D4）。

    与四消费方共用 `_call_llm` 这一个 litellm 出口（AU-D3 单点纪律）——所以圈判定的
    成本也自动进 AU 的账，caller 记 `cycle_judge`。
    """
    return _call_llm(
        prompt,
        model=model,
        api_key=api_key,
        base_url=base_url,
        caller=caller,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def resolve_model_for_structured(
    tenant_id: str | None, model: str | None, *, caller: str
) -> tuple[str, str | None, str | None]:
    """`_resolve_model` 的公开出口（跨包调用不碰私有名；语义逐字相同）。"""
    return _resolve_model(tenant_id, model, caller=caller)


def _parse_json(content: str) -> dict[str, Any] | None:
    try:
        match = _JSON_RE.search(content)
        payload = json.loads(match.group(0) if match else content)
        if isinstance(payload, dict):
            return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return None


def classify_intent(
    *,
    text: str,
    intents: list[dict[str, Any]],
    examples: list[str] | None = None,
    tenant_id: str | None = None,
    model: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """意图识别（docs/109 §2.1）：返回 {intent, confidence, slots}。

    无模型抛 LLMStructuredUnavailable；LLM 非法形状兜底 {intent: None, confidence: 0.0,
    slots: {}}（不 FAILED，记 llm_errors）。
    """
    resolved = _resolve_llm_config(tenant_id)
    if resolved is None:
        env_model = os.getenv("LITELLM_MODEL", "").strip()
        if not env_model:
            raise LLMStructuredUnavailable(
                node_id or "intent_recognition",
                "未配置 LLM 模型，意图识别节点拒绝静默降级；请配置 LITELLM_MODEL "
                "与 OPENAI_API_KEY / OPENAI_BASE_URL，或租户 BYOK 模型配置。",
            )
        model_name, api_key, base_url = env_model, None, None
    else:
        model_name, api_key, base_url = resolved
    model_name = (model or "").strip() or model_name

    intent_lines = []
    for index, intent in enumerate(intents or []):
        name = str(intent.get("name", ""))
        desc = str(intent.get("description", "") or "")
        intent_lines.append(f"- {name}" + (f"：{desc}" if desc else ""))
    examples_text = ""
    if examples:
        examples_text = "\n示例：\n" + "\n".join(f"- {item}" for item in examples)
    prompt = (
        "对下列文本做意图识别。从给定意图中选择最匹配的一个，只输出 JSON：\n"
        '{"intent": "<意图名，未匹配用 null>", "confidence": 0到1数字, "slots": {键值字符串对象}}'
        f"\n可选意图：\n{chr(10).join(intent_lines) or '（无）'}"
        f"{examples_text}\n文本：{text}"
    )
    try:
        content = _call_llm(
            prompt, model=model_name, api_key=api_key, base_url=base_url,
            caller="intent_recognition",
        )
    except Exception as exc:  # noqa: BLE001 - 调用异常统一按解析失败兜底
        logger.warning("classify_intent: LLM 调用失败（model=%s）兜底：%s", model_name, exc)
        return {"intent": None, "confidence": 0.0, "slots": {}}
    payload = _parse_json(content)
    if payload is None:
        return {"intent": None, "confidence": 0.0, "slots": {}}
    try:
        intent = payload.get("intent")
        if intent is not None:
            names = {str(i.get("name")) for i in (intents or [])}
            if intent not in names:
                intent = None
        slots = payload.get("slots")
        if not isinstance(slots, dict):
            slots = {}
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        return {"intent": None, "confidence": 0.0, "slots": {}}
    return {"intent": intent, "confidence": confidence, "slots": dict(slots)}


def extract_fields(
    *,
    text: str,
    fields: list[dict[str, Any]],
    tenant_id: str | None = None,
    model: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """信息抽取（docs/109 §2.1）：返回 {fields, missing}。

    缺字段列入 missing；无模型抛 LLMStructuredUnavailable。
    """
    resolved = _resolve_llm_config(tenant_id)
    if resolved is None:
        env_model = os.getenv("LITELLM_MODEL", "").strip()
        if not env_model:
            raise LLMStructuredUnavailable(
                node_id or "info_extraction",
                "未配置 LLM 模型，信息抽取节点拒绝静默降级；请配置 LITELLM_MODEL "
                "与 OPENAI_API_KEY / OPENAI_BASE_URL，或租户 BYOK 模型配置。",
            )
        model_name, api_key, base_url = env_model, None, None
    else:
        model_name, api_key, base_url = resolved
    model_name = (model or "").strip() or model_name

    field_lines = []
    for field in fields or []:
        name = str(field.get("name", ""))
        ftype = str(field.get("type", "string"))
        desc = str(field.get("description", "") or "")
        field_lines.append(f"- {name}（{ftype}）" + (f"：{desc}" if desc else ""))
    prompt = (
        "从下列文本中抽取字段，只输出 JSON：\n"
        '{"fields": {字段名: 值}}（无法抽取的字段省略，不得伪造）'
        f"\n字段清单：\n{chr(10).join(field_lines) or '（无）'}\n文本：{text}"
    )
    try:
        content = _call_llm(
            prompt, model=model_name, api_key=api_key, base_url=base_url,
            caller="info_extraction",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract_fields: LLM 调用失败（model=%s）兜底：%s", model_name, exc)
        return {"fields": {}, "missing": [str(f.get("name")) for f in (fields or [])]}
    payload = _parse_json(content)
    extracted: dict[str, Any] = {}
    missing: list[str] = []
    if payload is not None:
        raw = payload.get("fields")
        if isinstance(raw, dict):
            for field in fields or []:
                name = str(field.get("name", ""))
                if name in raw:
                    extracted[name] = raw[name]
                else:
                    missing.append(name)
        else:
            missing = [str(f.get("name")) for f in (fields or [])]
    else:
        missing = [str(f.get("name")) for f in (fields or [])]
    return {"fields": extracted, "missing": missing}


def generate_content(
    *,
    context: str,
    template: str,
    style: str | None = None,
    max_length: int = 800,
    tenant_id: str | None = None,
    model: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """内容生成（docs/109 §2.1）：返回 {text, prompt_rendered}。

    无模型抛 LLMStructuredUnavailable；调用失败兜底原文 template（不 FAILED）。
    """
    resolved = _resolve_llm_config(tenant_id)
    if resolved is None:
        env_model = os.getenv("LITELLM_MODEL", "").strip()
        if not env_model:
            raise LLMStructuredUnavailable(
                node_id or "content_generation",
                "未配置 LLM 模型，内容生成节点拒绝静默降级；请配置 LITELLM_MODEL "
                "与 OPENAI_API_KEY / OPENAI_BASE_URL，或租户 BYOK 模型配置。",
            )
        model_name, api_key, base_url = env_model, None, None
    else:
        model_name, api_key, base_url = resolved
    model_name = (model or "").strip() or model_name

    style_text = f"\n风格：{style}" if (style or "").strip() else ""
    prompt_rendered = (
        f"按模板生成内容，只输出正文（不输出 JSON）：\n模板：{template}"
        f"{style_text}\n上下文：{context}\n长度不超过 {max_length} 字"
    )
    try:
        content = _call_llm(
            prompt_rendered,
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            caller="content_generation",
            max_tokens=max(64, min(max_length, 4000)),
            temperature=0.7,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("generate_content: LLM 调用失败（model=%s）兜底模板原文：%s", model_name, exc)
        return {"text": template, "prompt_rendered": prompt_rendered}
    text = content.strip()
    return {"text": text, "prompt_rendered": prompt_rendered}


def _resolve_model(tenant_id: str | None, model: str | None, *, caller: str) -> tuple[str, str | None, str | None]:
    """模型解析（三节点与 auto_extract 共用）：租户 BYOK > 内置 > env；都没有就显式抛错。

    抛错而不是返回空：调用方要么是把错折算成节点 FAILED（三节点），要么是折算成产出里的
    `skipped`（记忆工具），两条路都**要求"根本没接模型"这件事被说出来**——静默空结果会伪造
    下游行为（docs/109 §2.2、docs/63 静默降级族）。
    """
    resolved = _resolve_llm_config(tenant_id)
    if resolved is None:
        env_model = os.getenv("LITELLM_MODEL", "").strip()
        if not env_model:
            raise LLMStructuredUnavailable(
                caller,
                "未配置 LLM 模型；请配置 LITELLM_MODEL 与 OPENAI_API_KEY / OPENAI_BASE_URL，"
                "或给租户配 BYOK 模型。",
            )
        model_name, api_key, base_url = env_model, None, None
    else:
        model_name, api_key, base_url = resolved
    return ((model or "").strip() or model_name), api_key, base_url


def extract_memories(
    *,
    text: str,
    tenant_id: str | None = None,
    model: str | None = None,
    caller: str = "memory_auto_extract",
) -> dict[str, Any]:
    """从一段文本里提取可长期保留的事实与偏好（docs/122 D-2）。

    返回 `{"facts": [{content, confidence}], "preferences": [...]}`；置信度由模型给，
    **阈值门槛在调用方**（memory 工具），这样阈值是可配参数而不是埋在 LLM 层里的常数。
    模型不可用抛 :class:`LLMStructuredUnavailable`；这次调用失败或返回坏 JSON 则返回空集合
    并记 warning（与 extract_fields 同族：形状问题不炸运行）。
    """
    model_name, api_key, base_url = _resolve_model(tenant_id, model, caller=caller)
    prompt = (
        "从下面文本中提取值得长期保留的信息，只输出 JSON：\n"
        '{"facts": [{"content": "客观事实", "confidence": 0.0-1.0}], '
        '"preferences": [{"content": "主体明确表达的偏好", "confidence": 0.0-1.0}]}\n'
        "要求：只提取文本里明确存在的信息，不得推测或补全；每条不超过 200 字；"
        "没有可提取项就返回空数组。\n"
        f"文本：{text}"
    )
    try:
        content = _call_llm(
            prompt, model=model_name, api_key=api_key, base_url=base_url,
            caller="memory_auto_extract",
        )
    except Exception as exc:  # noqa: BLE001 单次调用失败按"这次没提取到"，不炸运行
        logger.warning("extract_memories: LLM 调用失败（model=%s）按空结果处理：%s", model_name, exc)
        return {"facts": [], "preferences": [], "model": model_name}
    payload = _parse_json(content)
    if payload is None:
        logger.warning("extract_memories: 模型返回不是 JSON，按空结果处理（model=%s）", model_name)
        return {"facts": [], "preferences": [], "model": model_name}
    return {
        "facts": _memory_candidates(payload.get("facts")),
        "preferences": _memory_candidates(payload.get("preferences")),
        "model": model_name,
    }


def _memory_candidates(raw: Any) -> list[dict[str, Any]]:
    """归一化一组候选：丢空内容、把置信度折进 0-1（缺省按 0 处理＝宁缺不滥）。"""
    if not isinstance(raw, list):
        return []
    candidates: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        candidates.append({"content": content[:2000], "confidence": max(0.0, min(1.0, confidence))})
    return candidates


def generate_adapter_definition(
    *,
    prompt: str,
    tenant_id: str | None = None,
    model: str | None = None,
    caller: str = "adapter_draft",
) -> dict[str, Any]:
    """NL 造工具：从业务描述生成 `AdapterDefinition` 草稿（docs/133 BQ-D5，docs/107 §3.2 闭合）。

    走既有 `_call_llm` 单点（AU-D3 记账自动进账）；**失败显式不静默**（与 extract_memories
    的"形状问题不炸运行"相反：草稿必须有效才能进 dry-run/采纳，坏形状直接报错由 API 层折
    422）——模型不可用/调用失败/坏 JSON 均抛 :class:`LLMStructuredUnavailable`。
    返回 AdapterDefinition 的 dict 形态（shape 校验在 harness/definition.py 调用方执行）。
    """
    model_name, api_key, base_url = _resolve_model(tenant_id, model, caller=caller)
    instruction = (
        "把下面的业务描述转换成一个可执行的 HTTP API 适配器定义，只输出 JSON：\n"
        '{"name": "适配器名（如 shopify-orders-api）", "description": "一句话说明", '
        '"base_url": "https://...", "auth_type": "none|header|query", '
        '"permission": "read|write|delete|financial", '
        '"capabilities": [{"name": "能力名（如 get_orders）", "description": "说明", '
        '"method": "GET|POST|PUT|PATCH|DELETE", "path": "/orders/{id}", '
        '"input_schema": {"type": "object", "properties": {...}}, '
        '"output_schema": {"type": "object", "properties": {...}}, '
        '"permission": "read", "is_idempotent": false}]}\n'
        "要求：capabilities 至少一条；path 以 / 开头；auth_type/permission 只能取枚举值；"
        "不要发明 base_url——业务描述没给就保留用户给出的平台地址。\n"
        f"业务描述：{prompt}"
    )
    try:
        content = _call_llm(
            instruction, model=model_name, api_key=api_key, base_url=base_url,
            caller=caller, max_tokens=1200,
        )
    except Exception as exc:  # noqa: BLE001 单次调用失败显式报错，不静默降级
        raise LLMStructuredUnavailable(
            caller,
            f"生成适配器草稿时 LLM 调用失败：{type(exc).__name__}: {exc}",
        ) from exc
    payload = _parse_json(content)
    if payload is None:
        raise LLMStructuredUnavailable(
            caller, "生成适配器草稿时 LLM 返回的不是 JSON，拒绝静默降级"
        )
    return payload
