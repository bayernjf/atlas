"""退款决策客户端（T1：LiteLLM 接商业 API；无 key 时规则兜底）。

W9-W10 对齐 docs/06 §9.2 黄金用例：
- 商品破损等质量原因且金额 ≤ 审批限额 → approve_refund
- 其余（主观原因/超限额/解析失败）→ request_human_approval（fail-safe）

模型经 LiteLLM 调用，供应商/key 由环境变量切换（10 文档 T1）；
未配置 `LITELLM_MODEL` 时使用规则决策，保证 Demo 离线可跑。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Protocol

logger = logging.getLogger(__name__)

AUTO_APPROVE = "approve_refund"
HUMAN_APPROVAL = "request_human_approval"

_QUALITY_REASONS = ("破损", "损坏", "质量", "瑕疵", "残次", "漏发", "错发", "假货", "缺陷")
_DEFAULT_LIMIT = 500.0
_DECISION_RE = re.compile(r"\{.*\}", re.DOTALL)


class DecisionClient(Protocol):
    def decide_refund(self, *, reason: str, amount: float, limit: float) -> dict[str, Any]: ...


class RuleBasedDecisionClient:
    """确定性规则决策（黄金测试集与离线 Demo 使用）。"""

    def decide_refund(self, *, reason: str, amount: float, limit: float) -> dict[str, Any]:
        is_quality = any(keyword in reason for keyword in _QUALITY_REASONS)
        if is_quality and amount <= limit:
            return {
                "action": AUTO_APPROVE,
                "reason": f"质量原因（{reason}）且金额 {amount:g} ≤ 限额 {limit:g}，自动退款",
                "confidence": 1.0,
                "source": "rule",
            }
        if not is_quality:
            detail = f"非质量原因（{reason}），转人工审批"
        else:
            detail = f"质量原因但金额 {amount:g} 超过限额 {limit:g}，转人工审批"
        return {"action": HUMAN_APPROVAL, "reason": detail, "confidence": 1.0, "source": "rule"}


class LiteLLMDecisionClient:
    """经 LiteLLM 的 LLM 决策；输出无法解析时 fail-safe 转人工。"""

    def __init__(self, model: str):
        self.model = model

    def decide_refund(self, *, reason: str, amount: float, limit: float) -> dict[str, Any]:
        import litellm

        prompt = (
            "你是电商售后审批员。根据退款原因和金额决定动作，只输出 JSON：\n"
            '{"action": "approve_refund" 或 "request_human_approval", "reason": "中文简述", '
            '"confidence": 0到1的数字}\n'
            f"规则：商品质量问题（破损/质量缺陷/错漏发等）且金额不超过 {limit:g} 元才自动退款；"
            f"主观原因（不想要了等）或超限额必须转人工。\n"
            f"退款原因：{reason}\n退款金额：{amount:g}"
        )
        response = litellm.completion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            # J-3c：出向 LLM 必须带超时与输出上限，防挂死/超长回包拖垮编排
            timeout=float(os.getenv("ATLAS_LLM_TIMEOUT_SECONDS", "60")),
            max_tokens=512,
        )
        content = response["choices"][0]["message"]["content"]
        try:
            match = _DECISION_RE.search(content)
            payload = json.loads(match.group(0) if match else content)
            action = payload["action"]
            if action not in (AUTO_APPROVE, HUMAN_APPROVAL):
                raise ValueError(f"未知动作：{action}")
            return {
                "action": action,
                "reason": str(payload.get("reason", "")),
                "confidence": float(payload.get("confidence", 0.0)),
                "source": f"llm:{self.model}",
            }
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return {
                "action": HUMAN_APPROVAL,
                "reason": f"LLM 输出解析失败，fail-safe 转人工：{exc}",
                "confidence": 0.0,
                "source": f"llm:{self.model}",
            }


_decision_mode_logged = False


def get_decision_client() -> DecisionClient:
    """构建决策器；首次调用打印运行模式（docs/64 J-2a：不再静默降级）。"""
    global _decision_mode_logged
    model = os.getenv("LITELLM_MODEL", "").strip()
    if model:
        client: DecisionClient = LiteLLMDecisionClient(model)
        if not _decision_mode_logged:
            logger.info("decision client: LiteLLM(model=%s)", model)
            _decision_mode_logged = True
        return client
    if not _decision_mode_logged:
        logger.warning(
            "ATLAS 运行于规则决策降级模式（LITELLM_MODEL 未配置），无 LLM 语义判断；"
            "如需真实 LLM，请在 .env 配置 LITELLM_MODEL 与供应商 key（docs/64 J-2a）"
        )
        _decision_mode_logged = True
    return RuleBasedDecisionClient()
