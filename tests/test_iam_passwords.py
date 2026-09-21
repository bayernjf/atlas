"""U227 scrypt 口令哈希纯逻辑（ADR T25，docs/31 §0-1/§7）。"""

from __future__ import annotations

import base64

from atlas.iam.passwords import (
    ALGORITHM,
    DKLEN,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    hash_password,
    verify_password,
)


def test_hash_does_not_contain_plaintext() -> None:
    stored = hash_password("admin123")
    assert "admin123" not in stored
    assert stored.startswith(f"{ALGORITHM}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$")


def test_verify_accepts_correct_password() -> None:
    assert verify_password("admin123", hash_password("admin123"))


def test_verify_rejects_wrong_password() -> None:
    stored = hash_password("admin123")
    assert not verify_password("wrong-password", stored)
    assert not verify_password("", stored)


def test_salt_is_random() -> None:
    assert hash_password("admin123") != hash_password("admin123")


def test_hash_has_16_byte_salt_and_32_byte_digest() -> None:
    _, _, _, _, salt_b64, hash_b64 = hash_password("admin123").split("$")
    assert len(base64.b64decode(salt_b64)) == 16
    assert len(base64.b64decode(hash_b64)) == DKLEN


def test_verify_rejects_malformed_stored() -> None:
    assert not verify_password("admin123", "not-a-hash")
    assert not verify_password("admin123", "scrypt$1$8$1$!!$!!")
    assert not verify_password("admin123", "bcrypt$16384$8$1$YWJj$YWJj")
    assert not verify_password("admin123", "scrypt$notint$8$1$YWJj$YWJj")
