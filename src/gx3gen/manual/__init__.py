"""手册知识库（Phase 1）：提取 → 结构切块 → SQLite FTS5 检索。"""
from __future__ import annotations

from .extract import ManualError
from .retrieval import search
from .store import (add_manual, list_manuals, manifest, read, remove_manual, toc)

__all__ = [
    "ManualError",
    "add_manual",
    "list_manuals",
    "manifest",
    "read",
    "remove_manual",
    "search",
    "toc",
]
