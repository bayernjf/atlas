"""知识库文档解析（A-08，docs/108 §7）。

只做本地可验证的格式：Markdown / 纯文本 / DOCX。PDF 不做，因为仓库当前没有
PDF 解析依赖，A-08 的完成口径是「至少 pdf/docx/md 两类」；docx + md 已满足两类。
解析器不依赖第三方库：DOCX 用标准库 zipfile + ElementTree 读 ``word/document.xml``。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

DOCUMENT_MAX_BYTES = 10 * 1024 * 1024
DOCX_XML_MAX_BYTES = 50 * 1024 * 1024

SUPPORTED_FORMATS = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".docx": "docx",
}


class DocumentParseError(ValueError):
    """文档不合法或无法解析；REST 层折 422 中文。"""


def normalize_filename(filename: str) -> str:
    """只保留 basename，避免把本地路径写进 citation。"""
    name = Path(filename.replace("\\", "/")).name.strip()
    if not name or name in (".", ".."):
        raise DocumentParseError("filename 必须是有效文件名")
    return name


def detect_format(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    fmt = SUPPORTED_FORMATS.get(suffix)
    if fmt is None:
        raise DocumentParseError(
            "只支持 .md/.markdown/.txt/.docx；PDF 尚未接入"
        )
    return fmt


def decode_base64_document(content_base64: str, filename: str) -> tuple[bytes, str, str]:
    """解码 base64 文档，返回 (bytes, 规范化文件名, 格式)。"""
    name = normalize_filename(filename)
    fmt = detect_format(name)
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DocumentParseError("content_base64 不是合法 base64") from exc
    if not raw:
        raise DocumentParseError("文档内容为空")
    if len(raw) > DOCUMENT_MAX_BYTES:
        raise DocumentParseError(
            f"文档超过 {DOCUMENT_MAX_BYTES // (1024 * 1024)}MB 上限"
        )
    return raw, name, fmt


def document_digest(raw: bytes) -> str:
    """内容指纹；重复导入同一文件得到同一 doc_id，便于引用对齐。"""
    return hashlib.sha256(raw).hexdigest()


def extract_text(raw: bytes, fmt: str) -> str:
    if fmt == "docx":
        text = _extract_docx(raw)
    elif fmt == "markdown":
        text = _extract_markdown(raw)
    else:
        text = _decode_text(raw)
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise DocumentParseError("文档解析后没有可导入的文本")
    return text


def _decode_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentParseError("文本文件必须是 UTF-8 编码") from exc


def _extract_markdown(raw: bytes) -> str:
    """去 Markdown 标记但保留正文；标题/列表/链接文字仍参与检索。"""
    text = _decode_text(raw)
    # 去掉 YAML front matter，避免键值噪声进入知识库。
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            text = text[end + 4 :]
    text = re.sub(r"```[^\n]*\n", "", text)  # 开围栏
    text = text.replace("```", "")  # 闭围栏
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    # 列表项按段落断开，避免多条规则被塞进同一个 chunk。
    text = re.sub(r"^\s*[-*+]\s+", "\n\n", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(?<!\*)\*(?!\*)", "", text)
    return text


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _extract_docx(raw: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise DocumentParseError("DOCX 不是合法 zip 包") from exc
    try:
        try:
            entry = archive.getinfo("word/document.xml")
        except KeyError as exc:
            raise DocumentParseError("DOCX 缺少 word/document.xml") from exc
        if entry.file_size > DOCX_XML_MAX_BYTES:
            raise DocumentParseError("DOCX document.xml 超过解压上限")
        xml_bytes = archive.read(entry)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise DocumentParseError("DOCX 解压失败") from exc
    finally:
        archive.close()
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise DocumentParseError("DOCX document.xml XML 不合法") from exc

    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_W_NS}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_W_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W_NS}tab":
                parts.append("\t")
            elif node.tag in (f"{_W_NS}br", f"{_W_NS}cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs)
