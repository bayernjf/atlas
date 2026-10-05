# -*- coding: utf-8 -*-
"""打包 BA（U1158）：src/atlas 读到的每个部署变量必须「进得了容器」或有具名豁免。

SMTP（U1157）那次缺口的根因不是 SMTP 特殊，而是「代码读 env、compose 忘了透传」
这件事在部署面上没有机检。本文件把判据推广到**全部**部署变量：
  - 名单从 src/atlas 源码正则抽出（含间接读——常量名定义处的字面量也会被抽到）；
  - 每个名字必须在 atlas 服务的 environment 块里有映射，或在 EXEMPT 上带理由；
  - 反向门两个方向都要能红：compose 少一条 ⇒ 点名；compose 多一条 src 不读的
    死配置 ⇒ 也点名（死配置 = 部署面在讲一个代码已经不认的故事）。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "atlas"
COMPOSE = REPO / "docker-compose.yml"

# 这些是「平台自身基建」变量：由 compose 直接赋值或属于运行面契约，不是业务可调旋钮。
KNOWN_INFRA = {
    "ATLAS_ENV",
    "DATABASE_URL",
    "ATLAS_MASTER_KEY",
    "ATLAS_APPROVAL_HMAC_SECRET",
    "ATLAS_ADMIN_BOOTSTRAP_PASSWORD",
    "LITELLM_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",  # litellm 直连路径读取，src 不直接出现
}

# 唯一豁免：stdio MCP server 跑在操作员宿主机（mcp/server.py），不在 compose 容器里。
EXEMPT = {
    "ATLAS_MCP_TENANT_ID": "stdio MCP server 运行在操作员宿主机，不经 compose 容器",
}

_READ_RE = re.compile(r'["\']((?:ATLAS|LITELLM|OPENAI|SHOPIFY)_[A-Z0-9_]+|DATABASE_URL)["\']')
_ATLAS_BODY_RE = re.compile(r"\n  atlas:\n(?P<body>.*?)(?=\n  [A-Za-z0-9_-]+:\n|\Z)", re.S)
_ENV_LINE_RE = re.compile(r"^\s{6}([A-Z][A-Z0-9_]+):", re.M)


def names_read_by_src() -> set[str]:
    names: set[str] = set()
    for path in SRC.rglob("*.py"):
        names |= set(_READ_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
    return names


def atlas_env_names(compose_text: str) -> set[str]:
    match = _ATLAS_BODY_RE.search(compose_text)
    assert match is not None, "compose 里找不到 atlas 服务块"
    body = match.group("body")
    start = body.index("environment:")
    return set(_ENV_LINE_RE.findall(body[start:]))


def missing_from_compose(names, compose_text: str) -> set[str]:
    present = atlas_env_names(compose_text)
    return {n for n in names if n not in present and n not in EXEMPT}


def dead_in_compose(compose_text: str, read_names) -> set[str]:
    present = atlas_env_names(compose_text)
    infra = KNOWN_INFRA | set(read_names)
    return {n for n in present if n not in infra}


SOURCE_NAMES = names_read_by_src()
COMPOSE_TEXT = COMPOSE.read_text(encoding="utf-8")


def test_u1158_the_source_scan_is_not_blind():
    assert "ATLAS_SMTP_HOST" in SOURCE_NAMES
    assert "ATLAS_PUBLIC_URL" in SOURCE_NAMES
    # 间接读（常量名风格）也要被抽到，否则守护只看得到一半的面
    assert "ATLAS_AUDIT_RING" in SOURCE_NAMES
    assert len(SOURCE_NAMES) >= 50, len(SOURCE_NAMES)


def test_u1158_every_read_variable_reaches_the_container():
    gaps = missing_from_compose(SOURCE_NAMES, COMPOSE_TEXT)
    assert gaps == set(), f"代码在读、atlas 服务没透传也没豁免：{sorted(gaps)}"


def test_u1158_compose_carries_no_dead_config():
    dead = dead_in_compose(COMPOSE_TEXT, SOURCE_NAMES)
    assert dead == set(), f"compose 透传了 src 不再读的变量（部署面在讲旧故事）：{sorted(dead)}"


def test_u1158_reverse_gate_a_dropped_line_is_named():
    doctored = COMPOSE_TEXT.replace("      ATLAS_PUBLIC_URL: ${ATLAS_PUBLIC_URL:-}\n", "", 1)
    assert doctored != COMPOSE_TEXT, "锚点没命中＝这条反向门是空跑"
    assert "ATLAS_PUBLIC_URL" in missing_from_compose(SOURCE_NAMES, doctored)


def test_u1158_reverse_gate_a_dead_line_is_named():
    doctored = COMPOSE_TEXT.replace(
        "      ATLAS_SCHEDULE_ENABLED: ${ATLAS_SCHEDULE_ENABLED:-}\n",
        "      ATLAS_SCHEDULE_ENABLED: ${ATLAS_SCHEDULE_ENABLED:-}\n      ATLAS_NOT_READ_ANYWHERE: ${ATLAS_NOT_READ_ANYWHERE:-}\n",
        1,
    )
    assert "ATLAS_NOT_READ_ANYWHERE" in dead_in_compose(doctored, SOURCE_NAMES)


def test_u1158_exemptions_are_reasoned_and_alive():
    assert set(EXEMPT) == {"ATLAS_MCP_TENANT_ID"}
    # 豁免表不许腐烂：豁免的变量必须仍被代码读到（否则该把条目删了）
    assert {"ATLAS_MCP_TENANT_ID"} <= SOURCE_NAMES
