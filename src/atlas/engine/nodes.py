"""OODA 主循环节点与路由（契约：docs/06 §6.1-6.2、docs/12 §1.2-1.3）。

W1 最小实现：五个节点均为确定性占位逻辑（不调 LLM/Harness），
仅验证 StateGraph 拓扑、状态流转与终止条件；LLM 决策（decide 的
06 §6.2 四步）与 Harness 感知在后续周次接入，函数签名保持不变。
"""

from __future__ import annotations

from typing import Literal

from .state import LoopState

NextNode = Literal["observe", "orient", "decide", "act", "reflect", "__end__"]

MAX_STEPS_VAR = "max_steps"


def observe_node(state: LoopState) -> dict:
    """调用 Harness 感知当前状态（W1：记录一条占位 observation）。"""
    step = state.get("variables", {}).get("step", 0) + 1
    variables = {**state.get("variables", {}), "step": step}
    return {
        "current_node": "observe",
        "status": "running",
        "variables": variables,
        "observations": [f"observation@{step}"],
    }


def orient_node(state: LoopState) -> dict:
    """分析数据，定位进展（W1：占位）。"""
    step = state["variables"]["step"]
    return {
        "current_node": "orient",
        "messages": [f"orient@{step}: progress acknowledged"],
    }


def decide_node(state: LoopState) -> dict:
    """LLM 决策下一步（W1：步数达上限即完成；06 §6.2 的 LLM 决策后接）。"""
    variables = state.get("variables", {})
    step = variables.get("step", 0)
    max_steps = variables.get(MAX_STEPS_VAR, 3)
    if step >= max_steps:
        return {
            "current_node": "reflect",
            "status": "completed",
            "messages": [f"decide@{step}: goal achieved"],
        }
    return {
        "current_node": "act",
        "status": "running",
        "messages": [f"decide@{step}: continue to act"],
    }


def act_node(state: LoopState) -> dict:
    """执行工具/操作（W1：占位）。"""
    step = state["variables"]["step"]
    return {
        "current_node": "act",
        "messages": [f"act@{step}: action executed"],
    }


def reflect_node(state: LoopState) -> dict:
    """阶段性反思（W1：占位）。"""
    step = state["variables"]["step"]
    return {
        "current_node": "reflect",
        "messages": [f"reflect@{step}: loop finished"],
    }


def should_continue(state: LoopState) -> NextNode:
    """orient 后路由：paused/error 终止，否则进入决策。"""
    if state.get("status") in ("paused", "error"):
        return "__end__"
    return "decide"


def should_act_or_wait(state: LoopState) -> NextNode:
    """decide 后路由：completed → reflect；paused/error 终止；否则 act。"""
    status = state.get("status")
    if status == "completed":
        return "reflect"
    if status in ("paused", "error"):
        return "__end__"
    return "act"


def should_reflect_or_continue(state: LoopState) -> NextNode:
    """act 后路由：完成则反思，否则回到感知继续循环。"""
    if state.get("status") == "completed":
        return "reflect"
    return "observe"
