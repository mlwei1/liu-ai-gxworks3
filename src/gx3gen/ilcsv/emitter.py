"""指令 IR → GX 程序本体 CSV / 软元件注释 CSV。"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from ..ir import Instruction, Program
from .rows import CsvRow, ProgramCsv, write_comment_csv, write_program_csv

# 简化步长（GX 转换时会重算；此处仅用于输出可读的步号列）
_CONTACT_COIL = {"LD", "LDI", "AND", "ANI", "OR", "ORI", "ORB", "ANB",
                 "MPS", "MRD", "MPP", "INV", "OUT", "SET", "RST", "ALT", "PLS", "PLF"}
_SFC_STEP_RE = re.compile(r"^S\d+$", re.IGNORECASE)


def instruction_steps(ins: Instruction) -> int:
    op = (ins.op or "").upper()
    operands = ins.operands or []
    if op == "STL":
        return 3
    if op in ("RETSTL", "STLRET"):
        return 1
    if op in ("SET", "RST") and operands and _SFC_STEP_RE.match(operands[0].strip()):
        return 3
    if op in ("OUT", "OUTP") and len(operands) >= 2 and operands[0][:1].upper() in ("T", "C"):
        return 5
    if op in ("MOV", "MOVP"):
        return 3
    if op in ("BMOV", "FMOV", "ZRST", "DMOV"):
        return 4
    if op in ("CMP", "CMPP", "ADD", "SUB", "MUL", "DIV", "INC", "DEC"):
        return 4
    if op == "END":
        return 1
    if op in _CONTACT_COIL:
        return 2
    return 2


def emit(program: Program) -> Tuple[ProgramCsv, Dict[str, int]]:
    """把 IR 渲染成 GX 程序本体 CSV 的行模型。"""
    rows: List[CsvRow] = []
    cursor = 0
    instructions = 0
    rung_count = 0

    def add_marker(text: str) -> None:
        nonlocal cursor
        rows.append(CsvRow(step=str(cursor), decl=text))
        cursor += 5 + len(text)

    for segment in program.segments:
        if segment.title:
            add_marker("[Title]" + segment.title)
        for statement in segment.statements:
            add_marker(statement)
        for rung in segment.rungs:
            rung_count += 1
            for ins in rung.instructions:
                instructions += 1
                operands = list(ins.operands or [])
                first = operands[0] if operands else ""
                rows.append(CsvRow(step=str(cursor), instr=ins.op, io=first))
                for operand in operands[1:]:
                    rows.append(CsvRow(step="", instr="", io=operand))
                cursor += instruction_steps(ins)
            if rung.comment:
                rows.append(CsvRow(note=rung.comment))

    rows.append(CsvRow(step=str(cursor), instr="END"))
    cursor += 1

    prog = ProgramCsv(project=program.project, cpu=program.cpu, rows=rows)
    stats = {"rows": len(rows), "instructions": instructions,
             "rungs": rung_count, "segments": len(program.segments), "steps": cursor}
    return prog, stats


def emit_csv(program: Program, program_csv_path: str, comment_csv_path: str,
             comments: Dict[str, str]) -> Dict[str, int]:
    prog, stats = emit(program)
    write_program_csv(prog, program_csv_path)
    write_comment_csv(program.project, comments, comment_csv_path)
    stats["comments"] = len(comments)
    return stats
