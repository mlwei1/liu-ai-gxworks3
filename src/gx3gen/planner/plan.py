"""规划入口：项目（需求 + IO + 风格 + 反馈）→ ProgramSpec。

**默认走规则/领域模板**：:func:`gx3gen.planner.deterministic.plan_deterministic`；
``use_llm=True`` 时才会调用可选 standalone LLM 适配器（需要用户显式配置 base_url/model/api_key）。
**Agent-first 模式不使用本模块的 LLM 路径**：agent 自己产出 ProgramSpec，MCP 只做校验/生成。
返回前统一补 project.name / style_profile，并调用 ``validate_spec``。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import workspace
from ..quality import check_quality
from ..spec import CPU, validate_spec
from . import deterministic
from .llm import PlannerError, llm_configured, plan_with_llm

__all__ = ["PlannerError", "plan", "plan_deterministic", "plan_with_llm", "plan_with_quality"]

plan_deterministic = deterministic.plan_deterministic


def _dedupe(items: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _load_project_style(project: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """project["style_profile"] 可以是内嵌画像 dict，也可以是风格库 id。"""
    reference = project.get("style_profile")
    if isinstance(reference, dict):
        return dict(reference) or None
    if isinstance(reference, str) and reference.strip():
        try:
            return workspace.load_style(reference.strip())
        except (OSError, ValueError):
            return None
    return None


def _project_lessons(project: Dict[str, Any]) -> List[Any]:
    """优先读项目反馈派生的 lessons；项目尚未落盘时退回 project["lessons"]。"""
    project_id = project.get("id")
    if project_id:
        try:
            from ..feedback import store

            records = store.lessons(str(project_id))
        except Exception:  # 项目文件不存在/反馈层未就绪时不影响规划
            records = []
        if records:
            return list(records)
    return list(project.get("lessons") or [])


def plan(
    project: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    use_llm: bool = False,
) -> Dict[str, Any]:
    """规划 ProgramSpec。

    :param project: workspace 项目 dict（至少含 requirement/io；可选 id/name/style_profile）
    :param config: gx3gen.config.load_config() 的结果；缺省时按"未配置 LLM"处理
    :param use_llm: True 且 LLM 配置齐全时使用 LLM，否则确定性规划
    """
    if not isinstance(project, dict):
        raise PlannerError("project 必须是字典")
    config = config if isinstance(config, dict) else {}
    requirement = str(project.get("requirement") or "")
    io = list(project.get("io") or [])
    style = _load_project_style(project)
    lessons = _project_lessons(project)
    name = str(project.get("name") or "").strip() or "自动生成项目"

    used_llm = False
    if use_llm and llm_configured(config):
        spec = plan_with_llm(requirement, io, style, lessons, config)
        used_llm = True
    else:
        spec = deterministic.plan_deterministic(
            requirement, io, style, lessons, name=name
        )

    spec = dict(spec)
    project_spec = spec.setdefault("project", {})
    if not isinstance(project_spec, dict):
        project_spec = {}
        spec["project"] = project_spec
    project_spec["name"] = name
    project_spec["cpu"] = CPU
    if style and isinstance(style, dict) and style.get("id"):
        project_spec["style_profile"] = style.get("id")

    if used_llm:
        deterministic.apply_style_baseline(spec, style)
        deterministic.apply_lessons_to_spec(spec, lessons)
        spec["assumptions"] = _dedupe(
            list(spec.get("assumptions") or []) + ["本 spec 由 LLM 规划，请人工复核假设与设备"]
        )
        spec["notes"] = _dedupe(list(spec.get("notes") or []))
    else:
        spec["assumptions"] = _dedupe(spec.get("assumptions") or [])
        spec["notes"] = _dedupe(spec.get("notes") or [])

    validate_spec(spec)
    return spec


def plan_with_quality(
    project: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    use_llm: bool = False,
) -> Dict[str, Any]:
    """规划 + 质量检查，返回 {spec, quality, planner}。

    供 Web API / MCP 工具 / Agent 使用；``plan()`` 保持只返回 spec 的旧行为。
    """
    config = config if isinstance(config, dict) else {}
    used_llm = bool(use_llm and llm_configured(config))
    spec = plan(project, config, use_llm=use_llm)
    requirement = str((project or {}).get("requirement") or "") if isinstance(project, dict) else ""
    quality = check_quality(spec, requirement)
    return {
        "spec": spec,
        "quality": quality,
        "planner": "llm" if used_llm else "deterministic",
    }
