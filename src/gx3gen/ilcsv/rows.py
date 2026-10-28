"""GX Works3 程序本体 CSV / 软元件注释 CSV 的行模型与读写。

格式（GX Works3 1.082 实测 + 官方手册）：
- UTF-16LE + BOM、TAB 分隔、所有字段双引号、CRLF；
- 7 列：步号 | 行间声明 | 指令 | I/O(软元件) | 空白栏 | PI声明 | 注解；
- 第 1 行项目名，第 2 行机型信息，第 3 行表头，之后为数据行。
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..spec import decode_text

CSV_HEADER = ["步号", "行间声明", "指令", "I/O(软元件)", "空白栏", "PI声明", "注解"]

CLASS_ORDER = ["U", "SD", "SM", "M", "D", "S", "X", "Y", "T", "C", "ZR", "L",
               "B", "F", "V", "Z", "SW", "SB", "TN", "CN", "STN", "LTN", "LCN",
               "LSTN", "ST", "R", "W"]


@dataclass
class CsvRow:
    step: str = ""
    decl: str = ""
    instr: str = ""
    io: str = ""
    blank: str = ""
    pi: str = ""
    note: str = ""

    def cells(self) -> List[str]:
        return [self.step, self.decl, self.instr, self.io, self.blank, self.pi, self.note]


@dataclass
class ProgramCsv:
    project: str = ""
    cpu: str = "FX5CPU FX5U"
    rows: List[CsvRow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------
def _read_content(path_or_text: Any) -> str:
    """接受路径 / bytes / 文本。"""
    if isinstance(path_or_text, bytes):
        return decode_text(path_or_text)
    if isinstance(path_or_text, str) and "\t" not in path_or_text \
            and "\n" not in path_or_text and Path(path_or_text).exists():
        return decode_text(Path(path_or_text).read_bytes())
    return decode_text(path_or_text)


def read_program_csv(path_or_text: Any) -> ProgramCsv:
    content = _read_content(path_or_text)
    raw = list(csv.reader(io.StringIO(content, newline=""), delimiter="\t"))
    prog = ProgramCsv()
    if not raw:
        return prog
    prog.project = raw[0][0] if raw[0] else ""
    if len(raw) > 1 and len(raw[1]) > 1:
        prog.cpu = raw[1][1]
    for cells in raw[3:]:
        cells = list(cells) + [""] * 7
        prog.rows.append(CsvRow(step=cells[0], decl=cells[1], instr=cells[2],
                                io=cells[3], blank=cells[4], pi=cells[5], note=cells[6]))
    return prog


def read_comment_csv(path_or_text: Any) -> Dict[str, str]:
    """读软元件注释 CSV；多候选注释列时取第一个非空注释。"""
    content = _read_content(path_or_text)
    raw = list(csv.reader(io.StringIO(content, newline=""), delimiter="\t"))
    out: Dict[str, str] = {}
    for cells in raw[2:]:
        cells = list(cells) + [""] * 2
        device = (cells[0] or "").strip()
        if not device:
            continue
        comment = ""
        for cell in cells[1:]:
            if (cell or "").strip():
                comment = cell.strip()
                break
        out[device] = comment
    return out


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
def _write_rows(path: str, rows: Iterable[List[str]], lineterminator: str = "\r\n") -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-16", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator=lineterminator,
                            quoting=csv.QUOTE_ALL)
        writer.writerows(rows)


def write_program_csv(prog: ProgramCsv, path: str) -> None:
    rows: List[List[str]] = [[prog.project], ["机型信息:", prog.cpu], list(CSV_HEADER)]
    rows.extend(row.cells() for row in prog.rows)
    _write_rows(path, rows)


def _sort_key(device: str):
    match = re.match(r"([A-Za-z]+)(.*)", device or "")
    cls = match.group(1).upper() if match else (device or "")
    number = match.group(2) if match else ""
    try:
        order = CLASS_ORDER.index(cls)
    except ValueError:
        order = len(CLASS_ORDER)
    try:
        value = int(float(number))
    except ValueError:
        value = 0
    return order, value, device


# GX Works3 软元件注释 CSV 的注释标题（[视图]→[多注释显示设置] 里的注释标题名）。
#
# 实测（GX Works3 1.082 真实导出样例）：
# - 中文版默认工程导出的是 "Chinese Simplified/简体中文"；
# - 另有工程把标题自定义成 "注释"（FK 阀内漏测试设备样例）。
# GX 导入时按标题名去工程里匹配，匹配不到就报
# 「"注释"相应的注释标题不存在，因此未读取」+「文件格式有误」。
#
# 默认只输出一个标题（与目标工程一致 → 零错误）；换工程时用
# spec 的 project.comment_title / project.comment_titles 覆盖。
# 注意不要图省事输出多个候选：GX 会逐列检查，不存在的标题会各报一次错。
COMMENT_DEVICE_HEADER = "软元件名"
COMMENT_TITLE = "Chinese Simplified/简体中文"


def write_comment_csv(project: str, comments: Dict[str, str], path: str,
                      device_header: str = COMMENT_DEVICE_HEADER,
                      comment_titles: Optional[Iterable[str]] = None) -> None:
    """写软元件注释 CSV。

    与 GX Works3 1.082 真实导出样例严格一致：
    - UTF-16LE + BOM、TAB 分隔、所有字段双引号、**行尾 LF**；
    - 第 1 行工程名（单字段）、第 2 行 "软元件名" + 注释标题、第 3 行起为数据；
    - 注释标题必须是目标工程里存在的那个，否则 GX 报「注释标题不存在」。
    """
    if comment_titles is None:
        titles = [COMMENT_TITLE]
    else:
        titles = [str(item).strip() for item in comment_titles if str(item).strip()]
        if not titles:
            titles = [COMMENT_TITLE]
    rows: List[List[str]] = [[project], [device_header] + titles]
    for device in sorted(comments.keys(), key=_sort_key):
        rows.append([device] + [comments[device]] * len(titles))
    _write_rows(path, rows, lineterminator="\n")


# ---------------------------------------------------------------------------
# 便捷解析
# ---------------------------------------------------------------------------
def iter_markers(prog: ProgramCsv) -> List[Dict[str, Any]]:
    """段标题 / 语句行。"""
    out: List[Dict[str, Any]] = []
    for index, row in enumerate(prog.rows, start=1):
        decl = (row.decl or "").strip()
        if decl and not (row.instr or "").strip():
            out.append({"line": index, "step": row.step, "text": decl,
                        "title": decl[len("[Title]"):] if decl.startswith("[Title]") else None})
    return out


def iter_instructions(prog: ProgramCsv) -> List[Dict[str, Any]]:
    """把行模型还原成指令序列（处理多操作数续行与注解行）。"""
    out: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            out.append(current)
            current = None

    for index, row in enumerate(prog.rows, start=1):
        instr = (row.instr or "").strip()
        operand = (row.io or "").strip()
        note = (row.note or "").strip()
        if instr:
            flush()
            current = {"line": index, "step": row.step, "decl": row.decl,
                       "op": instr, "operands": [operand] if operand else [],
                       "note": note}
        elif operand and current is not None:
            current["operands"].append(operand)
            if note:
                current["note"] = (current["note"] + " " + note).strip()
        elif note and current is not None:
            current["note"] = (current["note"] + " " + note).strip()
        elif (row.decl or "").strip():
            flush()
        else:
            flush()
    flush()
    return out
