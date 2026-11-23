"""A2A 调用方鉴权（docs/90 §5，A-02）。

两条凭证路径并存：

- `ATLAS_A2A_TASK_TOKENS`：多 token 标签，格式 `caller:tenant:token,...`。
  每个 token 绑定一个调用方与一个租户；真实 run 只接受这类绑定身份。
- `ATLAS_A2A_TASK_TOKEN`：旧单 token，保留给 plan-only 联调；不产生租户绑定，
  因此不能发起真实 run。

prod 档两条都未配置时一律拒绝；非 prod 档放行本地联调身份，同样不能发起
真实 run。token 只以恒定时间比较，日志与返回值都不回显 token。
"""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass

from atlas.security.bootstrap import read_env_profile

logger = logging.getLogger("atlas.a2a.auth")

TOKENS_ENV = "ATLAS_A2A_TASK_TOKENS"
LEGACY_TOKEN_ENV = "ATLAS_A2A_TASK_TOKEN"


@dataclass(frozen=True)
class CallerIdentity:
    """一次 A2A 调用的稳定身份；`tenant_id=None` 表示未绑定租户。"""

    caller_id: str
    tenant_id: str | None
    source: str


def _parse_token_map(raw: str) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 2)
        if len(parts) != 3 or not all(part.strip() for part in parts):
            logger.warning(
                "%s 存在格式不合法的条目（期望 caller:tenant:token），已忽略", TOKENS_ENV
            )
            continue
        caller, tenant, token = (part.strip() for part in parts)
        entries.append((caller, tenant, token))
    return entries


def authenticate(authorization: str | None) -> CallerIdentity | None:
    """把 Authorization 头解析成调用方身份；失败返回 None（路由回 401）。"""
    header = authorization or ""
    tokens_raw = os.environ.get(TOKENS_ENV, "").strip()
    if tokens_raw:
        for caller, tenant, token in _parse_token_map(tokens_raw):
            if hmac.compare_digest(header, f"Bearer {token}"):
                return CallerIdentity(caller_id=caller, tenant_id=tenant, source="token-map")
        # 配了多 token 标签后不再回退旧单 token，避免「旧 token 绕过租户绑定」。
        return None

    legacy = os.environ.get(LEGACY_TOKEN_ENV, "").strip()
    if legacy:
        if hmac.compare_digest(header, f"Bearer {legacy}"):
            return CallerIdentity(caller_id="legacy", tenant_id=None, source="legacy-token")
        return None

    if read_env_profile() == "prod":
        logger.warning("%s 未配置：/api/a2a/tasks 已 fail-closed 关闭（prod）", TOKENS_ENV)
        return None
    logger.warning(
        "%s 未配置：/api/a2a/tasks 在非 prod 档使用本地联调身份（不能发起真实 run）",
        TOKENS_ENV,
    )
    return CallerIdentity(caller_id="local-dev", tenant_id=None, source="insecure-dev")
