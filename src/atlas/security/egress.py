"""HTTP 出向目标 SSRF 校验纯函数与策略（docs/32 §3，P0 批 3）。

运营体 HTTP 节点出向调用真实外部系统前的第一道闸：

- 仅允许 http/https scheme（拒 file/gopher/dict/ftp/ldap 等）；
- 对环回 / 私网 / 链路本地 / 云元数据（169.254.169.254）/ CGNAT /
  组播 / 保留 / 未指定地址恒拦，IPv4-mapped IPv6 按内嵌 v4 判定；
- 拒绝整数 / 八进制 / 十六进制 / 混写 / 尾点 IP / URL 内嵌 userinfo 等
  常见 SSRF 绕过（凡不是「规范 IP 字面量」或「严格域名」的 host 一律拒）；
- 配置出向白名单后仅命中项可出网（fail-closed），私网/元数据在白名单
  判定之前恒拦（白名单不能放行元数据）；未配白名单则只跑 denylist，
  公网放行（保 demo/现状）；
- DNS 解析器可注入，离线单测不依赖真实 DNS；不读 X-Forwarded-For 等
  任何客户端传入头做网络判定。

``permit_cidrs`` 是**仅供进程内集成测试**的代码注入缝（默认空、绝不从
环境变量/用户配置读取）：用于让「HTTP 节点回调本机 uvicorn mock 端点」
这类回环 e2e 测试显式 opt-in 127.0.0.0/8；生产装配路径不传入，环回/私网/
元数据恒拦。env 出向白名单（``ATLAS_HTTP_EGRESS_ALLOWLIST``）任何情况下
都不能放行私网/元数据。

纯逻辑层已知残留（docs/32 §9 风险 1）：「解析即校验」与真正建连之间存在
DNS rebinding/TOCTOU 窗口，彻底修复需在建连层 pin 校验通过的 IP（自定义
transport）与出站防火墙，随部署批做；本模块不假装已解决。
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, Union

from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")

# 显式恒拦 IPv4 网段（不依赖不同 Python 版本的 is_private 口径）
_DENY_V4_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",  # 本网络（含 0.0.0.0）
        "10.0.0.0/8",  # 私网
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",  # 环回
        "169.254.0.0/16",  # 链路本地（含云元数据 169.254.169.254）
        "172.16.0.0/12",  # 私网
        "192.0.0.0/24",  # IETF 协议分配
        "192.0.2.0/24",  # TEST-NET-1
        "192.168.0.0/16",  # 私网
        "198.18.0.0/15",  # 网络基准测试
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",  # TEST-NET-3
    )
]
_DENY_V4_MULTICAST = ipaddress.ip_network("224.0.0.0/4")
_DENY_V4_RESERVED = ipaddress.ip_network("240.0.0.0/4")

_DENY_V6_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "::/128",  # 未指定
        "::1/128",  # 环回
        "fc00::/7",  # 唯一本地 ULA
        "fe80::/10",  # 链路本地
        "ff00::/8",  # 组播
        "2001:db8::/32",  # 文档示例
        "::ffff:0:0/96",  # IPv4-mapped（按内嵌 v4 判定，先占位排除）
    )
]

# 严格域名：点分标签，标签为字母/数字/连字符且首尾为字母数字；总长 ≤253
_DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_DOMAIN_LABEL}(?:\.{_DOMAIN_LABEL})*$", re.IGNORECASE)

Resolver = Callable[[str], list[str]]
_IP = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
_Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


class EgressDenied(Exception):
    """出向目标未通过 SSRF 校验；code 取 EGRESS_DENIED / EGRESS_INVALID_URL。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolvedTarget:
    scheme: str
    host: str  # 归一化主机（域名小写 / IP 规范形式，不含端口与尾点）
    port: int
    matched_ip: str  # 分类所依据的 IP（字面量或解析到的公网代表地址）
    is_literal: bool


def default_resolver(host: str) -> list[str]:
    """用系统 DNS 解析主机名，返回去重后的全部 A/AAAA 地址。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise EgressDenied("EGRESS_INVALID_URL", f"无法解析主机名：{host}") from exc
    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise EgressDenied("EGRESS_INVALID_URL", f"主机名无解析记录：{host}")
    return ips


def _parse_ip(host: str) -> _IP | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _parse_networks(entries: Iterable[str]) -> tuple[_Network, ...]:
    nets: list[_Network] = []
    for item in entries:
        try:
            nets.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _v4_denied(ip: ipaddress.IPv4Address) -> str | None:
    """返回 IPv4 拦截原因；None 表示公网可放行。"""
    for net in _DENY_V4_NETS:
        if ip in net:
            return f"{ip} 命中受限/保留网段 {net}"
    if ip in _DENY_V4_MULTICAST:
        return f"{ip} 为组播地址"
    if ip in _DENY_V4_RESERVED:
        return f"{ip} 为保留地址"
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return f"{ip} 为环回/私网/链路本地/特殊用途地址"
    return None


def _ip_denied(ip: _IP, permit_nets: tuple[_Network, ...] = ()) -> str | None:
    """返回拦截原因；None 表示可放行。命中进程内测试 permit_cidrs 视为放行。"""
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped  # ::ffff:a.b.c.d → 取内嵌 v4 同口径判定
        if mapped is not None:
            denied = _v4_denied(mapped)
        else:
            denied = None
            for net in _DENY_V6_NETS:
                if ip in net:
                    denied = f"{ip} 命中 IPv6 受限/保留网段 {net}"
                    break
            if denied is None and (
                ip.is_loopback
                or ip.is_private
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                denied = f"{ip} 为 IPv6 环回/私网/链路本地/特殊用途地址"
    else:
        denied = _v4_denied(ip)
    if denied is not None and any(ip in net for net in permit_nets):
        return None
    return denied


def _is_strict_domain(host: str) -> bool:
    """严格 ASCII 全限定域名。

    要求至少两个点分标签（出向目标必须是 FQDN；单标签名如 ``localhost`` 与
    无点的整数/十六进制 IP 编码一律不走域名分支），顶级标签必须含字母
    （排除 ``127.1`` / ``0177.0.0.1`` 等纯数字紧凑 IP 写法）。
    """
    if "." not in host or len(host) > 253 or not _DOMAIN_RE.fullmatch(host):
        return False
    tld = host.rsplit(".", 1)[-1]
    return any(ch.isalpha() for ch in tld)


@dataclass(frozen=True)
class _ExactHost:
    host: str  # 小写、无尾点


@dataclass(frozen=True)
class _Suffix:
    suffix: str  # *.example.com → example.com（含裸域与其子域）


@dataclass(frozen=True)
class _Cidr:
    network: _Network


def parse_allowlist(entries: Iterable[str]) -> tuple[object, ...]:
    """解析 env 出向白名单：精确 host、``*.suffix`` 后缀、CIDR。

    注意：env 白名单仅在通过 denylist（私网/元数据恒拦）之后才判定，
    因此即使列出私网/元数据 CIDR 也无法放行。
    """
    rules: list[object] = []
    for raw in entries:
        item = raw.strip().lower()
        if not item:
            continue
        if item.startswith("*."):
            rules.append(_Suffix(suffix=item[2:].rstrip(".")))
            continue
        if "/" in item:
            net = _parse_networks([item])
            if net:
                rules.append(_Cidr(network=net[0]))
            continue
        host = item.rstrip(".")
        if _is_strict_domain(host) or _parse_ip(host) is not None:
            rules.append(_ExactHost(host=host))
        # 非法条目静默跳过（fail-closed：不产生放行能力）
    return tuple(rules)


def _allowlisted(host: str, literal_ip: _IP | None, resolved: list[_IP], rules: tuple[object, ...]) -> bool:
    for rule in rules:
        if isinstance(rule, _ExactHost):
            if host == rule.host or (literal_ip is not None and str(literal_ip) == rule.host):
                return True
        elif isinstance(rule, _Suffix):
            if host == rule.suffix or host.endswith("." + rule.suffix):
                return True
        elif isinstance(rule, _Cidr):
            candidates = [literal_ip] if literal_ip is not None else []
            candidates.extend(resolved)
            if any(ip is not None and ip in rule.network for ip in candidates):
                return True
    return False


def validate_egress_url(
    url: str,
    *,
    resolver: Resolver = default_resolver,
    allowlist: Iterable[str] = (),
    permit_cidrs: Iterable[str] = (),
) -> ResolvedTarget:
    """校验最终出向 URL；不通过抛 :class:`EgressDenied`，通过返回目标信息。

    ``permit_cidrs`` 见模块说明：仅进程内回环集成测试代码注入，默认空。
    """
    permit_nets = _parse_networks(permit_cidrs)
    if not isinstance(url, str) or not url.strip():
        raise EgressDenied("EGRESS_INVALID_URL", "空 URL")
    try:
        parts = urlsplit(url.strip())
        scheme = (parts.scheme or "").lower()
        if scheme not in ALLOWED_SCHEMES:
            raise EgressDenied("EGRESS_INVALID_URL", f"仅允许 http/https，收到：{scheme or '缺失'}")
        # 出向 URL 不允许内嵌 userinfo（@ 绕过 / 凭据泄露面）
        if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
            raise EgressDenied("EGRESS_DENIED", "出向 URL 不允许内嵌用户名/密码（userinfo）")
        raw_host = parts.hostname
        if not raw_host:
            raise EgressDenied("EGRESS_INVALID_URL", "URL 缺少主机名")
        try:
            port = parts.port
        except ValueError as exc:
            raise EgressDenied("EGRESS_INVALID_URL", f"非法端口：{exc}") from exc
        if port is None:
            port = 443 if scheme == "https" else 80
    except EgressDenied:
        raise
    except ValueError as exc:
        raise EgressDenied("EGRESS_INVALID_URL", f"URL 无法解析：{exc}") from exc

    host = raw_host.rstrip(".").lower()
    rules = parse_allowlist(allowlist)

    literal_ip = _parse_ip(host)
    if literal_ip is not None:
        reason = _ip_denied(literal_ip, permit_nets)
        if reason:
            raise EgressDenied("EGRESS_DENIED", reason)
        if rules and not _allowlisted(host, literal_ip, [], rules):
            raise EgressDenied("EGRESS_DENIED", f"{host} 不在出向白名单内")
        return ResolvedTarget(scheme, str(literal_ip), port, str(literal_ip), True)

    if _is_strict_domain(host):
        resolved: list[_IP] = []
        for addr in resolver(host):
            parsed = _parse_ip(addr)
            if parsed is None:
                continue
            resolved.append(parsed)
            reason = _ip_denied(parsed, permit_nets)
            if reason:
                raise EgressDenied("EGRESS_DENIED", f"{host} 解析到受限地址 {parsed}：{reason}")
        public = [ip for ip in resolved if _ip_denied(ip, permit_nets) is None]
        if not public:
            raise EgressDenied("EGRESS_DENIED", f"{host} 无可放行的解析地址")
        if rules and not _allowlisted(host, None, resolved, rules):
            raise EgressDenied("EGRESS_DENIED", f"{host} 不在出向白名单内")
        return ResolvedTarget(scheme, host, port, str(public[0]), False)

    # 既非规范 IP 又非严格域名 → 整数/八进制/十六进制/混写等绕过，fail-closed
    raise EgressDenied("EGRESS_DENIED", f"非法或可疑的主机字面量：{raw_host}")


class EgressGuard:
    """按配置装配的出向校验器（denylist 恒启用，allowlist 可选 fail-closed）。"""

    def __init__(
        self,
        allowlist: Iterable[str] = (),
        resolver: Resolver = default_resolver,
        permit_cidrs: Iterable[str] = (),
    ) -> None:
        self._entries = tuple(item.strip() for item in allowlist if item.strip())
        self._resolver = resolver
        # 仅进程内回环集成测试注入；无 env 通道
        self._permit = tuple(permit_cidrs)
        self.mode = "allowlist" if self._entries else "denylist-only"

    @classmethod
    def from_env(cls, resolver: Resolver = default_resolver) -> "EgressGuard":
        raw = os.getenv("ATLAS_HTTP_EGRESS_ALLOWLIST", "")
        entries = [item.strip() for item in raw.split(",") if item.strip()]
        return cls(entries, resolver=resolver)

    def check(self, url: str) -> ResolvedTarget:
        return validate_egress_url(
            url,
            resolver=self._resolver,
            allowlist=self._entries,
            permit_cidrs=self._permit,
        )
