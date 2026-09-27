# -*- coding: utf-8 -*-
"""ATLAS_ENV 环境档位与 prod fail-closed 密钥门（docs/64 打包 J，J-1a）。

根因（docs/63 S7/S1）：src/atlas 从不读 ATLAS_ENV，缺密钥只 warning 落到仓库内
写死的 dev 常量——"配置不安全照样起得来"。本模块是唯一档位入口：

- read_env_profile() 读 ATLAS_ENV（dev/test/prod，缺省 dev，非法值 raise）；
- read_storage_backend() 读 ATLAS_STORAGE_BACKEND（memory/pg，缺省 memory，非法值 raise）；
- demo_surface_enabled() demo 演示面总开关（非 prod 恒开；prod 仅显式开关才开）；
- assert_prod_secrets() 在 prod 下缺任一必需密钥（或 <32 字节）即 raise，
  由 api 启动处调用，prod 缺密钥拒绝启动（fail-closed）。

非 prod 一律静默通过，本地/演示行为不变。
"""

from __future__ import annotations

import os

_VALID_PROFILES = ("dev", "test", "prod")

#: 存储后端档位（docs/24 §1.2②）：进程内 memory（默认/测试）或持久化 pg。
#: 与 ATLAS_ENV 同属"档位读取器"——大小写不敏感、非法值 raise（拒启而非静默降级）。
_VALID_STORAGE_BACKENDS = ("memory", "pg")

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


def read_storage_backend() -> str:
    """读取 ATLAS_STORAGE_BACKEND；缺省 memory，非法值 raise ValueError。大小写不敏感。

    docs/77 R4：此前三处是裸比较（直读环境变量再与 "pg" 比对），不经校验——声明 prod
    却把值写成 `postgres`／拼错时，进程照起、全部状态落在 RAM、重启即失，而 `/api/ready`
    仍 200。本函数是唯一读取入口：非法值当场拒启，合法值归一。
    """
    raw = os.getenv("ATLAS_STORAGE_BACKEND", "memory").strip().lower()
    if raw not in _VALID_STORAGE_BACKENDS:
        raise ValueError(
            f"ATLAS_STORAGE_BACKEND 非法值 {raw!r}，合法值 {list(_VALID_STORAGE_BACKENDS)}"
        )
    return raw


def demo_surface_enabled() -> bool:
    """demo 演示面总开关（docs/75 打包 P；docs/77 R2/R5 复用同一判定）。

    非 prod（dev/test）恒开；prod 默认关（fail-closed），仅 `ATLAS_ENABLE_DEMO_MOCK=1`
    显式开。档位一律走 `read_env_profile()`（大小写不敏感、拒非法值）。HTTP mock 路由、
    运行期演示适配器（shop/database）、FastAPI 文档面与渠道出向测试缝共用这一处。
    """
    return read_env_profile() != "prod" or os.getenv("ATLAS_ENABLE_DEMO_MOCK") == "1"


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
