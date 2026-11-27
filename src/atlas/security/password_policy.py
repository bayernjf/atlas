"""口令策略（docs/31 §0-6）：纯函数，不依赖 IAM 运行时。

放在 security 包而不是 `atlas.iam.passwords`：生产入口 preflight 在迁移前校验引导口令，
若从 IAM 包导入，会触发 `atlas.iam.__init__` → `deps` 的模块级账号播种，空库首启直接
`UndefinedTable: iam_users`。策略本身是纯逻辑，不该带任何 IAM 初始化副作用。
"""

from __future__ import annotations

PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 128

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
    """长度 8-128、弱口令黑名单；不达标抛 ValueError（中文）。"""
    if not PASSWORD_MIN_LEN <= len(password) <= PASSWORD_MAX_LEN:
        raise ValueError(f"密码长度须为 {PASSWORD_MIN_LEN}-{PASSWORD_MAX_LEN} 位")
    if password.lower() in WEAK_PASSWORDS:
        raise ValueError("密码过于常见，请更换更强的密码")
