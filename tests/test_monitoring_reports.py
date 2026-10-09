"""打包 AG（docs/113）U1306–U1310：监控运行报表聚合纯函数。"""

from __future__ import annotations

import pytest

from atlas.monitoring.records import RunRecord
from atlas.monitoring.reports import aggregate_runs, bucket_key


def make(
    rid: str,
    started: str,
    status: str = "completed",
    duration: float = 100.0,
    version: int | None = None,
) -> RunRecord:
    return RunRecord(
        id=rid,
        graph_id="g1",
        mode="sync",
        status=status,  # type: ignore[arg-type]
        started_at=started,
        finished_at=started,
        duration_ms=duration,
        nodes=[],
        resolved_version=version,
    )


def test_u1306_aggregate_by_day_counts_and_success_rate():
    runs = [
        make("r1", "2026-10-08T10:00:00+00:00", "completed"),
        make("r2", "2026-10-08T11:00:00+00:00", "completed"),
        make("r3", "2026-10-08T12:00:00+00:00", "error"),
        make("r4", "2026-10-09T09:00:00+00:00", "completed"),
    ]
    report = aggregate_runs(runs, "day")
    assert [b["key"] for b in report] == ["2026-10-08", "2026-10-09"]
    first = report[0]
    assert first["total"] == 3
    assert first["completed"] == 2
    assert first["error"] == 1
    assert first["success_rate"] == round(2 / 3, 4)
    assert report[1]["completed"] == 1
    assert report[1]["success_rate"] == 1.0


def test_u1307_duration_avg_p50_p95_nearest_rank():
    runs = [make(f"r{i}", f"2026-10-08T0{i}:00:00+00:00", duration=d)
            for i, d in enumerate([10.0, 20.0, 30.0, 40.0])]
    bucket = aggregate_runs(runs, "day")[0]
    assert bucket["duration_avg_ms"] == 25.0
    assert bucket["duration_p50_ms"] == 20.0  # ceil(.5*4)=2 → ordered[1]
    assert bucket["duration_p95_ms"] == 40.0  # ceil(.95*4)=4 → ordered[3]


def test_u1308_aggregate_by_version_with_manual_draft():
    runs = [
        make("r1", "2026-10-08T10:00:00+00:00", version=1),
        make("r2", "2026-10-08T11:00:00+00:00", version=1),
        make("r3", "2026-10-08T12:00:00+00:00", version=2),
        make("r4", "2026-10-08T13:00:00+00:00", version=None),
    ]
    report = aggregate_runs(runs, "version")
    assert [b["key"] for b in report] == ["v1", "v2", "manual-draft"]
    assert report[0]["total"] == 2
    assert report[2]["key"] == "manual-draft"
    assert bucket_key(make("x", "2026-10-08T00:00:00+00:00", version=3), "version") == "v3"


def test_u1310_empty_runs_returns_empty_list():
    assert aggregate_runs([], "day") == []
    assert aggregate_runs([], "version") == []


def test_cancelled_counted():
    runs = [make("r1", "2026-10-08T10:00:00+00:00", "cancelled")]
    bucket = aggregate_runs(runs, "day")[0]
    assert bucket["cancelled"] == 1
    assert bucket["success_rate"] == 0
