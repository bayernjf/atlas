"""span 级链路追踪（M10；04 §5.15 / 06 §6.15 / 03 ``trace_span``，ADR T21）。

与 OTel 同形的最小 span 模型，纯 stdlib、不引 OTel SDK。进程内 contextvars 传播，
``Tracer`` 经 ``run_graph(tracer=...)`` 显式透传。
"""

from .tracer import (
    KIND_APPROVAL,
    KIND_NODE,
    KIND_PARALLEL,
    KIND_RUN,
    KIND_SUBGRAPH,
    KIND_TASK_DISPATCH,
    KIND_TASK_DONE,
    KIND_TOOL,
    Span,
    Tracer,
    current_span,
    new_span_id,
    new_trace_id,
)

__all__ = [
    "Span",
    "Tracer",
    "current_span",
    "new_span_id",
    "new_trace_id",
    "KIND_RUN",
    "KIND_NODE",
    "KIND_TOOL",
    "KIND_PARALLEL",
    "KIND_SUBGRAPH",
    "KIND_TASK_DISPATCH",
    "KIND_TASK_DONE",
    "KIND_APPROVAL",
]
