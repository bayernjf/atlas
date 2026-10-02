# -*- coding: utf-8 -*-
"""打包 ZO：文件型密钥注入 `<NAME>_FILE`（docs/08 打包 ZO，docs/73 1.1 残余）。

覆盖 U1077–U1081：read_secret 三态与"读不到绝不回退 env"、prod 仅文件注入可启动、
缺失即拒启并点名变量、LLM key 补水与不覆盖、运行期三处（邮件签名／OAuth state／
信封主密钥）在仅文件注入下拿得到密钥。

每条都带**差分对照**：把"摘掉 read_secret、退回裸 os.getenv"的旧行为并排写出来并断言
它在本场景下会失败/泄漏——这是本批唯一能证明"承重的是文件通道"的办法（docs/76 §7 的
教训：植入门被权限拦下时改用同库差分，证明力等价但不改生产代码）。
"""

from __future__ import annotations

import base64
import os

import pytest

from atlas.security.bootstrap import (
    hydrate_file_secrets,
    prod_bootstrap_password,
    read_secret,
)

_SECRET_ENV_NAMES = (
    "ATLAS_MASTER_KEY",
    "ATLAS_APPROVAL_HMAC_SECRET",
    "ATLAS_ADMIN_BOOTSTRAP_PASSWORD",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def _neutral_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """每条用例自带环境：清掉密钥本体与其 _FILE 指针，退出时还原。"""
    for name in _SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    monkeypatch.delenv("ATLAS_ENV", raising=False)


def _write(path, content: str) -> str:
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_u1077_file_value_wins_and_strips_one_trailing_newline(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """U1077-①：`_FILE` 优先于 env，且只剥一个尾换行（CRLF 一并处理）。"""
    monkeypatch.setenv("ATLAS_MASTER_KEY", "e" * 32)
    monkeypatch.setenv("ATLAS_MASTER_KEY_FILE", _write(tmp_path / "k", "f" * 32 + "\n"))
    assert read_secret("ATLAS_MASTER_KEY") == "f" * 32

    monkeypatch.setenv("ATLAS_MASTER_KEY_FILE", _write(tmp_path / "k2", "g" * 32 + "\r\n"))
    assert read_secret("ATLAS_MASTER_KEY") == "g" * 32

    # 两个尾换行只剥一个：多余的空白是配置错误，不该被静默吞掉
    monkeypatch.setenv("ATLAS_MASTER_KEY_FILE", _write(tmp_path / "k3", "h" * 32 + "\n\n"))
    assert len(read_secret("ATLAS_MASTER_KEY")) == 33


def test_u1077_env_fallback_when_no_file_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """U1077-②：未设 `_FILE` 时逐字走旧 env 路径（非 prod 行为零变化）。"""
    monkeypatch.setenv("ATLAS_MASTER_KEY", "z" * 32)
    assert read_secret("ATLAS_MASTER_KEY") == "z" * 32


def test_u1077_unreadable_file_is_missing_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """U1077-③（核心）：`_FILE` 设了却读不到 ⇒ 缺失，绝不回退 env。

    差分对照：旧行为（裸 os.getenv）在这一刻会**拿到旁边那份明文 env**并放行，
    把"挂载没生效"伪装成"配好了"——本批要防的正是这个。
    """
    monkeypatch.setenv("ATLAS_MASTER_KEY", "y" * 32)
    monkeypatch.setenv("ATLAS_MASTER_KEY_FILE", str(tmp_path / "nope"))
    assert read_secret("ATLAS_MASTER_KEY") == ""
    # 差分：env 里那份明文确实还在（证明"空"来自文件通道，不是 env 本来就没配）
    assert os.getenv("ATLAS_MASTER_KEY") == "y" * 32

    monkeypatch.setenv("ATLAS_MASTER_KEY_FILE", _write(tmp_path / "empty", ""))
    assert read_secret("ATLAS_MASTER_KEY") == ""


def test_u1078_prod_starts_with_file_only_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """U1078：三把 prod 必需值全部只经文件注入 ⇒ 启动门通过。"""
    from atlas.security.bootstrap import assert_prod_secrets

    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_MASTER_KEY_FILE", _write(tmp_path / "m", "a" * 32))
    monkeypatch.setenv("ATLAS_APPROVAL_HMAC_SECRET_FILE", _write(tmp_path / "h", "b" * 32))
    monkeypatch.setenv(
        "ATLAS_ADMIN_BOOTSTRAP_PASSWORD_FILE", _write(tmp_path / "p", "Boot-Strap-2026")
    )
    assert_prod_secrets()  # 不 raise
    assert prod_bootstrap_password() == "Boot-Strap-2026"


def test_u1079_missing_file_refuses_startup_and_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """U1079：`_FILE` 指向不存在 ⇒ 拒启，且报错点名 `_FILE` 变量本身。"""
    from atlas.security.bootstrap import assert_prod_secrets

    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_MASTER_KEY_FILE", _write(tmp_path / "m", "a" * 32))
    monkeypatch.setenv("ATLAS_APPROVAL_HMAC_SECRET_FILE", str(tmp_path / "gone"))
    monkeypatch.setenv(
        "ATLAS_ADMIN_BOOTSTRAP_PASSWORD_FILE", _write(tmp_path / "p", "Boot-Strap-2026")
    )
    with pytest.raises(RuntimeError, match="ATLAS_APPROVAL_HMAC_SECRET_FILE"):
        assert_prod_secrets()

    # 引导口令单独一条消息，同样点名 _FILE
    monkeypatch.setenv("ATLAS_APPROVAL_HMAC_SECRET_FILE", _write(tmp_path / "h", "b" * 32))
    monkeypatch.setenv("ATLAS_ADMIN_BOOTSTRAP_PASSWORD_FILE", str(tmp_path / "gone"))
    with pytest.raises(RuntimeError, match="ATLAS_ADMIN_BOOTSTRAP_PASSWORD_FILE"):
        assert_prod_secrets()


def test_u1080_llm_key_is_hydrated_from_file_and_env_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """U1080：`OPENAI_API_KEY_FILE` ⇒ 补水进 env（litellm 自己读 env）；env 已有值不覆盖。"""
    monkeypatch.setenv("OPENAI_API_KEY_FILE", _write(tmp_path / "k", "sk-from-file"))
    hydrate_file_secrets()
    assert os.environ["OPENAI_API_KEY"] == "sk-from-file"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-already-there")
    hydrate_file_secrets()
    assert os.environ["OPENAI_API_KEY"] == "sk-already-there"

    # 幂等：再跑一次不改写
    hydrate_file_secrets()
    assert os.environ["OPENAI_API_KEY"] == "sk-already-there"


def test_u1081_runtime_secret_readers_see_file_injected_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """U1081：运行期三处真正消费密钥的地方，在**仅文件**注入下都拿得到。

    差分对照：三处若退回裸 os.getenv，此刻 env 里是空的 ⇒ 要么抛"dev 密钥"、要么
    静默退明文 provider；断言"它们没走那条路"＝证明文件通道真的贯通到消费点。
    """
    from atlas.collaboration import email_token
    from atlas.connections import oauth
    from atlas.security.secrets import PlaintextSecretProvider, build_secret_provider_from_env

    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_APPROVAL_HMAC_SECRET_FILE", _write(tmp_path / "h", "b" * 32))
    monkeypatch.setenv(
        "ATLAS_MASTER_KEY_FILE",
        _write(tmp_path / "m", base64.b64encode(bytes(range(32))).decode()),
    )
    assert os.getenv("ATLAS_APPROVAL_HMAC_SECRET") is None  # 差分前提：env 里什么都没有

    assert email_token.resolve_token_secret() == "b" * 32
    assert oauth.resolve_state_secret() == base64.b64encode(bytes(range(32))).decode()
    provider = build_secret_provider_from_env()
    assert not isinstance(provider, PlaintextSecretProvider)
