"""W4 演练骨架：dry-run 零网络、安全守卫拒绝远端/真实渠道。"""

import json
import sys

from scripts.ops import w4_drill


def test_dry_run_is_offline_and_prints_the_chain(monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.setenv("ATLAS_STORAGE_BACKEND", "memory")
    code = w4_drill.run_dry(strict=False)
    output = capsys.readouterr().out

    assert code == 0
    assert "dry-run" in output
    for step in ("health", "demo-guard", "instantiate", "run", "approve", "settle"):
        assert f"[{step}]" in output


def test_explicit_dry_run_flag_keeps_json_output_clean(monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.setenv("ATLAS_STORAGE_BACKEND", "memory")
    monkeypatch.setattr(sys, "argv", ["w4_drill", "--dry-run", "--json"])

    assert w4_drill.main() == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry-run"


def test_dry_run_refuses_when_prod_env_has_blocks(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_MASTER_KEY", raising=False)
    monkeypatch.delenv("ATLAS_APPROVAL_HMAC_SECRET", raising=False)
    assert w4_drill.run_dry(strict=True) == 1


def test_local_targets_are_recognised():
    assert w4_drill._base_host_is_local("http://127.0.0.1:8000")
    assert w4_drill._base_host_is_local("http://localhost:8000")
    assert w4_drill._base_host_is_local("http://192.168.1.10:8000")
    assert not w4_drill._base_host_is_local("https://atlas.example.com")


def test_execute_refuses_a_remote_target(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["w4_drill", "--execute", "--base-url", "https://atlas.example.com", "--token", "t"],
    )
    assert w4_drill.main() == 2
    assert "REFUSED" in capsys.readouterr().err
