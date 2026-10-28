"""本地手册知识库：SQLite 索引 + 清单 + 检索入口。

存储位置：``<workspace>/manuals/manuals.sqlite3``（workspace 默认 gitignore）。
查询只用标准库 sqlite3；PDF 解析依赖可选 PyMuPDF（仅建索引时）。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

from ..config import workspace_root
from .chunk import build_sections, split_text
from .extract import ManualError, extract
from .tokens import token_field

SCHEMA_VERSION = "1"
CHUNK_TARGET = 1600
CHUNK_OVERLAP = 200


def manual_root() -> pathlib.Path:
    path = pathlib.Path(workspace_root()) / "manuals"
    path.mkdir(parents=True, exist_ok=True)
    (path / "inbox").mkdir(exist_ok=True)
    return path


def db_path() -> pathlib.Path:
    return manual_root() / "manuals.sqlite3"


def manifest_path() -> pathlib.Path:
    return manual_root() / "manifest.json"


def connect() -> sqlite3.Connection:
    path = db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS manuals (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                path TEXT,
                sha256 TEXT UNIQUE,
                pages INTEGER DEFAULT 0,
                toc_count INTEGER DEFAULT 0,
                model TEXT DEFAULT '',
                version TEXT DEFAULT '',
                language TEXT DEFAULT 'zh',
                status TEXT DEFAULT 'indexed',
                size_bytes INTEGER DEFAULT 0,
                indexed_at TEXT,
                metadata_json TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manual_id TEXT NOT NULL,
                parent_id INTEGER,
                level INTEGER DEFAULT 1,
                ordinal INTEGER DEFAULT 0,
                heading TEXT,
                heading_path TEXT,
                page_start INTEGER DEFAULT 1,
                page_end INTEGER DEFAULT 1,
                body TEXT
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manual_id TEXT NOT NULL,
                section_id INTEGER NOT NULL,
                heading_path TEXT,
                page_start INTEGER DEFAULT 1,
                page_end INTEGER DEFAULT 1,
                body TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                body_tokens, heading_tokens,
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE INDEX IF NOT EXISTS idx_sections_manual ON sections(manual_id, ordinal);
            CREATE INDEX IF NOT EXISTS idx_chunks_manual ON chunks(manual_id);
            """
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        conn.close()
        raise ManualError("SQLite FTS5 不可用：%s（请使用官方 Python 3.9+）" % exc) from exc
    return conn


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _slug(text: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "-", str(text or "")).strip("-._")
    return (text or "manual")[:48]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _manifest(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id,title,path,pages,toc_count,model,version,status,indexed_at,size_bytes "
        "FROM manuals ORDER BY indexed_at DESC, title"
    ).fetchall()
    data = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "manuals": [dict(row) for row in rows],
    }
    manifest_path().write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")


def add_manual(path, title: Optional[str] = None, model: str = "", version: str = "",
               replace: bool = False, max_pages: Optional[int] = None) -> Dict[str, Any]:
    """提取并索引一本手册；返回摘要；同一 sha256 不重复建索引。"""
    path = pathlib.Path(path)
    extracted = extract(path, max_pages=max_pages)
    sha = _sha256(path)
    conn = connect()
    try:
        existing = conn.execute("SELECT * FROM manuals WHERE sha256=?", (sha,)).fetchone()
        if existing:
            return {"ok": True, "skipped": True, "manual": dict(existing),
                    "message": "该文件已建过索引（sha256 相同）"}
        manual_title = (title or extracted.get("title") or path.stem).strip() or path.stem
        sections = build_sections(extracted, manual_title)
        if not sections:
            raise ManualError("没有提取到可索引的文本：%s" % path)
        if replace:
            conn.execute(
                "UPDATE manuals SET status='archived' WHERE status='indexed' AND title=? AND model=?",
                (manual_title, model or ""),
            )

        manual_id = "%s-%s" % (_slug(manual_title), sha[:8])
        pages = int(extracted.get("pages") or len(extracted.get("page_texts") or []) or 1)
        toc_count = len(extracted.get("toc") or sections)
        meta = {"kind": extracted.get("kind"), "total_pages": extracted.get("total_pages"),
                "source_suffix": path.suffix.lower()}
        conn.execute(
            "INSERT OR REPLACE INTO manuals "
            "(id,title,path,sha256,pages,toc_count,model,version,language,status,size_bytes,indexed_at,metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (manual_id, manual_title, str(path), sha, pages, toc_count, model or "",
             version or "", "zh", "indexed", path.stat().st_size, _now(),
             json.dumps(meta, ensure_ascii=False)),
        )
        # 清理同 id 的旧数据（INSERT OR REPLACE 不会级联）
        old_chunks = [row[0] for row in conn.execute(
            "SELECT id FROM chunks WHERE manual_id=?", (manual_id,)).fetchall()]
        if old_chunks:
            conn.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(item,) for item in old_chunks])
        conn.execute("DELETE FROM chunks WHERE manual_id=?", (manual_id,))
        conn.execute("DELETE FROM sections WHERE manual_id=?", (manual_id,))

        chunk_total = 0
        for ordinal, section in enumerate(sections):
            cursor = conn.execute(
                "INSERT INTO sections "
                "(manual_id,level,ordinal,heading,heading_path,page_start,page_end,body) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (manual_id, int(section.get("level") or 1), ordinal,
                 str(section.get("heading") or ""), str(section.get("heading_path") or ""),
                 int(section.get("page_start") or 1), int(section.get("page_end") or 1),
                 str(section.get("body") or "")),
            )
            section_id = cursor.lastrowid
            for part in split_text(str(section.get("body") or ""), CHUNK_TARGET, CHUNK_OVERLAP):
                chunk_cursor = conn.execute(
                    "INSERT INTO chunks (manual_id,section_id,heading_path,page_start,page_end,body) "
                    "VALUES (?,?,?,?,?,?)",
                    (manual_id, section_id, str(section.get("heading_path") or ""),
                     int(section.get("page_start") or 1), int(section.get("page_end") or 1), part),
                )
                chunk_id = chunk_cursor.lastrowid
                conn.execute(
                    "INSERT INTO chunks_fts (rowid, body_tokens, heading_tokens) VALUES (?,?,?)",
                    (chunk_id, token_field(part), token_field(str(section.get("heading_path") or ""))),
                )
                chunk_total += 1
        conn.commit()
        _manifest(conn)
        return {
            "ok": True,
            "manual_id": manual_id,
            "title": manual_title,
            "pages": pages,
            "sections": len(sections),
            "chunks": chunk_total,
            "model": model or "",
            "version": version or "",
            "skipped": False,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_manuals(include_archived: bool = False) -> List[Dict[str, Any]]:
    conn = connect()
    try:
        sql = ("SELECT id,title,pages,toc_count,model,version,status,indexed_at,size_bytes "
               "FROM manuals")
        params: tuple = ()
        if not include_archived:
            sql += " WHERE status='indexed'"
        sql += " ORDER BY indexed_at DESC, title"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def remove_manual(manual_id: str) -> Dict[str, Any]:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM manuals WHERE id=?", (manual_id,)).fetchone()
        if row is None:
            raise ManualError("手册不存在：%s" % manual_id)
        chunk_ids = [item[0] for item in conn.execute(
            "SELECT id FROM chunks WHERE manual_id=?", (manual_id,)).fetchall()]
        if chunk_ids:
            conn.executemany("DELETE FROM chunks_fts WHERE rowid=?",
                             [(item,) for item in chunk_ids])
        conn.execute("DELETE FROM chunks WHERE manual_id=?", (manual_id,))
        conn.execute("DELETE FROM sections WHERE manual_id=?", (manual_id,))
        conn.execute("DELETE FROM manuals WHERE id=?", (manual_id,))
        conn.commit()
        _manifest(conn)
        return {"ok": True, "manual_id": manual_id, "title": row["title"]}
    finally:
        conn.close()


def toc(manual_id: str, depth: int = 3, max_items: int = 300) -> List[Dict[str, Any]]:
    conn = connect()
    try:
        manual = conn.execute("SELECT id FROM manuals WHERE id=?", (manual_id,)).fetchone()
        if manual is None:
            raise ManualError("手册不存在：%s" % manual_id)
        rows = conn.execute(
            "SELECT id,level,heading,heading_path,page_start,page_end "
            "FROM sections WHERE manual_id=? AND level<=? ORDER BY ordinal LIMIT ?",
            (manual_id, max(1, int(depth)), max(1, int(max_items))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def read(manual_id: str, section_id: Optional[int] = None, page: Optional[int] = None,
         max_chars: int = 6000) -> Dict[str, Any]:
    conn = connect()
    try:
        manual = conn.execute("SELECT * FROM manuals WHERE id=?", (manual_id,)).fetchone()
        if manual is None:
            raise ManualError("手册不存在：%s" % manual_id)
        max_chars = max(500, min(int(max_chars or 6000), 20000))
        if section_id is not None:
            row = conn.execute(
                "SELECT * FROM sections WHERE id=? AND manual_id=?",
                (int(section_id), manual_id),
            ).fetchone()
            if row is None:
                raise ManualError("章节不存在：manual=%s section=%s" % (manual_id, section_id))
            text = str(row["body"] or "")
            start, end = row["page_start"], row["page_end"]
            path = row["heading_path"]
        elif page is not None:
            page = int(page)
            rows = conn.execute(
                "SELECT * FROM sections WHERE manual_id=? AND page_start<=? AND page_end>=? "
                "ORDER BY ordinal", (manual_id, page, page),
            ).fetchall()
            if not rows:
                raise ManualError("没有覆盖第 %s 页的章节（页码范围可能不精确）" % page)
            text = "\n\n".join(str(item["body"] or "") for item in rows)
            start, end = page, page
            path = " / ".join(str(item["heading_path"] or "") for item in rows[:2])
        else:
            raise ManualError("请提供 section_id 或 page")
        truncated = len(text) > max_chars
        return {
            "manual_id": manual_id,
            "title": manual["title"],
            "section_id": section_id,
            "heading_path": path,
            "page_start": start,
            "page_end": end,
            "text": text[:max_chars],
            "truncated": truncated,
        }
    finally:
        conn.close()


def manifest() -> Dict[str, Any]:
    if manifest_path().exists():
        try:
            return json.loads(manifest_path().read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"schema_version": SCHEMA_VERSION, "manuals": list_manuals(True)}
