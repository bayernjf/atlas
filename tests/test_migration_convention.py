# -*- coding: utf-8 -*-
"""迁移文件命名与首行来源注释的守护（MIGRATION_CONVENTION.md §3 修订版，2026-09-25）。

规则从"没人执行的模板头"改成两条可机检的：版本号权威在文件名且严格连续；
首个非空行必须是注释，保证文件被打印出来时先读到出处而不是 SQL。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
NAME_RE = re.compile(r"^(\d{3})_[a-z0-9][a-z0-9_]*\.sql$")

#: 029 及以前按 MIGRATION_CONVENTION §3.1 例外留档（守护只管此后新增的迁移）。
BASELINE = 29


def _files() -> list[Path]:
    return sorted(MIGRATIONS.glob("*.sql"))


def test_every_file_name_is_numbered_snake_case():
    offenders = [f.name for f in _files() if not NAME_RE.match(f.name)]
    assert offenders == [], f"命名不合 §2：{offenders}"


def test_versions_are_strictly_incremental_without_gaps():
    numbers = sorted(int(NAME_RE.match(f.name).group(1)) for f in _files() if NAME_RE.match(f.name))
    assert len(numbers) == len(_files()), "有文件名不符合命名规则（上一条已单独报）"
    assert numbers[0] == 1, f"首个迁移应为 001，实为 {numbers[0]:03d}"
    gaps = [n for a, b in zip(numbers, numbers[1:]) for n in [b] if b != a + 1]
    assert not gaps, f"版本号有跳号或重号：{gaps}"


def test_first_non_empty_line_is_a_comment_for_new_migrations():
    """只约束 030 起的新迁移：029 及以前有 23 个文件首行不是注释，改它们＝改已应用迁移。"""
    offenders = []
    for f in _files():
        match = NAME_RE.match(f.name)
        if match is None or int(match.group(1)) <= BASELINE:
            continue  # 命名不合规由第 1 条单独报，这里不重复炸
        first = next((l for l in f.read_text(encoding="utf-8").split("\n") if l.strip()), "")
        if not first.lstrip().startswith("--"):
            offenders.append(f.name)
    assert offenders == [], f"新增迁移首个非空行必须是 -- 注释（MIGRATION_CONVENTION §3 第 2 条）：{offenders}"


# --- 反向门：证明上面三条守护不是永真断言（在本用例自己的 tmp_path 里植缺陷，不碰真目录）---

def test_guards_actually_bite_on_planted_defects(tmp_path, monkeypatch):
    """反向门：在本用例自己的 tmp_path 里造一套完整 001–030，再逐项破坏，
    证明三条守护各自都会红——不碰真目录，且随 CI 常驻。"""
    guards = (
        test_every_file_name_is_numbered_snake_case,
        test_versions_are_strictly_incremental_without_gaps,
        test_first_non_empty_line_is_a_comment_for_new_migrations,
    )

    def baseline() -> dict[str, str]:
        return {f"{n:03d}_t.sql": "-- docs/00\n" for n in range(1, 31)}

    def plant(files: dict[str, str]) -> list[int]:
        for stale in tmp_path.glob("*.sql"):
            stale.unlink()
        for name, body in files.items():
            (tmp_path / name).write_text(body, encoding="utf-8")
        monkeypatch.setattr(sys.modules[__name__], "MIGRATIONS", tmp_path)
        red = []
        for index, guard in enumerate(guards):
            try:
                guard()
            except AssertionError:
                red.append(index)
        return red

    assert plant(baseline()) == [], "干净基线三条必须全绿，否则守护本身写坏了"

    bad_name = plant({**baseline(), "099_BadName.sql": "-- docs/00\n"})
    assert 0 in bad_name and 2 not in bad_name, f"坏命名未被第 1 条抓到（red={bad_name}）"

    gapped = {k: v for k, v in baseline().items() if k != "015_t.sql"}
    assert 1 in plant(gapped), "跳号未被第 2 条抓到"

    no_header = {**baseline(), "030_t.sql": "CREATE TABLE t(i INT);\n"}
    assert 2 in plant(no_header), "030 缺首行注释未被第 3 条抓到"

    legacy_only = {f"{n:03d}_t.sql": "CREATE TABLE t(i INT);\n" for n in range(1, BASELINE + 1)}
    assert 2 not in plant(legacy_only), "第 3 条不该回头管 029 及以前的留档文件"
