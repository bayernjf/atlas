"""凭证信封、SecretProvider 与运行时秘密注入（docs/32 §2，P0 批 3，ADR T26=(a)）。

设计目标：主密钥只由运行时环境注入（``ATLAS_MASTER_KEY``），绝不入库、不进
图 JSON、不进日志/录制/监控；图与配置里只出现 ``secret://<name>`` 引用或
``enc$...`` 信封，发送前在进程内解析为明文，明文只活在单次请求内存里。

紧凑信封（落配置/落库形态，与算法解耦）：

- AES-GCM（生产）：``enc$v1$<kid>$<b64url iv>$<b64url ciphertext||tag>``
- plain（显式非机密，仅 dev/demo）：``enc$v1$plain$<b64url plaintext>``

装配规则（:func:`build_secret_provider_from_env`）：

- 未配 ``ATLAS_MASTER_KEY`` → :class:`PlaintextSecretProvider`（启动 WARNING，
  仅供 dev/demo）；
- 配置了主密钥 → :class:`AesGcmSecretProvider`（需要 ``cryptography``，ADR
  T26 选定）；密钥非法或加密后端不可用 → fail-closed 启动报错，不静默退回
  明文；生产 provider 拒绝解密 ``plain`` 信封。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Protocol

logger = logging.getLogger("atlas.security.secrets")

ENVELOPE_PREFIX = "enc$"
ENVELOPE_VERSION = "v1"
PLAIN_KID = "plain"
DEFAULT_KEY_ID = "k1"
KEY_LEN = 32  # AES-256
NONCE_LEN = 12  # AES-GCM 推荐 96-bit IV

# 敏感请求/响应头（小写匹配），对外观测/记录通道一律渲染为 ***
SENSITIVE_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie"}
)
REDACTED = "***"

_SECRET_REF_RE = re.compile(r"secret://([A-Za-z0-9_.\-]+)")
# 先匹配 AES 信封（kid$iv$ct），再匹配 plain 信封（plain$pt）
_ENVELOPE_RE = re.compile(
    r"enc\$v1\$[A-Za-z0-9_.\-]+\$[A-Za-z0-9_\-]+\$[A-Za-z0-9_\-]+"
    r"|enc\$v1\$plain\$[A-Za-z0-9_\-]+"
)


class SecretError(Exception):
    """秘密解析/解密失败基类；子类携带 StructuredError 风格 code。"""

    code = "SECRET_UNAVAILABLE"


class SecretUnavailable(SecretError):
    """引用的运行时秘密未注入（或未配置 provider 却出现 secret://）。"""

    code = "SECRET_UNAVAILABLE"


class SecretDecryptError(SecretError):
    """信封非法 / 篡改 / 错 key / 不支持的 alg。"""

    code = "SECRET_DECRYPT_ERROR"


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise SecretDecryptError(f"信封字段不是合法 base64url：{exc}") from exc


def _decode_master_key(raw: str) -> bytes:
    key = _b64d(raw.strip())
    if len(key) != KEY_LEN:
        raise SecretDecryptError(
            f"ATLAS_MASTER_KEY 解码后须为 {KEY_LEN} 字节，实际 {len(key)} 字节"
        )
    return key


def parse_envelope(envelope: str) -> tuple[str, str | None, bytes, bytes | None]:
    """解析紧凑信封 → (kid, iv_or_None, payload, aad_or_None 由调用方按 kid 处理)。

    返回 ``(kid, iv, ciphertext_or_plaintext)``；plain 信封 iv 为 None。
    """
    parts = envelope.split("$")
    if len(parts) < 4 or parts[0] != ENVELOPE_PREFIX.rstrip("$") or parts[1] != ENVELOPE_VERSION:
        raise SecretDecryptError("非法信封前缀或版本")
    kid = parts[2]
    if kid == PLAIN_KID:
        if len(parts) != 4:
            raise SecretDecryptError("plain 信封字段数非法")
        return kid, None, _b64d(parts[3])
    if len(parts) != 5:
        raise SecretDecryptError("AES-GCM 信封字段数非法")
    iv = _b64d(parts[3])
    ciphertext = _b64d(parts[4])
    return kid, iv, ciphertext


class SecretProvider(Protocol):
    def get_secret(self, name: str) -> str: ...

    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, envelope: str) -> str: ...


class PlaintextSecretProvider:
    """显式非机密 provider：仅 dev/demo；信封为 plain，AES 信封无法解密。"""

    alg = "plain"

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets: dict[str, str] = dict(secrets or {})

    def get_secret(self, name: str) -> str:
        if name not in self._secrets:
            raise SecretUnavailable(f"运行时秘密未注入：{name}")
        raw = self._secrets[name]
        return self.decrypt(raw) if isinstance(raw, str) and raw.startswith(ENVELOPE_PREFIX) else raw

    def encrypt(self, plaintext: str) -> str:
        return f"{ENVELOPE_PREFIX}{ENVELOPE_VERSION}${PLAIN_KID}${_b64e(plaintext.encode('utf-8'))}"

    def decrypt(self, envelope: str) -> str:
        kid, _iv, payload = parse_envelope(envelope)
        if kid != PLAIN_KID:
            raise SecretDecryptError("明文 provider 无法解密 AES-GCM 信封（未配置主密钥）")
        return payload.decode("utf-8")


class AesGcmSecretProvider:
    """AES-256-GCM 认证加密 provider（ADR T26=(a)，依赖 cryptography）。"""

    alg = "AES-GCM"

    def __init__(self, key: bytes, kid: str = DEFAULT_KEY_ID, secrets: dict[str, str] | None = None) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover - 装配层已预检
            raise SecretDecryptError("cryptography 后端不可用") from exc
        if len(key) != KEY_LEN:
            raise SecretDecryptError(f"AES-GCM 密钥须为 {KEY_LEN} 字节")
        self._aes = AESGCM(key)
        self.kid = kid
        self._secrets = dict(secrets or {})

    def get_secret(self, name: str) -> str:
        if name not in self._secrets:
            raise SecretUnavailable(f"运行时秘密未注入：{name}")
        raw = self._secrets[name]
        return self.decrypt(raw) if isinstance(raw, str) and raw.startswith(ENVELOPE_PREFIX) else raw

    def encrypt(self, plaintext: str) -> str:
        iv = os.urandom(NONCE_LEN)
        ct = self._aes.encrypt(iv, plaintext.encode("utf-8"), self.kid.encode("utf-8"))
        return f"{ENVELOPE_PREFIX}{ENVELOPE_VERSION}${self.kid}${_b64e(iv)}${_b64e(ct)}"

    def decrypt(self, envelope: str) -> str:
        kid, iv, ciphertext = parse_envelope(envelope)
        if kid == PLAIN_KID:
            raise SecretDecryptError("生产 provider 拒绝解密 plain 信封（请改用 AES-GCM 加密）")
        if iv is None:
            raise SecretDecryptError("AES-GCM 信封缺少 iv")
        if kid != self.kid:
            # 密钥标识必须匹配当前主密钥版本（轮换后旧信封应由保留旧密钥的 provider 解）
            raise SecretDecryptError(f"信封 kid={kid} 与当前主密钥 kid={self.kid} 不符")
        try:
            plaintext = self._aes.decrypt(iv, ciphertext, self.kid.encode("utf-8"))
        except Exception as exc:  # InvalidTag 等：错 key / 篡改 / AAD 不符
            raise SecretDecryptError(f"AES-GCM 解密失败（密钥错误或信封被篡改）：{type(exc).__name__}") from exc
        return plaintext.decode("utf-8")


def build_secret_provider_from_env() -> SecretProvider:
    """按环境变量装配 provider；配了主密钥但后端不可用则 fail-closed。"""
    raw_secrets = os.getenv("ATLAS_SECRETS", "").strip()
    secrets: dict[str, str] = {}
    if raw_secrets:
        try:
            parsed = json.loads(raw_secrets)
        except json.JSONDecodeError as exc:
            raise ValueError("ATLAS_SECRETS 不是合法 JSON 对象") from exc
        if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
        ):
            raise ValueError("ATLAS_SECRETS 必须是 {name: str} 的 JSON 对象")
        secrets = {str(k): str(v) for k, v in parsed.items()}

    master = os.getenv("ATLAS_MASTER_KEY", "").strip()
    if not master:
        logger.warning(
            "ATLAS_MASTER_KEY 未配置：使用明文 secret provider，秘密不受加密保护，"
            "仅限 dev/demo；生产必须配置 32 字节主密钥。"
        )
        return PlaintextSecretProvider(secrets)

    kid = os.getenv("ATLAS_MASTER_KEY_ID", DEFAULT_KEY_ID).strip() or DEFAULT_KEY_ID
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401

        key = _decode_master_key(master)
    except Exception as exc:
        # 配了主密钥却无法提供加密后端 → fail-closed，禁止静默退回明文
        raise RuntimeError(
            "配置了 ATLAS_MASTER_KEY 但 AES-GCM 后端不可用或密钥非法，启动中止"
        ) from exc
    return AesGcmSecretProvider(key, kid=kid, secrets=secrets)


def resolve_references(text: object, provider: SecretProvider | None) -> object:
    """把字符串中的 ``secret://name`` 与 ``enc$...`` 解析为明文；非字符串原样返回。

    provider 为 None 却出现引用/信封 → SecretUnavailable（fail-closed，不把
    占位符当字面量发出）。
    """
    if not isinstance(text, str):
        return text

    def _enc(match: re.Match[str]) -> str:
        if provider is None:
            raise SecretUnavailable("检测到 enc$ 信封但未配置 secret provider")
        return provider.decrypt(match.group(0))

    def _ref(match: re.Match[str]) -> str:
        if provider is None:
            raise SecretUnavailable(f"检测到 secret://{match.group(1)} 但未配置 secret provider")
        return provider.get_secret(match.group(1))

    resolved = _ENVELOPE_RE.sub(_enc, text)
    resolved = _SECRET_REF_RE.sub(_ref, resolved)
    return resolved


def redact_headers(headers: dict[str, object] | None) -> dict[str, object]:
    """复制一份头，敏感头值渲染为 ***（用于任何对外观测/记录通道）。"""
    if not headers:
        return {}
    redacted: dict[str, object] = {}
    for key, value in headers.items():
        if str(key).lower() in SENSITIVE_HEADERS:
            redacted[key] = REDACTED
        elif isinstance(value, str) and (value.startswith(ENVELOPE_PREFIX) or value.startswith("secret://")):
            redacted[key] = REDACTED
        else:
            redacted[key] = value
    return redacted
