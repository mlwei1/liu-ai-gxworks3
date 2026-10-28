"""网络表与生成报告（Markdown，给工程师 review / 修改）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..ir import Program
from ..spec import is_device


def _escape(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def network_table(program: Program) -> str:
    lines: List[str] = ["# 程序网络表", "",
                        "> 由 gx3gen 生成；指令为 GX Works3 程序本体 CSV 的 IL 文本，可在 GX 导入后查看梯形图。", ""]
    index = 0
    for segment in program.segments:
        title = segment.title or "（无标题）"
        lines.append("## %s（%s）" % (title, segment.kind))
        if segment.statements:
            lines.append("")
            for statement in segment.statements:
                lines.append("- 语句：%s" % statement)
        if not segment.rungs:
            lines.append("")
            lines.append("（无网络）")
            lines.append("")
            continue
        lines.append("")
        lines.append("| # | 指令 | 注释 |")
        lines.append("|---|---|---|")
        for rung in segment.rungs:
            index += 1
            instructions = " ".join(
                "%s %s" % (instruction.op, " ".join(instruction.operands))
                for instruction in rung.instructions)
            lines.append("| %d | `%s` | %s |" % (index, _escape(instructions), _escape(rung.comment)))
        lines.append("")
    return "\n".join(lines)


def report_markdown(program: Program, spec: Dict[str, Any], stats: Dict[str, Any],
                    issues: Optional[List[Dict[str, str]]] = None,
                    style_summary: str = "", summary: str = "") -> str:
    project = spec.get("project") or {}
    lines: List[str] = [
        "# 生成报告 · %s" % (project.get("name") or program.project),
        "",
        "- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- CPU：%s ｜ GX Works3：%s" % (project.get("cpu", "FX5U"), project.get("gx_version", "1.082")),
        "- 代码风格：软元件 + 注释 / 设备段 + SFC(STL)",
        "- 段数：%d ｜ 网络数：%d ｜ 指令数：%d ｜ 估算步数：%d" % (
            stats.get("segments", 0), stats.get("rungs", 0),
            stats.get("instructions", 0), stats.get("steps", 0)),
        "",
    ]
    if summary:
        lines.extend([summary, ""])
    if style_summary:
        lines.extend(["## 使用的风格画像", "", style_summary, ""])

    lines.extend(["## 段结构", "", "| # | 段 | 类型 | 网络数 |", "|---|---|---|---|"])
    for index, segment in enumerate(program.segments, start=1):
        lines.append("| %d | %s | %s | %d |" % (index, segment.title or "（无标题）",
                                                 segment.kind, len(segment.rungs)))
    lines.append("")

    assumptions = spec.get("assumptions") or []
    if assumptions:
        lines.extend(["## 假设 / 待确认", ""])
        for item in assumptions:
            lines.append("- %s" % item)
        lines.append("")

    notes = spec.get("notes") or []
    if notes:
        lines.extend(["## 备注", ""])
        for item in notes:
            lines.append("- %s" % item)
        lines.append("")

    if issues:
        lines.extend(["## 轻量检查", ""])
        for item in issues:
            lines.append("- [%s] %s（%s）" % (item.get("severity"), item.get("message"), item.get("code")))
        lines.append("")

    lines.extend([
        "## 在 GX Works3 中落地",
        "",
        "1. 打开基座工程副本（FX5U，已建 MAIN 程序）。",
        "2. 打开 MAIN 程序本体，光标放在程序内（不要停在 END 行）。",
        "3. `[编辑] → [导入文件]`，选择 `program.csv`。",
        "4. 软元件注释窗口导入 `comments.csv`。",
        "5. `全部转换`（Shift+Alt+F4），修编译错误、补逻辑、保存。",
        "",
        "> 导入会整段替换目标程序本体；CSV 由 GX 转换时重算梯形图几何与步号。",
        "",
    ])
    return "\n".join(lines)
