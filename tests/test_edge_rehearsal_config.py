# -*- coding: utf-8 -*-
"""打包 BB：edge 演练用的 Caddyfile 必须与出厂样例**结构相等**（U1159）。

为什么这条是承重件而不是锦上添花：`edge_rehearsal.py` 用的是 `deploy/Caddyfile.rehearsal`
（自签＋占位域名），不是出厂那份 `deploy/Caddyfile`。若两份配置走形，"演练跑过 edge"
就可能跑的是一份与生产无关的配置——那比没跑更坏：它会给出一个没有对应事实的 ✅。
本文件把"两份同形"变成机检，纯文本、零 docker、零网络。

五例：正向三条（gzip／metrics 网段白名单／反代目标恰为 `atlas:8000`）＋ 两个方向的反向门
（从合成文本里删掉 metrics 白名单、改掉反代目标，守护必须点名，并先断言替换锚命中）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROD_CADDYFILE = ROOT / "deploy" / "Caddyfile"
REHEARSAL_CADDYFILE = ROOT / "deploy" / "Caddyfile.rehearsal"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


METRICS_WHITELIST = "not remote_ip 10.0.0.0/8 127.0.0.1/32"
PROXY_TARGET = "reverse_proxy atlas:8000"

#: 守护的三条结构要素；`_missing_tokens` 是**唯一**判据入口，正向用例与两条反向门共用它——
#: 反向门各自在测试里重写一份判据的话，它能证明的只是"那段复制来的代码"会红。
REQUIRED: dict[str, str] = {
    "gzip": "encode gzip",
    "metrics 网段白名单": METRICS_WHITELIST,
    "反代目标 atlas:8000": PROXY_TARGET,
}


def _missing_tokens(text: str) -> list[str]:
    return [name for name, token in REQUIRED.items() if token not in text]


def _site_block(text: str) -> str:
    """取站点块（第一个 `{` 全局块之后的内容），避免把全局 `admin off` 也算进来。"""
    # 全局块是文件里第一个顶层 `{ ... }`；站点块是它之后的全部内容。
    end = text.find("}")
    assert end != -1, "Caddyfile 里没找到全局块"
    return text[end + 1 :]


@pytest.fixture
def prod() -> str:
    return _site_block(_text(PROD_CADDYFILE))


@pytest.fixture
def rehearsal() -> str:
    return _site_block(_text(REHEARSAL_CADDYFILE))


def test_u1159_rehearsal_keeps_gzip_and_metrics_whitelist_and_proxy_target(
    prod: str, rehearsal: str
) -> None:
    """U1159-①：三条结构要素在演练配置里一条不少，且反代目标与出厂逐字一致。"""
    assert _missing_tokens(rehearsal) == [], f"演练配置缺结构要素：{_missing_tokens(rehearsal)}"
    # 与出厂样例对齐：三要素在出厂那份里也必须在（否则这条守护自己就是盲的）
    assert _missing_tokens(prod) == [], f"出厂样例缺结构要素：{_missing_tokens(prod)}"


def test_u1159_rehearsal_differs_from_prod_only_by_host_and_tls(
    prod: str, rehearsal: str
) -> None:
    """U1159-②：两份配置的差别**只**在站点地址与证书来源，别的都要一样。"""
    prod_body = re.sub(r"^[^\{\n]+\{", "", prod.strip(), count=1)
    reh_body = re.sub(r"^[^\{\n]+\{", "", rehearsal.strip(), count=1)
    # 站点块内逐行比较，忽略自签那一行与注释
    def lines_of(block: str) -> list[str]:
        return [
            l.strip()
            for l in block.splitlines()
            if l.strip() and not l.strip().startswith("#") and l.strip() != "tls internal"
        ]

    assert lines_of(prod_body) == lines_of(reh_body), (
        "演练配置与出厂配置在站点块内出现了除自签之外的差异——演练跑的就不是生产的形状了"
    )


def test_u1159_guard_names_missing_metrics_whitelist() -> None:
    """U1159-③ 反向门：从合成文本里抽掉 metrics 白名单那一行，守护必须点名。"""
    text = _text(REHEARSAL_CADDYFILE)
    anchor = METRICS_WHITELIST
    assert anchor in text, "替换锚没命中，反向门会变成空跑的绿"
    doctored = text.replace(anchor, "\t\tnot remote_ip 10.0.0.0/8")
    assert doctored != text
    missing = _missing_tokens(doctored)
    assert "metrics 网段白名单" in missing, f"守护没有点名被抽掉的白名单：{missing}"


def test_u1159_guard_names_wrong_proxy_target() -> None:
    """U1159-④ 反向门：把反代目标改掉，守护必须点名（并先断言替换锚命中）。"""
    text = _text(REHEARSAL_CADDYFILE)
    anchor = "reverse_proxy atlas:8000"
    assert anchor in text, "替换锚没命中，反向门会变成空跑的绿"
    doctored = text.replace(anchor, "reverse_proxy atlas:9000")
    assert doctored != text
    missing = _missing_tokens(doctored)
    assert "反代目标 atlas:8000" in missing, f"守护没有点名被改掉的反代目标：{missing}"


def test_u1159_guard_rejects_self_signed_in_prod_sample() -> None:
    """U1159-⑤：出厂样例**不许**带 `tls internal`——抄到自签配置的人永远拿不到真证书。"""
    assert "tls internal" not in prod_text(), (
        "deploy/Caddyfile 里出现了 `tls internal`：真部署方照抄会拿到一份自签配置"
    )


def prod_text() -> str:
    return _text(PROD_CADDYFILE)
