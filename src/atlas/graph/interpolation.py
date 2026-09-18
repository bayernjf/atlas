"""``{{路径}}`` 模板插值（04 §6.3 权威语义）。

抽到独立轻量模块供 ``graph.loader`` 与 ``cards.render`` 共用，避免
cards ↔ loader 循环导入（批 2 起 loader 会调用 cards，cards 只能依赖本模块
而不能反向 import loader）。路径缺失时占位符原样保留（fail-soft，与前端
L1/L2 插值口径一致）。
"""

from __future__ import annotations

import re
from typing import Any

_TEMPLATE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_PATH_SEGMENT_RE = re.compile(r"[^.[\]]+|\[\d+\]")


def interpolate(template: str, context: dict[str, Any]) -> str:
    """渲染 04 §6.3 ``{{路径}}``；路径解析为 None 时占位符原样保留。"""

    def replace(match: re.Match[str]) -> str:
        value = resolve_path(match.group(1), context)
        return match.group(0) if value is None else str(value)

    return _TEMPLATE_RE.sub(replace, template)


def resolve_path(path: str, context: dict[str, Any]) -> Any:
    """按 ``a.b[0].c`` 取值；任一段缺失（键不存在/下标越界/类型不符）返 None。"""

    current: Any = context
    for segment in _PATH_SEGMENT_RE.findall(path):
        if segment.startswith("["):
            index = int(segment[1:-1])
            if not isinstance(current, list) or index >= len(current):
                return None
            current = current[index]
        else:
            if not isinstance(current, dict) or segment not in current:
                return None
            current = current[segment]
    return current
