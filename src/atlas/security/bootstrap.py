# -*- coding: utf-8 -*-
"""ATLAS_ENV 环境档位与 prod fail-closed 密钥门（docs/64 打包 J，J-1a）。

根因（docs/63 S7/S1）：src/atlas 从不读 ATLAS_ENV，缺密钥只 warning 落到仓库内
写死的 dev 常量——"配置不安全照样起得来"。本模块是唯一档位入口：

- read_env_profile() 读 ATLAS_ENV（dev/test/prod，缺省 dev，非法值 raise）；
- assert_prod_secrets() 在 prod 下缺任一必需密钥（或 <32 字节）即 raise，
  由 api 启动处调用，prod 缺密钥拒绝启动（fail-closed）。

非 prod 一律静默通过，本地/演示行为不变。
"""

from __future__ import annotations

import os

_VALID_PROFILES = ("dev", "test", "prod")

# prod 下必须配置的两把密钥（≥32 字节），缺任一即拒绝启动。
_REQUIRED_PROD_SECRETS = ("ATLAS_APPROVAL_HMAC_SECRET", "ATLAS_MASTER_KEY")
_MIN_SECRET_BYTES = 32


def read_env_profile() -> str:
    """读取 ATLAS_ENV；缺省 dev，非法值 raise ValueError。大小写不敏感。"""
    raw = os.getenv("ATLAS_ENV", "dev").strip().lower()
    if raw not in _VALID_PROFILES:
        raise ValueError(
            f"ATLAS_ENV 非法值 {raw!r}，合法值 {list(_VALID_PROFILES)}"
        )
    return raw


def assert_prod_secrets() -> None:
    """prod 下缺任一必需密钥（或 <32 字节）即 raise RuntimeError（fail-closed）。"""
    if read_env_profile() != "prod":
        return
    missing: list[str] = []
    for name in _REQUIRED_PROD_SECRETS:
        if len(os.getenv(name, "").strip()) < _MIN_SECRET_BYTES:
            missing.append(name)
    if missing:
        raise RuntimeError(
            "ATLAS_ENV=prod 拒绝启动：缺少或过短的必需密钥 "
            f"{missing}（各需 ≥{_MIN_SECRET_BYTES} 字节随机值，见 .env.example）"
        )
