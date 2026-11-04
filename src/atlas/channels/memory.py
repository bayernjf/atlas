# -*- coding: utf-8 -*-
"""渠道绑定内存存储（docs/38 §1A）；per-tenant，不可预测 id。

id 以前是顺序号（ch-N），全网可枚举，接到入站端点即可按序号探测 binding。
现在用随机 32 位十六进制；旧格式的存量 binding 仍按原 id 直接命中，不做迁移。
"""

from __future__ import annotations

import secrets
import threading

from atlas.channels.base import ChannelBinding, utc_now_iso


def generate_binding_id() -> str:
    """随机 binding id（`ch-<32 hex>`）；不承载序号，无法从任何序号推出。"""
    return f"ch-{secrets.token_hex(16)}"


class ChannelStore:
    def __init__(self) -> None:
        self._items: dict[str, ChannelBinding] = {}
        self._lock = threading.Lock()

    def create(self, binding: ChannelBinding) -> ChannelBinding:
        with self._lock:
            binding.id = generate_binding_id()
            now = utc_now_iso()
            binding.created_at = now
            binding.updated_at = now
            self._items[binding.id] = binding
            return binding

    def get(self, binding_id: str) -> ChannelBinding | None:
        return self._items.get(binding_id)

    def list(self) -> list[ChannelBinding]:
        # Insertion order == creation order, and it is the same order the old
        # numeric sort produced for legacy ch-N ids. Non-numeric ids made the
        # old `int(k.split('-')[1])` key impossible.
        return list(self._items.values())

    def save(self, binding: ChannelBinding) -> ChannelBinding:
        with self._lock:
            binding.updated_at = utc_now_iso()
            self._items[binding.id] = binding
            return binding

    def delete(self, binding_id: str) -> bool:
        with self._lock:
            return self._items.pop(binding_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
