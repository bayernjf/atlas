"""反思候选与收尾报告（docs/88 §4 ``ReflectionCandidate``／``ReflectionReport``，D-5）。

**只出建议，不动系统**：候选是「待落到新草稿版本的变更建议」，采纳走既有人工流程
（`PUT /api/graphs/{id}` → publish → canary），本模块**没有任何 apply/publish/promote
调用**（守 `routing/store.py` 的唯一放量路径不变量，docs/88 §0.3 T22）。

**fail-closed**：`changes` 里任一 `param_key` 不在 `TunableParam` 白名单内 ⇒ 整份候选作废
（`rejected_whitelist`，不做部分采纳）；白名单内但取值越 `bounds` ⇒ `rejected_bounds`。
两种情况都不落候选，只留一条带原因的收尾报告。

ring 与计数沿用 `ReleaseReport`/`ShadowRun` 先例：进程内、每租户一个、单锁、`refl-N` 递增。
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .adapter import Summarizer, get_summarizer
from .evidence import ReflectionEvidence, build_evidence
from .tunables import TUNABLE_WHITELIST, in_bounds, is_whitelisted

REPORT_RING_SIZE = 100
CANDIDATE_RING_SIZE = 100

ReflectionStatus = Literal["ok", "rejected_whitelist", "rejected_bounds", "no_evidence"]
DecisionStatus = Literal["adopted", "dismissed"]

# 仅这一条白名单 key 有节点语义（ai_decision 节点的 confidenceThreshold，docs/94 E-3）。
_NODE_SCOPED_PARAM = "node.confidenceThreshold"
_NODE_TYPE = "ai_decision"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Change(BaseModel):
    """一条参数变更建议。

    ``from``/``to`` 是 Python 保留字，故字段名为 `from_value`/`to_value`，对外一律以
    `from`/`to` 序列化（`model_dump(by_alias=True)`，docs/88 §4／docs/12 端点契约）。

    ``node_id`` 为打包 ZU（docs/94 E-3）新增的可选节点定位：仅
    ``node.confidenceThreshold`` 行可能非空，由 ``resolve_node_ids`` 在候选落库前
    确定性补全；旧数据缺省 null。
    """

    model_config = ConfigDict(populate_by_name=True)

    param_key: str
    from_value: int | float | str | None = Field(default=None, alias="from")
    to_value: int | float | str = Field(alias="to")
    reason: str = ""
    node_id: str | None = None


class ReflectionCandidate(BaseModel):
    """待采纳的参数变更建议（只读投影，不含执行语义）。

    打包 ZU（docs/94 §3.2）增候选级处理标记：``decision_status`` 为 null 即待处理
    （pending），``adopted``/``dismissed`` 由人经 PUT decision 端点登记，可改判覆盖。
    """

    candidate_id: str
    graph_id: str
    base_version: int
    changes: list[Change] = []
    prompt_suggestions: list[str] = []
    evidence_digest: str = ""
    generated_at: str
    decision_status: DecisionStatus | None = None
    decided_at: str | None = None


class ReflectionReport(BaseModel):
    """一次 pass 的收尾记录（ring 100），供人查看与后续验收。

    docs/88 §4 只列 `candidate_id`/`status`/`reasons`；`graph_id`/`base_version`/
    `generated_at` 是 docs/12 列表端点（`?graph_id=&limit=`）所需的定位字段，两处契约不冲突。
    打包 ZU（docs/94 E-5）增 `decision_status`：candidate_id 非空时附候选当前处理态，
    列表投影时由 store 动态填充（候选已被 ring 裁掉则为 null）。
    """

    candidate_id: str | None = None
    graph_id: str
    base_version: int
    status: ReflectionStatus
    reasons: list[str] = []
    generated_at: str
    decision_status: DecisionStatus | None = None


class ReflectionStore:
    """进程内 ring（每租户一个；单锁；reset 清空）。PG 化随真实需求（docs/88 D-6）。"""

    def __init__(
        self,
        report_maxlen: int = REPORT_RING_SIZE,
        candidate_maxlen: int = CANDIDATE_RING_SIZE,
    ) -> None:
        self._reports: deque[ReflectionReport] = deque(maxlen=report_maxlen)
        self._candidates: deque[ReflectionCandidate] = deque(maxlen=candidate_maxlen)
        self._counter = 0
        self._lock = threading.Lock()

    def next_candidate_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"refl-{self._counter}"

    def add_candidate(self, candidate: ReflectionCandidate) -> ReflectionCandidate:
        with self._lock:
            self._candidates.append(candidate)
        return candidate

    def add_report(self, report: ReflectionReport) -> ReflectionReport:
        with self._lock:
            self._reports.append(report)
        return report

    def list_reports(self, graph_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """本租户收尾记录倒序（可按图过滤；limit 1–200，端点层再 clamp）。

        打包 ZU（docs/94 E-5）：candidate_id 非空的行附候选当前 decision_status；
        候选已被 ring 裁掉则为 null（照实，不报错）。状态动态取自 candidates ring，
        不依赖落报告时的快照，故改判后列表即时反映。
        """
        bounded = max(1, min(int(limit), 200))
        with self._lock:
            status_by_id = {row.candidate_id: row.decision_status for row in self._candidates}
            items = [
                item for item in reversed(self._reports)
                if graph_id is None or item.graph_id == graph_id
            ][:bounded]
            projected = [item.model_dump() for item in items]
        for row in projected:
            cid = row.get("candidate_id")
            if cid is not None:
                row["decision_status"] = status_by_id.get(cid)
        return projected

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        """按 id 取候选详情；不存在返 None（API 层 404）。`from`/`to` 以别名输出。"""
        with self._lock:
            item = next(
                (row for row in self._candidates if row.candidate_id == candidate_id), None
            )
        return item.model_dump(by_alias=True) if item is not None else None

    def record_decision(
        self,
        candidate_id: str,
        status: DecisionStatus,
        decided_at: str | None = None,
    ) -> bool:
        """登记/改判候选的人工处理标记（打包 ZU，docs/94 E-1/E-2）。

        允许覆盖（人可改主意），每次覆盖刷新 decided_at；候选不存在返 False（API 404）。
        只改处理标记，不改图、不发布、不动路由（守 T22）。
        """
        stamp = decided_at or _now_iso()
        with self._lock:
            item = next(
                (row for row in self._candidates if row.candidate_id == candidate_id), None
            )
            if item is None:
                return False
            item.decision_status = status
            item.decided_at = stamp
        return True

    def reset(self) -> None:
        with self._lock:
            self._reports.clear()
            self._candidates.clear()
            self._counter = 0


def to_change(row: dict[str, Any]) -> Change:
    """把摘要器给的原始行规整为 `Change`；`from` 缺失时按白名单 `current` 补齐。

    摘要器只负责说「改成什么」，当前值由白名单（代码缺省）或调用方提供。
    """
    key = row.get("param_key")
    if "from" not in row and isinstance(key, str) and key in TUNABLE_WHITELIST:
        row = {**row, "from": TUNABLE_WHITELIST[key].current}
    return Change(**row)


def validate_changes(changes: list[Change]) -> tuple[ReflectionStatus | None, list[str]]:
    """校验候选变更；通过返 `(None, [])`，否则返 `(status, 原因列表)`。"""
    unknown = [row.param_key for row in changes if not is_whitelisted(row.param_key)]
    if unknown:
        allowed = "、".join(sorted(TUNABLE_WHITELIST))
        return "rejected_whitelist", [
            f"参数 {key} 不在可调白名单内（整份候选作废，不做部分采纳）；允许：{allowed}"
            for key in unknown
        ]
    out_of_range = [
        row.param_key for row in changes if not in_bounds(row.param_key, row.to_value)
    ]
    if out_of_range:
        return "rejected_bounds", [
            f"参数 {key} 的建议值越界（允许区间 {TUNABLE_WHITELIST[key].bounds.min}–"
            f"{TUNABLE_WHITELIST[key].bounds.max}）"
            for key in out_of_range
        ]
    return None, []


def evidence_digest(evidence: ReflectionEvidence) -> str:
    """人可读的证据摘要（docs/88 §4 `evidence_digest`）。"""
    parts = [f"{evidence.graph_id}@{evidence.base_version}"]
    parts.append(f"运行 {sum(row.runs for row in evidence.per_version)} 条")
    if evidence.failed_nodes:
        top = evidence.failed_nodes[0]
        parts.append(f"失败节点 Top1 {top.node_id}×{top.count}")
    if evidence.replay is not None:
        parts.append(f"回放 {evidence.replay.matches}/{evidence.replay.total}")
    if evidence.shadow is not None:
        parts.append(f"影子 {'全符' if evidence.shadow.match else '有差异'}×{evidence.shadow.total}")
    if evidence.alerts:
        parts.append(f"未闭合告警 {len(evidence.alerts)} 条")
    return "；".join(parts)


def resolve_node_ids(changes: list[Change], graph_snapshot: dict[str, Any] | None) -> None:
    """就地补全 ``node.confidenceThreshold`` change 的 ``node_id``（docs/94 E-3，确定性）。

    规则：① change 已带 node_id（摘要器回包透传）时，仅当该 id 指向快照中存在的
    ai_decision 节点才保留，否则置 None（不信 LLM 给的错 id）；② 仍为 None 时，快照中
    **恰好 1 个** ai_decision 节点则自动填入，**0 个或 ≥2 个**保持 None（照实不猜）；
    ③ 其余三条白名单 key 的 change node_id 恒为 None；④ 快照缺失/形状异常不抛，
    全部保持 None（定位是增强，不是 pass 主链路）。
    """
    if not graph_snapshot:
        return
    raw_nodes = graph_snapshot.get("nodes")
    if not isinstance(raw_nodes, list):
        return
    ai_ids = [
        node.get("id")
        for node in raw_nodes
        if isinstance(node, dict) and node.get("type") == _NODE_TYPE
        and isinstance(node.get("id"), str)
    ]
    valid_ids = set(ai_ids)
    sole_id = ai_ids[0] if len(ai_ids) == 1 else None
    for change in changes:
        if change.param_key != _NODE_SCOPED_PARAM:
            change.node_id = None
            continue
        node_id = change.node_id
        if node_id is not None and node_id not in valid_ids:
            node_id = None
        if node_id is None:
            node_id = sole_id
        change.node_id = node_id


def _load_graph_snapshot(services: Any, graph_id: str, base_version: int) -> dict[str, Any] | None:
    """取基线版本的已发布图快照 raw dict（docs/94 E-3）；任何缺失/异常都返 None 不阻断 pass。"""
    graph_store = getattr(services, "graph_store", None)
    if graph_store is None:
        return None
    try:
        return graph_store.get(graph_id, base_version)
    except Exception:  # noqa: BLE001 - 定位是增强，快照读不到不影响反思主链路
        return None


def run_pass(
    services: Any,
    store: ReflectionStore,
    *,
    tenant_id: str,
    graph_id: str,
    base_version: int,
    summarizer: Summarizer | None = None,
    since: str | None = None,
    until: str | None = None,
    now: str | None = None,
) -> ReflectionReport:
    """跑一次反思 pass，返回收尾报告（候选命中时已落 ring）。

    纯函数式编排：读证据 → 问摘要器 → fail-closed 校验 → 落候选/报告。**无副作用外溢**：
    不写图、不发布、不改版本、不动路由。未配 LLM 时 `summarizer` 走确定性降级
    （`NullSummarizer`，零建议 ⇒ `status="ok"` 且 `candidate_id=None`，不报错，docs/88 D-7）。
    """
    stamp = now or _now_iso()
    evidence = build_evidence(
        services,
        tenant_id=tenant_id,
        graph_id=graph_id,
        base_version=base_version,
        since=since,
        until=until,
    )
    if evidence is None:
        return store.add_report(
            ReflectionReport(
                graph_id=graph_id,
                base_version=base_version,
                status="no_evidence",
                reasons=["无可用证据：该图近期无运行、无门禁报告、无未闭合告警"],
                generated_at=stamp,
            )
        )

    summarizer = summarizer or get_summarizer(tenant_id=tenant_id)
    raw_changes, suggestions = summarizer.summarize(evidence)
    changes = [to_change(row) for row in raw_changes]

    # 打包 ZU（docs/94 E-3）：节点级定位。快照缺失/异常不阻断 pass（定位是增强）。
    snapshot = _load_graph_snapshot(services, graph_id, base_version)
    resolve_node_ids(changes, snapshot)

    status, reasons = validate_changes(changes)
    if status is not None:
        return store.add_report(
            ReflectionReport(
                graph_id=graph_id,
                base_version=base_version,
                status=status,
                reasons=reasons,
                generated_at=stamp,
            )
        )

    if not changes and not suggestions:
        return store.add_report(
            ReflectionReport(
                graph_id=graph_id,
                base_version=base_version,
                status="ok",
                reasons=["证据未见需要调整的参数；未产出建议（未配 LLM 的确定性降级）"],
                generated_at=stamp,
            )
        )

    candidate_id = store.next_candidate_id()
    store.add_candidate(
        ReflectionCandidate(
            candidate_id=candidate_id,
            graph_id=graph_id,
            base_version=base_version,
            changes=changes,
            prompt_suggestions=suggestions,
            evidence_digest=evidence_digest(evidence),
            generated_at=stamp,
        )
    )
    return store.add_report(
        ReflectionReport(
            candidate_id=candidate_id,
            graph_id=graph_id,
            base_version=base_version,
            status="ok",
            reasons=[f"产出 {len(changes)} 条参数建议、{len(suggestions)} 条提示词建议"],
            generated_at=stamp,
        )
    )
