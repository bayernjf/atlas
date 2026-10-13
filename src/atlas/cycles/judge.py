# -*- coding: utf-8 -*-
"""圈终止判定（docs/130 打包 BN＝D69；C-D4 的落点）。

**规则优先、LLM 可选二段**——这条是对"由模型判终止"的修正，理由写在这里以免日后
被"顺手改回去"：每圈问一次模型既烧钱，又让"为什么还没停"变成不可复现的东西，
而终止判定必须能回放、能复现、能被审计。

- `validate_complete_when(expr)`：**编译期**校验（包一层 `conditions.validate_expression`，
  零新语法——表达式与 condition 节点同一套引擎）；
- `evaluate_complete_when(expr, context)`：**运行期**判定（对 `{"global":…, **outputs}`
  求值）。表达式异常 ⇒ **fail-safe 返 False + warning**，绝不炸运行、也绝不"当成达成"
  （静默当达成＝把没做完的圈说成做完了）；
- `llm_judge(...)`：**显式开启**才走的二段（`judgeMode="llm"`），经既有结构化出口
  `llm/structured.call_structured_json`，成本照 AU 的口径记在 `caller="cycle_judge"`。
  无模型 ⇒ `LLMStructuredUnavailable` ⇒ 调用方折 False + warning。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from atlas.graph.conditions import ConditionEvalError, evaluate_expression, validate_expression

logger = logging.getLogger(__name__)

#: LLM 判定的调用点身份（AU 的 caller 枚举第九项；不复用 content_generation——
#: 混用会把圈判定的成本记到别的面，D70 的按 caller 归因就废了）。
JUDGE_CALLER = "cycle_judge"

_JSON_RE_HINT = "只输出 JSON，不要解释"


def validate_complete_when(expression: str) -> list[str]:
    """编译期校验 `completeWhen`（语法＋静态类型＋顶层须布尔）；空表达式 ⇒ 无错（缺省＝不判定）。"""
    if not (expression or "").strip():
        return []
    return validate_expression(expression)


def evaluate_complete_when(
    expression: str, context: dict[str, Any]
) -> bool:
    """运行期判定"这一圈之后可以收工了吗"。表达式异常 ⇒ False ＋ warning（fail-safe）。"""
    if not (expression or "").strip():
        return False
    try:
        return bool(evaluate_expression(expression, context))
    except ConditionEvalError as exc:
        logger.warning("cycle completeWhen 求值失败（按未达成处理）：%s", exc)
        return False
    except Exception as exc:  # noqa: BLE001 - 判定旁路，任何异常都不许炸运行
        logger.warning("cycle completeWhen 求值异常（按未达成处理）：%s", exc)
        return False


def llm_judge(
    prompt: str,
    context: dict[str, Any],
    *,
    tenant_id: str | None = None,
    model: str | None = None,
    max_tokens: int = 256,
) -> bool:
    """可选二段：问模型"目标达成了吗"。**无模型/坏形状 ⇒ False ＋ warning**（不静默当达成）。

    只在 `judgeMode="llm"` 且运营给了 `judgePrompt` 时被调用（docs/130 C-D4）。
    提示词与上下文经既有插值/脱敏通道；本函数**不落任何 prompt/response 正文**（AU-D5）。
    """
    from atlas.llm.structured import (
        LLMStructuredUnavailable,
        call_structured_json,
        resolve_model_for_structured,
    )

    ask = (
        f"{prompt}\n\n"
        f"以下是本轮执行的产出与变量（JSON）：\n{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
        f'判断目标是否已达成。{_JSON_RE_HINT}：'
        '{"achieved": true 或 false, "reason": "中文简述"}'
    )
    try:
        # 模型解析也在 try 内：没接模型是"判定不可用"，不是异常上浮（U1430 fail-safe 半边）。
        model_name, api_key, base_url = resolve_model_for_structured(
            tenant_id, model, caller=JUDGE_CALLER
        )
        content = call_structured_json(
            ask,
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            caller=JUDGE_CALLER,
            max_tokens=max_tokens,
        )
    except LLMStructuredUnavailable as exc:
        logger.warning("cycle llm 判定无模型（按未达成处理，不静默当达成）：%s", exc)
        return False
    except Exception as exc:  # noqa: BLE001 - 判定旁路
        logger.warning("cycle llm 判定调用失败（按未达成处理）：%s", exc)
        return False
    return _parse_achieved(content)


def _parse_achieved(content: str) -> bool:
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        logger.warning("cycle llm 判定回包不是 JSON（按未达成处理）")
        return False
    try:
        payload = json.loads(content[start : end + 1])
        return bool(payload.get("achieved"))
    except (ValueError, TypeError) as exc:
        logger.warning("cycle llm 判定回包解析失败（按未达成处理）：%s", exc)
        return False
