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
        self,
        *,
        branches: list[dict],
        context_text: str,
        instruction: str,
        node_id: str | None = None,
        model: str | None = None,
    ) -> str: ...


class OfflineConditionClassifier:
    """未配置 LLM 模型时的兜底：语义判定必抛错，由 loader 走 defaultTarget。"""

    def classify(
        self,
        *,
        branches: list[dict],
        context_text: str,
        instruction: str,
        node_id: str | None = None,
        model: str | None = None,
    ) -> str:
        raise ConditionClassifyError("LLM 未配置，语义分支无法求值")


class LiteLLMConditionClassifier:
    """经 LiteLLM 的语义分类；输出无法解析或标签越界抛 ConditionClassifyError。"""

    def __init__(self, model: str):
        self.model = model

    def classify(
        self,
        *,
        branches: list[dict],
        context_text: str,
        instruction: str,
        node_id: str | None = None,
        model: str | None = None,
    ) -> str:
        import litellm

        # ZP：节点级 model 覆盖（docs/08 打包 ZP 立项块）——非空覆盖构造期默认，仅本次调用生效。
        model_name = (model or "").strip() or self.model
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
            model=model_name,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            # J-3c：出向 LLM 必须带超时与输出上限，防挂死/超长回包拖垮编排
            timeout=float(os.getenv("ATLAS_LLM_TIMEOUT_SECONDS", "60")),
            max_tokens=512,
            # ZP：结构化输出强制（与 llm/decision.py:127 同构）；模型必须返纯 JSON。
            response_format={"type": "json_object"},
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


class ScriptedConditionClassifier:
    """回放专用（docs/83 打包 V）：按 node_id 返录制时的分支标签，零 LLM 调用。

    node_id 缺失或不在脚本中 → 抛 ConditionClassifyError，由 loader 既有 fail-safe
    走 defaultTarget（录制后新增的节点不猜语义，让 compare 报分支漂移）。
    """

    def __init__(self, branches_by_node: dict[str, str]):
        self._branches = dict(branches_by_node)

    def classify(
        self,
        *,
        branches: list[dict],
        context_text: str,
        instruction: str,
        node_id: str | None = None,
        model: str | None = None,
    ) -> str:
        if node_id is None or node_id not in self._branches:
            raise ConditionClassifyError(f"回放脚本中无节点 {node_id!r} 的分支记录")
        return self._branches[node_id]


def get_condition_classifier() -> ConditionClassifier:
    model = os.getenv("LITELLM_MODEL", "").strip()
    if model:
        return LiteLLMConditionClassifier(model)
    return OfflineConditionClassifier()
