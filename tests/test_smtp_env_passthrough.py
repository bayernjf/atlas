# -*- coding: utf-8 -*-
"""打包 AZ（docs/73 1.3 的部署面）：代码读的 SMTP 变量必须真的进得了容器。

发现的形状很平常、后果很具体：`SmtpConfig.from_env()` 读 `ATLAS_SMTP_*`，
而 `docker-compose.yml` 的 atlas 服务以前只透传 `LITELLM_*`／`OPENAI_*`。于是宿主
`.env` 里配好 SMTP，出厂容器读到的仍是空 HOST ⇒ 发信静默回退成进程内记录，
1.3 的判据卡在部署层，而不是卡在"还没找供应商"。

本文件把这件事钉成机检：**代码新增一个 ATLAS_SMTP_* 而 compose 没跟着透传 ⇒ 红**。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SMTP_SOURCE = REPO / "src" / "atlas" / "message" / "smtp.py"
COMPOSE = REPO / "docker-compose.yml"

_READ_RE = re.compile(r'env\.get\("(ATLAS_SMTP_[A-Z_]+)"')
_ATLAS_BODY_RE = re.compile(r"\n  atlas:\n(?P<body>.*?)(?=\n  [A-Za-z0-9_-]+:\n|\Z)", re.S)


def smtp_names_read_by_code(source_text: str) -> set[str]:
    return set(_READ_RE.findall(source_text))


def atlas_service_body(compose_text: str) -> str:
    match = _ATLAS_BODY_RE.search(compose_text)
    assert match is not None, "docker-compose.yml 里找不到 atlas 服务块"
    return match.group("body")


def missing_passthrough(names, compose_text: str) -> set[str]:
    """返回"代码读了但 atlas 服务没透传"的变量名。"""
    body = atlas_service_body(compose_text)
    return {name for name in names if f"{name}: ${{{name}" not in body}


SOURCE_TEXT = SMTP_SOURCE.read_text(encoding="utf-8")
COMPOSE_TEXT = COMPOSE.read_text(encoding="utf-8")


def test_u1157_the_regex_is_not_blind():
    names = smtp_names_read_by_code(SOURCE_TEXT)
    assert "ATLAS_SMTP_HOST" in names, names
    # 少抽一个就意味着守护只看得到部分面：HOST/PORT/USERNAME/PASSWORD/FROM/USE_TLS
    assert len(names) == 6, names


def test_u1157_every_smtp_var_the_code_reads_reaches_the_container():
    gaps = missing_passthrough(smtp_names_read_by_code(SOURCE_TEXT), COMPOSE_TEXT)
    assert gaps == set(), f"这些 SMTP 变量代码在读、atlas 服务却没透传：{sorted(gaps)}"


def test_u1157_guard_reports_a_dropped_passthrough():
    """反向门：从**合成**的 compose 文本里抽掉一条，守护必须点名它（不动真文件）。"""
    body = atlas_service_body(COMPOSE_TEXT)
    doctored = COMPOSE_TEXT.replace(body, body.replace(
        "ATLAS_SMTP_PASSWORD: ${ATLAS_SMTP_PASSWORD:-}\n", "", 1
    ), 1)
    assert doctored != COMPOSE_TEXT, "锚点没命中＝这条反向门是空跑"
    assert "ATLAS_SMTP_PASSWORD: ${ATLAS_SMTP_PASSWORD:-}" not in atlas_service_body(doctored)
    assert missing_passthrough({"ATLAS_SMTP_PASSWORD"}, doctored) == {"ATLAS_SMTP_PASSWORD"}


def test_u1157_defaults_keep_the_demo_fallback_and_the_tls_semantics():
    body = atlas_service_body(COMPOSE_TEXT)
    # 这四条缺省必须是空：空 HOST 才是"回退进程内记录"的 demo 语义（不许塞一个真主进来）
    for name in ("ATLAS_SMTP_HOST", "ATLAS_SMTP_USERNAME", "ATLAS_SMTP_PASSWORD", "ATLAS_SMTP_FROM"):
        assert f"{name}: ${{{name}:-}}" in body, name
    # 这两条的缺省要和代码语义一致：587 端口、STARTTLS 默认开
    assert "ATLAS_SMTP_PORT: ${ATLAS_SMTP_PORT:-587}" in body
    assert "ATLAS_SMTP_USE_TLS: ${ATLAS_SMTP_USE_TLS:-true}" in body
