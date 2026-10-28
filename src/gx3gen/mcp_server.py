"""gx3gen MCP stdio server（agent-first 入口，零第三方依赖）。

协议：MCP 2024-11-05 + JSON-RPC 2.0，stdio 按行分隔（一行一条消息）。

约束（很重要）：

- **stdout 只允许协议 JSON**；所有日志/异常一律写 stderr；
- 工具实现全部使用函数内局部 import：``gx3gen.quality`` / ``gx3gen.domains`` 等模块缺失或尚未就绪时，
  工具返回 ``{ok: false, error/errors}``，server 不能崩溃；**MCP 不调用任何项目内 LLM，也不需要 API Key**；
- 每个工具返回 MCP 格式 ``{"content": [{"type": "text", "text": ...}], "isError": bool}``；
  除 ``gx3gen_get_generation_context`` 返回给 agent 阅读的 Markdown 外，其余工具
  text 内是 JSON 字符串（``ensure_ascii=False``）。

运行方式::

    python run_mcp.py                     # 仓库根启动器（推荐）
    python -m gx3gen.mcp_server           # 已把 src 放进 sys.path 时

客户端配置见仓库根 ``mcp.json.example`` / ``docs/AGENT_WORKFLOW.md``。
"""
from __future__ import annotations

import contextlib
import json
import re
import sys
import traceback
from pathlib import Path

SERVER_NAME = "gx3gen"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "samples"

# 供 initialize.instructions 指路（agent 打开后先读这些）
SERVER_INSTRUCTIONS = (
    "gx3gen 是 Mitsubishi FX5U / GX Works3 的 ProgramSpec → 程序初稿工具包。"
    "生成任何 PLC 程序前先调用 gx3gen_get_generation_context 读取 ProgramSpec 结构、"
    "架构默认、质量红线、GX 导入硬约束与示例；ProgramSpec 必须由你（AI agent）产出，不要让用户手写 JSON。"
    "流程：get_generation_context → 自己写 ProgramSpec → gx3gen_validate_spec → "
    "gx3gen_generate_program（结果含 audit）→ 需要时 gx3gen_audit_csv → "
    "向用户报告产物路径/质量/audit/assumptions。"
    "注意：SET/RST 只能 1 个操作数、LD/LDI 不能连续、一个 STL 配一个 RETSTL、注释 CSV 用 LF。"
    "同时阅读仓库根 AGENTS.md 与 skills/gx3gen-plc/SKILL.md。"
)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
class ToolError(Exception):
    """工具参数/前置条件错误（转成结构化 MCP 结果，不退出 server）。"""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.message = str(message)
        self.errors = [str(item) for item in (errors or [])]


def _log(message):
    """日志只写 stderr，绝不污染 stdout 协议。"""
    try:
        sys.stderr.write("[gx3gen-mcp] %s\n" % message)
        sys.stderr.flush()
    except Exception:  # pragma: no cover - stderr 不可用时静默
        pass


def _as_str(value, default=""):
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _truncate(text, limit):
    text = _as_str(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 30)].rstrip() + "\n…（已截断，完整内容见工具返回/仓库文件）"


def _text_result(text, is_error=False):
    return {"content": [{"type": "text", "text": _as_str(text)}], "isError": bool(is_error)}


def _json_result(payload, is_error=False):
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return _text_result(text, is_error=is_error)


def _ok(**fields):
    data = {"ok": True}
    data.update(fields)
    return _json_result(data)


def _fail(message, errors=None, **fields):
    data = {"ok": False, "error": _as_str(message)}
    clean_errors = [str(item) for item in (errors or []) if str(item).strip()]
    if clean_errors:
        data["errors"] = clean_errors
    data.update(fields)
    return _json_result(data, is_error=True)


def _exception_errors(exc):
    """从异常里尽量提取错误清单（QualityError/SpecError 都可能挂 errors/issues）。"""
    for attr in ("errors", "issues", "problems"):
        value = getattr(exc, attr, None)
        if isinstance(value, (list, tuple)) and value:
            out = []
            for item in value:
                if isinstance(item, dict):
                    out.append(_as_str(item.get("message") or item.get("code") or item))
                else:
                    out.append(_as_str(item))
            if out:
                return out
    text = _as_str(exc)
    lines = [line.strip()[2:].strip() for line in text.splitlines()
             if line.strip().startswith("- ")]
    return lines or ([text] if text.strip() else [type(exc).__name__])


def _read_text_file(path):
    """读取文本文件；GX 导出/CSV 可能是 UTF-16LE+BOM。"""
    raw = Path(path).read_bytes()
    try:
        from .spec import decode_text  # 局部 import：spec 是稳定模块

        return decode_text(raw)
    except Exception:
        return raw.decode("utf-8", "replace")


def _safe_name(name, default="mcp-project"):
    text = re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "_", _as_str(name, default)).strip("._")
    return (text or default)[:60]


def _load_json_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# quality 适配层（模块由其他任务并行实现，接口不完全确定）
# ---------------------------------------------------------------------------
def _empty_quality(note=""):
    return {
        "available": False,
        "ok": True,
        "score": None,
        "errors": [],
        "warnings": [],
        "suggestions": [],
        "issues": [],
        "summary": note or "quality 模块不可用，未执行红线检查",
    }


def _normalize_quality_result(result):
    """把 quality.check_quality 的任意返回形态归一成统一 dict。"""
    data = _empty_quality()
    data["available"] = True
    if isinstance(result, dict):
        issues = result.get("issues")
        if isinstance(issues, (list, tuple)):
            for item in issues:
                if isinstance(item, dict):
                    severity = _as_str(item.get("severity")).lower() or "warning"
                    message = _as_str(item.get("message") or item.get("code") or "")
                    entry = dict(item)
                    entry["severity"] = severity
                    entry["message"] = message
                    data["issues"].append(entry)
                    if severity == "error":
                        data["errors"].append(message)
                    elif severity == "suggestion":
                        data["suggestions"].append(message)
                    else:
                        data["warnings"].append(message)
                else:
                    data["warnings"].append(_as_str(item))
        for key in ("errors", "warnings", "suggestions"):
            value = result.get(key)
            if not isinstance(value, (list, tuple)):
                continue
            for item in value:
                message = _as_str(item.get("message") if isinstance(item, dict) else item)
                if message and message not in data[key]:
                    data[key].append(message)
        if "ok" in result:
            data["ok"] = bool(result.get("ok"))
        else:
            data["ok"] = not data["errors"]
        for key in ("score", "total_score", "grade"):
            if result.get(key) is not None:
                data["score"] = result.get(key)
                break
        data["summary"] = _as_str(result.get("summary") or result.get("message"))
        for key in ("checks", "metrics", "red_lines", "details", "level", "stats"):
            if key in result:
                data[key] = result[key]
        if not data["summary"]:
            data["summary"] = "质量检查：%d 错误 / %d 告警 / %d 建议" % (
                len(data["errors"]), len(data["warnings"]), len(data["suggestions"]))
        return data
    if isinstance(result, (list, tuple)):
        return _normalize_quality_result({"issues": list(result)})
    if isinstance(result, str):
        data["summary"] = result
        return data
    if isinstance(result, bool):
        data["ok"] = result
        return data
    data["summary"] = "质量检查返回了无法识别的结果：%r" % (result,)
    return data


def _call_quality_fn(fn, spec, requirement):
    """按签名调用 quality.check_quality（兼容 (spec) / (spec, requirement) 等写法）。"""
    try:
        import inspect

        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(spec)
    names = list(parameters)
    if len(names) <= 1:
        return fn(spec)
    if "requirement" in names or "req" in names or "user_requirement" in names:
        keyword = "requirement" if "requirement" in names else (
            "req" if "req" in names else "user_requirement")
        return fn(spec, **{keyword: requirement or ""})
    return fn(spec, requirement or "")


def _check_quality(spec, requirement=""):
    """有 quality 模块就检查，没有就返回 available=False 的安全结果。"""
    try:
        from . import quality as quality_mod  # type: ignore  # 并行开发模块
    except Exception as exc:
        data = _empty_quality("quality 模块不可用（%s），仅做 spec 结构校验" % type(exc).__name__)
        return data
    fn = getattr(quality_mod, "check_quality", None)
    if not callable(fn):
        return _empty_quality("quality.check_quality 未实现，仅做 spec 结构校验")
    try:
        result = _call_quality_fn(fn, spec, requirement)
    except Exception as exc:  # QualityError 或实现异常：都转成结构化结果
        errors = _exception_errors(exc)
        data = _normalize_quality_result({"issues": [
            {"severity": "error", "message": item} for item in errors]})
        data["error_type"] = type(exc).__name__
        data["summary"] = "质量检查未通过（%s）" % type(exc).__name__
        return data
    data = _normalize_quality_result(result)
    if data.get("error_type") is None:
        data.pop("error_type", None)
    return data


def _is_quality_error(exc):
    if "Quality" in type(exc).__name__:
        return True
    try:
        from .quality import QualityError  # type: ignore

        return isinstance(exc, QualityError)
    except Exception:
        return False


def _quality_module_checklist():
    """读取 quality 模块自带的检查清单（如果有），用于注入 agent 上下文。"""
    try:
        from . import quality as quality_mod  # type: ignore
    except Exception:
        return []
    for attr in ("QUALITY_CHECKLIST", "CHECKLIST", "RED_LINES", "QUALITY_RULES"):
        value = getattr(quality_mod, attr, None)
        if isinstance(value, (list, tuple)):
            return [_as_str(item) for item in value if _as_str(item).strip()][:20]
    for name in ("checklist", "quality_checklist", "red_lines"):
        fn = getattr(quality_mod, name, None)
        if not callable(fn):
            continue
        try:
            value = fn()
        except TypeError:
            try:
                value = fn(None)
            except Exception:
                continue
        except Exception:
            continue
        if isinstance(value, (list, tuple)):
            return [_as_str(item) for item in value if _as_str(item).strip()][:20]
    return []


# ---------------------------------------------------------------------------
# 风格 / 项目 适配层
# ---------------------------------------------------------------------------


def _load_style(style_id):
    if not style_id:
        return None
    try:
        from . import workspace

        return workspace.load_style(style_id)
    except Exception:
        return None




def _style_from_spec(spec):
    project = spec.get("project") if isinstance(spec, dict) else None
    if isinstance(project, dict):
        style_id = project.get("style_profile")
        if isinstance(style_id, str) and style_id.strip():
            return _load_style(style_id.strip())
    return None








# ---------------------------------------------------------------------------
# 上下文渲染（gx3gen_get_generation_context）
# ---------------------------------------------------------------------------
_LIGHT_INLINE_SPEC = {
    "spec_version": "0.1",
    "project": {"name": "双向红绿灯", "cpu": "FX5U", "gx_version": "1.082", "style_profile": None},
    "io": [
        {"device": "X0", "comment": "启动按钮", "role": "input"},
        {"device": "X1", "comment": "停止按钮", "role": "input"},
        {"device": "Y0", "comment": "东西红灯", "role": "output"},
        {"device": "Y1", "comment": "东西黄灯", "role": "output"},
        {"device": "Y2", "comment": "东西绿灯", "role": "output"},
        {"device": "Y3", "comment": "南北红灯", "role": "output"},
        {"device": "Y4", "comment": "南北黄灯", "role": "output"},
        {"device": "Y5", "comment": "南北绿灯", "role": "output"},
    ],
    "modes": {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"},
    "common": {
        "handshake_base": 1000, "alarm_base": 2000, "alarm_timer_base": 10,
        "start_step": 0, "station_step_base": 100, "station_step_stride": 100,
        "step_increment": 10, "auto_rungs": True,
    },
    "segments": [
        {
            "title": "初始化",
            "kind": "init",
            "rungs": [{
                "comment": "上电置待机步",
                "instructions": [{"op": "LD", "operands": ["SM402"]},
                                 {"op": "SET", "operands": ["S0"]}],
            }],
        },
        {
            "title": "红绿灯循环",
            "kind": "station",
            "step_range": [100, 199],
            "entry_conditions": ["S0", "X0"],
            "steps": [
                {"step": 100,
                 "actions": [{"set": ["Y2"], "reset": ["Y1", "Y0", "Y3", "Y4", "Y5"],
                              "comment": "东西绿灯 / 南北红灯"},
                             {"instructions": [{"op": "OUT", "operands": ["T0", "K100"]}],
                              "comment": "东西绿灯 10s"}],
                 "transition": {"conditions": ["T0"], "next": 110, "comment": "绿灯到 → 黄灯"}},
                {"step": 110,
                 "actions": [{"set": ["Y1"], "reset": ["Y2"], "comment": "东西黄灯"}],
                 "transition": {"conditions": ["T1"], "next": 120, "comment": "黄灯 3s"}},
                {"step": 120,
                 "actions": [{"set": ["Y0"], "reset": ["Y1"], "comment": "东西红灯：全红过渡"}],
                 "transition": {"conditions": ["T2"], "next": 130, "comment": "全红 1s"}},
                {"step": 130,
                 "actions": [{"set": ["Y5"], "reset": ["Y4", "Y2", "Y1", "Y0", "Y3"],
                              "comment": "南北绿灯 / 东西红灯"},
                             {"instructions": [{"op": "OUT", "operands": ["T3", "K100"]}],
                              "comment": "南北绿灯 10s"}],
                 "transition": {"conditions": ["T3"], "next": 140, "comment": "绿灯到 → 黄灯"}},
                {"step": 140,
                 "actions": [{"set": ["Y4"], "reset": ["Y5"], "comment": "南北黄灯"}],
                 "transition": {"conditions": ["T4"], "next": 150, "comment": "黄灯 3s"}},
                {"step": 150,
                 "actions": [{"set": ["Y0", "Y3"], "reset": ["Y4"], "comment": "全红过渡"}],
                 "transition": {"conditions": ["T5"], "next": 100, "comment": "全红 1s → 循环"}},
            ],
        },
        {
            "title": "启动停止",
            "kind": "common",
            "rungs": [
                {"comment": "启动 → 运行中 + 自动",
                 "instructions": [{"op": "LD", "operands": ["X0"]},
                                  {"op": "SET", "operands": ["M500"]},
                                  {"op": "SET", "operands": ["M521"]}]},
                {"comment": "停止 → 复位运行/自动与所有灯",
                 "instructions": [{"op": "LD", "operands": ["X1"]},
                                  {"op": "RST", "operands": ["M500"]},
                                  {"op": "RST", "operands": ["M521"]},
                                  {"op": "RST", "operands": ["Y0", "Y1", "Y2", "Y3", "Y4", "Y5"]}]},
            ],
        },
    ],
    "alarms": {"enabled": False, "base": 2000, "timer_base": 10, "cylinders": []},
    "assumptions": [
        "绿灯 10s / 黄灯 3s / 全红 1s 为建议值，按现场交规与验收要求调整",
        "停止时所有灯复位（安全状态），如需“全红等待”请修改停止段逻辑",
        "东西/南北相位互锁：任一方向绿灯时另一方向必须红灯",
    ],
}

_LIGHT_INLINE_REQUIREMENT = (
    "做一个双向红绿灯（东西向、南北向）：启动后自动循环，"
    "东西绿→东西黄→全红→南北绿→南北黄→全红→循环；"
    "绿灯 10s、黄灯 3s、全红 1s；任一方向绿灯时另一方向必须红灯；"
    "停止按钮复位所有灯；含初始化（SM402 置待机步）与启动/停止段。"
)


def _outline_spec(spec):
    """压缩表示 ProgramSpec 结构（给上下文省 token）。"""
    lines = []
    project = spec.get("project") or {}
    lines.append("project: name=%s cpu=%s" % (project.get("name"), project.get("cpu")))
    io_rows = spec.get("io") or []
    devices = ["%s(%s)" % (item.get("device"), item.get("comment"))
               for item in io_rows if isinstance(item, dict)][:16]
    lines.append("io(%d): %s" % (len(io_rows), "、".join(devices) + ("…" if len(io_rows) > 16 else "")))
    lines.append("modes: %s" % json.dumps(spec.get("modes") or {}, ensure_ascii=False))
    for segment in spec.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        kind = segment.get("kind")
        title = segment.get("title")
        if kind == "station":
            steps = segment.get("steps") or []
            lines.append("- station %s step_range=%s steps=%d entry=%s" % (
                title, segment.get("step_range"), len(steps),
                segment.get("entry_conditions") or []))
            for step in steps[:6]:
                if not isinstance(step, dict):
                    continue
                actions = []
                for action in step.get("actions") or []:
                    if not isinstance(action, dict):
                        continue
                    if action.get("set"):
                        actions.append("SET " + "、".join(_as_str(x) for x in action["set"]))
                    if action.get("reset"):
                        actions.append("RST " + "、".join(_as_str(x) for x in action["reset"]))
                    for rule in action.get("instructions") or []:
                        if isinstance(rule, dict):
                            actions.append("%s %s" % (rule.get("op"),
                                                      " ".join(_as_str(x) for x in rule.get("operands") or [])))
                transition = step.get("transition") or {}
                lines.append("    S%s: %s → next=%s cond=%s" % (
                    step.get("step"), "；".join(actions) if actions else "（无动作）",
                    transition.get("next"), transition.get("conditions") or []))
            if len(steps) > 6:
                lines.append("    …（其余 %d 步略）" % (len(steps) - 6))
        else:
            count = len(segment.get("rungs") or []) or len(segment.get("statements") or [])
            lines.append("- %s %s（%d 项）" % (kind, title, count))
    return "\n".join(lines)


def _traffic_example():
    """优先读 samples/traffic_light.spec.json；不存在时用内联示例。"""
    spec_path = SAMPLES_DIR / "traffic_light.spec.json"
    if spec_path.exists():
        try:
            spec = _load_json_file(spec_path)
            requirement = ""
            requirement_path = SAMPLES_DIR / "traffic_light.requirement.md"
            if requirement_path.exists():
                requirement = _read_text_file(requirement_path).strip()
            return _LIGHT_INLINE_REQUIREMENT if not requirement else requirement, spec, \
                "samples/traffic_light.spec.json"
        except Exception as exc:
            _log("读取 samples/traffic_light.spec.json 失败: %s" % exc)
    return _LIGHT_INLINE_REQUIREMENT, _LIGHT_INLINE_SPEC, "内联最小示例（samples/ 下暂无红绿灯样例）"


def _three_station_example():
    spec_path = SAMPLES_DIR / "three_station.spec.json"
    if spec_path.exists():
        try:
            return _load_json_file(spec_path), "samples/three_station.spec.json"
        except Exception as exc:
            _log("读取 samples/three_station.spec.json 失败: %s" % exc)
    return None, ""


def _format_io_rows(io_rows, limit=40):
    rows = [item for item in (io_rows or []) if isinstance(item, dict) and item.get("device")]
    if not rows:
        return ("（未提供 IO 表。先向用户索取 IO 表（设备号/注释/类型），"
                "或只使用需求中明确出现的设备号，并在 assumptions 标注“设备待确认”。）")
    lines = ["| 设备 | 注释 | 类型 |", "|---|---|---|"]
    for item in rows[:limit]:
        comment = _as_str(item.get("comment")).replace("|", "\\|")
        lines.append("| %s | %s | %s |" % (item.get("device"), comment,
                                           item.get("role") or item.get("type") or ""))
    if len(rows) > limit:
        lines.append("…（共 %d 行，其余略）" % len(rows))
    return "\n".join(lines)


def _format_style(style):
    if not style:
        return "（未指定风格画像，按内置默认风格：初始化 → 设备段 → 启动停止 → 报警）"
    try:
        from .style.profile import summarize

        return summarize(style)
    except Exception:
        return "风格：%s（%s 段）" % (style.get("name") or "未命名",
                                    len(style.get("segments") or []))


def _format_lessons(lessons):
    if not lessons:
        return "（暂无历史反馈；生成后可用 gx3gen_add_feedback 记录现场纠错）"
    lines = []
    for item in lessons[:12]:
        if isinstance(item, dict):
            category = item.get("category") or "其他"
            count = item.get("count") or 1
            message = item.get("last_message") or item.get("message") or ""
            lines.append("- [%s×%s] %s" % (category, count, message))
            for example in (item.get("examples") or [])[:2]:
                lines.append("  例：%s" % example)
        else:
            lines.append("- %s" % item)
    return "\n".join(lines)


def _domain_notes(requirement):
    """领域识别：优先 domains 模块，缺失时退化为关键词判断。"""
    text = _as_str(requirement)
    try:
        from . import domains as domains_mod  # type: ignore  # 并行开发模块

        for name in ("detect", "detect_domain", "classify", "match", "resolve"):
            fn = getattr(domains_mod, name, None)
            if callable(fn):
                try:
                    result = fn(text)
                except Exception:
                    continue
                if isinstance(result, str) and result.strip():
                    return ["domain=%s（来自 domains 模块）" % result.strip()]
                if isinstance(result, dict):
                    label = result.get("name") or result.get("id") or result.get("domain") \
                        or result.get("title")
                    if label:
                        return ["domain=%s（来自 domains 模块）" % label]
    except Exception:
        pass
    if any(word in text for word in ("红绿灯", "交通灯", "信号灯", "traffic light", "红绿")):
        return ["domain=traffic_light（双向红绿灯：≥6 相位 + 定时器 + 互锁 + 自动循环）"]
    if any(word in text for word in ("工位", "取放", "气缸", "阀体", "压装", "检测站", "上料")):
        return ["domain=station_sequence（多工位/气缸动作序列：动作 + 到位条件 + 回位/交接）"]
    return []


_QUALITY_RED_LINES = """通用红线（任何一条不过 → 不交付）：
1. 结构完整：至少 1 个 init 段（SM402 → SET S0）+ 1 个 station 段（≥3 个有效步）+ 启动/停止段。
2. 每步必须有“动作或转移条件”，禁止只有 1–2 个空 STL 步/空动作。
3. 每个输出 Y 至少被 SET/RST/OUT 一次；延时定时器必须出现在转移条件里。
4. 最后一步 transition.next=null（回待机）或指回首步（自动循环）。
5. 停止/急停必须复位运行模式位（M500/M521）；报警要有清除路径。
6. 步号段不重叠、定时器/报警位地址不冲突、X/Y 注释覆盖完整。
7. 所有猜测（延时值、按钮、报警位、互锁）必须写进 assumptions 并在回复里告诉用户。

红绿灯专属红线（双向红绿灯必须全部满足）：
1. 相位 ≥6：东西绿 → 东西黄 → 全红 → 南北绿 → 南北黄 → 全红（回到相位 1）。
2. 绿/黄/全红都受定时器控制（T0 起，K 值按 100ms 基准）；建议绿灯 8–10s、黄灯 3s、全红 1s。
3. 互锁：任一方向绿灯时另一方向必须红灯；禁止同方向“绿+黄”同时亮。
4. 自动循环：最后一步 transition.next 指回首步；启动后自动循环。
5. 启动/停止映射到模式位（M500/M501/M521），停止后所有灯回到安全状态。"""


def _render_generation_context(requirement, io_csv, style_id, project_id):
    warnings = []
    project = None
    if project_id:
        try:
            from . import workspace

            project = workspace.load_project(project_id)
        except Exception as exc:
            warnings.append("项目 %s 读取失败：%s" % (project_id, exc))

    io_rows = []
    if isinstance(project, dict) and isinstance(project.get("io"), list):
        io_rows = [dict(item) for item in project["io"] if isinstance(item, dict)]
    if isinstance(project, dict) and not requirement:
        requirement = _as_str(project.get("requirement")).strip()
    if io_csv:
        try:
            from .spec import parse_io_csv

            parsed = parse_io_csv(io_csv)
            if parsed:
                io_rows = parsed
            else:
                warnings.append("io_csv 未解析出有效设备行（列：序号/软元件名/注释/类型，需有表头）")
        except Exception as exc:
            warnings.append("io_csv 解析失败：%s" % exc)

    if not style_id and isinstance(project, dict):
        reference = project.get("style_profile")
        if isinstance(reference, str):
            style_id = reference.strip()
    style = _load_style(style_id)
    if style_id and style is None:
        warnings.append("未找到风格画像 %s（可用 gx3gen_list_styles 查看，或先 gx3gen_import_style）" % style_id)

    lessons = []
    if project_id:
        try:
            from .feedback import store

            lessons = store.lessons(project_id)
        except Exception as exc:
            warnings.append("历史 lessons 读取失败：%s" % exc)

    lines = []
    lines.append("# gx3gen 生成上下文（PLC · GX Works3 · FX5U）")
    if warnings:
        lines.append("## 读取提示\n" + "\n".join("- %s" % item for item in warnings))

    lines.append(
        "## 0. 你的角色与必须遵守的流程\n"
        "你是 PLC 程序工程师的 AI 助手。用户只给中文需求/IO 表；**ProgramSpec JSON 必须由你产出**，"
        "不要让用户手写 JSON，也不要把 GX 程序 CSV 当作交付物。\n"
        "流程：读本上下文 → 缺信息先问用户 → 你产出 ProgramSpec → `gx3gen_validate_spec` → "
        "`gx3gen_generate_program` → 向用户报告产物路径、段结构、质量结果、audit、assumptions 与 GX 落地步骤。\n"
        "GX 导入硬约束：SET/RST 每条 1 个操作数；同一 rung 内 LD/LDI 不得连续（串联用 AND/ANI）；"
        "一个 STL 步块配一个 RETSTL；软元件注释 CSV 用 LF 且标题与目标工程一致。"
        "生成结果里的 audit.ok=false 时先修 spec 再重生成。\n"
        "不要声称已在 GX Works3 里编译/仿真通过——本工具只做初稿生成，编译与验证由工程师在 GX Works3 接手。"
    )

    lines.append(
        "## 1. ProgramSpec 0.1 必需结构（照此写 JSON）\n"
        "```json\n"
        "{\n"
        '  "spec_version": "0.1",\n'
        '  "project": {"name": "项目名", "cpu": "FX5U", "gx_version": "1.082", "style_profile": null},\n'
        '  "io": [{"device": "X0", "comment": "启动按钮", "role": "input"}],\n'
        '  "modes": {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"},\n'
        '  "common": {"handshake_base": 1000, "alarm_base": 2000, "alarm_timer_base": 10,\n'
        '             "start_step": 0, "station_step_base": 100, "station_step_stride": 100,\n'
        '             "step_increment": 10, "auto_rungs": true},\n'
        '  "segments": [\n'
        '    {"title": "初始化", "kind": "init",\n'
        '     "rungs": [{"comment": "上电置待机步", "instructions": [\n'
        '                {"op": "LD", "operands": ["SM402"]}, {"op": "SET", "operands": ["S0"]}]}]},\n'
        '    {"title": "工位段", "kind": "station", "step_range": [100, 199],\n'
        '     "entry_conditions": ["S0"],\n'
        '     "steps": [{"step": 100, "comment": "动作步",\n'
        '                "actions": [{"set": ["Y0"], "reset": ["Y1"], "comment": "动作说明"},\n'
        '                            {"instructions": [{"op": "OUT", "operands": ["T0", "K20"]}],\n'
        '                             "comment": "延时 2s"}],\n'
        '                "transition": {"conditions": ["X3"], "next": 110, "comment": "到位 → 下一步"}}]},\n'
        '    {"title": "启动停止", "kind": "common",\n'
        '     "rungs": [{"comment": "启动", "instructions": [{"op": "LD", "operands": ["X0"]},\n'
        '                                                  {"op": "SET", "operands": ["M500"]}]}]}\n'
        "  ],\n"
        '  "alarms": {"enabled": false, "base": 2000, "timer_base": 10,\n'
        '             "cylinders": [{"name": "竖直缸", "output": "Y0", "on_sensor": "X3",\n'
        '                            "home_sensor": "X2"}]},\n'
        '  "assumptions": ["延时 2s 待确认"],\n'
        '  "notes": []\n'
        "}\n"
        "```\n"
        "硬约束：spec_version 必须 \"0.1\"、cpu 必须 \"FX5U\"；segments 至少 1 个 init + 1 个 station；"
        "station.steps[].step 同段内递增不重复（建议 100/110/120…）；transition.next 为下一步整数或 null"
        "（null = 回待机 S0）；设备只能是 IO 表/需求中出现的合法软元件号，不确定就写 assumptions。"
        "GX 指令硬约束：SET/RST 只能 1 操作数；LD/LDI 不能连续；STL/RETSTL 1:1；"
        "注释标题用 project.comment_title（默认 Chinese Simplified/简体中文）。"
    )

    lines.append(
        "## 2. 架构默认（照此搭骨架）\n"
        "- 初始化 init：`SM402 → SET S0`；每段步号从 100 起、段步长 100、步内增量 10。\n"
        "- 启动/停止 common：`LD X0 → SET M500 / SET M521`；`LD X1 → RST M500 / RST M521 / 复位输出`。\n"
        "- 自动流程 station：`entry_conditions: [\"S0\", \"X0\"]`；每步 = 动作（SET/RST/OUT T） + "
        "转移（LD S当前 AND 条件 → SET S下一步）；最后一步 transition.next=null 自动回待机。\n"
        "- 手自动：M521 自动 / M522 手动；手动段可用 `kind: \"rungs\"` 写互锁/点动网络。\n"
        "- 报警：`alarms.cylinders` 填每个气缸（output/on_sensor/home_sensor），生成器自动补 "
        "M2000 起报警位、T10 起延时定时器（默认 K20=2s）与停止清除。\n"
        "- 注释：所有 IO 写中文短注释；段标题用 `[Title]段名` 由生成器自动加；全局 END 由生成器自动补。"
    )

    domain_lines = _domain_notes(requirement)
    quality_section = ["## 3. 质量红线与检查清单", _QUALITY_RED_LINES]
    if domain_lines:
        quality_section.append("当前需求领域提示：\n" + "\n".join("- %s" % item for item in domain_lines))
    module_checklist = _quality_module_checklist()
    if module_checklist:
        quality_section.append(
            "本地 quality 模块检查项（最终以工具返回的 quality 为准）：\n"
            + "\n".join("- %s" % item for item in module_checklist))
    quality_section.append(
        "提交前自检：步数是否够？每个 Y 都有驱动？停止能复位？定时器都有转移条件？assumptions 是否写全？"
    )
    lines.append("\n\n".join(quality_section))

    example_lines = ["## 4. 示例 A：双向红绿灯（照此结构写）"]
    requirement_text, example_spec, source = _traffic_example()
    example_lines.append("需求：%s" % requirement_text.strip())
    example_lines.append("spec 来源：%s" % source)
    example_json = json.dumps(example_spec, ensure_ascii=False, indent=2)
    if len(example_json) > 5000:
        example_lines.append("（完整 JSON 较长，以下为结构摘要；完整文件：%s）" % source)
        example_lines.append("```text\n" + _outline_spec(example_spec) + "\n```")
    else:
        example_lines.append("```json\n" + example_json + "\n```")
    example_lines.append(
        "要点：6 相位循环、每相位都有定时器、任一方向绿灯时另一方向红灯、最后一步 next 指回首步。"
    )
    lines.append("\n\n".join(example_lines))

    three_spec, three_source = _three_station_example()
    if three_spec:
        lines.append(
            "## 5. 示例 B：三工位序列（%s）\n"
            "段结构：初始化 → 取放站（动作步 + 到位条件 + 延时 T0 + 回位/交接）→ 启动停止；"
            "3 个气缸报警（M2000 起 / T10 起）。\n"
            "结构摘要：\n```text\n%s\n```\n"
            "完整 spec 可用 `gx3gen_get_examples` 获取。" % (three_source, _outline_spec(three_spec))
        )

    lines.append("## 6. 当前 IO 表\n" + _format_io_rows(io_rows))
    lines.append("## 7. 风格摘要\n" + _format_style(style))
    lines.append("## 8. 历史经验（lessons，按反馈自动积累）\n" + _format_lessons(lessons))
    lines.append(
        "## 9. 交付与报告（生成成功后必须这么回复用户）\n"
        "1) 生成文件：program.csv（程序本体）/ comments.csv（软元件注释）/ network_table.md / report.md"
        "（用工具返回的绝对或工作区相对路径）；\n"
        "2) 段结构与步号（用返回的 segments/stats）；\n"
        "3) 质量结果：quality.ok / 错误 / 告警 / lint；不达标时先迭代或明确询问用户；\n"
        "4) assumptions 逐条列出（设备、延时、报警、互锁等待现场确认项）；\n"
        "5) GX Works3 落地步骤：打开基座工程副本 → MAIN 程序内 [编辑]→[导入文件] 选 program.csv → "
        "导入软元件注释 → 全部转换（Shift+Alt+F4）→ 修编译错误/补逻辑 → 保存。\n"
        "6) 提醒用户：CSV 导入只替换已存在程序，导入后需“全部转换”，建议先备份工程副本。"
    )

    text = "\n\n".join(lines)
    return _truncate(text, 14000)


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------
def _tool_get_generation_context(arguments):
    requirement = _as_str(arguments.get("requirement")).strip()
    io_csv = _as_str(arguments.get("io_csv")).strip()
    style_id = _as_str(arguments.get("style_id")).strip()
    project_id = _as_str(arguments.get("project_id")).strip()
    try:
        text = _render_generation_context(requirement, io_csv, style_id, project_id)
    except Exception as exc:  # 上下文渲染失败也不崩
        _log("生成上下文失败: %s\n%s" % (exc, traceback.format_exc()))
        return _fail("生成上下文失败：%s: %s" % (type(exc).__name__, exc))
    return _text_result(text)


def _extract_spec(arguments):
    spec = arguments.get("spec")
    if isinstance(spec, str) and spec.strip():
        try:
            spec = json.loads(spec)
        except ValueError as exc:
            raise ToolError("spec 字符串不是合法 JSON：%s" % exc)
    if not isinstance(spec, dict):
        raise ToolError("缺少 spec：请传 ProgramSpec JSON 对象（不要传文件路径）")
    return spec


def _validate_spec_or_errors(spec):
    """返回 (ok, errors)。"""
    try:
        from .spec import SpecError, validate_spec

        try:
            validate_spec(spec)
        except SpecError as exc:
            errors = [line.strip()[2:].strip() for line in str(exc).splitlines()
                      if line.strip().startswith("- ")]
            return False, errors or [str(exc)]
        except Exception as exc:
            return False, ["%s: %s" % (type(exc).__name__, exc)]
        return True, []
    except Exception as exc:  # spec 模块本身不可用
        return False, ["spec 模块不可用：%s" % exc]


def _tool_validate_spec(arguments):
    spec = _extract_spec(arguments)
    requirement = _as_str(arguments.get("requirement")).strip()
    ok, errors = _validate_spec_or_errors(spec)
    quality = _check_quality(spec, requirement) if ok else _empty_quality("spec 结构不合法，跳过质量检查")
    warnings = list(quality.get("warnings") or [])
    suggestions = list(quality.get("suggestions") or [])
    result = {
        "ok": ok,
        "spec_version": spec.get("spec_version"),
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "quality": quality,
        "quality_ok": bool(quality.get("ok")) if quality.get("available") else None,
        "next_step": "" if not ok else "spec 结构合法：用 gx3gen_generate_program 生成程序；"
                                       "若 quality_ok=false 先按 quality.errors 修复或与用户确认。",
    }
    return _json_result(result, is_error=not ok)


def _tool_generate_program(arguments):
    spec = _extract_spec(arguments)
    requirement = _as_str(arguments.get("requirement")).strip()
    name = _as_str(arguments.get("name")).strip()
    out_dir_arg = _as_str(arguments.get("out_dir")).strip()
    force = bool(arguments.get("force"))

    ok, errors = _validate_spec_or_errors(spec)
    if not ok:
        return _fail("ProgramSpec 校验失败，未生成文件", errors=errors)

    quality = _check_quality(spec, requirement)
    if quality.get("available") and not quality.get("ok") and not force:
        return _fail(
            "质量红线未通过，未生成文件。请先修复 quality.errors；"
            "若确认要生成初稿可传 force=true（风险由你向用户说明）。",
            errors=list(quality.get("errors") or []) or ["质量检查未通过"],
            quality=quality,
        )

    style = _load_style(_as_str(arguments.get("style_id")).strip())
    if style is None:
        style = _style_from_spec(spec)

    if out_dir_arg:
        out_dir = out_dir_arg
    else:
        try:
            from .config import workspace_root

            root = Path(workspace_root())
        except Exception:
            root = REPO_ROOT / "workspace"
        project_name = name or _as_str((spec.get("project") or {}).get("name"), "mcp-project")
        from datetime import datetime

        out_dir = str(root / "out" / ("%s-%s" % (_safe_name(project_name), datetime.now().strftime("%Y%m%d-%H%M%S"))))

    try:
        from .generator import generate  # 局部 import：生成器可能是并行开发产物

        try:  # 新签名带 requirement/force（质量检查更严格）
            result = generate(spec, style, out_dir, project_name=name or None,
                              requirement=requirement, force=force)
        except TypeError:
            try:
                result = generate(spec, style, out_dir, project_name=name or None)
            except TypeError:
                result = generate(spec, style, out_dir)
    except Exception as exc:
        if _is_quality_error(exc):
            return _fail("质量检查未通过，生成器拒绝生成", errors=_exception_errors(exc),
                         quality=getattr(exc, "quality", {}) or {})
        _log("generate 失败: %s\n%s" % (exc, traceback.format_exc()))
        return _fail("生成失败：%s: %s" % (type(exc).__name__, exc))

    result = result if isinstance(result, dict) else {}
    raw_quality = result.get("quality")
    merged_quality = _normalize_quality_result(raw_quality) if isinstance(raw_quality, dict) else quality
    return _ok(
        summary=result.get("summary") or "",
        project_name=name or _as_str((spec.get("project") or {}).get("name")),
        out_dir=out_dir,
        program_csv=result.get("program_csv"),
        comment_csv=result.get("comment_csv"),
        network_table=result.get("network_table"),
        report=result.get("report"),
        stats=result.get("stats") or {},
        segments=result.get("segments") or [],
        assumptions=list(result.get("assumptions") or spec.get("assumptions") or []),
        lint=result.get("lint") or [],
        audit=result.get("audit") or {},
        quality=merged_quality,
        quality_ok=bool(merged_quality.get("ok")) if merged_quality.get("available") else None,
        generated_at=result.get("generated_at"),
        next_step=(
            "把 program.csv/comments.csv 发给用户，并报告 stats/segments/quality/lint/audit/assumptions；"
            "导入前可再调用 gx3gen_audit_csv 复核；提醒在 GX Works3 副本工程中导入并执行“全部转换”。"
        ),
    )


def _tool_audit_csv(arguments):
    """导入 GX Works3 前自检 program.csv / comments.csv。"""
    program_path = _as_str(arguments.get("program_csv_path")).strip()
    comment_path = _as_str(arguments.get("comment_csv_path")).strip()
    expected_title = _as_str(arguments.get("expect_comment_title")).strip()
    if not program_path:
        raise ToolError("缺少 program_csv_path：请传 gx3gen_generate_program 返回的程序本体 CSV 路径")
    try:
        from .audit import audit

        result = audit(program_path, comment_path or None,
                       expected_comment_title=expected_title or None)
    except FileNotFoundError as exc:
        return _fail("CSV 文件不存在：%s" % exc)
    except Exception as exc:
        _log("audit 失败: %s\n%s" % (exc, traceback.format_exc()))
        return _fail("自检失败：%s: %s" % (type(exc).__name__, exc))
    payload = dict(result)
    payload["next_step"] = (
        "自检通过可继续 GX Works3 导入+全部转换；未通过请按 problems 修 spec/CSV 后重新生成。"
    )
    return _json_result(payload, is_error=not bool(result.get("ok")))




def _project_spec_path(project_id):
    from . import workspace

    return Path(workspace.project_dir(project_id)) / "spec.json"


def _tool_create_project(arguments):
    name = _as_str(arguments.get("name")).strip() or "未命名项目"
    requirement = _as_str(arguments.get("requirement")).strip()
    io_rows = arguments.get("io")
    io_csv = _as_str(arguments.get("io_csv")).strip()
    if io_csv:
        try:
            from .spec import parse_io_csv

            io_rows = parse_io_csv(io_csv) or []
        except Exception as exc:
            return _fail("io_csv 解析失败：%s: %s" % (type(exc).__name__, exc))
    if io_rows is not None and not isinstance(io_rows, list):
        raise ToolError("io 必须是 IO 行数组：[{device, comment, role}]")
    try:
        from . import workspace

        project = workspace.create_project(name, requirement=requirement)
        if io_rows:
            project["io"] = io_rows
            workspace.save_project(project)
        spec_path = _project_spec_path(project["id"])
        return _ok(project_id=project["id"], name=project.get("name"),
                   project_path=workspace.project_dir(project["id"]),
                   spec_path=str(spec_path),
                   next_step="用 gx3gen_save_spec 写入 ProgramSpec；Web 软件会读取同一份项目/spec。")
    except Exception as exc:
        return _fail("创建项目失败：%s: %s" % (type(exc).__name__, exc))


def _tool_save_spec(arguments):
    project_id = _as_str(arguments.get("project_id")).strip()
    if not project_id:
        raise ToolError("缺少 project_id（先用 gx3gen_create_project 创建）")
    spec = _extract_spec(arguments)
    requirement = _as_str(arguments.get("requirement"))
    try:
        from . import workspace

        project = workspace.load_project(project_id)
        project["spec"] = spec
        if requirement.strip():
            project["requirement"] = requirement
        io_rows = arguments.get("io")
        if isinstance(io_rows, list) and io_rows:
            project["io"] = io_rows
        workspace.save_project(project)
        spec_path = _project_spec_path(project_id)
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        ok, errors = _validate_spec_or_errors(spec)
        quality = _check_quality(spec, requirement) if ok else _empty_quality("spec 结构不合法")
        return _ok(project_id=project_id, spec_path=str(spec_path),
                   valid=ok, errors=errors, quality=quality,
                   quality_ok=bool(quality.get("ok")) if quality.get("available") else None,
                   next_step="确认质量通过后调用 gx3gen_generate_program；Web 软件读取同一份 spec。")
    except FileNotFoundError:
        return _fail("项目不存在：%s" % project_id)
    except Exception as exc:
        return _fail("保存 spec 失败：%s: %s" % (type(exc).__name__, exc))


def _tool_load_spec(arguments):
    project_id = _as_str(arguments.get("project_id")).strip()
    if not project_id:
        raise ToolError("缺少 project_id")
    try:
        from . import workspace

        project = workspace.load_project(project_id)
    except FileNotFoundError:
        return _fail("项目不存在：%s" % project_id)
    except Exception as exc:
        return _fail("读取项目失败：%s: %s" % (type(exc).__name__, exc))
    spec = project.get("spec")
    spec_path = _project_spec_path(project_id)
    if not isinstance(spec, dict) and spec_path.exists():
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except ValueError:
            spec = None
    if not isinstance(spec, dict):
        return _fail("项目 %s 还没有 ProgramSpec；先写 spec 或调用 gx3gen_save_spec" % project_id)
    return _ok(project_id=project_id, spec=spec,
               requirement=project.get("requirement") or "",
               io=project.get("io") or [], spec_path=str(spec_path))


def _tool_list_projects(arguments):
    try:
        from . import workspace

        return _ok(projects=workspace.list_projects())
    except Exception as exc:
        return _fail("项目列表读取失败：%s: %s" % (type(exc).__name__, exc))


def _tool_get_examples(arguments):
    kind = _as_str(arguments.get("kind"), "all").strip() or "all"
    if kind not in ("traffic_light", "three_station", "all"):
        raise ToolError("kind 只能是 traffic_light / three_station / all，收到：%r" % kind)
    examples = {}
    if kind in ("traffic_light", "all"):
        requirement, spec, source = _traffic_example()
        examples["traffic_light"] = {
            "requirement": requirement,
            "spec": spec,
            "source": source,
            "notes": "双向红绿灯基线：6 相位 + 定时器 + 互锁 + 自动循环；可按现场交规调整时长。",
        }
    if kind in ("three_station", "all"):
        spec, source = _three_station_example()
        if spec is None:
            spec = {
                "spec_version": "0.1",
                "project": {"name": "三工位取放", "cpu": "FX5U", "gx_version": "1.082",
                            "style_profile": None},
                "io": [], "modes": {}, "segments": [],
                "alarms": {"enabled": False, "base": 2000, "timer_base": 10, "cylinders": []},
                "assumptions": ["样例文件不存在，使用最小占位结构"],
            }
            source = "内联最小示例"
        examples["three_station"] = {
            "requirement": _read_text_file(SAMPLES_DIR / "three_station.requirement.md").strip()
            if (SAMPLES_DIR / "three_station.requirement.md").exists() else "三工位取放（竖直缸/水平缸/夹爪）",
            "spec": spec,
            "source": source,
            "notes": "序列控制参考：初始化 → 工位段（动作+到位+延时+回位）→ 启动停止 + 气缸报警。",
        }
    return _ok(examples=examples, kind=kind,
               usage="把示例 spec 当作结构参考，按当前需求/IO 改写；不要照抄设备与工艺。")


def _tool_list_styles(arguments):
    try:
        from . import workspace

        styles = workspace.list_styles()
    except Exception as exc:
        return _fail("风格库读取失败：%s: %s" % (type(exc).__name__, exc))
    return _ok(styles=styles, count=len(styles))


def _tool_import_style(arguments):
    program_path = _as_str(arguments.get("program_csv_path")).strip()
    comment_path = _as_str(arguments.get("comment_csv_path")).strip()
    name = _as_str(arguments.get("name")).strip()
    if not program_path:
        raise ToolError("缺少 program_csv_path：请传 GX Works3 导出的程序本体 CSV 路径")
    program_file = Path(program_path)
    if not program_file.exists():
        return _fail("程序本体 CSV 不存在：%s" % program_path)
    try:
        program_text = _read_text_file(program_file)
    except OSError as exc:
        return _fail("程序本体 CSV 读取失败：%s" % exc)
    comment_text = ""
    if comment_path:
        comment_file = Path(comment_path)
        if not comment_file.exists():
            return _fail("注释 CSV 不存在：%s" % comment_path)
        try:
            comment_text = _read_text_file(comment_file)
        except OSError as exc:
            return _fail("注释 CSV 读取失败：%s" % exc)
    try:
        from .style.miner import mine  # 局部 import：风格挖掘模块并行开发

        profile = mine(program_text, comment_text or None, name or program_file.stem)
    except Exception as exc:
        _log("风格挖掘失败: %s\n%s" % (exc, traceback.format_exc()))
        if isinstance(exc, (KeyError, ValueError, IndexError)):
            return _fail("程序本体 CSV 解析失败（需要 GX Works3 导出的 7 列程序本体 CSV）：%s" % exc)
        return _fail("风格学习失败：%s: %s" % (type(exc).__name__, exc))
    if not isinstance(profile, dict):
        return _fail("风格学习返回了非字典结果：%r" % (type(profile),))
    if name:
        profile["name"] = name
    try:
        from . import workspace

        saved = workspace.save_style(profile)
    except Exception as exc:
        return _fail("风格画像保存失败：%s: %s" % (type(exc).__name__, exc))
    return _ok(
        style={"id": saved.get("id"), "name": saved.get("name"),
               "created_at": saved.get("created_at"),
               "segment_count": len(saved.get("segments") or [])},
        message="风格已导入；在 get_generation_context / generate 里用 style_id=%s 引用。" % saved.get("id"),
    )


def _tool_add_feedback(arguments):
    project_id = _as_str(arguments.get("project_id")).strip()
    if not project_id:
        raise ToolError("缺少 project_id")
    message = _as_str(arguments.get("message")).strip()
    if not message:
        raise ToolError("缺少 message：请描述问题（如“缺少压装站到检测站的交接信号”）")
    target = arguments.get("target")
    category = _as_str(arguments.get("category"), "其他").strip() or "其他"
    severity = _as_str(arguments.get("severity"), "warning").strip() or "warning"
    expected = _as_str(arguments.get("expected")).strip()
    try:
        from .feedback import store

        record = store.add(project_id, target, category, severity, message, expected)
    except FileNotFoundError:
        return _fail("项目不存在：%s（可先用 gx3gen_create_project 创建项目）" % project_id)
    except Exception as exc:
        return _fail("反馈写入失败：%s: %s" % (type(exc).__name__, exc))
    return _ok(record=record,
               message="反馈已记录；下次 gx3gen_get_generation_context 会自动带上 lessons。")


def _tool_get_lessons(arguments):
    project_id = _as_str(arguments.get("project_id")).strip()
    if not project_id:
        raise ToolError("缺少 project_id")
    try:
        from .feedback import store

        lessons = store.lessons(project_id)
    except FileNotFoundError:
        return _fail("项目不存在：%s" % project_id)
    except Exception as exc:
        return _fail("lessons 读取失败：%s: %s" % (type(exc).__name__, exc))
    return _ok(project_id=project_id, lessons=lessons, count=len(lessons))


def _dispatch_tool(name, arguments):
    handlers = {
        "gx3gen_get_generation_context": _tool_get_generation_context,
        "gx3gen_validate_spec": _tool_validate_spec,
        "gx3gen_generate_program": _tool_generate_program,
        "gx3gen_audit_csv": _tool_audit_csv,
        "gx3gen_create_project": _tool_create_project,
        "gx3gen_save_spec": _tool_save_spec,
        "gx3gen_load_spec": _tool_load_spec,
        "gx3gen_list_projects": _tool_list_projects,
        "gx3gen_get_examples": _tool_get_examples,
        "gx3gen_list_styles": _tool_list_styles,
        "gx3gen_import_style": _tool_import_style,
        "gx3gen_add_feedback": _tool_add_feedback,
        "gx3gen_get_lessons": _tool_get_lessons,
    }
    handler = handlers.get(name)
    if handler is None:
        return _fail("未知工具：%s" % name, available_tools=sorted(handlers))
    if arguments is None:
        arguments = {}
    if isinstance(arguments, str) and arguments.strip():
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return _fail("tools/call 的 arguments 必须是 JSON 对象（收到无法解析的字符串）")
    if not isinstance(arguments, dict):
        return _fail("tools/call 的 arguments 必须是 JSON 对象")
    # 兼容把参数包在 req 对象里的调用方式：{"req": {"requirement": ...}}
    if isinstance(arguments.get("req"), dict):
        merged = dict(arguments["req"])
        merged.update({key: value for key, value in arguments.items() if key != "req"})
        arguments = merged
    try:
        return handler(arguments)
    except ToolError as exc:
        return _fail(exc.message, errors=exc.errors)
    except Exception as exc:
        _log("工具 %s 执行失败: %s\n%s" % (name, exc, traceback.format_exc()))
        return _fail("工具执行失败：%s: %s" % (type(exc).__name__, exc))


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------
def _tool_definitions():
    return [
        {
            "name": "gx3gen_get_generation_context",
            "description": (
                "生成 PLC 程序前必读：返回给 AI agent 的紧凑上下文（ProgramSpec 结构、架构默认、"
                "质量红线、双向红绿灯/三工位示例、IO 表、风格摘要、历史 lessons）。"
                "触发词：PLC、GX Works3、FX5U、红绿灯、工位、程序生成。"
                "先调用本工具，再由你（agent）自己产出 ProgramSpec。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string", "description": "用户中文需求原文（可选）"},
                    "io_csv": {"type": "string", "description": "IO 表 CSV 文本（软元件名/注释/类型）"},
                    "style_id": {"type": "string", "description": "风格画像 id（可选，见 gx3gen_list_styles）"},
                    "project_id": {"type": "string", "description": "已有项目 id（读取需求/IO/风格/lessons，可选）"},
                },
            },
        },
        {
            "name": "gx3gen_validate_spec",
            "description": (
                "校验 ProgramSpec 并运行质量红线检查。返回 {ok, errors, warnings, suggestions, quality}。"
                "生成前必须调用；ok 表示结构合法，quality.ok 表示质量红线是否通过。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "spec": {"type": "object", "description": "ProgramSpec JSON 对象"},
                    "requirement": {"type": "string", "description": "需求原文（给质量检查做语义核对，可选）"},
                },
                "required": ["spec"],
            },
        },
        {
            "name": "gx3gen_generate_program",
            "description": (
                "由 ProgramSpec 生成 GX Works3 可导入的程序初稿：program.csv、comments.csv、"
                "network_table.md、report.md。质量红线不过默认拒绝生成（force=true 可强制生成初稿）。"
                "成功返回产物路径 + summary/stats/segments/lint/quality/audit（GX 导入自检）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "spec": {"type": "object", "description": "ProgramSpec JSON 对象"},
                    "requirement": {"type": "string", "description": "需求原文（可选）"},
                    "name": {"type": "string", "description": "项目/程序名（可选，覆盖 spec 里的名字）"},
                    "out_dir": {"type": "string", "description": "输出目录（可选；默认在 GX3GEN_WORKSPACE/out 下）"},
                    "force": {"type": "boolean", "description": "质量红线未过时仍生成初稿（默认 false）"},
                    "style_id": {"type": "string", "description": "风格画像 id（可选，见 gx3gen_list_styles）"},
                },
                "required": ["spec"],
            },
        },
        {
            "name": "gx3gen_audit_csv",
            "description": (
                "GX Works3 导入前自检 program.csv/comments.csv：BOM/行尾/操作数个数/"
                "rung 内 IL 序列/STL-RETSTL 配对/注释标题与 LF。返回 {ok, problems, ...}；"
                "不通过时 isError=true。生成器已自动包含 audit，可用本工具复核或检查外部 CSV。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "program_csv_path": {"type": "string", "description": "程序本体 CSV 路径"},
                    "comment_csv_path": {"type": "string", "description": "软元件注释 CSV 路径（可选）"},
                    "expect_comment_title": {
                        "type": "string",
                        "description": "目标工程的注释标题（可选，如 Chinese Simplified/简体中文）",
                    },
                },
                "required": ["program_csv_path"],
            },
        },
        {
            "name": "gx3gen_create_project",
            "description": (
                "创建一个 agent 工作项目（可选需求/IO）。返回 project_id、project_path、spec_path。"
                "项目与 Web 软件共用 workspace：agent 写入的 spec 可在 Web 界面打开审阅。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "项目名"},
                    "requirement": {"type": "string", "description": "需求原文（可选）"},
                    "io": {
                        "type": "array",
                        "description": "IO 行数组 [{device, comment, role}]（可选）",
                        "items": {"type": "object"},
                    },
                    "io_csv": {"type": "string", "description": "IO 表 CSV 文本（与 io 二选一，可选）"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "gx3gen_save_spec",
            "description": (
                "把 agent 产出的 ProgramSpec 保存到项目（写 project.json 与 spec.json），"
                "并返回结构校验/质量检查结果。之后调用 gx3gen_generate_program；"
                "Web 软件会读取同一份 spec。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "gx3gen_create_project 返回的 id"},
                    "spec": {"type": "object", "description": "ProgramSpec JSON 对象"},
                    "requirement": {"type": "string", "description": "需求原文（可选，用于更新项目需求）"},
                    "io": {
                        "type": "array",
                        "description": "IO 行数组（可选，用于更新项目 IO）",
                        "items": {"type": "object"},
                    },
                },
                "required": ["project_id", "spec"],
            },
        },
        {
            "name": "gx3gen_load_spec",
            "description": "读取项目中已保存的 ProgramSpec（project.json 或 spec.json），用于继续修改/生成。",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
            },
        },
        {
            "name": "gx3gen_list_projects",
            "description": "列出 workspace 中的项目（id/name/是否有 spec/输出/反馈数），便于 agent 选择项目继续工作。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "gx3gen_get_examples",
            "description": "获取示例需求 + ProgramSpec（双向红绿灯 traffic_light / 三工位 three_station）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["traffic_light", "three_station", "all"]},
                },
            },
        },
        {
            "name": "gx3gen_list_styles",
            "description": "列出已学习的风格画像（StyleProfile）。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "gx3gen_import_style",
            "description": (
                "导入 GX Works3 导出的程序本体 CSV（+可选软元件注释 CSV），学习用户编程风格并存进风格库。"
                "返回 style.id，可在 context/generate 里用 style_id 引用。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "program_csv_path": {"type": "string", "description": "程序本体 CSV 文件路径"},
                    "comment_csv_path": {"type": "string", "description": "软元件注释 CSV 文件路径（可选）"},
                    "name": {"type": "string", "description": "风格名称（可选）"},
                },
                "required": ["program_csv_path"],
            },
        },
        {
            "name": "gx3gen_add_feedback",
            "description": (
                "记录对生成程序的纠错/审阅反馈（项目级），会转成 lessons 注入下一轮生成。"
                "category：结构/命名/逻辑/互锁/报警/停止/其他；severity：error/warning/suggestion。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "target": {"type": "object", "description": "{kind: network|segment|program, segment, index}"},
                    "category": {"type": "string"},
                    "severity": {"type": "string", "enum": ["error", "warning", "suggestion"]},
                    "message": {"type": "string"},
                    "expected": {"type": "string", "description": "期望的正确写法（可选）"},
                },
                "required": ["project_id", "message"],
            },
        },
        {
            "name": "gx3gen_get_lessons",
            "description": "读取项目历史反馈聚合出的 lessons（按 category 聚合），用于下一轮规划/生成自检。",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
            },
        },
    ]


# ---------------------------------------------------------------------------
# JSON-RPC / MCP 协议
# ---------------------------------------------------------------------------
def _response(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error_response(req_id, code, message, data=None):
    error = {"code": int(code), "message": _as_str(message)}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def _handle_initialize(req_id, params):
    result = {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": SERVER_INSTRUCTIONS,
    }
    client_version = params.get("protocolVersion") if isinstance(params, dict) else None
    if isinstance(client_version, str) and client_version.strip():
        result["clientProtocolVersion"] = client_version
    return _response(req_id, result)


def _handle_tools_call(req_id, params):
    if not isinstance(params, dict) or not _as_str(params.get("name")).strip():
        return _error_response(req_id, -32602, "tools/call 需要 params.name")
    name = _as_str(params.get("name")).strip()
    arguments = params.get("arguments")
    with contextlib.redirect_stdout(sys.stderr):
        result = _dispatch_tool(name, arguments)
    return _response(req_id, result)


def handle_message(message):
    """处理一条 JSON-RPC 消息；返回响应 dict（通知返回 None）。"""
    if isinstance(message, list):
        return _error_response(None, -32600, "暂不支持 JSON-RPC 批量消息（batch）")
    if not isinstance(message, dict):
        return _error_response(None, -32600, "Invalid Request：消息必须是 JSON 对象")
    method = message.get("method")
    req_id = message.get("id")
    is_notification = "id" not in message
    if is_notification:
        if method in ("notifications/initialized", "notifications/cancelled",
                      "notifications/roots/list_changed"):
            if method == "notifications/initialized":
                _log("客户端已初始化（notifications/initialized）")
            return None
        _log("忽略未知通知：%s" % method)
        return None
    params = message.get("params")
    if method == "initialize":
        return _handle_initialize(req_id, params if isinstance(params, dict) else {})
    if method == "ping":
        return _response(req_id, {})
    if method == "tools/list":
        return _response(req_id, {"tools": _tool_definitions()})
    if method == "tools/call":
        try:
            return _handle_tools_call(req_id, params)
        except Exception as exc:  # 兜底：任何异常都不得中断 server
            _log("tools/call 未捕获异常: %s\n%s" % (exc, traceback.format_exc()))
            return _error_response(req_id, -32603, "Internal error: %s" % exc)
    return _error_response(req_id, -32601, "Method not found: %s" % method)


def _reconfigure_stdio():
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace", newline="\n"
                            if stream_name != "stdin" else None)
            except (TypeError, ValueError, OSError):
                try:
                    reconfigure(encoding="utf-8", errors="replace")
                except (TypeError, ValueError, OSError):
                    pass


def serve(stdin=None, stdout=None):
    """stdio 主循环（可注入流便于测试）；返回退出码。"""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    def _write(response):
        stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
        stdout.flush()

    for raw_line in stdin:
        # 容忍 Windows 管道/重定向写入的 UTF-8 BOM（PowerShell $OutputEncoding 常见）
        line = raw_line.lstrip("\ufeff").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            _write(_error_response(None, -32700, "Parse error: %s" % exc))
            continue
        try:
            response = handle_message(message)
        except Exception as exc:  # 单条消息异常不影响后续
            _log("处理消息异常: %s\n%s" % (exc, traceback.format_exc()))
            req_id = message.get("id") if isinstance(message, dict) else None
            response = _error_response(req_id, -32603, "Internal error: %s" % exc)
        if response is not None:
            _write(response)
    _log("stdin 已关闭，退出")
    return 0


def main(argv=None):
    """MCP stdio server 入口。"""
    _reconfigure_stdio()
    _log("%s MCP server %s 启动（stdio，协议 %s）" % (SERVER_NAME, SERVER_VERSION, PROTOCOL_VERSION))
    try:
        return serve()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # 绝不把 traceback 写 stdout
        _log("server 异常退出: %s\n%s" % (exc, traceback.format_exc()))
        return 1


if __name__ == "__main__":
    sys.exit(main())
