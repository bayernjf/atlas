"""FastAPI Demo 入口（docs/12 §5；T5：Demo 用 Python 实现网关同构接口）。

W7-W8：Graph DSL 保存/读取/编译/运行。存储为进程内字典
（与 T4 进程内总线一致；持久化待业务表 DDL，docs/11 S1）。
W9-W10：退款 Demo 装配（共享 shop 服务/注册表）、SSE 运行进度、
NL 生成草稿、适配器发现、模拟商家售后控制台。
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import functools
import time
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

from atlas.evaluation import EvaluationTask
from atlas.graph.loader import (
    run_graph,
)
from atlas.collaboration.email_token import TokenIssuer
from atlas.harness.registry import AdapterRegistry
from atlas.harness.runtime import (
    build_base_registry,
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
from atlas.cycles.supervisor import (
    CycleOutcome,
    session_for_run,
)
from atlas.memory.database import ping, wait_for_database
from atlas.message.template_store import (
    validate_message_template,
)
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
from atlas.services import (
    approval_decision,
    background_run,
    run_orchestration,
    run_recovery,
    scheduler_runtime,
)
from atlas.services.error_handlers import register_exception_handlers
from atlas.services.webhook_runtime import build_webhook_callbacks
# P2-1: one test imports _token_ref from atlas.api.main; keep the re-export.
from atlas.services.error_safety import token_ref as _token_ref  # noqa: F401
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


# Tier0 review P2: graph release/rollout/debug helpers live in
# atlas.services.graph_release; the aliases below keep the historical
# main-module names callable (routers import them at module load and tests
# patch through atlas.api.main). _runtime_registry stays a thin wrapper so the
# process-singleton _demo_registry/_secret_provider are read at call time.
from atlas.services import graph_release  # noqa: E402
from atlas.services.graph_release import (  # noqa: F401,E402
    graph_version as _graph_version,
    load_graph_or_404 as _load_graph_or_404,
    resolve_event_version as _resolve_event_version,
    rollout_config_or_422 as _rollout_config_or_422,
    rollout_projection as _rollout_projection,
    tenant_graph_resolver as _tenant_graph_resolver,
    tool_call_from_event as _tool_call_from_event,
    validate_debug as _validate_debug,
)


def _runtime_registry(services: TenantServices) -> AdapterRegistry:
    """执行期适配器注册表：沿用全局 shop/http/database 适配器实例，
    message 适配器替换为当前租户消息服务（04 §5.14 分区；06 §6.12）。

    装配本体在 atlas.harness.runtime（MCP 的 atlas_list_adapters 锚同一张表）。"""
    return graph_release.runtime_registry(
        services, demo_registry=_demo_registry, secret_provider=_secret_provider
    )


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


# RunGraphResponse moved to atlas.services.run_orchestration (Tier0 review P2);
# re-exported with the other run-orchestration aliases below.
from atlas.services.run_orchestration import RunGraphResponse  # noqa: F401,E402

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


def _background_run_deps() -> background_run.BackgroundRunDeps:
    """读 main 当前模块全局构造依赖束（service layer batch 2）。

    薄封装：调用时读 main 模块当前全局，保住测试对 `atlas.api.main.run_graph` /
    `_frame_sink_for` / `_resume_claim_for` 的 monkeypatch 语义。
    """
    return background_run.BackgroundRunDeps(
        run_graph=run_graph,
        frame_sink_for=_frame_sink_for,
        resume_claim_for=_resume_claim_for,
        runtime_registry_of=_runtime_registry,
        tenant_graph_resolver_of=_tenant_graph_resolver,
        secret_provider=_secret_provider,
        storage_backend=STORAGE_BACKEND,
    )


def _background_run_cycle(
    services: TenantServices, tenant_id: str, graph_id: str, version: int,
    event: TriggerEvent, *, mode: str, run_id: str | None,
    cycle_seq: int = 0, cycle_group_id: str = "", inputs: dict[str, Any] | None = None,
    agent_id: str = "",
) -> CycleOutcome:
    """后台跑**一圈**；语义在 atlas.services.background_run.run_background_cycle。

    薄封装：调用时读 main 模块当前全局构造依赖束，保住测试的 monkeypatch 缝
    （service layer batch 2，与 `_run_saved_graph_impl` 同款纪律）。
    """
    return background_run.run_background_cycle(
        services, tenant_id, graph_id, version, event,
        mode=mode, run_id=run_id, cycle_seq=cycle_seq, cycle_group_id=cycle_group_id,
        inputs=inputs, agent_id=agent_id, deps=_background_run_deps(),
    )


def _background_run_worker(
    services: TenantServices, tenant_id: str,
    graph_id: str, version: int, event: TriggerEvent,
    agent_id: str = "", *, mode: str = "webhook", run_id: str | None = None,
) -> None:
    """后台运行钉版图（webhook/入站/定时共用；WorkerFn 同形签名见 webhook_runtime）。

    语义在 atlas.services.background_run.run_background_worker；薄封装保 patch 缝
    （service layer batch 2）。
    """
    background_run.run_background_worker(
        services, tenant_id, graph_id, version, event, agent_id,
        mode=mode, run_id=run_id, deps=_background_run_deps(),
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


# Tier0 review P2: template param validation lives in atlas.services.template_validation;
# these aliases keep the historical main-module names importable (routers import them
# at module load and some tests patch through atlas.api.main).
from atlas.services.template_validation import (  # noqa: F401,E402
    _TEMPLATE_PARAM_KEYS,
    _TEMPLATE_PARAM_MAX_DEPTH,
    _TEMPLATE_PARAM_TYPES,
    template_matches_query as _template_matches_query,
    validate_instantiate_values as _validate_instantiate_values,
    validate_param_decl as _validate_param_decl,
    validate_param_value as _validate_param_value,
    validate_template_params,
)


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
    return graph_release.promote_compile(
        services, graph, graph_id=graph_id, runtime_registry_of=_runtime_registry
    )


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
    """同步跑一次图；业务语义在 atlas.services.run_orchestration（Tier0 review P2）。

    薄封装：调用时读 main 模块当前全局构造依赖束，保住测试对
    `atlas.api.main.run_graph` / `_frame_sink_for` / `_resume_claim_for` /
    `evaluate_after_run` 的 monkeypatch 语义。
    """
    return run_orchestration.run_saved_graph(
        graph_id, payload, principal,
        deps=run_orchestration.SyncRunDeps(
            services_for=services_for,
            run_graph=run_graph,
            frame_sink_for=_frame_sink_for,
            resume_claim_for=_resume_claim_for,
            evaluate_after_run=evaluate_after_run,
            runtime_registry_of=_runtime_registry,
            tenant_graph_resolver_of=_tenant_graph_resolver,
            secret_provider=_secret_provider,
            storage_backend=STORAGE_BACKEND,
            public_url=_PUBLIC_URL,
        ),
    )


class ApprovalDecisionRequest(BaseModel):
    # M8：旧 {decision, comment?} 仍可用；命中卡片可提交 {actionId, form?}（decision/comment 可省）。
    decision: Literal["approved", "rejected"] | None = None
    comment: str = Field(default="", max_length=500)
    action_id: str | None = Field(default=None, alias="actionId")
    form: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class InterruptionResolveRequest(BaseModel):
    action: str
    reason: str = Field(default="", max_length=500)


# Tier0 review P2: approval decision + wait-event parsing live in
# atlas.services.approval_decision; the aliases/wrappers below keep the
# historical main-module names importable (routers import them at module load,
# one test calls _apply_approval_decision directly, and the email-token /
# tenant-registry seams must stay patchable through atlas.api.main).
from atlas.services.approval_decision import (  # noqa: F401,E402
    EMAIL_LINK_INVALID as _EMAIL_LINK_INVALID,
    MAX_WAIT_PAYLOAD_BYTES,
    MAX_WAIT_PAYLOAD_KEYS,
    apply_approval_decision as _apply_approval_decision,
    parse_wait_event_key as _parse_wait_event_key,
    parse_wait_payload as _parse_wait_payload,
)


def _resolve_email_signed(signed: str) -> tuple[Any, str, str]:
    """验签 → peek 已装配租户；语义在 atlas.services.approval_decision（薄封装保 patch 缝）。"""
    return approval_decision.resolve_email_signed(
        signed,
        token_issuer=_email_token_issuer,
        peek_registry=tenant_registry.peek,
    )


def _assert_email_token_recipient(
    signed: str, approval_token: str, broker: Any
) -> None:
    """token 载荷 rcpt 归属校验；语义在 atlas.services.approval_decision（薄封装保 patch 缝）。"""
    approval_decision.assert_email_token_recipient(
        signed, approval_token, broker, token_issuer=_email_token_issuer
    )


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
