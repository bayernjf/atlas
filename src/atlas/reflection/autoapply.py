"""进化候选门控自动生效（docs/132 打包 BP，D75；docs/127 里程碑 E）。

推进链（BP-D4，每阶段 ``record_stage`` 落证据，任一失败中止留原因）：
  patch（BP-D3）→ 影子必跑 → 黄金回放（``run_release_gate``，blocked 中止）→
  自动 publish（gate 语义，blocked 中止）→ 自动 start canary（版本不足/未配置
  记录留证）→ 门控自动回滚（复用 ``routing/gate.py``，零改动）。

**自动生效是 opt-in（BP-D1）**：类别开关按 ``TunableScope`` 分开关，v1 只开图内参数类
（graph_variable＋node_config）；开关缺省全关（fail-closed）。候选含**未开启类别**的
可自动变更 ⇒ 中止留证（不部分自动）；monitor_rule/gate_config 行恒跳过（v1 不自动，
apply 目标在草稿外、影响防御面）；结构类恒不自动。**不自动 promote**（唯一放量路径仍
手动，docs/132 BP-D7）。

每圈证据（BP-D6）：``AutoApplyRun``（内存 ring 100/租户 ＋ PG 表 ``autoapply_runs``
迁移 053），"自动"的每一步都留下可否证记录（docs/127 里程碑 E Done-when）。

编排是**同步**的（端点响应即完成推进链）；``abort`` 覆盖 canary 态中止（=手动回滚）
与 pending 态取消，终态幂等返现状（BP-D5）。影子/门禁/发布/灰度全复用既有件：
registry、graph_resolver、publish_hook 由 API 端点注入（照 ``_release_gate_for_draft``
注入 registry/resolver 的先例），autoapply 不反向依赖 api.main。
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field

from ..graph.dsl import parse_graph
from ..graph.loader import _tool_permissions, run_graph
from ..recording.gate import run_release_gate
from ..recording.shadow import (
    extract_shadow_events,
    preset_all_approvals,
    preset_all_wait_events,
)
from ..tracing import Tracer
from .candidate import Change, ReflectionCandidate, _now_iso
from .patch import AUTO_SCOPES, PatchOutcome, patch_candidate
from .tunables import TUNABLE_WHITELIST

AUTOAPPLY_RING_SIZE = 100

RunStatus = str  # pending / completed / aborted / failed


class AutoApplyRun(BaseModel):
    """一次候选自动生效的**每圈证据**（docs/132 §3.2/BP-D6）。

    ``patch``/``shadow``/``gate`` 各自沉淀可否证记录；``stages`` 逐阶段
    ``{stage, at, ok, note}``；终态 ``status``：completed（链到底）/ failed（某步中止）/
    aborted（人中止）；``rollout_status`` 反映灰度进展（canary / rolled_back / idle）。
    """

    run_id: str
    candidate_id: str
    graph_id: str
    base_version: int
    enabled_scopes: list[str] = Field(default_factory=list)
    patch: dict[str, Any] | None = None
    shadow: dict[str, Any] | None = None
    gate: dict[str, Any] | None = None
    publish_version: int | None = None
    rollout_status: str | None = None
    status: RunStatus = "pending"
    reason: str | None = None
    stages: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class AutoApplyStore:
    """进程内 ring（每租户一个；单锁；reset 清空）。PG 档见 ``pg_autoapply.PgAutoApplyStore``。"""

    def __init__(self, maxlen: int = AUTOAPPLY_RING_SIZE) -> None:
        self._items: deque[AutoApplyRun] = deque(maxlen=maxlen)
        self._counter = 0
        self._lock = threading.Lock()

    def next_run_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"aa-{self._counter}"

    def add(self, run: AutoApplyRun) -> AutoApplyRun:
        with self._lock:
            self._items.append(run)
        return run

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = next((row for row in self._items if row.run_id == run_id), None)
        return item.model_dump() if item is not None else None

    def list(self, graph_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """本租户自动生效记录倒序（可按图过滤；limit 1–200，端点层再 clamp）。"""
        bounded = max(1, min(int(limit), 200))
        with self._lock:
            items = [
                item for item in reversed(self._items)
                if graph_id is None or item.graph_id == graph_id
            ][:bounded]
        return [item.model_dump() for item in items]

    def update(self, run: AutoApplyRun) -> AutoApplyRun:
        """整体替换（编排就地改 stages/status 后落库）。"""
        with self._lock:
            for idx, row in enumerate(self._items):
                if row.run_id == run.run_id:
                    self._items[idx] = run
                    break
            else:  # 环裁掉后的更新：补回（run_autoapply 全程同步，正常不会发生）
                self._items.append(run)
        return run

    def reset(self) -> None:
        with self._lock:
            self._items.clear()
            self._counter = 0


def _scope_of(param_key: str) -> str | None:
    param = TUNABLE_WHITELIST.get(param_key)
    return param.scope if param is not None else None


def _record_stage(
    run: AutoApplyRun, stage: str, *, ok: bool, note: str = ""
) -> None:
    run.stages.append({"stage": stage, "at": _now_iso(), "ok": ok, "note": note})
    run.updated_at = _now_iso()


def run_autoapply(
    services: Any,
    *,
    tenant_id: str,
    candidate_id: str,
    enabled_scopes: set[str],
    registry: Any,
    graph_resolver: Callable[[str], Any],
    publish_hook: Callable[[Any, str, str], int],
) -> dict[str, Any]:
    """跑一次候选自动生效推进链；返回 AutoApplyRun 投影（含每圈证据）。

    任何前置不满足/步骤失败都**中止留证**（status=failed + reason），绝不静默降级；
    monitor/gate 类别的行在 patch 里跳过并计入 diff（BP-D1），不阻断其余行。
    """
    store = services.autoapply_store
    reflection_store = services.reflection_store
    candidate_raw = reflection_store.get_candidate(candidate_id)
    if candidate_raw is None:
        raise LookupError(f"反思候选不存在：{candidate_id}")
    candidate = ReflectionCandidate(**candidate_raw)
    if candidate.decision_status is not None:
        raise ValueError(
            f"候选已处理（decision_status={candidate.decision_status}），不自动生效"
        )

    run = AutoApplyRun(
        run_id=store.next_run_id(),
        candidate_id=candidate_id,
        graph_id=candidate.graph_id,
        base_version=candidate.base_version,
        enabled_scopes=sorted(enabled_scopes),
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    _record_stage(run, "start", ok=True, note=f"候选 {candidate_id} 自动生效启动")
    store.add(run)

    changes = [Change.model_validate(c) for c in (candidate_raw.get("changes") or [])]

    # ① 类别开关（BP-D1 fail-closed）：可自动类别全部显式开启才继续，越类别中止留证。
    candidate_auto_scopes = {_scope_of(c.param_key) for c in changes} & AUTO_SCOPES
    if not candidate_auto_scopes:
        return _finish_failed(
            store, run, "候选无可自动类别的变更（图内参数类为空，全跳过不自动）"
        )
    missing = candidate_auto_scopes - set(enabled_scopes)
    if missing:
        return _finish_failed(
            store, run,
            f"候选含未开启类别的自动变更（{sorted(missing)}），类别开关未开（fail-closed）",
        )

    # ② patch（BP-D3）：dirty 草稿中止，不覆盖人工改动。dirty 判据＝内容比较：
    # latest 草稿 ≠ base_version 发布快照（排除 releaseVersion）或快照缺失 → 中止。
    outcome = patch_candidate(
        changes=changes,
        latest_draft=services.graph_store.get(candidate.graph_id),
        base_version=candidate.base_version,
        graph_snapshot=services.graph_store.get(
            candidate.graph_id, candidate.base_version
        ),
    )
    if not outcome.ok:
        return _finish_failed(store, run, outcome.reason or "patch 未通过")
    run.patch = {"diff": outcome.diff}
    _record_stage(run, "patch", ok=True, note=f"diff {len(outcome.diff)} 条")

    # ③ 影子必跑（健康检查语义：空 inputs＋预置审批/等待秒过）。
    shadow = _run_shadow_check(
        services, tenant_id=tenant_id, graph_id=candidate.graph_id,
        draft=outcome.new_draft, registry=registry, graph_resolver=graph_resolver,
    )
    run.shadow = shadow
    if shadow["status"] == "error":
        return _finish_failed(store, run, f"影子必跑未通过：{shadow.get('error')}")
    _record_stage(run, "shadow", ok=True, note="影子运行完成（无异常）")

    # ④ 黄金回放（run_release_gate）：blocked 中止；total=0（skipped）不阻塞。
    gate = run_release_gate(
        graph_id=candidate.graph_id,
        draft=outcome.new_draft,
        cases=services.recording_store.list(),
        services=services,
        registry=registry,
        graph_resolver=graph_resolver,
    )
    run.gate = {
        "total": gate["total"],
        "passed": gate["passed"],
        "failed": gate["failed"],
        "skipped": gate["skipped"],
        "blocked": gate["blocked"],
    }
    if gate["blocked"]:
        return _finish_failed(
            store, run, f"黄金回放未通过（{gate['failed']}/{gate['total']} 例不匹配），已拦截发布"
        )
    _record_stage(
        run, "gate", ok=True,
        note=f"黄金回放 {gate['passed']}/{gate['total']} 通过"
        + ("（无录制用例，skipped 不阻塞）" if gate["skipped"] else ""),
    )

    # ⑤ patch 落地：把新草稿写回 latest（否则 publish 发布的是旧草稿，自动生效名存实亡）。
    #    只在此刻（shadow/gate 都通过后）写回——blocked/failed 路径不触碰草稿。
    try:
        services.graph_store.update_draft(candidate.graph_id, outcome.new_draft)
    except Exception as exc:  # noqa: BLE001 - 写回失败中止留证
        return _finish_failed(store, run, f"自动写回草稿失败：{type(exc).__name__}: {exc}")

    # ⑥ 自动 publish（gate 语义）：publish_hook＝publish_graph_version＋调度派生。
    try:
        version = publish_hook(services, tenant_id, candidate.graph_id)
    except Exception as exc:  # noqa: BLE001 - 发布失败中止留证
        return _finish_failed(store, run, f"自动发布失败：{type(exc).__name__}: {exc}")
    run.publish_version = version
    _record_stage(run, "publish", ok=True, note=f"发布为版本 {version}")

    # ⑦ 自动 start canary（版本不足/未配置 → 记录"已发布未进灰度"留证，不报错）。
    try:
        state = services.routing_store.start(
            candidate.graph_id, services.graph_store.list_versions(candidate.graph_id)
        )
        run.rollout_status = state.status
        _record_stage(run, "canary", ok=True, note=f"灰度已启动（{state.status}）")
    except Exception as exc:  # noqa: BLE001 - RolloutError（版本不足/未配置）照契约留证
        run.rollout_status = "idle"
        _record_stage(
            run, "canary", ok=False,
            note=f"已发布但未进灰度：{exc}（留证，不报错）",
        )

    run.status = "completed"
    run.updated_at = _now_iso()
    store.update(run)
    return store.get(run.run_id) or run.model_dump()


def abort_autoapply(services: Any, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    """人可随时中止（BP-D5）：canary 态中止＝手动回滚；pending 态取消；终态幂等返现状。"""
    store = services.autoapply_store
    run_raw = store.get(run_id)
    if run_raw is None:
        return None
    run = AutoApplyRun(**run_raw)
    if run.status == "pending":
        run.status = "aborted"
        run.reason = "用户中止"
        _record_stage(run, "abort", ok=True, note="pending 态取消（未进入推进链）")
        store.update(run)
        return store.get(run_id)
    if run.status == "completed" and run.rollout_status == "canary":
        try:
            state = services.routing_store.rollback(
                run.graph_id, actor="manual", reason="进化自动生效被用户中止"
            )
            run.rollout_status = state.status
        except Exception as exc:  # noqa: BLE001 - 回滚失败也留证，不吞
            _record_stage(run, "abort", ok=False, note=f"手动回滚失败：{exc}")
            store.update(run)
            return store.get(run_id)
        run.status = "aborted"
        run.reason = "用户中止（canary 已手动回滚）"
        _record_stage(run, "abort", ok=True, note=f"canary 中止并回滚（{state.status}）")
        store.update(run)
        return store.get(run_id)
    # 其余终态（completed 无 canary / failed / 已 aborted）幂等返现状。
    return run_raw


def _finish_failed(
    store: AutoApplyStore, run: AutoApplyRun, reason: str
) -> dict[str, Any]:
    run.status = "failed"
    run.reason = reason
    _record_stage(run, "abort", ok=False, note=reason)
    store.update(run)
    return store.get(run.run_id) or run.model_dump()


def _run_shadow_check(
    services: Any,
    *,
    tenant_id: str,
    graph_id: str,
    draft: dict[str, Any],
    registry: Any,
    graph_resolver: Callable[[str], Any],
) -> dict[str, Any]:
    """对草稿跑一次影子（健康检查语义，照既有 shadow-runs 端点同款纪律）。

    预置全部 human_approval 秒过、event wait 空 payload 秒过；独立 broker/tracer；
    写短路（loader shadow）；不进生产观测面。异常也折叠为 status=error（不抛）。
    """
    graph = parse_graph(draft)
    inputs: dict[str, Any] = {}
    presets = preset_all_approvals(graph, resolver=graph_resolver)
    if presets:
        inputs["approvals"] = presets
    event_presets = preset_all_wait_events(graph, resolver=graph_resolver)
    if event_presets:
        inputs["waitEvents"] = event_presets

    registry_ = registry
    tool_permissions = _tool_permissions(registry_)
    node_index = {node.id: node for node in graph.nodes}
    top_events: list[tuple[str, dict[str, Any]]] = []

    def shadow_emit(event: dict[str, Any]) -> None:
        if event.get("type") == "node_end" and not event.get("subgraphPath"):
            output = event.get("output")
            if isinstance(output, dict):
                top_events.append((event["node_id"], output))

    tracer = Tracer(graph_id=graph_id)
    shadow_broker = services.approval_broker.__class__()  # 独立 broker：不污染租户 pending
    status = "completed"
    error: str | None = None
    try:
        run_graph(
            graph,
            inputs=inputs,
            registry=registry_,
            approval_broker=shadow_broker,
            graph_id=graph_id,
            graph_resolver=graph_resolver,
            emit=shadow_emit,
            tracer=tracer,
            shadow=True,
            tenant_id=tenant_id,
            memory_repo=services.memory_store,
        )
    except Exception as exc:  # noqa: BLE001 - 影子异常折叠为 error，绝不阻断生产链路
        status = "error"
        error = f"{type(exc).__name__}: {exc}"

    decisions, intents = extract_shadow_events(top_events, node_index, tool_permissions)
    return {
        "status": status,
        "error": error,
        "trace_id": tracer.trace_id,
        "decisions": [d.model_dump() for d in decisions],
        "intent_count": len(intents),
    }
