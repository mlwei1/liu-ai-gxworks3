"""gx3gen 本地 Web API（仅标准库）。

路由与返回结构遵循 docs/INTERFACES.md 的 HTTP API 段：

    GET  /api/state                          -> {config(无 key), projects, styles}
    POST /api/projects                       {name}
    GET  /api/projects/<id>
    PUT  /api/projects/<id>                  {requirement?, io?, io_csv?, spec?, style_profile?}
    POST /api/plan                           {project_id, use_llm?} -> {spec, checklist, prompt_hint}
    POST /api/generate                       {project_id} -> {outputs, stats, summary, ...}
    GET  /api/projects/<id>/artifact/<name>  -> 文件下载
    POST /api/feedback                       {project_id, target, category, severity, message}
    GET  /api/feedback?project_id=<id>
    POST /api/optimize                       {project_id} -> {spec, checklist, prompt_hint}
    POST /api/style/import                   {name, files:[{filename, base64}]} -> profile
    GET  /api/styles ; GET /api/styles/<id>
    GET/POST /api/config                     LLM 配置（GET 去掉 api_key）

设计约定：
- 业务模块（workspace/config/spec/generator/planner/feedback/style）一律在函数内
  局部 import；模块缺失或未实现时返回 ``{"error": ...}`` 的 500，而不是让服务崩溃。
- 所有非 2xx 响应体统一为 ``{"error": "中文说明"}``。
- 本项目只在本机运行，不提供 CORS / 鉴权。
"""
from __future__ import annotations

import base64
import copy
import importlib
import inspect
import json
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

try:  # 版本号仅用于 /api/health、/api/state 展示
    from .. import __version__ as VERSION
except Exception:  # pragma: no cover - 包外单独加载时的兜底
    VERSION = "0.1.0"

Response = Tuple[int, Dict[str, str], bytes]
Payload = Dict[str, Any]

# 生成器产物：键 -> (默认文件名, 界面中文标签)
ARTIFACT_SPECS = (
    ("program_csv", "程序本体.csv", "程序本体 CSV"),
    ("comment_csv", "软元件注释.csv", "软元件注释 CSV"),
    ("network_table", "网络表.md", "网络表"),
    ("report", "生成报告.md", "生成报告"),
)

FEEDBACK_CATEGORIES = ("结构", "命名", "逻辑", "互锁", "报警", "停止", "其他")
FEEDBACK_SEVERITIES = ("error", "warning", "suggestion")
TARGET_KINDS = ("segment", "network", "program")

CONTENT_TYPES = {
    ".csv": "text/csv; charset=utf-16",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".html": "text/html; charset=utf-8",
}

# 兼容契约中文名与生成器实际文件名（program.csv 等）两种产物命名
ARTIFACT_ALIASES = {
    "程序本体.csv": "program.csv",
    "program.csv": "程序本体.csv",
    "软元件注释.csv": "comments.csv",
    "comments.csv": "软元件注释.csv",
    "网络表.md": "network_table.md",
    "network_table.md": "网络表.md",
    "生成报告.md": "report.md",
    "report.md": "生成报告.md",
}


class ApiError(Exception):
    """带 HTTP 状态码的 API 错误（extra 会并入错误响应，如 quality 详情）。"""

    def __init__(self, status: int, message: str, extra: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra if isinstance(extra, dict) else {}


# workspace 的 JSON 文件是"整文件覆盖写"。本服务是多线程的，必须把 API 读写串行化，
# 否则并发读可能看到被截断的 project.json（浏览器会同时发多个请求）。
_API_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# 响应工具
# ---------------------------------------------------------------------------
def _json(status: int, payload: Any) -> Response:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
    }, body


# ---------------------------------------------------------------------------
# 延迟加载业务模块
# ---------------------------------------------------------------------------
def _load(module_name: str):
    """局部 import 业务模块；缺失时转换成可展示的 ApiError。"""
    try:
        return importlib.import_module("gx3gen." + module_name)
    except Exception as exc:  # ImportError / 模块内部异常
        raise ApiError(500, "核心模块 gx3gen.%s 不可用：%s" % (module_name, exc))


def _call(module_name: str, attr: str, *args: Any, **kwargs: Any) -> Any:
    target = getattr(_load(module_name), attr, None)
    if not callable(target):
        raise ApiError(500, "核心模块尚未就绪：gx3gen.%s.%s 未实现" % (module_name, attr))
    return target(*args, **kwargs)


def _load_project(project_id: str) -> Payload:
    try:
        project = _call("workspace", "load_project", project_id)
    except FileNotFoundError:
        raise ApiError(404, "项目不存在：%s" % project_id)
    if not isinstance(project, dict):
        raise ApiError(500, "项目数据损坏：%s" % project_id)
    return project


def _save_project(project: Payload) -> None:
    _call("workspace", "save_project", project)


def _load_config() -> Payload:
    cfg = _call("config", "load_config")
    return cfg if isinstance(cfg, dict) else {}


def _public_config(cfg: Payload) -> Payload:
    """GET 用配置：去掉 api_key（界面无需回显密钥）。"""
    out = copy.deepcopy(cfg or {})
    llm = out.get("llm")
    if isinstance(llm, dict):
        llm.pop("api_key", None)
    return out


def _output_basename(value: Any) -> str:
    try:
        return Path(str(value)).name
    except (OSError, ValueError):
        return str(value)


def _public_project(project: Payload) -> Payload:
    """返回给前端的项目视图：产物统一成文件名（CLI 写入的可能是绝对路径）。"""
    data = copy.deepcopy(project or {})
    outputs = data.get("outputs")
    if isinstance(outputs, dict):
        data["outputs"] = {
            str(key): _output_basename(value)
            for key, value in outputs.items() if value
        }
    return data


def _lint_items(value: Any) -> List[str]:
    """把生成器/Lint 的告警（dict 或 str）统一成可展示文本。"""
    items: List[str] = []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    for item in value:
        if isinstance(item, dict):
            severity = item.get("severity") or item.get("level") or "info"
            message = item.get("message") or item.get("text") or ""
            code = item.get("code")
            text = "[%s] %s" % (severity, message)
            if code:
                text += "（%s）" % code
            items.append(text)
        else:
            text = str(item)
            if text.strip():
                items.append(text)
    return items


def _validate_spec(spec_data: Any, label: str, status: int) -> None:
    try:
        _call("spec", "validate_spec", spec_data)
    except Exception as exc:
        if exc.__class__.__name__ == "SpecError" or isinstance(exc, ValueError):
            raise ApiError(status, "%s不合法：\n%s" % (label, exc))
        raise


def _parse_io_text(text: Any) -> List[Payload]:
    """解析 IO 表文本（兼容 Tab 分隔的 GX 顺序表）。"""
    content = "" if text is None else str(text)
    try:
        rows = _call("spec", "parse_io_csv", content)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(400, "IO 表解析失败：%s" % exc)
    if not rows and "\t" in content:
        rows = _call("spec", "parse_io_csv", content.replace("\t", ","))
    return list(rows or [])


def _normalize_io_items(items: List[Any]) -> List[Payload]:
    spec_mod = _load("spec")
    device_re = getattr(spec_mod, "IO_DEVICE_RE", None)
    out: List[Payload] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ApiError(400, "io[%d] 必须是对象" % index)
        device = str(item.get("device") or "").strip()
        if not device or (device_re is not None and not device_re.match(device)):
            raise ApiError(400, "io[%d].device 不是合法软元件：%r" % (index, device))
        comment = str(item.get("comment") or "").strip()
        if not comment:
            raise ApiError(400, "io[%d].comment 不能为空" % index)
        row: Payload = {"device": device.upper(), "comment": comment}
        if item.get("role") in ("input", "output", "inout", "internal"):
            row["role"] = item["role"]
        if item.get("type"):
            row["type"] = str(item["type"])
        out.append(row)
    return out


def _require_project_id(payload: Payload) -> str:
    project_id = payload.get("project_id") or payload.get("id")
    if isinstance(project_id, (int, float)):
        project_id = str(project_id)
    if not isinstance(project_id, str) or not project_id.strip():
        raise ApiError(400, "缺少 project_id")
    return project_id.strip()


def _require_method(method: str, allowed: str) -> None:
    allowed_list = [item.strip() for item in allowed.split(",") if item.strip()]
    if method not in allowed_list:
        raise ApiError(405, "接口只支持 %s 请求（当前 %s）" % ("/".join(allowed_list), method))
    return None


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def dispatch(method: str, segments: List[str], query: Dict[str, str],
             body: bytes) -> Response:
    """把一次 HTTP 请求分发到具体处理函数，返回 (status, headers, body)。"""
    if method == "HEAD":
        method = "GET"
    try:
        with _API_LOCK:
            payload = _decode_payload(body)
            return _route(method, segments, query, payload)
    except ApiError as exc:
        payload = {"error": exc.message}
        payload.update(getattr(exc, "extra", {}) or {})
        return _json(exc.status, payload)
    except Exception as exc:  # 兜底：业务模块抛出的未知异常也不能让服务崩
        traceback.print_exc()
        return _json(500, {"error": "服务器内部错误：%s" % exc})


def _decode_payload(body: bytes) -> Payload:
    if not body:
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ApiError(400, "请求体不是合法 JSON：%s" % exc)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ApiError(400, "请求体必须是 JSON 对象")
    return data


def _route(method: str, segments: List[str], query: Dict[str, str],
           payload: Payload) -> Response:
    parts = list(segments)
    if parts and parts[0] == "api":
        parts = parts[1:]
    if not parts:
        raise ApiError(404, "未知接口：/api/")

    head = parts[0]
    rest = parts[1:]

    if head == "health" and not rest:
        return _json(200, {"ok": True, "version": VERSION, "name": "gx3gen"})

    if head == "state" and not rest:
        _require_method(method, "GET")
        return _json(200, _state())

    if head == "config" and not rest:
        if method == "GET":
            cfg = _public_config(_load_config())
            return _json(200, {"config": cfg, "llm": cfg.get("llm") or {}})
        if method == "POST":
            return _save_config(payload)
        _require_method(method, "GET, POST")

    if head == "projects":
        return _projects_route(method, rest, payload)

    if head == "plan" and not rest:
        _require_method(method, "POST")
        return _plan(payload)

    if head == "generate" and not rest:
        _require_method(method, "POST")
        return _generate(payload)

    if head == "optimize" and not rest:
        _require_method(method, "POST")
        return _optimize(payload)

    if head == "feedback" and not rest:
        if method == "GET":
            return _list_feedback(query)
        if method == "POST":
            return _add_feedback(payload)
        _require_method(method, "GET, POST")

    if head == "styles":
        if not rest:
            _require_method(method, "GET")
            return _json(200, {"styles": _call("workspace", "list_styles") or []})
        _require_method(method, "GET")
        return _style_detail(rest[0])

    if head == "style" and len(rest) == 1 and rest[0] == "import":
        _require_method(method, "POST")
        return _import_style(payload)

    if head == "io" and len(rest) == 1 and rest[0] == "parse":
        _require_method(method, "POST")
        return _parse_io(payload)

    if head == "spec" and len(rest) == 1 and rest[0] == "validate":
        _require_method(method, "POST")
        return _validate_spec_endpoint(payload)

    raise ApiError(404, "未知接口：/api/%s" % "/".join(parts))


# ---------------------------------------------------------------------------
# /api/state、/api/config
# ---------------------------------------------------------------------------
def _state() -> Payload:
    cfg = _public_config(_load_config())
    return {
        "config": cfg,
        "llm": cfg.get("llm") or {},
        "projects": _call("workspace", "list_projects") or [],
        "styles": _call("workspace", "list_styles") or [],
        "version": VERSION,
    }


def _save_config(payload: Payload) -> Response:
    body = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    if not isinstance(body, dict):
        raise ApiError(400, "配置必须是 JSON 对象")
    cfg = _load_config()
    llm = cfg.setdefault("llm", {})
    if not isinstance(llm, dict):
        llm = cfg["llm"] = {}
    incoming = body.get("llm") if isinstance(body.get("llm"), dict) else body
    if isinstance(incoming, dict):
        for key in ("base_url", "model"):
            if key in incoming:
                llm[key] = str(incoming.get(key) or "").strip()
        if "api_key" in incoming:
            value = str(incoming.get("api_key") or "").strip()
            if value:  # 留空表示不修改已保存的密钥
                llm["api_key"] = value
        if body.get("clear_api_key"):
            llm["api_key"] = ""
        for key in ("timeout", "temperature"):
            if key not in incoming:
                continue
            try:
                llm[key] = float(incoming[key]) if key == "temperature" else int(float(incoming[key]))
            except (TypeError, ValueError):
                raise ApiError(400, "llm.%s 必须是数字" % key)
    ui_in = body.get("ui")
    if isinstance(ui_in, dict):
        ui = cfg.setdefault("ui", {})
        if "host" in ui_in:
            ui["host"] = str(ui_in.get("host") or "127.0.0.1")
        if "port" in ui_in:
            try:
                ui["port"] = int(ui_in["port"])
            except (TypeError, ValueError):
                raise ApiError(400, "ui.port 必须是整数")
    _call("config", "save_config", cfg)
    public = _public_config(cfg)
    return _json(200, {"config": public, "llm": public.get("llm") or {}})


# ---------------------------------------------------------------------------
# /api/projects
# ---------------------------------------------------------------------------
def _projects_route(method: str, rest: List[str], payload: Payload) -> Response:
    if not rest:
        if method == "GET":
            return _json(200, {"projects": _call("workspace", "list_projects") or []})
        if method == "POST":
            return _create_project(payload)
        _require_method(method, "GET, POST")

    project_id = rest[0]
    if len(rest) == 1:
        if method == "GET":
            return _json(200, {"project": _public_project(_load_project(project_id))})
        if method == "PUT":
            return _update_project(project_id, payload)
        _require_method(method, "GET, PUT")

    if len(rest) == 3 and rest[1] == "artifact":
        _require_method(method, "GET")
        return _artifact(project_id, rest[2])

    raise ApiError(404, "未知接口：/api/projects/%s" % "/".join(rest))


def _create_project(payload: Payload) -> Response:
    name = str(payload.get("name") or payload.get("project_name") or "").strip()
    if not name:
        raise ApiError(400, "项目名称不能为空")
    requirement = str(payload.get("requirement") or "")
    project = _call("workspace", "create_project", name, requirement)
    if not isinstance(project, dict):
        raise ApiError(500, "创建项目失败：workspace.create_project 返回异常")
    return _json(200, {"project": _public_project(project),
                       "projects": _call("workspace", "list_projects") or []})


def _update_project(project_id: str, payload: Payload) -> Response:
    project = _load_project(project_id)

    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ApiError(400, "项目名称不能为空")
        project["name"] = name

    if "requirement" in payload:
        project["requirement"] = str(payload.get("requirement") or "")

    if "io_csv" in payload and "io" not in payload:
        project["io"] = _parse_io_text(payload.get("io_csv"))
    if "io" in payload:
        value = payload.get("io")
        if isinstance(value, str):
            project["io"] = _parse_io_text(value)
        elif isinstance(value, list):
            project["io"] = _normalize_io_items(value)
        elif value is None:
            project["io"] = []
        else:
            raise ApiError(400, "io 必须是数组或 CSV 文本")

    if "style_profile" in payload:
        value = payload.get("style_profile")
        profile: Optional[Payload] = None
        if value in (None, "", False):
            profile = None
        elif isinstance(value, str):
            profile = _call("workspace", "load_style", value)
            if not isinstance(profile, dict):
                raise ApiError(404, "风格不存在：%s" % value)
        elif isinstance(value, dict):
            profile = value
        else:
            raise ApiError(400, "style_profile 必须是风格 id、画像对象或 null")
        project["style_profile"] = profile
        spec_data = project.get("spec")
        if isinstance(spec_data, dict):
            spec_project = spec_data.get("project")
            if isinstance(spec_project, dict):
                spec_project["style_profile"] = (profile or {}).get("id") if profile else None

    if "spec" in payload:
        value = payload.get("spec")
        if value is None:
            project["spec"] = None
        else:
            if not isinstance(value, dict):
                raise ApiError(400, "spec 必须是 JSON 对象")
            _validate_spec(value, "ProgramSpec", 400)
            project["spec"] = value
            profile = project.get("style_profile")
            if isinstance(profile, dict) and profile.get("id"):
                value.setdefault("project", {})
                if isinstance(value["project"], dict):
                    value["project"]["style_profile"] = profile.get("id")

    _save_project(project)
    return _json(200, {"project": _public_project(project),
                       "projects": _call("workspace", "list_projects") or []})


# ---------------------------------------------------------------------------
# 产物下载
# ---------------------------------------------------------------------------
def _artifact_url(project_id: str, name: str) -> str:
    return "/api/projects/%s/artifact/%s" % (quote(project_id, safe=""), quote(name, safe=""))


def _artifacts(project_id: str, outputs: Payload) -> List[Payload]:
    items: List[Payload] = []
    for key, default_name, label in ARTIFACT_SPECS:
        name = outputs.get(key) if isinstance(outputs, dict) else None
        if not name:
            continue
        items.append({
            "key": key,
            "name": str(name),
            "label": label or default_name,
            "url": _artifact_url(project_id, str(name)),
        })
    return items


def _artifact(project_id: str, name: str) -> Response:
    _load_project(project_id)  # 404：项目不存在
    if not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name:
        raise ApiError(400, "非法的产物文件名：%s" % name)
    workspace = _load("workspace")
    try:
        out_dir = (Path(workspace.project_dir(project_id)) / "out").resolve()
        candidate = (out_dir / name).resolve()
    except OSError as exc:
        raise ApiError(500, "无法定位产物目录：%s" % exc)
    if candidate.parent != out_dir:
        raise ApiError(400, "非法的产物路径：%s" % name)
    if not candidate.is_file():
        # 兼容契约中文名与生成器实际文件名
        alias = ARTIFACT_ALIASES.get(name)
        if alias:
            alias_candidate = (out_dir / alias).resolve()
            if alias_candidate.parent == out_dir and alias_candidate.is_file():
                candidate = alias_candidate
    if not candidate.is_file():
        raise ApiError(404, "产物不存在：%s（请先在“生成”步骤生成程序）" % name)
    try:
        data = candidate.read_bytes()
    except OSError as exc:
        raise ApiError(500, "读取产物失败：%s" % exc)
    suffix = candidate.suffix.lower()
    content_type = CONTENT_TYPES.get(suffix, "application/octet-stream")
    ascii_base = name.encode("ascii", "ignore").decode("ascii").strip()
    if not ascii_base or not any(ch.isalnum() for ch in ascii_base):
        ascii_name = "artifact" + suffix
    else:
        ascii_name = ascii_base
    disposition = "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
        ascii_name.replace('"', "_"), quote(name, safe=""))
    return 200, {
        "Content-Type": content_type,
        "Content-Disposition": disposition,
        "Cache-Control": "no-store",
    }, data


# ---------------------------------------------------------------------------
# 规划 / 生成 / 优化
# ---------------------------------------------------------------------------
def _refresh_feedback(project: Payload) -> None:
    """把 store 中的反馈/经验同步进 project（供 planner/optimize 使用）。"""
    try:
        store = _load("feedback.store")
    except ApiError:
        return
    project_id = project.get("id")
    if not project_id:
        return
    try:
        list_records = getattr(store, "list_records", None)
        if callable(list_records):
            project["feedback"] = list(list_records(project_id) or [])
        lessons_fn = getattr(store, "lessons", None)
        if callable(lessons_fn):
            project["lessons"] = list(lessons_fn(project_id) or [])
    except Exception:
        traceback.print_exc()


def _feedback_hints(project: Payload) -> Tuple[List[str], str]:
    """返回 (checklist, prompt_hint)；feedback.optimize 未就绪时用 lessons 兜底。"""
    try:
        optimize = _load("feedback.optimize")
        fn = getattr(optimize, "apply_lessons", None)
        if callable(fn):
            result = fn(project)
            if isinstance(result, dict):
                checklist = [str(item) for item in (result.get("checklist") or [])]
                return checklist, str(result.get("prompt_hint") or "")
    except Exception:
        pass
    checklist: List[str] = []
    for item in project.get("lessons") or []:
        if isinstance(item, dict):
            checklist.append("[%s] %s（%s 次）" % (
                item.get("category") or "经验",
                item.get("last_message") or "",
                item.get("count") or 0))
        else:
            checklist.append(str(item))
    return checklist, ""


def _segment_summary(spec_data: Payload) -> List[Payload]:
    items: List[Payload] = []
    for index, segment in enumerate(spec_data.get("segments") or []):
        if not isinstance(segment, dict):
            continue
        kind = str(segment.get("kind") or "")
        item: Payload = {
            "index": index,
            "title": str(segment.get("title") or ("段 %d" % (index + 1))),
            "kind": kind,
        }
        if kind == "station":
            item["steps"] = len(segment.get("steps") or [])
        elif kind == "rungs":
            item["networks"] = len(segment.get("rungs") or [])
        elif kind == "statement":
            item["statements"] = len(segment.get("statements") or [])
        if isinstance(segment.get("step_range"), list) and len(segment["step_range"]) == 2:
            item["step_range"] = segment["step_range"]
        items.append(item)
    return items


def _plan(payload: Payload) -> Response:
    project_id = _require_project_id(payload)
    project = _load_project(project_id)
    use_llm = bool(payload.get("use_llm"))
    cfg = _load_config()
    _refresh_feedback(project)

    planner = _load("planner")
    plan_fn = getattr(planner, "plan", None)
    plan_quality_fn = getattr(planner, "plan_with_quality", None)
    planner_error = getattr(planner, "PlannerError", None)
    if not callable(plan_fn):
        # 兼容实现放在子模块 gx3gen.planner.plan 的布局
        try:
            plan_module = importlib.import_module("gx3gen.planner.plan")
        except Exception:
            plan_module = None
        if plan_module is not None:
            plan_fn = getattr(plan_module, "plan", None)
            plan_quality_fn = getattr(plan_module, "plan_with_quality", plan_quality_fn)
            planner_error = planner_error or getattr(plan_module, "PlannerError", None)
    if not callable(plan_fn):
        raise ApiError(500, "核心模块尚未就绪：gx3gen.planner.plan 未实现")
    planner_kind = "deterministic"
    quality: Payload = {}
    try:
        if callable(plan_quality_fn):
            planned = plan_quality_fn(project, cfg, use_llm)
            spec_data = planned.get("spec")
            quality = planned.get("quality") or {}
            planner_kind = str(planned.get("planner") or "deterministic")
        else:
            spec_data = plan_fn(project, cfg, use_llm)
            quality_fn = getattr(_load("quality"), "check_quality", None)
            if callable(quality_fn):
                try:
                    quality = quality_fn(spec_data, str(project.get("requirement") or ""))
                except Exception:
                    quality = {}
    except Exception as exc:
        traceback.print_exc()
        if planner_error is not None and isinstance(exc, planner_error):
            raise ApiError(400, "规划失败：%s" % exc)
        raise ApiError(500, "规划失败：%s" % exc)
    if not isinstance(spec_data, dict):
        raise ApiError(500, "规划器返回的不是 JSON 对象")

    profile = project.get("style_profile")
    if isinstance(profile, dict) and profile.get("id"):
        spec_project = spec_data.setdefault("project", {})
        if isinstance(spec_project, dict):
            spec_project["style_profile"] = profile.get("id")

    _validate_spec(spec_data, "规划结果", 500)
    project["spec"] = spec_data
    if quality:
        project["quality"] = quality
    project["planner"] = planner_kind
    _save_project(project)
    checklist, prompt_hint = _feedback_hints(project)
    return _json(200, {
        "spec": spec_data,
        "quality": quality,
        "planner": planner_kind,
        "checklist": checklist,
        "prompt_hint": prompt_hint,
        "assumptions": spec_data.get("assumptions") or [],
        "project": _public_project(project),
    })


def _generate(payload: Payload) -> Response:
    project_id = _require_project_id(payload)
    project = _load_project(project_id)
    spec_data = project.get("spec")
    if not isinstance(spec_data, dict):
        raise ApiError(400, "项目还没有 ProgramSpec，请先完成“架构确认”（规则解析 / AI 解析）")
    _validate_spec(spec_data, "ProgramSpec", 400)

    style = project.get("style_profile")
    if isinstance(style, str):
        style = _call("workspace", "load_style", style)
    workspace = _load("workspace")
    out_dir = Path(workspace.project_dir(project_id)) / "out"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ApiError(500, "无法创建产物目录：%s" % exc)

    generator = _load("generator")
    generate_fn = getattr(generator, "generate", None)
    if not callable(generate_fn):
        raise ApiError(500, "核心模块尚未就绪：gx3gen.generator.generate 未实现")
    project_name = str(project.get("name") or "未命名项目")
    quality_module = _load("quality")
    quality_error = getattr(quality_module, "QualityError", None)
    requirement = str(project.get("requirement") or "")
    force = bool(payload.get("force"))
    try:
        result = generate_fn(spec_data, style, str(out_dir), project_name,
                             requirement=requirement, force=force)
    except Exception as exc:
        traceback.print_exc()
        if quality_error is not None and isinstance(exc, quality_error):
            raise ApiError(400, str(exc), extra={"quality": getattr(exc, "quality", {})})
        if isinstance(exc, ValueError):
            raise ApiError(400, "生成失败：%s" % exc)
        raise ApiError(500, "生成失败：%s" % exc)
    if not isinstance(result, dict):
        raise ApiError(500, "生成器返回格式不正确（应为 dict）")

    outputs: Payload = {}
    for key, _default_name, _label in ARTIFACT_SPECS:
        value = result.get(key)
        if value:
            outputs[key] = Path(str(value)).name
    stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
    summary = str(result.get("summary") or "")
    lint = _lint_items(
        result.get("lint") or result.get("warnings")
        or stats.get("lint") or stats.get("warnings") or [])
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}

    project["outputs"] = outputs
    project["stats"] = stats
    project["summary"] = summary
    project["lint"] = lint
    project["quality"] = quality
    _save_project(project)

    return _json(200, {
        "outputs": outputs,
        "stats": stats,
        "summary": summary,
        "lint": project["lint"],
        "quality": quality,
        "artifacts": _artifacts(project_id, outputs),
        "segments": _segment_summary(spec_data),
        "assumptions": spec_data.get("assumptions") or [],
        "project": _public_project(project),
    })


def _optimize(payload: Payload) -> Response:
    project_id = _require_project_id(payload)
    project = _load_project(project_id)
    if not isinstance(project.get("spec"), dict):
        raise ApiError(400, "项目还没有确认的 ProgramSpec，请先在“架构确认”中生成并保存")
    _refresh_feedback(project)

    optimize = _load("feedback.optimize")
    fn = getattr(optimize, "apply_lessons", None)
    if not callable(fn):
        raise ApiError(500, "核心模块尚未就绪：gx3gen.feedback.optimize.apply_lessons 未实现")
    feedback_error = getattr(optimize, "FeedbackError", None)
    try:
        result = fn(project)
    except Exception as exc:
        traceback.print_exc()
        if feedback_error is not None and isinstance(exc, feedback_error):
            raise ApiError(400, "应用反馈经验失败：%s" % exc)
        raise ApiError(500, "应用反馈经验失败：%s" % exc)
    if not isinstance(result, dict):
        raise ApiError(500, "反馈优化返回格式不正确（应为 dict）")

    spec_data = result.get("spec") or project.get("spec")
    if not isinstance(spec_data, dict):
        raise ApiError(500, "反馈优化返回的 spec 不合法")
    _validate_spec(spec_data, "反馈优化后的 ProgramSpec", 500)
    checklist = [str(item) for item in (result.get("checklist") or [])]
    prompt_hint = str(result.get("prompt_hint") or "")

    project["spec"] = spec_data
    project["checklist"] = checklist
    project["prompt_hint"] = prompt_hint
    _save_project(project)
    return _json(200, {
        "spec": spec_data,
        "checklist": checklist,
        "prompt_hint": prompt_hint,
        "project": _public_project(project),
    })


# ---------------------------------------------------------------------------
# /api/feedback
# ---------------------------------------------------------------------------
def _add_feedback(payload: Payload) -> Response:
    project_id = _require_project_id(payload)
    _load_project(project_id)
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ApiError(400, "反馈说明不能为空")
    expected = str(payload.get("expected") or "").strip()
    category = str(payload.get("category") or "其他").strip() or "其他"
    severity = str(payload.get("severity") or "suggestion").strip().lower()
    if severity not in FEEDBACK_SEVERITIES:
        severity = "suggestion"
    target = payload.get("target")
    if not isinstance(target, dict):
        target = {"kind": "program"}
    else:
        if target.get("kind") not in TARGET_KINDS:
            target = dict(target)
            target["kind"] = "segment" if target.get("segment") else "program"

    store = _load("feedback.store")
    add_fn = getattr(store, "add", None)
    if not callable(add_fn):
        raise ApiError(500, "核心模块尚未就绪：gx3gen.feedback.store.add 未实现")
    try:
        parameters = inspect.signature(add_fn).parameters
    except (TypeError, ValueError):
        parameters = {}
    stored_message = message
    try:
        if expected and "expected" in parameters:
            record = add_fn(project_id, target, category, severity, message, expected=expected)
        else:
            if expected:  # 兼容不支持 expected 参数的实现
                stored_message = message + "\n期望做法：" + expected
            record = add_fn(project_id, target, category, severity, stored_message)
    except Exception as exc:
        traceback.print_exc()
        raise ApiError(500, "保存反馈失败：%s" % exc)

    records: List[Any] = []
    lessons: List[Any] = []
    try:
        list_fn = getattr(store, "list_records", None)
        if callable(list_fn):
            records = list(list_fn(project_id) or [])
        lessons_fn = getattr(store, "lessons", None)
        if callable(lessons_fn):
            lessons = list(lessons_fn(project_id) or [])
    except Exception:
        traceback.print_exc()
    try:
        project = _load_project(project_id)
        if records:
            project["feedback"] = records
        if lessons:
            project["lessons"] = lessons
        _save_project(project)
    except Exception:
        traceback.print_exc()

    if not isinstance(record, dict):
        record = {"project_id": project_id, "target": target, "category": category,
                  "severity": severity, "message": message}
    return _json(200, {"record": record, "feedback": records, "lessons": lessons})


def _list_feedback(query: Dict[str, str]) -> Response:
    project_id = (query.get("project_id") or query.get("id") or "").strip()
    if not project_id:
        raise ApiError(400, "缺少 project_id 查询参数")
    project = _load_project(project_id)
    records: List[Any] = []
    lessons: List[Any] = []
    try:
        store = _load("feedback.store")
        list_fn = getattr(store, "list_records", None)
        if callable(list_fn):
            records = list(list_fn(project_id) or [])
        lessons_fn = getattr(store, "lessons", None)
        if callable(lessons_fn):
            lessons = list(lessons_fn(project_id) or [])
    except ApiError:
        records = list(project.get("feedback") or [])
    except Exception:
        traceback.print_exc()
        records = list(project.get("feedback") or [])
    return _json(200, {"feedback": records, "lessons": lessons})


# ---------------------------------------------------------------------------
# /api/style/import、/api/styles
# ---------------------------------------------------------------------------
def _decode_upload(item: Payload, index: int) -> Tuple[str, str]:
    """把上传项转成 (文件名, 文本)；兼容 base64 与直接 text。"""
    filename = str(item.get("filename") or item.get("name") or ("文件%d.csv" % (index + 1)))
    if isinstance(item.get("text"), str):
        return filename, _call("spec", "decode_text", item["text"])
    raw_b64 = item.get("base64") or item.get("data") or ""
    if not isinstance(raw_b64, str) or not raw_b64.strip():
        raise ApiError(400, "文件 %s 缺少 base64 内容" % filename)
    cleaned = raw_b64.strip()
    if cleaned.startswith("data:"):  # 兼容 data URL
        comma = cleaned.find(",")
        if comma >= 0:
            cleaned = cleaned[comma + 1:]
    try:
        raw = base64.b64decode(cleaned)
    except Exception:
        raise ApiError(400, "文件 %s 的 base64 解码失败" % filename)
    if not raw:
        raise ApiError(400, "文件 %s 内容为空" % filename)
    text = _call("spec", "decode_text", raw)
    if not str(text).strip():
        raise ApiError(400, "文件 %s 解码后为空（请确认是 GX Works3 导出的 CSV）" % filename)
    return filename, text


def _looks_like_comment_csv(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and "软元件名" in lines[1]:
        return True
    return False


def _looks_like_program_csv(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines[:4]:
        if "步号" in line and "指令" in line:
            return True
    return False


def _classify_style_file(filename: str, text: str) -> str:
    """判断上传的是程序本体 CSV 还是软元件注释 CSV。"""
    is_program = _looks_like_program_csv(text)
    is_comment = _looks_like_comment_csv(text)
    if is_program and not is_comment:
        return "program"
    if is_comment and not is_program:
        return "comment"
    lower = filename.lower()
    if any(key in filename for key in ("注释",)) or any(key in lower for key in ("comment", "label")):
        return "comment"
    if any(key in filename for key in ("程序", "本体")) or any(
            key in lower for key in ("prog", "main", "pou")):
        return "program"
    return "program"


def _import_style(payload: Payload) -> Response:
    name = str(payload.get("name") or "").strip() or "用户风格"
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ApiError(400, "请至少上传一个 CSV 文件（程序本体 CSV / 软元件注释 CSV）")
    decoded: List[Tuple[str, str]] = []
    for index, item in enumerate(raw_files):
        if not isinstance(item, dict):
            raise ApiError(400, "files[%d] 必须是对象" % index)
        decoded.append(_decode_upload(item, index))
    if not decoded:
        raise ApiError(400, "没有可用的 CSV 文件")

    programs: List[Tuple[str, str]] = []
    comments: List[Tuple[str, str]] = []
    for filename, text in decoded:
        if _classify_style_file(filename, text) == "comment":
            comments.append((filename, text))
        else:
            programs.append((filename, text))
    if not programs:
        raise ApiError(400, "未识别到程序本体 CSV（需要包含“步号 / 指令”表头或文件名含“程序”）")

    mine = _load("style.miner")
    mine_fn = getattr(mine, "mine", None)
    if not callable(mine_fn):
        raise ApiError(500, "核心模块尚未就绪：gx3gen.style.miner.mine 未实现")
    profiles: List[Payload] = []
    for index, (filename, text) in enumerate(programs):
        comment_text = comments[index][1] if index < len(comments) else None
        try:
            profile = mine_fn(text, comment_text, name)
        except Exception as exc:
            traceback.print_exc()
            if isinstance(exc, ValueError):
                raise ApiError(400, "风格分析失败（%s）：%s" % (filename, exc))
            raise ApiError(500, "风格分析失败（%s）：%s" % (filename, exc))
        if not isinstance(profile, dict):
            raise ApiError(500, "风格分析返回格式不正确：%s" % filename)
        profiles.append(profile)

    profile: Payload
    if len(profiles) > 1:
        try:
            profile = _call("style.profile", "merge_profiles", profiles)
        except Exception:
            profile = profiles[0]
    else:
        profile = profiles[0]
    if not isinstance(profile, dict):
        raise ApiError(500, "风格画像格式不正确")
    profile["name"] = name
    profile["source_files"] = [filename for filename, _text in decoded]
    try:
        profile = _call("workspace", "save_style", profile)
    except Exception as exc:
        traceback.print_exc()
        raise ApiError(500, "保存风格失败：%s" % exc)
    return _json(200, profile)


def _style_detail(style_id: str) -> Response:
    profile = _call("workspace", "load_style", style_id)
    if not isinstance(profile, dict):
        raise ApiError(404, "风格不存在：%s" % style_id)
    return _json(200, {"style": profile})


# ---------------------------------------------------------------------------
# /api/io/parse、/api/spec/validate
# ---------------------------------------------------------------------------
def _parse_io(payload: Payload) -> Response:
    spec_mod = _load("spec")
    text: Optional[str] = None
    if isinstance(payload.get("text"), str):
        text = payload["text"]
    elif isinstance(payload.get("io_csv"), str):
        text = payload["io_csv"]
    elif isinstance(payload.get("files"), list):
        chunks: List[str] = []
        for index, item in enumerate(payload["files"]):
            if not isinstance(item, dict):
                continue
            _filename, content = _decode_upload(item, index)
            chunks.append(str(content))
        text = "\n".join(chunks)
    if text is None:
        raise ApiError(400, "缺少 IO 表内容（text 或 files）")
    decoded = spec_mod.decode_text(text) if hasattr(spec_mod, "decode_text") else str(text)
    io_rows = _parse_io_text(decoded)
    return _json(200, {"io": io_rows, "text": str(decoded), "count": len(io_rows)})


def _validate_spec_endpoint(payload: Payload) -> Response:
    spec_data = payload.get("spec")
    if not isinstance(spec_data, dict):
        raise ApiError(400, "缺少 spec 对象")
    try:
        _call("spec", "validate_spec", spec_data)
    except Exception as exc:
        if exc.__class__.__name__ == "SpecError" or isinstance(exc, ValueError):
            return _json(200, {"ok": False, "error": str(exc)})
        raise
    return _json(200, {"ok": True, "error": ""})
