"""打包 A3（docs/99 §3）：敏感变量展开值脱敏 helper。

判据：`source ∈ {env, secret}` 的变量，其**展开值**在投影/落盘通道一律脱敏为
`<redacted:{source}:{引用名}>`；明文绝不落投影、录制、观察、比对与日志。

`redact_sensitive(value, mapping)` 递归处理 dict/list：字符串**等于**任一敏感明文时
替换为占位；等于不上但**内含**明文（插值后的长文本，如 `"body": "token=<secret>"`）
同样替换其子串。mapping 由调用方（loader）从 `global` 展开值与敏感表构建。
"""

from __future__ import annotations

from typing import Any

# 子串替换只处理够长的明文：短值（如 4 位 PIN）作为子串命中时，几乎必然是无关文本
# 里的巧合，替换它会把可读输出啃成占位串。短明文的整串相等通道仍在，子串通道刻意不做。
_MIN_SUBSTRING_LEN = 8


def _mask_string(value: str, mapping: dict[str, str]) -> str:
    exact = mapping.get(value)
    if exact is not None:
        return exact
    masked = value
    # 先长后短：短明文可能是长明文的子串，先换短的会把长明文截断成换不掉的残片。
    for secret in sorted(
        (key for key in mapping if len(key) >= _MIN_SUBSTRING_LEN), key=len, reverse=True
    ):
        if secret in masked:
            masked = masked.replace(secret, mapping[secret])
    return masked


def redact_sensitive(value: Any, mapping: dict[str, str]) -> Any:
    """递归把敏感明文（整串或子串）替换为占位；非匹配值原样返回。

    mapping: {敏感展开值: 占位串}。dict/list 递归，其他类型（数字/布尔/None）不动——
    敏感展开值恒为字符串，数字/布尔等与敏感值绝不等价，无需处理。
    """
    if isinstance(value, str):
        return _mask_string(value, mapping)
    if isinstance(value, dict):
        return {key: redact_sensitive(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item, mapping) for item in value]
    return value
