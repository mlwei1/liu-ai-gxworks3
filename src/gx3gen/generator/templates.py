"""构造指令 IR 的小工具。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from ..ir import Instruction, Rung
from ..spec import is_device, is_operand, resolve_operand

SFC_STEP_RE = re.compile(r"^S\d+$", re.IGNORECASE)


def ins(op: str, *operands: str) -> Instruction:
    return Instruction(op=op, operands=[operand for operand in operands if operand != ""])


def rung(*instructions: Instruction, comment: str = "") -> Rung:
    return Rung(instructions=list(instructions), comment=comment)


def is_sfc_step(device: str) -> bool:
    return bool(SFC_STEP_RE.match((device or "").strip()))


def resolve(spec: Dict[str, Any], token: Any) -> str:
    return resolve_operand(spec, str(token))


def rung_from_rules(spec: Dict[str, Any], rules: List[Dict[str, Any]], comment: str = "") -> Rung:
    """把 spec 里的 [{op, operands}] 规则转成 Rung（操作数做设备解析）。"""
    instructions = []
    for rule in rules:
        op = str(rule.get("op") or "").strip()
        operands = [resolve(spec, item) for item in (rule.get("operands") or [])]
        instructions.append(ins(op, *operands))
    return rung(*instructions, comment=comment)
