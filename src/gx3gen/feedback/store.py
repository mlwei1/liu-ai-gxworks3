"""反馈记录存储。

数据落在 workspace 项目 JSON 里：

- ``project["feedback"]``：用户对"某段/某网络/整个程序"的纠错记录；
- ``project["corrected_specs"]``：用户修正后的 spec 样例（LLM few-shot / 模板候选）。

记录结构::

    {
      "id": "f-20261027...", "created_at": "...",
      "target": {"kind": "network|segment|program", "segment": "入料站", "index": 3},
      "category": "结构|命名|逻辑|互锁|报警|停止|其他",
      "severity": "error|warning|suggestion",
      "message": "缺少压装站到检测站的交接信号",
      "expected": "M1110 压装->检测/送料完成",
      "applied": false
    }
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List

from .. import workspace

__all__ = [
    "CATEGORIES",
    "SEVERITIES",
    "add",
    "corrections",
    "lessons",
    "list_records",
    "save_correction",
]

CATEGORIES = ("结构", "命名", "逻辑", "互锁", "报警", "停止", "其他")
SEVERITIES = ("error", "warning", "suggestion")
DEFAULT_SEVERITY = "warning"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _new_id(prefix: str) -> str:
    return "%s-%s-%s" % (
        prefix,
        datetime.now().strftime("%Y%m%d%H%M%S"),
        uuid.uuid4().hex[:6],
    )


def _load(project_id: str) -> Dict[str, Any]:
    project = workspace.load_project(str(project_id))
    if not isinstance(project.get("feedback"), list):
        project["feedback"] = []
    if not isinstance(project.get("corrected_specs"), list):
        project["corrected_specs"] = []
    return project


def _save(project: Dict[str, Any]) -> None:
    workspace.save_project(project)


def _normalize_category(category: Any) -> str:
    text = str(category or "").strip()
    if not text:
        return "其他"
    for known in CATEGORIES:
        if text == known:
            return known
    return text


def _normalize_severity(severity: Any) -> str:
    text = str(severity or "").strip().lower()
    return text if text in SEVERITIES else DEFAULT_SEVERITY


def _normalize_target(target: Any) -> Dict[str, Any]:
    if isinstance(target, dict):
        return dict(target)
    if isinstance(target, str) and target.strip():
        return {"kind": "segment", "segment": target.strip()}
    return {"kind": "program"}


def add(
    project_id: str,
    target: Any = None,
    category: str = "其他",
    severity: str = "warning",
    message: str = "",
    expected: str = "",
) -> Dict[str, Any]:
    """新增一条反馈记录并落盘（返回带 id/created_at/applied 的记录）。"""
    project = _load(project_id)
    record: Dict[str, Any] = {
        "id": _new_id("f"),
        "created_at": _now(),
        "target": _normalize_target(target),
        "category": _normalize_category(category),
        "severity": _normalize_severity(severity),
        "message": str(message or "").strip(),
        "expected": str(expected or "").strip(),
        "applied": False,
    }
    project["feedback"].append(record)
    _save(project)
    return record


def list_records(project_id: str) -> List[Dict[str, Any]]:
    """按写入顺序返回该项目全部反馈记录。"""
    project = _load(project_id)
    return [dict(record) for record in project.get("feedback") or [] if isinstance(record, dict)]


def lessons(project_id: str) -> List[Dict[str, Any]]:
    """按 category 聚合反馈：``[{category, count, last_message, examples}]``。

    - 按出现次数降序、category 升序排序（保证稳定）；
    - ``last_message`` 取该类别最近一条反馈说明；
    - ``examples`` 取最近的说明/期望（去重，最多 5 条）。
    """
    records = list_records(project_id)
    groups: Dict[str, Dict[str, Any]] = {}
    for record in records:
        category = _normalize_category(record.get("category"))
        group = groups.setdefault(category, {
            "category": category,
            "count": 0,
            "last_message": "",
            "examples": [],
            "_last_created": "",
            "_ordered": [],
        })
        group["count"] += 1
        message = str(record.get("message") or "").strip()
        expected = str(record.get("expected") or "").strip()
        created = str(record.get("created_at") or "")
        if created >= group["_last_created"]:
            group["_last_created"] = created
            if message:
                group["last_message"] = message
        if message and expected:
            detail = "%s（期望：%s）" % (message, expected)
        else:
            detail = message or expected
        if detail:
            group["_ordered"].append(detail)

    out: List[Dict[str, Any]] = []
    for group in groups.values():
        examples: List[str] = []
        for detail in reversed(group["_ordered"]):
            if detail not in examples:
                examples.append(detail)
            if len(examples) >= 5:
                break
        out.append({
            "category": group["category"],
            "count": group["count"],
            "last_message": group["last_message"],
            "examples": examples,
        })
    out.sort(key=lambda item: (-int(item["count"]), str(item["category"])))
    return out


def save_correction(project_id: str, spec: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    """保存用户修正后的 spec（下一轮作为 LLM few-shot / 模板候选）。"""
    project = _load(project_id)
    record: Dict[str, Any] = {
        "id": _new_id("c"),
        "created_at": _now(),
        "note": str(note or "").strip(),
        "spec": spec,
    }
    project["corrected_specs"].append(record)
    _save(project)
    return record


def corrections(project_id: str, limit: int = 3) -> List[Dict[str, Any]]:
    """最近的修正样例（新 → 旧，最多 limit 条）。"""
    project = _load(project_id)
    records = [record for record in project.get("corrected_specs") or [] if isinstance(record, dict)]
    records = sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    if limit is not None and limit >= 0:
        records = records[: int(limit)]
    return [dict(record) for record in records]
