"""影子模式（线上旁路录制，D26 进程内 v1，docs/33 §3）。

一次影子运行对同一输入旁路跑完整决策链路并记录「系统本会怎么做」，但：

- 读透传、写短路：READ 能力照常执行；WRITE/DELETE/FINANCIAL 能力不触达适配器，
  在引擎层（``graph/loader.py``）短路为 ``SHADOW_DRY_RUN`` 意图回执；
- 不弹审批：API 层预置全部 human_approval 节点 approved 秒过；
- 不进生产观测面：不写 run_store/RunRecord、不触发告警与灰度门控、不产 tool_metric。

本模块只承载**模型 + 纯函数 + 进程内 ring 存储**（照 ``reports.ReportStore`` 先例，
挂 TenantServices、不进 Repository）：内存档为进程内实例，PG 档另见
``recording/pg_shadow.py``（docs/61 §5 H4，表 ``shadow_runs``、跨重启/跨实例可见），
两档 reset 均清空本租户：

- ``ToolIntent/ShadowDecision/HumanOutcome/ShadowRun/ShadowComparison`` 记录模型；
- ``preset_all_approvals`` 预置审批、``extract_shadow_events`` 从顶层 node_end 提取决策/意图；
- ``infer_auto_action`` 推断系统自动动作、``normalize_human_action``/``compare_shadow`` 对比；
- ``ShadowStore`` ring 100/租户、sr-N 单调计数。
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable

from pydantic import BaseModel, Field

from ..graph.dsl import GraphDSL, NodeDSL

# 每租户保留最近影子运行数（ring 自然淘汰；体量与报告同级）。
SHADOW_RING_SIZE = 100

# 路由决策类节点（docs/33 §3.3 ShadowDecision.node_type）。
DECISION_NODE_TYPES = {"condition", "human_approval", "loop"}

# 退款/转人工动作归一映射（决策串 → 对比用标准动作）。
_REFUND_ALIASES = {"refunded", "refund", "approve_refund", "execute_refund"}
_HUMAN_ALIASES = {"human_review", "request_human_approval", "human_approval", "manual_review"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ToolIntent(BaseModel):
    """一次工具节点的旁路意图（docs/33 §3.3）。"""

    node_id: str
    tool: str  # adapter/capability；SIMULATED 为裸工具名
    permission: str | None = None  # read/write/delete/financial；SIMULATED 为 None
    dry_run: bool  # True＝写能力被短路；False＝READ 透传或 SIMULATED
    parameters: dict[str, Any] | None = None  # 脱敏后最终参数（v1 沙盘无密钥原样记录）
    action_status: str  # SHADOW_DRY_RUN / SUCCESS / FAILED / SIMULATED


class ShadowDecision(BaseModel):
    """一次路由决策（condition/human_approval/loop）的产出目标。"""

    node_id: str
    node_type: str
    target: str | None = None


class HumanOutcome(BaseModel):
    """人工实际处理（创建时可带，或事后补录）。"""

    action: str  # refunded | human_review | 其他自定义动作串
    note: str | None = None


class ShadowComparison(BaseModel):
    match: bool | None = None  # True 一致 / False 不一致 / None 无法判定（系统无写意图）
    auto_action: str | None = None
    human_action: str | None = None
    diffs: list[str] = Field(default_factory=list)


class ShadowRun(BaseModel):
    id: str
    graph_id: str
    inputs: dict[str, Any] | None = None
    status: str  # completed | error
    error: str | None = None
    decisions: list[ShadowDecision] = Field(default_factory=list)
    tool_intents: list[ToolIntent] = Field(default_factory=list)
    trace_id: str
    auto_action: str | None = None
    human_outcome: HumanOutcome | None = None
    comparison: ShadowComparison = Field(default_factory=ShadowComparison)
    created_at: str


# --- 纯函数 -----------------------------------------------------------------------


def preset_all_approvals(graph: GraphDSL) -> dict[str, str]:
    """影子运行预置：全部 human_approval 节点 approved 秒过（docs/33 §3.1，零 loader 改动）。"""
    return {
        node.id: "approved"
        for node in graph.nodes
        if node.type == "human_approval"
    }


def _tool_intent(
    node: NodeDSL, output: dict[str, Any], tool_permissions: dict[str, str]
) -> ToolIntent:
    """从单个 tool_call 节点产出构造旁路意图。"""
    result = output.get("result") if isinstance(output, dict) else None
    result = result if isinstance(result, dict) else {}
    status = str(result.get("status") or "")
    configured_tool = str(node.config.get("tool", "") or "")

    if status == "SIMULATED":
        return ToolIntent(
            node_id=node.id,
            tool=str(result.get("tool") or configured_tool),
            permission=None,
            dry_run=False,
            parameters=None,
            action_status="SIMULATED",
        )
    if status == "SHADOW_DRY_RUN":
        parameters = result.get("parameters")
        return ToolIntent(
            node_id=node.id,
            tool=str(result.get("tool") or configured_tool),
            permission=result.get("permission"),
            dry_run=True,
            parameters=parameters if isinstance(parameters, dict) else None,
            action_status="SHADOW_DRY_RUN",
        )
    # READ 透传（SUCCESS/PARTIAL）或执行失败（FAILED）：真实触达，仅 read 能力会走到这里。
    action_status = str(output.get("action_status") or ("FAILED" if status == "FAILED" else "SUCCESS"))
    return ToolIntent(
        node_id=node.id,
        tool=configured_tool,
        permission=tool_permissions.get(configured_tool),
        dry_run=False,
        parameters=None,
        action_status=action_status,
    )


def extract_shadow_events(
    events: Iterable[tuple[str, dict[str, Any]]],
    node_index: dict[str, NodeDSL],
    tool_permissions: dict[str, str],
) -> tuple[list[ShadowDecision], list[ToolIntent]]:
    """从**顶层** node_end 事件（按到达顺序）提取路由决策与工具意图。

    带 subgraphPath 的子图内部节点由 API 层在收集时排除（与监控 tool_calls 同口径）；
    子图内写能力的短路保证在引擎层（loader 透传 shadow），由引擎测试断言。
    """
    decisions: list[ShadowDecision] = []
    intents: list[ToolIntent] = []
    for node_id, output in events:
        node = node_index.get(node_id)
        if node is None or not isinstance(output, dict):
            continue
        if node.type in DECISION_NODE_TYPES:
            target = output.get("target")
            decisions.append(
                ShadowDecision(
                    node_id=node_id,
                    node_type=node.type,
                    target=target if isinstance(target, str) else None,
                )
            )
        elif node.type == "tool_call":
            intents.append(_tool_intent(node, output, tool_permissions))
    return decisions, intents


def _refund_class(tool: str, parameters: dict[str, Any] | None) -> str | None:
    """判定单个写意图的标准动作：refunded / human_review / None。"""
    action = str((parameters or {}).get("action") or "").strip().lower()
    if tool == "shop/process_refund":
        # process_refund 按上游决策分流：approve_refund→退款，request_human_approval→转人工。
        if action in _HUMAN_ALIASES:
            return "human_review"
        if action in _REFUND_ALIASES:
            return "refunded"
        return None
    if tool in ("shop/execute_refund",):
        return "refunded"
    if tool in ("shop/request_human_approval",):
        return "human_review"
    # 通用兜底：仅依据 parameters.action 判定。
    if action in _HUMAN_ALIASES:
        return "human_review"
    if action in _REFUND_ALIASES:
        return "refunded"
    return None


def infer_auto_action(
    tool_intents: Iterable[ToolIntent], decisions: Iterable[ShadowDecision] = ()
) -> str | None:
    """纯函数推断系统「本会采取」的动作（docs/33 §3.3）。

    仅统计写意图（dry_run=True）：退款类 → refunded；转人工类 → human_review；都没有 → None。
    按意图顺序，退款与转人工同时出现时以**首个写意图**为准（流程上二者互斥分流）。
    ``decisions`` 保留入参以贴契约签名，v1 推断不依赖路由节点。
    """
    for intent in tool_intents:
        if not intent.dry_run:
            continue
        classified = _refund_class(intent.tool, intent.parameters)
        if classified is not None:
            return classified
    return None


def normalize_human_action(action: str) -> str:
    """人工动作归一：去空格小写并映射别名；未知自定义动作串原样（小写）保留。"""
    key = (action or "").strip().lower()
    if key in _REFUND_ALIASES:
        return "refunded"
    if key in _HUMAN_ALIASES:
        return "human_review"
    return key


def compare_shadow(auto_action: str | None, outcome: HumanOutcome | None) -> ShadowComparison:
    """对比系统自动动作与人工实际处理（docs/33 §3.3）。

    - 有人工结果且与 auto 相等：match=True、diffs=[]；
    - 有人工结果但不等：match=False、diffs 记一条；
    - 无人工结果（尚未补录）或 auto 为 None：match=None（无法判定，diffs 说明）。
    """
    human_action = normalize_human_action(outcome.action) if outcome is not None else None
    if human_action is None:
        return ShadowComparison(
            match=None,
            auto_action=auto_action,
            human_action=None,
            diffs=["尚未补录人工实际处理，无法比对"]
            if auto_action is not None
            else ["系统旁路未产生退款/转人工写意图，且无人工结果"],
        )
    if auto_action is None:
        return ShadowComparison(
            match=None,
            auto_action=None,
            human_action=human_action,
            diffs=["系统旁路未产生退款/转人工写意图，无法与人工结果比对"],
        )
    if human_action == auto_action:
        return ShadowComparison(
            match=True, auto_action=auto_action, human_action=human_action, diffs=[]
        )
    return ShadowComparison(
        match=False,
        auto_action=auto_action,
        human_action=human_action,
        diffs=[f"系统本会 {auto_action}，人工实际 {human_action or '未记录'}"],
    )


# --- 进程内 ring 存储 --------------------------------------------------------------


class ShadowStore:
    """进程内影子运行 ring（每租户一个；单锁；reset 清空）。PG 档见 ``pg_shadow.PgShadowStore``。"""

    def __init__(self, maxlen: int = SHADOW_RING_SIZE) -> None:
        self._items: deque[ShadowRun] = deque(maxlen=maxlen)
        self._counter = 0
        self._lock = threading.Lock()

    def add(
        self,
        *,
        graph_id: str,
        trace_id: str,
        decisions: list[ShadowDecision],
        tool_intents: list[ToolIntent],
        inputs: dict[str, Any] | None = None,
        status: str = "completed",
        error: str | None = None,
        human_outcome: HumanOutcome | None = None,
    ) -> dict[str, Any]:
        """落一次影子运行，推断 auto_action；创建时可带人工结果则一并算 comparison。"""
        auto_action = infer_auto_action(tool_intents, decisions)
        with self._lock:
            self._counter += 1
            sid = f"sr-{self._counter}"
            run = ShadowRun(
                id=sid,
                graph_id=graph_id,
                inputs=inputs,
                status=status,
                error=error,
                decisions=decisions,
                tool_intents=tool_intents,
                trace_id=trace_id,
                auto_action=auto_action,
                human_outcome=human_outcome,
                comparison=compare_shadow(auto_action, human_outcome),
                created_at=_now_iso(),
            )
            self._items.append(run)
        return run.model_dump()

    def get(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            item = next((item for item in self._items if item.id == sid), None)
            return item.model_dump() if item is not None else None

    def list(
        self, graph_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """本租户影子记录倒序（可按图过滤；limit 1–200，端点层再 clamp）。"""
        bounded = max(1, min(int(limit), 200))
        with self._lock:
            items = [
                item for item in reversed(self._items)
                if graph_id is None or item.graph_id == graph_id
            ][:bounded]
        return [item.model_dump() for item in items]

    def attach_outcome(self, sid: str, outcome: HumanOutcome) -> dict[str, Any] | None:
        """补录/覆盖人工结果并重算 comparison；不存在返 None（API 层 404）。"""
        with self._lock:
            item = next((item for item in self._items if item.id == sid), None)
            if item is None:
                return None
            item.human_outcome = outcome
            item.comparison = compare_shadow(item.auto_action, outcome)
            return item.model_dump()

    def reset(self) -> None:
        """/api/demo/reset 清空。"""
        with self._lock:
            self._items.clear()
            self._counter = 0
