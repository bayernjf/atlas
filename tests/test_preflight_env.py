"""生产入口 env 预检：缺哪把密钥点名、用途齐全、prod 真拦、dev 只提示。"""

from pathlib import Path

import pytest

from atlas.security.bootstrap import _REQUIRED_PROD_SECRETS
from scripts.ops.preflight_env import SECRET_PURPOSE, collect_findings

_LEAKY_VARS = (
    "ATLAS_ENV",
    "ATLAS_STORAGE_BACKEND",
    "ATLAS_ALLOW_VOLATILE_STORAGE",
    "ATLAS_ENABLE_DEMO_MOCK",
    "DATABASE_URL",
    "ATLAS_PUBLIC_URL",
    "ATLAS_ADMIN_BOOTSTRAP_PASSWORD",
    "ATLAS_A2A_TASK_TOKEN",
    "ATLAS_METRICS_TOKEN",
    "ATLAS_SHOPIFY_ADMIN_BASE_URL",
    "OPENAI_API_KEY",
    "LITELLM_MODEL",
    "MODEL_HUB_URL",
    "ATLAS_SMTP_HOST",
    "ATLAS_SMTP_FROM",
    "ATLAS_SMTP_PASSWORD",
)


@pytest.fixture
def clean_env(monkeypatch):
    """清掉宿主环境里可能存在的真实配置，让每个用例从同一初始面出发。"""
    for name in _LEAKY_VARS + tuple(f"{item}_FILE" for item in _REQUIRED_PROD_SECRETS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ATLAS_APPROVAL_HMAC_SECRET_FILE", raising=False)
    monkeypatch.delenv("ATLAS_MASTER_KEY_FILE", raising=False)
    monkeypatch.delenv("ATLAS_ADMIN_BOOTSTRAP_PASSWORD_FILE", raising=False)
    return monkeypatch


def _prod_ready(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_STORAGE_BACKEND", "pg")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://atlas:x@localhost:5432/atlas")
    monkeypatch.setenv("ATLAS_APPROVAL_HMAC_SECRET", "h" * 40)
    monkeypatch.setenv("ATLAS_MASTER_KEY", "m" * 40)
    monkeypatch.setenv("ATLAS_ADMIN_BOOTSTRAP_PASSWORD", "AtlasProd!2026")
    monkeypatch.setenv("ATLAS_PUBLIC_URL", "https://atlas.example.com")


def test_every_required_secret_has_a_purpose():
    # 新增必需密钥却忘了写用途时，这里先红：不允许只报名字不解释「缺了会怎样」。
    assert set(SECRET_PURPOSE) == set(_REQUIRED_PROD_SECRETS)
    assert all(text.strip() for text in SECRET_PURPOSE.values())


def test_prod_missing_items_are_named_individually(clean_env):
    clean_env.setenv("ATLAS_ENV", "prod")
    findings = collect_findings("prod", True)
    blocked = {item.name for item in findings if item.level == "BLOCK"}

    assert "ATLAS_APPROVAL_HMAC_SECRET" in blocked
    assert "ATLAS_MASTER_KEY" in blocked
    assert "ATLAS_ADMIN_BOOTSTRAP_PASSWORD" in blocked
    assert "ATLAS_STORAGE_BACKEND" in blocked
    # 每一条阻断都要带用途，运维才知道缺的是「签名」还是「加密」。
    assert all(item.purpose.strip() for item in findings if item.level == "BLOCK")


def test_prod_ready_env_has_no_blocks(clean_env):
    _prod_ready(clean_env)
    findings = collect_findings("prod", True)
    assert [item for item in findings if item.level == "BLOCK"] == []


def test_pg_without_dsn_is_blocked(clean_env):
    _prod_ready(clean_env)
    clean_env.delenv("DATABASE_URL")
    findings = collect_findings("prod", True)
    assert "DATABASE_URL" in {item.name for item in findings if item.level == "BLOCK"}


def test_dev_never_blocks(clean_env):
    clean_env.setenv("ATLAS_ENV", "dev")
    findings = collect_findings("dev", False)
    assert [item for item in findings if item.level == "BLOCK"] == []
    assert any(item.level == "WARN" for item in findings)


def test_demo_mock_allows_memory_storage_but_warns(clean_env):
    _prod_ready(clean_env)
    clean_env.setenv("ATLAS_STORAGE_BACKEND", "memory")
    clean_env.setenv("ATLAS_ENABLE_DEMO_MOCK", "1")
    findings = collect_findings("prod", True)

    assert "ATLAS_STORAGE_BACKEND" not in {item.name for item in findings if item.level == "BLOCK"}
    assert "ATLAS_ENABLE_DEMO_MOCK" in {item.name for item in findings if item.level == "WARN"}


def test_entrypoint_runs_preflight_before_migrations():
    script = (Path(__file__).resolve().parents[1] / "scripts/ops/docker-entrypoint.sh").read_text()
    assert "scripts.ops.preflight_env" in script
    assert script.index("scripts.ops.preflight_env") < script.index("scripts.ops.apply_migrations")
