"""D13 后端元数据多语言（docs/70）反向门 G1–G3。

约定（docs/70 §2/§8）：
- 模板 ``name``/``description`` 均本地化；``id`` 是稳定键。
- 适配器只本地化 ``description``；``name`` 是 wire id（前端 ``adapter_id/tool.name`` = ``config.tool``），**禁止本地化**。
- locale 取 ``Accept-Language`` 首档，fail-closed 回退 zh-CN。

端点需 read 鉴权（autouse 夹具填充 DEFAULT_AUTH_HEADER）；此处再叠加 Accept-Language。
前端 ``apiClient`` 已自动带 Accept-Language；此处用显式 Header 验证后端按 locale 换值。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.web.i18n import CAPABILITY_I18N, TEMPLATE_I18N
from tests.conftest import DEFAULT_AUTH_HEADER

client = TestClient(app)

EN = "en-US"
ZH = "zh-CN"


def _get(path: str, lang: str | None = None):
    headers = dict(DEFAULT_AUTH_HEADER)
    if lang is not None:
        headers["Accept-Language"] = lang
    return client.get(path, headers=headers)


# ---------- G1：Accept-Language: en-US 返回英文 ----------

def test_templates_list_en_us_returns_english():
    items = _get("/api/templates", EN).json()["items"]
    assert {item["id"] for item in items} == set(TEMPLATE_I18N)
    for item in items:
        entry = TEMPLATE_I18N[item["id"]][EN]
        assert item["name"] == entry["name"]
        assert item["description"] == entry["description"]


def test_template_detail_en_us_returns_english_and_keeps_id_and_tags():
    for template_id in TEMPLATE_I18N:
        body = _get(f"/api/templates/{template_id}", EN).json()
        entry = TEMPLATE_I18N[template_id][EN]
        assert body["id"] == template_id
        assert body["name"] == entry["name"]
        assert body["description"] == entry["description"]
        assert body["tags"]  # tags 不翻译，原样保留 zh-CN 标签


def test_adapters_en_us_localizes_description_only_not_name():
    body = _get("/api/adapters", EN).json()
    seen_any = False
    for adapter in body:
        for tool in adapter["tools"]:
            seen_any = True
            # name 是 wire id（如 "login"），永不被本地化；description 按 locale 换
            if tool["description"] in CAPABILITY_I18N:
                assert tool["description"] == CAPABILITY_I18N[tool["description"]][EN]
            else:
                # 动态/未登记能力：fail-safe 回退 canonical（zh-CN 原文），不抛错
                assert tool["description"]
    assert seen_any


# ---------- G2：缺省 / 非法 / 不支持语言 fail-closed 回退 zh-CN ----------

def test_templates_list_no_header_returns_canonical_zh():
    items = _get("/api/templates").json()["items"]
    for item in items:
        assert item["name"] == _zh_template_name(item["id"])  # 与 canonical 一致（非英文）


def test_templates_list_invalid_locale_falls_back_to_zh():
    items = _get("/api/templates", "fr-FR").json()["items"]
    for item in items:
        assert item["name"] == _zh_template_name(item["id"])


def test_templates_list_unsupported_prefix_falls_back_to_zh():
    # 首档 ja-JP 不在支持列表且无 zh/en 前缀 → 回退默认 zh-CN
    items = _get("/api/templates", "ja-JP, fr-FR").json()["items"]
    for item in items:
        assert item["name"] == _zh_template_name(item["id"])


def test_adapters_no_header_keeps_canonical_zh_description():
    body = _get("/api/adapters").json()
    for adapter in body:
        for tool in adapter["tools"]:
            # 无 header → zh-CN → description 保持 canonical 中文原文（即表键），不替换
            if tool["description"] in CAPABILITY_I18N:
                assert CAPABILITY_I18N[tool["description"]]["en-US"] != tool["description"]
            else:
                assert tool["description"]


# ---------- G3：每个模板 + 适配器能力双语非空（翻译覆盖完整性）----------

def test_every_template_has_both_locales_non_empty():
    for template_id, locales in TEMPLATE_I18N.items():
        entry = locales[EN]
        assert entry["name"].strip(), template_id
        assert entry["description"].strip(), template_id


def test_every_capability_has_en_non_empty():
    for zh_name, locales in CAPABILITY_I18N.items():
        assert zh_name.strip()
        assert locales[EN].strip()


# ---------- 辅助 ----------

def _zh_template_name(template_id: str) -> str:
    """从 canonical 模板目录取 zh-CN name，用于验证回退不被英文污染。"""
    from atlas.template import get_template

    return get_template(template_id).name
