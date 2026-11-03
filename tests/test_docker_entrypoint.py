"""docker-entrypoint.sh 行为测试（2026-11-02 评审 P2-5）。

入口脚本的迁移/播种步骤依赖环境，测试中把 `python` 换成 PATH 里的空 stub
（立即 exit 0），让控制流直达最后的 `exec "$@"`；以 `echo MARKER` 作探针命令，
断言 --proxy-headers 只在 ATLAS_BEHIND_PROXY=1 时追加，且 workers 闸门不受影响。
不触碰任何真实数据库或网络。
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRYPOINT = os.path.join(REPO_ROOT, "scripts", "ops", "docker-entrypoint.sh")


def _run(tmp_path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    python_stub = stub_dir / "python"
    python_stub.write_text("#!/bin/sh\nexit 0\n")
    python_stub.chmod(python_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    env = {**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}", **env_extra}
    return subprocess.run(
        ["sh", ENTRYPOINT, "echo", "MARKER"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=30,
    )


def test_proxy_headers_off_by_default(tmp_path: Path):
    result = _run(tmp_path, {})
    assert result.returncode == 0, result.stderr
    assert "MARKER" in result.stdout
    assert "--proxy-headers" not in result.stdout


def test_proxy_headers_appended_only_when_behind_proxy(tmp_path: Path):
    result = _run(tmp_path, {"ATLAS_BEHIND_PROXY": "1"})
    assert result.returncode == 0, result.stderr
    assert "MARKER --proxy-headers" in result.stdout


def test_proxy_flag_does_not_weaken_workers_gate(tmp_path: Path):
    result = _run(tmp_path, {"ATLAS_BEHIND_PROXY": "1", "ATLAS_WORKERS": "2"})
    assert result.returncode == 1
    assert "只能单副本运行" in result.stderr
    assert "MARKER" not in result.stdout
