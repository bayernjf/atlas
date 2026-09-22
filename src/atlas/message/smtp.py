"""SMTP 邮件真实投递（docs/34 §五 P0-2；D24 的 email 子集）。

message/send 在 channel=email 且配置了 ATLAS_SMTP_HOST 时经此模块真实发信；
未配置 HOST 时 from_env 返回 None，MessageService 回退进程内记录（demo 行为不变）。

仅依赖标准库 smtplib/email，零新依赖。SMTP 口令从环境变量读取（与
LITELLM_API_KEY 同一信任边界）；生产环境应改用 secret provider 信封注入
（docs/32 secrets），多实例/重试队列缓做 docs/14 D24。
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable


class SmtpError(RuntimeError):
    """SMTP 投递失败（连接/认证/被拒），由上层折算为 SMTP_SEND_FAILED。"""


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    from_addr: str
    use_tls: bool = True  # STARTTLS；置 False 用于本地 25 端口明文中继

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "SmtpConfig | None":
        """从 ATLAS_SMTP_* 环境变量构造；未配置 HOST 返回 None（回退进程内记录）。"""
        env = environ if environ is not None else dict(os.environ)
        host = env.get("ATLAS_SMTP_HOST", "").strip()
        if not host:
            return None
        from_addr = env.get("ATLAS_SMTP_FROM", "").strip()
        if not from_addr:
            # 未显式给 From 时用 username@host 兜底，仍缺则抛错（fail-closed）
            username = env.get("ATLAS_SMTP_USERNAME", "").strip()
            if username and "@" in username:
                from_addr = username
            else:
                raise SmtpError("ATLAS_SMTP_FROM 未配置且无法从用户名推导发件地址")
        port_raw = env.get("ATLAS_SMTP_PORT", "587").strip() or "587"
        try:
            port = int(port_raw)
        except ValueError:
            raise SmtpError(f"ATLAS_SMTP_PORT 非法：{port_raw!r}") from None
        username = env.get("ATLAS_SMTP_USERNAME", "").strip() or None
        password = env.get("ATLAS_SMTP_PASSWORD", "") or None
        use_tls = env.get("ATLAS_SMTP_USE_TLS", "true").strip().lower() not in ("0", "false", "no")
        return cls(
            host=host, port=port, username=username, password=password,
            from_addr=from_addr, use_tls=use_tls,
        )


# SMTP 连接工厂：默认走 smtplib，测试注入 fake 以避免真实网络。
SmtpFactory = Callable[[SmtpConfig], "smtplib.SMTP"]


def _default_factory(config: SmtpConfig) -> "smtplib.SMTP":
    client = smtplib.SMTP(config.host, config.port, timeout=15)
    try:
        if config.use_tls:
            context = ssl.create_default_context()
            client.starttls(context=context)
        if config.username and config.password is not None:
            client.login(config.username, config.password)
    except Exception:
        client.close()
        raise
    return client


class SmtpSender:
    """同步 SMTP 发信器（进程内单例，由 registry 装配注入 MessageService）。"""

    def __init__(self, config: SmtpConfig, smtp_factory: SmtpFactory | None = None) -> None:
        self._config = config
        self._factory = smtp_factory or _default_factory

    def send(self, to: list[str], subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._config.from_addr
        message["To"] = ", ".join(to)
        message["Subject"] = subject
        message.set_content(body)

        client = None
        try:
            client = self._factory(self._config)
            client.send_message(message)
        except SmtpError:
            raise
        except Exception as exc:
            raise SmtpError(f"SMTP 投递失败：{type(exc).__name__}: {exc}") from exc
        finally:
            if client is not None:
                try:
                    client.quit()
                except Exception:
                    client.close()


_sender: SmtpSender | None = None


def get_smtp_sender() -> SmtpSender | None:
    """进程级惰性单例：ATLAS_SMTP_HOST 未配置时返回 None。"""
    global _sender
    if _sender is None:
        config = SmtpConfig.from_env()
        if config is not None:
            _sender = SmtpSender(config)
    return _sender
