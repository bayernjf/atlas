# -*- coding: utf-8 -*-
"""圈驱动器（docs/130 打包 BN＝D69；C-D1/C-D3/C-D5/C-D6/C-D7）。

**一圈＝一次真实 `run_graph`**，所以本模块**不复制**四个运行入口的 run 生命周期
（begin/finish/record_run/frame_sink/监控埋点各有一整套，抄一遍就是两套真相）。做法是
**回调式驱动器**：调用方把"跑一圈"封成一个 `run_once(step)` 交给本模块，本模块只管
圈的事——开会话、发号、carry 注入、终止判定、三道闸门、圈间间隔、token 记账、停止。

## 哪些入口参与圈（v1）

- **参与**：同步 `POST /api/graphs/{id}/run`（人/API 手动跑）与后台 worker
  （cron/webhook/调度触发）——这两条正是"自走循环"的产品形态。
- **不参与**（配置存在时**只跑第一圈**，并在运行记录与 SSE log 里明说 `loopSkipped`）：
  流式 `POST /run/stream` 与续跑 `_resume_run`。理由：流式那条要重排终帧与
  `event: result` 的终止判定（前端按它认结束），续跑那条的起点是**帧**而不是一次触发。
  圈在定时/回调侧自走，编辑器里看第一圈——边界写在 docs/130 §3.2 C-D10，不是静默忽略。

## 三条不许越的线（docs/130 §5）

1. **不写圈帧**：圈状态只落 `runs` 两列 ＋ 进程内 registry（C-D6/D44）。
2. **不在圈内等待挂起**：遇 suspended 立即退出交还帧机制（C-D5）。
3. **不解锁多副本**：registry 是进程内单例，单副本语义（docs/62）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from atlas.cycles.judge import evaluate_complete_when, llm_judge
from atlas.cycles.state import (
    CycleRegistry,
    CycleSession,
    budget_exhausted,
    cycle_registry,
)

logger = logging.getLogger(__name__)

#: v1 圈配置的缺省（docs/130 §3.1 表格；编译期已校验区间，运行期只做归一化）。
DEFAULT_MAX_ITERATIONS = 10
DEFAULT_MAX_WALL_CLOCK = 1800
DEFAULT_MAX_TOKENS = 200000
DEFAULT_INTERVAL_SECONDS = 0


def cycle_config(graph: Any) -> dict[str, Any] | None:
    """取图的圈配置并归一化；**`None`/`enabled=False` ⇒ 不启用**（返回 None）。

    启用面缺省关闭是本批的唯一运行期行为变更的护栏（C-D2）：既有图逐字走原路径。
    """
    raw = getattr(graph, "loop", None)
    if not isinstance(raw, dict):
        return None
    if raw.get("enabled") is False:
        return None
    carry = raw.get("carry") if isinstance(raw.get("carry"), dict) else {}

    def _int(name: str, default: int) -> int:
        value = raw.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else default

    return {
        "maxIterations": _int("maxIterations", DEFAULT_MAX_ITERATIONS),
        "maxWallClockSeconds": _int("maxWallClockSeconds", DEFAULT_MAX_WALL_CLOCK),
        "maxTokens": _int("maxTokens", DEFAULT_MAX_TOKENS),
        "completeWhen": str(raw.get("completeWhen") or ""),
        "judgeMode": "llm" if raw.get("judgeMode") == "llm" else "rule",
        "judgePrompt": str(raw.get("judgePrompt") or ""),
        "carry": dict(carry),
        "intervalSeconds": _int("intervalSeconds", DEFAULT_INTERVAL_SECONDS),
    }


@dataclass
class CycleStep:
    """交给 `run_once` 的一圈参数（`run_once` 据此跑一次真实运行）。"""

    group_id: str
    seq: int
    inputs: dict[str, Any]
    is_last_hint: bool = False  # 仅提示（预算看起来只剩一圈），**不授权**跳闸


@dataclass
class CycleOutcome:
    """`run_once` 交回的一圈结果（由调用方按各自入口的既有语义填）。"""

    run_id: str
    status: str  # completed/failed/cancelled/superseded/interrupted
    outputs: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    total_tokens: int = 0
    stop_run: bool = False  # 人叫停了这一圈（cancel）⇒ 不开下一圈


class RunOnce(Protocol):
    """一圈的驱动器：调用方实现（内部走既有 run 生命周期）。"""

    def __call__(self, step: CycleStep) -> CycleOutcome: ...


def tokens_for_runs(services: Any, run_ids: list[str]) -> int:
    """按 run_id 求 token 合计（打包 AU 的账；无记录按 0，不报错）。

    口径说明：走 `list_recent(limit=200)` 的明细扫描，因此**单会话超过 200 条用量行
    时会低估**—— AU 的端点没有按 run_id 聚合的读面，加一个只为圈预算服务不划算
    （圈预算本身还有墙钟与迭代两道闸门兜着）。低估方向是"更早触顶"，不会烧超。
    """
    if not run_ids:
        return 0
    store = getattr(services, "usage_store", None)
    if store is None:
        return 0
    try:
        rows = store.list_recent(limit=200)
    except Exception as exc:  # noqa: BLE001 - 记账失败不许影响圈
        logger.warning("cycle 读取用量失败（按 0 计）：%s", exc)
        return 0
    wanted = set(run_ids)
    return sum(int(row.get("totalTokens", 0)) for row in rows if row.get("runId") in wanted)


def render_carry(carry: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    """把 carry 声明的 `{{路径}}` 对本圈 context 求值（复用既有插值器，零新语法）。

    **解析不出来的键直接不带**（而不是把 `{{no.such.path}}` 这种字面占位符塞给下一圈）：
    下游节点拿到字面占位符会以为那是数据——那是"伪造事实"，比少一个键坏得多。
    """
    if not carry:
        return {}
    from atlas.graph.interpolation import render_mapping_value

    rendered: dict[str, Any] = {}
    for key, template in carry.items():
        try:
            value = render_mapping_value(str(template), context)
        except Exception as exc:  # noqa: BLE001 - 单键坏不拖垮整圈
            logger.warning("cycle carry 键 %s 插值失败（本圈不带该键）：%s", key, exc)
            continue
        if isinstance(value, str) and "{{" in value and "}}" in value:
            logger.warning(
                "cycle carry 键 %s 的路径在本圈取不到值（%s），本圈不带该键", key, value
            )
            continue
        rendered[key] = value
    return rendered


def run_cycle_session(
    services: Any,
    graph: Any,
    *,
    tenant_id: str,
    graph_id: str,
    inputs: dict[str, Any],
    run_once: RunOnce,
    registry: CycleRegistry | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """跑整条圈会话；返回会话投影（`GET /api/runs/{id}/cycle` 的形状）。

    **未启用圈**（`cycle_config` 返 None）⇒ 只跑一圈（`run_once(seq=1)`）并把结果原样
    带回：调用方拿到的仍是"那一次运行"，圈层不存在（零行为变化，C-D2）。
    """
    config = cycle_config(graph)
    current_inputs = dict(inputs or {})
    if config is None:
        outcome = run_once(CycleStep(group_id="", seq=1, inputs=current_inputs))
        return {
            "enabled": False,
            "groupId": "",
            "runs": [outcome.run_id],
            "outcome": outcome,
            "loopSkipped": False,
        }

    reg = registry or cycle_registry()
    session = reg.create(
        tenant_id=tenant_id,
        graph_id=graph_id,
        max_iterations=int(config["maxIterations"]),
        max_wall_clock_seconds=int(config["maxWallClockSeconds"]),
        max_tokens=int(config["maxTokens"]),
    )
    logger.info("cycle 会话开始：group=%s graph=%s 预算 迭代≤%s 墙钟≤%ss token≤%s",
                session.group_id, graph_id, config["maxIterations"],
                config["maxWallClockSeconds"], config["maxTokens"])

    last: CycleOutcome | None = None
    while True:
        step = CycleStep(group_id=session.group_id, seq=session.seq, inputs=dict(current_inputs))
        outcome = run_once(step)
        last = outcome
        reg.bind_run(outcome.run_id, session.group_id)
        # token：AU 的账按 run_id 归集，这里取全会话累计并折成本圈增量（读不到按 0）
        prev_cumulative = session.tokens_spent
        cumulative = tokens_for_runs(
            services, [ref.run_id for ref in session.runs] + [outcome.run_id]
        )
        session.record_run(
            outcome.run_id,
            seq=step.seq,
            status=outcome.status,
            total_tokens=max(0, cumulative - prev_cumulative),
        )

        # ① 圈内挂起 ⇒ 圈层立即退出，把控制权交还帧机制（C-D5：不写圈帧）
        if outcome.status in ("suspended", "interrupted"):
            reg.finish(session.group_id, state="suspended", stop_reason=f"run_{outcome.status}")
            logger.info("cycle 会话挂起让位：group=%s seq=%s run=%s",
                        session.group_id, step.seq, outcome.run_id)
            break
        # ② 人叫停这一圈 ⇒ 不开下一圈，但**圈本身**未必终止（停整条圈是另一个动作，C-D8）
        if outcome.stop_run or outcome.status == "cancelled":
            reg.finish(session.group_id, state="stopped", stop_reason="run_cancelled")
            break
        if outcome.status != "completed":
            reg.finish(session.group_id, state="failed", stop_reason=f"run_{outcome.status}")
            break
        # ③ 规则优先的终止判定（C-D4）
        if _judge_complete(
            config, outcome, services=services, tenant_id=tenant_id, graph_id=graph_id
        ):
            reg.finish(session.group_id, state="completed", stop_reason="complete_when")
            break
        # ④ 三道预算闸门（C-D3）——按**已完成的圈数**（= step.seq）判
        gate = budget_exhausted(session)
        if gate:
            reg.finish(session.group_id, state="budget_exhausted", stop_reason=gate)
            logger.info("cycle 会话预算触顶：group=%s 闸门=%s 已完成 %s 圈",
                        session.group_id, gate, step.seq)
            break
        # ⑤ 人叫停**整条圈**（C-D8 的 stop 端点）⇒ 不开下一圈。
        # 这条检查必须在开新圈之前：少了它，"停整条圈"只停得下一圈，人点了还在跑。
        if session.is_stopped():
            logger.info("cycle 会话已被叫停，不再开新圈：group=%s 已完成 %s 圈",
                        session.group_id, step.seq)
            break
        # ⑥ 发下一圈的号 + carry 注入（C-D7：缺省无 carry ＝ 原样起跑）
        session.seq = step.seq + 1
        current_inputs = next_cycle_inputs(config, current_inputs, outcome)
        # ⑦ 圈间间隔（可选；缺省 0＝不休眠）
        interval = int(config["intervalSeconds"])
        if interval > 0:
            sleep(float(interval))

    view = session.public_view()
    view["enabled"] = True
    view["loopSkipped"] = False
    view["outcome"] = last
    return view


def _judge_complete(
    config: dict[str, Any], outcome: CycleOutcome, *,
    services: Any, tenant_id: str, graph_id: str,
) -> bool:
    """终止判定：规则优先（`completeWhen`），LLM 仅在显式开启时作二段（C-D4）。

    **判定那一次 LLM 调用也要计量**：判定发生在 `run_graph` 返回**之后**，此时运行入口
    设在 run 体内的 usage 上下文已经退出——所以这里自己补一段上下文（挂本圈 run_id），
    否则圈判定的成本会从 AU 的账上漏掉（那正是打包 AU 建立按 caller 归因的意义所在）。
    """
    from atlas.llm.metering import usage_context

    expression = str(config.get("completeWhen") or "")
    context = {"global": outcome.variables or {}, **(outcome.outputs or {})}
    llm_enabled = (
        config.get("judgeMode") == "llm" and str(config.get("judgePrompt") or "").strip()
    )

    def _llm_second_stage() -> bool:
        store = getattr(services, "usage_store", None)
        with usage_context(
            tenant_id, run_id=outcome.run_id, graph_id=graph_id, store=store
        ):
            return llm_judge(
                str(config["judgePrompt"]), context, tenant_id=tenant_id, model=None
            )

    if expression.strip():
        if evaluate_complete_when(expression, context):
            return True
        # 规则说"还没成"时，若运营显式开了 LLM 二段，再问一次模型。
        return _llm_second_stage() if llm_enabled else False
    return _llm_second_stage() if llm_enabled else False


def next_cycle_inputs(
    config: dict[str, Any], base_inputs: dict[str, Any], outcome: CycleOutcome
) -> dict[str, Any]:
    """下一圈的 `inputs`＝本圈 inputs ＋ carry 声明的映射（C-D7，缺省无 carry＝原样起跑）。

    carry 走既有 `inputs` 通道，因此同时进 `variables.global` 与 trigger payload
    ——与现状 `inputs` 的双角色语义一致（`initial_state`）。
    """
    merged = dict(base_inputs or {})
    context = {"global": outcome.variables or {}, **(outcome.outputs or {})}
    merged.update(render_carry(config.get("carry") or {}, context))
    return merged


def carry_inputs_for_next(
    config: dict[str, Any], base_inputs: dict[str, Any], outcome: CycleOutcome
) -> dict[str, Any]:
    """`next_cycle_inputs` 的别名（供调用方按"给下一圈备输入"语义读）。"""
    return next_cycle_inputs(config, base_inputs, outcome)


def session_for_run(run_id: str, registry: CycleRegistry | None = None) -> CycleSession | None:
    """由 runId 反查圈会话（REST 层用；非圈 run ⇒ None）。"""
    return (registry or cycle_registry()).find_by_run(run_id)
