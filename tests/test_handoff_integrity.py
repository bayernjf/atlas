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
# Recently shipped 的行首是状态表情，不是 `N. ` —— 原来的守护按 `^\d+\. ` 数行，
# 于是对真实文件**恒数到 0 行**，「只留最近 5 条」这条规矩其实一直没被守住过
# （实测数行时该区已有 6 条而门是绿的）。两种形状都数，见下面的植行测试。
SHIPPED_ROW = re.compile(r"^(?:\d+\. )?[✅🏁📋🐛📝📌🚀]")


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


def _is_shipped_row(line: str) -> bool:
    """Recently shipped 区里「一条收口」的判据。

    写法演进本身是个缺陷记录：原来写成 `ACTIVE_ITEM.match(l)`（只认 `12. ` 这种编号行），
    而该区实际用 `✅ **…**`／`📋 **…**` 表情行——于是守护对真实文件**恒数到 0 行**，
    实测当时已有 6 条而门是绿的。一条永远不会红的守护比没有守护更糟：它让下一个人以为
    这条规矩被守着。现在表情行与编号行都算，标题／归档指针／`↪` 说明行不算。
    """
    if not line.strip():
        return False
    if line.startswith(("## ", "> ")) or "↪" in line:
        return False
    return True


def _shipped_rows(shipped: list[str]) -> list[str]:
    return [l for l in shipped if _is_shipped_row(l)]


def test_recently_shipped_keeps_at_most_five():
    rows = _shipped_rows(_sections()[2])
    assert len(rows) <= 5, (
        f"Recently shipped 只留最近 5 条，现有 {len(rows)} 条（旧的逐字滚入手写 handoff-archive-*.md）"
    )


def test_recently_shipped_guard_can_actually_count_rows():
    """植行测试：守护的判据自己也要被按住，否则"改对了判据"和"改错了"一样绿。"""
    planted = [
        "## Recently shipped（最近变更；只留最近 5 条）",
        "✅ **表情行收口**",
        "🏁 **另一种表情收口**",
        "101. ✅ **旧编号形状也要继续算**",
        "↪ 这条是滚动指针，不算一条收口",
        "> 更早的完成项见 handoff-archive-*.md",
        "",
        "📋 **第三条**",
    ]
    assert len(_shipped_rows(planted)) == 4, _shipped_rows(planted)
    # 判别对照：旧写法只数到那一条编号行——那正是它一直绿的原因。
    assert [l for l in planted if ACTIVE_ITEM.match(l) and "↪" not in l] == [
        "101. ✅ **旧编号形状也要继续算**"
    ]
