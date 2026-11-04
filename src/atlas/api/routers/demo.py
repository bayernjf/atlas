"""P2-1 split: Demo shop/mock routes, NL generation, evaluations and feedback.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
from atlas.evaluation import EvaluationGraphNotFound, run_task
from atlas.graph.loader import tool_input_schemas
from atlas.iam.deps import get_principal, require, tenant_registry
from atlas.iam.principals import Principal
from atlas.llm.metering import usage_context
from atlas.llm.nl_generate import generate_graph, validate_param_fills
from atlas.message.service import MessageSendError
from atlas.storage.memory import FeedbackRequest
from atlas.storage.recovery import clear_tenant_frames
from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from typing import Any
from atlas.api.main import (
    DemoLoginRequest,
    EvaluationRunRequest,
    NLGenerateRequest,
    _CONSOLE_HTML,
    _MOCK_ORDERS,
    _MOCK_SHOPIFY_WEBHOOKS,
    _db_client,
    _demo_shop,
    _heavy_batch_guard,
    _mock_shopify_webhook_seq,
    schedule_store,
)


router: APIRouter = APIRouter()


@router.post("/api/nl/generate")
def nl_generate(
    request: NLGenerateRequest, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    services = _main.services_for(principal)
    try:
        # docs/129 打包 AU：端点级调用（无 run/graph/node，只挂 tenant）。
        with usage_context(principal.tenant_id, store=services.usage_store):
            graph = generate_graph(request.prompt, tenant_id=principal.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    warnings = validate_param_fills(graph, tool_input_schemas(_main._demo_registry))
    return {"graph": graph, "paramWarnings": warnings}


@router.post("/api/demo/shop/login")
def demo_shop_login(request: DemoLoginRequest) -> dict[str, Any]:
    if not _main._demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if not _demo_shop.login(request.username, request.password):
        raise HTTPException(status_code=401, detail="登录失败：用户名或密码错误（demo/demo）")
    return {"logged_in": True}


@router.get("/api/demo/shop/orders")
def demo_shop_orders() -> dict[str, Any]:
    if not _main._demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if not _demo_shop.logged_in:
        raise HTTPException(status_code=401, detail="未登录")
    # orders 仍是"待处理"（老消费方与图里的 shop/list_pending_refunds 语义不变）；
    # processed 是新增的第二段，控制台用它把「已退款」和「转人工」分开显示。
    return {
        "orders": _demo_shop.list_pending_refunds(),
        "processed": _demo_shop.list_processed_refunds(),
    }


@router.get("/api/demo/mock/orders")
def demo_mock_orders(request: Request) -> dict[str, Any]:
    """API 适配器演示目标（04 §4.6 / 12 §5）：要求 X-Demo-Token: demo-token。

    常量串只算"防误用"，不算凭证——它会 404 在这台进程是 prod 档的时候。
    """
    if not _main._demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if request.headers.get("x-demo-token") != "demo-token":
        raise HTTPException(status_code=401, detail="缺少或错误的 X-Demo-Token 请求头")
    return {"orders": _MOCK_ORDERS}


@router.post("/api/demo/mock/orders/{order_id}/receipt")
def demo_mock_receipt(order_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """API 适配器演示目标：回显 JSON 请求体，供 POST/body/插值端到端验证。

    回显就是风险面：prod 档必须 404，否则任何人无需凭证即可让平台原样吐回它收到的体。
    """
    if not _main._demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return {"order_id": order_id, "body": body or {}, "received": True}


@router.get("/api/demo/mock/shopify-admin/webhooks.json")
def mock_shopify_admin_webhooks_list() -> dict[str, Any]:
    if not _main._demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "webhooks": [
            {"id": remote_id, **record}
            for remote_id, record in _MOCK_SHOPIFY_WEBHOOKS.items()
        ]
    }


@router.post("/api/demo/mock/shopify-admin/webhooks.json")
def mock_shopify_admin_webhooks_create(
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _main._demo_mock_enabled():
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


@router.delete("/api/demo/mock/shopify-admin/webhooks/{webhook_id}.json")
def mock_shopify_admin_webhooks_delete(webhook_id: str) -> dict[str, Any]:
    if not _main._demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        remote_id = int(webhook_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Not Found")
    if remote_id not in _MOCK_SHOPIFY_WEBHOOKS:
        raise HTTPException(status_code=404, detail="Not Found")
    del _MOCK_SHOPIFY_WEBHOOKS[remote_id]
    return {}


@router.get("/api/demo/messages")
def demo_messages(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """消息适配器演示查看（04 §4.8）：本租户进程内已记录消息，重启/reset 清空，无真实投递。"""
    return {"items": _main.services_for(principal).message_service.list()}


@router.get("/api/demo/deliveries")
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
        "items": _main.services_for(principal).message_service.list_deliveries(
            bounded, status=status
        )
    }


@router.post("/api/demo/deliveries/{seq}/replay")
def demo_replay_delivery(
    seq: int,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """打包 U（docs/82 D-5）：按原内容重放一条出站失败投递，原失败行不可变。"""
    try:
        result = _main.services_for(principal).message_service.replay_failed(seq)
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


@router.post("/api/evaluations", status_code=201)
@_heavy_batch_guard
def run_evaluation(
    body: EvaluationRunRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """AI 评估 Harness 离线批评估（administer；docs/110 §2）。

    对 graph_id 批量跑 task.test_cases，产出逐 case 结果与 metrics 三元组并持久化。
    任务形状非法 422 ``EVALUATION_TASK_INVALID``（pydantic ValidationError 折叠）；
    图不存在 404 ``EVALUATION_GRAPH_NOT_FOUND``；运行期 case 级异常记 error 不使整批失败。
    """
    services = _main.services_for(principal)
    try:
        run = run_task(services.graph_store, body.graph_id, body.task)
    except EvaluationGraphNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{exc.args[0]}") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"评估任务形状非法（{exc.errors()[0].get('msg', '')}）") from exc
    services.evaluation_store.save(run)
    return run.model_dump(mode="json")


@router.get("/api/evaluations")
def list_evaluations(
    principal: Principal = Depends(require("administer")),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """评估历史列表（administer；docs/110 §2.5）：最新在前，跨租户不可见。"""
    return {"items": _main.services_for(principal).evaluation_store.list(limit=limit)}


@router.post("/api/demo/reset")
def demo_reset(
    principal: Principal = Depends(require("administer")),
) -> dict[str, bool]:
    """重置本租户 Demo 数据（清空已保存图、释放 pending 审批/调试、清空消息、
    清空监控运行/告警并恢复默认规则）；共享 demo 店铺与 demo SQLite 种子同步重建。

    admin only（04 §5.14）。反馈与录制用例为测试资产，按租户保留不在此清除
    （04 §5.11；用例图已快照进自身）。
    """
    # 审查发现 #14（docs/125 §3.7，U1391）：reset 是核弹级租户状态操作，prod 面关时
    # 与同族 demo mock 路由逐字同形 fail-closed 404。administer 鉴权在依赖注入期先跑
    # （匿名先拿 401），门在其后（登录管理员在面关时拿 404，不暴露面是否存在）。
    if not _main._demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    tenant_registry.reset_tenant(principal.tenant_id)
    # docs/68 §1 D-4：图被清掉而注册项还在，就会对着一张不存在的图空转派发。
    schedule_store().reset_tenant(principal.tenant_id)
    if _main.STORAGE_BACKEND == "pg":
        # PG 档：清挂起帧表（帧是 loader frame_sink 写的，内存 broker 不负责；recordings/feedback 保留）。
        clear_tenant_frames(_main.get_pg_backend().engine, principal.tenant_id)
    _demo_shop.reset()
    if _db_client is not None:  # docs/77 R2：prod 未开 demo 面时该适配器未装配
        _db_client.reseed_demo()
    _MOCK_SHOPIFY_WEBHOOKS.clear()
    return {"reset": True}


@router.post("/api/feedback", status_code=201)
def submit_feedback(
    request: FeedbackRequest, principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    """反馈入口对全部登录角色开放（viewer 可提交，04 §5.14）；按租户分区。

    首登强制改密**不**挡这一条（docs/95 U1146 的豁免面里唯一非 auth 路由）：它不读业务数据，
    而被门挡住的人正好需要它报障。新增业务端点若也想用 `Depends(get_principal)` 直接过，
    U1146 会红——要么改 `require()`，要么把理由加进那张表。
    """
    return _main.services_for(principal).feedback_store.add(request)


@router.get("/api/feedback")
def list_feedback(
    principal: Principal = Depends(require("administer")),
) -> dict[str, list[dict[str, Any]]]:
    """反馈列表 admin only（04 §5.14）；只列本租户。"""
    return {"items": _main.services_for(principal).feedback_store.list()}


@router.get("/demo/shop", response_class=HTMLResponse)
def demo_shop_console() -> str:
    if not _main._demo_mock_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return _CONSOLE_HTML
