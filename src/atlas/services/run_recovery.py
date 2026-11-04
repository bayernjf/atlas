# -*- coding: utf-8 -*-
"""Run recovery runtime extracted from ``atlas.api.main`` (third batch).

The module owns the startup scan (``recover_pending``), the pending rebuild
from a persisted frame (``resume_from_frame``), the resume thread body
(``resume_run``) and the two PG gates that pair with every persisted frame
(``frame_sink_for`` / ``resume_claim_for``).

Backend switches are passed in at call time instead of captured at import so
``atlas.api.main`` stays the single source of truth for ``STORAGE_BACKEND``;
that keeps existing monkeypatch-based tests working unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from atlas.graph.dsl import GraphDSL
from atlas.graph.loader import RunSuperseded, WaitTimeoutSuspend
from atlas.llm.metering import usage_context
from atlas.services.error_safety import safe_run_error, token_ref
from atlas.storage.frame import card_context_from_frame, remaining_seconds
from atlas.storage.recovery import clear_frame, load_pending_frames, make_frame_sink, make_resume_claim

logger = logging.getLogger(__name__)

PgEngine = Callable[[], Any]


def recover_pending(
    *, storage_backend: str, pg_engine: PgEngine, resume_from_frame: Callable[[Any, dict], None]
) -> None:
    """服务启动钩子：读未决挂起帧 → 逐帧重建 pending + 重启续跑线程（docs/24 §2.4）。

    仅 PG 后端生效（进程内后端重启即失、无帧可恢复）；失败帧隔离记日志、不阻塞其余。
    """
    if storage_backend != "pg":
        return
    try:
        engine = pg_engine()
        frames = load_pending_frames(engine)
    except Exception as exc:  # 无 DATABASE_URL / PG 未就绪时不阻断启动
        logger.warning("中断恢复扫描跳过：%s", exc)
        return
    for frame in frames:
        try:
            resume_from_frame(engine, frame)
        except Exception as exc:
            logger.warning("帧 %s 恢复失败、隔离跳过：%s", token_ref(frame.get("resume_token")), exc)


def resume_from_frame(
    engine: Any,
    frame: dict,
    *,
    services_for: Callable[[str], Any],
    submit_resume_run: Callable[[Any, Any, dict], None],
) -> None:
    """按帧重建 pending（approval 重挂 Event + 剩余 deadline），并重启续跑线程。"""
    services = services_for(frame["tenant_id"])
    token = frame["resume_token"]
    if frame.get("resumed_at") is not None:
        # docs/62 §2 D-1 / §8.1 第②种：帧已被某进程认领（写帧后、执行完前该进程崩了），
        # at-most-once 语义下不再为它重建 pending、不起线程——宁可这条 run 停在 suspended，
        # 也不重复执行下游；开口收敛属 D36（人工重放/reconcile）。
        logger.info(
            "帧 %s 已由 %s 认领，跳过恢复（不重建 pending、不起续跑线程）",
            token,
            frame.get("resumed_by") or "其它进程",
        )
        return
    if frame["kind"] == "approval":
        card_template_id = frame.get("card_template_id") or None
        services.approval_broker.restore(
            token=token,
            node_id=frame["node_id"],
            graph_id=frame["resume_state"].get("graph_id", ""),
            summary=frame.get("summary", ""),
            approver=frame.get("approver", ""),
            remaining_seconds=remaining_seconds(frame.get("deadline_at")),
            card_template_id=card_template_id,
            card_context=card_context_from_frame(frame) if card_template_id else None,
        )
    elif frame["kind"] == "wait" and (frame.get("wait") or {}).get("waitType") == "event":
        wait_payload = frame["wait"]
        # docs/54：多键竞速帧存 eventKeys；单键帧只有 eventKey（首键）。
        # docs/55：帧存 eventWaitMode（all=AND 竞速；缺省 any 兼容旧帧）。
        _frame_keys = wait_payload.get("eventKeys")
        services.event_wait_broker.restore(
            token=token,
            event_key=wait_payload.get("eventKey"),
            event_keys=_frame_keys
            if isinstance(_frame_keys, list) and _frame_keys
            else None,
            node_id=frame["node_id"],
            graph_id=frame["resume_state"].get("graph_id", ""),
            timeout_seconds=remaining_seconds(frame.get("deadline_at")),
            mode=wait_payload.get("eventWaitMode")
            if wait_payload.get("eventWaitMode") in ("any", "all")
            else "any",
        )
    submit_resume_run(engine, services, frame)


def resume_run(
    engine: Any,
    services: Any,
    frame: dict,
    *,
    run_graph_fn: Callable[..., dict],
    runtime_registry: Callable[[Any], Any],
    graph_resolver: Callable[[Any], Any],
    secret_provider: Any,
) -> None:
    """续跑线程：从挂起节点沿边到 END，完成后清帧并落 run 终态（决策 / wait 到点 / 再超时均覆盖）。"""
    run_id = frame.get("run_id", "")
    try:
        resume_graph = GraphDSL.model_validate(frame["graph_snapshot"])
        with usage_context(
            frame["tenant_id"],
            run_id=run_id,
            graph_id=frame["resume_state"].get("graph_id", ""),
            store=services.usage_store,
        ):
            result = run_graph_fn(
                resume_graph,
                inputs=frame["resume_state"].get("inputs", {}),
                registry=runtime_registry(services),
                approval_broker=services.approval_broker,
                event_wait_broker=services.event_wait_broker,
                graph_id=frame["resume_state"].get("graph_id", ""),
                graph_resolver=graph_resolver(services),
                frame_sink=make_frame_sink(engine, frame["tenant_id"], run_id),
                resume_claim=make_resume_claim(engine),
                resume=frame,
                tenant_id=frame["tenant_id"],
                secret_provider=secret_provider,
                memory_repo=services.memory_store,
            )
        if run_id:
            services.run_store.finish(
                run_id=run_id, status="completed",
                outputs=result["outputs"], trace=result["trace"],
            )
        clear_frame(engine, frame["resume_token"])
    except RunSuperseded as exc:
        # docs/62 §2 D-4：续跑输家——帧已被其它进程认领，本线程停止驱动。
        # 既不写 run 终态也不清帧（终态与清理都归赢家），否则会把赢家的执行结果覆盖掉。
        logger.info("续跑让位 %s：%s", token_ref(frame.get("resume_token")), exc)
    except WaitTimeoutSuspend as exc:
        # docs/136 打包 BS：续跑中挂起节点超时且 onTimeout="suspend"——run 保持 suspended
        # （帧保留、不写终态、不清帧），等人工后续恢复；本线程停止驱动即可。
        logger.info("续跑保持挂起 %s：%s", token_ref(frame.get("resume_token")), exc)
    except Exception as exc:
        logger.error("续跑 %s 失败：%s", token_ref(frame.get("resume_token")), exc)
        if run_id:
            services.run_store.finish(
                run_id=run_id, status="failed", error=safe_run_error(exc)
            )


def frame_sink_for(
    tenant_id: str, run_store: Any, run_id: str, *, storage_backend: str, pg_engine: PgEngine
):
    """挂起回调：先落 run 状态为 suspended（所有后端），PG 后端再写 interruptions 帧。"""

    def sink(frame: dict) -> None:
        run_store.suspend(
            run_id=run_id,
            node_id=frame["node_id"],
            kind=frame["kind"],
            resume_token=frame["resume_token"],
            deadline_at=frame.get("deadline_at"),
        )
        if storage_backend == "pg":
            make_frame_sink(pg_engine(), tenant_id, run_id)(frame)

    return sink


def resume_claim_for(*, storage_backend: str, pg_engine: PgEngine) -> Callable[[str], bool] | None:
    """挂起帧认领门（docs/62 §3.4）：只有 PG 后端有帧可认领，进程内后端返回 None＝恒放行。

    与 `frame_sink` 严格配对注入——凡写帧的运行必带认领门，反之亦然，否则会出现
    "有帧无人认领判定"或"无帧却被判定为已被接管"两种漂移。
    """
    if storage_backend != "pg":
        return None
    return make_resume_claim(pg_engine())
