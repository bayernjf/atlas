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

#: prod 首任管理员的引导口令环境变量（docs/66 打包 L，补 docs/64 J-1c 的进门缺口）。
PROD_BOOTSTRAP_PASSWORD_ENV = "ATLAS_ADMIN_BOOTSTRAP_PASSWORD"


def read_env_profile() -> str:
    """读取 ATLAS_ENV；缺省 dev，非法值 raise ValueError。大小写不敏感。"""
    raw = os.getenv("ATLAS_ENV", "dev").strip().lower()
    if raw not in _VALID_PROFILES:
        raise ValueError(
            f"ATLAS_ENV 非法值 {raw!r}，合法值 {list(_VALID_PROFILES)}"
        )
    return raw


def prod_bootstrap_password() -> str | None:
    """prod 的首任管理员引导口令；非 prod 返回 None（演示/测试形态零变化）。

    prod 下缺失或不过口令策略即 raise——这是 fail-closed 的正身：宁可不起来，也不要起来一个
    "谁都进不去"或"仓库明文口令可登"的实例（docs/63 §0A N1）。口令只校验、不回显、不写日志。
    """
    if read_env_profile() != "prod":
        return None
    from atlas.iam.passwords import validate_password  # 函数级导入，避开 iam→security 环

    raw = os.getenv(PROD_BOOTSTRAP_PASSWORD_ENV, "").strip()
    if not raw:
        raise RuntimeError(
            f"ATLAS_ENV=prod 拒绝启动：缺少 {PROD_BOOTSTRAP_PASSWORD_ENV}"
            "（prod 不播种仓库内明文口令，首任管理员需要引导口令，见 docs/66）"
        )
    try:
        validate_password(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"ATLAS_ENV=prod 拒绝启动：{PROD_BOOTSTRAP_PASSWORD_ENV} 不合口令策略——{exc}"
        ) from exc
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
    # 引导口令单独一条消息：它缺的不是"密钥"而是"进门方式"，混在一起会误导排障。
    prod_bootstrap_password()
