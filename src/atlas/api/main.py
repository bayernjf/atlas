"""FastAPI Demo 入口（docs/12 §5；T5：Demo 用 Python 实现网关同构接口）。

W7-W8：Graph DSL 保存/读取/编译/运行。存储为进程内字典
（与 T4 进程内总线一致；持久化待业务表 DDL，docs/11 S1）。
W9-W10：退款 Demo 装配（共享 shop 服务/注册表）、SSE 运行进度、
NL 生成草稿、适配器发现、模拟商家售后控制台。
"""

from __future__ import annotations

import json
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from atlas.collaboration.approvals import ApprovalBroker
from atlas.database.adapter import DatabaseHarnessAdapter
from atlas.database.service import DatabaseClient, demo_engine
from atlas.graph.dsl import GraphValidationError, parse_graph
from atlas.graph.loader import compile_graph, run_graph
from atlas.harness.base import Permission
from atlas.harness.registry import AdapterRegistry
from atlas.httpapi.adapter import HttpApiHarnessAdapter
from atlas.httpapi.service import HttpApiClient
from atlas.llm.nl_generate import generate_graph
from atlas.message.adapter import MessageHarnessAdapter
from atlas.message.service import MessageService
from atlas.shop.adapter import ShopHarnessAdapter
from atlas.shop.service import DemoShopService

app = FastAPI(title="Atlas API", version="0.0.1")

# Demo 单例：控制台页面与编译运行的图共享同一份店铺状态
_demo_shop = DemoShopService()
# 通用 HTTP 适配器：默认连接配置来自 ATLAS_HTTPAPI_* 环境变量（04 §4.6）
_http_client = HttpApiClient.from_env()
# 数据适配器：ATLAS_DATABASE_URL 出站连接（与平台 DATABASE_URL 隔离）；
# 未配置时回退内置 SQLite demo 订单库（04 §4.7）
_db_client = DatabaseClient.from_env()
if _db_client is None:
    _db_client = DatabaseClient(demo_engine(), demo=True)
# 消息适配器：进程内消息服务，仅记录不真实投递（04 §4.8）
_message_service = MessageService()
_demo_registry = AdapterRegistry()
_demo_registry.register(
    ShopHarnessAdapter(
        service=_demo_shop,
        granted_permissions={Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL},
    )
)
_demo_registry.register(
    HttpApiHarnessAdapter(
        client=_http_client,
        granted_permissions={Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL},
    )
)
_demo_registry.register(
    DatabaseHarnessAdapter(
        client=_db_client,
        granted_permissions={Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL},
    )
)
_demo_registry.register(
    MessageHarnessAdapter(
        service=_message_service,
        granted_permissions={Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL},
    )
)
# human_approval 节点的进程内审批信号单例（04 §5.6）
_approval_broker = ApprovalBroker()


@app.exception_handler(GraphValidationError)
def graph_validation_handler(_request: Request, exc: GraphValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors})


class GraphStore:
    def __init__(self) -> None:
        self._graphs: dict[str, dict[str, Any]] = {}
        self._updated_at: dict[str, str] = {}
        self._counter = 0

    def save(self, raw: dict[str, Any]) -> str:
        self._counter += 1
        graph_id = f"graph-{self._counter}"
        self._graphs[graph_id] = raw
        self._updated_at[graph_id] = datetime.now(timezone.utc).isoformat()
        return graph_id

    def get(self, graph_id: str) -> dict[str, Any] | None:
        return self._graphs.get(graph_id)

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": graph_id,
                "node_count": len(raw.get("nodes", [])),
                "updated_at": self._updated_at[graph_id],
            }
            for graph_id, raw in self._graphs.items()
        ]

    def clear(self) -> None:
        self._graphs = {}
        self._updated_at = {}
        self._counter = 0


_store = GraphStore()


def _resolve_saved_graph(graph_id: str):
    """subgraph 节点 graph_resolver（04 §5.7）：按 id 解析已保存图，缺失抛 KeyError。"""
    raw = _store.get(graph_id)
    if raw is None:
        raise KeyError(graph_id)
    return parse_graph(raw)


class SaveGraphResponse(BaseModel):
    id: str
    version: int


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


@app.get("/api/adapters")
def list_adapters() -> list[dict[str, Any]]:
    return _demo_registry.list_adapters()


@app.post("/api/graphs", response_model=SaveGraphResponse)
def save_graph(raw: dict[str, Any]) -> SaveGraphResponse:
    graph = parse_graph(raw)
    graph_id = _store.save(raw)
    return SaveGraphResponse(id=graph_id, version=graph.version)


@app.get("/api/graphs")
def list_graphs() -> dict[str, list[dict[str, Any]]]:
    """列出已保存图（subgraph 节点选择器数据源，04 §5.7）。"""
    return {"items": _store.list()}


@app.get("/api/graphs/{graph_id}")
def get_graph(graph_id: str) -> dict[str, Any]:
    raw = _store.get(graph_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return raw


@app.post("/api/graphs/{graph_id}/compile", response_model=CompileResponse)
def compile_saved_graph(graph_id: str) -> CompileResponse:
    raw = _store.get(graph_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    graph = parse_graph(raw)
    compile_graph(graph, graph_id=graph_id, graph_resolver=_resolve_saved_graph)

    incoming = {edge.target for edge in graph.edges}
    outgoing = {edge.source for edge in graph.edges}
    return CompileResponse(
        id=graph_id,
        nodes=[{"id": node.id, "type": node.type, "name": node.name} for node in graph.nodes],
        edges=[{"id": edge.id, "source": edge.source, "target": edge.target} for edge in graph.edges],
        entrypoints=[node.id for node in graph.nodes if node.id not in incoming],
        terminals=[node.id for node in graph.nodes if node.id not in outgoing],
    )


def _load_graph_or_404(graph_id: str):
    raw = _store.get(graph_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return parse_graph(raw)


@app.post("/api/graphs/{graph_id}/run", response_model=RunGraphResponse)
def run_saved_graph(graph_id: str, payload: dict[str, Any] | None = None) -> RunGraphResponse:
    graph = _load_graph_or_404(graph_id)
    result = run_graph(
        graph,
        inputs=(payload or {}).get("inputs"),
        registry=_demo_registry,
        approval_broker=_approval_broker,
        graph_id=graph_id,
        graph_resolver=_resolve_saved_graph,
    )
    return RunGraphResponse(id=graph_id, **result)


@app.post("/api/graphs/{graph_id}/run/stream")
def run_saved_graph_stream(graph_id: str, payload: dict[str, Any] | None = None) -> StreamingResponse:
    """SSE：node_start/node_end/run_end 实时推送到画布（08 §7.3 验收 5）。

    run_graph 在后台线程执行、事件经 queue 实时下发（真流式）；
    human_approval 节点依赖 node_start 在阻塞前到达，前端凭 token 调决策端点放行。
    """
    graph = _load_graph_or_404(graph_id)
    inputs = (payload or {}).get("inputs")

    def event_stream():
        events: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def emit(event: dict[str, Any]) -> None:
            events.put(event)

        def worker() -> None:
            try:
                result = run_graph(
                    graph,
                    inputs=inputs,
                    registry=_demo_registry,
                    approval_broker=_approval_broker,
                    graph_id=graph_id,
                    emit=emit,
                    graph_resolver=_resolve_saved_graph,
                )
                events.put({"__result__": result})
            except Exception as exc:  # 运行期异常经 SSE error 帧下发，不静默吞线程
                events.put({"__error__": f"{type(exc).__name__}: {exc}"})

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            event = events.get()
            if event is None:
                continue
            if "__result__" in event:
                yield (
                    f"event: result\ndata: "
                    f"{json.dumps({'id': graph_id, **event['__result__']}, ensure_ascii=False)}\n\n"
                )
                break
            if "__error__" in event:
                yield f"event: error\ndata: {json.dumps({'detail': event['__error__']}, ensure_ascii=False)}\n\n"
                break
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=500)


@app.get("/api/approvals")
def list_approvals() -> dict[str, list[dict[str, Any]]]:
    """列出当前 pending 的人工审批请求（进程内单例，重启即失）。"""
    return {"items": _approval_broker.list_pending()}


@app.post("/api/approvals/{token}/decision")
def decide_approval(token: str, request: ApprovalDecisionRequest) -> dict[str, Any]:
    if _approval_broker.get(token) is None:
        raise HTTPException(status_code=404, detail=f"审批请求不存在或已清理：{token}")
    if not _approval_broker.resolve(token, request.decision, comment=request.comment):
        raise HTTPException(status_code=409, detail="该审批请求已有决策，重复提交不生效")
    return {"token": token, "decision": request.decision, "resolvedBy": "human"}


@app.post("/api/nl/generate")
def nl_generate(request: NLGenerateRequest) -> dict[str, Any]:
    try:
        return {"graph": generate_graph(request.prompt)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
def demo_messages() -> dict[str, Any]:
    """消息适配器演示查看（04 §4.8）：进程内已记录消息，重启/reset 清空，无真实投递。"""
    return {"items": _message_service.list()}


@app.post("/api/demo/reset")
def demo_reset() -> dict[str, bool]:
    """重置 Demo 数据（店铺恢复 5 笔种子退款单、清空已保存图、释放 pending 审批），供种子客户从头体验。"""
    _demo_shop.reset()
    _store.clear()
    _approval_broker.reset()
    _message_service.reset()
    _db_client.reseed_demo()
    return {"reset": True}


class FeedbackRequest(BaseModel):
    type: Literal["bug", "suggestion"]
    content: str = Field(min_length=1, max_length=2000)
    contact: str = Field(default="", max_length=200)


class FeedbackStore:
    """Phase 1 种子反馈：进程内存储（重启清空，与 Demo 同假设）；reset 不清除。"""

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        self._counter = 0

    def add(self, request: FeedbackRequest) -> dict[str, Any]:
        self._counter += 1
        item = {
            "id": f"feedback-{self._counter}",
            "type": request.type,
            "content": request.content,
            "contact": request.contact,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._items.append(item)
        return item

    def list(self) -> list[dict[str, Any]]:
        return list(self._items)


_feedback_store = FeedbackStore()


@app.post("/api/feedback", status_code=201)
def submit_feedback(request: FeedbackRequest) -> dict[str, Any]:
    return _feedback_store.add(request)


@app.get("/api/feedback")
def list_feedback() -> dict[str, list[dict[str, Any]]]:
    return {"items": _feedback_store.list()}


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
