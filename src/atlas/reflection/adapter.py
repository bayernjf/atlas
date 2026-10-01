"""反思摘要器接缝（docs/88 D-7）。

把「证据 → 建议」这一步隔离成一个 Protocol，v1 两个实现：

- `NullSummarizer`：**确定性降级**。未配 `LITELLM_MODEL` 时使用，返回零建议零提示词，
  pass 于是收尾为 `status="ok"` 且无候选——**不报错、不静默失败**（同 `llm/decision.py`
  的规则兜底精神，docs/64 J-2a：运行模式必须打印，不静默降级）。
- `LiteLLMSummarizer`：经 LiteLLM 调商业 API（T1 选型，见 docs/10 §3），要求结构化 JSON；
  输出无法解析时 fail-safe 回退为零建议（**宁可不出建议，也不出无法校验的建议**）。

白名单与区间**不在这一层把关**：摘要器可以返回任何 `param_key`，由
`candidate.validate_changes` 统一 fail-closed 拒绝（越权与越界都在那里整份作废）。
本层只负责「把模型的话变成结构」，`from` 缺失时按白名单 `current` 补齐。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Protocol

from .evidence import ReflectionEvidence
from .tunables import TUNABLE_WHITELIST

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_MESSAGE = (
    "你是工作流运营的复盘助手。根据给定的运行证据，提出**少量**参数调整建议与提示词改进建议。"
    "你只能输出一个 JSON 对象，不要输出任何解释性文字。字段："
    '{"changes": [{"param_key": "允许的参数名", "to": 数值或字符串, "reason": "中文简述"}], '
    '"prompt_suggestions": ["中文建议文本"]}。'
    "参数名必须来自允许列表，数值必须在允许区间内；没有把握就给空数组。"
)


class Summarizer(Protocol):
    def summarize(self, evidence: ReflectionEvidence) -> tuple[list[dict[str, Any]], list[str]]: ...


class NullSummarizer:
    """确定性降级：零建议、零提示词（未配 LLM 的默认档）。"""

    def summarize(self, evidence: ReflectionEvidence) -> tuple[list[dict[str, Any]], list[str]]:
        return [], []


class LiteLLMSummarizer:
    """经 LiteLLM 的反思摘要器；输出无法解析时 fail-safe 回零建议。"""

    def __init__(self, model: str):
        self.model = model

    def _user_content(self, evidence: ReflectionEvidence) -> str:
        allowed = [
            {
                "param_key": param.key,
                "scope": param.scope,
                "current": param.current,
                "min": param.bounds.min,
                "max": param.bounds.max,
            }
            for param in TUNABLE_WHITELIST.values()
        ]
        return (
            "允许的参数列表（JSON）：\n"
            + json.dumps(allowed, ensure_ascii=False)
            + "\n\n本次证据（JSON）：\n"
            + evidence.model_dump_json(indent=2)
        )

    def summarize(self, evidence: ReflectionEvidence) -> tuple[list[dict[str, Any]], list[str]]:
        import litellm

        response = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_MESSAGE},
                {"role": "user", "content": self._user_content(evidence)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            timeout=float(os.getenv("ATLAS_LLM_TIMEOUT_SECONDS", "60")),
            max_tokens=1024,
        )
        content = response["choices"][0]["message"]["content"]
        try:
            match = _JSON_RE.search(content)
            payload = json.loads(match.group(0) if match else content)
            return _parse_payload(payload)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("reflection summarizer 输出解析失败，fail-safe 回零建议：%s", exc)
            return [], []


def _parse_payload(payload: object) -> tuple[list[dict[str, Any]], list[str]]:
    """把模型回包规整成 `(changes, prompt_suggestions)`；坏行逐条丢弃，不整份报错。

    只保留「改成什么」；`from`（当前值）由 `candidate.to_change` 统一按白名单补齐。
    """
    if not isinstance(payload, dict):
        raise ValueError(f"回包不是 JSON 对象：{type(payload).__name__}")
    changes: list[dict[str, Any]] = []
    for row in payload.get("changes") or []:
        if not isinstance(row, dict):
            continue
        key = row.get("param_key")
        if not isinstance(key, str) or "to" not in row:
            continue
        changes.append({"param_key": key, "to": row["to"], "reason": str(row.get("reason", ""))})
    raw_suggestions = payload.get("prompt_suggestions") or []
    suggestions = [row for row in raw_suggestions if isinstance(row, str) and row.strip()]
    return changes, suggestions


_summarizer_mode_logged = False


def get_summarizer() -> Summarizer:
    """构建摘要器；首次调用打印运行模式（不静默降级）。"""
    global _summarizer_mode_logged
    model = os.getenv("LITELLM_MODEL", "").strip()
    if model:
        if not _summarizer_mode_logged:
            logger.info("reflection summarizer: LiteLLM(model=%s)", model)
            _summarizer_mode_logged = True
        return LiteLLMSummarizer(model)
    if not _summarizer_mode_logged:
        logger.info(
            "reflection summarizer 运行于确定性降级模式（LITELLM_MODEL 未配置），"
            "pass 只出收尾报告、不产出候选"
        )
        _summarizer_mode_logged = True
    return NullSummarizer()
