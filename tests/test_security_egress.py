"""HTTP 出向 SSRF 校验纯函数测试（docs/32 §3，13 文档 U235）。

DNS 解析全部经注入的假 resolver，零真实网络出口。
"""

from __future__ import annotations

import pytest

from atlas.security.egress import (
    EgressDenied,
    EgressGuard,
    validate_egress_url,
)

PUBLIC_IP = "93.184.216.34"  # example.com 公网地址，仅作假 resolver 返回值，不真实连接


def public_resolver(_host: str) -> list[str]:
    return [PUBLIC_IP]


def resolver_for(mapping: dict[str, list[str]]):
    def _resolve(host: str) -> list[str]:
        return mapping[host]

    return _resolve


def deny_code(url, **kwargs):
    kwargs.setdefault("resolver", public_resolver)
    with pytest.raises(EgressDenied) as exc_info:
        validate_egress_url(url, **kwargs)
    return exc_info.value.code


# --- 公网放行（denylist-only） ---

def test_public_domain_allowed():
    target = validate_egress_url("https://example.com/orders", resolver=public_resolver)
    assert target.host == "example.com"
    assert target.matched_ip == PUBLIC_IP
    assert target.is_literal is False
    assert target.port == 443


def test_public_ipv4_literal_allowed():
    target = validate_egress_url("http://93.184.216.34:8080/x", resolver=public_resolver)
    assert target.is_literal is True
    assert target.port == 8080


# --- 恒拦 IPv4 区间（含云元数据） ---

@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.1.2.3",
        "0.0.0.0",
        "10.0.0.5",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.0.1",
        "169.254.169.254",  # 云元数据端点
        "100.64.0.1",  # CGNAT
        "224.0.0.1",  # 组播
        "255.255.255.255",  # 保留/广播
    ],
)
def test_blocked_private_and_metadata_ipv4(ip):
    assert deny_code(f"http://{ip}/latest/meta-data") == "EGRESS_DENIED"


def test_private_ipv4_boundary_172_15_and_32_public():
    # 172.15/172.32 不在 172.16/12，属公网（放行代表，验证网段边界）
    validate_egress_url("http://172.15.0.1/", resolver=public_resolver)
    validate_egress_url("http://172.32.0.1/", resolver=public_resolver)


# --- IPv6 拦截（含 IPv4-mapped） ---

@pytest.mark.parametrize(
    "ip",
    [
        "[::1]",
        "[fc00::1]",
        "[fd00::1]",
        "[fe80::1]",
        "[::ffff:127.0.0.1]",
        "[::ffff:169.254.169.254]",
        "[::]",
    ],
)
def test_blocked_ipv6(ip):
    assert deny_code(f"http://{ip}/") == "EGRESS_DENIED"


def test_public_ipv6_allowed():
    target = validate_egress_url("http://[2606:2800:220:1:248:1893:25c8:1946]/", resolver=public_resolver)
    assert target.is_literal is True


# --- 绕过写法全部 fail-closed ---

@pytest.mark.parametrize(
    "host",
    [
        "2130706433",  # 十进制整数 = 127.0.0.1
        "0x7f000001",  # 十六进制整数
        "0x7f.1",
        "0177.0.0.1",  # 八进制
        "017700000001",
        "127.0.0.1.",  # 尾点 IP
        "127.1",  # 紧凑省略形式
        "0",
    ],
)
def test_obfuscated_ip_variants_blocked(host):
    assert deny_code(f"http://{host}/") == "EGRESS_DENIED"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com@127.0.0.1/",
        "http://expected.com%2f@127.0.0.1/",
        "http://user:pass@169.254.169.254/",
    ],
)
def test_userinfo_embedded_address_blocked(url):
    assert deny_code(url) == "EGRESS_DENIED"


# --- scheme / 形态校验 ---

@pytest.mark.parametrize(
    "url,code",
    [
        ("file:///etc/passwd", "EGRESS_INVALID_URL"),
        ("ftp://example.com/x", "EGRESS_INVALID_URL"),
        ("gopher://127.0.0.1/", "EGRESS_INVALID_URL"),
        ("dict://127.0.0.1:2628/", "EGRESS_INVALID_URL"),
        ("ldap://127.0.0.1/", "EGRESS_INVALID_URL"),
        ("", "EGRESS_INVALID_URL"),
        ("   ", "EGRESS_INVALID_URL"),
        ("127.0.0.1:8080/x", "EGRESS_INVALID_URL"),  # 缺 scheme
    ],
)
def test_invalid_scheme_or_shape(url, code):
    assert deny_code(url) == code


def test_invalid_port_rejected():
    assert deny_code("http://example.com:99999/") == "EGRESS_INVALID_URL"


# --- 域名解析结果分类 ---

def test_domain_resolving_to_private_blocked():
    resolver = resolver_for({"evil.example": ["10.0.0.9"]})
    assert deny_code("http://evil.example/x", resolver=resolver) == "EGRESS_DENIED"


def test_domain_resolving_to_metadata_blocked():
    resolver = resolver_for({"rebind.example": ["169.254.169.254"]})
    assert deny_code("http://rebind.example/x", resolver=resolver) == "EGRESS_DENIED"


def test_domain_with_mixed_public_and_private_records_blocked():
    resolver = resolver_for({"split.example": [PUBLIC_IP, "192.168.0.1"]})
    assert deny_code("http://split.example/x", resolver=resolver) == "EGRESS_DENIED"


def test_unresolvable_domain_is_invalid():
    def no_records(_host):
        return []

    with pytest.raises(EgressDenied) as exc_info:
        validate_egress_url("http://nx.example/", resolver=no_records)
    assert exc_info.value.code == "EGRESS_DENIED"


# --- 白名单（fail-closed，私网恒拦优先于白名单） ---

def test_allowlist_exact_host_match_allows():
    target = validate_egress_url(
        "https://api.example.com/v1",
        resolver=resolver_for({"api.example.com": [PUBLIC_IP]}),
        allowlist=["api.example.com"],
    )
    assert target.host == "api.example.com"


def test_allowlist_unmatched_host_denied():
    resolver = resolver_for({"other.example": [PUBLIC_IP]})
    assert deny_code("http://other.example/", resolver=resolver, allowlist=["api.example.com"]) == "EGRESS_DENIED"


def test_allowlist_suffix_matches_subdomain_and_apex():
    resolver = resolver_for(
        {
            "a.example.com": [PUBLIC_IP],
            "example.com": [PUBLIC_IP],
            "evil.com": [PUBLIC_IP],
        }
    )
    validate_egress_url("http://a.example.com/", resolver=resolver, allowlist=["*.example.com"])
    validate_egress_url("http://example.com/", resolver=resolver, allowlist=["*.example.com"])
    assert deny_code("http://evil.com/", resolver=resolver, allowlist=["*.example.com"]) == "EGRESS_DENIED"


def test_allowlist_cidr_matches_resolved_ip():
    in_cidr = resolver_for({"in.example": ["93.184.216.10"]})
    out_cidr = resolver_for({"out.example": ["8.8.8.8"]})
    validate_egress_url("http://in.example/", resolver=in_cidr, allowlist=["93.184.216.0/24"])
    assert deny_code("http://out.example/", resolver=out_cidr, allowlist=["93.184.216.0/24"]) == "EGRESS_DENIED"


def test_allowlist_cannot_permit_metadata():
    # 即使白名单精确列出元数据地址，denylist 仍恒拦
    assert deny_code("http://169.254.169.254/", allowlist=["169.254.169.254"]) == "EGRESS_DENIED"


def test_allowlist_cidr_cannot_permit_private():
    resolver = resolver_for({"inside.example": ["10.0.0.5"]})
    assert deny_code("http://inside.example/", resolver=resolver, allowlist=["10.0.0.0/8"]) == "EGRESS_DENIED"


def test_allowlist_blocks_public_literal_not_listed():
    assert deny_code("http://93.184.216.34/", allowlist=["api.example.com"]) == "EGRESS_DENIED"


# --- EgressGuard 装配 ---

def test_guard_mode_switch(monkeypatch):
    assert EgressGuard().mode == "denylist-only"
    guarded = EgressGuard(allowlist=["api.example.com"], resolver=public_resolver)
    assert guarded.mode == "allowlist"
    guarded.check("https://api.example.com/x")


def test_guard_from_env(monkeypatch):
    monkeypatch.setenv("ATLAS_HTTP_EGRESS_ALLOWLIST", "api.example.com, *.cdn.example")
    guard = EgressGuard.from_env(resolver=public_resolver)
    assert guard.mode == "allowlist"


# --- permit_cidrs：仅进程内回环集成测试的代码注入缝 ---

def test_permit_cidrs_opts_in_loopback_for_in_process_test():
    # 默认环回恒拦
    assert deny_code("http://127.0.0.1:8000/x") == "EGRESS_DENIED"
    # 显式注入 permit_cidrs 后该回环段放行（不读 env，仅代码注入）
    target = validate_egress_url("http://127.0.0.1:8000/x", permit_cidrs=["127.0.0.0/8"])
    assert target.matched_ip == "127.0.0.1"


def test_permit_loopback_does_not_permit_metadata():
    # 只 opt-in 环回段时，云元数据/其他私网仍恒拦
    assert deny_code("http://169.254.169.254/", permit_cidrs=["127.0.0.0/8"]) == "EGRESS_DENIED"
    assert deny_code("http://10.0.0.5/", permit_cidrs=["127.0.0.0/8"]) == "EGRESS_DENIED"


def test_env_allowlist_carries_no_permit_power():
    # from_env 不暴露 permit_cidrs，env 白名单无法放行环回
    guard = EgressGuard(allowlist=["127.0.0.1"], resolver=public_resolver)
    with pytest.raises(EgressDenied) as exc_info:
        guard.check("http://127.0.0.1/")
    assert exc_info.value.code == "EGRESS_DENIED"
