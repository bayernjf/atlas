"""FastAPI Demo 入口（docs/12 §5；T5：Demo 用 Python 实现网关同构接口）。

W7-W8：Graph DSL 保存/读取/编译/运行。存储为进程内字典
（与 T4 进程内总线一致；持久化待业务表 DDL，docs/11 S1）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from atlas.graph.dsl import GraphValidationError, parse_graph
from atlas.graph.loader import compile_graph, run_graph

app = FastAPI(title="Atlas API", version="0.0.1")


@app.exception_handler(GraphValidationError)
def graph_validation_handler(_request: Request, exc: GraphValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors})


class GraphStore:
    def __init__(self) -> None:
        self._graphs: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def save(self, raw: dict[str, Any]) -> str:
        self._counter += 1
        graph_id = f"graph-{self._counter}"
        self._graphs[graph_id] = raw
        return graph_id

    def get(self, graph_id: str) -> dict[str, Any] | None:
        return self._graphs.get(graph_id)


_store = GraphStore()


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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/graphs", response_model=SaveGraphResponse)
def save_graph(raw: dict[str, Any]) -> SaveGraphResponse:
    graph = parse_graph(raw)
    graph_id = _store.save(raw)
    return SaveGraphResponse(id=graph_id, version=graph.version)


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
    compile_graph(graph)

    incoming = {edge.target for edge in graph.edges}
    outgoing = {edge.source for edge in graph.edges}
    return CompileResponse(
        id=graph_id,
        nodes=[{"id": node.id, "type": node.type, "name": node.name} for node in graph.nodes],
        edges=[{"id": edge.id, "source": edge.source, "target": edge.target} for edge in graph.edges],
        entrypoints=[node.id for node in graph.nodes if node.id not in incoming],
        terminals=[node.id for node in graph.nodes if node.id not in outgoing],
    )


@app.post("/api/graphs/{graph_id}/run", response_model=RunGraphResponse)
def run_saved_graph(graph_id: str, payload: dict[str, Any] | None = None) -> RunGraphResponse:
    raw = _store.get(graph_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    graph = parse_graph(raw)
    result = run_graph(graph, inputs=(payload or {}).get("inputs"))
    return RunGraphResponse(id=graph_id, **result)
