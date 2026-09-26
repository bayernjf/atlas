# -*- coding: utf-8 -*-
"""handoff 索引完整性守护（治理批 #69，2026-09-25）。

handoff 是任务追踪的唯一入口，而"每批在 Active work 留一条、状态行按号引用"这条约定
此前只靠人守：实测 **打包 G／J／K 三批在 Active work 里没有条目**，状态行里
`Active work #66` 指向不存在的条目、`#65` 指向了别的批次。这里把最容易烂的三条变成机检。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

HANDOFF = Path(__file__).resolve().parents[1] / "handoff.md"
ACTIVE_ITEM = re.compile(r"^(\d+)\. ")
POINTER = re.compile(r"Active work #(\d+)")


def _sections() -> tuple[list[str], list[str], list[str]]:
    lines = HANDOFF.read_text(encoding="utf-8").split("\n")
    def start(prefix: str) -> int:
        return next(i for i, l in enumerate(lines) if l.startswith(prefix))
    a0, a1, r0 = start('## Active work'), start('## Recently shipped'), start('## Quality gate')
    return lines[:a0], lines[a0:a1], lines[a1:r0]


def _item_numbers(active: list[str]) -> list[int]:
    return [int(m.group(1)) for l in active if (m := ACTIVE_ITEM.match(l))]


def test_active_work_numbers_are_unique():
    _, active, _ = _sections()
    dupes = {n: c for n, c in Counter(_item_numbers(active)).items() if c > 1}
    assert dupes == {}, f"Active work 编号重复：{dupes}"


def test_every_active_work_pointer_resolves():
    """状态行与正文里每个 `Active work #N` 都必须指向一个真实存在的 Active 条目。"""
    head, active, _ = _sections()
    present = set(_item_numbers(active))
    dangling = sorted({int(m.group(1)) for block in (head, active)
                       for l in block for m in POINTER.finditer(l)} - present)
    assert dangling == [], (
        f"引用了不存在的 Active work 条目：#{dangling}（补条目或改指针，不要留着断链）"
    )


def test_recently_shipped_keeps_at_most_five():
    """上限只数真实条目；`↪` 那行是"旧的滚到哪去了"的指针，不算一条收口。"""
    _, _, shipped = _sections()
    rows = [l for l in shipped if ACTIVE_ITEM.match(l) and '↪' not in l]
    assert len(rows) <= 5, f"Recently shipped 只留最近 5 条，现有 {len(rows)} 条（旧的滚入 handoff-archive-*.md）"
