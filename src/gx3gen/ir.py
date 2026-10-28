"""与厂商无关的程序 IR（编译器内部格式）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Instruction:
    op: str
    operands: List[str] = field(default_factory=list)

    def text(self) -> str:
        return self.op + (" " + " ".join(self.operands) if self.operands else "")


@dataclass
class Rung:
    instructions: List[Instruction] = field(default_factory=list)
    comment: str = ""


@dataclass
class Segment:
    title: str
    kind: str
    rungs: List[Rung] = field(default_factory=list)
    step_range: Optional[Tuple[int, int]] = None
    statements: List[str] = field(default_factory=list)


@dataclass
class Program:
    project: str
    cpu: str
    segments: List[Segment] = field(default_factory=list)

    def all_rungs(self) -> List[Rung]:
        out: List[Rung] = []
        for segment in self.segments:
            out.extend(segment.rungs)
        return out

    def all_instructions(self) -> List[Instruction]:
        out: List[Instruction] = []
        for rung in self.all_rungs():
            out.extend(rung.instructions)
        return out

    def device_set(self) -> List[str]:
        seen = []
        for ins in self.all_instructions():
            for operand in ins.operands:
                if operand and operand not in seen:
                    seen.append(operand)
        return seen
