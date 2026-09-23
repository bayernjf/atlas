"""condition 节点语义分支分类器（D14 v1：LiteLLM 返唯一标签；无模型时离线兜底）。

模型经 LiteLLM 调用，供应商/key 由环境变量切换（10 文档 T1，同 decision.py）；
未配置 `LITELLM_MODEL` 时使用 Offline 分类器——语义判定没有可信的确定性规则，
任何调用都抛 ConditionClassifyError，由 loader fail-safe 路由 defaultTarget
（04 §5.2 LLM 语义分支追加段；docs/48）。
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol

DEFAULT_BRANCH = "__default__"

_BRANCH_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_PROMPT = (
    "你是流程分流判断器。根据运行上下文与下列分支描述，选择唯一最匹配的分支。"
    "只能从给定的分支标签中选择；如果没有任何分支匹配，选择 "
    f"{DEFAULT_BRANCH}。只输出 JSON，不要输出其他内容："
    '{"branch": "<分支标签>"}'
)


class ConditionClassifyError(Exception):
    """分类无法完成（未配置模型、输出无法解析、标签越界、调用异常）。"""


class ConditionClassifier(Protocol):
    def classify(
        self, *, branches: list[dict], context_text: str, instruction: str
    ) -> str: ...


class OfflineConditionClassifier:
    """未配置 LLM 模型时的兜底：语义判定必抛错，由 loader 走 defaultTarget。"""

    def classify(
        self, *, branches: list[dict], context_text: str, instruction: str
    ) -> str:
        raise ConditionClassifyError("LLM 未配置，语义分支无法求值")


class LiteLLMConditionClassifier:
    """经 LiteLLM 的语义分类；输出无法解析或标签越界抛 ConditionClassifyError。"""

    def __init__(self, model: str):
        self.model = model

    def classify(
        self, *, branches: list[dict], context_text: str, instruction: str
    ) -> str:
        import litellm

        labels = [item["label"] for item in branches]
        branch_lines = "\n".join(
            f"- {item['label']}：{item['description']}" for item in branches
        )
        instruction_block = f"附加判定要求：{instruction}\n" if instruction else ""
        prompt = (
            f"{branch_lines}\n"
            f"{instruction_block}"
            f"运行上下文：\n{context_text}"
        )
        response = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        content = response["choices"][0]["message"]["content"]
        try:
            match = _BRANCH_RE.search(content)
            payload = json.loads(match.group(0) if match else content)
            label = payload["branch"]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ConditionClassifyError(f"LLM 返回无法解析：{exc}") from exc
        if label == DEFAULT_BRANCH:
            return label
        if label not in labels:
            raise ConditionClassifyError(f"LLM 返回了未知分支标签：{label}")
        return label


def get_condition_classifier() -> ConditionClassifier:
    model = os.getenv("LITELLM_MODEL", "").strip()
    if model:
        return LiteLLMConditionClassifier(model)
    return OfflineConditionClassifier()
