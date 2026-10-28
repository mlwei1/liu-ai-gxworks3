"""反馈优化：把历史纠错变成"下一轮必须自检的清单"和"规划提示词"，并重新规划。

``apply_lessons(project)`` 的返回结构（契约见 docs/INTERFACES.md）::

    {"spec": ProgramSpec|None, "checklist": [str], "prompt_hint": str}

- 项目已有 spec：按 lessons 重新走确定性规划，产出更新后的 spec；
- 项目还没有 spec：只返回 checklist/prompt_hint（spec 原样返回 None）；
- 无论哪种情况，都把最新聚合 lessons 写回 project 并调用 ``workspace.save_project``。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Sequence

from ..spec import validate_spec
from . import store

__all__ = [
    "apply_lessons",
    "build_checklist",
    "build_prompt_hint",
]

BASE_CHECKS = [
    "spec 必须通过 validate_spec，且所有设备都是合法软元件号",
    "所有动作/条件设备都能在 IO 表或 assumptions 中找到依据",
    "每段最后一步能回到待机步或给出交接/完成信号",
    "延时定时器（T0 起）与报警定时器（T10 起）地址不冲突",
]

CATEGORY_CHECKS: Dict[str, List[str]] = {
    "结构": [
        "核对段顺序：初始化 → 设备段 → 启动停止 → 报警",
        "核对各工位段步号段不重叠、步号递增",
    ],
    "命名": [
        "核对软元件注释命名与现场 IO 表完全一致",
        "核对握手/交接信号命名符合用户习惯（如 A->B/动作）",
    ],
    "逻辑": [
        "逐条复核被反馈网络的置位/复位/自保逻辑",
        "确认动作输出不会在同一扫描周期被覆盖（避免双线圈）",
    ],
    "互锁": [
        "动作前检查对侧原位/夹紧/安全互锁条件是否齐全",
        "确认两个气缸/夹爪不会同时动作",
    ],
    "报警": [
        "每个气缸都要有到位/原位报警位与延时定时器",
        "确认报警清除逻辑与停止/复位按钮一致",
    ],
    "停止": [
        "确认停止/急停能复位运行步、模式位和输出",
        "确认复位后能回到待机步 S0",
    ],
    "其他": ["复核反馈描述的具体问题"],
}

KEYWORD_CHECKS = (
    (("互锁", "interlock"), "互锁"),
    (("报警", "alarm"), "报警"),
    (("停止", "急停", "stop"), "停止"),
    (("顺序", "结构", "段"), "结构"),
    (("注释", "命名", "名称"), "命名"),
    (("逻辑", "条件", "输出"), "逻辑"),
)


def _short(text: Any, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit]


def _normalize_lessons(lessons: Any) -> List[Dict[str, Any]]:
    """兼容 store.lessons 结构、dict 列表与纯字符串列表。"""
    out: List[Dict[str, Any]] = []
    for item in lessons or []:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append({"category": "其他", "count": 1, "last_message": text,
                            "message": text, "examples": []})
            continue
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "其他").strip() or "其他"
        message = str(item.get("last_message") or item.get("message") or "").strip()
        examples = [str(value).strip() for value in (item.get("examples") or []) if str(value).strip()]
        if not message and examples:
            message = examples[0]
        if not message and not examples:
            continue
        try:
            count = int(item.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        out.append({
            "category": category,
            "count": max(1, count),
            "last_message": message,
            "message": message,
            "examples": examples[:5],
        })
    return out


def _guess_categories(message: str) -> List[str]:
    text = str(message or "")
    categories: List[str] = []
    for keywords, category in KEYWORD_CHECKS:
        if any(word in text for word in keywords) and category not in categories:
            categories.append(category)
    return categories or ["其他"]


def build_checklist(lessons: Any, limit: int = 24) -> List[str]:
    """lessons → 本轮生成必须自检的清单（去重、稳定顺序、上限 limit 条）。"""
    items = _normalize_lessons(lessons)
    checklist: List[str] = list(BASE_CHECKS)
    seen = set(checklist)

    def _add(text: str) -> None:
        text = text.strip()
        if not text or text in seen or len(checklist) >= limit:
            return
        seen.add(text)
        checklist.append(text)

    for item in items:
        categories = [item["category"]] if item["category"] in CATEGORY_CHECKS else []
        if not categories:
            categories = _guess_categories(item["message"])
        for category in categories:
            for rule in CATEGORY_CHECKS.get(category, CATEGORY_CHECKS["其他"]):
                _add(rule)
        if item["message"]:
            _add("复核历史问题：%s" % _short(item["message"]))
    return checklist


def build_prompt_hint(lessons: Any, corrections: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    """lessons/修正样例 → 注入 LLM 与规则规划的提示词。"""
    items = _normalize_lessons(lessons)
    lines: List[str] = [
        "以下是用户历史纠错经验。本轮规划/生成必须优先规避同类问题，"
        "但不得违背本次需求与 IO 表（反馈是更高优先级的检查项，不是绝对规则）："
    ]
    if items:
        for item in items[:12]:
            line = "- [%s×%d] %s" % (item["category"], item["count"], item["last_message"])
            if item["examples"]:
                line += "（例：%s）" % "；".join(item["examples"][:2])
            lines.append(line)
    else:
        lines.append("-（暂无历史反馈）")

    correction_items = [item for item in (corrections or []) if isinstance(item, dict)]
    if correction_items:
        lines.append("")
        lines.append("用户修正过的 spec 样例（只作结构参考，不复制具体逻辑）：")
        for item in correction_items[:3]:
            note = str(item.get("note") or "").strip()
            spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
            titles = [
                str(segment.get("title") or "")
                for segment in spec.get("segments") or []
                if isinstance(segment, dict) and segment.get("title")
            ]
            detail = "、".join(titles[:8]) if titles else "（无段信息）"
            if note:
                lines.append("- %s：%s" % (_short(note), detail))
            else:
                lines.append("- %s" % detail)

    lines.append("")
    lines.append("本轮自检清单：")
    for item in build_checklist(items):
        lines.append("- " + item)
    return "\n".join(lines)[:3000]


def _load_lessons(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    project_id = project.get("id")
    if project_id:
        try:
            records = store.lessons(str(project_id))
        except Exception:
            records = []
        if records:
            return _normalize_lessons(records)
    return _normalize_lessons(project.get("lessons"))


def _replan(project: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """按需求 + IO + 风格 + lessons 重新规划；失败时返回 None（保留原 spec）。"""
    try:
        from .. import config as config_mod
        from ..planner.plan import plan as plan_project

        fresh = plan_project(project, config_mod.load_config(), use_llm=False)
        validate_spec(fresh)
        return fresh
    except Exception:
        return None


def _annotate_spec(spec: Dict[str, Any], lessons: List[Dict[str, Any]],
                   checklist: List[str]) -> Dict[str, Any]:
    """无法重新规划时，至少把经验写进 spec.notes（不改动原有逻辑）。"""
    annotated = copy.deepcopy(spec)
    notes = annotated.setdefault("notes", [])
    if not isinstance(notes, list):
        notes = []
        annotated["notes"] = notes
    notes.append("根据历史反馈检查：%s" % "；".join(checklist[:6]))
    for item in lessons:
        notes.append("历史反馈（%s）：%s" % (item["category"], item["last_message"]))
    return annotated


def apply_lessons(project: Dict[str, Any]) -> Dict[str, Any]:
    """把项目历史反馈转成 checklist/prompt_hint，并在已有 spec 时重新规划。

    返回 ``{"spec": ProgramSpec|None, "checklist": [str], "prompt_hint": str}``；
    同时把最新 lessons 写回项目并保存。
    """
    data = copy.deepcopy(project) if isinstance(project, dict) else {}
    if not isinstance(data, dict):
        data = {}
    lessons = _load_lessons(data)
    corrections: List[Dict[str, Any]] = []
    project_id = data.get("id")
    if project_id:
        try:
            corrections = store.corrections(str(project_id), limit=3)
        except Exception:
            corrections = []

    checklist = build_checklist(lessons)
    prompt_hint = build_prompt_hint(lessons, corrections)

    current_spec = data.get("spec")
    result_spec = current_spec
    if current_spec is not None:
        fresh = _replan(data)
        if fresh is not None:
            notes = fresh.setdefault("notes", [])
            if isinstance(notes, list):
                notes.append("本轮已根据 %d 条历史反馈重新规划（教训 %d 类）"
                             % (sum(item["count"] for item in lessons), len(lessons)))
            result_spec = fresh
        elif isinstance(current_spec, dict):
            result_spec = _annotate_spec(current_spec, lessons, checklist)

    canonical_lessons = [
        {
            "category": item["category"],
            "count": item["count"],
            "last_message": item["last_message"],
            "examples": list(item["examples"]),
        }
        for item in lessons
    ]
    data["lessons"] = canonical_lessons
    if result_spec is not None:
        data["spec"] = result_spec
    if project_id:
        # 契约要求：把 latest 反馈/重新规划的 spec 写回项目
        from .. import workspace

        workspace.save_project(data)

    return {"spec": result_spec, "checklist": checklist, "prompt_hint": prompt_hint}
