"""口令哈希（ADR T25，docs/31 §0-1）：标准库 scrypt，零新依赖。

存储串 scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>；验证恒定时间比较。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

ALGORITHM = "scrypt"
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
DKLEN = 32
SALT_BYTES = 16

PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 128
USERNAME_MIN_LEN = 3
USERNAME_MAX_LEN = 64
USERNAME_RE = re.compile(r"^[a-z0-9_.-]+$")

WEAK_PASSWORDS = frozenset(
    {
        "admin123",
        "password",
        "password123",
        "12345678",
        "123456789",
        "qwerty123",
        "11111111",
        "00000000",
        "abc12345",
        "iloveyou",
        "admin@123",
        "welcome1",
    }
)


def validate_password(password: str) -> None:
    """口令策略（docs/31 §0-6）：长度 8-128、弱口令黑名单；不达标抛 ValueError（中文）。"""
    if not PASSWORD_MIN_LEN <= len(password) <= PASSWORD_MAX_LEN:
        raise ValueError(f"密码长度须为 {PASSWORD_MIN_LEN}-{PASSWORD_MAX_LEN} 位")
    if password.lower() in WEAK_PASSWORDS:
        raise ValueError("密码过于常见，请更换更强的密码")


def validate_username(username: str) -> None:
    """用户名 3-64 位 [a-z0-9_.-]（docs/31 §0-6）；不达标抛 ValueError（中文）。"""
    if not USERNAME_MIN_LEN <= len(username) <= USERNAME_MAX_LEN:
        raise ValueError(f"用户名长度须为 {USERNAME_MIN_LEN}-{USERNAME_MAX_LEN} 位")
    if USERNAME_RE.fullmatch(username) is None:
        raise ValueError("用户名仅允许小写字母、数字及 _ . -")


def hash_password(password: str) -> str:
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=DKLEN,
    )
    return "$".join(
        (
            ALGORITHM,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n_raw, r_raw, p_raw, salt_b64, hash_b64 = stored.split("$")
        if algo != ALGORITHM:
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)
