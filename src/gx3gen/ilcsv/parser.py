"""程序本体 CSV 的解析视图（供风格学习 / 测试使用）。"""
from __future__ import annotations

from typing import Any, Dict, List

from .rows import ProgramCsv, iter_instructions, iter_markers, read_program_csv


def parse(path_or_text: Any) -> Dict[str, Any]:
    prog = read_program_csv(path_or_text)
    return {
        "project": prog.project,
        "cpu": prog.cpu,
        "rows": prog.rows,
        "markers": iter_markers(prog),
        "instructions": iter_instructions(prog),
    }


def extract_segments(prog: ProgramCsv) -> List[Dict[str, Any]]:
    """按 [Title] 段标题 / 语句行切段，返回每段的标记与指令列表。

    返回：[{"title": str, "kind": "title"|"statement", "markers":[str],
            "instructions":[{"op","operands","step","note","line"}]}]
    段前（第一个标记之前）的指令不会被丢弃，而是归入 title="" 的首段。
    """
    markers = iter_markers(prog)
    instructions = iter_instructions(prog)
    segments: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {"title": "", "kind": "body", "markers": [], "instructions": []}
    marker_iter = iter(markers)
    pending_marker = next(marker_iter, None)
    for instruction in instructions:
        while pending_marker is not None and pending_marker["line"] < instruction["line"]:
            if current["instructions"] or current["markers"]:
                segments.append(current)
            current = {
                "title": pending_marker.get("title") or pending_marker["text"],
                "kind": "title" if pending_marker.get("title") else "statement",
                "markers": [pending_marker["text"]],
                "instructions": [],
            }
            pending_marker = next(marker_iter, None)
        current["instructions"].append(instruction)
    while pending_marker is not None:
        if current["instructions"] or current["markers"]:
            segments.append(current)
        current = {
            "title": pending_marker.get("title") or pending_marker["text"],
            "kind": "title" if pending_marker.get("title") else "statement",
            "markers": [pending_marker["text"]],
            "instructions": [],
        }
        pending_marker = next(marker_iter, None)
    if current["instructions"] or current["markers"]:
        segments.append(current)
    return segments
