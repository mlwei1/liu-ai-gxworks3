"""规划器共享异常。"""
from __future__ import annotations


class PlannerError(RuntimeError):
    """需求无法规划为可用的 ProgramSpec（配置、LLM、质量不达标等）。"""
