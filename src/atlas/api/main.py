"""FastAPI Demo 入口（docs/12 §5；T5：Demo 用 Python 实现网关同构接口）。

W7-W8：Graph DSL 保存/读取/编译/运行。存储为进程内字典
（与 T4 进程内总线一致；持久化待业务表 DDL，docs/11 S1）。
W9-W10：退款 Demo 装配（共享 shop 服务/注册表）、SSE 运行进度、
NL 生成草稿、适配器发现、模拟商家售后控制台。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from itertools import count
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
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
from atlas.database.adapter import DatabaseHarnessAdapter
from atlas.database.service import DatabaseClient, demo_engine
from atlas.debug import DebugController, DebugStopped
from atlas.graph.conditions import validate_expression
from atlas.graph.dsl import GraphDSL, GraphValidationError, parse_graph
from atlas.graph.loader import _tool_permissions, compile_graph, run_graph, tool_input_schemas
from atlas.collaboration.cancellations import RunCancelled
from atlas.collaboration.notifications import EmailApprovalNotifier
from atlas.collaboration.email_token import EmailTokenError, TokenIssuer
from atlas.tracing import Tracer
from atlas.harness.base import Permission
from atlas.harness.registry import AdapterRegistry
from atlas.httpapi.adapter import HttpApiHarnessAdapter
from atlas.httpapi.service import HttpApiClient
from atlas.iam.deps import (
    authenticate_login,
    get_principal,
    login_throttle,
    require,
    services_for,
    session_store,
    tenant_registry,
    user_store,
)
from atlas.iam.accounts import UserExists
from atlas.iam.passwords import validate_password, validate_username, verify_password
from atlas.iam.principals import Principal, Role
from atlas.iam.registry import STORAGE_BACKEND, TenantServices
from atlas.llm.nl_generate import generate_graph, validate_param_fills
from atlas.memory.adapter import MemoryHarnessAdapter
from atlas.memory.models import MemoryValidationError
from atlas.message.adapter import MessageHarnessAdapter
from atlas.monitoring import RUN_RING_SIZE, extract_business, extract_node_results
from atlas.observability.audit import AUDITED_METHODS, LOGIN_PATH
from atlas.channels.adapter import ShopifyHarnessAdapter
from atlas.channels.base import ChannelBinding, ChannelError
from atlas.channels.webhooks import (
    HMAC_HEADER,
    SUPPORTED_TOPICS,
    WebhookDeliverer,
    build_envelope,
    verify_shopify_hmac,
)
from atlas.connections.service import ConnectionServiceError
from atlas.observability.health import check_ready
from atlas.observability.metrics_export import render_prometheus
from atlas.openapi.adapter import ImportedApiHarnessAdapter
from atlas.openapi.errors import OpenApiError
from atlas.openapi.parser import parse_document
from atlas.openapi.store import ImportStoreError
from atlas.security.egress import EgressDenied, EgressGuard
from atlas.security.secrets import build_secret_provider_from_env
from atlas.monitoring.silences import OnCallEmpty, current_assignee, is_silence_active
from atlas.recording import (
    RecordingCreateRequest,
    RecordingUpdateRequest,
    ReplayRequest,
    build_tool_mocks,
    clock_anchor,
    collect_steps,
    collect_subgraph_snapshots,
    compare as compare_recording,
    extract_shadow_events,
    HumanOutcome,
    inline_first_resolver,
    preset_all_approvals,
    preset_approvals,
    report_to_csv,
    run_release_gate,
)
from atlas.routing import (
    RolloutConfig,
    RolloutError,
    RolloutState,
    TriggerEvent,
    evaluate_after_run,
)
from atlas.shop.adapter import ShopHarnessAdapter
from atlas.shop.service import DemoShopService
from atlas.storage.frame import card_context_from_frame, remaining_seconds
from atlas.storage.memory import ApprovalBroker, FeedbackRequest
from atlas.storage.pg import get_pg_backend
from atlas.storage.recovery import (
    clear_frame,
    clear_tenant_frames,
    load_pending_frames,
    make_frame_sink,
)
from atlas.template import get_template, list_templates
from atlas.versioning.publish import publish as publish_graph_version
from atlas.versioning.upgrades import subgraph_upgrade_plan

logger = logging.getLogger(__name__)

# docs/35 §2（T2）：审批挂起邮件中的应用入口（前端地址）。
_PUBLIC_URL = os.getenv("ATLAS_PUBLIC_URL", "http://localhost:5174")
# docs/36 §3：邮件深链验签单例（密钥取 ATLAS_APPROVAL_HMAC_SECRET）。
_email_token_issuer = TokenIssuer()


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
            logger.warning("帧 %s 恢复失败、隔离跳过：%s", frame.get("resume_token"), exc)


def _resume_from_frame(engine, frame: dict) -> None:
    """按帧重建 pending（approval 重挂 Event + 剩余 deadline），并重启续跑线程。"""
    services = tenant_registry.get(frame["tenant_id"])
    token = frame["resume_token"]
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
    threading.Thread(
        target=_resume_run, args=(engine, services, frame), daemon=True
    ).start()


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
            graph_id=frame["resume_state"].get("graph_id", ""),
            graph_resolver=_tenant_graph_resolver(services),
            frame_sink=make_frame_sink(engine, frame["tenant_id"], run_id),
            resume=frame,
        )
        if run_id:
            services.run_store.finish(
                run_id=run_id, status="completed",
                outputs=result["outputs"], trace=result["trace"],
            )
        clear_frame(engine, frame["resume_token"])
    except Exception as exc:
        logger.error("续跑 %s 失败：%s", frame.get("resume_token"), exc)
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    recover_pending()
    yield


app = FastAPI(title="Atlas API", version="0.0.1", lifespan=lifespan)

# Demo 单例：控制台页面与编译运行的图共享同一份店铺状态
_demo_shop = DemoShopService()
# 通用 HTTP 适配器：默认连接配置来自 ATLAS_HTTPAPI_* 环境变量（04 §4.6）
_http_client = HttpApiClient.from_env()
# 数据适配器：ATLAS_DATABASE_URL 出站连接（与平台 DATABASE_URL 隔离）；
# 未配置时回退内置 SQLite demo 订单库（04 §4.7）
_db_client = DatabaseClient.from_env()
if _db_client is None:
    _db_client = DatabaseClient(demo_engine(), demo=True)
# Demo 全局基础设施（04 §5.14）：店铺/出向连接/适配器注册不按租户分区；
# 图/录制/反馈/消息/审批/调试/监控每租户一套，由 iam.TenantRegistry 惰性装配。
_FULL_PERMISSIONS = {Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL}
_demo_registry = AdapterRegistry()
_demo_registry.register(
    ShopHarnessAdapter(
        service=_demo_shop,
        granted_permissions=_FULL_PERMISSIONS,
    )
)
_demo_registry.register(
    HttpApiHarnessAdapter(
        client=_http_client,
        granted_permissions=_FULL_PERMISSIONS,
    )
)
_demo_registry.register(
    DatabaseHarnessAdapter(
        client=_db_client,
        granted_permissions=_FULL_PERMISSIONS,
    )
)
# 全局注册表里的 message 实例仅供适配器发现；执行期注册表替换为租户消息服务
_demo_registry.register(MessageHarnessAdapter(granted_permissions=_FULL_PERMISSIONS))
# 全局注册表里的 memory 实例仅供适配器发现；执行期注册表替换为租户记忆存储（docs/26 §5.1）
_demo_registry.register(MemoryHarnessAdapter(granted_permissions=_FULL_PERMISSIONS))

_secret_provider = build_secret_provider_from_env()


@app.exception_handler(GraphValidationError)
def graph_validation_handler(_request: Request, exc: GraphValidationError) -> JSONResponse:
    # locations 为稀疏侧车（04 §6.5/06 §6.13）：有可定位条目时才下发，index 对齐 detail。
    content: dict[str, Any] = {"detail": exc.errors}
    if exc.locations:
        content["locations"] = exc.locations
    return JSONResponse(status_code=422, content=content)


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
    message 适配器替换为当前租户消息服务（04 §5.14 分区；06 §6.12）。"""
    registry = AdapterRegistry()
    for item in _demo_registry.list_adapters():
        adapter = _demo_registry.get(item["id"])
        if item["id"] == "message":
            adapter = MessageHarnessAdapter(
                service=services.message_service, granted_permissions=_FULL_PERMISSIONS
            )
        if item["id"] == "memory":
            adapter = MemoryHarnessAdapter(
                repo=services.memory_store, granted_permissions=_FULL_PERMISSIONS
            )
        registry.register(adapter)
    for view in services.channel_registry.list():
        registry.register(
            ShopifyHarnessAdapter(
                view["id"], services.channel_registry,
                granted_permissions=_FULL_PERMISSIONS,
            )
        )
    for imported in services.openapi_imports.list():
        registry.register(
            ImportedApiHarnessAdapter(
                imported,
                secret_provider=_secret_provider,
                granted_permissions=_FULL_PERMISSIONS,
            )
        )
    return registry


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


@app.get("/metrics")
def prometheus_metrics() -> Response:
    """Prometheus 文本指标拉取端点（无鉴权，部署时由反代/网络层限制访问，见 deploy/）。

    只暴露进程级与按租户聚合的计数/分位数；正式 OTel/Grafana 栈缓做 docs/14 D11。
    """
    snapshots: list[tuple[str, dict[str, Any]]] = []
    for tenant_id in tenant_registry.all_tenant_ids():
        try:
            snapshots.append((tenant_id, tenant_registry.get(tenant_id).monitoring.snapshot_metrics()))
        except Exception as exc:  # 指标端点绝不因单租户快照失败而 500
            logger.warning("metrics snapshot failed for %s: %s", tenant_id, exc)
    body = render_prometheus(storage_backend=STORAGE_BACKEND, tenant_snapshots=snapshots)
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/audit/events")
def list_audit_events(
    principal: Principal = Depends(require("administer")),
    limit: int = 100,
    action: str | None = None,
) -> dict[str, Any]:
    """写操作审计事件（docs/35 §6，T6）：倒序（最新在前），admin only，只读本租户；
    limit clamp 到 1–500；action 为路由动作前缀过滤（如 ``POST /api/graphs``）。"""
    bounded = max(1, min(int(limit), 500))
    action_prefix = action.strip() if action and action.strip() else None
    items = services_for(principal).audit_store.list(
        limit=bounded, action_prefix=action_prefix
    )
    return {"items": items, "limit": bounded}


@app.get("/api/audit/export")
def export_audit_events(
    principal: Principal = Depends(require("administer")),
    action: str | None = None,
    fmt: str | None = Query(default="jsonl", alias="format"),
) -> StreamingResponse:
    """导出审计为 JSONL 附件（admin only，正序旧→新）；v1 仅支持 format=jsonl。"""
    if fmt != "jsonl":
        raise HTTPException(status_code=422, detail="仅支持 format=jsonl")
    action_prefix = action.strip() if action and action.strip() else None
    body = services_for(principal).audit_store.export_jsonl(action_prefix=action_prefix)
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
    return HTTPException(status_code=exc.status_code, detail=str(exc))


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
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _record_channel_audit(
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
        logger.warning("channel audit record failed: %s", exc)


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
def test_channel(binding_id: str, principal: Principal = Depends(require("read"))) -> dict[str, Any]:
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
    threading.Thread(
        target=_webhook_run_worker,
        args=(services, tenant, graph_id, version, event),
        daemon=True,
    ).start()


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


def _webhook_run_worker(
    services: TenantServices, tenant_id: str,
    graph_id: str, version: int, event: TriggerEvent,
) -> None:
    """后台运行钉版图：run 记录独立于 HTTP 请求；失败落 failed，不回传 Shopify。"""
    run_id = uuid.uuid4().hex
    run_store = services.run_store
    run_store.begin(run_id=run_id, graph_id=graph_id, mode="webhook")
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
            graph_id=graph_id,
            graph_resolver=_tenant_graph_resolver(services),
            frame_sink=_frame_sink_for(tenant_id, run_store, run_id),
            graph_version=version,
        )
        run_store.finish(
            run_id=run_id, status="completed",
            outputs=result["outputs"], trace=result["trace"],
        )
    except Exception as exc:
        logger.error("webhook 触发图运行失败 graph=%s@%s", graph_id, version, exc_info=True)
        run_store.finish(
            run_id=run_id, status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )


@app.post("/api/channels/hooks/shopify/{binding_id}")
async def shopify_webhook_ingress(binding_id: str, http_request: Request) -> dict[str, Any]:
    """Shopify 公开入站：先跨租户定位绑定，再在原始 body 上 HMAC 验签，最后才解析 JSON。

    验签通过后一律 200（received/duplicate/ignored）；绑定不存在统一 404、
    签名失败统一 401（不泄漏原因差异）；密钥不可用 503；body/头非法 400。
    """
    located = _locate_binding(binding_id)
    if located is None:
        raise HTTPException(status_code=404, detail="渠道绑定不存在")
    tenant_id, binding, services = located
    secret = services.connection_service.client_secret_for(binding.connection_id)
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook 验签密钥暂不可用")
    raw = await http_request.body()
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
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
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
        if exc.code == "CHANNEL_UNAUTHORIZED":
            return {"items": [], "error": "CHANNEL_UNAUTHORIZED"}
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


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else None


@app.post("/api/auth/login", response_model=LoginResponse)
def login(request: LoginRequest, http_request: Request) -> LoginResponse:
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
    return LoginResponse(token=token, principal=principal)


@app.get("/api/auth/me", response_model=LoginResponse)
def me(request: Request, principal: Principal = Depends(get_principal)) -> LoginResponse:
    return LoginResponse(token=_bearer_token(request) or "", principal=principal)


@app.post("/api/auth/logout")
def logout(request: Request, principal: Principal = Depends(get_principal)) -> dict[str, bool]:
    token = _bearer_token(request)
    if token:
        session_store.revoke(token)
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
    """登录用户改本人密码（docs/31 §3）：旧口令错 400；成功吊销本人其他会话、保留当前。"""
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
        keep_token=_bearer_token(request),
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
    """admin 重置本租户用户密码（docs/31 §3）：404/422；成功吊销该用户全部会话。"""
    if user_store.get(principal.tenant_id, username) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _validate_password_or_422(raw.newPassword)
    user_store.set_password(principal.tenant_id, username, raw.newPassword)
    return {"reset": True}


@app.get("/api/adapters")
def list_adapters(principal: Principal = Depends(require("read"))) -> list[dict[str, Any]]:
    # 执行期注册表在全局基础设施之上合并本租户渠道适配器（docs/38 §1E）
    return _runtime_registry(services_for(principal)).list_adapters()


_OPENAPI_FETCH_TIMEOUT = 10.0
_openapi_egress = EgressGuard.from_env()


def _fetch_openapi_spec(url: str) -> str:
    """API 层 URL 抓取（docs/42 §4）：出向过 EgressGuard，10s、不跟重定向；
    任何取数失败统一折 OPENAPI_FETCH_FAILED。"""
    try:
        _openapi_egress.check(url)
        with httpx.Client(follow_redirects=False) as client:
            response = client.get(url, timeout=_OPENAPI_FETCH_TIMEOUT)
    except (EgressDenied, httpx.HTTPError, ValueError) as exc:
        raise OpenApiError("OPENAPI_FETCH_FAILED", f"规格抓取失败：{exc}") from exc
    if response.status_code >= 400:
        raise OpenApiError(
            "OPENAPI_FETCH_FAILED", f"规格抓取失败：HTTP {response.status_code}"
        )
    return response.text


class OpenApiSourceRequest(BaseModel):
    content: str | None = None
    url: str | None = None
    credentials: dict[str, str] | None = None


class OpenApiCredentialsRequest(BaseModel):
    credentials: dict[str, str]


def _credential_error(name: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "OPENAPI_INVALID_CREDENTIAL", "message": f"未知鉴权方案：{name}"},
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
    return HTTPException(
        status_code=422,
        detail={"code": exc.code, "message": exc.message},
    )


def _parse_openapi_source(raw: OpenApiSourceRequest):
    if (raw.content is None) == (raw.url is None):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "OPENAPI_INVALID_DOCUMENT",
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
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return imported.model_dump()


@app.get("/api/openapi/imports")
def list_openapi_imports(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    items = services_for(principal).openapi_imports.list()
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
    principal: Principal = Depends(require("administer")),
) -> dict[str, bool]:
    if not services_for(principal).openapi_imports.delete(spec_id):
        raise HTTPException(status_code=404, detail="导入规格不存在")
    return {"deleted": True}


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
        if not isinstance(value, str) or not value.strip():
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

    limit 默认 100、上限 200（非整数 query 由 FastAPI 422）；报告进程内 ring 不 PG 化。
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
    presets = preset_all_approvals(graph)
    if presets:
        inputs["approvals"] = {**presets, **(inputs.get("approvals") or {})}
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
) -> dict[str, list[dict[str, Any]]]:
    """列出内置流程模板（列表投影不含 graph，04 §5.10；12 §3.6）。"""
    return {
        "items": [
            {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "tags": template.tags,
                "node_count": len(template.graph["nodes"]),
            }
            for template in list_templates()
        ]
    }


@app.get("/api/templates/{template_id}")
def get_catalog_template(
    template_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """返回模板完整元数据（含 graph），未知 id 404（04 §5.10）。"""
    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    return template.model_dump()


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
    if payload is not None and payload.mock_tools:
        tool_mocks, mocked_tools = build_tool_mocks(case)

    try:
        graph = parse_graph(case.graph)
        emit, take_steps = collect_steps()
        anchor, clock_note = clock_anchor(case)
        inputs = dict(case.inputs or {})
        if payload is not None and payload.inputs_override:
            # 顶层键浅合并（dict 值整体替换）；一次性覆写，不修改已入库用例。
            inputs = {**inputs, **payload.inputs_override}
        presets = preset_approvals(case.steps)
        if presets:
            approvals = dict(inputs.get("approvals") or {})
            approvals.update(presets)
            inputs["approvals"] = approvals
        result = run_graph(
            graph,
            inputs=inputs,
            registry=_runtime_registry(services),
            approval_broker=services.approval_broker,
            graph_id=f"replay-{case.id}",
            emit=emit,
            graph_resolver=inline_first_resolver(
                case.subgraphs, _tenant_graph_resolver(services)
            ),
            now_override=anchor,
            tool_mocks=tool_mocks,
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
        )
        if clock_note:
            report["clock_note"] = clock_note
        report["mocked_tools"] = mocked_tools
        return report
    except Exception as exc:  # 回放失败折叠为报告而非 500
        return {
            "matches": False,
            "baseline_status": case.status,
            "replay_status": "failed",
            "mocked_tools": mocked_tools,
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

    try:
        result = run_graph(
            graph,
            inputs=body.get("inputs"),
            registry=_runtime_registry(services),
            approval_broker=services.approval_broker,
            approval_notifier=EmailApprovalNotifier(
                services.message_service, _PUBLIC_URL, principal.tenant_id
            ),
            graph_id=graph_id,
            graph_resolver=_tenant_graph_resolver(services),
            emit=_metric_collect,
            frame_sink=frame_sink,
            tracer=tracer,
            graph_version=tracer.graph_version,
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
                    approval_notifier=approval_notifier,
                    graph_id=graph_id,
                    emit=recording_emit if monitored else emit,
                    graph_resolver=graph_resolver,
                    debug_controller=debug_controller,
                    frame_sink=frame_sink,
                    tracer=tracer,
                    graph_version=tracer.graph_version if tracer is not None else None,
                    is_cancelled=cancel_event.is_set,
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
                events.put({"__error__": f"{type(exc).__name__}: {exc}"})
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
                logger.warning("审批结果通知失败 token=%s: %s", token, exc)
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
    return services, str(body.get("at", "")), tenant_id


@app.get("/api/approvals/email-view")
def email_approval_view(token: str) -> dict[str, Any]:
    """邮件深链只读视图（docs/36 §3）：无需登录、无副作用（防邮件预取）。"""
    services, approval_token, _ = _resolve_email_signed(token)
    broker = services.approval_broker
    pending = broker.get(approval_token)
    if pending is None:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID)
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
    """最近运行（新→旧，默认 50、上限 200；04 §5.13；按租户分区）。"""
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
    schedule = services_for(principal).monitoring.set_oncall(
        members=members, updated_by=principal.username
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
        graph = generate_graph(request.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    warnings = validate_param_fills(graph, tool_input_schemas(_demo_registry))
    return {"graph": graph, "paramWarnings": warnings}


@app.post("/api/demo/shop/login")
def demo_shop_login(request: DemoLoginRequest) -> dict[str, Any]:
    if not _demo_shop.login(request.username, request.password):
        raise HTTPException(status_code=401, detail="登录失败：用户名或密码错误（demo/demo）")
    return {"logged_in": True}


@app.get("/api/demo/shop/orders")
def demo_shop_orders() -> dict[str, Any]:
    if not _demo_shop.logged_in:
        raise HTTPException(status_code=401, detail="未登录")
    return {"orders": _demo_shop.list_pending_refunds()}


_MOCK_ORDERS = [
    {"order_id": "12345", "reason": "商品破损", "amount": 299},
    {"order_id": "12346", "reason": "不想要了", "amount": 5000},
]


@app.get("/api/demo/mock/orders")
def demo_mock_orders(request: Request) -> dict[str, Any]:
    """API 适配器演示目标（04 §4.6 / 12 §5）：要求 X-Demo-Token: demo-token。"""
    if request.headers.get("x-demo-token") != "demo-token":
        raise HTTPException(status_code=401, detail="缺少或错误的 X-Demo-Token 请求头")
    return {"orders": _MOCK_ORDERS}


@app.post("/api/demo/mock/orders/{order_id}/receipt")
def demo_mock_receipt(order_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """API 适配器演示目标：回显 JSON 请求体，供 POST/body/插值端到端验证。"""
    return {"order_id": order_id, "body": body or {}, "received": True}


# Shopify Admin webhooks 资源的同进程模拟（docs/41 §D；随 demo reset 清空）。
_MOCK_SHOPIFY_WEBHOOKS: dict[int, dict[str, Any]] = {}
_mock_shopify_webhook_seq = count(1)


@app.get("/api/demo/mock/shopify-admin/webhooks.json")
def mock_shopify_admin_webhooks_list() -> dict[str, Any]:
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
    if STORAGE_BACKEND == "pg":
        # PG 档：清挂起帧表（帧是 loader frame_sink 写的，内存 broker 不负责；recordings/feedback 保留）。
        clear_tenant_frames(get_pg_backend().engine, principal.tenant_id)
    _demo_shop.reset()
    _db_client.reseed_demo()
    _MOCK_SHOPIFY_WEBHOOKS.clear()
    return {"reset": True}


@app.post("/api/feedback", status_code=201)
def submit_feedback(
    request: FeedbackRequest, principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    """反馈入口对全部登录角色开放（viewer 可提交，04 §5.14）；按租户分区。"""
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
