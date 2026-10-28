"""手册文档提取：PDF（可选 PyMuPDF）/ Markdown / TXT / DOCX → 统一文本结构。

设计原则：
- 核心查询（搜索/目录/阅读）只用标准库 SQLite，不依赖 PDF 库；
- 只有“建索引”需要可选的 PDF 解析器（PyMuPDF）或外部 pdftotext；
- 不把整本手册放进 agent 上下文，提取结果只写入本地 workspace/manuals。
"""
from __future__ import annotations

import pathlib
import re
import zipfile
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

from ..spec import decode_text


class ManualError(ValueError):
    """手册提取/建索引失败。"""


SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".docx"}


def _read_text_file(path: pathlib.Path) -> str:
    return decode_text(path.read_bytes())


def extract(path, max_pages: Optional[int] = None) -> Dict[str, Any]:
    """提取手册内容。

    返回：
    - PDF：``{"kind":"pdf", "title", "pages", "toc":[{level,title,page}], "page_texts":[...]}``
    - 文本类：``{"kind":"text", "title", "pages":1, "text": "..."}``
    """
    path = pathlib.Path(path)
    if not path.exists():
        raise ManualError("文件不存在：%s" % path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ManualError("不支持的文件类型 %s（支持：%s）"
                          % (suffix or "无后缀", " / ".join(sorted(SUPPORTED_SUFFIXES))))
    if suffix == ".pdf":
        return _extract_pdf(path, max_pages=max_pages)
    if suffix == ".docx":
        return {"kind": "text", "title": path.stem, "pages": 1,
                "text": _docx_to_text(path)}
    return {"kind": "text", "title": path.stem, "pages": 1,
            "text": _read_text_file(path)}


def _extract_pdf(path: pathlib.Path, max_pages: Optional[int] = None) -> Dict[str, Any]:
    try:
        import fitz  # type: ignore  # 可选依赖：PyMuPDF
    except Exception as exc:  # pragma: no cover - 取决于本机环境
        raise ManualError(
            "PDF 建索引需要可选依赖 PyMuPDF。请先运行：\n"
            "    python -m pip install -r requirements-manuals.txt\n"
            "（查询已有索引不需要该依赖。）"
        ) from exc

    doc = fitz.open(str(path))
    try:
        total = len(doc)
        limit = total if max_pages is None else max(1, min(total, int(max_pages)))
        page_texts: List[str] = []
        for index in range(limit):
            page = doc[index]
            try:
                text = page.get_text("text", sort=True) or ""
            except TypeError:  # 旧版 PyMuPDF 没有 sort 参数
                text = page.get_text("text") or ""
            page_texts.append(text)

        toc: List[Dict[str, Any]] = []
        try:
            raw_toc = doc.get_toc() or []
        except Exception:
            raw_toc = []
        for item in raw_toc:
            if not item or len(item) < 3:
                continue
            level, title, page = item[0], item[1], item[2]
            try:
                page = int(page)
                level = int(level)
            except (TypeError, ValueError):
                continue
            if page < 1:
                continue
            toc.append({"level": max(1, level), "title": str(title).strip(),
                        "page": page})

        meta = doc.metadata or {}
        title = str(meta.get("title") or "").strip() or path.stem
        return {
            "kind": "pdf",
            "title": title,
            "pages": limit,
            "total_pages": total,
            "toc": toc,
            "page_texts": page_texts,
            "source": str(path),
        }
    finally:
        doc.close()


def _docx_to_text(path: pathlib.Path) -> str:
    """用标准库 zipfile+xml 粗提 DOCX（保留标题层级为 Markdown #）。"""
    try:
        with zipfile.ZipFile(str(path)) as archive:
            xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise ManualError("DOCX 缺少 word/document.xml：%s" % path) from exc
    except Exception as exc:
        raise ManualError("DOCX 读取失败：%s" % exc) from exc

    root = ElementTree.fromstring(xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines: List[str] = []
    for paragraph in root.iter(ns + "p"):
        texts = [node.text or "" for node in paragraph.iter(ns + "t")]
        text = "".join(texts).strip()
        if not text:
            continue
        level = 0
        style = paragraph.find(".//" + ns + "pStyle")
        if style is not None:
            value = style.get(ns + "val") or ""
            match = re.search(r"(?i)(?:heading|标题)\s*([1-6])", value)
            if match:
                level = int(match.group(1))
        lines.append(("#" * level + " " + text) if level else text)
    return "\n".join(lines)
