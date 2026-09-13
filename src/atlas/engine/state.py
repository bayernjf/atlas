"""LoopState — OODA 主循环状态（契约：docs/06 §6.1、docs/12 §1.1）。

字段与 06/12 文档完全一致；messages/observations 用 add 归约器，
使节点返回片段时在状态中追加而非覆盖。
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class LoopState(TypedDict):
    goal: str
    messages: Annotated[list, operator.add]       # 对话/执行历史
    current_node: str
    observations: Annotated[list, operator.add]   # 感知结果
    variables: dict                               # 会话变量
    status: str                                   # running/paused/completed/error
    memory_id: str
