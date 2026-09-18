"""span 级链路追踪：与 OTel 同形的最小 span 模型（M10，纯 stdlib）。

契约：04 §5.15 / 06 §6.15 / 03 ``trace_span``；ADR T21（不引 OTel SDK/Collector，
跨服务传播随 D5、正式栈合流随 D11）。

- ``Span`` 字段与 OTel 概念同形：traceId(128bit/32hex)、spanId(64bit/16hex)、
  parentSpanId、kind、attrs、status、起止耗时；``internal`` 标记 subgraph 内部 span，
  导出时可折叠（对齐 04 §5.7 子图 ``emit=None`` 事件不外泄）。
- 进程内传播：``contextvars`` 记「当前 span」，供同线程节点执行器→工具就近取父；
  ``Tracer`` 实例经 ``run_graph(tracer=...)`` 显式透传（SSE worker 在后台线程跑
  run_graph，守 06 §6.12「无线程上下文变量」，不依赖跨线程 contextvars 继承）。
- 完整 span 树经 ``to_tree`` 进程内导出，不进 SSE 高频帧；SSE 三帧只带 span 三元组。
"""

from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Literal

SpanKind = Literal[
    "run",
    "node",
    "tool",
    "parallel",
    "subgraph",
    "task_dispatch",
    "task_done",
    "approval",
]
SpanStatus = Literal["ok", "error"]

# kind 常量（避免调用方拼写漂移，与 19 §2.3.4 对齐）
KIND_RUN = "run"
KIND_NODE = "node"
KIND_TOOL = "tool"
KIND_PARALLEL = "parallel"
KIND_SUBGRAPH = "subgraph"
KIND_TASK_DISPATCH = "task_dispatch"
KIND_TASK_DONE = "task_done"
KIND_APPROVAL = "approval"

# 当前 span（同线程 contextvars；后台线程入口须显式 attach/reset）。
_current_span: contextvars.ContextVar["Span | None"] = contextvars.ContextVar(
    "atlas_current_span", default=None
)


def new_trace_id() -> str:
    """OTel 同形 trace id：128bit / 32 hex。"""
    return uuid.uuid4().hex


def new_span_id() -> str:
    """OTel 同形 span id：64bit / 16 hex。"""
    return uuid.uuid4().hex[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Span:
    """一棵 span 树的节点（进程内对象；导出投影见 ``to_dict``）。"""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str = KIND_NODE
    started_at: str = field(default_factory=_now_iso)
    start_ns: int = field(default_factory=time.perf_counter_ns, repr=False)
    end_ns: int | None = field(default=None, repr=False)
    status: SpanStatus = "ok"
    graph_version: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    internal: bool = False
    children: list["Span"] = field(default_factory=list)

    def end(self, status: SpanStatus = "ok") -> None:
        """结束 span（幂等：重复调用不覆盖首次终态与耗时）。"""
        if self.end_ns is None:
            self.end_ns = time.perf_counter_ns()
            self.status = status

    @property
    def duration_ms(self) -> float:
        end = self.end_ns if self.end_ns is not None else self.start_ns
        return max((end - self.start_ns) / 1_000_000.0, 0.0)

    def context(self) -> dict[str, str]:
        """SSE 帧注入用三元组；root 的 parentSpanId 缺省（19 §2.3.4 超集）。"""
        frame = {"traceId": self.trace_id, "spanId": self.span_id}
        if self.parent_span_id is not None:
            frame["parentSpanId"] = self.parent_span_id
        return frame

    def to_dict(self, include_internal: bool = True) -> dict[str, Any]:
        """导出为可 JSON 化嵌套 dict；include_internal=False 折叠 internal 子树。"""
        node: dict[str, Any] = {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "name": self.name,
            "kind": self.kind,
            "startedAt": self.started_at,
            "durationMs": round(self.duration_ms, 3),
            "status": self.status,
        }
        if self.parent_span_id is not None:
            node["parentSpanId"] = self.parent_span_id
        if self.graph_version is not None:
            node["graphVersion"] = self.graph_version
        if self.internal:
            node["internal"] = True
        if self.attrs:
            node["attrs"] = self.attrs
        visible_children = [
            child
            for child in self.children
            if include_internal or not child.internal
        ]
        if visible_children:
            node["children"] = [
                child.to_dict(include_internal) for child in visible_children
            ]
        return node


class Tracer:
    """一次 run 一棵 span 树；root 为 ``run`` span。

    典型用法（run_graph 内，同线程同步执行）::

        tracer = Tracer(graph_id="refund-flow", graph_version="refund-flow@7")
        with tracer.span("node:tool-refund", kind="node") as node:
            with tracer.span("tool:shop/execute_refund", kind="tool"):
                ...
        tree = tracer.finish()
    """

    def __init__(
        self,
        *,
        graph_id: str = "adhoc",
        graph_version: str | None = None,
        name: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        self._trace_id = trace_id or new_trace_id()
        self.root = Span(
            trace_id=self._trace_id,
            span_id=new_span_id(),
            parent_span_id=None,
            name=name or f"run:{graph_id}",
            kind=KIND_RUN,
            graph_version=graph_version,
        )

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def graph_version(self) -> str | None:
        return self.root.graph_version

    def start_span(
        self,
        name: str,
        *,
        kind: str = KIND_NODE,
        parent: Span | None = None,
        internal: bool = False,
        graph_version: str | None | object = ...,
        **attrs: Any,
    ) -> Span:
        """创建并挂载一个子 span（不结束；通常用 ``span()`` 上下文管理器）。"""
        parent_span = parent if parent is not None else _current_span.get()
        if parent_span is None:
            parent_span = self.root
        span = Span(
            trace_id=self._trace_id,
            span_id=new_span_id(),
            parent_span_id=parent_span.span_id,
            name=name,
            kind=kind,
            graph_version=(
                self.root.graph_version if graph_version is ... else graph_version
            ),
            attrs=dict(attrs),
            internal=internal,
        )
        parent_span.children.append(span)
        return span

    @contextmanager
    def span(
        self,
        name: str,
        *,
        kind: str = KIND_NODE,
        parent: Span | None = None,
        internal: bool = False,
        **attrs: Any,
    ) -> Iterator[Span]:
        """开一个子 span 并设为当前 span；异常→status=error，退出恢复父 span。"""
        span = self.start_span(
            name, kind=kind, parent=parent, internal=internal, **attrs
        )
        token = _current_span.set(span)
        try:
            yield span
        except BaseException:
            span.end("error")
            raise
        else:
            span.end("ok")
        finally:
            _current_span.reset(token)

    def attach(self) -> contextvars.Token["Span | None"]:
        """把 root 设为当前 span（后台线程入口调用，配对 reset 使用）。"""
        return _current_span.set(self.root)

    @staticmethod
    def reset(token: contextvars.Token["Span | None"]) -> None:
        _current_span.reset(token)

    def current_span(self) -> Span:
        """当前 span；未进入任何 span 上下文时回落到 root。"""
        return _current_span.get() or self.root

    def current_context(self) -> dict[str, str]:
        """供 SSE 帧注入的当前 span 三元组。"""
        return self.current_span().context()

    def finish(self, status: SpanStatus = "ok") -> dict[str, Any]:
        """结束 root 并返回完整（含 internal）span 树。"""
        self.root.end(status)
        return self.to_tree(include_internal=True)

    def to_tree(self, include_internal: bool = True) -> dict[str, Any]:
        return self.root.to_dict(include_internal=include_internal)


def current_span() -> Span | None:
    """线程当前 span（未 attach/未在 span 上下文内时为 None）。"""
    return _current_span.get()
