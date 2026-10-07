"""打包 A3（docs/99 §3）：敏感变量展开值脱敏 helper。

判据：`source ∈ {env, secret}` 的变量，其**展开值**在投影/落盘通道一律脱敏为
`<redacted:{source}:{引用名}>`；明文绝不落投影、录制、观察、比对与日志。

`redact_sensitive(value, mapping)` 递归处理 dict/list：字符串精确等于任一敏感明文
时替换为占位。mapping 由调用方（loader）从 `global` 展开值与敏感表构建。
"""

from __future__ import annotations

from typing import Any


def redact_sensitive(value: Any, mapping: dict[str, str]) -> Any:
    """递归把等于敏感明文的字符串替换为占位；非匹配值原样返回。

    mapping: {敏感展开值: 占位串}。dict/list 递归，其他类型（数字/布尔/None）不动——
    敏感展开值恒为字符串，数字/布尔等与敏感值绝不等价，无需处理。
    """
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, dict):
        return {key: redact_sensitive(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item, mapping) for item in value]
    return value
