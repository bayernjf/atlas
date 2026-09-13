"""OODA 主循环装配（契约：docs/06 §6.1、docs/12 §1.3）。

拓扑：observe → orient →[should_continue]→ decide →[should_act_or_wait]→
act →[should_reflect_or_continue]→ observe | reflect → END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .nodes import (
    act_node,
    decide_node,
    observe_node,
    orient_node,
    reflect_node,
    should_act_or_wait,
    should_continue,
    should_reflect_or_continue,
)
from .state import LoopState


def build_graph():
    graph = StateGraph(LoopState)
    graph.add_node("observe", observe_node)
    graph.add_node("orient", orient_node)
    graph.add_node("decide", decide_node)
    graph.add_node("act", act_node)
    graph.add_node("reflect", reflect_node)

    graph.add_edge(START, "observe")
    graph.add_edge("observe", "orient")
    graph.add_conditional_edges(
        "orient",
        should_continue,
        {"decide": "decide", "__end__": END},
    )
    graph.add_conditional_edges(
        "decide",
        should_act_or_wait,
        {"act": "act", "reflect": "reflect", "__end__": END},
    )
    graph.add_conditional_edges(
        "act",
        should_reflect_or_continue,
        {"observe": "observe", "reflect": "reflect"},
    )
    graph.add_edge("reflect", END)
    return graph.compile()


def initial_state(
    goal: str,
    *,
    max_steps: int = 3,
    memory_id: str = "",
    variables: dict[str, Any] | None = None,
) -> LoopState:
    return LoopState(
        goal=goal,
        messages=[],
        current_node="",
        observations=[],
        variables={"max_steps": max_steps, **(variables or {})},
        status="running",
        memory_id=memory_id,
    )


def run_loop(goal: str, *, max_steps: int = 3, memory_id: str = "") -> LoopState:
    graph = build_graph()
    return graph.invoke(initial_state(goal, max_steps=max_steps, memory_id=memory_id))
