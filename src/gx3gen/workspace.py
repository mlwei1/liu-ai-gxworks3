"""文件层：项目 / 风格库 / 产物 / 反馈的本地持久化。"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import workspace_root


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:80]


def new_id(prefix: str) -> str:
    return "%s-%s-%s" % (prefix, datetime.now().strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:6])


def projects_dir() -> Path:
    path = workspace_root() / "projects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def styles_dir() -> Path:
    path = workspace_root() / "styles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_dir(project_id: str) -> str:
    return str(projects_dir() / _safe_id(project_id))


def artifact_path(project_id: str, filename: str) -> str:
    safe = Path(filename).name  # 防目录穿越
    out = Path(project_dir(project_id)) / "out"
    out.mkdir(parents=True, exist_ok=True)
    return str(out / safe)


def _project_file(project_id: str) -> Path:
    return Path(project_dir(project_id)) / "project.json"


def list_projects() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in sorted(projects_dir().glob("*/project.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append({
            "id": data.get("id"),
            "name": data.get("name"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "has_spec": bool(data.get("spec")),
            "has_outputs": bool(data.get("outputs")),
            "style_profile": data.get("style_profile"),
            "feedback_count": len(data.get("feedback") or []),
        })
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return items


def create_project(name: str, requirement: str = "") -> Dict[str, Any]:
    name = (name or "").strip() or "未命名项目"
    project = {
        "id": new_id("p"),
        "name": name,
        "created_at": _now(),
        "updated_at": _now(),
        "requirement": requirement,
        "io": [],
        "spec": None,
        "style_profile": None,
        "outputs": {},
        "stats": {},
        "summary": "",
        "feedback": [],
        "corrected_specs": [],
        "lessons": [],
        "notes": [],
    }
    save_project(project)
    return project


def load_project(project_id: str) -> Dict[str, Any]:
    path = _project_file(project_id)
    if not path.exists():
        raise FileNotFoundError("项目不存在: %s" % project_id)
    return json.loads(path.read_text(encoding="utf-8"))


def save_project(project: Dict[str, Any]) -> None:
    if not project.get("id"):
        raise ValueError("project 缺少 id")
    project["updated_at"] = _now()
    path = _project_file(project["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 风格库
# ---------------------------------------------------------------------------
def list_styles() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in sorted(styles_dir().glob("*/profile.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append({
            "id": data.get("id"),
            "name": data.get("name"),
            "created_at": data.get("created_at"),
            "source_files": data.get("source_files") or [],
            "segment_count": len(data.get("segments") or []),
        })
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return items


def save_style(profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = dict(profile or {})
    if not profile.get("id"):
        profile["id"] = new_id("s")
    if not profile.get("created_at"):
        profile["created_at"] = _now()
    if not profile.get("name"):
        profile["name"] = "未命名风格"
    directory = styles_dir() / _safe_id(profile["id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def load_style(style_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not style_id:
        return None
    safe = _safe_id(style_id)
    path = styles_dir() / safe / "profile.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
