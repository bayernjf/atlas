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
_WHOLE_TEMPLATE_RE = re.compile(r"\s*\{\{\s*([^{}]+?)\s*\}\}\s*")


def interpolate(template: str, context: dict[str, Any]) -> str:
    """渲染 04 §6.3 ``{{路径}}``；路径解析为 None 时占位符原样保留。"""

    def replace(match: re.Match[str]) -> str:
        value = resolve_path(match.group(1), context)
        return match.group(0) if value is None else str(value)

    return _TEMPLATE_RE.sub(replace, template)


def render_mapping_value(value: Any, context: dict[str, Any]) -> Any:
    """渲染 subgraph inputs 映射值（04 §5.7）：值恰为单个 ``{{路径}}`` 时原类型透传。

    解析为 None（缺失）时回退字符串插值，占位符按 fail-soft 原样保留；
    字面量与混合模板按 §6.3 渲染为字符串。
    """
    text = value if isinstance(value, str) else str(value)
    match = _WHOLE_TEMPLATE_RE.fullmatch(text)
    if match is not None:
        resolved = resolve_path(match.group(1), context)
        if resolved is not None:
            return resolved
    return interpolate(text, context)


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
