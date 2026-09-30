"""录制回放：易变值归一化、审批决策预置、操作序列与逐节点比对（纯函数）。

契约 04 §5.11、运行时边界 06 §6.9。
"""

from collections.abc import Callable
from datetime import datetime, timezone
import secrets
from typing import Any

from .cases import RecordStep, RecordingCase

_VOLATILE_KEYS = ("token", "sent_at")
# M10：span 元数据键（随机 id/版本标注）不参与录制回放逐节点比对（04 §5.15、U52）。
_TRACE_KEYS = ("traceId", "spanId", "parentSpanId", "graphVersion")


def normalize(
    value: Any, tool: str | None = None, node_type: str | None = None
) -> Any:
    """深拷贝后递归剔除运行时易变值。

    - 任意层级删除 ``token`` 与 ``sent_at``；
    - 同层含 ``sent_at`` 的 dict（message/send 记录）额外删除 uuid ``id``；
    - tool == "http/request" 的节点产出删除 ``result.headers.date``；
    - human_approval 产出删除 ``resolvedBy``（回放经 inputs.approvals 预置，
      决策来源 input/timeout/human 属运行时来源，不是业务结果）；
    - trigger 产出删除 ``context.payload.approvals``（预置通道随载荷回显）；
    - subgraph 产出递归剔除子层同类运行期值（见 ``_normalize_subgraph``）；
    - 任意层级删除 M10 span 元数据键 ``traceId/spanId/parentSpanId/graphVersion``
      （随机 id 与版本标注不参与逐节点比对）。
    业务键（order_id 等）不受影响。
    """
    if node_type == "human_approval" and isinstance(value, dict):
        value = {k: v for k, v in value.items() if k != "resolvedBy"}
    elif node_type == "trigger" and isinstance(value, dict):
        context = value.get("context")
        if isinstance(context, dict) and isinstance(context.get("payload"), dict):
            payload = {k: v for k, v in context["payload"].items() if k != "approvals"}
            value = {**value, "context": {**context, "payload": payload}}
    elif node_type == "subgraph" and isinstance(value, dict):
        value = _normalize_subgraph(value)
    return _normalize(value, tool)


def _normalize_subgraph(value: Any, *, is_payload: bool = False) -> Any:
    """子图步骤产出：按子层节点自述的 ``mode`` 递归剔除运行期值。

    子图产出把子层各节点产出嵌在 ``outputs`` 里，逐节点归一化规则须下潜到该层：
    ``mode=="human_approval"`` 删 ``resolvedBy``、``payload.approvals`` 删预置通道回显、
    ``mode=="subgraph"`` 整条丢 ``trace``（人读日志，内嵌 ``(来源)`` 等运行期值，
    业务结果在 ``outputs`` 里另有比对）。嵌套子图按同一规则递归。
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        mode = value.get("mode")
        for key, item in value.items():
            if key == "trace" and mode == "subgraph":
                continue
            if is_payload and key == "approvals":
                continue
            if key == "resolvedBy" and mode == "human_approval":
                continue
            out[key] = _normalize_subgraph(item, is_payload=(key == "payload"))
        return out
    if isinstance(value, list):
        return [_normalize_subgraph(item) for item in value]
    return value


def _normalize(value: Any, tool: str | None) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        is_message_record = "sent_at" in value
        if tool == "http/request":
            result = value.get("result")
            if isinstance(result, dict) and isinstance(result.get("headers"), dict):
                result = {**result, "headers": {k: v for k, v in result["headers"].items() if k.lower() != "date"}}
                value = {**value, "result": result}
        for key, item in value.items():
            if key in _VOLATILE_KEYS or key in _TRACE_KEYS:
                continue
            if is_message_record and key == "id":
                continue
            normalized[key] = _normalize(item, None)
        return normalized
    if isinstance(value, list):
        return [_normalize(item, None) for item in value]
    return value


def preset_approvals(
    steps: list[RecordStep],
    *,
    subgraphs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, str]:
    """从 baseline 步骤抽取 ``{node_id: decision}``，供回放 inputs.approvals 预置。

    顶层 human_approval 步骤键为裸 node id；子图内审批经 ``subgraphs``（录制快照
    ``{graphId 原文: raw}``）递归下潜，键为路径限定 ``"sub-1/human-1"``（04 §5.11），
    与 loader ``_scope_approvals`` 的下发口径逐字对应。缺快照/引用缺失/形状异常时
    该子树静默跳过（保持旧行为，不抛错）。
    """
    presets: dict[str, str] = {}
    for step in steps:
        if step.node_type == "human_approval":
            decision = step.output.get("decision")
            if isinstance(decision, str):
                presets[step.node_id] = decision
        elif step.node_type == "subgraph":
            _collect_nested_approvals(step.output, f"{step.node_id}/", subgraphs or {}, presets)
    return presets


def _collect_nested_approvals(
    subgraph_output: Any,
    prefix: str,
    subgraphs: dict[str, dict[str, Any]],
    presets: dict[str, str],
) -> None:
    """按录制快照的节点类型，从 subgraph 步骤产出里递归抽取审批决策（键带路径前缀）。"""
    if not isinstance(subgraph_output, dict):
        return
    raw = subgraphs.get(subgraph_output.get("graphId"))
    outputs = subgraph_output.get("outputs")
    if not isinstance(raw, dict) or not isinstance(outputs, dict):
        return
    for node in raw.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        child_output = outputs.get(node_id) if isinstance(node_id, str) else None
        if not isinstance(node_id, str) or not isinstance(child_output, dict):
            continue
        if node.get("type") == "human_approval":
            decision = child_output.get("decision")
            if isinstance(decision, str):
                presets[f"{prefix}{node_id}"] = decision
        elif node.get("type") == "subgraph":
            _collect_nested_approvals(child_output, f"{prefix}{node_id}/", subgraphs, presets)


def dedupe_steps(steps: list[RecordStep]) -> list[RecordStep]:
    """node_id 去重保末（parallel 两次 node_end、loop 重访）；末次出现的位置为准。"""
    by_id: dict[str, RecordStep] = {}
    order: list[str] = []
    for step in steps:
        if step.node_id in by_id:
            order.remove(step.node_id)
        by_id[step.node_id] = step
        order.append(step.node_id)
    return [by_id[node_id] for node_id in order]


def _diff_top_level(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    return sorted(key for key in expected.keys() | actual.keys() if expected.get(key) != actual.get(key))


def compare(
    baseline: list[RecordStep],
    replay_steps: list[RecordStep],
    *,
    tools_by_node: dict[str, str | None],
    baseline_status: str,
    replay_status: str,
) -> dict[str, Any]:
    """序列先比对（多走/漏走节点 = 分支漂移），再逐节点归一化深等；产出 ReplayReport。"""
    base_steps = dedupe_steps(baseline)
    replay_steps = dedupe_steps(replay_steps)
    replay_by_id = {step.node_id: step for step in replay_steps}
    base_ids = {step.node_id for step in base_steps}

    rows: list[dict[str, Any]] = []
    all_match = baseline_status == replay_status

    for step in base_steps:
        replayed = replay_by_id.get(step.node_id)
        if replayed is None:
            rows.append({"node_id": step.node_id, "match": False, "note": "回放缺少该节点（操作序列不一致，疑似分支漂移）"})
            all_match = False
            continue
        tool = tools_by_node.get(step.node_id)
        expected = normalize(step.output, tool, step.node_type)
        actual = normalize(replayed.output, tool, step.node_type)
        notes: list[str] = []
        if step.node_type != replayed.node_type:
            notes.append(f"节点类型不一致（baseline={step.node_type}，replay={replayed.node_type}）")
            all_match = False
        if expected == actual:
            rows.append({"node_id": step.node_id, "match": True, "note": "；".join(notes) or "一致"})
        else:
            diff_keys = _diff_top_level(expected, actual)
            notes.append(f"归一化后产出不一致，差异顶层键：{', '.join(diff_keys) if diff_keys else '（嵌套差异）'}")
            rows.append({"node_id": step.node_id, "match": False, "note": "；".join(notes), "diff_keys": diff_keys})
            all_match = False

    for step in replay_steps:
        if step.node_id not in base_ids:
            rows.append({"node_id": step.node_id, "match": False, "note": "回放多出该节点（操作序列不一致，疑似分支漂移）"})
            all_match = False

    return {
        "matches": all_match,
        "baseline_status": baseline_status,
        "replay_status": replay_status,
        "steps": rows,
    }


def build_condition_script(
    case: RecordingCase,
) -> tuple[Any, list[str]]:
    """从录制步骤构造 LLM condition 回放脚本，返回 ``(classifier | None, node_ids)``（docs/83）。

    只取 ``node_type=="condition"`` 且产出 ``mode=="llm"`` 的步骤，映射
    node_id → 录制时 branch 标签（非空 str；坏值跳过该节点）。无 LLM condition
    步骤返 ``(None, [])``。dedupe 保末，与 build_tool_mocks 同口径。
    """
    from atlas.llm.condition_classifier import ScriptedConditionClassifier

    scripted: dict[str, str] = {}
    for step in dedupe_steps(case.steps):
        if step.node_type != "condition":
            continue
        branch = step.output.get("branch")
        if step.output.get("mode") == "llm" and isinstance(branch, str) and branch:
            scripted[step.node_id] = branch
    if not scripted:
        return None, []
    return ScriptedConditionClassifier(scripted), list(scripted)


def build_tool_mocks(case: RecordingCase) -> tuple[dict[str, Any], list[str]]:
    """从录制步骤构造工具桩，返回 ``(mocks, mocked_node_ids)``（docs/28 §2.2）。

    仅取 ``node_type == "tool_call"`` 的**顶层**步骤（collect_steps 已排除子图内部
    node_end）；桩 output 取录制入库的**原始未归一化**形状，回放时 node_end 走与真实
    工具相同的链路，compare 两端各自 normalize，同形必然一致。dedupe 保末与 baseline 口径一致。
    """
    mocks: dict[str, Any] = {}
    mocked: list[str] = []
    for step in dedupe_steps(case.steps):
        if step.node_type == "tool_call":
            mocks[step.node_id] = step.output
            mocked.append(step.node_id)
    return mocks, mocked


def collect_steps() -> tuple[Callable[[dict[str, Any]], None], Callable[[], list[RecordStep]]]:
    """返回 (emit, take_steps)：emit 接 run_graph 事件，take 取去重保末的 node_end 步骤。"""
    collected: list[RecordStep] = []

    def emit(event: dict[str, Any]) -> None:
        # A 包（docs/27 §3.2/§10.1）：子图内部 node_end 带 subgraphPath，录制/回放步骤只
        # 统计顶层节点（子图结果由 subgraph 节点自身 node_end 体现），与 baseline 口径一致。
        if event.get("type") == "node_end" and not event.get("subgraphPath"):
            collected.append(
                RecordStep(
                    node_id=event["node_id"],
                    node_type=event["node_type"],
                    output=event.get("output") or {},
                )
            )

    def take_steps() -> list[RecordStep]:
        return dedupe_steps(collected)

    return emit, take_steps


def clock_anchor(case: RecordingCase) -> tuple[datetime | None, str | None]:
    """C（docs/27 §2.4）：取回放冻结时钟锚点。

    优先 recorded_at，回退 created_at；解析失败/缺失返回 (None, note)，
    调用方据此退回真实时钟并在报告标注 today()/now() 时间分支可能漂移。
    """
    raw = case.recorded_at or getattr(case, "created_at", None)
    if not raw:
        return None, "用例缺少 recorded_at/created_at，回放使用真实时钟（today()/now() 时间分支可能漂移）"
    try:
        anchor = datetime.fromisoformat(raw)
    except ValueError:
        return None, f"时钟锚点无法解析（{raw}），回放使用真实时钟（时间分支可能漂移）"
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    return anchor.astimezone(timezone.utc), None


def seed_anchor(case: RecordingCase) -> tuple[int | None, str | None]:
    """打包 W（docs/84 D-3）：取回放 RNG 种子锚点。

    用例携带 rng_seed 时原样钉住；历史用例缺省 None → 现场生成新种子，
    调用方据此运行并在报告标注 random/randint/uuid 分支可能漂移；
    不伪造锚点（与 clock_anchor 回退口径一致）。
    """
    seed = getattr(case, "rng_seed", None)
    if isinstance(seed, bool) or not isinstance(seed, int):
        return secrets.randbits(63), "用例缺少 rng_seed，回放使用新随机种子（random/randint/uuid 分支可能漂移）"
    return seed, None
