"""进化候选自动生效的草稿补丁（docs/132 打包 BP，BP-D3；docs/126 §5 D75）。

把候选 ``changes`` 里 scope ∈ {graph_variable, node_config} 的行应用到 latest 草稿，
产出 Graph diff（可审计）；scope ∈ {monitor_rule, gate_config} 的行**跳过**并计入 diff
（BP-D1：v1 不自动——apply 目标不在图草稿闭环内、影响防御面）；结构类（增删节点/改边/
promptTemplate）根本不在白名单（docs/88 D-4），到不了这里。

**dirty 草稿保护（BP-D3）**：latest 草稿 version ≠ 候选 base_version ⇒ ok=False
（不自动覆盖人工改动）；无草稿同理。形状照契约 §3.1：``PatchOutcome``。

本模块**无副作用**：只算新草稿与 diff，不落库、不发布、不启动灰度（编排在 autoapply）。
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .candidate import Change
from .tunables import TUNABLE_WHITELIST

# v1 允许自动生效的类别（BP-D1：只开图内参数类；其余 scope 跳过并计入 diff）。
AUTO_SCOPES = frozenset({"graph_variable", "node_config"})

_NODE_SCOPED_PARAM = "node.confidenceThreshold"
_NODE_TYPE = "ai_decision"

PatchOutcomeStatus = Literal["ok", "no_draft", "dirty", "shape"]


class PatchDiff(BaseModel):
    """一条参数落点（可审计；``skipped``＝类别未开/白名单外，不静默吞）。"""

    model_config = ConfigDict(populate_by_name=True)

    param_key: str
    from_value: int | float | str | None = Field(default=None, alias="from")
    to_value: int | float | str = Field(alias="to")
    node_id: str | None = None
    path: str
    skipped: bool = False
    reason: str | None = None


class PatchOutcome(BaseModel):
    """patch_candidate 的结果：ok 时带 new_draft 与 diff；否则带 reason（调用方中止留证）。"""

    ok: bool
    new_draft: dict[str, Any] | None = None
    diff: list[dict[str, Any]] = []
    reason: str | None = None


def _scope_of(param_key: str) -> str | None:
    param = TUNABLE_WHITELIST.get(param_key)
    return param.scope if param is not None else None


def _normalized(raw: dict[str, Any]) -> dict[str, Any]:
    """内容比较的归一化视图：排除 ``releaseVersion``（发布快照携带、latest 草稿不携带，
    ADR T19），否则发布过的图恒"不等"，dirty 判据失效。"""
    return {k: v for k, v in raw.items() if k != "releaseVersion"}


def patch_candidate(
    *,
    changes: list[Change],
    latest_draft: dict[str, Any] | None,
    base_version: int,
    graph_snapshot: dict[str, Any] | None = None,
) -> PatchOutcome:
    """把候选变更 apply 到 latest 草稿（只认图内参数类），返回新草稿与 diff。

    - 无草稿 → ``no_draft``（图不存在或从未保存）；
    - ``graph_snapshot is None``（候选 base_version 无对应发布）→ ``dirty``（fail-closed，
      无法确认基线一致，不自动覆盖）；
    - ``latest 草稿 ≠ base_version 发布快照``（内容比较，排除 releaseVersion）→ ``dirty``
      （草稿被人工改过，**不覆盖**；BP-D3）；
    - 图内参数落点形状异常（变量未命中/节点找不到/非 ai_decision/缺 node_id）→ ``shape``
      （照实不猜，调用方中止留证）；
    - monitor_rule/gate_config 行 → ``skipped=True`` 且计入 diff（v1 不自动，BP-D1）。
    """
    if latest_draft is None:
        return PatchOutcome(ok=False, reason="无 latest 草稿（图不存在或从未保存）")
    if graph_snapshot is None:
        return PatchOutcome(
            ok=False,
            reason=(
                f"草稿与候选基线不一致（发布版本 {base_version} 不存在，无法确认基线），"
                f"需人工处理"
            ),
        )
    if _normalized(graph_snapshot) != _normalized(latest_draft):
        return PatchOutcome(
            ok=False,
            reason=(
                f"草稿与候选基线不一致（latest 草稿 ≠ 发布版本 {base_version}，"
                f"草稿被人工改过），需人工处理"
            ),
        )

    new_draft = copy.deepcopy(latest_draft)
    diff: list[dict[str, Any]] = []
    for change in changes:
        scope = _scope_of(change.param_key)
        if scope is None:
            # 白名单外：到不了这里（run_pass fail-closed 已拦），防御性跳过并留证。
            diff.append(
                PatchDiff(
                    param_key=change.param_key,
                    from_value=change.from_value,
                    to_value=change.to_value,
                    node_id=change.node_id,
                    path="<unknown>",
                    skipped=True,
                    reason="参数不在可调白名单内（fail-closed）",
                ).model_dump(by_alias=True)
            )
            continue
        if scope not in AUTO_SCOPES:
            diff.append(
                PatchDiff(
                    param_key=change.param_key,
                    from_value=change.from_value,
                    to_value=change.to_value,
                    node_id=change.node_id,
                    path=f"<{scope}>",
                    skipped=True,
                    reason="v1 不自动（BP-D1：monitor_rule/gate_config 本批不自动，落点在草稿外）",
                ).model_dump(by_alias=True)
            )
            continue

        if scope == "graph_variable":
            applied, entry, reason = _apply_graph_variable(new_draft, change)
        else:  # node_config（v1 白名单内仅 node.confidenceThreshold 一条，docs/94 E-3）
            applied, entry, reason = _apply_node_config(new_draft, change)
        if not applied:
            return PatchOutcome(
                ok=False,
                reason=reason or f"落点形状异常：{change.param_key}",
                diff=diff,
            )
        diff.append(entry)

    return PatchOutcome(ok=True, new_draft=new_draft, diff=diff)


def _apply_graph_variable(
    draft: dict[str, Any], change: Change
) -> tuple[bool, dict[str, Any], str | None]:
    """改 ``variables[]`` 中 name 命中行的 value；未命中 → 照实不猜。"""
    variables = draft.get("variables")
    if not isinstance(variables, list):
        return False, {}, f"草稿 variables 段缺失或形状异常：{change.param_key}"
    for idx, variable in enumerate(variables):
        if isinstance(variable, dict) and variable.get("name") == change.param_key:
            from_value = variable.get("value")
            variable["value"] = change.to_value
            entry = PatchDiff(
                param_key=change.param_key,
                from_value=from_value if isinstance(from_value, (int, float, str)) else None,
                to_value=change.to_value,
                node_id=None,
                path=f"variables[{idx}].value",
            ).model_dump(by_alias=True)
            return True, entry, None
    return False, {}, f"图变量未命中：{change.param_key}（不在草稿 variables 中）"


def _apply_node_config(
    draft: dict[str, Any], change: Change
) -> tuple[bool, dict[str, Any], str | None]:
    """改 ``nodes[]`` 中 ai_decision 节点的 ``config.confidenceThreshold``（按 node_id 定位）。

    规则照 docs/94 E-3 的确定性定位：change 缺 node_id／节点找不到／非 ai_decision → 中止
    （照实不猜，不自动改到别的节点）。
    """
    nodes = draft.get("nodes")
    if not isinstance(nodes, list):
        return False, {}, f"草稿 nodes 段缺失或形状异常：{change.param_key}"
    node_id = change.node_id
    if not node_id:
        return False, {}, (
            f"节点级变更缺少 node_id 定位（{change.param_key}），不自动猜节点"
        )
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict) or node.get("id") != node_id:
            continue
        if node.get("type") != _NODE_TYPE:
            return False, {}, f"节点 {node_id} 非 {_NODE_TYPE} 类型，拒绝自动改其 config"
        config = node.setdefault("config", {})
        if not isinstance(config, dict):
            return False, {}, f"节点 {node_id} 的 config 形状异常（非对象）"
        from_value = config.get("confidenceThreshold")
        config["confidenceThreshold"] = change.to_value
        entry = PatchDiff(
            param_key=change.param_key,
            from_value=from_value if isinstance(from_value, (int, float)) else None,
            to_value=change.to_value,
            node_id=node_id,
            path=f"nodes[{idx}].config.confidenceThreshold",
        ).model_dump(by_alias=True)
        return True, entry, None
    return False, {}, f"节点未命中：{node_id}（不在草稿 nodes 中）"
