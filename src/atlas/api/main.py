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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

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
from atlas.graph.loader import compile_graph, run_graph, tool_input_schemas
from atlas.tracing import Tracer
from atlas.harness.base import Permission
from atlas.harness.registry import AdapterRegistry
from atlas.httpapi.adapter import HttpApiHarnessAdapter
from atlas.httpapi.service import HttpApiClient
from atlas.iam.deps import get_principal, require, services_for, session_store, tenant_registry
from atlas.iam.principals import Principal, authenticate
from atlas.iam.registry import STORAGE_BACKEND, TenantServices
from atlas.llm.nl_generate import generate_graph, validate_param_fills
from atlas.message.adapter import MessageHarnessAdapter
from atlas.monitoring import RUN_RING_SIZE, extract_business, extract_node_results
from atlas.recording import (
    RecordingCreateRequest,
    collect_steps,
    compare as compare_recording,
    preset_approvals,
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
from atlas.storage.memory import FeedbackRequest
from atlas.storage.pg import get_pg_backend
from atlas.storage.recovery import (
    clear_frame,
    clear_tenant_frames,
    load_pending_frames,
    make_frame_sink,
)
from atlas.template import get_template, list_templates
from atlas.versioning.publish import publish as publish_graph_version

logger = logging.getLogger(__name__)


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


@app.exception_handler(GraphValidationError)
def graph_validation_handler(_request: Request, exc: GraphValidationError) -> JSONResponse:
    # locations 为稀疏侧车（04 §6.5/06 §6.13）：有可定位条目时才下发，index 对齐 detail。
    content: dict[str, Any] = {"detail": exc.errors}
    if exc.locations:
        content["locations"] = exc.locations
    return JSONResponse(status_code=422, content=content)


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
        registry.register(adapter)
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
    return {"status": "ok"}


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
def login(request: LoginRequest) -> LoginResponse:
    """账号登录换进程内 sess-token（04 §5.14）；坏凭证 401，不区分用户名/密码错误。"""
    principal = authenticate(request.username, request.password)
    if principal is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = session_store.issue(principal)
    # 触发租户装配，登录后该租户即有独立服务实例
    tenant_registry.get(principal.tenant_id)
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


@app.get("/api/adapters")
def list_adapters(principal: Principal = Depends(require("read"))) -> list[dict[str, Any]]:
    return _demo_registry.list_adapters()


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
    return report


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
    case = services.recording_store.add(
        name=request.name,
        graph_id=request.graph_id,
        graph=raw,
        inputs=request.inputs,
        steps=request.steps,
        status=request.status,
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


@app.delete("/api/recordings/{case_id}")
def delete_recording(
    case_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, bool]:
    if not services_for(principal).recording_store.delete(case_id):
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")
    return {"deleted": True}


@app.post("/api/recordings/{case_id}/replay")
def replay_recording(
    case_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """回放冻结快照：标准 run_graph + 审批决策预置，比对操作序列与逐节点产出。

    回放期异常（如快照内 subgraph 引用的 graphId 已被 reset 删除）折叠为
    replay_status="failed"/matches=false，不抛 500（04 §5.11，06 §6.9）。
    """
    services = services_for(principal)
    case = services.recording_store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"录制用例不存在：{case_id}")

    try:
        graph = parse_graph(case.graph)
        emit, take_steps = collect_steps()
        inputs = dict(case.inputs or {})
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
            graph_resolver=_tenant_graph_resolver(services),
        )
        replay_steps = take_steps()
        tools_by_node = {
            node.id: (node.config.get("tool") if node.type == "tool_call" else None)
            for node in graph.nodes
        }
        return compare_recording(
            case.steps,
            replay_steps,
            tools_by_node=tools_by_node,
            baseline_status=case.status,
            replay_status=result["status"],
        )
    except Exception as exc:  # 回放失败折叠为报告而非 500
        return {
            "matches": False,
            "baseline_status": case.status,
            "replay_status": "failed",
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
        normalized.append({"node_id": node_id, "expression": expression})
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors))
    return normalized


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
    try:
        result = run_graph(
            graph,
            inputs=body.get("inputs"),
            registry=_runtime_registry(services),
            approval_broker=services.approval_broker,
            graph_id=graph_id,
            graph_resolver=_tenant_graph_resolver(services),
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
    graph_resolver = _tenant_graph_resolver(services)
    monitoring = services.monitoring
    run_store = services.run_store
    run_id = uuid.uuid4().hex
    frame_sink = _frame_sink_for(principal.tenant_id, run_store, run_id)
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
            DebugController(debug_session, emit) if debug_session is not None else None
        )
        # debug 会话不是真实运行，全程不埋点（04 §5.13）
        monitored = debug_controller is None
        collected: dict[str, Any] = {}

        def recording_emit(event: dict[str, Any]) -> None:
            if event.get("type") == "node_end":
                collected[event["node_id"]] = event.get("output")
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
                    graph_id=graph_id,
                    emit=recording_emit if monitored else emit,
                    graph_resolver=graph_resolver,
                    debug_controller=debug_controller,
                    frame_sink=frame_sink,
                    tracer=tracer,
                    graph_version=tracer.graph_version if tracer is not None else None,
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
                    )
                    evaluate_after_run(services, record)
                events.put({"__result__": result})
            except DebugStopped as exc:
                events.put({"__stopped__": exc.node_id})
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
                    )
                    evaluate_after_run(services, record)
                events.put({"__error__": f"{type(exc).__name__}: {exc}"})

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
        "running", "suspended", "completed", "failed", "interrupted",
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
    if pending.get("decision") is not None:
        raise HTTPException(status_code=409, detail="该审批请求已有决策，重复提交不生效")

    action_id: str | None = None
    if request.action_id:
        # M8：卡片动作提交，服务端按 action.output 映射 decision/comment（map_action_output 唯一权威）。
        card_template_id = pending.get("cardTemplateId")
        card = get_card(card_template_id) if card_template_id else None
        if card is None:
            raise HTTPException(status_code=422, detail="该审批请求未配置交互卡片或卡片不存在")
        try:
            mapped = map_action_output(card, request.action_id, request.form)
        except CardRenderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        decision = mapped["decision"]
        comment = mapped.get("comment", "")
        action_id = request.action_id
    else:
        if request.decision is None:
            raise HTTPException(
                status_code=422,
                detail="请提供 decision（approved/rejected）或卡片 actionId",
            )
        decision = request.decision
        comment = request.comment

    if not broker.resolve(token, decision, comment=comment, action_id=action_id):
        raise HTTPException(status_code=409, detail="该审批请求已有决策，重复提交不生效")
    result: dict[str, Any] = {"token": token, "decision": decision, "resolvedBy": "human"}
    if action_id:
        result["actionId"] = action_id
    return result


class ResumeDebugRequest(BaseModel):
    action: Literal["step", "continue", "stop"]


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
    return {"items": [run.model_dump() for run in runs]}


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


@app.get("/api/demo/messages")
def demo_messages(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """消息适配器演示查看（04 §4.8）：本租户进程内已记录消息，重启/reset 清空，无真实投递。"""
    return {"items": services_for(principal).message_service.list()}


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
