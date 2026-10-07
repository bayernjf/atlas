"""FastAPI Demo 入口（docs/12 §5；T5：Demo 用 Python 实现网关同构接口）。

W7-W8：Graph DSL 保存/读取/编译/运行。存储为进程内字典
（与 T4 进程内总线一致；持久化待业务表 DDL，docs/11 S1）。
W9-W10：退款 Demo 装配（共享 shop 服务/注册表）、SSE 运行进度、
NL 生成草稿、适配器发现、模拟商家售后控制台。
"""

from __future__ import annotations

import json
import copy
import hmac
import logging
import os
import queue
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import uuid
from itertools import count
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import httpx

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from atlas.cards import (
    CardRenderError,
    get_card,
    list_cards,
    map_action_output,
    render_card,
)
from atlas.debug import DebugController, DebugStopped
from atlas.graph.conditions import ConditionEvalError, validate_expression
from atlas.graph.dsl import GraphDSL, GraphValidationError, parse_graph, valid_event_key
from atlas.graph.diff import diff_graph, diff_summary
from atlas.graph.loader import (
    AiDecisionUnavailable,
    ConditionClassifierUnavailable,
    RunSuperseded,
    SubgraphSuspendUnsupported,
    WaitNodeFailure,
    _tool_permissions,
    compile_graph,
    run_graph,
    runtime_error_meta,
    tool_input_schemas,
)
from atlas.collaboration.cancellations import RunCancelled
from atlas.collaboration.event_waits import (
    WaitAlreadySignaled,
    WaitTokenNotFound,
)
from atlas.collaboration.notifications import EmailApprovalNotifier
from atlas.collaboration.email_token import EmailTokenError, TokenIssuer
from atlas.tracing import Tracer
from atlas.harness.runtime import (
    build_base_registry,
    build_runtime_registry,
    resolve_database_client,
)
from atlas.iam.deps import (
    SESSION_COOKIE,
    authenticate_login,
    get_principal,
    login_throttle,
    must_change_password,
    presented_token,
    require,
    services_for,
    session_store,
    tenant_registry,
    user_store,
)
from atlas.iam.accounts import UserExists
from atlas.iam.passwords import validate_password, validate_username, verify_password
from atlas.iam.principals import Principal, Role, can
from atlas.iam.registry import STORAGE_BACKEND, TenantServices
from atlas.llm.config import ModelConfig
from atlas.llm.decision import get_decision_client
from atlas.llm.nl_generate import generate_graph, validate_param_fills
from atlas.memory.database import ping, wait_for_database
from atlas.memory.models import MemoryValidationError
from atlas.message.service import MessageSendError
from atlas.monitoring import RUN_RING_SIZE, extract_business, extract_node_results
from atlas.observability.audit import AUDITED_METHODS, LOGIN_PATH
from atlas.channels.base import (
    CHANNEL_AUTHORIZATION_ERROR_CODES,
    CHANNEL_UPSTREAM_UNAUTHORIZED,
    ChannelBinding,
    ChannelError,
)
from atlas.channels.webhooks import (
    HMAC_HEADER,
    SUPPORTED_TOPICS,
    WebhookDeliverer,
    build_envelope,
    verify_shopify_hmac,
)
from atlas.a2a.router import router as a2a_router  # docs/90 ADR T32 (Zeus A2A execution-agent face)
from atlas.connections.service import ConnectionServiceError
from atlas.observability.health import check_ready
from atlas.observability.logging import (
    configure_logging,
    install_request_id_middleware,
)
from atlas.observability.metrics_export import render_prometheus
from atlas.openapi.errors import OpenApiError
from atlas.openapi.parser import parse_document
from atlas.openapi.store import ImportStoreError
from atlas.security.bootstrap import (
    assert_prod_secrets,
    assert_prod_storage_backend,
    demo_surface_enabled,
    hydrate_file_secrets,
    read_env_profile,
    read_public_url,
)
from atlas.security.egress import EgressDenied, EgressGuard
from atlas.security.secrets import build_secret_provider_from_env
from atlas.monitoring.silences import OnCallEmpty, current_assignee, is_silence_active
from atlas.monitoring.rule_templates import get_rule_template, list_rule_templates
from atlas.recording import (
    RecordingCreateRequest,
    RecordingUpdateRequest,
    ReplayRequest,
    build_condition_script,
    build_tool_mocks,
    clock_anchor,
    collect_steps,
    collect_subgraph_snapshots,
    compare as compare_recording,
    extract_shadow_events,
    HumanOutcome,
    inline_first_resolver,
    preset_all_approvals,
    preset_all_wait_events,
    preset_approvals,
    preset_wait_events,
    report_to_csv,
    run_release_gate,
    seed_anchor,
)
from atlas.routing import (
    RolloutConfig,
    RolloutError,
    RolloutState,
    TriggerEvent,
    evaluate_after_run,
)
from atlas.shop.service import DemoShopService
from atlas.storage.frame import card_context_from_frame, remaining_seconds
from atlas.storage.memory import ApprovalBroker, FeedbackRequest
from atlas.storage.pg import get_pg_backend
from atlas.storage.retention import run_retention_once
from atlas.storage.recovery import (
    RESOLVE_FRAME_NOT_CLAIMED,
    RESOLVE_FRAME_NOT_FOUND,
    RESOLVE_OK,
    RESOLVE_RUN_NOT_SUSPENDED,
    abandon_claimed_suspended_frame,
    clear_frame,
    clear_tenant_frames,
    interruption_view,
    load_pending_frames,
    make_frame_sink,
    make_resume_claim,
)
from atlas.scheduling.cron import CronExpressionError, next_fire_utc, validate_cron
from atlas.scheduling.engine import (
    ACTION_SKIPPED_OVERLAP,
    TickLedger,
    TickOutcome,
    tick,
)
from atlas.scheduling.models import (
    DEFAULT_SCHEDULE_ACTION,
    REFLECT_SCHEDULE_ID,
    ScheduleAction,
    ScheduleRecord,
    schedule_projection,
    slot_key,
    tz_of,
)
from atlas.scheduling.pg_store import PgScheduleStore
from atlas.scheduling.store import InMemoryScheduleStore, ScheduleStore
from atlas.reflection import run_pass as run_reflection_pass
from atlas.template import get_template, list_templates
from atlas.template.user_store import TemplateVersionConflict
from atlas.web.i18n import localize_template, localize_tool_desc, resolve_locale
from atlas.versioning.publish import publish as publish_graph_version
from atlas.versioning.upgrades import subgraph_upgrade_plan

logger = logging.getLogger(__name__)

# docs/35 §2（T2）：审批挂起邮件中的应用入口（前端地址）。
# 应用对外入口（审批深链基址）：唯一读取器在 security.bootstrap（docs/89 §15 A-6，
# 此前这里与 channels/registry、notifications 各读一遍同一 env＋同一缺省）
_PUBLIC_URL = read_public_url()


def _token_ref(token: Any) -> str:
    """日志里指代一枚令牌的可读引用（docs/89 §15 A-8b）。

    `resume_token`／审批 `token` 都是能直接推进状态的凭证，全文进日志＝任何读得到日志的人
    都能代替持有人续跑或决策；而运维排障又确实需要一个能对上表行的标识，所以留 8 位前缀：
    够定位，不够使用。
    """
    text = str(token or "")
    return text[:8] + "…" if len(text) > 8 else (text or "<空令牌>")

MAX_WAIT_PAYLOAD_BYTES = 4096
MAX_WAIT_PAYLOAD_KEYS = 50
# docs/36 §3：邮件深链验签单例（密钥取 ATLAS_APPROVAL_HMAC_SECRET）。
_email_token_issuer = TokenIssuer()


def _wait_for_database_ready() -> None:
    """docs/79（打包 S）D-4：启动钩子之前先有界等待 PG 连通。

    空卷首启时 PG initdb 之后有一段 recovery 窗口（实测约 90s）会拒绝一切连接，
    而 compose 的 `pg_isready` 探针在这段窗口里判过 healthy。此处等待使恢复扫描与
    retention **不再被静默跳过**；预算耗尽只 warning 不阻断启动——与 recover_pending /
    run_retention_once 的"清扫绝不挡服务起"语义一致（`/api/ready` 仍对 PG 做真 SELECT 1）。
    非 PG 档直接返回（进程内档无跨重启状态可恢复）。
    """
    if STORAGE_BACKEND != "pg":
        return
    try:
        wait_for_database(lambda: ping(get_pg_backend().engine))
    except Exception as exc:  # 预算耗尽：与既有启动钩子同款，不阻断启动
        logger.warning("PG 连通性等待超预算，启动继续（恢复扫描/retention 可能被跳过）：%s", exc)


def recover_pending() -> None:
    """服务启动钩子：读未决挂起帧 → 逐帧重建 pending + 重启续跑线程（docs/24 §2.4）。

    仅 PG 后端生效（进程内后端重启即失、无帧可恢复）；失败帧隔离记日志、不阻塞其余。
    """
    if STORAGE_BACKEND != "pg":
        return
    try:
        engine = get_pg_backend().engine
        frames = load_pending_frames(engine)
    except Exception as exc:  # 无 DATABASE_URL / PG 未就绪时不阻断启动
        logger.warning("中断恢复扫描跳过：%s", exc)
        return
    for frame in frames:
        try:
            _resume_from_frame(engine, frame)
        except Exception as exc:
            logger.warning("帧 %s 恢复失败、隔离跳过：%s", _token_ref(frame.get("resume_token")), exc)


def _resume_from_frame(engine, frame: dict) -> None:
    """按帧重建 pending（approval 重挂 Event + 剩余 deadline），并重启续跑线程。"""
    services = tenant_registry.get(frame["tenant_id"])
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
    _BACKGROUND_WORKER_POOL.submit(_resume_run, engine, services, frame)


def _resume_run(engine, services: TenantServices, frame: dict) -> None:
    """续跑线程：从挂起节点沿边到 END，完成后清帧并落 run 终态（决策 / wait 到点 / 再超时均覆盖）。"""
    run_id = frame.get("run_id", "")
    try:
        resume_graph = GraphDSL.model_validate(frame["graph_snapshot"])
        result = run_graph(
            resume_graph,
            inputs=frame["resume_state"].get("inputs", {}),
            registry=_runtime_registry(services),
            approval_broker=services.approval_broker,
            event_wait_broker=services.event_wait_broker,
            graph_id=frame["resume_state"].get("graph_id", ""),
            graph_resolver=_tenant_graph_resolver(services),
            frame_sink=make_frame_sink(engine, frame["tenant_id"], run_id),
            resume_claim=make_resume_claim(engine),
            resume=frame,
            tenant_id=frame["tenant_id"],
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
        logger.info("续跑让位 %s：%s", _token_ref(frame.get("resume_token")), exc)
    except Exception as exc:
        logger.error("续跑 %s 失败：%s", _token_ref(frame.get("resume_token")), exc)
        if run_id:
            services.run_store.finish(
                run_id=run_id, status="failed", error=f"{type(exc).__name__}: {exc}"
            )


def _frame_sink_for(tenant_id: str, run_store, run_id: str):
    """挂起回调：先落 run 状态为 suspended（所有后端），PG 后端再写 interruptions 帧。"""

    def sink(frame: dict) -> None:
        run_store.suspend(
            run_id=run_id,
            node_id=frame["node_id"],
            kind=frame["kind"],
            resume_token=frame["resume_token"],
            deadline_at=frame.get("deadline_at"),
        )
        if STORAGE_BACKEND == "pg":
            make_frame_sink(get_pg_backend().engine, tenant_id, run_id)(frame)

    return sink


def _resume_claim_for() -> Callable[[str], bool] | None:
    """挂起帧认领门（docs/62 §3.4）：只有 PG 后端有帧可认领，进程内后端返回 None＝恒放行。

    与 `frame_sink` 严格配对注入——凡写帧的运行必带认领门，反之亦然，否则会出现
    "有帧无人认领判定"或"无帧却被判定为已被接管"两种漂移。
    """
    if STORAGE_BACKEND != "pg":
        return None
    return make_resume_claim(get_pg_backend().engine)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # docs/79（打包 S）D-4：先有界等待 PG 连通，再跑恢复扫描与 retention。
    _wait_for_database_ready()
    recover_pending()
    # docs/64 J-2a：启动即打印决策器运行模式（含降级警告），不再静默。
    get_decision_client()
    # docs/65 K-A：启动跑一次 retention 清扫（PG 档；失败只 warning 不阻断启动）。
    run_retention_once()
    # docs/68 §2.3：调度线程在恢复扫描**之后**起——先让挂起帧归位，再派发新运行。
    start_scheduler()
    yield
    stop_scheduler()


# docs/65 K-D：统一日志 formatter（UTC 时间戳/级别/logger/request_id）；幂等。
configure_logging()

# docs/08 打包 ZO：先把 `*_FILE` 指向的密钥补进 env（litellm 自读 env，改不了它的
# 读取点），必须在 import 期装配之前、且紧邻下面的启动门。
hydrate_file_secrets()

# docs/64 J-1a：prod 缺必需密钥 fail-closed（拒绝启动），非 prod 静默。
assert_prod_secrets()

# docs/89 §16 A-11：prod 忘了配 pg 也 fail-closed（缺省内存档不再让进程带着假配置起来）。
assert_prod_storage_backend()

app = FastAPI(title="Atlas API", version="0.0.1", lifespan=lifespan)

# docs/90 ADR T32：A2A 执行 Agent 面（Zeus 协同决策平台，plan-only），卡片公开、任务 Bearer 保护。
app.include_router(a2a_router)

# docs/65 K-D：request-id 中间件（X-Request-Id 响应头 + 日志 request_id 字段）。
install_request_id_middleware(app)

# Demo 单例：控制台页面与编译运行的图共享同一份店铺状态
_demo_shop = DemoShopService()
# Demo 全局基础设施（04 §5.14）：店铺/出向连接/适配器注册不按租户分区；
# 图/录制/反馈/消息/审批/调试/监控每租户一套，由 iam.TenantRegistry 惰性装配。
# 装配逻辑在 atlas.harness.runtime（REST 与 MCP 两个只读面共用一份，防漂移）。
_db_client = resolve_database_client(demo_surface_enabled())
_demo_registry = build_base_registry(demo_surface_enabled(), _db_client, shop=_demo_shop)

_secret_provider = build_secret_provider_from_env()


@app.exception_handler(GraphValidationError)
def graph_validation_handler(_request: Request, exc: GraphValidationError) -> JSONResponse:
    # locations 为稀疏侧车（04 §6.5/06 §6.13）：有可定位条目时才下发，index 对齐 detail。
    # codes/params 与 detail 等长、下标对齐（docs/17 §2.4）：前端按 code 映射本地文案，
    # detail 为中文 message，仅作调试日志/默认兜底，不直接面向最终用户。
    content: dict[str, Any] = {
        "detail": exc.errors,
        "codes": exc.codes,
        "params": exc.params,
    }
    if exc.locations:
        content["locations"] = exc.locations
    return JSONResponse(status_code=422, content=content)


@app.exception_handler(WaitNodeFailure)
def wait_node_failure_handler(_request: Request, exc: WaitNodeFailure) -> JSONResponse:
    # 运行期 wait 确定性失败（docs/47 §3.4）：返回结构化 500 而非让异常逃逸到 ASGI
    # 顶层。docs/54 收口修复：原先异常 re-raise 会令 uvicorn 关闭该 keep-alive 连接，
    # 失败运行后紧邻的列表/详情 GET 复用连接时被 RST（Connection reset by peer）。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "nodeId": exc.node_id,
            }
        },
    )


@app.exception_handler(ConditionEvalError)
def condition_eval_failure_handler(
    _request: Request, exc: ConditionEvalError
) -> JSONResponse:
    # 运行期 condition/loop/foreach 表达式求值失败（docs/60 G1）：与 WaitNodeFailure
    # 同形返回结构化 500，携带 COND_* 机器码与 params，前端可按当前语言渲染；中文
    # message 仍是兜底真相。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "params": exc.params,
            }
        },
    )


@app.exception_handler(AiDecisionUnavailable)
def ai_decision_unavailable_handler(
    _request: Request, exc: AiDecisionUnavailable
) -> JSONResponse:
    # prod 档 ai_decision 拿不到 LLM 决策器（docs/73 W1-1.1）：结构化 500 携带
    # LLM_DECISION_UNAVAILABLE 与 nodeId，与 WaitNodeFailure 同形，前端按语言渲染文案。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "nodeId": exc.node_id,
            }
        },
    )


@app.exception_handler(ConditionClassifierUnavailable)
def condition_classifier_unavailable_handler(
    _request: Request, exc: ConditionClassifierUnavailable
) -> JSONResponse:
    # prod 档 condition(llm) 拿不到 LLM 分类器（docs/73 W5-5.4）：同形结构化 500 携带
    # LLM_CLASSIFIER_UNAVAILABLE 与 nodeId，前端按 LLM_ 前缀渲染双语文案。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "nodeId": exc.node_id,
            }
        },
    )


@app.exception_handler(SubgraphSuspendUnsupported)
def subgraph_suspend_unsupported_handler(
    _request: Request, exc: SubgraphSuspendUnsupported
) -> JSONResponse:
    # 打包 ZK 让 SubgraphSuspendUnsupported 穿透子图 fail-safe、显式拒跑（被吞掉会变
    # 「子图 failed、父 run completed」的假完成）。SSE 通道一直有 `event: error` 帧承载它，
    # 但**同步 /run 此前没有异常处理器**：异常逃出端点 ⇒ Starlette 给一个没有 code 的裸 500，
    # 运营看到的是一句 Internal Server Error，而"把审批/wait 节点移到图顶层"这个可执行
    # 下一步只存在于日志里。补齐成与 LLM_DECISION_UNAVAILABLE 同形的结构化 500。
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "nodeId": exc.node_id,
            }
        },
    )


@app.middleware("http")
async def audit_write_actions(request: Request, call_next):
    """T6 审计中间件（docs/35 §6）：仅 /api/ 写方法在响应后记录元数据（actor/路由模板/
    status/实际 path/IP）；登录路径跳过（端点内仅成功才记）；审计异常只告警、绝不阻断主请求。
    绝不读取请求体或 Authorization 头。"""
    response = await call_next(request)
    try:
        path = request.url.path
        if (
            path.startswith("/api/")
            and request.method in AUDITED_METHODS
            and path != LOGIN_PATH
        ):
            principal = getattr(request.state, "principal", None)
            if principal is not None:
                route = request.scope.get("route")
                template = getattr(route, "path_format", None) or path
                services_for(principal).audit_store.record(
                    tenant_id=principal.tenant_id,
                    actor=principal.username,
                    action=f"{request.method} {template}",
                    status_code=response.status_code,
                    path=path,
                    ip=request.client.host if request.client else "",
                )
    except Exception as exc:  # 审计绝不阻断业务
        logger.warning("audit record failed: %s", exc)
    return response


# docs/77 R3：FastAPI 自带文档面（/docs、/redoc、/openapi.json、oauth2 回调）是**非
# APIRoute**，打包 P 的匿名面 allowlist（只遍历 APIRoute）管不到它们——prod 下匿名者
# 能拿到完整 OpenAPI 形状。这里在 prod 且未开 demo 面时逐条 404（与 demo 面同一开关）。
_OPENAPI_SURFACE = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


@app.middleware("http")
async def gate_openapi_surface(request: Request, call_next):
    if request.url.path in _OPENAPI_SURFACE and not _demo_mock_enabled():
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)


def _tenant_graph_resolver(services: TenantServices):
    """subgraph 节点 graph_resolver（04 §5.7）：在当前租户 GraphStore 内按 id 解析，缺失抛 KeyError。"""

    def resolve(graph_id: str):
        # M6 钉版：subgraph graphId 可为 `graph-7@3`（先按 pin 版本，失败回退 latest 由 get 返回 None 触发 KeyError）
        ref_id, _, version = graph_id.partition("@")
        release_version = int(version) if version else None
        raw = services.graph_store.get(ref_id, release_version)
        if raw is None:
            raise KeyError(graph_id)
        return parse_graph(raw)

    return resolve


def _runtime_registry(services: TenantServices) -> AdapterRegistry:
    """执行期适配器注册表：沿用全局 shop/http/database 适配器实例，
    message 适配器替换为当前租户消息服务（04 §5.14 分区；06 §6.12）。

    装配本体在 atlas.harness.runtime（MCP 的 atlas_list_adapters 锚同一张表）。"""
    return build_runtime_registry(services, _demo_registry, _secret_provider)


class SaveGraphResponse(BaseModel):
    id: str
    version: int


class PublishGraphResponse(BaseModel):
    id: str
    releaseVersion: int


class PublishGraphRequest(BaseModel):
    # M9 发布门禁：true 时先批量回放，blocked 拦截发布（03 `release_gate`）
    gate: bool = False


class CompileResponse(BaseModel):
    id: str
    nodes: list[dict[str, str]]
    edges: list[dict[str, str]]
    entrypoints: list[str]
    terminals: list[str]


class RunGraphResponse(BaseModel):
    id: str
    status: str
    outputs: dict[str, Any]
    trace: list[str]
    # 打包 W（docs/84）：实际执行完成的运行携带种子；cancelled/suspended 早退响应为 None。
    rng_seed: int | None = None


class NLGenerateRequest(BaseModel):
    prompt: str


class DemoLoginRequest(BaseModel):
    username: str
    password: str


@app.get("/api/health")
def health() -> dict[str, str]:
    """存活探针：进程在跑即 200（不检查依赖）。"""
    return {"status": "ok"}


@app.get("/api/ready")
def ready() -> JSONResponse:
    """就绪探针：依赖（PG）可用才 200，否则 503，供 compose healthcheck/反代摘流。"""
    ok, detail = check_ready()
    detail = {"status": "ready" if ok else "not_ready", **detail}
    return JSONResponse(detail, status_code=200 if ok else 503)


def _metrics_auth_gate(request: Request) -> Response | None:
    """docs/65 K-C：`/metrics` prod fail-closed Bearer 闸门。

    非 prod 返回 None（放行，demo/本地拉取行为不变）；prod 下 `ATLAS_METRICS_TOKEN`
    未配置 → 404（fail-closed，与 J-1a 同语义，避免误配置即裸奔）；配置后必须
    `Authorization: Bearer <token>`（hmac.compare_digest 恒定时间），否则 401。
    """
    if read_env_profile() != "prod":
        return None

    token = os.environ.get("ATLAS_METRICS_TOKEN", "")
    if not token:
        logger.warning("ATLAS_METRICS_TOKEN 未配置：/metrics 已 fail-closed 关闭（prod）")
        return Response(status_code=404)
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}"
    if not hmac.compare_digest(auth, expected):
        return Response(status_code=401)
    return None


@app.get("/metrics")
def prometheus_metrics(request: Request) -> Response:
    """Prometheus 文本指标拉取端点（docs/65 K-C：prod 走 Bearer token 闸门）。

    只暴露进程级与按租户聚合的计数/分位数；正式 OTel/Grafana 栈缓做 docs/14 D11。
    """
    gate = _metrics_auth_gate(request)
    if gate is not None:
        return gate
    snapshots: list[tuple[str, dict[str, Any]]] = []
    for tenant_id in tenant_registry.all_tenant_ids():
        try:
            snapshots.append((tenant_id, tenant_registry.get(tenant_id).monitoring.snapshot_metrics()))
        except Exception as exc:  # 指标端点绝不因单租户快照失败而 500
            logger.warning("metrics snapshot failed for %s: %s", tenant_id, exc)
    body = render_prometheus(storage_backend=STORAGE_BACKEND, tenant_snapshots=snapshots)
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


def _audit_bounds(since: str | None, until: str | None) -> tuple[str | None, str | None]:
    """校验 since/until（docs/61 §4.2）：非法 ISO 或 since>until → 422 中文聚合。

    项目没有全局 RequestValidationError 中文处理器，故在端点内手工校验（照静默 PUT 先例）。
    """
    from datetime import timezone

    from atlas.observability.audit import parse_bound

    def _clean(value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    lo, hi = _clean(since), _clean(until)
    errors: list[str] = []
    parsed: dict[str, Any] = {}
    for name, label, raw in (("since", "since（起始时间）", lo), ("until", "until（截止时间）", hi)):
        if raw is None:
            continue
        try:
            parsed[name] = parse_bound(raw).astimezone(timezone.utc)
        except ValueError:
            errors.append(f"{label}不是合法的 ISO-8601 时间：{raw}")
    if not errors and lo and hi and parsed["since"] > parsed["until"]:
        errors.append(f"since 不能晚于 until（{lo} > {hi}）")
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return lo, hi


def _audit_cursor(cursor: int | None) -> int | None:
    if cursor is None:
        return None
    if int(cursor) < 1:
        raise HTTPException(status_code=422, detail=[f"cursor 必须为正整数游标，收到：{cursor}"])
    return int(cursor)


@app.get("/api/audit/events")
def list_audit_events(
    principal: Principal = Depends(require("administer")),
    limit: int = 100,
    action: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    cursor: int | None = None,
) -> dict[str, Any]:
    """写操作审计事件（docs/35 §6 T6；docs/61 §4.2 补过滤与游标分页）：
    倒序（最新在前），admin only，只读本租户；limit 为页大小 clamp 1–500；
    action 路由动作前缀、actor 精确等值、since/until UTC ISO-8601 闭区间、
    cursor 取严格更早的一页（上一页最后一条的 seq）；响应补 nextCursor（无更多则 null）。"""
    bounded = max(1, min(int(limit), 500))
    action_prefix = action.strip() if action and action.strip() else None
    actor_exact = actor.strip() if actor and actor.strip() else None
    lo, hi = _audit_bounds(since, until)
    page = _audit_cursor(cursor)
    items = services_for(principal).audit_store.list(
        limit=bounded,
        action_prefix=action_prefix,
        actor=actor_exact,
        since=lo,
        until=hi,
        cursor=page,
    )
    # 取满一页才可能有下一页；游标即本页最后一条的 seq（不足一页说明到底了）。
    next_cursor = items[-1]["seq"] if len(items) == bounded and items else None
    return {"items": items, "limit": bounded, "nextCursor": next_cursor}


@app.get("/api/audit/export")
def export_audit_events(
    principal: Principal = Depends(require("administer")),
    action: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    fmt: str | None = Query(default="jsonl", alias="format"),
) -> StreamingResponse:
    """导出审计为 JSONL 附件（admin only，正序旧→新）；受同一套过滤，**不受分页影响**
    （无 cursor，导出的是全部匹配行）；v1 仅支持 format=jsonl。"""
    if fmt != "jsonl":
        raise HTTPException(status_code=422, detail="仅支持 format=jsonl")
    action_prefix = action.strip() if action and action.strip() else None
    actor_exact = actor.strip() if actor and actor.strip() else None
    lo, hi = _audit_bounds(since, until)
    body = services_for(principal).audit_store.export_jsonl(
        action_prefix=action_prefix, actor=actor_exact, since=lo, until=hi
    )
    headers = {"Content-Disposition": 'attachment; filename="atlas-audit.jsonl"'}
    return StreamingResponse(
        iter([body.encode("utf-8")]),
        media_type="application/x-ndjson; charset=utf-8",
        headers=headers,
    )


# ================= OAuth2 连接管理（docs/35 §4，T4；generic，平台无关）=================

class ConnectionUpsertRequest(BaseModel):
    provider: str | None = None
    displayName: str | None = None
    authUrl: str | None = None
    tokenUrl: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    scopes: list[str] | None = None
    redirectUri: str | None = None


class ConnectionCreateRequest(ConnectionUpsertRequest):
    provider: str
    displayName: str
    authUrl: str
    tokenUrl: str
    clientId: str


class ConnectionExchangeRequest(BaseModel):
    code: str
    state: str


def _conn_http_error(exc: ConnectionServiceError) -> "HTTPException":
    detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
    if getattr(exc, "params", None):
        detail["params"] = exc.params
    return HTTPException(status_code=exc.status_code, detail=detail)


@app.post("/api/connections", status_code=201)
def create_connection(
    body: ConnectionCreateRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """创建 OAuth2 连接配置（admin only）；授权/token URL 过出向校验，client_secret 立即加密。"""
    try:
        return services_for(principal).connection_service.create(
            body.model_dump(), created_by=principal.username
        )
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@app.get("/api/connections")
def list_connections(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    return {"items": services_for(principal).connection_service.list()}


@app.get("/api/connections/{conn_id}")
def get_connection(conn_id: str, principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    try:
        return services_for(principal).connection_service.get(conn_id)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@app.put("/api/connections/{conn_id}")
def update_connection(
    conn_id: str,
    body: ConnectionUpsertRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """更新连接白名单字段（admin only）；client_secret 未传/空串保留原信封。"""
    try:
        return services_for(principal).connection_service.update(
            conn_id, body.model_dump(exclude_unset=True)
        )
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@app.delete("/api/connections/{conn_id}")
def delete_connection(conn_id: str, principal: Principal = Depends(require("administer"))) -> dict[str, Any]:
    try:
        deleted = services_for(principal).connection_service.delete(conn_id)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)
    return {"deleted": deleted}


# ================= LLM 模型配置管理（docs/93 打包 Y；内置模型＋BYOK）=================

class ModelConfigRequest(BaseModel):
    model: str | None = None
    apiKey: str | None = None  # 明文 key，写入即 AES-GCM 加密；空/未传保留原信封
    baseUrl: str | None = None
    enabled: bool | None = None


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _masked_view(cfg: ModelConfig) -> dict[str, Any]:
    """HTTP 脱敏视图：api_key 回 ``***``＋**明文**尾 4 位（docs/93 §2.3）。

    public_view() 只够在 store 层防御性用（信封尾 4 位无意义）；产品语义要的是
    明文 key 尾 4 位，故在此解密一次。解密失败（密钥轮换/坏信封）只回 ***。
    """
    view = cfg.public_view()
    if cfg.api_key_enc:
        from atlas.connections.service import get_secret_provider

        try:
            plain = get_secret_provider().decrypt(cfg.api_key_enc)
            view["apiKey"] = f"***{plain[-4:]}" if plain else ""
        except Exception:  # 坏信封/密钥不可用：只回占位，绝不泄漏
            view["apiKey"] = "***"
    return view


def _seal_api_key(api_key: str | None, existing: str | None) -> str | None:
    """明文 key → AES-GCM 信封；None/空串保留原信封（照 connection.client_secret 语义）。"""
    if api_key is None or not api_key.strip():
        return existing  # 未传/空 → 不动（前端脱敏回显不会回明文）
    from atlas.connections.service import get_secret_provider

    return get_secret_provider().encrypt(api_key.strip())


def _build_model_config(
    mode: str, raw: ModelConfigRequest, existing: ModelConfig | None,
    *, updated_by: str,
) -> ModelConfig:
    """由请求体构造 ModelConfig；api_key 写即加密，缺省字段沿用现状。"""
    prev = existing
    model = (raw.model or "").strip() or (prev.model if prev else "")
    if not model:
        raise HTTPException(status_code=422, detail="模型名不能为空")
    api_key_enc = _seal_api_key(raw.apiKey, prev.api_key_enc if prev else None)
    return ModelConfig(
        mode=mode,
        model=model,
        api_key_enc=api_key_enc or "",
        base_url=(raw.baseUrl or "").strip() or (prev.base_url if prev else None),
        enabled=raw.enabled if raw.enabled is not None else (prev.enabled if prev else True),
        updated_by=updated_by,
        updated_at=_now_iso_utc(),
    )


def _effective_model_summary(services: TenantServices, tenant_id: str) -> dict[str, Any]:
    """当前生效摘要（docs/93 §2.3 GET /api/models）：BYOK > 内置 > env 兜底。"""
    from atlas.llm.config import resolve_default_model

    store = services.model_config
    cfg = resolve_default_model(store, tenant_id)
    if cfg is not None:
        return {"source": cfg.mode, **_masked_view(cfg)}
    model = os.getenv("LITELLM_MODEL", "").strip()
    if model:
        return {"source": "env", "model": model, "apiKey": "", "baseUrl": None,
                "enabled": True, "mode": "builtin", "updatedBy": "", "updatedAt": ""}
    return {"source": "none", "model": "", "apiKey": "", "baseUrl": None,
            "enabled": False, "mode": "builtin", "updatedBy": "", "updatedAt": ""}


@app.get("/api/models")
def get_models(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    """模型配置总览（read 门可读；api_key 全脱敏）。"""
    services = services_for(principal)
    store = services.model_config
    builtin = store.get_builtin()
    byok = store.get_byok(principal.tenant_id)
    return {
        "builtin": _masked_view(builtin) if builtin else None,
        "byok": _masked_view(byok) if byok else None,
        "effective": _effective_model_summary(services, principal.tenant_id),
    }


@app.put("/api/models/builtin")
def put_builtin_model(
    body: ModelConfigRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """admin 维护内置模型（docs/93 §1：平台级单一配置，所有租户共享）。"""
    services = services_for(principal)
    store = services.model_config
    existing = store.get_builtin()
    cfg = _build_model_config("builtin", body, existing, updated_by=principal.username)
    return _masked_view(store.set_builtin(cfg))


@app.get("/api/models/byok")
def get_byok_model(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    """本租户 BYOK 配置（脱敏）。"""
    services = services_for(principal)
    cfg = services.model_config.get_byok(principal.tenant_id)
    return _masked_view(cfg) if cfg else {"configured": False}


@app.put("/api/models/byok")
def put_byok_model(
    body: ModelConfigRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """租户管理员/operator 维护本租户 BYOK（docs/93 §1 语义约束 2：operate 可配自己的 key）。"""
    services = services_for(principal)
    store = services.model_config
    existing = store.get_byok(principal.tenant_id)
    cfg = _build_model_config("byok", body, existing, updated_by=principal.username)
    return _masked_view(store.set_byok(principal.tenant_id, cfg))


@app.post("/api/connections/{conn_id}/authorize")
def authorize_connection(conn_id: str, principal: Principal = Depends(require("operate"))) -> dict[str, Any]:
    """返回授权 URL 与 state（前端 window.open 打开，state 10 分钟有效，仅防 CSRF）。"""
    try:
        return services_for(principal).connection_service.authorize(conn_id)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@app.post("/api/connections/{conn_id}/exchange")
def exchange_connection(
    conn_id: str,
    body: ConnectionExchangeRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """校验 state→授权码换 token→加密落库；token 失败置 status=error。"""
    try:
        return services_for(principal).connection_service.exchange(conn_id, body.code, body.state)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@app.post("/api/connections/{conn_id}/refresh")
def refresh_connection(conn_id: str, principal: Principal = Depends(require("operate"))) -> dict[str, Any]:
    try:
        return services_for(principal).connection_service.refresh(conn_id)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@app.post("/api/connections/{conn_id}/test")
def test_connection(conn_id: str, principal: Principal = Depends(require("operate"))) -> dict[str, Any]:
    """连接测试：过期先刷新；draft/error 返回 ok=false 与中文原因。generic 框架不调用真实业务 API。"""
    try:
        return services_for(principal).connection_service.test(conn_id)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


class ChannelBindRequest(BaseModel):
    provider: str
    connectionId: str
    config: dict[str, Any] | None = None


def _channel_http_error(exc: ChannelError) -> "HTTPException":
    detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
    if getattr(exc, "params", None):
        detail["params"] = exc.params
    return HTTPException(status_code=exc.status_code, detail=detail)


def _record_audit(
    services: TenantServices, principal: Principal,
    http_request: Request, action: str, status_code: int,
) -> None:
    try:
        services.audit_store.record(
            tenant_id=principal.tenant_id,
            actor=principal.username,
            action=action,
            status_code=status_code,
            path=http_request.url.path,
            ip=http_request.client.host if http_request.client else "",
        )
    except Exception as exc:
        logger.warning("audit record failed: %s", exc)


_record_channel_audit = _record_audit


@app.get("/api/channels")
def list_channels(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    return {"items": services_for(principal).channel_registry.list()}


@app.post("/api/channels", status_code=201)
def bind_channel(
    body: ChannelBindRequest,
    http_request: Request,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    services = services_for(principal)
    try:
        view = services.channel_registry.bind(
            body.provider, body.connectionId, body.config,
            created_by=principal.username,
        )
    except ChannelError as exc:
        _record_channel_audit(services, principal, http_request, "channel.bind", exc.status_code)
        raise _channel_http_error(exc)
    _record_channel_audit(services, principal, http_request, "channel.bind", 201)
    return view


@app.get("/api/channels/{binding_id}")
def get_channel(binding_id: str, principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    try:
        return services_for(principal).channel_registry.get(binding_id)
    except ChannelError as exc:
        raise _channel_http_error(exc)


@app.post("/api/channels/{binding_id}/test")
def test_channel(
    binding_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """连通性测试：真出向＋写 binding.status，故与兄弟端点 `/api/connections/{id}/test` 同档（operate）。

    docs/89 §15 A-3：此前挂在 `read` 档——一个只读角色既能让平台对外发起请求，又能把绑定
    状态改成 connected/error，是与 RBAC 白名单相违的写面。
    """
    # 上游错误不 5xx：200 体 ok=false（docs/38 §1C）
    return services_for(principal).channel_registry.test(binding_id)


@app.delete("/api/channels/{binding_id}")
def delete_channel(
    binding_id: str,
    http_request: Request,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    services = services_for(principal)
    try:
        deleted = services.channel_registry.delete(binding_id)
    except ChannelError as exc:
        _record_channel_audit(services, principal, http_request, "channel.unbind", exc.status_code)
        raise _channel_http_error(exc)
    _record_channel_audit(services, principal, http_request, "channel.unbind", 200)
    return {"deleted": deleted}


# --- 入站 Webhook（docs/39，ADR T29）--------------------------------------

MAX_WEBHOOK_SUBSCRIPTIONS = 10
MAX_WEBHOOK_BODY_BYTES = 1024 * 1024

# J-3d：后台出向（webhook 触发 / wait·approval 续跑）走共享线程池，防止每请求裸开线程。
_BACKGROUND_WORKER_POOL = ThreadPoolExecutor(max_workers=8)


class WebhookSubscriptionsRequest(BaseModel):
    subscriptions: list[dict[str, Any]]


def _locate_binding(binding_id: str) -> tuple[str, ChannelBinding, TenantServices] | None:
    """公开路径的跨租户绑定定位：绝不因探测而惰性创建租户。"""
    if STORAGE_BACKEND == "pg":
        engine = get_pg_backend().engine
        with engine.connect() as db:
            row = db.execute(
                text("SELECT tenant_id FROM channel_bindings WHERE id = :id"),
                {"id": binding_id},
            ).first()
        if row is None:
            return None
        tenant_id = row[0]
        services = tenant_registry.get(tenant_id)
        binding = services.channel_registry._store.get(binding_id)
        if binding is None:
            return None
        return tenant_id, binding, services
    for tenant_id in tenant_registry.all_tenant_ids():
        services = tenant_registry.peek(tenant_id)
        if services is None:
            continue
        binding = services.channel_registry._store.get(binding_id)
        if binding is not None:
            return tenant_id, binding, services
    return None


def _webhook_resolve(graph_id: str, tenant: str, event: TriggerEvent) -> int | None:
    services = tenant_registry.get(tenant)
    version, _segment = services.routing_store.resolve(graph_id, tenant=tenant, event=event)
    return version


def _webhook_trigger(graph_id: str, version: int, event: TriggerEvent, tenant: str) -> None:
    services = tenant_registry.get(tenant)
    _BACKGROUND_WORKER_POOL.submit(
        _background_run_worker, services, tenant, graph_id, version, event
    )


def _webhook_audit(action: str, tenant: str, metadata: dict) -> None:
    services = tenant_registry.get(tenant)
    # AuditStore 无 metadata 列：关键事实编码进 path（不含 body/密钥）。
    path = (
        f"webhook binding={metadata.get('bindingId')} "
        f"id={metadata.get('webhookId')} shop={metadata.get('shop')}"
    )
    services.audit_store.record(
        tenant_id=tenant, actor="shopify-webhook", action=action,
        status_code=200, path=path, ip="",
    )


_webhook_deliverer = WebhookDeliverer(
    resolver=_webhook_resolve,
    trigger=_webhook_trigger,
    auditor=_webhook_audit,
    store_provider=lambda tenant: tenant_registry.get(tenant).webhook_deliveries,
)


def _background_run_worker(
    services: TenantServices, tenant_id: str,
    graph_id: str, version: int, event: TriggerEvent,
    *, mode: str = "webhook", run_id: str | None = None,
) -> None:
    """后台运行钉版图：run 记录独立于 HTTP 请求；失败落 failed，不回传触发方。

    webhook 与定时触发共用这条路径（docs/68 §1 D-3：派发按发布号钉版，绝不回落草稿）。
    `run_id` 由调度派发方预先 begin 后传入——它必须在 tick 内就让 run 变成 running，
    否则下一轮 tick 的"同图还在跑"判定会漏看这条刚提交的运行。
    """
    run_store = services.run_store
    if run_id is None:
        run_id = uuid.uuid4().hex
        run_store.begin(run_id=run_id, graph_id=graph_id, mode=mode)
    try:
        raw = services.graph_store.get(graph_id, version)
        if raw is None:
            run_store.finish(
                run_id=run_id, status="failed",
                error=f"已发布版本不存在：{graph_id}@{version}",
            )
            return
        graph = parse_graph(raw)
        result = run_graph(
            graph,
            inputs={"event": event.model_dump()},
            registry=_runtime_registry(services),
            approval_broker=services.approval_broker,
            event_wait_broker=services.event_wait_broker,
            graph_id=graph_id,
            graph_resolver=_tenant_graph_resolver(services),
            frame_sink=_frame_sink_for(tenant_id, run_store, run_id),
            resume_claim=_resume_claim_for(),
            _block_subgraph_suspend=(STORAGE_BACKEND == "pg"),
            graph_version=version,
            tenant_id=tenant_id,
        )
        run_store.finish(
            run_id=run_id, status="completed",
            outputs=result["outputs"], trace=result["trace"],
        )
    except RunSuperseded as exc:
        # docs/62 §2 D-4：后台运行同样受认领门约束——输家不落 failed
        # （否则把赢家的执行记录成失败），只让位并留日志。
        logger.info("后台运行让位 graph=%s@%s：%s", graph_id, version, exc)
    except Exception as exc:
        logger.error("%s 触发图运行失败 graph=%s@%s", mode, graph_id, version, exc_info=True)
        run_store.finish(
            run_id=run_id, status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )


# --- 定时触发调度器（docs/68，打包 N／ADR T30） ---------------------------

MIN_SCHEDULE_TICK_SECONDS = 1
MAX_SCHEDULE_TICK_SECONDS = 300
# 重叠判定要扫的在途运行条数上限：超过就会漏判（后果只是同图并发一次，派发互斥仍由
# 认领表守着），扫全表换不来什么。
BUSY_SCAN_LIMIT = 200


def _schedule_tick_seconds() -> int:
    raw = os.getenv("ATLAS_SCHEDULE_TICK_SECONDS", "").strip()
    if not raw:
        return 30
    try:
        value = int(raw)
    except ValueError:
        logger.warning("ATLAS_SCHEDULE_TICK_SECONDS 非整数（%r），按默认 30 秒", raw)
        return 30
    bounded = max(MIN_SCHEDULE_TICK_SECONDS, min(MAX_SCHEDULE_TICK_SECONDS, value))
    if bounded != value:
        logger.warning(
            "ATLAS_SCHEDULE_TICK_SECONDS %s 超出 %s-%s，按 %s 秒",
            raw, MIN_SCHEDULE_TICK_SECONDS, MAX_SCHEDULE_TICK_SECONDS, bounded,
        )
    return bounded


def _schedule_enabled() -> bool:
    return os.getenv("ATLAS_SCHEDULE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


_schedule_store: ScheduleStore | None = None
_schedule_store_lock = threading.Lock()
_scheduler_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None


def schedule_store() -> ScheduleStore:
    """全局调度 store（惰性）：不按租户装配的理由见 `scheduling/store.py` 模块头。"""
    global _schedule_store
    with _schedule_store_lock:
        if _schedule_store is None:
            _schedule_store = (
                PgScheduleStore(get_pg_backend().engine)
                if STORAGE_BACKEND == "pg"
                else InMemoryScheduleStore()
            )
        return _schedule_store


def _start_pinned_run(
    services: TenantServices, tenant_id: str, graph_id: str, version: int, payload: dict
) -> str:
    """按钉版起一次后台运行，返回 runId。

    `begin` 在这里**同步**做完再交给线程池：调度 tick 必须立刻看得见这条 running，
    否则同一张图会在下一轮（30 秒后）被"同图还在跑"判定放过去。
    """
    run_id = uuid.uuid4().hex
    services.run_store.begin(run_id=run_id, graph_id=graph_id, mode="schedule")
    event = TriggerEvent(channel="api", payload=payload)
    _BACKGROUND_WORKER_POOL.submit(
        _background_run_worker,
        services, tenant_id, graph_id, version, event,
        mode="schedule", run_id=run_id,
    )
    return run_id


def _schedule_claim(record: ScheduleRecord, slot: datetime) -> bool:
    return schedule_store().claim(
        record.tenant_id, record.graph_id, record.schedule_id, slot, record.action
    )


def _schedule_is_busy(record: ScheduleRecord) -> bool:
    """同图是否还有 running/suspended 运行（docs/68 §1 D-6，保守按图粒度判）。

    反思项**不受此限**：它是一次只读计算（读既有投影、不写图、不产运行），与在途运行
    不争用任何东西；反过来拿"图在跑"挡住反思，会让繁忙的图永远得不到反思（docs/88 §3 P-4）。
    """
    if record.action == "reflect":
        return False
    services = tenant_registry.get(record.tenant_id)
    for status in ("running", "suspended"):
        for run in services.run_store.list(status=status, limit=BUSY_SCAN_LIMIT):
            if run.get("graphId") == record.graph_id:
                return True
    return False


def _schedule_dispatch(record: ScheduleRecord, slot: datetime) -> None:
    """派发一次定时动作；跑图的事件载荷带槽位，图里可用 `{{global.event.payload.slot}}` 引用。

    `reflect` 项共用调度载体，只共用**槽位与认领**：它不写 run_store、不发 TriggerEvent
    ——反思不是"跑一次图"，把它塞进运行态会让监控里多出一批没有业务含义的 run（docs/88 §3 P-4）。
    """
    services = tenant_registry.get(record.tenant_id)
    key = slot_key(slot)
    if record.action == "reflect":
        report = run_reflection_pass(
            services, services.reflection_store,
            tenant_id=record.tenant_id, graph_id=record.graph_id, base_version=record.version,
        )
        schedule_store().note_fired(
            record.tenant_id, record.graph_id, record.schedule_id, slot
        )
        services.audit_store.record(
            tenant_id=record.tenant_id, actor="scheduler", action="schedule.fire",
            status_code=200,
            path=(f"action=reflect graph={record.graph_id}@{record.version} "
                  f"slot={key} status={report.status}"),
            ip="",
        )
        return
    _start_pinned_run(
        services, record.tenant_id, record.graph_id, record.version,
        {"source": "schedule", "slot": key, "cron": record.cron},
    )
    schedule_store().note_fired(
        record.tenant_id, record.graph_id, record.schedule_id, slot
    )
    services.audit_store.record(
        tenant_id=record.tenant_id, actor="scheduler", action="schedule.fire",
        status_code=200, path=f"graph={record.graph_id}@{record.version} slot={key}", ip="",
    )


def run_schedule_tick(ledger: TickLedger | None = None) -> list[TickOutcome]:
    """跑一轮调度评估：引擎只判定，写库与跳过计数都在这里（`scheduling/engine.py` 零 IO）。"""
    store = schedule_store()
    outcomes = tick(
        datetime.now(timezone.utc),
        store.list_all(),
        _schedule_claim,
        _schedule_dispatch,
        busy=_schedule_is_busy,
        ledger=ledger,
    )
    for outcome in outcomes:
        if outcome.action == ACTION_SKIPPED_OVERLAP:
            store.note_skipped(
                outcome.tenant_id, outcome.graph_id, outcome.schedule_id, outcome.slot_utc
            )
    return outcomes


def _scheduler_loop() -> None:
    ledger = TickLedger()
    interval = _schedule_tick_seconds()
    while not _scheduler_stop.is_set():
        try:
            run_schedule_tick(ledger)
        except Exception:  # noqa: BLE001 — 一轮调度的异常不能带走进程
            logger.exception("调度 tick 异常，下一轮继续")
        _scheduler_stop.wait(interval)


def start_scheduler() -> None:
    """在 lifespan 里、`recover_pending()` 之后起线程（docs/68 §2.3 的启动顺序）。"""
    global _scheduler_thread
    if _scheduler_thread is not None:
        return
    if not _schedule_enabled():
        logger.info("定时触发调度器已由 ATLAS_SCHEDULE_ENABLED 关闭")
        return
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="atlas-scheduler", daemon=True
    )
    _scheduler_thread.start()
    logger.info(
        "定时触发调度器已启动：每 %s 秒评估一轮（UTC 槽位、不补跑；单副本约束不变）",
        _schedule_tick_seconds(),
    )


def stop_scheduler(timeout_seconds: float = 2.0) -> None:
    global _scheduler_thread
    if _scheduler_thread is None:
        return
    _scheduler_stop.set()
    _scheduler_thread.join(timeout=timeout_seconds)
    if _scheduler_thread.is_alive():
        # 宁可留着这个引用：清成 None 会让下一次 start_scheduler 再起**第二条** tick 循环。
        # 两条循环不会双发（派发权在认领表），但会双份扫库与双份日志，排查时说不清是谁。
        logger.warning("调度线程未在 %ss 内退出，保留引用避免起第二条", timeout_seconds)
        return
    _scheduler_thread = None


def _schedule_specs_of_published(
    graph_raw: dict,
) -> list[tuple[str, str, str, int, str]]:
    """已发布版本里**全部**定时触发节点的调度形状（打包 ZN，D41 ③；发布派生）。

    返回每条 `(schedule_id, cron, timezone, catch_up_minutes, overlap_policy)`——
    schedule_id 即该定时触发**节点 id**（随图钉版、稳定），一图挂 N 个定时节点就返回 N 条。
    节点 id 缺失或以保留前缀 `__` 开头（撞 reflect／存量回填命名空间）时日志点名跳过：
    静默建一条会与保留行主键冲突，阻断发布又把极端命名问题变成发布事故，二者都不取。
    """
    found: list[tuple[str, str, str, int, str]] = []
    for node in graph_raw.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "trigger":
            continue
        config = node.get("config") or {}
        if config.get("triggerType") in ("schedule", "cron") and config.get("cron"):
            node_id = str(node.get("id", ""))
            if not node_id or node_id.startswith("__"):
                logger.warning(
                    "定时触发节点 id 缺失或撞保留命名空间，跳过该调度：%r", node_id
                )
                continue
            catch_up = config.get("catchUpMinutes", 0)
            try:
                catch_up = max(0, int(catch_up))
            except (TypeError, ValueError):
                catch_up = 0
            overlap = config.get("overlapPolicy", "skip")
            overlap = overlap if overlap in ("skip", "allow") else "skip"
            found.append((
                node_id, str(config["cron"]),
                str(config.get("timezone", "UTC")), catch_up, overlap,
            ))
    return found


def _derive_schedule_on_publish(
    services: TenantServices, tenant_id: str, graph_id: str, version: int
) -> None:
    """发布出口：按图内定时节点**全量 reconcile** 调度注册——发布是版本变化的唯一时刻。

    （打包 ZN，D41 ③④）每个定时触发节点派生一条 `run`（schedule_id＝节点 id，各自
    cron/tz/catch_up/overlap_policy）；另派生**一条**图级 `reflect`（`__reflect__`，反思是
    整图 pass，不随节点倍增，节奏跟第一个定时节点，overlap 固定 skip）。本次派生集合之外、
    库里本图仍存在的 schedule_id（节点被删）一律撤销——替代旧「只取第一个、其余 warning」。
    """
    raw = services.graph_store.get(graph_id, version)
    if raw is None:
        return
    specs = _schedule_specs_of_published(raw)
    store = schedule_store()
    if not specs:
        store.remove(tenant_id, graph_id)
        return
    desired: set[str] = set()
    for schedule_id, cron, tz_name, catch_up, overlap in specs:
        desired.add(schedule_id)
        store.upsert_published(
            tenant_id=tenant_id, graph_id=graph_id, schedule_id=schedule_id,
            version=version, cron=cron, action="run", timezone=tz_name,
            catch_up_minutes=catch_up, overlap_policy=overlap,
        )
        logger.info(
            "定时跑图调度已登记：graph=%s schedule=%s@%s（cron=%s，tz=%s，catch_up=%s，overlap=%s）",
            graph_id, schedule_id, version, cron, tz_name, catch_up, overlap,
        )
    # 图级反思一条：节奏（cron/tz/catch_up）跟第一个定时节点。
    _, cron0, tz0, catch0, _ = specs[0]
    desired.add(REFLECT_SCHEDULE_ID)
    store.upsert_published(
        tenant_id=tenant_id, graph_id=graph_id, schedule_id=REFLECT_SCHEDULE_ID,
        version=version, cron=cron0, action="reflect", timezone=tz0,
        catch_up_minutes=catch0, overlap_policy="skip",
    )
    logger.info(
        "定时反思调度已登记：graph=%s schedule=%s@%s（cron=%s，tz=%s）",
        graph_id, REFLECT_SCHEDULE_ID, version, cron0, tz0,
    )
    # reconcile：撤销本次不再存在的调度（删掉的定时节点）。
    existing = {
        row.schedule_id
        for row in store.list_tenant(tenant_id) if row.graph_id == graph_id
    }
    for stale in sorted(existing - desired):
        store.remove_schedule(tenant_id, graph_id, stale)
        logger.info("定时调度已撤销：graph=%s schedule=%s", graph_id, stale)


class ScheduleEnabledRequest(BaseModel):
    enabled: bool
    schedule_id: str


class RunNowRequest(BaseModel):
    """run-now 的可选 body（打包 ZN）：`scheduleId` 指定跑哪条调度；缺省（null／无 body）
    ＝该图第一条 run 调度（v1 一图一调度时即主调度，逐字等价旧的无 body 行为）。"""

    schedule_id: str | None = None


def _no_schedule_detail(graph_id: str, schedule_id: str | None = None) -> str:
    label = f"（schedule={schedule_id}）" if schedule_id else ""
    return (
        f"图 {graph_id} 没有定时调度{label}：只有含定时触发节点的**已发布**版本才会登记"
    )


def _first_run_schedule(tenant_id: str, graph_id: str) -> ScheduleRecord | None:
    """该图第一条 run 调度（run-now 缺省目标）；reflect 不计入。"""
    for record in schedule_store().list_tenant(tenant_id):
        if record.graph_id == graph_id and record.action == "run":
            return record
    return None


@app.get("/api/schedules")
def list_schedules(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    """本租户的调度登记（含下次触发时刻，UTC 口径）；纯只读，不改任何状态。

    docs/77 R7 enrich：每条 schedule 附带最近一次 run 的状态（handler 层查 run_store，
    零迁移、零 store 结构改动；run 只按 graph_id 匹配，不区分 schedule/manual mode）。
    """
    now = datetime.now(timezone.utc)
    records = schedule_store().list_tenant(principal.tenant_id)
    # 一次性拉所有 run 按 graph_id 分组，避免 N+1
    services = tenant_registry.get(principal.tenant_id)
    all_runs = services.run_store.list(status=None, limit=200)
    runs_by_graph: dict[str, list[dict]] = {}
    for run in all_runs:
        gid = run.get("graphId", "")
        runs_by_graph.setdefault(gid, []).append(run)
    items: list[dict[str, Any]] = []
    for record in records:
        proj = schedule_projection(record, now)
        graph_runs = runs_by_graph.get(record.graph_id, [])
        # 反思项不挂 lastRun*：反思 pass 不产 run（它不写 run_store），硬挂一条同图的跑图
        # run 会让人以为"这条反思调度跑出过那个运行"（docs/88 §3 P-4）。
        if graph_runs and record.action != "reflect":
            latest = graph_runs[0]  # list 已按 startedAt DESC，第一条即最近
            proj["lastRunId"] = latest.get("runId")
            proj["lastRunStatus"] = latest.get("status")
            proj["lastRunStartedAt"] = latest.get("startedAt")
        items.append(proj)
    return {"items": items}


@app.post("/api/schedules/{graph_id}/enabled")
def set_schedule_enabled(
    http_request: Request,
    graph_id: str,
    request: ScheduleEnabledRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """开/关一条调度（跨重启保留）。关只停未来触发，不取消在途运行（docs/68 §6.5）。"""
    services = services_for(principal)
    record = schedule_store().set_enabled(
        principal.tenant_id, graph_id, request.schedule_id, request.enabled
    )
    if record is None:
        raise HTTPException(
            status_code=404, detail=_no_schedule_detail(graph_id, request.schedule_id)
        )
    _record_audit(
        services, principal, http_request,
        "schedule.enable" if request.enabled else "schedule.disable", 200,
    )
    return schedule_projection(record, datetime.now(timezone.utc))


@app.post("/api/schedules/{graph_id}/run-now")
def run_schedule_now(
    http_request: Request,
    graph_id: str,
    request: RunNowRequest | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """按钉版立刻跑一次（联调与验收入口）：**不写认领表、不占槽位**（docs/68 §2.4）。

    打包 ZN 起 body 用 `scheduleId` 指定调度；要跑反思传 `{"scheduleId": "__reflect__"}`
    （返回收尾报告摘要，`candidateId` 可为 null）。反思项**不做 busy 判定**——它不产运行、
    不与在途运行争用。缺省 body（null／无）跑该图第一条 run 调度。
    """
    services = services_for(principal)
    store = schedule_store()
    sid = request.schedule_id if request is not None else None
    record = (
        store.get(principal.tenant_id, graph_id, sid)
        if sid is not None
        else _first_run_schedule(principal.tenant_id, graph_id)
    )
    if record is None:
        raise HTTPException(status_code=404, detail=_no_schedule_detail(graph_id, sid))
    sid = record.schedule_id
    now = datetime.now(timezone.utc)
    if record.action == "reflect":
        report = run_reflection_pass(
            services, services.reflection_store,
            tenant_id=principal.tenant_id, graph_id=graph_id, base_version=record.version,
        )
        # 同 run-now 的口径：记"最近一次真实执行"，不是槽位认领（不占槽）。
        store.note_fired(principal.tenant_id, graph_id, sid, now)
        _record_audit(services, principal, http_request, "schedule.run_now", 200)
        return {
            "candidateId": report.candidate_id,
            "graphId": graph_id,
            "baseVersion": report.base_version,
            "status": report.status,
        }
    if _schedule_is_busy(record):
        raise HTTPException(
            status_code=409,
            detail=f"图 {graph_id} 仍有运行在执行中（running/suspended），本次立即运行已跳过",
        )
    run_id = _start_pinned_run(
        services, principal.tenant_id, record.graph_id, record.version,
        {"source": "schedule-run-now", "slot": slot_key(now), "cron": record.cron},
    )
    # 记的是"最近一次真实运行"，不是槽位认领：run-now 不占槽，但运营要看得见它跑过。
    store.note_fired(principal.tenant_id, graph_id, sid, now)
    _record_audit(services, principal, http_request, "schedule.run_now", 200)
    return {
        "runId": run_id, "graphId": record.graph_id, "version": record.version,
        "scheduleId": sid,
    }


@app.get("/api/reflection/reports")
def list_reflection_reports(
    graph_id: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """本租户反思 pass 的收尾记录（进程内 ring，新→旧；docs/88 §4／docs/12）。

    **纯只读**：反思无自动 apply／publish／promote，候选落草稿版本后由人走既有流程采纳。
    `limit` 非整数由 FastAPI 挡成 422，越界由 store 侧 clamp 到 1–200（不报错）。
    """
    services = services_for(principal)
    return {"items": services.reflection_store.list_reports(graph_id=graph_id, limit=limit)}


@app.get("/api/reflection/candidates/{candidate_id}")
def get_reflection_candidate(
    candidate_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """单个反思候选详情；不存在或跨租户统一 404（候选表本就每租户一份，跨租户天然取不到）。"""
    services = services_for(principal)
    candidate = services.reflection_store.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"反思候选不存在：{candidate_id}")
    return candidate


class ReflectionDecisionRequest(BaseModel):
    # 打包 ZU（docs/94 E-4）：候选级人工处理标记；Literal 非法值 FastAPI 自动 422。
    status: Literal["adopted", "dismissed"]


@app.put("/api/reflection/candidates/{candidate_id}/decision")
def put_reflection_decision(
    http_request: Request,
    candidate_id: str,
    body: ReflectionDecisionRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """登记/改判候选的人工处理标记（adopted/dismissed；docs/94 E-1/E-2/E-4）。

    **只记处理标记**：不触发任何 apply/publish/promote，采纳后的改图仍走既有人工流程
    （守 T22 唯一放量路径）。允许覆盖改判；候选不存在/跨租户统一 404；read 角色 403。
    """
    services = services_for(principal)
    store = services.reflection_store
    if not store.record_decision(candidate_id, body.status):
        raise HTTPException(status_code=404, detail=f"反思候选不存在：{candidate_id}")
    candidate = store.get_candidate(candidate_id)
    _record_audit(
        services, principal, http_request,
        f"reflection.decide.{body.status}", 200,
    )
    return candidate if candidate is not None else {}


class CronPreviewRequest(BaseModel):
    cron: str = ""
    timeZone: str = "UTC"


# 预览只数到第 3 个槽：再多没有排障意义，也防止逐分钟扫描变慢。
MAX_CRON_PREVIEW_HITS = 3


@app.post("/api/schedules/cron-preview")
def preview_schedule_cron(
    request: CronPreviewRequest, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """cron 预演：这个表达式合不合法、接下来三个 UTC 槽位是几点（不落库、不占槽位）。

    刻意**不在前端复刻一个 cron 解析器**：两份实现对"日与周取并集""7＝周日"这类边角
    迟早分叉，分叉的表现就是画布里看着绿、保存时才 422。合法与非法都回 200——请求本身
    格式正确，"表达式不合法"是答案不是错误（与保存期 422 的分工：这里只预演，不拦保存）。
    """
    cron = request.cron.strip()
    if not cron:
        raise HTTPException(status_code=422, detail="请先填写 Cron 表达式（5 个字段：分 时 日 月 周）")
    cursor = datetime.now(timezone.utc)
    tz = tz_of(request.timeZone)  # 非法 tz 在保存期被 DSL 拦，这里回落 UTC 只做预演
    try:
        spec = validate_cron(cron, now=cursor, tz=tz)
    except CronExpressionError as exc:
        return {"valid": False, "message": str(exc), "nextFireAt": [], "timeZone": request.timeZone}
    hits: list[str] = []
    while len(hits) < MAX_CRON_PREVIEW_HITS:
        upcoming = next_fire_utc(spec, cursor, tz=tz)
        if upcoming is None:
            break
        hits.append(upcoming.isoformat())
        cursor = upcoming
    return {"valid": True, "message": "", "nextFireAt": hits, "timeZone": request.timeZone}


@app.post("/api/channels/hooks/shopify/{binding_id}")
async def shopify_webhook_ingress(binding_id: str, http_request: Request) -> dict[str, Any]:
    """Shopify 公开入站：先跨租户定位绑定，再在原始 body 上 HMAC 验签，最后才解析 JSON。

    验签通过后一律 200（received/duplicate/ignored）；绑定不存在统一 404、
    签名失败统一 401（不泄漏原因差异）；密钥不可用 503；body/头非法 400。
    """
    located = await asyncio.to_thread(_locate_binding, binding_id)
    if located is None:
        raise HTTPException(status_code=404, detail="渠道绑定不存在")
    tenant_id, binding, services = located
    secret = services.connection_service.client_secret_for(binding.connection_id)
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook 验签密钥暂不可用")
    raw = await http_request.body()
    if len(raw) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook 请求体超过 1MB 上限")
    signature = http_request.headers.get(HMAC_HEADER)
    if not verify_shopify_hmac(raw, signature, secret):
        raise HTTPException(status_code=401, detail="Webhook 签名校验失败")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Webhook 请求体不是合法 JSON 对象")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Webhook 请求体必须是 JSON 对象")
    try:
        envelope = build_envelope(http_request.headers, data)
    except ChannelError as exc:
        raise _channel_http_error(exc)
    return _webhook_deliverer.deliver(
        tenant_id,
        {"id": binding.id, "webhook_subscriptions": binding.webhook_subscriptions},
        envelope,
    )


@app.get("/api/channels/{binding_id}/webhooks")
def get_webhook_subscriptions(
    binding_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    try:
        items = services_for(principal).channel_registry.subscriptions(binding_id)
    except ChannelError as exc:
        raise _channel_http_error(exc)
    return {"items": items}


@app.put("/api/channels/{binding_id}/webhooks")
def put_webhook_subscriptions(
    binding_id: str,
    body: WebhookSubscriptionsRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    services = services_for(principal)
    try:
        services.channel_registry._require(binding_id)
    except ChannelError as exc:
        raise _channel_http_error(exc)

    subs = body.subscriptions
    errors: list[str] = []
    if not isinstance(subs, list):
        errors.append("订阅必须是数组")
    elif len(subs) > MAX_WEBHOOK_SUBSCRIPTIONS:
        errors.append(f"订阅数量不能超过 {MAX_WEBHOOK_SUBSCRIPTIONS} 条")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(subs if isinstance(subs, list) else []):
        if not isinstance(item, dict):
            errors.append(f"第 {index + 1} 条订阅格式非法")
            continue
        topic = item.get("topic")
        graph_id = item.get("graphId")
        enabled = item.get("enabled", True)
        prefix = f"第 {index + 1} 条订阅"
        if topic not in SUPPORTED_TOPICS:
            errors.append(f"{prefix}：不支持的 Webhook topic：{topic}")
        if not isinstance(graph_id, str) or not graph_id.strip():
            errors.append(f"{prefix}：缺少 graphId")
        elif services.graph_store.get(graph_id.strip()) is None:
            errors.append(f"{prefix}：订阅的图不存在：{graph_id}")
        if not isinstance(enabled, bool):
            errors.append(f"{prefix}：enabled 必须是布尔值")
        if isinstance(topic, str) and isinstance(graph_id, str):
            key = (topic, graph_id.strip())
            if key in seen:
                errors.append(f"{prefix}：同一 topic 与图的订阅重复：{topic} → {graph_id}")
            seen.add(key)
        if isinstance(graph_id, str):
            graph_id = graph_id.strip()
        normalized.append({"topic": topic, "graph_id": graph_id, "enabled": enabled})
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    items = services.channel_registry.set_subscriptions(binding_id, normalized)
    return {"items": items}


@app.get("/api/channels/webhooks/dead-letters")
def list_webhook_dead_letters(
    topic: str | None = None,
    bindingId: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    store = services_for(principal).webhook_deliveries
    items = store.list_dead(
        principal.tenant_id, topic=topic, binding_id=bindingId, limit=limit
    )
    return {"items": items}


@app.post("/api/channels/webhooks/dead-letters/{webhook_id}/replay")
def replay_webhook_dead_letter(
    webhook_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    services = services_for(principal)
    store = services.webhook_deliveries
    dead = store.get_dead(principal.tenant_id, webhook_id)
    if dead is None:
        raise HTTPException(status_code=404, detail="死信投递不存在或已处理")
    binding = services.channel_registry._store.get(dead["bindingId"])
    if binding is None:
        raise HTTPException(status_code=404, detail="死信所属的渠道绑定不存在")
    result = _webhook_deliverer.replay(
        principal.tenant_id,
        {"id": binding.id, "webhook_subscriptions": binding.webhook_subscriptions},
        webhook_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="死信投递不存在或已处理")
    return result


@app.delete("/api/channels/webhooks/dead-letters/{webhook_id}")
def delete_webhook_dead_letter(
    webhook_id: str, principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    store = services_for(principal).webhook_deliveries
    deleted = store.delete(principal.tenant_id, webhook_id)
    return {"deleted": deleted}


@app.get("/api/channels/webhooks/metrics")
def webhook_delivery_metrics(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    store = services_for(principal).webhook_deliveries
    return store.metrics(principal.tenant_id)


# --- Shopify 店铺侧 webhook 注册（docs/41） --------------------------------


class RemoteWebhookRequest(BaseModel):
    topic: str


@app.get("/api/channels/{binding_id}/remote-webhooks")
def list_remote_webhooks(
    binding_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """渠道令牌失效不污染端点契约：200 空 items + error（binding error 态已由 registry 落库）。"""
    registry = services_for(principal).channel_registry
    try:
        items = registry.remote_webhooks(binding_id)
    except ChannelError as exc:
        if exc.code in CHANNEL_AUTHORIZATION_ERROR_CODES:
            return {"items": [], "error": CHANNEL_UPSTREAM_UNAUTHORIZED}
        raise _channel_http_error(exc)
    return {"items": items}


@app.post("/api/channels/{binding_id}/remote-webhooks", status_code=201)
def register_remote_webhook(
    binding_id: str,
    body: RemoteWebhookRequest,
    http_request: Request,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    services = services_for(principal)
    try:
        item = services.channel_registry.register_remote(binding_id, body.topic)
    except ChannelError as exc:
        _record_channel_audit(
            services, principal, http_request, "channel.webhook.register",
            exc.status_code,
        )
        raise _channel_http_error(exc)
    _record_channel_audit(services, principal, http_request, "channel.webhook.register", 201)
    return item


@app.delete("/api/channels/{binding_id}/remote-webhooks/{topic:path}")
def unregister_remote_webhook(
    binding_id: str,
    topic: str,
    http_request: Request,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """幂等：未注册返回 {deleted:false}。"""
    services = services_for(principal)
    try:
        deleted = services.channel_registry.unregister_remote(binding_id, topic)
    except ChannelError as exc:
        _record_channel_audit(
            services, principal, http_request, "channel.webhook.unregister",
            exc.status_code,
        )
        raise _channel_http_error(exc)
    _record_channel_audit(
        services, principal, http_request, "channel.webhook.unregister", 200
    )
    return {"deleted": deleted}


@app.get("/connections/callback", response_class=HTMLResponse, include_in_schema=False)
def oauth_callback_page() -> HTMLResponse:
    """OAuth provider 回调落地页（无鉴权、无服务端副作用、不发 token）：展示 code/state 并引导
    回到「连接管理 → 完成授权」粘贴。code 仅停留在本页与用户剪贴板。"""
    return HTMLResponse(content=_OAUTH_CALLBACK_HTML)


_OAUTH_CALLBACK_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Atlas · 授权回调</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f6f8;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;color:#1f2329}
  .card{background:#fff;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.08);
        padding:32px;max-width:560px;width:92%}
  h1{font-size:18px;margin:0 0 8px}
  p{color:#646a73;font-size:14px;line-height:1.7;margin:8px 0}
  .field{margin-top:16px}
  label{font-size:12px;color:#8f959e;display:block;margin-bottom:4px}
  .row{display:flex;gap:8px}
  code{flex:1;background:#f2f3f5;border-radius:6px;padding:10px;font-size:13px;
       word-break:break-all;white-space:pre-wrap;min-height:20px}
  button{border:1px solid #d0d3d6;background:#fff;border-radius:6px;padding:0 14px;
         font-size:13px;cursor:pointer;height:38px}
  button:hover{background:#f2f3f5}
  .tip{margin-top:20px;background:#eef4ff;border-radius:8px;padding:12px;font-size:13px;color:#2b5fd9}
  .err{color:#d83931;font-size:13px;margin-top:12px;display:none}
</style>
</head>
<body>
<div class="card">
  <h1>已收到平台授权回调</h1>
  <p>本页不会自动提交任何凭据。请复制下方 <b>授权码（code）</b>，回到 Atlas「连接管理」，
     在对应连接上点击「完成授权」并粘贴该授权码。</p>
  <div class="field">
    <label>授权码 code</label>
    <div class="row">
      <code id="code"></code>
      <button onclick="copy('code')">复制</button>
    </div>
  </div>
  <div class="field">
    <label>state</label>
    <div class="row">
      <code id="state"></code>
      <button onclick="copy('state')">复制</button>
    </div>
  </div>
  <div class="tip" id="tip">提示：授权码通常只能使用一次且很快过期，请尽快完成「完成授权」。</div>
  <div class="err" id="err"></div>
</div>
<script>
  var q = new URLSearchParams(window.location.search);
  var code = q.get('code') || '';
  var state = q.get('state') || '';
  document.getElementById('code').textContent = code || '（回调中未找到 code）';
  document.getElementById('state').textContent = state || '（回调中未找到 state）';
  var err = document.getElementById('err');
  if (!code) { err.style.display='block'; err.textContent='回调地址缺少 code 参数，请重新发起授权。'; }
  function copy(id){
    var text = document.getElementById(id).textContent;
    navigator.clipboard.writeText(text).then(function(){
      document.getElementById('tip').textContent = '已复制：' + id + '。请回到连接管理完成授权。';
    }, function(){
      window.prompt('请手动复制：', text);
    });
  }
</script>
</body>
</html>
"""


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    principal: Principal
    # docs/95 打包 AV：登录时读一次用户行，前端据此弹出不可关闭的改密框。
    # 放在响应顶层而不是 Principal 里——Principal 会被 PG 档 iam_sessions 重建，
    # 那张表只有四列，标志塞进去就等于在两档上给出不同答案。
    mustChangePassword: bool = False


def _cookie_secure() -> bool:
    """httpOnly Cookie 的 Secure 位：真 prod（ATLAS_ENV=prod 且未开 demo mock）置位，
    要求 https；演练档（prod 开 demo mock / dev）不置位，保证 http://localhost 可登录。"""
    return read_env_profile() == "prod" and not demo_surface_enabled()


@app.post("/api/auth/login", response_model=LoginResponse)
def login(request: LoginRequest, http_request: Request, response: Response) -> LoginResponse:
    """账号登录换 sess-token（04 §5.14，docs/31 §2.2/§5）。

    坏凭证 401、停用 403；600s 内同 username+IP 5 次失败 → 429（锁定时不校验口令）；成功清零。
    """
    client_ip = http_request.client.host if http_request.client else ""
    throttle_key = f"{request.username}|{client_ip}"
    now = time.time()
    if login_throttle.is_locked(throttle_key, now):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    try:
        principal = authenticate_login(request.username, request.password)
    except HTTPException:
        login_throttle.record_failure(throttle_key, now)
        raise
    login_throttle.reset(throttle_key)
    token = session_store.issue(principal)
    # 触发租户装配，登录后该租户即有独立服务实例
    login_services = tenant_registry.get(principal.tenant_id)
    # T6 审计：登录成功显式记一条（失败在上方抛 401/403 不记；中间件跳过 login 路径）。
    # 审计失败不影响登录本身（fail-safe）。
    try:
        login_services.audit_store.record(
            tenant_id=principal.tenant_id,
            actor=principal.username,
            action="POST /api/auth/login",
            status_code=200,
            path=LOGIN_PATH,
            ip=client_ip,
        )
    except Exception as exc:  # 审计绝不阻断登录
        logger.warning("login audit record failed: %s", exc)
    # 打包 ZQ Q4：会话凭证同时以 httpOnly Cookie 下发（HttpOnly＋SameSite=Strict＋prod Secure）；
    # 前端不落盘 token，浏览器自动携带。响应体仍带 token 以兼容既有客户端与 A2A 调用方。
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(),
        path="/",
    )
    return LoginResponse(
        token=token,
        principal=principal,
        mustChangePassword=must_change_password(principal),
    )


@app.get("/api/auth/me", response_model=LoginResponse)
def me(request: Request, principal: Principal = Depends(get_principal)) -> LoginResponse:
    return LoginResponse(
        token=presented_token(request) or "",
        principal=principal,
        mustChangePassword=must_change_password(principal),
    )


@app.post("/api/auth/logout")
def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
) -> dict[str, bool]:
    token = presented_token(request)
    if token:
        session_store.revoke(token)
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict", path="/")
    return {"logged_out": True}


class ChangePasswordRequest(BaseModel):
    oldPassword: str
    newPassword: str


class UserResponse(BaseModel):
    username: str
    displayName: str
    role: Role
    status: str
    createdAt: str
    updatedAt: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    displayName: str
    role: Role


class UpdateUserRequest(BaseModel):
    displayName: str | None = None
    role: Role | None = None
    status: Literal["active", "disabled"] | None = None


class ResetPasswordRequest(BaseModel):
    newPassword: str


def _user_response(account: Any) -> UserResponse:
    return UserResponse(
        username=account.username,
        displayName=account.display_name,
        role=account.role,
        status=account.status,
        createdAt=account.created_at,
        updatedAt=account.updated_at,
    )


def _validate_password_or_422(password: str) -> None:
    try:
        validate_password(password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/auth/change-password")
def change_password(
    raw: ChangePasswordRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict[str, bool]:
    """登录用户改本人密码（docs/31 §3）：旧口令错 400；成功吊销本人其他会话、保留当前。

    `by_owner=True` 盖轮换位（docs/95 打包 AV）——这是唯一能清掉强制改密门的写入路径。
    keep_token 用 `presented_token`（与 `get_principal` 同一份取数规则）：以前这里是
    `_bearer_token`，打包 ZQ Q4 之后 SPA 不带 Authorization 头，于是"保留当前"保留的是
    另一个会话，改密的人反而被登出。
    """
    account = user_store.get(principal.tenant_id, principal.username)
    if account is None or not verify_password(raw.oldPassword, account.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    _validate_password_or_422(raw.newPassword)
    if raw.newPassword == raw.oldPassword:
        raise HTTPException(status_code=422, detail="新密码不能与原密码相同")
    user_store.set_password(
        principal.tenant_id,
        principal.username,
        raw.newPassword,
        keep_token=presented_token(request),
        by_owner=True,
    )
    return {"changed": True}


@app.get("/api/users")
def list_users(principal: Principal = Depends(require("administer"))) -> list[UserResponse]:
    """admin 列本租户用户（docs/31 §3），不含 password_hash。"""
    return [_user_response(account) for account in user_store.list(principal.tenant_id)]


@app.post("/api/users", status_code=201)
def create_user(
    raw: CreateUserRequest,
    principal: Principal = Depends(require("administer")),
) -> UserResponse:
    """admin 在本租户建用户（docs/31 §3）：重名 409、策略不达标 422。"""
    try:
        validate_username(raw.username)
        validate_password(raw.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not raw.displayName.strip():
        raise HTTPException(status_code=422, detail="显示名不能为空")
    try:
        account = user_store.create(
            tenant_id=principal.tenant_id,
            username=raw.username,
            password=raw.password,
            display_name=raw.displayName.strip(),
            role=raw.role,
        )
    except UserExists as exc:
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    return _user_response(account)


@app.patch("/api/users/{username}")
def update_user(
    username: str,
    raw: UpdateUserRequest,
    principal: Principal = Depends(require("administer")),
) -> UserResponse:
    """admin 改本租户用户 displayName/role/status（docs/31 §3）；跨租户/不存在 → 404。"""
    if user_store.get(principal.tenant_id, username) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    data = raw.model_dump(exclude_unset=True)
    updated = user_store.update(
        principal.tenant_id,
        username,
        display_name=data.get("displayName"),
        role=raw.role,
        status=data.get("status"),
    )
    assert updated is not None
    return _user_response(updated)


@app.post("/api/users/{username}/reset-password")
def reset_password(
    username: str,
    raw: ResetPasswordRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, bool]:
    """admin 重置本租户用户密码（docs/31 §3）：404/422；成功吊销该用户全部会话。

    不盖轮换位＝**重新装填**强制改密门（docs/95 §2 订正）：重置后的口令只有操作它的 admin
    与该系统账号知道，本人从未亲手套上过口令，强制位在 prod 因此回到未满足态。
    """
    if user_store.get(principal.tenant_id, username) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _validate_password_or_422(raw.newPassword)
    user_store.set_password(principal.tenant_id, username, raw.newPassword)
    return {"reset": True}


@app.get("/api/adapters")
def list_adapters(
    principal: Principal = Depends(require("read")),
    accept_language: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    # 执行期注册表在全局基础设施之上合并本租户渠道适配器（docs/38 §1E）
    locale = resolve_locale(accept_language)
    adapters = _runtime_registry(services_for(principal)).list_adapters()
    for adapter in adapters:
        adapter["tools"] = [localize_tool_desc(tool, locale) for tool in adapter.get("tools", [])]
    return adapters


_OPENAPI_FETCH_TIMEOUT = 10.0
_openapi_egress = EgressGuard.from_env()
# 打包 ZM：模板远程导入的出向校验（与 httpapi/openapi 同一份策略与 env，不开第二份配置）。
_template_import_egress = EgressGuard.from_env()
_TEMPLATE_IMPORT_TIMEOUT = 10.0
_TEMPLATE_IMPORT_MAX_BYTES = 256 * 1024


def _fetch_openapi_spec(url: str) -> str:
    """API 层 URL 抓取（docs/42 §4）：出向过 EgressGuard，10s、不跟重定向；
    网络/出向失败折 OPENAPI_FETCH_NETWORK_ERROR，上游 HTTP ≥400 折
    OPENAPI_FETCH_HTTP_ERROR（docs/08 打包 BF：原 OPENAPI_FETCH_FAILED 一码承载两条
    不同答案，已按答案拆开）。"""
    try:
        _openapi_egress.check(url)
        with httpx.Client(follow_redirects=False) as client:
            response = client.get(url, timeout=_OPENAPI_FETCH_TIMEOUT)
    except (EgressDenied, httpx.HTTPError, ValueError) as exc:
        raise OpenApiError(
            "OPENAPI_FETCH_NETWORK_ERROR",
            f"规格抓取失败：{exc}",
            params={"detail": str(exc)},
        ) from exc
    if response.status_code >= 400:
        raise OpenApiError(
            "OPENAPI_FETCH_HTTP_ERROR",
            f"规格抓取失败：HTTP {response.status_code}",
            params={"status": response.status_code},
        )
    return response.text


class OpenApiSourceRequest(BaseModel):
    content: str | None = None
    url: str | None = None
    credentials: dict[str, str] | None = None


class OpenApiCredentialsRequest(BaseModel):
    credentials: dict[str, str | dict[str, str] | None]


def _credential_error(
    name: str,
    message: str = "未知鉴权方案",
    code: str = "OPENAPI_CREDENTIAL_SCHEME_UNKNOWN",
) -> HTTPException:
    """docs/08 打包 BC：一个码只承载一条答案——两种失败各有自己的码（旧码
    `OPENAPI_INVALID_CREDENTIAL` 把「未知鉴权方案」与「Basic 缺用户名或密码」压成了一个）。"""
    return HTTPException(
        status_code=422,
        detail={"code": code, "message": f"{message}：{name}", "params": {"name": name}},
    )


def _encrypt_credentials(
    raw: dict[str, str] | None, scheme_names: set[str]
) -> dict[str, str]:
    envelopes: dict[str, str] = {}
    if not raw:
        return envelopes
    for name, value in raw.items():
        if name not in scheme_names:
            raise _credential_error(name)
        if isinstance(value, str) and value.strip():
            envelopes[name] = _secret_provider.encrypt(value.strip())
    return envelopes


def _openapi_http_error(exc: OpenApiError) -> HTTPException:
    # 带 params 的码要把 params 一起下发，否则英文态模板填不满会回退中文原文。
    detail: dict[str, object] = {"code": exc.code, "message": exc.message}
    if getattr(exc, "params", None):
        detail["params"] = exc.params
    return HTTPException(status_code=422, detail=detail)


def _parse_openapi_source(raw: OpenApiSourceRequest):
    if (raw.content is None) == (raw.url is None):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "OPENAPI_DOCUMENT_SOURCE_EXCLUSIVE",
                "message": "content 与 url 必须二选一",
            },
        )
    if raw.content is not None:
        text = raw.content
    else:
        try:
            text = _fetch_openapi_spec(raw.url)
        except OpenApiError as exc:
            raise _openapi_http_error(exc) from exc
    try:
        return parse_document(text)
    except OpenApiError as exc:
        raise _openapi_http_error(exc) from exc


@app.post("/api/openapi/preview")
def preview_openapi(
    raw: OpenApiSourceRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    spec = _parse_openapi_source(raw)
    return {
        "title": spec.title,
        "base_url": spec.base_url,
        "operations": [op.model_dump() for op in spec.operations],
        "security_schemes": [
            {
                "name": scheme.name,
                "kind": scheme.kind,
                "location": scheme.location,
                "param": scheme.param,
            }
            for scheme in spec.security_schemes.values()
        ],
        "imported_count": sum(not op.skipped for op in spec.operations),
        "skipped_count": sum(op.skipped for op in spec.operations),
    }


@app.post("/api/openapi/imports", status_code=201)
def import_openapi(
    raw: OpenApiSourceRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    spec = _parse_openapi_source(raw)
    if not any(not op.skipped for op in spec.operations):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "OPENAPI_NO_IMPORTABLE_OPERATION",
                "message": "没有可导入的 operation（文档内操作全部被跳过）",
            },
        )
    try:
        envelopes = _encrypt_credentials(
            raw.credentials, set(spec.security_schemes)
        )
        imported = services_for(principal).openapi_imports.add(
            spec, envelopes=envelopes
        )
    except ImportStoreError as exc:
        detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
        if getattr(exc, "existing_spec_id", None):
            detail["existingSpecId"] = exc.existing_spec_id
        if getattr(exc, "params", None):
            detail["params"] = exc.params
        raise HTTPException(
            status_code=exc.status_code, detail=detail
        ) from exc
    return imported.model_dump()


@app.get("/api/openapi/imports")
def list_openapi_imports(
    include_deleted: bool = False,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    # docs/60 G2：查看含已软删条目需 administer；缺省/false 维持 read 且不返已删。
    if include_deleted and not can(principal.role, "administer"):
        raise HTTPException(status_code=403, detail="无权查看已删除的 API 规格")
    items = services_for(principal).openapi_imports.list(
        include_deleted=include_deleted
    )
    return {"items": [spec.model_dump() for spec in items]}


@app.get("/api/openapi/imports/{spec_id}")
def get_openapi_import(
    spec_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    imported = services_for(principal).openapi_imports.get(spec_id)
    if imported is None:
        raise HTTPException(status_code=404, detail="导入规格不存在")
    return imported.model_dump()


@app.delete("/api/openapi/imports/{spec_id}")
def delete_openapi_import(
    spec_id: str,
    hard: bool = False,
    principal: Principal = Depends(require("administer")),
) -> Response:
    store = services_for(principal).openapi_imports
    if hard:
        # docs/60 G2：仅已软删记录可物理删除；未软删 → 409，不存在 → 404，成功 204。
        try:
            purged = store.purge(spec_id)
        except ImportStoreError as exc:
            # 带 params 的码要把 params 一起下发，否则英文态模板填不满回退中文原文。
            detail: dict[str, object] = {"code": exc.code, "message": str(exc)}
            if getattr(exc, "params", None):
                detail["params"] = exc.params
            raise HTTPException(status_code=exc.status_code, detail=detail) from exc
        if not purged:
            raise HTTPException(status_code=404, detail="导入规格不存在")
        return Response(status_code=204)
    if not store.delete(spec_id):
        raise HTTPException(status_code=404, detail="导入规格不存在")
    return {"deleted": True}


@app.post("/api/openapi/imports/{spec_id}/restore")
def restore_openapi_import(
    spec_id: str,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """docs/56 §3.3：恢复软删规格；与另一未删同指纹规格冲突返 409。"""
    ok, code, existing = services_for(principal).openapi_imports.restore(spec_id)
    if ok:
        return {"restored": True}
    if code == "OPENAPI_RESTORE_CLASH":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OPENAPI_RESTORE_CLASH",
                "message": "恢复后与现有未删规格内容重复",
                "existingSpecId": existing,
            },
        )
    raise HTTPException(status_code=404, detail="导入规格不存在或未被删除")


@app.put("/api/openapi/imports/{spec_id}/credentials")
def put_openapi_credentials(
    spec_id: str,
    raw: OpenApiCredentialsRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, list[str]]:
    store = services_for(principal).openapi_imports
    imported = store.get(spec_id)
    if imported is None:
        raise HTTPException(status_code=404, detail="导入规格不存在")
    envelopes = dict(imported.credential_envelopes)
    for name, value in raw.credentials.items():
        if name not in imported.security_schemes:
            raise _credential_error(name)
        scheme = imported.security_schemes[name]
        if scheme.kind == "basic":
            if not isinstance(value, dict):
                envelopes.pop(name, None)
                continue
            username = value.get("username")
            password = value.get("password")
            if (
                not isinstance(username, str)
                or not username.strip()
                or not isinstance(password, str)
                or not password.strip()
            ):
                raise _credential_error(name, "Basic 鉴权需同时提供非空用户名与密码",
                               code="OPENAPI_CREDENTIAL_BASIC_INCOMPLETE")
            plaintext = json.dumps(
                {"username": username.strip(), "password": password.strip()},
                ensure_ascii=False,
            )
            envelopes[name] = _secret_provider.encrypt(plaintext)
        elif not isinstance(value, str) or not value.strip():
            envelopes.pop(name, None)
        else:
            envelopes[name] = _secret_provider.encrypt(value.strip())
    updated = store.put_credentials(spec_id, envelopes)
    if updated is None:
        raise HTTPException(status_code=404, detail="导入规格不存在")
    configured = [name for name in updated.security_schemes if name in envelopes]
    return {"configured": configured}


@app.post("/api/graphs", response_model=SaveGraphResponse)
def save_graph(
    raw: dict[str, Any], principal: Principal = Depends(require("operate"))
) -> SaveGraphResponse:
    services = services_for(principal)
    graph = parse_graph(raw)
    graph_id = services.graph_store.save(raw)
    return SaveGraphResponse(id=graph_id, version=graph.version)


@app.get("/api/graphs")
def list_graphs(principal: Principal = Depends(require("read"))) -> dict[str, list[dict[str, Any]]]:
    """列出已保存图（subgraph 节点选择器数据源，04 §5.7；按租户分区，04 §5.14）。"""
    return {"items": services_for(principal).graph_store.list()}


@app.get("/api/graphs/{graph_id}")
def get_graph(
    graph_id: str,
    releaseVersion: int | None = None,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    raw = services_for(principal).graph_store.get(graph_id, releaseVersion)
    if raw is None:
        # 跨租户访问同样 404，不泄漏资源存在性（04 §5.14）
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return raw


@app.put("/api/graphs/{graph_id}")
def update_graph(
    graph_id: str,
    raw: dict[str, Any],
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """覆盖已存图的 latest 草稿（M9 发布流：同一 graph 迭代多版本，不新建 id）。

    已发布版本不可变、不受影响；图不存在（含跨租户）404。校验与 POST /api/graphs 一致。
    """
    services = services_for(principal)
    graph = parse_graph(raw)
    try:
        services.graph_store.update_draft(graph_id, raw)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{exc.args[0]}") from exc
    return {"id": graph_id, "version": graph.version}


def _release_gate_for_draft(services: TenantServices, graph_id: str) -> dict[str, Any]:
    """对当前 latest 草稿跑发布前批量回放门禁（M9）；无草稿/图不存在返 None（调用方 404）。"""
    raw = services.graph_store.get(graph_id)
    if raw is None:
        return None
    return run_release_gate(
        graph_id=graph_id,
        draft=raw,
        cases=services.recording_store.list(),
        services=services,
        registry=_runtime_registry(services),
        graph_resolver=_tenant_graph_resolver(services),
    )


@app.post("/api/graphs/{graph_id}/release-gate")
def run_graph_release_gate(
    graph_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """发布前批量回放门禁：只跑门禁不发布（M9，03 `release_gate`；D26 部分取回）。

    对当前 latest 草稿逐例重跑 graph_id 匹配的录制用例并比对；无草稿/图不存在 404。
    """
    services = services_for(principal)
    report = _release_gate_for_draft(services, graph_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在或无待发布草稿：{graph_id}")
    # D26 报告 v1：每次门禁运行沉淀（manual，含 skipped），响应纯超集带报告 id（03 release_report）
    return services.report_store.record(graph_id=graph_id, trigger="manual", report=report)


@app.get("/api/graphs/{graph_id}/release-reports")
def list_graph_release_reports(
    graph_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """本图批量回放报告历史（倒序摘要，不含 cases；D26 报告 v1，03 release_report）。

    图在本租户不存在（无草稿且无发布版）→ 404，跨租户不泄漏存在性（照 rollout 端点）。
    """
    services = services_for(principal)
    if services.graph_store.get(graph_id) is None and not services.graph_store.list_versions(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return {"items": services.report_store.list_summary(graph_id)}


@app.get("/api/release-reports")
def list_all_release_reports(
    limit: int = 100, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """跨图批量回放报告看板（docs/28 §2.4）：倒序摘要，不含 cases，read 角色。

    limit 默认 100、上限 200（非整数 query 由 FastAPI 422）；报告两档：内存 ring 100 /
    PG 表 `release_reports`（迁移 022 已落库，PG 档不做 ring 淘汰），原「不 PG 化」口径过时
    （docs/61 §6 H5 勘误）。
    """
    services = services_for(principal)
    bounded = max(1, min(limit, 200))
    return {"items": services.report_store.list_all_summary(limit=bounded)}


@app.get("/api/graphs/{graph_id}/release-reports/{report_id}")
def get_graph_release_report(
    graph_id: str, report_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """报告详情（含 cases 逐例结果；D26 报告 v1，03 release_report）。

    图不存在、报告不属于该图或不存在 → 404（跨租户/跨图不泄漏存在性）。
    """
    services = services_for(principal)
    if services.graph_store.get(graph_id) is None and not services.graph_store.list_versions(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    report = services.report_store.get(graph_id, report_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"发布报告不存在：{graph_id}/{report_id}"
        )
    return report


@app.get("/api/graphs/{graph_id}/release-reports/{report_id}/export")
def export_graph_release_report(
    graph_id: str,
    report_id: str,
    format: Literal["csv", "json"] = "csv",
    principal: Principal = Depends(require("read")),
):
    """导出报告为 CSV/JSON 下载（D26 报告导出；read 角色；404 口径同详情端点，03 release_report）。"""
    services = services_for(principal)
    if services.graph_store.get(graph_id) is None and not services.graph_store.list_versions(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    report = services.report_store.get(graph_id, report_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"发布报告不存在：{graph_id}/{report_id}"
        )
    if format == "json":
        return JSONResponse(
            report,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{report_id}.json"'
            },
        )
    # UTF-8 BOM 让 Excel 直接识别中文表头（零新依赖，stdlib csv 渲染见 reports.report_to_csv）。
    csv_text = report_to_csv(report)
    return Response(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.csv"'},
    )


# --- D26 影子模式（docs/33 §3）：线上旁路录制，进程内 v1 -----------------------------


def _parse_human_outcome(raw: Any) -> HumanOutcome | None:
    """校验并构造人工实际处理；action 必须是非空串，非法由端点转 422。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="human_outcome 必须是对象")
    action = str(raw.get("action") or "").strip()
    if not action:
        raise HTTPException(status_code=422, detail="human_outcome.action 不能为空")
    note = raw.get("note")
    return HumanOutcome(action=action, note=str(note) if note else None)


@app.post("/api/graphs/{graph_id}/shadow-runs", status_code=201)
def create_shadow_run(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """影子运行：旁路跑完整决策链，读透传/写短路，不进生产观测面（docs/33 §3.4）。

    预置全部 human_approval 节点 approved 秒过；不写 run_store/RunRecord、不触发告警与灰度
    门控、不产 tool_metric（loader shadow 短路）；独立 tracer（trace_id 入记录）与独立审批
    broker（不污染租户 pending）；异常也沉淀 status=error 记录。
    """
    services = services_for(principal)
    graph = _load_graph_or_404(services, graph_id, None)
    body = payload or {}
    raw_inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
    # approvals 是运行控制键（不进全局变量、不回记入记录 inputs）。
    inputs = dict(raw_inputs)
    presets = preset_all_approvals(graph, resolver=_tenant_graph_resolver(services))
    if presets:
        inputs["approvals"] = {**presets, **(inputs.get("approvals") or {})}
    # 事件等待无法在影子中被信号放行：预置空 payload 秒过（docs/47 §3.3）。
    # 打包 ZJ（D47）：递归下潜子图，键路径限定（"sub-1/wait-1"），子图内 wait 同样秒过。
    event_presets = preset_all_wait_events(graph, resolver=_tenant_graph_resolver(services))
    if event_presets:
        inputs["waitEvents"] = {**event_presets, **(inputs.get("waitEvents") or {})}
    outcome = _parse_human_outcome(body.get("human_outcome"))

    registry = _runtime_registry(services)
    tool_permissions = _tool_permissions(registry)
    node_index = {node.id: node for node in graph.nodes}
    top_events: list[tuple[str, dict[str, Any]]] = []

    def shadow_emit(event: dict[str, Any]) -> None:
        # 只收顶层 node_end（子图内部事件带 subgraphPath，与监控 tool_calls 同口径排除）。
        if event.get("type") == "node_end" and not event.get("subgraphPath"):
            output = event.get("output")
            if isinstance(output, dict):
                top_events.append((event["node_id"], output))

    tracer = Tracer(graph_id=graph_id)
    shadow_broker = ApprovalBroker()  # 独立 broker：预置秒过且不污染租户 pending
    status = "completed"
    error: str | None = None
    try:
        run_graph(
            graph,
            inputs=inputs,
            registry=registry,
            approval_broker=shadow_broker,
            graph_id=graph_id,
            graph_resolver=_tenant_graph_resolver(services),
            emit=shadow_emit,
            tracer=tracer,
            shadow=True,
            tenant_id=principal.tenant_id,
        )
    except Exception as exc:  # 影子异常也沉淀记录，绝不影响生产链路
        status = "error"
        error = f"{type(exc).__name__}: {exc}"

    decisions, intents = extract_shadow_events(top_events, node_index, tool_permissions)
    return services.shadow_store.add(
        graph_id=graph_id,
        trace_id=tracer.trace_id,
        decisions=decisions,
        tool_intents=intents,
        inputs=raw_inputs or None,
        status=status,
        error=error,
        human_outcome=outcome,
    )


@app.get("/api/shadow-runs")
def list_shadow_runs(
    graph_id: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """本租户影子运行记录倒序（可按图过滤，limit 1–200，默认 50）。"""
    services = services_for(principal)
    bounded = max(1, min(int(limit), 200))
    return {"items": services.shadow_store.list(graph_id=graph_id, limit=bounded)}


@app.get("/api/shadow-runs/{sid}")
def get_shadow_run(sid: str, principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    services = services_for(principal)
    run = services.shadow_store.get(sid)
    if run is None:
        raise HTTPException(status_code=404, detail=f"影子运行不存在：{sid}")
    return run


@app.post("/api/shadow-runs/{sid}/compare")
def compare_shadow_run(
    sid: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """补录/覆盖人工实际处理并重算对比（docs/33 §3.4）；action 非空，非法 422，不存在 404。"""
    services = services_for(principal)
    body = payload or {}
    outcome = _parse_human_outcome(body.get("human_outcome"))
    if outcome is None:
        raise HTTPException(status_code=422, detail="human_outcome.action 不能为空")
    run = services.shadow_store.attach_outcome(sid, outcome)
    if run is None:
        raise HTTPException(status_code=404, detail=f"影子运行不存在：{sid}")
    return run


@app.post("/api/graphs/{graph_id}/publish", response_model=PublishGraphResponse)
def publish_graph(
    graph_id: str,
    request: PublishGraphRequest | None = None,
    principal: Principal = Depends(require("operate")),
) -> PublishGraphResponse:
    """发布 latest 草稿为不可变版本（M6，docs/20 §4.1 / ADR T19）。

    M9：可选 body ``{"gate": true}``——先跑发布前批量回放门禁，blocked → 409
    带完整 GateReport 且不产新版本；total=0（skipped）不阻塞（03 `release_gate`）。
    """
    services = services_for(principal)
    if request is not None and request.gate:
        report = _release_gate_for_draft(services, graph_id)
        if report is None:
            raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
        # D26 报告 v1：发布门禁沉淀（publish-gate，通过/blocked/skipped 均沉淀，409 报告体同样带 id）
        report = services.report_store.record(
            graph_id=graph_id, trigger="publish-gate", report=report
        )
        if report["blocked"]:
            raise HTTPException(
                status_code=409,
                detail={"message": "发布门禁未通过，已拦截发布（存在不匹配用例）", "report": report},
            )
    try:
        release_version = publish_graph_version(services.graph_store, graph_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{exc.args[0]}") from exc
    # docs/68 §1 D-4：调度不是用户另填的一张表，是发布的派生物（同处撤销已删掉定时节点的调度）。
    _derive_schedule_on_publish(services, principal.tenant_id, graph_id, release_version)
    return PublishGraphResponse(id=graph_id, releaseVersion=release_version)


@app.get("/api/graphs/{graph_id}/subgraph-upgrades")
def subgraph_upgrades(
    graph_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """发布前子图版本升级体检（docs/28 §5.2 ⑪，read）：纯只读、不产版本、不阻断。

    返 ``{items: [{node_id, sub_id, from_version, to_version, first_pin}]}``；
    草稿不存在 404。v1 只扫顶层 subgraph、只对已发布版本号（不检测子图草稿 dirty）。
    """
    plan = subgraph_upgrade_plan(services_for(principal).graph_store, graph_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return {"items": plan}


@app.get("/api/graphs/{graph_id}/versions")
def list_graph_versions(
    graph_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, list[int]]:
    """已发布版本号列表（升序；未发布过 → 空列表）（M6）。"""
    return {"items": services_for(principal).graph_store.list_versions(graph_id)}


@app.get("/api/graphs/{graph_id}/diff")
def graph_diff(
    graph_id: str,
    from_version: int | None = Query(default=None, alias="fromVersion"),
    to_version: int | None = Query(default=None, alias="toVersion"),
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """两版 graph 配置结构差异（B3，D26 子集，read）：纯只读、不产版本、不阻断。

    - toVersion 缺省＝latest 草稿；给定＝该发布版本快照。
    - fromVersion 缺省＝最近发布版本；该图从未发布时基线取空图（首次发布前全为新增）。
    目标快照缺失（图/版本不存在）→ 404，跨租户不泄漏存在性。
    """
    store = services_for(principal).graph_store
    to_raw = (
        store.get(graph_id) if to_version is None else store.get(graph_id, to_version)
    )
    if to_raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 或目标版本不存在：{graph_id}")
    if from_version is None:
        versions = store.list_versions(graph_id)
        from_raw = store.get(graph_id, versions[-1]) if versions else {}
        from_label: int | None = versions[-1] if versions else None
    else:
        from_raw = store.get(graph_id, from_version)
        from_label = from_version
        if from_raw is None:
            raise HTTPException(status_code=404, detail=f"基线版本不存在：{from_version}")
    diff = diff_graph(from_raw, to_raw)
    return {
        "graphId": graph_id,
        "fromVersion": from_label,
        "toVersion": to_version,  # None 表示 latest 草稿
        "summary": diff_summary(diff),
        "diff": diff,
    }


@app.get("/api/graphs/{graph_id}/rollout")
def get_rollout(
    graph_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """灰度配置与运行态投影（M9，03 `rollout_config`；从未配置返 idle 默认态）。

    图在本租户不存在（无草稿且无发布版）→ 404，跨租户不泄漏存在性（04 §5.14）。
    """
    services = services_for(principal)
    if services.graph_store.get(graph_id) is None and not services.graph_store.list_versions(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    state = services.routing_store.snapshot(graph_id)
    return _rollout_projection(state)


@app.put("/api/graphs/{graph_id}/rollout")
def put_rollout(
    graph_id: str,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """存灰度配置（只存不启动；非法形状 422 中文；M9，04 §5.16）。"""
    config = _rollout_config_or_422(body)
    state = services_for(principal).routing_store.configure(graph_id, config)
    return _rollout_projection(state)


@app.post("/api/graphs/{graph_id}/rollout/start")
def start_rollout(
    graph_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """idle→canary：取最新两个发布版（stable/candidate）；未配置/版本不足/非 idle → 409。"""
    services = services_for(principal)
    versions = services.graph_store.list_versions(graph_id)
    try:
        state = services.routing_store.start(graph_id, versions)
    except RolloutError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _rollout_projection(state)


@app.post("/api/graphs/{graph_id}/rollout/promote")
def promote_rollout(
    graph_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """canary→full：唯一放量路径，仅手动（不存在任何自动 promote）。"""
    try:
        state = services_for(principal).routing_store.promote(graph_id)
    except RolloutError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _rollout_projection(state)


@app.post("/api/graphs/{graph_id}/rollout/rollback")
def rollback_rollout(
    graph_id: str,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """任意态→rolled_back（幂等；candidate 撤流、stable 接新流量；M9 门控自动回滚走同一函数）。"""
    reason = (body or {}).get("reason") or "manual rollback"
    try:
        state = services_for(principal).routing_store.rollback(
            graph_id, actor="manual", reason=str(reason)
        )
    except RolloutError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _rollout_projection(state)


@app.get("/api/templates")
def list_catalog_templates(
    principal: Principal = Depends(require("read")),
    accept_language: str | None = Header(default=None),
    q: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """列出模板（内置目录 04 §5.10 在前、租户自建模板 docs/85 在后；列表投影不含 graph）；
    内置 name/description 按 Accept-Language 本地化（docs/70）。
    打包 A1（docs/97）：可选 q 过滤（两段各自按 name/description/tags/category casefold 子串任一命中），
    q 缺失/空白＝全量；用户段投影增 version/updated_at/usage_count，内置段 version 恒 1。"""
    locale = resolve_locale(accept_language)
    query = (q or "").strip().casefold()
    items = [
        localize_template(
            {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "tags": template.tags,
                "category": template.category,
                "node_count": len(template.graph["nodes"]),
                "source": "catalog",
                "deletable": False,
                "version": 1,
            },
            locale,
        )
        for template in list_templates()
        if not query or _template_matches_query(template.model_dump(), query)
    ]
    items.extend(
        {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "tags": template.tags,
            "category": template.category,
            "node_count": len(template.graph.get("nodes", [])),
            "source": "user",
            "deletable": True,
            "created_at": template.created_at,
            "version": template.version,
            "updated_at": template.updated_at,
            "usage_count": template.usage_count,
        }
        for template in services_for(principal).user_templates.list()
        if not query or _template_matches_query(template.model_dump(), query)
    )
    return {"items": items}


def _template_matches_query(template: dict[str, Any], query: str) -> bool:
    """A1 搜索：name/description/tags/category 任一 casefold 子串命中（docs/97 E-3）。"""
    haystacks = [
        str(template.get("name") or ""),
        str(template.get("description") or ""),
        " ".join(template.get("tags") or []),
        str(template.get("category") or ""),
    ]
    return any(query in (part.casefold()) for part in haystacks)


_TEMPLATE_PARAM_TYPES = {"string", "number", "boolean", "select"}
_TEMPLATE_PARAM_KEYS = {"type", "label", "required", "default", "hint", "options"}


def validate_template_params(params: dict[str, Any]) -> list[str]:
    """A1 参数化向导声明形状校验（docs/97 E-5）：type 枚举、label≤40、required 布尔、
    select 须 options 非空字符串列表 ≤20、未知键拒绝；返回中文错误列表（空＝通过）。"""
    errors: list[str] = []
    if not isinstance(params, dict):
        return ["参数声明必须是对象"]
    for pname, pdecl in params.items():
        if not isinstance(pdecl, dict):
            errors.append(f"参数 {pname} 的声明必须是对象")
            continue
        unknown = set(pdecl) - _TEMPLATE_PARAM_KEYS
        if unknown:
            errors.append(f"参数 {pname} 含未知字段：{', '.join(sorted(unknown))}")
        ptype = pdecl.get("type", "string")
        if ptype not in _TEMPLATE_PARAM_TYPES:
            errors.append(f"参数 {pname} 的 type 必须是 string/number/boolean/select 之一")
        label = pdecl.get("label")
        if label is not None and not isinstance(label, str):
            errors.append(f"参数 {pname} 的 label 必须是字符串")
        elif isinstance(label, str) and len(label) > 40:
            errors.append(f"参数 {pname} 的 label 长度须在 40 字符以内")
        if "required" in pdecl and not isinstance(pdecl["required"], bool):
            errors.append(f"参数 {pname} 的 required 必须是布尔值")
        if ptype == "select":
            options = pdecl.get("options")
            if (
                not isinstance(options, list)
                or not options
                or len(options) > 20
                or not all(isinstance(o, str) for o in options)
            ):
                errors.append(f"参数 {pname} 的 options 必须是非空字符串列表（≤20 项）")
    return errors


def _validate_instantiate_values(
    params: dict[str, Any], values: dict[str, Any]
) -> list[str]:
    """A1 instantiate 值校验（docs/97 E-6）：required 缺失/类型不符/select 范围/未知参数名。"""
    errors: list[str] = []
    for pname, pdecl in params.items():
        present = pname in values
        if pdecl.get("required") and not present:
            errors.append(f"参数 {pname} 为必填")
            continue
        if not present:
            continue
        value = values[pname]
        ptype = pdecl.get("type", "string")
        if ptype == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"参数 {pname} 必须是数字")
        elif ptype == "boolean":
            if not isinstance(value, bool):
                errors.append(f"参数 {pname} 必须是布尔值")
        elif ptype == "select":
            options = pdecl.get("options") or []
            if value not in options:
                errors.append(f"参数 {pname} 的值不在可选范围内")
    for pname in values:
        if pname not in params:
            errors.append(f"未知参数：{pname}")
    return errors


class UserTemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=8)
    category: str = Field(default="", max_length=30)
    graph: dict[str, Any]
    params: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/templates", status_code=201)
def create_user_template(
    body: UserTemplateCreateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """画布另存为租户私有模板（docs/85 D-2）：parse_graph 仅校验可编译，图原样存。
    A1（docs/97）：body 可带 params（参数化声明，形状校验失败 422 且模板未变）。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="模板名称不能为空")
    tags = [tag.strip() for tag in body.tags]
    for tag in tags:
        if not 1 <= len(tag) <= 20:
            raise HTTPException(status_code=422, detail="标签长度须在 1-20 字符之间")
    category = body.category.strip()
    if not category:
        category = ""
    elif len(category) > 30:
        raise HTTPException(status_code=422, detail="分类长度须在 30 字符以内")
    params_errors = validate_template_params(body.params)
    if params_errors:
        raise HTTPException(status_code=422, detail="；".join(params_errors))
    parse_graph(body.graph)
    template = services_for(principal).user_templates.add(
        name=name,
        description=body.description,
        tags=tags,
        category=category,
        graph=body.graph,
        params=body.params,
    )
    return {**template.model_dump(), "source": "user", "deletable": True}


class UserTemplateUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    tags: list[str] | None = Field(default=None, max_length=8)
    category: str | None = Field(default=None, max_length=30)
    graph: dict[str, Any]
    params: dict[str, Any] | None = Field(default=None)
    if_match_version: int | None = Field(default=None, ge=1)


@app.put("/api/templates/{template_id}")
def update_user_template(
    template_id: str,
    body: UserTemplateUpdateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """整体更新租户私有模板（docs/86 D-1）：id/seq/created_at 不变；
    tags 字段缺省＝保留旧值（D-5 唯一例外），内置 id 与不存在统一 404。
    A1（docs/97）：可选 `if_match_version` CAS——不匹配 409 `TEMPLATE_VERSION_CONFLICT`（缺省无防护）；
    `params` 缺省＝保留旧值（照 tags 先例）。"""
    store = services_for(principal).user_templates
    if get_template(template_id) is not None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    current = store.get(template_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="模板名称不能为空")
    tags = current.tags if body.tags is None else [tag.strip() for tag in body.tags]
    for tag in tags:
        if not 1 <= len(tag) <= 20:
            raise HTTPException(status_code=422, detail="标签长度须在 1-20 字符之间")
    category = current.category if body.category is None else body.category.strip()
    if len(category) > 30:
        raise HTTPException(status_code=422, detail="分类长度须在 30 字符以内")
    if body.params is not None:
        params_errors = validate_template_params(body.params)
        if params_errors:
            raise HTTPException(status_code=422, detail="；".join(params_errors))
    parse_graph(body.graph)
    try:
        updated = store.update(
            template_id,
            name=name,
            description=body.description,
            tags=tags,
            category=category,
            graph=body.graph,
            params=body.params,
            if_match_version=body.if_match_version,
        )
    except TemplateVersionConflict:
        raise HTTPException(
            status_code=409,
            detail=f"模板已被他人更新（当前版本 {current.version}），请刷新后重试",
        ) from None
    return {**updated.model_dump(), "source": "user", "deletable": True}


@app.delete("/api/templates/{template_id}")
def delete_user_template(
    template_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, bool]:
    """仅删用户模板（docs/85 D-3）：内置 id 与不存在 id 统一 404。"""
    if get_template(template_id) is not None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    if not services_for(principal).user_templates.delete(template_id):
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    return {"deleted": True}


@app.post("/api/templates/{template_id}/usage")
def touch_template_usage(
    template_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, int]:
    """A1 使用统计（docs/97 E-4）：显式 touch 计数 +1，返回新值；
    内置/不存在/跨租户统一 404（内置 v1 不计数）。"""
    if get_template(template_id) is not None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    store = services_for(principal).user_templates
    if not store.touch(template_id):
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    updated = store.get(template_id)
    return {"usage_count": updated.usage_count}


class TemplateInstantiateRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/templates/{template_id}/instantiate")
def instantiate_user_template(
    template_id: str,
    body: TemplateInstantiateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """A1 参数化实例化（docs/97 E-6）：校验 required/类型/options/未知参数名后返回
    模板图深拷贝，values 覆写进 graph.variables（命中 name 覆写 value、未命中 append
    {name,type,value,scope:"global"}）；模板原图逐键不变；内置/不存在/跨租户 404。"""
    if get_template(template_id) is not None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    store = services_for(principal).user_templates
    template = store.get(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    value_errors = _validate_instantiate_values(template.params, body.values)
    if value_errors:
        raise HTTPException(status_code=422, detail="；".join(value_errors))
    graph = copy.deepcopy(template.graph)
    variables = list(graph.get("variables", []))
    for pname, pdecl in template.params.items():
        if pname not in body.values:
            continue
        value = body.values[pname]
        found = False
        for var in variables:
            if isinstance(var, dict) and var.get("name") == pname:
                var["value"] = value
                found = True
                break
        if not found:
            variables.append(
                {
                    "name": pname,
                    "type": pdecl.get("type", "string"),
                    "value": value,
                    "scope": "global",
                }
            )
    graph["variables"] = variables
    return {
        "graph": graph,
        "template": {"id": template.id, "name": template.name, "version": template.version},
    }


@app.get("/api/templates/{template_id}")
def get_catalog_template(
    template_id: str,
    principal: Principal = Depends(require("read")),
    accept_language: str | None = Header(default=None),
) -> dict[str, Any]:
    """返回模板完整元数据（含 graph），未知 id 404（04 §5.10；用户模板 docs/85）；
    内置 name/description 按 Accept-Language 本地化（docs/70）。"""
    template = get_template(template_id)
    if template is not None:
        return localize_template(
            {**template.model_dump(), "source": "catalog", "deletable": False},
            resolve_locale(accept_language),
        )
    user_template = services_for(principal).user_templates.get(template_id)
    if user_template is None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    return {**user_template.model_dump(), "source": "user", "deletable": True}


class TemplateImportRequest(BaseModel):
    package: dict[str, Any] | None = None
    url: str | None = None


@app.get("/api/templates/{template_id}/export")
def export_template_package(
    template_id: str,
    principal: Principal = Depends(require("read")),
) -> Response:
    """导出模板包 JSON（打包 ZM）：内置/用户模板均可，read 档。

    格式 atlas-template-v1：{format, meta: {name, description, tags, category}, graph}；
    attachment 文件名 {id}.atlas-template.json；未知 id 404。
    """
    template = get_template(template_id)
    if template is not None:
        meta = {
            "name": template.name,
            "description": template.description,
            "tags": template.tags,
            "category": template.category,
        }
        graph = template.graph
    else:
        user = services_for(principal).user_templates.get(template_id)
        if user is None:
            raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
        meta = {
            "name": user.name,
            "description": user.description,
            "tags": user.tags,
            "category": user.category,
        }
        graph = user.graph
    package = {"format": "atlas-template-v1", "meta": meta, "graph": graph}
    filename = f"{template_id}.atlas-template.json"
    return Response(
        content=json.dumps(package, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/templates/import", status_code=201)
def import_template_package(
    body: TemplateImportRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """导入模板包（打包 ZM）：body 二选一——package 直接包 / url 远程拉取。

    远程拉取：仅 https、先过 EgressGuard（与 httpapi 同一份策略/env）、
    10s 超时、不跟随重定向、响应体 ≤256KB；meta/graph 校验同创建端点。
    """
    if (body.package is None) == (body.url is None):
        raise HTTPException(status_code=422, detail="必须且只能提供 package 或 url 之一")
    if body.url is not None:
        if not body.url.startswith("https://"):
            raise HTTPException(status_code=422, detail="仅支持 https 远程导入")
        try:
            _template_import_egress.check(body.url)
            with httpx.Client(follow_redirects=False) as client:
                response = client.get(body.url, timeout=_TEMPLATE_IMPORT_TIMEOUT)
        except (EgressDenied, httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"远程模板拉取失败：{exc}") from exc
        if response.status_code >= 400:
            raise HTTPException(
                status_code=422, detail=f"远程模板拉取失败：HTTP {response.status_code}"
            )
        if len(response.content) > _TEMPLATE_IMPORT_MAX_BYTES:
            raise HTTPException(status_code=422, detail="远程模板包超过 256KB 上限")
        try:
            raw = response.json()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="远程内容不是合法 JSON") from exc
    else:
        raw = body.package or {}
    if raw.get("format") != "atlas-template-v1":
        raise HTTPException(status_code=422, detail="不认识的模板包格式（需 atlas-template-v1）")
    meta = raw.get("meta")
    if not isinstance(meta, dict):
        raise HTTPException(status_code=422, detail="模板包缺少 meta")
    name = str(meta.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="模板名称不能为空")
    if not 1 <= len(name) <= 60:
        raise HTTPException(status_code=422, detail="模板名称长度须在 1-60 字符之间")
    description = str(meta.get("description", "")).strip()
    if len(description) > 200:
        raise HTTPException(status_code=422, detail="模板描述长度须在 200 字符以内")
    tags_raw = meta.get("tags", [])
    if not isinstance(tags_raw, list) or len(tags_raw) > 8:
        raise HTTPException(status_code=422, detail="标签须为列表且不超过 8 个")
    tags = [str(tag).strip() for tag in tags_raw]
    for tag in tags:
        if not 1 <= len(tag) <= 20:
            raise HTTPException(status_code=422, detail="标签长度须在 1-20 字符之间")
    category = str(meta.get("category", "")).strip()
    if len(category) > 30:
        raise HTTPException(status_code=422, detail="分类长度须在 30 字符以内")
    graph = raw.get("graph")
    if not isinstance(graph, dict):
        raise HTTPException(status_code=422, detail="模板包缺少 graph")
    parse_graph(graph)
    template = services_for(principal).user_templates.add(
        name=name, description=description, tags=tags, category=category, graph=graph
    )
    return {**template.model_dump(), "source": "user", "deletable": True}


@app.get("/api/alert-rule-templates")
def list_alert_rule_templates(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出内置告警规则模板（docs/59 F-1；只读代码常量，列表投影不含 config，不受 reset 影响）。"""
    return {
        "items": [
            {
                "id": tpl.id,
                "name": tpl.name,
                "description": tpl.description,
                "tags": tpl.tags,
            }
            for tpl in list_rule_templates()
        ]
    }


@app.get("/api/alert-rule-templates/{template_id}")
def get_alert_rule_template(
    template_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """返回告警规则模板完整元数据（含可直接 PUT rules 的 config），未知 id 404（docs/59 F-1）。"""
    tpl = get_rule_template(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"告警规则模板不存在：{template_id}")
    return tpl.model_dump()


@app.get("/api/cards")
def list_catalog_cards(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出内置交互卡片目录（M8；只读代码常量，不受 reset 影响，12 §3.11）。"""
    return {"items": [card.model_dump() for card in list_cards()]}


@app.get("/api/approvals/{token}/card")
def render_approval_card(
    token: str,
    channel: Literal["web", "im", "email"] = "web",
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """审批卡片按渠道渲染（M8，viewer+）；只读，不产生决策副作用（防邮件预取）。

    未知 token 404、该审批未配卡片 404、非法 channel 由 FastAPI 判 422。
    渲染上下文取挂起时快照（中断恢复后从帧重建）。
    """
    broker = services_for(principal).approval_broker
    pending = broker.get(token)
    if pending is None:
        raise HTTPException(status_code=404, detail=f"审批请求不存在或已清理：{token}")
    card_template_id = pending.get("cardTemplateId")
    if not card_template_id:
        raise HTTPException(status_code=404, detail="该审批请求未配置交互卡片")
    card = get_card(card_template_id)
    if card is None:  # 理论不发生：编译期已校验目录命中
        raise HTTPException(status_code=404, detail=f"交互卡片不存在：{card_template_id}")
    try:
        return render_card(
            card,
            broker.get_card_context(token),
            token=token,
            channel=channel,
            approver=pending.get("approver", ""),
            timeout_seconds=pending.get("timeoutSeconds"),
        )
    except CardRenderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/recordings", status_code=201)
def create_recording(
    request: RecordingCreateRequest, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """录制用例入库：按 graph_id 取已保存图原始 JSON 作快照，不重新执行（04 §5.11）。"""
    services = services_for(principal)
    raw = services.graph_store.get(request.graph_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{request.graph_id}")

    def _fetch_subgraph_raw(ref: str):
        # 与 _tenant_graph_resolver 同口径：graphId 可为 `graph-7@3` 钉版。
        ref_id, _, version = ref.partition("@")
        release_version = int(version) if version else None
        return services.graph_store.get(ref_id, release_version)

    subgraphs = collect_subgraph_snapshots(raw, fetch_raw=_fetch_subgraph_raw)
    case = services.recording_store.add(
        name=request.name,
        graph_id=request.graph_id,
        graph=raw,
        inputs=request.inputs,
        steps=request.steps,
        status=request.status,
        subgraphs=subgraphs,
        rng_seed=request.rng_seed,
    )
    return case.model_dump()


@app.get("/api/recordings")
def list_recordings(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """录制用例列表投影（不含 graph/steps）；按租户分区（04 §5.14）。"""
    return {
        "items": [
            {
                "id": case.id,
                "name": case.name,
                "graph_id": case.graph_id,
                "node_count": len(case.graph.get("nodes", [])),
                "step_count": len(case.steps),
                "status": case.status,
                "created_at": case.created_at,
            }
            for case in services_for(principal).recording_store.list()
        ]
    }


@app.get("/api/recordings/{case_id}")
def get_recording(
    case_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    case = services_for(principal).recording_store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")
    return case.model_dump()


@app.put("/api/recordings/{case_id}")
def update_recording(
    case_id: str,
    request: RecordingUpdateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """编辑用例元信息（docs/28 §2.3）：仅 name/inputs 可改，其余录制事实不可改。

    字段缺省不改；name 空串/超长、inputs 非对象 → 422；用例不存在 → 404。
    """
    services = services_for(principal)
    updated = services.recording_store.update_meta(
        case_id, name=request.name, inputs=request.inputs
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")
    return updated.model_dump()


@app.delete("/api/recordings/{case_id}")
def delete_recording(
    case_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, bool]:
    if not services_for(principal).recording_store.delete(case_id):
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")
    return {"deleted": True}


@app.post("/api/recordings/{case_id}/replay")
def replay_recording(
    case_id: str,
    payload: ReplayRequest | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """回放冻结快照：标准 run_graph + 审批决策预置，比对操作序列与逐节点产出。

    可选 body（docs/28 §2.2/§2.3）：``mock_tools=true`` 以录制工具产出作桩，回放不
    触达适配器（隔离外部系统；发布门禁不接 mock）；``inputs_override`` 顶层键浅合并进
    用例 inputs（一次性入参参数化，不落库）。响应纯超集加 ``mocked_tools``（未启用为 []）。

    回放期异常（如快照内 subgraph 引用的 graphId 已被 reset 删除）折叠为
    replay_status="failed"/matches=false，不抛 500（04 §5.11，06 §6.9）。
    """
    services = services_for(principal)
    case = services.recording_store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")

    tool_mocks: dict[str, Any] | None = None
    mocked_tools: list[str] = []
    condition_script: Any | None = None
    mocked_conditions: list[str] = []
    if payload is not None and payload.mock_tools:
        tool_mocks, mocked_tools = build_tool_mocks(case)
        condition_script, mocked_conditions = build_condition_script(case)

    try:
        graph = parse_graph(case.graph)
        emit, take_steps = collect_steps()
        anchor, clock_note = clock_anchor(case)
        seed, rng_note = seed_anchor(case)
        inputs = dict(case.inputs or {})
        if payload is not None and payload.inputs_override:
            # 顶层键浅合并（dict 值整体替换）；一次性覆写，不修改已入库用例。
            inputs = {**inputs, **payload.inputs_override}
        presets = preset_approvals(case.steps, subgraphs=case.subgraphs)
        if presets:
            approvals = dict(inputs.get("approvals") or {})
            approvals.update(presets)
            inputs["approvals"] = approvals
        # 打包 ZJ（D47）：event wait 预置跨边界——从 baseline 抽 {node_id: payload}
        # （子图内路径限定 "sub-1/wait-1"），回放不真挂起；显式 inputs 优先。
        wait_presets = preset_wait_events(case.steps, subgraphs=case.subgraphs)
        if wait_presets:
            wait_events = dict(inputs.get("waitEvents") or {})
            wait_events.update(wait_presets)
            inputs["waitEvents"] = wait_events
        result = run_graph(
            graph,
            inputs=inputs,
            registry=_runtime_registry(services),
            approval_broker=services.approval_broker,
            event_wait_broker=services.event_wait_broker,
            graph_id=f"replay-{case.id}",
            emit=emit,
            graph_resolver=inline_first_resolver(
                case.subgraphs, _tenant_graph_resolver(services)
            ),
            now_override=anchor,
            rng_seed=seed,
            tool_mocks=tool_mocks,
            condition_classifier=condition_script,
            tenant_id=principal.tenant_id,
        )
        replay_steps = take_steps()
        tools_by_node = {
            node.id: (node.config.get("tool") if node.type == "tool_call" else None)
            for node in graph.nodes
        }
        report = compare_recording(
            case.steps,
            replay_steps,
            tools_by_node=tools_by_node,
            baseline_status=case.status,
            replay_status=result["status"],
            subgraphs=case.subgraphs,
        )
        if clock_note:
            report["clock_note"] = clock_note
        if rng_note:
            report["rng_seed_note"] = rng_note
        report["mocked_tools"] = mocked_tools
        report["mocked_conditions"] = mocked_conditions
        return report
    except Exception as exc:  # 回放失败折叠为报告而非 500
        return {
            "matches": False,
            "baseline_status": case.status,
            "replay_status": "failed",
            "mocked_tools": mocked_tools,
            "mocked_conditions": mocked_conditions,
            "steps": [
                {
                    "node_id": step.node_id,
                    "match": False,
                    "note": f"回放执行异常，未取得该节点产出：{type(exc).__name__}: {exc}",
                }
                for step in case.steps
            ],
        }


@app.post("/api/graphs/{graph_id}/compile", response_model=CompileResponse)
def compile_saved_graph(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> CompileResponse:
    services = services_for(principal)
    graph = _load_graph_or_404(services, graph_id, (payload or {}).get("releaseVersion"))
    compile_graph(graph, graph_id=graph_id, graph_resolver=_tenant_graph_resolver(services))

    incoming = {edge.target for edge in graph.edges}
    outgoing = {edge.source for edge in graph.edges}
    return CompileResponse(
        id=graph_id,
        nodes=[{"id": node.id, "type": node.type, "name": node.name} for node in graph.nodes],
        edges=[{"id": edge.id, "source": edge.source, "target": edge.target} for edge in graph.edges],
        entrypoints=[node.id for node in graph.nodes if node.id not in incoming],
        terminals=[node.id for node in graph.nodes if node.id not in outgoing],
    )


def _load_graph_or_404(
    services: TenantServices, graph_id: str, release_version: int | None = None
):
    raw = services.graph_store.get(graph_id, release_version)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return parse_graph(raw)


def _graph_version(graph_id: str, release_version: int | None) -> str:
    """M10 graphVersion 标注（与 M6 钉版形态一致，SUBSCRIPT_KEY='@'）：
    发布版 `graphId@<releaseVersion>`（int），草稿/未发布 `graphId@draft`。"""
    if release_version is not None:
        return f"{graph_id}@{release_version}"
    return f"{graph_id}@draft"


def _resolve_event_version(
    services: TenantServices, principal: Principal, graph_id: str, payload: dict[str, Any]
) -> tuple[int | None, int | None]:
    """M9 入站事件路由（03 `route_decision`）：返 (加载用 releaseVersion, RunRecord.resolved_version)。

    - 无 event：旧行为（手动 releaseVersion 或草稿），resolved_version 恒 None，零回归；
    - 带 event：经 RoutingStore 三段分桶解析钉住的发布版（tenant 取 Principal 不取 body）；
      event 与 releaseVersion 同传 422（语义冲突）；无任何发布版可路由 409。
    """
    event_raw = payload.get("event")
    if event_raw is None:
        return payload.get("releaseVersion"), None
    if payload.get("releaseVersion") is not None:
        raise HTTPException(status_code=422, detail="event 与 releaseVersion 不可同时指定（入站路由与手动钉版互斥）")
    if payload.get("debug") is not None:
        raise HTTPException(status_code=422, detail="入站事件运行不支持单步调试（debug 仅用于草稿手动运行）")
    if not isinstance(event_raw, dict):
        raise HTTPException(status_code=422, detail="event 必须是对象：{channel?, payload?}")
    try:
        event = TriggerEvent.model_validate(event_raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"event 非法：{exc.errors()[0]['msg']}") from exc
    version, _segment = services.routing_store.resolve(
        graph_id, tenant=principal.tenant_id, event=event
    )
    if version is None:
        raise HTTPException(status_code=409, detail="图尚未发布，入站事件无版本可路由")
    return version, version


def _rollout_config_or_422(body: Any) -> RolloutConfig:
    """PUT rollout body → RolloutConfig；非法形状聚合为中文 422（不泄漏 pydantic 英文堆栈）。"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="rollout 配置必须是 JSON 对象")
    try:
        return RolloutConfig.model_validate(body)
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"])
            messages.append(f"{loc}：{error['msg']}" if loc else error["msg"])
        raise HTTPException(status_code=422, detail="灰度配置非法：" + "；".join(messages)) from exc


def _rollout_projection(state: RolloutState) -> dict[str, Any]:
    """GET rollout 投影（camelCase；config 未配置为 null）。"""
    return {
        "graphId": state.graph_id,
        "status": state.status,
        "config": state.config.model_dump() if state.config is not None else None,
        "stable": state.stable,
        "candidate": state.candidate,
        "startedAt": state.started_at,
        "rolledBackAt": state.rolled_back_at,
        "rollbackReason": state.rollback_reason,
        "rollbackActor": state.rollback_actor,
        "traffic": state.traffic,
    }


def _validate_debug(graph, debug: Any) -> list[dict[str, Any]]:
    """校验 /run/stream 的 debug.breakpoints，返回规范化列表；错误聚合成中文 422。"""
    if not isinstance(debug, dict):
        raise HTTPException(status_code=422, detail="debug 必须为对象：{breakpoints: [...]}")
    raw_points = debug.get("breakpoints", [])
    if not isinstance(raw_points, list):
        raise HTTPException(status_code=422, detail="debug.breakpoints 必须为数组")
    node_ids = {node.id for node in graph.nodes}
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, point in enumerate(raw_points):
        if not isinstance(point, dict) or not isinstance(point.get("node_id"), str):
            errors.append(f"第 {index + 1} 个断点缺少 node_id 字符串")
            continue
        node_id = point["node_id"]
        if node_id not in node_ids:
            errors.append(f"断点节点不存在：{node_id}")
        expression = point.get("expression")
        if expression is not None:
            if not isinstance(expression, str):
                errors.append(f"断点 {node_id} 的 expression 必须为字符串")
                continue
            if expression.strip():
                errors.extend(
                    f"断点 {node_id} 表达式：{message}"
                    for message in validate_expression(expression)
                )
        # B 包（docs/27 §4.2）：hitCount 正整数、logMessage 字符串（非空即日志断点）。
        hit_count = point.get("hitCount")
        if hit_count is not None and (
            not isinstance(hit_count, int)
            or isinstance(hit_count, bool)
            or hit_count < 1
        ):
            errors.append(f"断点 {node_id} 的 hitCount 必须为正整数")
        log_message = point.get("logMessage")
        if log_message is not None and not isinstance(log_message, str):
            errors.append(f"断点 {node_id} 的 logMessage 必须为字符串")
        # docs/28 §3.2：onException 布尔（异常断点）；bool 是 int 子类须先判 bool。
        on_exception = point.get("onException", False)
        if not isinstance(on_exception, bool):
            errors.append(f"断点 {node_id} 的 onException 必须为布尔值")
            on_exception = False
        normalized.append(
            {
                "node_id": node_id,
                "expression": expression,
                "hitCount": hit_count if isinstance(hit_count, int) and hit_count > 0 else None,
                "logMessage": (
                    log_message
                    if isinstance(log_message, str) and log_message.strip()
                    else None
                ),
                "onException": on_exception,
            }
        )
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors))
    return normalized


def _tool_call_from_event(event: dict[str, Any]) -> dict[str, Any]:
    """docs/28 §4.1 ⑧：tool_metric SSE/采集事件 → RunRecord.tool_calls 载荷（dict，pydantic 转模型）。"""
    return {
        "node_id": event["node_id"],
        "tool": event.get("tool", ""),
        "duration_ms": float(event.get("duration_ms", 0.0)),
        "action_status": event["action_status"],
        "error_code": event.get("error_code"),
    }


@app.post("/api/graphs/{graph_id}/run", response_model=RunGraphResponse)
def run_saved_graph(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> RunGraphResponse:
    services = services_for(principal)
    body = payload or {}
    release_version, resolved_version = _resolve_event_version(services, principal, graph_id, body)
    graph = _load_graph_or_404(services, graph_id, release_version)
    event_payload = (body.get("event") or {}).get("payload") or {}
    if body.get("debug") is not None:
        raise HTTPException(status_code=422, detail="单步调试仅支持流式运行 /run/stream")
    graph_view = {"nodes": [{"id": node.id, "type": node.type} for node in graph.nodes]}
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    monitoring = services.monitoring
    run_id = uuid.uuid4().hex
    tracer = Tracer(
        graph_id=graph_id, graph_version=_graph_version(graph_id, release_version)
    )
    services.run_store.begin(run_id=run_id, graph_id=graph_id, mode="sync")
    frame_sink = _frame_sink_for(principal.tenant_id, services.run_store, run_id)
    # docs/28 §4.1 ⑧：同步入口无 SSE，构造同形收集 emit（只收顶层 tool_metric，其余忽略）。
    tool_calls: list[dict[str, Any]] = []

    def _metric_collect(event: dict[str, Any]) -> None:
        if event.get("type") == "tool_metric" and not event.get("subgraphPath"):
            tool_calls.append(_tool_call_from_event(event))

    # 打包 O（docs/69 §1 D-1）：同步入口此前从不登记取消句柄（只有 :3115 的流式路径注册），
    # 在途急停恒落 409「运行已结束」。与流式同构——请求线程注册，finally 注销覆盖全部出口。
    cancellation_broker = services.cancellation_broker
    cancel_event = cancellation_broker.register(run_id)
    try:
        result = run_graph(
            graph,
            inputs=body.get("inputs"),
            registry=_runtime_registry(services),
            approval_broker=services.approval_broker,
            event_wait_broker=services.event_wait_broker,
            approval_notifier=EmailApprovalNotifier(
                services.message_service, _PUBLIC_URL, principal.tenant_id
            ),
            graph_id=graph_id,
            graph_resolver=_tenant_graph_resolver(services),
            emit=_metric_collect,
            frame_sink=frame_sink,
            resume_claim=_resume_claim_for(),
            tracer=tracer,
            graph_version=tracer.graph_version,
            is_cancelled=cancel_event.is_set,
            _block_subgraph_suspend=(STORAGE_BACKEND == "pg"),
            tenant_id=principal.tenant_id,
        )
    except RunSuperseded as exc:
        # docs/62 §2 D-4：输家停止驱动——不写 run 终态、不记监控、不进门控评估，
        # 帧与终态都归赢家；本进程如实返回"仍在挂起"的形状（刻意不新增 run 状态/错误码）。
        logger.info("同步运行让位：%s", exc)
        return RunGraphResponse(
            id=graph_id,
            status="suspended",
            outputs={},
            trace=[f"{exc.node_id}: superseded by another process"],
        )
    except RunCancelled as exc:
        # 打包 O：同步入口的协作式急停与流式同形——用户主动 ⇒ 记 cancelled 且
        # **不调 evaluate_after_run**（不算失败、不进 M9 灰度门控，与流式 :3203 逐条对齐）。
        # 必须排在 except Exception 之前（RunCancelled 是 Exception 子类）。
        services.run_store.finish(run_id=run_id, status="cancelled")
        monitoring.record_run(
            graph_id=graph_id,
            mode="sync",
            status="cancelled",
            started_at=started_at,
            duration_ms=(time.monotonic() - started) * 1000,
            nodes=[],
            trace_id=tracer.trace_id,
            resolved_version=resolved_version,
            tool_calls=tool_calls,
            spans=tracer.to_tree(),
        )
        return RunGraphResponse(
            id=graph_id,
            status="cancelled",
            outputs={},
            trace=[f"{exc.node_id}: cancelled by user"],
        )
    except Exception as exc:
        services.run_store.finish(
            run_id=run_id, status="failed", error=f"{type(exc).__name__}: {exc}"
        )
        record = monitoring.record_run(
            graph_id=graph_id,
            mode="sync",
            status="error",
            started_at=started_at,
            duration_ms=(time.monotonic() - started) * 1000,
            nodes=[],
            error=f"{type(exc).__name__}: {exc}",
            trace_id=tracer.trace_id,
            resolved_version=resolved_version,
            tool_calls=tool_calls,
            spans=tracer.to_tree(),
        )
        evaluate_after_run(services, record)  # M9：异常运行同样计入 candidate 门控
        raise
    finally:
        # 打包 O：句柄必须随请求退出而注销——留着会让「已结束再取消」从 409 翻成 200
        # （tests/test_run_cancel.py::test_cancel_finished_run_returns_409 守着这条不变量）。
        cancellation_broker.unregister(run_id)
    services.run_store.finish(
        run_id=run_id, status="completed",
        outputs=result["outputs"], trace=result["trace"],
    )
    record = monitoring.record_run(
        graph_id=graph_id,
        mode="sync",
        status="completed",
        started_at=started_at,
        duration_ms=(time.monotonic() - started) * 1000,
        nodes=extract_node_results(graph_view, result["outputs"]),
        trace_id=tracer.trace_id,
        resolved_version=resolved_version,
        business=extract_business(graph_view, result["outputs"], event_payload=event_payload),
        tool_calls=tool_calls,
        spans=result["traceTree"],
    )
    evaluate_after_run(services, record)  # M9：灰度门控越阈自动回滚
    return RunGraphResponse(id=graph_id, **result)


@app.post("/api/graphs/{graph_id}/run/stream")
def run_saved_graph_stream(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> StreamingResponse:
    """SSE：node_start/node_end/run_end 实时推送到画布（08 §7.3 验收 5）。

    run_graph 在后台线程执行、事件经 queue 实时下发（真流式）；
    human_approval 节点依赖 node_start 在阻塞前到达，前端凭 token 调决策端点放行。
    租户服务 bundle 在请求线程解析后显式透传 worker（06 §6.12，无线程上下文变量）。
    """
    services = services_for(principal)
    body = payload or {}
    # M9：event 在请求线程解析（Principal 不进 worker），钉住的版本随闭包透传后台线程。
    release_version, resolved_version = _resolve_event_version(services, principal, graph_id, body)
    graph = _load_graph_or_404(services, graph_id, release_version)
    event_payload = (body.get("event") or {}).get("payload") or {}
    inputs = body.get("inputs")
    debug = body.get("debug")
    graph_view = {"nodes": [{"id": node.id, "type": node.type} for node in graph.nodes]}
    debug_session = None
    if debug is not None:
        breakpoints = _validate_debug(graph, debug)
        debug_session = services.debug_broker.create(graph_id=graph_id, breakpoints=breakpoints)
        if any(
            node.type == "wait" and node.config.get("waitType") == "event"
            for node in graph.nodes
        ):
            raise HTTPException(status_code=422, detail="事件等待不支持单步调试")

    # worker 启动前固定当前租户的分区对象，避免跨租户串用
    registry = _runtime_registry(services)
    approval_broker = services.approval_broker
    approval_notifier = EmailApprovalNotifier(
        services.message_service, _PUBLIC_URL, principal.tenant_id
    )
    graph_resolver = _tenant_graph_resolver(services)
    monitoring = services.monitoring
    run_store = services.run_store
    run_id = uuid.uuid4().hex
    frame_sink = _frame_sink_for(principal.tenant_id, run_store, run_id)
    # B 包（docs/27 §4.1）：worker 启动前登记取消事件（请求线程，确保端点可达时句柄已在）。
    cancellation_broker = services.cancellation_broker
    cancel_event = cancellation_broker.register(run_id)
    # M10：真实运行（非 debug）建 tracer 并显式透传 worker（06 §6.12 后台线程不靠全局）；
    # debug 单步会话显式传 None 不埋点（04 §5.13/§5.15，SSE 帧保持旧形状）。
    tracer: Tracer | None = (
        Tracer(graph_id=graph_id, graph_version=_graph_version(graph_id, release_version))
        if debug is None
        else None
    )

    def event_stream():
        events: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def emit(event: dict[str, Any]) -> None:
            events.put(event)

        debug_controller = (
            DebugController(debug_session, emit, is_cancelled=cancel_event.is_set)
            if debug_session is not None
            else None
        )
        # debug 会话不是真实运行，全程不埋点（04 §5.13）
        monitored = debug_controller is None
        collected: dict[str, Any] = {}
        tool_calls: list[dict[str, Any]] = []

        def recording_emit(event: dict[str, Any]) -> None:
            # A 包（docs/27 §10.1）：监控/运行产出只收顶层 node_end；带 subgraphPath 的
            # 子图内部节点不进 collected（子图结果归在 subgraph 节点），但仍转发 SSE 上屏。
            if event.get("type") == "node_end" and not event.get("subgraphPath"):
                collected[event["node_id"]] = event.get("output")
            # docs/28 §4.1 ⑧：另册收集顶层 tool_metric（子层已被命名空间 wrapper 白名单吞掉）。
            elif event.get("type") == "tool_metric" and not event.get("subgraphPath"):
                tool_calls.append(_tool_call_from_event(event))
            emit(event)

        def worker() -> None:
            started_at = datetime.now(timezone.utc).isoformat()
            started = time.monotonic()
            if monitored:
                run_store.begin(run_id=run_id, graph_id=graph_id, mode="stream")
            try:
                result = run_graph(
                    graph,
                    inputs=inputs,
                    registry=registry,
                    approval_broker=approval_broker,
                    event_wait_broker=services.event_wait_broker,
                    approval_notifier=approval_notifier,
                    graph_id=graph_id,
                    emit=recording_emit if monitored else emit,
                    graph_resolver=graph_resolver,
                    debug_controller=debug_controller,
                    frame_sink=frame_sink,
                    resume_claim=_resume_claim_for(),
                    tracer=tracer,
                    graph_version=tracer.graph_version if tracer is not None else None,
                    is_cancelled=cancel_event.is_set,
                    _block_subgraph_suspend=(STORAGE_BACKEND == "pg"),
                    tenant_id=principal.tenant_id,
                )
                if monitored:
                    run_store.finish(
                        run_id=run_id, status="completed",
                        outputs=result["outputs"], trace=result["trace"],
                    )
                    record = monitoring.record_run(
                        graph_id=graph_id,
                        mode="stream",
                        status="completed",
                        started_at=started_at,
                        duration_ms=(time.monotonic() - started) * 1000,
                        nodes=extract_node_results(graph_view, result["outputs"]),
                        trace_id=tracer.trace_id if tracer is not None else "",
                        resolved_version=resolved_version,
                        business=extract_business(
                            graph_view, result["outputs"], event_payload=event_payload
                        ),
                        tool_calls=tool_calls,
                        spans=result["traceTree"],
                    )
                    evaluate_after_run(services, record)
                events.put({"__result__": result})
            except RunSuperseded as exc:
                # docs/62 §2 D-4：输家零终态写——不 finish、不 record_run、不清帧，
                # 只发让位帧让前端停手（赢家的流会继续出结果）。
                logger.info("流式运行让位：%s", exc)
                events.put({"__superseded__": exc.node_id})
            except DebugStopped as exc:
                events.put({"__stopped__": exc.node_id})
            except RunCancelled as exc:
                # 协作式急停：普通流记 cancelled（用户主动，不进灰度门控评估、不算失败）。
                # 必须排在 except Exception 前（RunCancelled 是 Exception 子类）。
                if monitored:
                    run_store.finish(run_id=run_id, status="cancelled")
                    monitoring.record_run(
                        graph_id=graph_id,
                        mode="stream",
                        status="cancelled",
                        started_at=started_at,
                        duration_ms=(time.monotonic() - started) * 1000,
                        nodes=extract_node_results(graph_view, collected),
                        trace_id=tracer.trace_id if tracer is not None else "",
                        resolved_version=resolved_version,
                        tool_calls=tool_calls,
                        spans=tracer.to_tree() if tracer is not None else None,
                    )
                events.put({"__cancelled__": exc.node_id})
            except Exception as exc:  # 运行期异常经 SSE error 帧下发，不静默吞线程
                if monitored:
                    run_store.finish(
                        run_id=run_id, status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    record = monitoring.record_run(
                        graph_id=graph_id,
                        mode="stream",
                        status="error",
                        started_at=started_at,
                        duration_ms=(time.monotonic() - started) * 1000,
                        nodes=extract_node_results(graph_view, collected),
                        error=f"{type(exc).__name__}: {exc}",
                        trace_id=tracer.trace_id if tracer is not None else "",
                        resolved_version=resolved_version,
                        tool_calls=tool_calls,
                        spans=tracer.to_tree() if tracer is not None else None,
                    )
                    evaluate_after_run(services, record)
                err_meta = runtime_error_meta(exc)
                events.put(
                    {
                        "__error__": {
                            "message": f"{type(exc).__name__}: {exc}",
                            "code": err_meta["errorCode"],
                            "params": err_meta["errorParams"],
                        }
                    }
                )
            finally:
                cancellation_broker.unregister(run_id)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            event = events.get()
            if event is None:
                continue
            if "__result__" in event:
                # M10：终帧为 run_end 超集（traceId/spanId/graphVersion）；完整 traceTree
                # 只留在进程内返回值，不进 SSE（04 §5.15）。
                result_payload = {
                    key: value
                    for key, value in event["__result__"].items()
                    if key != "traceTree"
                }
                if tracer is not None:
                    result_payload.setdefault("spanId", tracer.root.span_id)
                    if tracer.graph_version is not None:
                        result_payload.setdefault("graphVersion", tracer.graph_version)
                yield (
                    f"event: result\ndata: "
                    f"{json.dumps({'id': graph_id, **result_payload}, ensure_ascii=False)}\n\n"
                )
                break
            if "__stopped__" in event:
                yield (
                    "event: stopped\ndata: "
                    + json.dumps(
                        {"type": "stopped", "node_id": event["__stopped__"],
                         "reason": "user_stop"},
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
                break
            if "__superseded__" in event:
                yield (
                    "event: superseded\ndata: "
                    + json.dumps(
                        {
                            "type": "superseded",
                            "node_id": event["__superseded__"],
                            "reason": "resumed_by_other_process",
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
                break
            if "__cancelled__" in event:
                yield (
                    "event: cancelled\ndata: "
                    + json.dumps(
                        {
                            "type": "cancelled",
                            "node_id": event["__cancelled__"],
                            "reason": "user_cancel",
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
                break
            if "__error__" in event:
                yield f"event: error\ndata: {json.dumps({'detail': event['__error__']}, ensure_ascii=False)}\n\n"
                break
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/runs")
def list_runs(
    status: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """本租户运行列表（新→旧；status 缺省=全部）（M5b，docs/24 §4）。"""
    if status is not None and status not in {
        "running", "suspended", "completed", "failed", "interrupted", "cancelled",
    }:
        raise HTTPException(status_code=422, detail="非法的 status 过滤值")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 200 之间")
    return {"items": services_for(principal).run_store.list(status=status, limit=limit)}


@app.get("/api/runs/{run_id}")
def get_run(
    run_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """单运行详情：状态/产出/轨迹/挂起信息；跨租户或不存在 → 404（docs/24 §4）。"""
    run = services_for(principal).run_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    return run


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """协作式急停（docs/27 §4.1）：置位本租户该 run 的取消事件，下一节点边界生效，
    不在 wait/approval/tool 阻塞中点强杀。运行中（含调试流）200，重复取消幂等 200；
    已结束且无注册句柄 409；run 不存在/跨租户 404。"""
    services = services_for(principal)
    if services.cancellation_broker.cancel(run_id):
        return {"run_id": run_id, "cancelled": True}
    if services.run_store.get(run_id) is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    raise HTTPException(status_code=409, detail="运行已结束，无法取消")


@app.get("/api/tasks")
def list_tasks(
    state: str | None = None,
    assignee: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """本租户任务信封列表（新→旧；state/assignee 可过滤）（M7，docs/20 §4.2 / 12 任务端点）。"""
    if state is not None and state not in {
        "pending", "accepted", "running", "done", "failed", "timeout",
    }:
        raise HTTPException(status_code=422, detail="非法的 state 过滤值")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 200 之间")
    tasks = services_for(principal).task_store.list(state=state)
    if assignee:
        tasks = [task for task in tasks if task.assignee == assignee]
    return {"items": [task.model_dump() for task in tasks[:limit]]}


@app.get("/api/tasks/{task_id}")
def get_task(
    task_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """单任务信封详情；跨租户或不存在 → 404（M7）。"""
    task = services_for(principal).task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task.model_dump()


class ApprovalDecisionRequest(BaseModel):
    # M8：旧 {decision, comment?} 仍可用；命中卡片可提交 {actionId, form?}（decision/comment 可省）。
    decision: Literal["approved", "rejected"] | None = None
    comment: str = Field(default="", max_length=500)
    action_id: str | None = Field(default=None, alias="actionId")
    form: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


@app.get("/api/approvals")
def list_approvals(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出当前租户 pending 的人工审批请求（进程内 broker，重启即失）。"""
    return {"items": services_for(principal).approval_broker.list_pending()}


def _parse_wait_event_key(body: dict[str, Any]) -> str:
    raw = body.get("eventKey")
    event_key = raw.strip() if isinstance(raw, str) else ""
    if not valid_event_key(event_key):
        raise HTTPException(
            status_code=422,
            detail={"code": "WAIT_EVENT_KEY_INVALID",
                    "message": "eventKey 必须是 1-128 位字母、数字及 :_- 组合"},
        )
    return event_key


def _parse_wait_payload(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("payload", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "WAIT_EVENT_PAYLOAD_NOT_OBJECT",
                    "message": "payload 必须是 JSON 对象"},
        )
    if len(raw) > MAX_WAIT_PAYLOAD_KEYS:
        raise HTTPException(
            status_code=422,
            detail={"code": "WAIT_EVENT_PAYLOAD_TOO_MANY_KEYS",
                    "message": f"payload 顶层键不能超过 {MAX_WAIT_PAYLOAD_KEYS} 个",
                    "params": {"max": MAX_WAIT_PAYLOAD_KEYS}},
        )
    serialized = json.dumps(raw, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > MAX_WAIT_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=422,
            detail={"code": "WAIT_EVENT_PAYLOAD_TOO_LARGE",
                    "message": f"payload 序列化后不能超过 {MAX_WAIT_PAYLOAD_BYTES} 字节",
                    "params": {"max": MAX_WAIT_PAYLOAD_BYTES}},
        )
    return raw


@app.get("/api/waits")
def list_waits(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出当前租户 pending 的事件等待（进程内 broker，重启即失，docs/47 §4）。"""
    return {"items": services_for(principal).event_wait_broker.list_pending()}


@app.get("/api/interruptions")
def list_interruptions(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """本租户挂起帧的只读投影（docs/76 打包 Q，解 14 D42）。

    存在的理由：`resumed_at` 此前只在启动恢复路径被读一次，崩在续跑途中的 run 除了重启
    翻日志无处可查。**本端点不重放、不清帧、不改任何状态**——`claimed_suspended` 只是把
    D36 那颗雷显形，怎么处理仍未决定。

    投影本体在 `atlas.storage.recovery.interruption_view`（MCP 面 `atlas_list_interruptions`
    锚同一份，只差 resumeToken 收窄）。
    """
    run_store = services_for(principal).run_store
    return interruption_view(
        backend=STORAGE_BACKEND,
        tenant_id=principal.tenant_id,
        engine=get_pg_backend().engine if STORAGE_BACKEND == "pg" else None,
        run_status_of=lambda run_id: (run_store.get(run_id) or {}).get("status"),
        include_resume_token=True,
    )


class InterruptionResolveRequest(BaseModel):
    action: str
    reason: str = Field(default="", max_length=500)


@app.post("/api/interruptions/{resume_token}/resolve")
def resolve_interruption(
    resume_token: str,
    body: InterruptionResolveRequest,
    http_request: Request,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """人工认领后崩溃帧的收敛（docs/96 打包 BL，解 docs/14 D36 人工收敛半边）。

    唯一动作 abandon：把「帧已认领、run 却永久停在 suspended」的卡死运行在一个
    PG 事务内置 `interrupted` 终态并删帧。**不重放**——下游 create_refund 等非
    幂等，安全重放要幂等键＋逐节点检查点（docs/14 D56）。承重的是事务谓词，
    下面的折算只读状态给错误码，不承担互斥。
    """
    if body.action != "abandon":
        _record_audit(services_for(principal), principal, http_request,
                      "interruption.resolve", 422)
        raise HTTPException(
            status_code=422,
            detail={"code": "INTERRUPTION_ACTION_UNSUPPORTED",
                    "message": f"不支持的收敛动作：{body.action}（v1 仅支持 abandon）",
                    "params": {"action": body.action}},
        )
    if STORAGE_BACKEND != "pg":
        _record_audit(services_for(principal), principal, http_request,
                      "interruption.resolve", 409)
        raise HTTPException(
            status_code=409,
            detail={"code": "INTERRUPTIONS_NOT_PERSISTED",
                    "message": "内存档挂起帧不跨进程持久化，无人工收敛能力"},
        )
    code, info = abandon_claimed_suspended_frame(
        get_pg_backend().engine,
        tenant_id=principal.tenant_id,
        token=resume_token,
    )
    status_code = 200
    if code == RESOLVE_OK:
        payload = {"resumeToken": resume_token, "runId": info.get("run_id"),
                   "resolved": "interrupted", "frameDeleted": True}
    elif code == RESOLVE_FRAME_NOT_FOUND:
        status_code = 404
        payload = {"code": "INTERRUPTION_FRAME_NOT_FOUND",
                   "message": f"挂起帧不存在或不属于当前租户：{resume_token}"}
    elif code == RESOLVE_FRAME_NOT_CLAIMED:
        status_code = 409
        payload = {"code": "INTERRUPTION_FRAME_NOT_CLAIMED",
                   "message": "该帧尚未被认领，重启恢复仍可能驱动它，不能人工了结"}
    else:
        status_code = 409
        payload = {"code": "INTERRUPTION_RUN_NOT_SUSPENDED",
                   "message": "该帧已认领但其运行不处于 suspended 状态",
                   "params": {"status": info.get("status")}}
    _record_audit(services_for(principal), principal, http_request,
                  "interruption.resolve", status_code)
    if code != RESOLVE_OK:
        raise HTTPException(status_code=status_code, detail=payload)
    return payload


@app.post("/api/waits/events")
def signal_wait_event(
    http_request: Request,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """按 eventKey 广播信号，释放同 key 全部等待；无 pending 则入排队 ring（docs/55）。

    返回 released=本次释放等待数、queued=是否因无消费者进入 per-key 排队。
    """
    data = body or {}
    event_key = _parse_wait_event_key(data)
    payload = _parse_wait_payload(data)
    services = services_for(principal)
    result = services.event_wait_broker.signal_key(event_key, payload)
    _record_audit(services, principal, http_request, "wait.signal_event", 200)
    return {"released": result["released"], "queued": result["queued"]}


@app.post("/api/waits/{token}/signal")
def signal_wait_token(
    token: str,
    http_request: Request,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """按 token 直投信号：未知/已取走 404，重复信号 409。"""
    data = body or {}
    payload = _parse_wait_payload(data)
    services = services_for(principal)
    try:
        services.event_wait_broker.signal_token(token, payload)
    except WaitTokenNotFound:
        _record_audit(services, principal, http_request, "wait.signal_token", 404)
        raise HTTPException(
            status_code=404,
            detail={"code": "WAIT_TOKEN_NOT_FOUND",
                    "message": f"等待不存在或已清理：{token}"},
        )
    except WaitAlreadySignaled:
        _record_audit(services, principal, http_request, "wait.signal_token", 409)
        raise HTTPException(
            status_code=409,
            detail={"code": "WAIT_ALREADY_SIGNALED",
                    "message": "该等待已有信号，重复提交不生效"},
        )
    _record_audit(services, principal, http_request, "wait.signal_token", 200)
    return {"token": token, "released": True}


@app.get("/api/approvals/decided")
def list_decided_approvals(
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """列出当前租户已决策的人工审批（最近处理在前，进程内、reset/重启即失）。"""
    bounded = max(1, min(int(limit), 200))
    items = services_for(principal).approval_broker.list_decided(bounded)
    return {"items": items, "limit": bounded}


@app.post("/api/approvals/{token}/decision")
def decide_approval(
    token: str,
    request: ApprovalDecisionRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    broker = services_for(principal).approval_broker
    pending = broker.get(token)
    if pending is None:
        # 跨租户 token 同样 404，不泄漏存在性（04 §5.14）
        raise HTTPException(status_code=404, detail=f"审批请求不存在或已清理：{token}")
    notifier = EmailApprovalNotifier(
        services_for(principal).message_service, _PUBLIC_URL, principal.tenant_id
    )
    return _apply_approval_decision(
        broker,
        pending,
        token,
        decision=request.decision,
        comment=request.comment,
        action_id=request.action_id,
        form=request.form,
        notifier=notifier,
        resolved_by="human",
    )


def _apply_approval_decision(
    broker: Any,
    pending: dict[str, Any],
    token: str,
    *,
    decision: Literal["approved", "rejected"] | None,
    comment: str,
    action_id: str | None,
    form: dict[str, Any] | None,
    notifier: Any = None,
    resolved_by: str = "human",
) -> dict[str, Any]:
    """登录态决策与邮件深链决策共用的唯一应用函数（docs/36 §3，防双路漂移）。"""
    if pending.get("decision") is not None:
        raise HTTPException(status_code=409, detail="该审批请求已有决策，重复提交不生效")

    resolved_action: str | None = None
    if action_id:
        # M8：卡片动作提交，服务端按 action.output 映射 decision/comment（map_action_output 唯一权威）。
        card_template_id = pending.get("cardTemplateId")
        card = get_card(card_template_id) if card_template_id else None
        if card is None:
            raise HTTPException(status_code=422, detail="该审批请求未配置交互卡片或卡片不存在")
        try:
            mapped = map_action_output(card, action_id, form)
        except CardRenderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        decision = mapped["decision"]
        comment = mapped.get("comment", "")
        resolved_action = action_id
    elif decision is None:
        raise HTTPException(
            status_code=422,
            detail="请提供 decision（approved/rejected）或卡片 actionId",
        )

    if not broker.resolve(token, decision, comment=comment, action_id=resolved_action):
        raise HTTPException(status_code=409, detail="该审批请求已有决策，重复提交不生效")
    # docs/37 §4：决策结果邮件（旁路 fail-safe，不影响响应与决策事实）。
    if notifier is not None:
        recipients = broker.get_notify_recipients(token)
        if recipients:
            try:
                notifier.notify_decided(
                    graph_id=pending.get("graph_id", ""),
                    node_id=pending.get("node_id", ""),
                    summary=pending.get("summary", ""),
                    decision=decision,
                    resolved_by=resolved_by,
                    comment=comment,
                    recipients=recipients,
                )
            except Exception as exc:  # noqa: BLE001 结果通知任何异常都不改变响应
                logger.warning("审批结果通知失败 token=%s: %s", _token_ref(token), exc)
    result: dict[str, Any] = {"token": token, "decision": decision, "resolvedBy": "human"}
    if resolved_action:
        result["actionId"] = resolved_action
    return result


_EMAIL_LINK_INVALID = "审批链接无效或已过期"


def _resolve_email_signed(signed: str) -> tuple[Any, str, str]:
    """验签 → peek 已装配租户；任何失败统一 404，不区分原因。"""
    try:
        body = _email_token_issuer.verify(signed)
    except EmailTokenError as exc:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID) from exc
    tenant_id = str(body.get("tenant", ""))
    services = tenant_registry.peek(tenant_id)
    if services is None:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID)
    if body.get("rcpt") is None:
        # docs/64 J-1b：无收件人绑定的旧式 token 一律失效（TTL 仅 1h+grace，不向后兼容）。
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID)
    return services, str(body.get("at", "")), tenant_id


def _assert_email_token_recipient(
    signed: str, approval_token: str, broker: Any
) -> None:
    """docs/64 J-1b：token 载荷 rcpt 必须属于该审批的收件人，否则 404（与验签同口径）。"""
    try:
        body = _email_token_issuer.verify(signed)
    except EmailTokenError as exc:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID) from exc
    rcpt = str(body.get("rcpt", "")).lower()
    recipients = {r.lower() for r in broker.get_notify_recipients(approval_token)}
    if rcpt not in recipients:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID)


@app.get("/api/approvals/email-view")
def email_approval_view(token: str) -> dict[str, Any]:
    """邮件深链只读视图（docs/36 §3）：无需登录、无副作用（防邮件预取）。"""
    services, approval_token, _ = _resolve_email_signed(token)
    broker = services.approval_broker
    pending = broker.get(approval_token)
    if pending is None:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID)
    _assert_email_token_recipient(token, approval_token, broker)
    now = time.time()
    remaining = max(
        0.0, float(pending.get("createdAt", 0.0)) + float(pending["timeoutSeconds"]) - now
    )
    view: dict[str, Any] = {
        "status": "resolved" if pending.get("decision") else "pending",
        "summary": pending.get("summary", ""),
        "nodeId": pending.get("node_id", ""),
        "graphId": pending.get("graph_id", ""),
        "approver": pending.get("approver", ""),
        "timeoutSeconds": pending["timeoutSeconds"],
        "createdAt": pending.get("createdAt", 0),
        "remainingSeconds": int(remaining),
    }
    if pending.get("decision"):
        view["decision"] = pending["decision"]
        view["resolvedBy"] = pending.get("resolvedBy")
    card_template_id = pending.get("cardTemplateId")
    if card_template_id:
        card = get_card(card_template_id)
        if card is not None:
            view["card"] = render_card(
                card,
                broker.get_card_context(approval_token),
                token=approval_token,
                channel="email",
                approver=pending.get("approver", ""),
                timeout_seconds=pending.get("timeoutSeconds"),
            )
    return view


class EmailDecisionRequest(BaseModel):
    token: str = Field(min_length=1)
    decision: Literal["approved", "rejected"] | None = None
    comment: str = Field(default="", max_length=500)
    action_id: str | None = Field(default=None, alias="actionId")
    form: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


@app.post("/api/approvals/email-decision")
def email_approval_decision(
    request: EmailDecisionRequest, http_request: Request
) -> dict[str, Any]:
    """邮件深链提交决策（docs/36 §3）：无需登录，与登录态端点共用决策应用函数。"""
    services, approval_token, tenant_id = _resolve_email_signed(request.token)
    broker = services.approval_broker
    pending = broker.get(approval_token)
    if pending is None:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID)
    _assert_email_token_recipient(request.token, approval_token, broker)
    notifier = EmailApprovalNotifier(
        services.message_service, _PUBLIC_URL, tenant_id
    )
    result = _apply_approval_decision(
        broker,
        pending,
        approval_token,
        decision=request.decision,
        comment=request.comment,
        action_id=request.action_id,
        form=request.form,
        notifier=notifier,
        resolved_by="email-link",
    )
    client_ip = http_request.client.host if http_request.client else ""
    try:
        services.audit_store.record(
            tenant_id=tenant_id,
            actor="email-link",
            action=f"approval.email_decision:{result['decision']}",
            status_code=200,
            path="/api/approvals/email-decision",
            ip=client_ip,
        )
    except Exception as exc:  # 审计绝不阻断决策
        logger.warning("email decision audit record failed: %s", exc)
    return result


class ResumeDebugRequest(BaseModel):
    action: Literal["step", "continue", "stop"]
    # B 包（docs/27 §4.3）：可选 globals 顶层键浅合并覆盖；stop 时忽略。
    globals: dict[str, Any] | None = None


@app.get("/api/debug")
def list_debug_pauses(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出当前租户活动调试暂停（04 §5.12；进程内，重启即失）。"""
    return {"items": services_for(principal).debug_broker.list_pending()}


@app.post("/api/debug/{token}/resume")
def resume_debug(
    token: str,
    request: ResumeDebugRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, str]:
    broker = services_for(principal).debug_broker
    session = broker.get_session(token)
    if session is None:
        raise HTTPException(status_code=404, detail=f"调试暂停不存在或已恢复：{token}")
    # 变量覆盖在 resolve（首决）前校验暂存；action=stop 忽略覆盖（§4.3）。
    if request.globals is not None and request.action != "stop":
        try:
            session.apply_overrides(token, request.globals)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not session.resolve(token, request.action):
        raise HTTPException(status_code=409, detail="该调试暂停已恢复，重复提交不生效")
    return {"token": token, "action": request.action}


@app.get("/api/monitoring/metrics")
def monitoring_metrics(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """运行指标聚合：租户内全局 + 按图 + 失败节点 Top（04 §5.13/§5.14）。"""
    return services_for(principal).monitoring.snapshot_metrics()


@app.get("/api/monitoring/runs")
def monitoring_runs(
    graph_id: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """最近运行（新→旧，默认 50、上限 200；04 §5.13；按租户分区）。
    注：本端点保留 RUN_RING_SIZE 手动门禁（docs/64 J-3b 的 le= 批不含此处——
    语义上限为环形缓冲大小，且手动校验保证 detail 为可读字符串）。"""
    if limit < 1 or limit > RUN_RING_SIZE:
        raise HTTPException(status_code=422, detail=f"limit 必须是 1-{RUN_RING_SIZE} 之间的整数")
    runs = services_for(principal).monitoring.list_runs(graph_id=graph_id, limit=limit)
    # docs/33 §4：列表投影剔除 spans（大 payload，仅 trace 端点按需返回）。
    return {
        "items": [
            {key: value for key, value in run.model_dump().items() if key != "spans"}
            for run in runs
        ]
    }


@app.get("/api/monitoring/runs/{run_id}/trace")
def monitoring_run_trace(
    run_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """docs/33 §4：单运行 span 树懒加载；历史/debug/回放无 spans 记录返 null，不存在 404。"""
    run = services_for(principal).monitoring.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"id": run.id, "trace_id": run.trace_id, "spans": run.spans}


@app.get("/api/monitoring/rules")
def monitoring_get_rules(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    return services_for(principal).monitoring.get_rules().model_dump()


@app.put("/api/monitoring/rules")
def monitoring_update_rules(
    raw: dict[str, Any], principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    """全量替换本租户规则配置；校验失败聚合为中文 422（admin only，04 §5.14）。"""
    try:
        rules = services_for(principal).monitoring.update_rules(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rules.model_dump()


def _alert_channel_payload(monitoring: Any) -> dict[str, Any]:
    channel = monitoring.get_alert_channel().model_dump()
    delivery = monitoring.get_alert_channel_delivery().model_dump()
    channel["lastDelivery"] = delivery if delivery["lastNotifiedAt"] else None
    return channel


@app.get("/api/monitoring/alert-channel")
def monitoring_get_alert_channel(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """告警外部通知配置 + 最近投递状态；从未投递 lastDelivery 为 null（docs/52）。"""
    return _alert_channel_payload(services_for(principal).monitoring)


@app.put("/api/monitoring/alert-channel")
def monitoring_update_alert_channel(
    raw: dict[str, Any], principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    """整体替换本租户告警通知配置；校验失败聚合为中文 422（admin only，docs/52）。"""
    monitoring = services_for(principal).monitoring
    try:
        monitoring.update_alert_channel(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _alert_channel_payload(monitoring)


@app.get("/api/alerts")
def list_alerts(
    status: str | None = None, principal: Principal = Depends(require("read"))
) -> dict[str, list[dict[str, Any]]]:
    """告警列表（新→旧，可按 open/acknowledged/resolved 过滤；04 §5.13；按租户分区）。"""
    if status is not None and status not in ("open", "acknowledged", "resolved"):
        raise HTTPException(
            status_code=422,
            detail="status 只允许 open、acknowledged、resolved",
        )
    alerts = services_for(principal).monitoring.list_alerts(status=status)
    return {"items": [alert.model_dump() for alert in alerts]}


@app.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    monitoring = services_for(principal).monitoring
    alert = monitoring.acknowledge_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"告警不存在：{alert_id}")
    if alert is False:
        raise HTTPException(status_code=409, detail="该告警已确认或已关闭，不能重复确认")
    return alert.model_dump()


@app.post("/api/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    monitoring = services_for(principal).monitoring
    alert = monitoring.resolve_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"告警不存在：{alert_id}")
    if alert is False:
        raise HTTPException(status_code=409, detail="该告警已关闭，重复提交不生效")
    return alert.model_dump()


def _normalize_optional_id(value: object, field: str, errors: list[str]) -> str | None:
    """静默 body 的 rule_id/graph_id：None/空白→None；非空 str 去空白；其余记 422。"""
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append(f"{field} 必须是字符串或 null")
        return None
    text = value.strip()
    return text or None


def _parse_future_expires_at(value: object, errors: list[str]) -> str | None:
    """docs/60 §4.1：expires_at 必须是合法 ISO 8601 且严格晚于当前时刻。"""
    if not isinstance(value, str) or not value.strip():
        errors.append("expires_at 必须是 ISO 8601 时间字符串")
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors.append("expires_at 必须是合法的 ISO 8601 时间")
        return None
    if parsed <= datetime.now(timezone.utc):
        errors.append("expires_at 必须是未来时刻")
    return text


@app.post("/api/monitoring/silences", status_code=201)
def create_silence(
    body: dict[str, Any], principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    """docs/33 §5.1：创建静默（administer）；duration 1-10080 分钟、reason 非空≤200。"""
    errors: list[str] = []
    rule_id = _normalize_optional_id(body.get("rule_id"), "rule_id", errors)
    graph_id = _normalize_optional_id(body.get("graph_id"), "graph_id", errors)
    duration = body.get("duration_minutes")
    if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 10080:
        errors.append("duration_minutes 必须是 1-10080 的整数")
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
        errors.append("reason 必须是非空且不超过 200 字的字符串")
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors))
    monitoring = services_for(principal).monitoring
    silence = monitoring.create_silence(
        rule_id=rule_id,
        graph_id=graph_id,
        duration_minutes=duration,
        reason=reason.strip(),
        created_by=principal.username,
    )
    return {**silence.model_dump(), "active": True}


@app.get("/api/monitoring/silences")
def list_silences(
    active: str | None = None, principal: Principal = Depends(require("read"))
) -> dict[str, list[dict[str, Any]]]:
    """docs/33 §5.1：静默列表（read），?active=true|false 过滤，每条带 active 计算字段。"""
    active_filter: bool | None = None
    if active is not None:
        if active not in ("true", "false"):
            raise HTTPException(status_code=422, detail="active 只允许 true 或 false")
        active_filter = active == "true"
    monitoring = services_for(principal).monitoring
    items = monitoring.list_silences(active_filter)
    return {"items": [{**s.model_dump(), "active": is_silence_active(s)} for s in items]}


@app.delete("/api/monitoring/silences/{silence_id}")
def delete_silence(
    silence_id: str, principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    """docs/33 §5.1：提前解除（administer）；不存在 404。"""
    monitoring = services_for(principal).monitoring
    if not monitoring.delete_silence(silence_id):
        raise HTTPException(status_code=404, detail=f"静默规则不存在：{silence_id}")
    return {"id": silence_id, "deleted": True}


@app.put("/api/monitoring/silences/{silence_id}")
def update_silence(
    silence_id: str, body: dict[str, Any],
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """docs/60 §4.1：编辑静默可变字段（administer）；不存在/已过期 404，校验失败 422。"""
    errors: list[str] = []
    updates: dict[str, Any] = {}
    if "reason" in body:
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
            errors.append("reason 必须是非空且不超过 200 字的字符串")
        else:
            updates["reason"] = reason.strip()
    if "rule_id" in body:
        updates["rule_id"] = _normalize_optional_id(body.get("rule_id"), "rule_id", errors)
    if "graph_id" in body:
        updates["graph_id"] = _normalize_optional_id(body.get("graph_id"), "graph_id", errors)
    if "expires_at" in body:
        expires = _parse_future_expires_at(body.get("expires_at"), errors)
        if expires is not None:
            updates["expires_at"] = expires
    if updates.get("rule_id") is not None and updates.get("graph_id") is not None:
        errors.append("rule_id 与 graph_id 不得同时指定")
    if not updates and not errors:
        errors.append("至少提供一个可更新字段：reason / rule_id / graph_id / expires_at")
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors))
    monitoring = services_for(principal).monitoring
    updated = monitoring.update_silence(silence_id, **updates)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"静默规则不存在或已过期：{silence_id}")
    return {**updated.model_dump(), "active": is_silence_active(updated)}


@app.get("/api/monitoring/on-call")
def get_on_call(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    """docs/33 §5.3：值班表（read），current 为当前值班人（空表 null）。"""
    schedule = services_for(principal).monitoring.get_oncall()
    return {**schedule.model_dump(), "current": current_assignee(schedule)}


@app.put("/api/monitoring/on-call")
def update_on_call(
    body: dict[str, Any], principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    """docs/33 §5.3：设置轮值表（administer）；members 1-20 个非空串，去重保序、重置 index=0。"""
    members = body.get("members")
    if not isinstance(members, list) or not 1 <= len(members) <= 20:
        raise HTTPException(status_code=422, detail="members 必须是 1-20 个用户名字符串组成的数组")
    if any(not isinstance(m, str) or not m.strip() for m in members):
        raise HTTPException(status_code=422, detail="members 每项必须是非空字符串")
    # docs/60 §4.2：可选 rotationIntervalDays（1-365 或 null）；未提供按整体替换置空（不自动）
    rotation_interval_days: int | None = None
    if "rotationIntervalDays" in body:
        raw = body.get("rotationIntervalDays")
        if raw is None:
            rotation_interval_days = None
        elif isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 365:
            rotation_interval_days = raw
        else:
            raise HTTPException(
                status_code=422, detail="rotationIntervalDays 必须是 1-365 的整数或 null"
            )
    schedule = services_for(principal).monitoring.set_oncall(
        members=members, updated_by=principal.username,
        rotation_interval_days=rotation_interval_days,
    )
    return {**schedule.model_dump(), "current": current_assignee(schedule)}


@app.post("/api/monitoring/on-call/rotate")
def rotate_on_call(principal: Principal = Depends(require("administer"))) -> dict[str, Any]:
    """docs/33 §5.3：手动轮换（administer）；空表 409。"""
    monitoring = services_for(principal).monitoring
    try:
        schedule = monitoring.rotate_oncall(updated_by=principal.username)
    except OnCallEmpty as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**schedule.model_dump(), "current": current_assignee(schedule)}


@app.post("/api/nl/generate")
def nl_generate(
    request: NLGenerateRequest, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    try:
        graph = generate_graph(request.prompt, tenant_id=principal.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    warnings = validate_param_fills(graph, tool_input_schemas(_demo_registry))
    return {"graph": graph, "paramWarnings": warnings}


@app.post("/api/demo/shop/login")
def demo_shop_login(request: DemoLoginRequest) -> dict[str, Any]:
    if not _demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if not _demo_shop.login(request.username, request.password):
        raise HTTPException(status_code=401, detail="登录失败：用户名或密码错误（demo/demo）")
    return {"logged_in": True}


@app.get("/api/demo/shop/orders")
def demo_shop_orders() -> dict[str, Any]:
    if not _demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if not _demo_shop.logged_in:
        raise HTTPException(status_code=401, detail="未登录")
    return {"orders": _demo_shop.list_pending_refunds()}


_MOCK_ORDERS = [
    {"order_id": "12345", "reason": "商品破损", "amount": 299},
    {"order_id": "12346", "reason": "不想要了", "amount": 5000},
]


@app.get("/api/demo/mock/orders")
def demo_mock_orders(request: Request) -> dict[str, Any]:
    """API 适配器演示目标（04 §4.6 / 12 §5）：要求 X-Demo-Token: demo-token。

    常量串只算"防误用"，不算凭证——它会 404 在这台进程是 prod 档的时候。
    """
    if not _demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if request.headers.get("x-demo-token") != "demo-token":
        raise HTTPException(status_code=401, detail="缺少或错误的 X-Demo-Token 请求头")
    return {"orders": _MOCK_ORDERS}


@app.post("/api/demo/mock/orders/{order_id}/receipt")
def demo_mock_receipt(order_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """API 适配器演示目标：回显 JSON 请求体，供 POST/body/插值端到端验证。

    回显就是风险面：prod 档必须 404，否则任何人无需凭证即可让平台原样吐回它收到的体。
    """
    if not _demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return {"order_id": order_id, "body": body or {}, "received": True}


def _demo_mock_enabled() -> bool:
    """J-3e：demo 模拟面开关——prod 默认关（fail-closed），ATLAS_ENABLE_DEMO_MOCK=1 显式开。

    档位一律走 `read_env_profile()`：它大小写不敏感且拒非法值。原先这里自己写
    `os.getenv("ATLAS_ENV") != "prod"`，于是 `ATLAS_ENV=PROD` 会被判成"非 prod"、
    整片匿名面**静默开门**——把"以为在 prod"和"真的在 prod"混成一个 bug。

    docs/77 R2/R5：判定本身已上收到 `security.bootstrap.demo_surface_enabled()`，
    HTTP mock 路由、运行期演示适配器、文档面与渠道出向测试缝共用同一处，此处只保留
    这个历史入口名供既有调用点使用。
    """
    return demo_surface_enabled()


# Shopify Admin webhooks 资源的同进程模拟（docs/41 §D；随 demo reset 清空）。
_MOCK_SHOPIFY_WEBHOOKS: dict[int, dict[str, Any]] = {}
_mock_shopify_webhook_seq = count(1)


@app.get("/api/demo/mock/shopify-admin/webhooks.json")
def mock_shopify_admin_webhooks_list() -> dict[str, Any]:
    if not _demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "webhooks": [
            {"id": remote_id, **record}
            for remote_id, record in _MOCK_SHOPIFY_WEBHOOKS.items()
        ]
    }


@app.post("/api/demo/mock/shopify-admin/webhooks.json")
def mock_shopify_admin_webhooks_create(
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    webhook = (body or {}).get("webhook")
    if (
        not isinstance(webhook, dict)
        or not isinstance(webhook.get("topic"), str)
        or not isinstance(webhook.get("address"), str)
        or webhook.get("format") != "json"
    ):
        raise HTTPException(status_code=422, detail="webhook 必填 topic、address 且 format=json")
    for existing in _MOCK_SHOPIFY_WEBHOOKS.values():
        if existing["topic"] == webhook["topic"] and existing["address"] == webhook["address"]:
            raise HTTPException(
                status_code=422,
                detail="Topic and address combination has already been taken",
            )
    remote_id = next(_mock_shopify_webhook_seq)
    record = {"topic": webhook["topic"], "address": webhook["address"], "format": "json"}
    _MOCK_SHOPIFY_WEBHOOKS[remote_id] = record
    return {"webhook": {"id": remote_id, **record}}


@app.delete("/api/demo/mock/shopify-admin/webhooks/{webhook_id}.json")
def mock_shopify_admin_webhooks_delete(webhook_id: str) -> dict[str, Any]:
    if not _demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        remote_id = int(webhook_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Not Found")
    if remote_id not in _MOCK_SHOPIFY_WEBHOOKS:
        raise HTTPException(status_code=404, detail="Not Found")
    del _MOCK_SHOPIFY_WEBHOOKS[remote_id]
    return {}


@app.get("/api/demo/messages")
def demo_messages(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """消息适配器演示查看（04 §4.8）：本租户进程内已记录消息，重启/reset 清空，无真实投递。"""
    return {"items": services_for(principal).message_service.list()}


@app.get("/api/demo/deliveries")
def demo_deliveries(
    limit: int = 100,
    status: str | None = None,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """docs/56 §4.3：消息投递日志（每次 send 一条，含尝试次数/耗时/错误），倒序。

    打包 U：status=failed|delivered 过滤（docs/82 D-5），非法值 422。
    """
    if status is not None and status not in ("failed", "delivered"):
        raise HTTPException(
            status_code=422,
            detail={"code": "DELIVERY_STATUS_INVALID", "message": "status 只接受 failed 或 delivered"},
        )
    bounded = max(1, min(limit, 200))
    return {
        "items": services_for(principal).message_service.list_deliveries(
            bounded, status=status
        )
    }


@app.post("/api/demo/deliveries/{seq}/replay")
def demo_replay_delivery(
    seq: int,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """打包 U（docs/82 D-5）：按原内容重放一条出站失败投递，原失败行不可变。"""
    try:
        result = services_for(principal).message_service.replay_failed(seq)
    except MessageSendError as exc:
        status_code = 422 if exc.code == "DLQ_BODY_UNAVAILABLE" else 409
        # 带 params 的码要把 params 一起下发，否则英文态模板填不满回退中文原文。
        detail: dict[str, object] = {"code": exc.code, "message": str(exc)}
        if getattr(exc, "params", None):
            detail["params"] = exc.params
        raise HTTPException(status_code=status_code, detail=detail) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DELIVERY_NOT_FOUND", "message": "投递记录不存在"},
        )
    return result


# ---- M11 长期记忆（docs/26 §6 / docs/28 §5.1）：读 viewer+、手动新建/编辑 operate（source=manual）、删 admin；图内 remember 工具仍是运行时写入主路径 ----


class MemoryCreateRequest(BaseModel):
    """手动新建记忆（⑩）；source 由端点固定 manual，不接受入参。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["fact", "preference"]
    content: str = Field(min_length=1, max_length=2000)
    scope: dict[str, str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, str] | None = None


class MemoryUpdateRequest(BaseModel):
    """手动编辑记忆（⑩）：白名单字段子集，至少一个；id/created_at/source/embedding 不可改。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["fact", "preference"] | None = None
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    scope: dict[str, str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, str] | None = None


@app.post("/api/memories", status_code=201)
def create_memory(
    body: MemoryCreateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """手动新建记忆（operate；source 固定 manual，201，docs/28 §5.1）。校验失败 422 中文。"""
    repo = services_for(principal).memory_store
    try:
        return repo.remember(
            kind=body.kind,
            content=body.content,
            scope=body.scope,
            confidence=1.0 if body.confidence is None else body.confidence,
            source="manual",
            metadata=body.metadata,
        )
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.put("/api/memories/{memory_id}")
def update_memory(
    memory_id: str,
    body: MemoryUpdateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """手动编辑记忆白名单字段（operate；source 置 manual）；空体 422，不存在/他租户 404。"""
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=422, detail="请求体至少包含一个可改字段")
    repo = services_for(principal).memory_store
    try:
        updated = repo.update(memory_id, **data)
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return updated


@app.get("/api/memories")
def list_memories(
    kind: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """当前租户记忆倒序列表（不含 embedding）；kind 可选过滤，limit 缺省 50、上限 200。"""
    if kind is not None and kind not in ("fact", "preference"):
        raise HTTPException(status_code=422, detail="kind 必须是 fact 或 preference")
    limit = max(1, min(limit, 200))
    return {"items": services_for(principal).memory_store.list(kind=kind, limit=limit)}


@app.get("/api/memories/search")
def search_memories(
    q: str = "",
    kind: str | None = None,
    top_k: int = 5,
    min_score: float = 0.0,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """语义检索当前租户记忆；q 空白返 422，无命中返空数组（成功不报错）。"""
    if not q or not q.strip():
        raise HTTPException(status_code=422, detail="q 必须是非空检索词")
    if kind is not None and kind not in ("fact", "preference"):
        raise HTTPException(status_code=422, detail="kind 必须是 fact 或 preference")
    top_k = max(1, min(top_k, 20))
    min_score = max(0.0, min(min_score, 1.0))
    results = services_for(principal).memory_store.recall(
        q.strip(), kind=kind, top_k=top_k, min_score=min_score
    )
    return {"results": results}


@app.delete("/api/memories/{memory_id}")
def delete_memory(
    memory_id: str,
    principal: Principal = Depends(require("administer")),
) -> dict[str, bool]:
    """删除一条记忆（admin only）；不存在或跨租户一律 404（不泄漏存在性，T15）。"""
    deleted = services_for(principal).memory_store.delete(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"deleted": True}


@app.post("/api/demo/reset")
def demo_reset(
    principal: Principal = Depends(require("administer")),
) -> dict[str, bool]:
    """重置本租户 Demo 数据（清空已保存图、释放 pending 审批/调试、清空消息、
    清空监控运行/告警并恢复默认规则）；共享 demo 店铺与 demo SQLite 种子同步重建。

    admin only（04 §5.14）。反馈与录制用例为测试资产，按租户保留不在此清除
    （04 §5.11；用例图已快照进自身）。
    """
    tenant_registry.reset_tenant(principal.tenant_id)
    # docs/68 §1 D-4：图被清掉而注册项还在，就会对着一张不存在的图空转派发。
    schedule_store().reset_tenant(principal.tenant_id)
    if STORAGE_BACKEND == "pg":
        # PG 档：清挂起帧表（帧是 loader frame_sink 写的，内存 broker 不负责；recordings/feedback 保留）。
        clear_tenant_frames(get_pg_backend().engine, principal.tenant_id)
    _demo_shop.reset()
    if _db_client is not None:  # docs/77 R2：prod 未开 demo 面时该适配器未装配
        _db_client.reseed_demo()
    _MOCK_SHOPIFY_WEBHOOKS.clear()
    return {"reset": True}


@app.post("/api/feedback", status_code=201)
def submit_feedback(
    request: FeedbackRequest, principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    """反馈入口对全部登录角色开放（viewer 可提交，04 §5.14）；按租户分区。

    首登强制改密**不**挡这一条（docs/95 U1146 的豁免面里唯一非 auth 路由）：它不读业务数据，
    而被门挡住的人正好需要它报障。新增业务端点若也想用 `Depends(get_principal)` 直接过，
    U1146 会红——要么改 `require()`，要么把理由加进那张表。
    """
    return services_for(principal).feedback_store.add(request)


@app.get("/api/feedback")
def list_feedback(
    principal: Principal = Depends(require("administer")),
) -> dict[str, list[dict[str, Any]]]:
    """反馈列表 admin only（04 §5.14）；只列本租户。"""
    return {"items": services_for(principal).feedback_store.list()}


_CONSOLE_HTML = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Demo 商家售后控制台</title>
<style>body{font-family:sans-serif;max-width:720px;margin:40px auto;padding:0 16px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px}
input,button{padding:6px;margin:4px 0}</style></head>
<body>
<h1>Demo 商家售后控制台</h1>
<p>Atlas 自动登录的目标系统（W9-W10 模拟平台，账号 demo/demo）。</p>
<div id="loginBox"><input id="u" value="demo" placeholder="用户名">
<input id="p" type="password" value="demo" placeholder="密码">
<button onclick="doLogin()">登录</button></div>
<div id="panel" hidden><h2>待处理退款单</h2><table><thead>
<tr><th>订单号</th><th>退款原因</th><th>金额</th></tr></thead><tbody id="rows"></tbody></table></div>
<script>
async function doLogin(){
  const r = await fetch('/api/demo/shop/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:u.value,password:p.value})});
  if(!r.ok){alert((await r.json()).detail);return;}
  loginBox.hidden = true; panel.hidden = false; loadOrders();
}
async function loadOrders(){
  const r = await fetch('/api/demo/shop/orders');
  const data = await r.json();
  rows.innerHTML = data.orders.map(o=>`<tr><td>${o.order_id}</td><td>${o.reason}</td><td>${o.amount}</td></tr>`).join('');
}
</script></body></html>
"""


@app.get("/demo/shop", response_class=HTMLResponse)
def demo_shop_console() -> str:
    if not _demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return _CONSOLE_HTML


def _frontend_dist() -> Path | None:
    configured = os.getenv("ATLAS_FRONTEND_DIST", "frontend/dist")
    path = Path(configured)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path if (path / "index.html").is_file() else None


_dist = _frontend_dist()
if _dist is not None:
    # 生产形态（Docker）：FastAPI 同源托管编辑器构建产物；dev 仍用 Vite 5174 代理
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
