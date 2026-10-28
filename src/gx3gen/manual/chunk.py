"""切块：按 PDF 书签目录 / Markdown 标题做结构感知切分。

每个 section 保留标题路径与页码范围；过长 section 再按段落切成带 overlap 的 chunk。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
PAGE_SPLIT_RE = re.compile(r"\n{3,}")


def build_sections(extracted: Dict[str, Any], fallback_title: str = "") -> List[Dict[str, Any]]:
    """把 extract() 的结果转成 section 列表。"""
    if extracted.get("kind") == "pdf":
        return _pdf_sections(extracted)
    return _text_sections(str(extracted.get("text") or ""), fallback_title or extracted.get("title") or "")


def _pdf_sections(extracted: Dict[str, Any]) -> List[Dict[str, Any]]:
    page_texts = [str(item or "") for item in extracted.get("page_texts") or []]
    pages = len(page_texts)
    toc = extracted.get("toc") or []
    if not toc:
        return _page_sections(page_texts)

    entries = sorted(toc, key=lambda item: (int(item.get("page") or 1),
                                            int(item.get("level") or 1)))
    sections: List[Dict[str, Any]] = []
    stack: List[str] = []  # heading 路径栈（按 level）
    for index, item in enumerate(entries):
        level = max(1, int(item.get("level") or 1))
        title = str(item.get("title") or "").strip()
        start = max(1, int(item.get("page") or 1))
        if index + 1 < len(entries):
            next_page = int(entries[index + 1].get("page") or start)
            end = next_page - 1 if next_page > start else start
        else:
            end = pages
        start = min(start, pages) if pages else 1
        end = min(max(end, start), pages) if pages else 1
        body = "\n".join(page_texts[start - 1:end]).strip()
        while len(stack) >= level:
            stack.pop()
        stack.append(title)
        sections.append({
            "level": level,
            "heading": title,
            "heading_path": " / ".join(item for item in stack if item),
            "page_start": start,
            "page_end": end,
            "body": body,
        })
    return sections or _page_sections(page_texts)


def _page_sections(page_texts: List[str]) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    for index, text in enumerate(page_texts, start=1):
        body = str(text or "").strip()
        if not body:
            continue
        sections.append({
            "level": 1,
            "heading": "第 %d 页" % index,
            "heading_path": "第 %d 页" % index,
            "page_start": index,
            "page_end": index,
            "body": body,
        })
    return sections


def _text_sections(text: str, fallback_title: str = "") -> List[Dict[str, Any]]:
    lines = text.splitlines()
    marks: List[Dict[str, Any]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            marks.append({"line": index, "level": len(match.group(1)),
                          "heading": match.group(2).strip()})
    if not marks:
        body = text.strip()
        if not body:
            return []
        title = fallback_title or "正文"
        return [{"level": 1, "heading": title, "heading_path": title,
                 "page_start": 1, "page_end": 1, "body": body}]

    sections: List[Dict[str, Any]] = []
    stack: List[str] = []
    for position, mark in enumerate(marks):
        start_line = mark["line"] + 1
        end_line = marks[position + 1]["line"] if position + 1 < len(marks) else len(lines)
        body = "\n".join(lines[start_line:end_line]).strip()
        level = max(1, int(mark["level"]))
        while len(stack) >= level:
            stack.pop()
        stack.append(mark["heading"])
        sections.append({
            "level": level,
            "heading": mark["heading"],
            "heading_path": " / ".join(item for item in stack if item),
            "page_start": 1,
            "page_end": 1,
            "body": body,
        })
    return sections


def split_text(text: str, target_chars: int = 1600, overlap_chars: int = 200) -> List[str]:
    """按段落切分长文本，尽量保持语义边界。"""
    text = re.sub(r"[ \t]+\n", "\n", (text or "").strip())
    if not text:
        return []
    if len(text) <= target_chars:
        return [text]
    paragraphs = [item.strip() for item in PAGE_SPLIT_RE.split(text) if item.strip()]
    chunks: List[str] = []
    current = ""
    for paragraph in paragraphs:
        if not current:
            current = paragraph
        elif len(current) + 2 + len(paragraph) <= target_chars:
            current += "\n\n" + paragraph
        else:
            chunks.append(current)
            tail = current[-overlap_chars:] if overlap_chars > 0 else ""
            current = (tail + "\n\n" + paragraph).strip() if tail else paragraph
    if current:
        chunks.append(current)
    # 极端情况下段落自身超长：硬切
    result: List[str] = []
    for chunk in chunks:
        if len(chunk) <= target_chars * 2:
            result.append(chunk)
            continue
        start = 0
        while start < len(chunk):
            result.append(chunk[start:start + target_chars])
            start += max(1, target_chars - overlap_chars)
    return [item for item in result if item.strip()]
