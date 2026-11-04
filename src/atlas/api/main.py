"""FastAPI Demo 入口（docs/12 §5；T5：Demo 用 Python 实现网关同构接口）。

W7-W8：Graph DSL 保存/读取/编译/运行。存储为进程内字典
（与 T4 进程内总线一致；持久化待业务表 DDL，docs/11 S1）。
W9-W10：退款 Demo 装配（共享 shop 服务/注册表）、SSE 运行进度、
NL 生成草稿、适配器发现、模拟商家售后控制台。
"""

from __future__ import annotations

import json
import hmac
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import functools
import time
import uuid
from itertools import count
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, NoReturn

import httpx

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text

from atlas.cards import (
    CardRenderError,
    get_card,
    map_action_output,
)
from atlas.evaluation import EvaluationTask
from atlas.graph.conditions import validate_expression
from atlas.graph.dsl import GraphValidationError, parse_graph, valid_event_key
from atlas.graph.loader import (
    RunSuperseded,
    WaitTimeoutSuspend,
    compile_graph,
    run_graph,
)
from atlas.collaboration.cancellations import RunCancelled
from atlas.collaboration.notifications import EmailApprovalNotifier
from atlas.collaboration.email_token import EmailTokenError, TokenIssuer
from atlas.tracing import Tracer
from atlas.harness.registry import AdapterRegistry
from atlas.harness.runtime import (
    build_base_registry,
    build_runtime_registry,
    resolve_database_client,
)
from atlas.iam.deps import (
    services_for,
    tenant_registry,
)
from atlas.iam.passwords import validate_password
from atlas.iam.principals import Principal, Role
from atlas.iam.registry import STORAGE_BACKEND, TenantServices
from atlas.llm.decision import get_decision_client, warm_litellm
from atlas.llm.metering import usage_context
from atlas.cycles.supervisor import (
    CycleOutcome,
    cycle_config,
    run_cycle_session,
    session_for_run,
)
from atlas.memory.database import ping, wait_for_database
from atlas.message.template_store import (
    validate_message_template,
)
from atlas.monitoring import extract_business, extract_node_results
from atlas.observability.audit import AUDITED_METHODS, LOGIN_PATH
from atlas.channels.base import (
    ChannelBinding,
    ChannelError,
)
from atlas.channels.webhooks import (
    WebhookDeliverer,
)
from atlas.channels.inbound import InboundReceiver
from atlas.a2a.router import router as a2a_router  # docs/90 ADR T32 (Zeus A2A execution-agent face)
# P2-1: split route modules read these through `atlas.api.main` at call time so
# that tests patching `atlas.api.main.<name>` still take effect. Keep the
# re-exports; ruff would otherwise drop them as unused.
from atlas.api.sse import SSE_HEARTBEAT_SECONDS  # noqa: F401
from atlas.httpapi.service import HttpApiClient  # noqa: F401
from atlas.connections.service import ConnectionServiceError
from atlas.services import run_recovery, scheduler_runtime
from atlas.services.error_safety import (
    safe_run_error as _safe_run_error,
    token_ref as _token_ref,
)
from atlas.services.error_handlers import register_exception_handlers
from atlas.services.webhook_runtime import build_webhook_callbacks
from atlas.observability.logging import (
    configure_logging,
    install_request_id_middleware,
)
from atlas.openapi.errors import OpenApiError
from atlas.openapi.parser import parse_document
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
from atlas.monitoring.alerts import validate_rules
from atlas.recording import (
    HumanOutcome,
    run_release_gate,
)
from atlas.routing import (
    Experiment,
    GateMetricId,
    RolloutConfig,
    RolloutState,
    TriggerEvent,
    evaluate_after_run,
)
from atlas.shop.service import DemoShopService
from atlas.storage.pg import get_pg_backend
# P2-1: the runtime router resolves claimed suspended frames through
# `atlas.api.main` so tests can monkeypatch the storage helper; keep it exported.
from atlas.storage.recovery import abandon_claimed_suspended_frame  # noqa: F401
from atlas.storage.retention import run_retention_once
from atlas.scheduling.engine import TickLedger, TickOutcome
from atlas.scheduling.models import (
    ScheduleRecord,
)
from atlas.scheduling.store import ScheduleStore
from atlas.versioning.publish import publish as publish_graph_version

logger = logging.getLogger(__name__)

# docs/35 §2（T2）：审批挂起邮件中的应用入口（前端地址）。
# 应用对外入口（审批深链基址）：唯一读取器在 security.bootstrap（docs/89 §15 A-6，
# 此前这里与 channels/registry、notifications 各读一遍同一 env＋同一缺省）
_PUBLIC_URL = read_public_url()

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


def _pg_engine():
    """当前 PG 引擎（惰性取值，保证测试 monkeypatch STORAGE_BACKEND 后仍按当次读）。"""
    return get_pg_backend().engine


def recover_pending() -> None:
    """服务启动钩子：读未决挂起帧 → 逐帧重建 pending + 重启续跑线程（docs/24 §2.4）。

    仅 PG 后端生效（进程内后端重启即失、无帧可恢复）；失败帧隔离记日志、不阻塞其余。
    """
    run_recovery.recover_pending(
        storage_backend=STORAGE_BACKEND,
        pg_engine=_pg_engine,
        resume_from_frame=_resume_from_frame,
    )


def _resume_from_frame(engine, frame: dict) -> None:
    """按帧重建 pending（approval 重挂 Event + 剩余 deadline），并重启续跑线程。"""
    run_recovery.resume_from_frame(
        engine,
        frame,
        services_for=tenant_registry.get,
        # 经模块全局取 _resume_run：测试 monkeypatch 掉的续跑体仍会被用到。
        submit_resume_run=lambda eng, services, pending: _BACKGROUND_WORKER_POOL.submit(
            _resume_run, eng, services, pending
        ),
    )


def _resume_run(engine, services: TenantServices, frame: dict) -> None:
    """续跑线程：从挂起节点沿边到 END，完成后清帧并落 run 终态（决策 / wait 到点 / 再超时均覆盖）。"""
    run_recovery.resume_run(
        engine,
        services,
        frame,
        run_graph_fn=run_graph,
        runtime_registry=_runtime_registry,
        graph_resolver=_tenant_graph_resolver,
        secret_provider=_secret_provider,
    )


def _frame_sink_for(tenant_id: str, run_store, run_id: str):
    """挂起回调：先落 run 状态为 suspended（所有后端），PG 后端再写 interruptions 帧。"""
    return run_recovery.frame_sink_for(
        tenant_id,
        run_store,
        run_id,
        storage_backend=STORAGE_BACKEND,
        pg_engine=_pg_engine,
    )


def _resume_claim_for() -> Callable[[str], bool] | None:
    """挂起帧认领门（docs/62 §3.4）：只有 PG 后端有帧可认领，进程内后端返回 None＝恒放行。

    与 `frame_sink` 严格配对注入——凡写帧的运行必带认领门，反之亦然，否则会出现
    "有帧无人认领判定"或"无帧却被判定为已被接管"两种漂移。
    """
    return run_recovery.resume_claim_for(
        storage_backend=STORAGE_BACKEND,
        pg_engine=_pg_engine,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # docs/79（打包 S）D-4：先有界等待 PG 连通，再跑恢复扫描与 retention。
    _wait_for_database_ready()
    recover_pending()
    # W5-3：配置了 hub 时启动同步一次投影（失败不致命，保留缓存/回退 env）。
    from atlas.llm import hub as _hub

    if _hub.is_hub_configured():
        try:
            _hub.sync_hub_projection()
        except Exception as exc:  # noqa: BLE001
            logger.warning("hub projection sync failed at startup: %s", exc)
    # docs/64 J-2a：启动即打印决策器运行模式（含降级警告），不再静默。
    get_decision_client()
    # 试用干跑实测（2026-10-08）：进程内首次 `import litellm` 若发生在 worker 线程，
    # 会和事件循环线程上被 litellm 自己挂进 root logger 的懒导入过滤器抢同一把
    # _ModuleLock → _DeadlockError → 之后整个进程不再响应。导在起服务之前＝主线程串行导完。
    warm_litellm()
    # docs/65 K-A：启动跑一次 retention 清扫（PG 档；失败只 warning 不阻断启动）。
    run_retention_once()
    # docs/68 §2.3：调度线程在恢复扫描**之后**起——先让挂起帧归位，再派发新运行。
    start_scheduler()
    # W5-3：后台周期刷新 hub 投影（5min 一轮）。
    _hub.start_hub_refresh()
    yield
    _hub.stop_hub_refresh()
    stop_scheduler()
    # 审查发现 #10：内置 demo 为 mkdtemp 临时文件库，关闭时只释放连接池；
    # 临时目录由 database.service 的 atexit 兜底回收（close 不删目录——
    # with TestClient(app) 反复进出 lifespan 时 import 期单例 _db_client 仍会被复用）。
    if _db_client is not None:
        _db_client.close()


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


# P1-复查（2026-10-19，docs/08 记录）：release-gate / evaluations / autoapply 三个
# 同步批处理端点（内部多次 run_graph）与 O-5 同族——并发触发时长期占用 uvicorn 线程池
# （约 40），超限拖垮含 cancel/审批/GET 在内的整个同步面。独立信号量（比 run 更保守，
# 一个批处理内部多次 run），超限 429（可重试）。运行语义零变化：只拒绝超限请求。
# 必须定义在所有使用它的端点（autoapply 2034 / release-gate / evaluations）之前。
_HEAVY_BATCH_MAX_CONCURRENT = 8
_heavy_batch_slots = threading.Semaphore(_HEAVY_BATCH_MAX_CONCURRENT)


def _heavy_batch_guard(func: Callable[..., Any]) -> Callable[..., Any]:
    """重负载批处理端点并发护栏：acquire(blocking=False) 失败 → 429；finally release。

    functools.wraps 保留原签名，FastAPI 依赖注入（principal 等 Depends）照常解析。
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not _heavy_batch_slots.acquire(blocking=False):
            raise HTTPException(
                status_code=429,
                detail=f"批量处理并发已达上限 {_HEAVY_BATCH_MAX_CONCURRENT}：请稍后重试",
            )
        try:
            return func(*args, **kwargs)
        finally:
            _heavy_batch_slots.release()

    return wrapper

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


register_exception_handlers(app)


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


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r 非整数，回退默认 %d", name, raw, default)
        return default
    return value if value > 0 else default


#: /api/ 写请求体全局上限（2026-11-02 评审 P2-4）：默认 2 MiB，远超 500 节点
#: 上限图的实测 JSON 体量；端点级更严上限（webhook 1MB、模板导入 256KB）仍各自生效。
#: 只读 Content-Length 预拒，不消费请求体；无长度头的 chunked 体由端点读取后上限兜底。
MAX_API_BODY_BYTES = _env_int("ATLAS_MAX_BODY_BYTES", 2 * 1024 * 1024)


@app.middleware("http")
async def cap_api_body_bytes(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH"):
        raw_length = request.headers.get("content-length")
        if raw_length is not None and raw_length.isdigit() and int(raw_length) > MAX_API_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "请求体超过大小上限"})
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


# ================= LLM 模型配置管理（docs/93 打包 Y；内置模型＋BYOK）=================

def _effective_model_summary(services: TenantServices, tenant_id: str) -> dict[str, Any]:
    """当前生效摘要（W5-3 收口）：BYOK > 单条内置 > hub 投影 > env；都没有 none。"""
    from atlas.llm.hub import resolve_runtime_model

    rt = resolve_runtime_model(tenant_id)
    if rt is None:
        return {"source": "none", "model": "", "apiKey": "", "baseUrl": None,
                "enabled": False, "mode": "builtin", "updatedBy": "", "updatedAt": ""}
    mode = "byok" if rt.source == "byok" else "builtin"
    return {"source": rt.source, "model": rt.model, "apiKey": "",
            "baseUrl": rt.base_url, "enabled": True, "mode": mode,
            "updatedBy": "", "updatedAt": ""}


def _hub_status() -> dict[str, Any]:
    """hub 同步状态：configured/disabled/revision。"""
    from atlas.llm import hub as _hub

    projection = _hub.load_projection()
    return {
        "configured": _hub.is_hub_configured(),
        "disabled": _hub.is_hub_disabled(),
        "revision": projection.revision if projection is not None else None,
    }


def _hub_builtin_models() -> list[dict[str, Any]]:
    """hub 投影内置模型的只读脱敏视图（api_key 不回显）。"""
    from atlas.llm import hub as _hub

    projection = _hub.load_projection()
    if projection is None:
        return []
    views: list[dict[str, Any]] = []
    for m in projection.models:
        views.append(
            {
                "assignmentId": m.assignment_id,
                "alias": m.alias,
                "role": m.role,
                "model": m.model,
                "rawModel": m.raw_model,
                "baseUrl": m.base_url,
                "provider": m.provider,
                "modalities": m.modalities,
                "isDefault": m.is_default,
            }
        )
    return views


# --- LLM 用量与成本计量（docs/129 打包 AU＝D70） -------------------------------

_USAGE_GROUP_BY = ("caller", "model", "day")


# --- 运行期自走循环（docs/130 打包 BN＝D69） ------------------------------------


def _cycle_session_or_404(run_id: str, tenant_id: str) -> Any:
    """由 runId 反查圈会话；跨租户/非圈 run 一律 404（不泄漏存在性，04 §5.14）。"""
    run = tenant_registry.get(tenant_id).run_store.get(run_id)
    if run is None or not run.get("cycleGroupId"):
        raise HTTPException(status_code=404, detail=f"该运行不属于任何圈会话：{run_id}")
    session = session_for_run(run_id)
    if session is None or session.tenant_id != tenant_id:
        # 会话已被 ring 淘汰或跨租户 ⇒ 与"不存在"同形（docs/12 跨租户 404 惯例）
        raise HTTPException(status_code=404, detail=f"圈会话不存在或已过期：{run_id}")
    return session


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


# webhook 四回调与 _webhook_deliverer 的装配落在 `_background_run_worker` 定义之后
# （见下）：回调由 services.webhook_runtime 工厂生成，pool/worker 在装配期注入。


def _background_run_cycle(
    services: TenantServices, tenant_id: str, graph_id: str, version: int,
    event: TriggerEvent, *, mode: str, run_id: str | None,
    cycle_seq: int = 0, cycle_group_id: str = "", inputs: dict[str, Any] | None = None,
    agent_id: str = "",
) -> CycleOutcome:
    """后台跑**一圈**（= 一次真实运行）；返回 `CycleOutcome` 供圈层判定。

    与同步入口同构的分工：圈层只管"跑几次"，一圈内部的 begin/finish/让位/失败记账
    语义逐字保持（docs/130 C-D1）。`cycle_seq`／`cycle_group_id` 缺省 0/''＝非圈运行。
    """
    run_store = services.run_store
    own_run_id = run_id
    if own_run_id is None:
        own_run_id = uuid.uuid4().hex
        run_store.begin(
            run_id=own_run_id, graph_id=graph_id, mode=mode,
            cycle_seq=cycle_seq, cycle_group_id=cycle_group_id,
            agent_id=agent_id,
        )
    try:
        raw = services.graph_store.get(graph_id, version)
        if raw is None:
            run_store.finish(
                run_id=own_run_id, status="failed",
                error=f"已发布版本不存在：{graph_id}@{version}",
            )
            return CycleOutcome(run_id=own_run_id, status="failed", stop_run=True)
        graph = parse_graph(raw)
        with usage_context(
            tenant_id, run_id=own_run_id, graph_id=graph_id, store=services.usage_store
        ):
            result = run_graph(
                graph,
                inputs=inputs if inputs is not None else {"event": event.model_dump()},
                registry=_runtime_registry(services),
                approval_broker=services.approval_broker,
                event_wait_broker=services.event_wait_broker,
                graph_id=graph_id,
                graph_resolver=_tenant_graph_resolver(services),
                frame_sink=_frame_sink_for(tenant_id, run_store, own_run_id),
                resume_claim=_resume_claim_for(),
                _block_subgraph_suspend=(STORAGE_BACKEND == "pg"),
                graph_version=version,
                tenant_id=tenant_id,
                secret_provider=_secret_provider,
                memory_repo=services.memory_store,
            )
        run_store.finish(
            run_id=own_run_id, status="completed",
            outputs=result["outputs"], trace=result["trace"],
        )
        return CycleOutcome(
            run_id=own_run_id, status="completed", outputs=result.get("outputs", {})
        )
    except RunSuperseded as exc:
        # docs/62 §2 D-4：后台运行同样受认领门约束——输家不落 failed
        # （否则把赢家的执行记录成失败），只让位并留日志。
        logger.info("后台运行让位 graph=%s@%s：%s", graph_id, version, exc)
        return CycleOutcome(run_id=own_run_id, status="suspended", stop_run=True)
    except WaitTimeoutSuspend as exc:
        # docs/136 打包 BS：后台运行挂起节点超时且 onTimeout="suspend"——run 保持
        # suspended（不 finish、不清帧），等人工后续恢复。
        logger.info("后台运行超时保持挂起 graph=%s@%s：%s", graph_id, version, exc)
        return CycleOutcome(run_id=own_run_id, status="suspended", stop_run=True)
    except Exception as exc:
        logger.error("%s 触发图运行失败 graph=%s@%s", mode, graph_id, version, exc_info=True)
        run_store.finish(
            run_id=own_run_id, status="failed",
            error=_safe_run_error(exc),
        )
        return CycleOutcome(run_id=own_run_id, status="failed", stop_run=True)


def _background_run_worker(
    services: TenantServices, tenant_id: str,
    graph_id: str, version: int, event: TriggerEvent,
    agent_id: str = "", *, mode: str = "webhook", run_id: str | None = None,
) -> None:
    """后台运行钉版图：run 记录独立于 HTTP 请求；失败落 failed，不回传触发方。

    webhook 与定时触发共用这条路径（docs/68 §1 D-3：派发按发布号钉版，绝不回落草稿）。
    `run_id` 由调度派发方预先 begin 后传入——它必须在 tick 内就让 run 变成 running，
    否则下一轮 tick 的"同图还在跑"判定会漏看这条刚提交的运行。

    **圈启用面**（docs/130 C-D10）：图级 `loop` 配置存在 ⇒ 本 worker 按圈会话驱动；
    预置的 `run_id` 只用于**第一圈**（保持"tick 内即可见 running"的既有语义），
    后续圈由圈层发新 run_id。缺省配置走原路径逐字不变。
    """
    raw = services.graph_store.get(graph_id, version)
    if raw is None:
        # 图不存在：预置 run 要落 failed（与既有语义同）；无预置 run 则无事可做。
        if run_id is not None:
            services.run_store.finish(
                run_id=run_id, status="failed",
                error=f"已发布版本不存在：{graph_id}@{version}",
            )
        return
    try:
        graph = parse_graph(raw)
    except Exception as exc:
        if run_id is not None:
            services.run_store.finish(
                run_id=run_id, status="failed", error=_safe_run_error(exc)
            )
        logger.error("%s 图解析失败 graph=%s@%s：%s", mode, graph_id, version, exc)
        return

    if cycle_config(graph) is None:
        _background_run_cycle(
            services, tenant_id, graph_id, version, event,
            mode=mode, run_id=run_id, agent_id=agent_id,
        )
        return

    def _run_once(step) -> CycleOutcome:
        return _background_run_cycle(
            services, tenant_id, graph_id, version, event,
            mode=mode,
            # 只有第一圈沿用派发方预置的 run_id（tick 内可见 running）
            run_id=run_id if step.seq == 1 else None,
            cycle_seq=step.seq, cycle_group_id=step.group_id,
            inputs=step.inputs, agent_id=agent_id,
        )

    run_cycle_session(
        services, graph,
        tenant_id=tenant_id, graph_id=graph_id,
        inputs={"event": event.model_dump()},
        run_once=_run_once,
    )


# webhook 回调装配（services.webhook_runtime 生成；worker 须已定义，故在本函数之后）。
# WebhookDeliverer.__init__ 仅存回调引用，装配位置的模块内移动不改变运行时行为。
_webhook_resolve, _webhook_trigger, _inbound_trigger, _webhook_audit = build_webhook_callbacks(
    pool=_BACKGROUND_WORKER_POOL, worker=_background_run_worker
)
_webhook_deliverer = WebhookDeliverer(
    resolver=_webhook_resolve,
    trigger=_webhook_trigger,
    auditor=_webhook_audit,
    store_provider=lambda tenant: tenant_registry.get(tenant).webhook_deliveries,
)


# --- 定时触发调度器（docs/68，打包 N／ADR T30） ---------------------------

def schedule_store() -> ScheduleStore:
    """全局调度 store（惰性）：不按租户装配的理由见 `scheduling/store.py` 模块头。"""
    return scheduler_runtime.schedule_store(
        storage_backend=STORAGE_BACKEND,
        pg_engine=_pg_engine,
    )


def _start_pinned_run(
    services: TenantServices, tenant_id: str, graph_id: str, version: int, payload: dict
) -> str:
    """按钉版起一次后台运行，返回 runId。"""
    return scheduler_runtime.start_pinned_run(
        services, tenant_id, graph_id, version, payload,
        pool=_BACKGROUND_WORKER_POOL,
        worker=_background_run_worker,
    )


def _schedule_claim(record: ScheduleRecord, slot: datetime) -> bool:
    return scheduler_runtime.schedule_claim(
        record, slot,
        storage_backend=STORAGE_BACKEND,
        pg_engine=_pg_engine,
    )


def _schedule_is_busy(record: ScheduleRecord) -> bool:
    """同图是否还有 running/suspended 运行（docs/68 §1 D-6，保守按图粒度判）。"""
    return scheduler_runtime.schedule_is_busy(record)


def _schedule_dispatch(record: ScheduleRecord, slot: datetime) -> None:
    """派发一次定时动作；跑图的事件载荷带槽位，图里可用 `{{global.event.payload.slot}}` 引用。"""
    return scheduler_runtime.schedule_dispatch(
        record, slot,
        storage_backend=STORAGE_BACKEND,
        pg_engine=_pg_engine,
        pool=_BACKGROUND_WORKER_POOL,
        worker=_background_run_worker,
    )


def run_schedule_tick(ledger: TickLedger | None = None) -> list[TickOutcome]:
    """跑一轮调度评估：引擎只判定，写库与跳过计数都在这里（`scheduling/engine.py` 零 IO）。"""
    return scheduler_runtime.run_schedule_tick(
        ledger,
        storage_backend=STORAGE_BACKEND,
        pg_engine=_pg_engine,
        pool=_BACKGROUND_WORKER_POOL,
        worker=_background_run_worker,
    )


def start_scheduler() -> None:
    """在 lifespan 里、`recover_pending()` 之后起线程（docs/68 §2.3 的启动顺序）。"""
    scheduler_runtime.start_scheduler(
        storage_backend=STORAGE_BACKEND,
        pg_engine=_pg_engine,
        pool=_BACKGROUND_WORKER_POOL,
        worker=_background_run_worker,
    )


def stop_scheduler(timeout_seconds: float = 2.0) -> None:
    scheduler_runtime.stop_scheduler(timeout_seconds)


def _derive_schedule_on_publish(
    services: TenantServices, tenant_id: str, graph_id: str, version: int
) -> None:
    """发布出口：按图内定时节点**全量 reconcile** 调度注册——发布是版本变化的唯一时刻。

    （打包 ZN，D41 ③④）每个定时触发节点派生一条 `run`（schedule_id＝节点 id，各自
    cron/tz/catch_up/overlap_policy）；另派生**一条**图级 `reflect`（`__reflect__`，反思是
    整图 pass，不随节点倍增，节奏跟第一个定时节点，overlap 固定 skip）。本次派生集合之外、
    库里本图仍存在的 schedule_id（节点被删）一律撤销——替代旧「只取第一个、其余 warning」。
    """
    scheduler_runtime.derive_schedule_on_publish(
        services, tenant_id, graph_id, version,
        storage_backend=STORAGE_BACKEND,
        pg_engine=_pg_engine,
    )


class ScheduleEnabledRequest(BaseModel):
    enabled: bool
    schedule_id: str


class RunNowRequest(BaseModel):
    """run-now 的可选 body（打包 ZN）：`scheduleId` 指定跑哪条调度；缺省（null／无 body）
    ＝该图第一条 run 调度（v1 一图一调度时即主调度，逐字等价旧的无 body 行为）。"""

    schedule_id: str | None = None


def _first_run_schedule(tenant_id: str, graph_id: str) -> ScheduleRecord | None:
    """该图第一条 run 调度（run-now 缺省目标）；reflect 不计入。"""
    return scheduler_runtime.first_run_schedule(
        tenant_id, graph_id,
        storage_backend=STORAGE_BACKEND,
        pg_engine=_pg_engine,
    )


class ReflectionDecisionRequest(BaseModel):
    # 打包 ZU（docs/94 E-4）：候选级人工处理标记；Literal 非法值 FastAPI 自动 422。
    status: Literal["adopted", "dismissed"]


# --- 打包 BP（docs/132）：进化候选门控自动生效（D75，里程碑 E） --------------------


def _publish_hook(services: TenantServices, tenant_id: str, graph_id: str) -> int:
    """自动生效的发布段：冻结 latest 草稿为不可变版本＋派生调度（与手动 publish 同出口）。"""
    release_version = publish_graph_version(services.graph_store, graph_id)
    _derive_schedule_on_publish(services, tenant_id, graph_id, release_version)
    return release_version


class AutoApplyRequest(BaseModel):
    """自动生效启动请求（docs/132 BP-D2）：enabledScopes 缺省＝租户已开类别。

    显式传时**并入**租户级类别开关（一次开启并执行）；仅接受图内参数类
    （graph_variable/node_config），其余值 422（fail-closed，BP-D1）。
    """

    enabledScopes: list[str] | None = None


class AdapterDraftRequest(BaseModel):
    prompt: str = Field(min_length=1)


class AdapterDecisionRequest(BaseModel):
    decision: Literal["adopted", "dismissed"]
    authConfirmed: bool = False


class EmbedConfigRequest(BaseModel):
    graph_id: str = Field(min_length=1)
    enabled: bool = False
    token_ttl_seconds: int = 3600


class EmbedTokenRequest(BaseModel):
    graph_id: str = Field(min_length=1)


# dry-run 连通校验用短超时（默认 30s 太久；探测失败即标记不可达）。
_DRY_RUN_TIMEOUT_SECONDS = 8.0


class CronPreviewRequest(BaseModel):
    cron: str = ""
    timeZone: str = "UTC"


# 预览只数到第 3 个槽：再多没有排障意义，也防止逐分钟扫描变慢。
MAX_CRON_PREVIEW_HITS = 3


# --- 入站会话映射（打包 BO，docs/131 D-D3；挂 binding，与 webhooks 同范式） ----------

class InboundMappingsRequest(BaseModel):
    mappings: list[dict[str, Any]]


# --- 入站消息接收（打包 BO，docs/131 D73） --------------------------------

def _inbound_audit(action: str, tenant: str, metadata: dict) -> None:
    services = tenant_registry.get(tenant)
    # 与 _webhook_audit 同纪律：AuditStore 无 metadata 列，关键事实编码进 path。
    path = (
        f"inbound binding={metadata.get('bindingId')} "
        f"id={metadata.get('messageId')} conv={metadata.get('conversationId')}"
    )
    services.audit_store.record(
        tenant_id=tenant, actor="channel-inbound", action=action,
        status_code=200, path=path, ip="",
    )


_inbound_receiver = InboundReceiver(
    resolver=_webhook_resolve,
    trigger=_inbound_trigger,
    store_provider=lambda tid: tenant_registry.get(tid).inbound_deliveries,
    session_store=lambda tid: tenant_registry.get(tid).inbound_sessions,
    auditor=_inbound_audit,
)


class _FailureWindowLimiter:
    """验签失败限流：binding id 可枚举（ch-{seq}）的缓解——同一来源连续失败即熔断。

    进程内固定窗口计数，无外部状态；只累计失败，验签成功即清零。
    真实不可枚举 ID 是更大的改动（存量 binding id 需迁移），见 docs/73 上线缺口。
    """

    def __init__(self, max_failures: int = 10, window_seconds: float = 60.0) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._failures: dict[str, list[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        bucket = [t for t in self._failures.get(key, []) if now - t < self._window]
        self._failures[key] = bucket
        return len(bucket) < self._max

    def note_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._failures.setdefault(key, []).append(now)

    def note_success(self, key: str) -> None:
        self._failures.pop(key, None)


_inbound_failure_limiter = _FailureWindowLimiter()


# --- 可管理 agent（打包 BO，docs/131 D74） ---------------------------------


# --- Shopify 店铺侧 webhook 注册（docs/41） --------------------------------


class RemoteWebhookRequest(BaseModel):
    topic: str


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


class SubgraphUpgradesApplyRequest(BaseModel):
    """手动升级动作（打包 ZU2）：可选指定 node_ids 子集，缺省=全部升级项。"""

    node_ids: list[str] | None = None


class ExperimentCreateBody(BaseModel):
    """POST /api/experiments 请求体（docs/134 §2.4；camelCase 直进直出，GateMetric 先例）。"""

    graphId: str
    versionA: int
    versionB: int
    trafficPercent: int = Field(default=50, ge=1, le=99)
    primaryMetric: GateMetricId
    minSamples: int = Field(default=30, ge=1)


class ExperimentConcludeBody(BaseModel):
    """POST /api/experiments/{exp_id}/conclude 请求体；判定只读建议、conclude 人决（BR-D4）。"""

    action: Literal["adopt_a", "adopt_b"]
    reason: str | None = None


def _experiment_body_or_422(body: Any, model: type[BaseModel], label: str) -> Any:
    """实验端点 body → 模型；非法形状聚合为中文 422（照 _rollout_config_or_422 先例）。"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail=f"{label}必须是 JSON 对象")
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"])
            messages.append(f"{loc}：{error['msg']}" if loc else error["msg"])
        raise HTTPException(status_code=422, detail=f"{label}非法：" + "；".join(messages)) from exc


def _experiment_window(services: Any, exp: Experiment) -> list[Any]:
    """判定窗口＝实验 createdAt 起累计的该图运行（ISO 字符串同型比较；上限照 monitoring ring 200，照实记）。"""
    runs = services.monitoring.list_runs(exp.graphId, limit=200)
    return [run for run in runs if run.finished_at >= exp.createdAt]


def _template_matches_query(template: dict[str, Any], query: str) -> bool:
    """A1 搜索：name/description/tags/category 任一 casefold 子串命中（docs/97 E-3）。"""
    haystacks = [
        str(template.get("name") or ""),
        str(template.get("description") or ""),
        " ".join(template.get("tags") or []),
        str(template.get("category") or ""),
    ]
    return any(query in (part.casefold()) for part in haystacks)


_TEMPLATE_PARAM_TYPES = {"string", "number", "boolean", "select", "object", "array"}
_TEMPLATE_PARAM_KEYS = {
    "type",
    "label",
    "required",
    "default",
    "hint",
    "options",
    # 打包 ZX（docs/106）：结构化声明面——嵌套 object / 数组 / 条件显隐
    "properties",
    "items",
    "minItems",
    "maxItems",
    "visibleWhen",
}
_TEMPLATE_PARAM_MAX_DEPTH = 4


def _validate_param_decl(name: str, pdecl: Any, errors: list[str], depth: int = 0) -> None:
    """打包 ZX（docs/106 §2.2）：TemplateParam 声明形状校验（含递归）。

    与 A1 E-5 同构的标量四型校验逐字保留；object 递归 properties、array 递归 items、
    visibleWhen 校验判别字段形状；深度上限 _TEMPLATE_PARAM_MAX_DEPTH 防病态嵌套。
    """
    if not isinstance(pdecl, dict):
        errors.append(f"参数 {name} 的声明必须是对象")
        return
    unknown = set(pdecl) - _TEMPLATE_PARAM_KEYS
    if unknown:
        errors.append(f"参数 {name} 含未知字段：{', '.join(sorted(unknown))}")
    ptype = pdecl.get("type", "string")
    if ptype not in _TEMPLATE_PARAM_TYPES:
        errors.append(
            f"参数 {name} 的 type 必须是 string/number/boolean/select/object/array 之一"
        )
    label = pdecl.get("label")
    if label is not None and not isinstance(label, str):
        errors.append(f"参数 {name} 的 label 必须是字符串")
    elif isinstance(label, str) and len(label) > 40:
        errors.append(f"参数 {name} 的 label 长度须在 40 字符以内")
    if "required" in pdecl and not isinstance(pdecl["required"], bool):
        errors.append(f"参数 {name} 的 required 必须是布尔值")
    if ptype == "select":
        options = pdecl.get("options")
        if (
            not isinstance(options, list)
            or not options
            or len(options) > 20
            or not all(isinstance(o, str) for o in options)
        ):
            errors.append(f"参数 {name} 的 options 必须是非空字符串列表（≤20 项）")
    if ptype == "object":
        properties = pdecl.get("properties")
        if not isinstance(properties, dict):
            errors.append(f"参数 {name} 的 properties 必须是对象")
        elif depth + 1 > _TEMPLATE_PARAM_MAX_DEPTH:
            errors.append(f"参数 {name} 的嵌套深度超过上限（{_TEMPLATE_PARAM_MAX_DEPTH}）")
        else:
            for subname, subdecl in properties.items():
                _validate_param_decl(f"{name}.{subname}", subdecl, errors, depth + 1)
    if ptype == "array":
        items = pdecl.get("items")
        if not isinstance(items, dict):
            errors.append(f"参数 {name} 的 items 必须是对象")
        elif depth + 1 > _TEMPLATE_PARAM_MAX_DEPTH:
            errors.append(f"参数 {name} 的嵌套深度超过上限（{_TEMPLATE_PARAM_MAX_DEPTH}）")
        else:
            _validate_param_decl(f"{name}[]", items, errors, depth + 1)
        for key in ("minItems", "maxItems"):
            value = pdecl.get(key)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                errors.append(f"参数 {name} 的 {key} 必须是非负整数")
        min_items, max_items = pdecl.get("minItems"), pdecl.get("maxItems")
        if (
            isinstance(min_items, int)
            and isinstance(max_items, int)
            and min_items > max_items
        ):
            errors.append(f"参数 {name} 的 minItems 不能大于 maxItems")
    visible_when = pdecl.get("visibleWhen")
    if visible_when is not None:
        if not isinstance(visible_when, dict) or not isinstance(
            visible_when.get("field"), str
        ):
            errors.append(f"参数 {name} 的 visibleWhen 必须是 {{field, equals}} 对象")
        elif not visible_when["field"]:
            errors.append(f"参数 {name} 的 visibleWhen.field 不能为空")


def validate_template_params(params: dict[str, Any]) -> list[str]:
    """A1 参数化向导声明形状校验（docs/97 E-5）+ 打包 ZX（docs/106 §2.2）结构化递归：
    type 枚举、label≤40、required 布尔、select 须 options 非空字符串列表 ≤20、未知键拒绝、
    object/array 递归、visibleWhen 形状；返回中文错误列表（空＝通过）。"""
    errors: list[str] = []
    if not isinstance(params, dict):
        return ["参数声明必须是对象"]
    for pname, pdecl in params.items():
        _validate_param_decl(pname, pdecl, errors)
    return errors


def _validate_param_value(pname: str, pdecl: dict[str, Any], value: Any, errors: list[str]) -> None:
    """打包 ZX（docs/106 §2.2）：instantiate 值校验递归核心。

    标量四型与 A1 E-6 逐字保留；object 须 dict 且按 properties 递归子字段、
    array 须 list 且按 items 递归元素、minItems/maxItems 门控、visibleWhen 不参与值校验。
    """
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
    elif ptype == "object":
        if not isinstance(value, dict):
            errors.append(f"参数 {pname} 必须是对象")
            return
        sub_params = pdecl.get("properties") or {}
        for sub_name, sub_decl in sub_params.items():
            if sub_name not in value:
                if sub_decl.get("required"):
                    errors.append(f"参数 {pname}.{sub_name} 为必填")
                continue
            _validate_param_value(f"{pname}.{sub_name}", sub_decl, value[sub_name], errors)
        for sub_name in value:
            if sub_name not in sub_params:
                errors.append(f"参数 {pname} 含未知子字段：{sub_name}")
    elif ptype == "array":
        if not isinstance(value, list):
            errors.append(f"参数 {pname} 必须是数组")
            return
        items = pdecl.get("items") or {}
        min_items = pdecl.get("minItems")
        max_items = pdecl.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"参数 {pname} 至少需要 {min_items} 项")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"参数 {pname} 最多允许 {max_items} 项")
        for index, item in enumerate(value):
            _validate_param_value(f"{pname}[{index}]", items, item, errors)


def _validate_instantiate_values(
    params: dict[str, Any], values: dict[str, Any]
) -> list[str]:
    """A1 instantiate 值校验（docs/97 E-6）+ 打包 ZX（docs/106 §2.2）递归：
    required 缺失/类型不符/select 范围/object 子字段/array 元素/未知参数名。"""
    errors: list[str] = []
    for pname, pdecl in params.items():
        present = pname in values
        if pdecl.get("required") and not present:
            errors.append(f"参数 {pname} 为必填")
            continue
        if not present:
            continue
        _validate_param_value(pname, pdecl, values[pname], errors)
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


class UserTemplateUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    tags: list[str] | None = Field(default=None, max_length=8)
    category: str | None = Field(default=None, max_length=30)
    graph: dict[str, Any]
    params: dict[str, Any] | None = Field(default=None)
    if_match_version: int | None = Field(default=None, ge=1)


class TemplateInstantiateRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class TemplateImportRequest(BaseModel):
    package: dict[str, Any] | None = None
    url: str | None = None


class MessageTemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: str = "approval"
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    variables: list[str] = Field(default_factory=list, max_length=20)


def _message_template_422(errors: list[str]) -> NoReturn:
    """消息模板形状校验失败统一 422（docs/98 §2.3：中文 detail，码名登记于 docs/03
    与 docs/98——`MESSAGE_TEMPLATE_UNDECLARED_VAR` 等仅在契约层识别，响应体沿用中文惯例）。"""
    raise HTTPException(status_code=422, detail="；".join(errors))


def _message_template_payload(body: MessageTemplateCreateRequest) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        _message_template_422(["模板名称不能为空"])
    errors = validate_message_template(
        name=name,
        kind=body.kind,
        subject=body.subject,
        body=body.body,
        variables=body.variables,
    )
    if errors:
        _message_template_422(errors)
    return {
        "name": name,
        "kind": body.kind,
        "subject": body.subject,
        "body": body.body,
        "variables": list(dict.fromkeys(body.variables)),
    }


class RuleTemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=10)
    config: dict[str, Any]


def _rule_template_422(errors: list[str]) -> NoReturn:
    """规则模板形状校验失败统一 422（docs/102 E-1：中文 detail；码名
    `RULE_TEMPLATE_CONFIG_INVALID` 登记于 docs/03，响应体沿用中文惯例）。"""
    raise HTTPException(status_code=422, detail="；".join(errors))


def _rule_template_payload(body: RuleTemplateCreateRequest) -> dict[str, Any]:
    """用户规则模板写入载荷校验：name 非空、tags 逐个长度、config 必须过
    validate_rules（坏配置 422 `RULE_TEMPLATE_CONFIG_INVALID`）。"""
    name = body.name.strip()
    if not name:
        _rule_template_422(["模板名称不能为空"])
    for tag in body.tags:
        if not 1 <= len(tag) <= 20:
            _rule_template_422(["标签长度须在 1-20 字符之间"])
    errors = validate_rules(body.config)
    if errors:
        _rule_template_422(errors)
    return {"name": name, "description": body.description.strip(), "tags": body.tags, "config": body.config}


def _promote_compile(
    services: TenantServices, graph: dict[str, Any], *, graph_id: str
) -> dict[str, Any]:
    """对草稿试编译：只回报，不写库（docs/128 AT-D4／AT-D5）。"""
    try:
        compile_graph(
            parse_graph(graph),
            graph_id=graph_id,
            graph_resolver=_tenant_graph_resolver(services),
            registry=_runtime_registry(services),
        )
    except GraphValidationError as exc:
        return {
            "ok": False,
            "issues": list(exc.errors),
            "codes": list(exc.codes),
            "params": list(exc.params),
            "locations": list(exc.locations),
        }
    return {"ok": True, "issues": [], "codes": [], "params": [], "locations": []}


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


# O-5 第一道护栏（docs/135 §6.6，2026-10-19）：同步 run 在挂起节点（审批/事件等待）时
# 会**阻塞 HTTP 直到审批或图配置超时**，长期占用 uvicorn 默认线程池（约 40）。
# 并发挂起超过线程池容量 ⇒ 服务不可用且 cancel/审批/GET 全排队（实测复现）。信号量
# 限制同时刻同步 run 数并预留余量给 cancel/审批/查询等轻请求；超限 429（可重试）。
# O-5 彻底解耦（docs/135 §6.9，2026-10-20）：同步 run 不再占用 uvicorn 默认线程池
# （约 40，P0 时挂起可拖垮整个同步面）；端点 async 化后 run 在专用池执行，挂起只占
# 专用池线程，uvicorn 池完全留给 cancel/审批/GET 等同步端点。信号量容量即池容量，
# 保留 429 语义：池满时新请求快速失败，避免专用池队列无限堆积。
_SYNC_RUN_MAX_CONCURRENT = 32
_sync_run_slots = threading.Semaphore(_SYNC_RUN_MAX_CONCURRENT)
_SYNC_RUN_POOL = ThreadPoolExecutor(
    max_workers=_SYNC_RUN_MAX_CONCURRENT,
    thread_name_prefix="atlas-sync-run",
)


def _run_saved_graph_impl(
    graph_id: str,
    payload: dict[str, Any] | None,
    principal: Principal,
) -> RunGraphResponse:
    """同步跑一次图（v1 也承载自走循环，见 `_run_sync_cycle` 与 docs/130 C-D10）。

    **圈启用面**：图级 `loop` 配置存在且 `enabled!==false` ⇒ 本入口按圈会话驱动
    （每圈一次真实运行、各自 run_id，返回**最后一圈**的结果）；缺省配置走原路径逐字不变。
    """
    services = services_for(principal)
    body = payload or {}
    release_version, resolved_version = _resolve_event_version(services, principal, graph_id, body)
    graph = _load_graph_or_404(services, graph_id, release_version)
    event_payload = (body.get("event") or {}).get("payload") or {}
    if body.get("debug") is not None:
        raise HTTPException(status_code=422, detail="单步调试仅支持流式运行 /run/stream")

    payloads: dict[str, dict[str, Any]] = {}

    def _run_once(step) -> Any:
        outcome, response = _run_sync_cycle(
            services,
            principal,
            graph,
            graph_id=graph_id,
            release_version=release_version,
            resolved_version=resolved_version,
            event_payload=event_payload,
            inputs=step.inputs,
            cycle_seq=step.seq if step.group_id else 0,
            cycle_group_id=step.group_id,
            payloads=payloads,
        )
        return outcome

    if cycle_config(graph) is None:
        # 现状路径：不建会话、不发圈号（打包 BN C-D2 的零行为变化承诺）
        outcome, response = _run_sync_cycle(
            services,
            principal,
            graph,
            graph_id=graph_id,
            release_version=release_version,
            resolved_version=resolved_version,
            event_payload=event_payload,
            inputs=body.get("inputs"),
            cycle_seq=0,
            cycle_group_id="",
            payloads=payloads,
        )
        return response if response is not None else RunGraphResponse(
            id=graph_id, **payloads[outcome.run_id]
        )

    session = run_cycle_session(
        services,
        graph,
        tenant_id=principal.tenant_id,
        graph_id=graph_id,
        inputs=dict(body.get("inputs") or {}),
        run_once=_run_once,
    )
    # `runs` 是 public_view 投影（每项是 dict），取最后一圈的 runId 去拿原始 run 结果
    last_run_id = session["runs"][-1]["runId"]
    return RunGraphResponse(id=graph_id, **payloads[last_run_id])


def _run_sync_cycle(
    services: TenantServices,
    principal: Principal,
    graph: Any,
    *,
    graph_id: str,
    release_version: int | None,
    resolved_version: int | None,
    event_payload: dict[str, Any],
    inputs: dict[str, Any] | None,
    cycle_seq: int,
    cycle_group_id: str,
    payloads: dict[str, dict[str, Any]],
) -> tuple[Any, RunGraphResponse | None]:
    """跑**一圈**（= 一次真实运行），返回 `(CycleOutcome, 非 None 的 HTTP 响应 | None)`。

    本函数是同步入口的**全部**既有语义（cancel 句柄、superseded 让位、异常记账与
    灰度评估、监控埋点、frame_sink、usage 上下文）逐字搬运——圈能力只加在"调用几次"
    这一层，不改任何一圈内部的语义（docs/130 C-D1：一圈＝一次真实运行）。

    `cycle_seq`／`cycle_group_id` 缺省 0/''＝非圈运行（与迁移 050 的列缺省同义）。
    `payloads` 由调用方持有，用于把每圈的原始 run 结果带给 HTTP 层（返回最后一圈）。
    """
    graph_view = {"nodes": [{"id": node.id, "type": node.type} for node in graph.nodes]}
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    monitoring = services.monitoring
    run_id = uuid.uuid4().hex
    tracer = Tracer(
        graph_id=graph_id, graph_version=_graph_version(graph_id, release_version)
    )
    services.run_store.begin(
        run_id=run_id, graph_id=graph_id, mode="sync",
        cycle_seq=cycle_seq, cycle_group_id=cycle_group_id,
        resolved_version=resolved_version,  # O-2：带 event 的路由版本落投影（无 event 恒 None）
    )
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
        with usage_context(
            principal.tenant_id,
            run_id=run_id,
            graph_id=graph_id,
            store=services.usage_store,
        ):
            result = run_graph(
                graph,
                inputs=inputs,
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
                secret_provider=_secret_provider,
                memory_repo=services.memory_store,
            )
    except RunSuperseded as exc:
        # docs/62 §2 D-4：输家停止驱动——不写 run 终态、不记监控、不进门控评估，
        # 帧与终态都归赢家；本进程如实返回"仍在挂起"的形状（刻意不新增 run 状态/错误码）。
        logger.info("同步运行让位：%s", exc)
        return (
            CycleOutcome(run_id=run_id, status="suspended", stop_run=True),
            RunGraphResponse(
                id=graph_id,
                status="suspended",
                outputs={},
                trace=[f"{exc.node_id}: superseded by another process"],
            ),
        )
    except WaitTimeoutSuspend as exc:
        # docs/136 打包 BS：挂起节点超时且 onTimeout="suspend"——run 保持 suspended
        # （不 finish、不记监控终态、不清帧、不进 evaluate_after_run），等人工后续恢复。
        logger.info("同步运行超时保持挂起：%s", exc)
        return (
            CycleOutcome(run_id=run_id, status="suspended", stop_run=True),
            RunGraphResponse(
                id=graph_id,
                status="suspended",
                outputs={},
                trace=[f"{exc.node_id}: wait timeout kept suspended"],
            ),
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
        return (
            CycleOutcome(run_id=run_id, status="cancelled", stop_run=True),
            RunGraphResponse(
                id=graph_id,
                status="cancelled",
                outputs={},
                trace=[f"{exc.node_id}: cancelled by user"],
            ),
        )
    except Exception as exc:
        services.run_store.finish(
            run_id=run_id, status="failed", error=_safe_run_error(exc)
        )
        record = monitoring.record_run(
            graph_id=graph_id,
            mode="sync",
            status="error",
            started_at=started_at,
            duration_ms=(time.monotonic() - started) * 1000,
            nodes=[],
            error=_safe_run_error(exc),
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
    payloads[run_id] = result
    return CycleOutcome(
        run_id=run_id,
        status="completed",
        outputs=result.get("outputs", {}),
        variables={},
    ), None


class ApprovalDecisionRequest(BaseModel):
    # M8：旧 {decision, comment?} 仍可用；命中卡片可提交 {actionId, form?}（decision/comment 可省）。
    decision: Literal["approved", "rejected"] | None = None
    comment: str = Field(default="", max_length=500)
    action_id: str | None = Field(default=None, alias="actionId")
    form: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


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


class InterruptionResolveRequest(BaseModel):
    action: str
    reason: str = Field(default="", max_length=500)


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


class EmailDecisionRequest(BaseModel):
    token: str = Field(min_length=1)
    decision: Literal["approved", "rejected"] | None = None
    comment: str = Field(default="", max_length=500)
    action_id: str | None = Field(default=None, alias="actionId")
    form: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class ResumeDebugRequest(BaseModel):
    action: Literal["step", "continue", "stop"]
    # B 包（docs/27 §4.3）：可选 globals 顶层键浅合并覆盖；stop 时忽略。
    globals: dict[str, Any] | None = None


def _report_window(days: int) -> tuple[str, str]:
    """docs/113：窗口 [now-days, now)（UTC ISO）。"""
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=days)).isoformat(), now.isoformat()


def _mask_alert_secret(secret: str) -> str:
    """HTTP 投影脱敏：加签 secret 回 ``***``＋明文尾 4 位；空串回空串（docs/125 §3.2，#3）。

    只作用于出体投影——模型/存储/出向加签发送仍用明文原值。
    """
    return f"***{secret[-4:]}" if secret else ""


def _alert_channel_payload(monitoring: Any) -> dict[str, Any]:
    channel = monitoring.get_alert_channel().model_dump()
    delivery = monitoring.get_alert_channel_delivery().model_dump()
    channel["secret"] = _mask_alert_secret(channel.get("secret", ""))
    channel["lastDelivery"] = delivery if delivery["lastNotifiedAt"] else None
    return channel


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


_MOCK_ORDERS = [
    {"order_id": "12345", "reason": "商品破损", "amount": 299},
    {"order_id": "12346", "reason": "不想要了", "amount": 5000},
]


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


# ---- M11 长期记忆（docs/26 §6 / docs/28 §5.1）：读 viewer+、手动新建/编辑 operate（source=manual）、删 admin；图内 remember 工具仍是运行时写入主路径 ----


class MemoryCreateRequest(BaseModel):
    """手动新建记忆（⑩）；source 由端点固定 manual，不接受入参。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["fact", "preference", "knowledge"]
    content: str = Field(min_length=1, max_length=2000)
    scope: dict[str, str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, str] | None = None


class MemoryUpdateRequest(BaseModel):
    """手动编辑记忆（⑩）：白名单字段子集，至少一个；id/created_at/source/embedding 不可改。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["fact", "preference", "knowledge"] | None = None
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    scope: dict[str, str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, str] | None = None


class KnowledgeImportRequest(BaseModel):
    """知识库文档导入（docs/108 §2.2）：纯文本按段落分段入库，MVP 不做多格式解析。"""

    model_config = ConfigDict(extra="forbid")

    category: Literal["faq", "sop", "manual", "rule", "case"]
    text: str = Field(min_length=1, max_length=200000)
    scope: dict[str, str] | None = None


_KNOWLEDGE_SEGMENT_MAX = 1200
_KNOWLEDGE_IMPORT_LIMIT = 200


def _split_knowledge_segments(text: str) -> list[str]:
    """按段落切分，单段超长硬切（docs/108 §2.4）。

    段落优先（``\\n\\n``）；段内超 ``_KNOWLEDGE_SEGMENT_MAX`` 字符按字符边界硬切；
    空段跳过。返回裁剪后的文本段列表。
    """
    segments: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= _KNOWLEDGE_SEGMENT_MAX:
            segments.append(paragraph)
            continue
        start = 0
        while start < len(paragraph):
            end = min(start + _KNOWLEDGE_SEGMENT_MAX, len(paragraph))
            segment = paragraph[start:end].strip()
            if segment:
                segments.append(segment)
            start = end
    return segments


class EvaluationRunRequest(BaseModel):
    """评估请求体：目标图 + evaluation_task（docs/110 §2.1）。"""

    graph_id: str
    task: EvaluationTask


_CONSOLE_HTML = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Demo 商家售后控制台</title>
<style>body{font-family:sans-serif;max-width:720px;margin:40px auto;padding:0 16px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px}
input,button{padding:6px;margin:4px 0}.refunded{color:#389e0d}.human_review{color:#d46b08}.rejected{color:#a8071a}
</style></head>
<body>
<h1>Demo 商家售后控制台</h1>
<p>Atlas 自动登录的目标系统（W9-W10 模拟平台，账号 demo/demo）。</p>
<div id="loginBox"><input id="u" value="demo" placeholder="用户名">
<input id="p" type="password" value="demo" placeholder="密码">
<button onclick="doLogin()">登录</button></div>
<div id="panel" hidden>
<h2>待处理退款单 <button onclick="loadOrders()">刷新</button></h2>
<table><thead>
<tr><th>订单号</th><th>退款原因</th><th>金额</th></tr></thead><tbody id="rows"></tbody></table>
<h2>AI 已处置</h2>
<table><colgroup><col style="width:96px"><col style="width:150px"><col style="width:90px"><col></colgroup>
<thead><tr><th>订单号</th><th>退款原因</th><th>金额</th><th>处置结果</th></tr></thead>
<tbody id="done"></tbody></table>
<p id="hint" hidden>还没有单子被处置过。在编辑器里运行一笔退款单，再点上面的「刷新」。</p>
</div>
<script>
const STATUS_LABEL = {refunded:'AI 已自动退款', human_review:'AI 转人工（历史标记）', rejected:'人工拒绝退款'};
function money(n){return '¥' + n;}
function render(data){
  document.getElementById('rows').innerHTML = (data.orders||[]).map(o=>
    `<tr><td>${o.order_id}</td><td>${o.reason}</td><td>${money(o.amount)}</td></tr>`).join('')
    || '<tr><td colspan="3">全部处理完了</td></tr>';
  const done = data.processed||[];
  document.getElementById('done').innerHTML = done.map(o=>
    `<tr><td>${o.order_id}</td><td>${o.reason}</td><td>${money(o.amount)
    }</td><td class="${o.status}">${STATUS_LABEL[o.status]||o.status}${
    o.note?'（'+o.note+'）':''}</td></tr>`).join('');
  document.getElementById('hint').hidden = done.length>0;
}
async function loadOrders(){
  const r = await fetch('/api/demo/shop/orders');
  if(!r.ok){ loginBox.hidden = false; panel.hidden = true; return false; }
  render(await r.json());
  loginBox.hidden = true; panel.hidden = false;
  return true;
}
async function doLogin(){
  const r = await fetch('/api/demo/shop/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:u.value,password:p.value})});
  if(!r.ok){alert((await r.json()).detail);return;}
  await loadOrders();
}
// 刷新不该回到登录框：登录态在服务端，进页面先直接要一次数据，要不到才亮出登录框。
loadOrders();
</script></body></html>
"""


# P2-1: route handlers live in per-domain routers (atlas/api/routers/);
# this module keeps app assembly, middleware, lifespan and exception handling.
# Routers are imported after every helper/model below so handlers can import
# shared state back from here without a circular-import failure, and they are
# included before the SPA static mount so "/" stays the last registered route.
from atlas.api.routers import (  # noqa: E402
    system as system_routes,
    connections as connections_routes,
    models as models_routes,
    runtime as runtime_routes,
    channels as channels_routes,
    schedules as schedules_routes,
    reflection as reflection_routes,
    adapters as adapters_routes,
    embeds as embeds_routes,
    agents as agents_routes,
    auth as auth_routes,
    graphs as graphs_routes,
    experiments as experiments_routes,
    templates as templates_routes,
    approvals as approvals_routes,
    recordings as recordings_routes,
    monitoring as monitoring_routes,
    demo as demo_routes,
    memory as memory_routes,
)

app.include_router(system_routes.router)
app.include_router(connections_routes.router)
app.include_router(models_routes.router)
app.include_router(runtime_routes.router)
app.include_router(channels_routes.router)
app.include_router(schedules_routes.router)
app.include_router(reflection_routes.router)
app.include_router(adapters_routes.router)
app.include_router(embeds_routes.router)
app.include_router(agents_routes.router)
app.include_router(auth_routes.router)
app.include_router(graphs_routes.router)
app.include_router(experiments_routes.router)
app.include_router(templates_routes.router)
app.include_router(approvals_routes.router)
app.include_router(recordings_routes.router)
app.include_router(monitoring_routes.router)
app.include_router(demo_routes.router)
app.include_router(memory_routes.router)


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
