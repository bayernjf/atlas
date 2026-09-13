"""环境配置（单一读取点，变量定义见 .env.example）。

W1 仅消费 DATABASE_URL；不引入 pydantic-settings（10 文档 §2 锁定清单外），
直接读环境变量，缺失时 fail-closed。
"""

from __future__ import annotations

import os


class Settings:
    def __init__(self) -> None:
        self.database_url = os.environ.get("DATABASE_URL", "")

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set; copy .env.example to .env and fill it in"
            )
        return self.database_url


settings = Settings()
