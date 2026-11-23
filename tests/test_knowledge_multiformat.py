# -*- coding: utf-8 -*-
"""A-08 知识库多格式导入 + 引用可追溯 + 20 文档检索评估。

覆盖：
- Markdown 文件（base64）去标记、分块、source/doc_id/chunk_index 元数据；
- DOCX（标准库 zip + document.xml）段落提取；
- 旧纯文本导入路径不受影响（另见 test_knowledge_import.py）；
- 检索结果 citation 与导入文件 doc_id 对齐；
- 20 篇文档、20 条唯一查询的 top-3 检索评估；
- 非法 base64 / 不支持后缀 / text+base64 二选一 / DOCX text 路径拒绝。
"""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app

client = TestClient(app)
T1_ADMIN = {"username": "admin-a", "password": "admin123"}


@pytest.fixture(autouse=True)
def _admin_session():
    login = client.post("/api/auth/login", json=T1_ADMIN)
    assert login.status_code == 200
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    yield
    client.post("/api/demo/reset")
    client.headers.pop("authorization", None)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>" for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
    return buf.getvalue()


def _import_file(filename: str, raw: bytes, category: str = "manual") -> dict:
    resp = client.post(
        "/api/knowledge/import",
        json={
            "category": category,
            "filename": filename,
            "content_base64": _b64(raw),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a08_markdown_file_import_strips_markup_and_tracks_source():
    markdown = (
        "---\ntitle: 退款手册\n---\n"
        "# 退款手册\n\n"
        "退款必须在 **7 天** 内发起，详见 [售后规则](https://example.test/rules)。\n\n"
        "- 保留原包装\n"
        "- 上传订单截图\n"
    ).encode("utf-8")
    body = _import_file("refund.md", markdown)
    assert body["imported"] == 4
    first = body["items"][0]
    assert "退款手册" in first["content"]
    assert "**" not in first["content"]
    assert "https://example.test/rules" not in first["content"]
    assert first["metadata"]["category"] == "manual"
    assert first["metadata"]["source"] == "refund.md"
    assert first["metadata"]["format"] == "markdown"
    assert first["metadata"]["doc_id"] == hashlib.sha256(markdown).hexdigest()
    assert first["metadata"]["chunk_index"] == "0"
    assert first["metadata"]["chunk_count"] == "4"


def test_a08_docx_file_import_paragraphs_and_citation():
    raw = _docx_bytes(
        [
            "DOCX 产品手册：安装前请备份数据库。",
            "故障排查：先看健康检查，再看审计日志。",
        ]
    )
    body = _import_file("manual.docx", raw)
    assert body["imported"] == 2
    assert body["items"][0]["content"] == "DOCX 产品手册：安装前请备份数据库。"
    assert body["items"][0]["metadata"]["format"] == "docx"
    assert body["items"][1]["metadata"]["chunk_index"] == "1"

    result = client.get(
        "/api/memories/search",
        params={"q": "安装前备份数据库", "kind": "knowledge", "top_k": 2},
    ).json()["results"]
    assert result
    citation = result[0]["citation"]
    assert citation["source"] == "manual.docx"
    assert citation["format"] == "docx"
    assert citation["doc_id"] == hashlib.sha256(raw).hexdigest()
    assert citation["chunk_index"] == 0


def test_a08_file_import_validation_paths():
    # 二选一：text 与 content_base64 同时给 → 422
    both = client.post(
        "/api/knowledge/import",
        json={"category": "faq", "text": "x", "filename": "x.md", "content_base64": _b64(b"x")},
    )
    assert both.status_code == 422
    # 非法 base64 → 422
    bad = client.post(
        "/api/knowledge/import",
        json={"category": "faq", "filename": "x.md", "content_base64": "not-base64!!"},
    )
    assert bad.status_code == 422
    # 文件导入缺 filename → 422
    no_name = client.post(
        "/api/knowledge/import",
        json={"category": "faq", "content_base64": _b64(b"# x")},
    )
    assert no_name.status_code == 422
    # 不支持的后缀（PDF 尚未接入）→ 422
    pdf = client.post(
        "/api/knowledge/import",
        json={"category": "faq", "filename": "x.pdf", "content_base64": _b64(b"%PDF-1.4")},
    )
    assert pdf.status_code == 422
    assert "PDF" in pdf.json()["detail"]
    # text + .docx 不能被当成已解析文本 → 422
    text_docx = client.post(
        "/api/knowledge/import",
        json={"category": "faq", "filename": "x.docx", "text": "假装已经解析"},
    )
    assert text_docx.status_code == 422


def test_a08_twenty_document_retrieval_top3():
    docs: list[tuple[str, bytes, str]] = []
    for index in range(20):
        code = f"ZETA-{index:02d}"
        raw = (
            f"# 项目 {code} 资料\n\n"
            f"项目代号 {code}，本项目的唯一业务规则是「{code} 审批必须双人复核」。\n\n"
            "通用说明：所有项目都需要保留审计记录。"
        ).encode("utf-8")
        docs.append((code, raw, hashlib.sha256(raw).hexdigest()))
        body = _import_file(f"{code}.md", raw, category="rule")
        assert body["imported"] == 3

    hits = 0
    for code, _raw, doc_id in docs:
        results = client.get(
            "/api/memories/search",
            params={"q": f"{code} 审批双人复核", "kind": "knowledge", "top_k": 3},
        ).json()["results"]
        assert results, code
        assert any(
            item.get("citation", {}).get("doc_id") == doc_id for item in results
        ), f"{code} 未在 top-3 命中：{[item.get('citation') for item in results]}"
        hits += 1
    assert hits == 20
