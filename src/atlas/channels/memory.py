# -*- coding: utf-8 -*-
"""渠道绑定内存存储（docs/38 §1A）；per-tenant，ch-N 顺序 id。"""

from __future__ import annotations

import threading

from atlas.channels.base import ChannelBinding, utc_now_iso


class ChannelStore:
    def __init__(self) -> None:
        self._items: dict[str, ChannelBinding] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def create(self, binding: ChannelBinding) -> ChannelBinding:
        with self._lock:
            self._seq += 1
            binding.id = f"ch-{self._seq}"
            now = utc_now_iso()
            binding.created_at = now
            binding.updated_at = now
            self._items[binding.id] = binding
            return binding

    def get(self, binding_id: str) -> ChannelBinding | None:
        return self._items.get(binding_id)

    def list(self) -> list[ChannelBinding]:
        return [self._items[key] for key in sorted(self._items, key=lambda k: int(k.split("-", 1)[1]))]

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
            self._seq = 0
