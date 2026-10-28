"""可选 standalone LLM 适配器：OpenAI 兼容 /chat/completions（urllib，标准库）。

**不属于 agent 主路径**：当用户用 AI agent（Claude Code / DSH / Cursor…）打开项目时，
agent 自己产出 ProgramSpec；MCP 不调用本模块、不需要 API Key。
本模块只用于「没有 agent 的独立 Web/CI 模式」，或在设置页显式配置后的 `run.py plan --llm`。

只用 :func:`build_prompt` / :func:`extract_json` / :func:`parse_response` 做离线可测；
真正发请求的只有 :func:`call_llm` 与 :func:`plan_with_llm`。

配置读 :mod:`gx3gen.config` 的 ``llm`` 段（可选）：
``{"base_url": "...", "api_key": "...", "model": "...", "timeout": 120, "temperature": 0.2}``。
base_url 可以是 ``https://host/v1``、``https://host/v1/chat/completions`` 或
``http://127.0.0.1:11434/v1``（会自动补 ``/chat/completions``）。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..quality import check_quality, quality_text
from ..spec import CPU, SPEC_VERSION, SpecError, default_spec, validate_spec
from .deterministic import (
    apply_style_baseline,
    normalize_io,
    normalize_lessons,
    style_summary,
)
from .errors import PlannerError

__all__ = [
    "PlannerError",
    "SYSTEM_PROMPT",
    "build_prompt",
    "call_llm",
    "extract_json",
    "llm_configured",
    "normalize_endpoint",
    "parse_response",
    "plan_with_llm",
]


SYSTEM_PROMPT = (
    "你是三菱 FX5U（GX Works3 1.082）PLC 梯形图程序规划助手。"
    "你的唯一输出是一个合法的 ProgramSpec JSON 对象：不要解释，不要 Markdown 代码块，不要注释。"
    "你必须给出完整的自动流程（初始化→启动停止→自动流程→循环/收尾），"
    "不允许只输出一两个空 STL 步；不确定的工艺参数写进 assumptions。"
)

SCHEMA_SUMMARY = """ProgramSpec(0.1) 结构摘要（必须严格遵守）：
{
  "spec_version": "0.1",
  "project": {"name": str, "cpu": "FX5U", "gx_version": "1.082", "style_profile": str|null},
  "io": [{"device": "X0", "comment": str, "role": "input|output|inout|internal"}],
  "modes": {"run": str, "stop": str, "auto": str, "manual": str},
  "common": {"start_step": int, "station_step_base": int, "station_step_stride": int,
             "step_increment": int, "handshake_base": int, "alarm_base": int,
             "alarm_timer_base": int, "auto_rungs": bool},
  "segments": [
    {"title": "初始化", "kind": "init",
     "rungs": [{"comment": str, "instructions": [{"op": "LD", "operands": ["SM402"]},
                                                {"op": "SET", "operands": ["S0"]}]}]},
    {"title": "取放站", "kind": "station", "step_range": [100, 199],
     "entry_conditions": ["S0", "X0"],
     "handshake": {"in": [{"device": str, "comment": str}],
                   "out": [{"device": str, "comment": str}], "shift": [str]},
     "steps": [{"step": 100, "comment": str,
                "actions": [{"comment": str, "set": ["Y0"], "reset": ["Y1"], "out": ["Y2"],
                             "instructions": [{"op": "OUT", "operands": ["T0", "K20"]}]}],
                "transition": {"conditions": ["X3"], "next": 110, "comment": str}}]},
    {"title": "启动停止", "kind": "common",
     "rungs": [{"comment": str, "instructions": [{"op": "LD", "operands": ["X0"]},
                                                {"op": "SET", "operands": ["M500"]}]}]}
  ],
  "alarms": {"enabled": bool, "base": int, "timer_base": int, "delay": int,
             "reset_device": str|null,
             "cylinders": [{"name": str, "output": "Y0", "on_sensor": "X3"|null,
                            "home_sensor": "X2"|null}]},
  "assumptions": [str],
  "notes": [str]
}
硬约束：
- spec_version 必须是 "0.1"；project.cpu 必须是 "FX5U"；
- 设备号只能是 IO 表/需求里出现的 X/Y/M/D/T/C/S/SM/SD 号或 K/H 常数，不得编造；
- station 段的 steps[].step 必须是同段内递增的不重复整数，最后一步 transition.next 可为 null；
- 每个 station 段至少一个 step，segments 至少包含一个 init 段（SM402→SET S0）与一个 station 段；
- 没把握/需求缺失的内容写进 assumptions，不要自己发明设备或工艺。"""


# ---------------------------------------------------------------------------
# 提示词拼装（离线可测）
# ---------------------------------------------------------------------------
def _format_io(io: Any, limit: int = 200) -> str:
    rows = normalize_io(io)
    if not rows:
        return "（未提供 IO 表：只能使用需求原文中出现的设备号，并在 assumptions 标明待确认）"
    lines = ["设备 | 注释 | 类型"]
    for row in rows[:limit]:
        lines.append("%s | %s | %s" % (row["device"], row["comment"], row["role"]))
    if len(rows) > limit:
        lines.append("…（共 %d 行，已截断）" % len(rows))
    return "\n".join(lines)


def _format_lessons(lessons: Any, limit: int = 20) -> str:
    items = normalize_lessons(lessons)
    if not items:
        return ""
    lines: List[str] = []
    for item in items[:limit]:
        line = "- [%s×%d] %s" % (item["category"], item["count"], item["message"])
        examples = item.get("examples") or []
        if examples:
            line += "（示例：%s）" % "；".join(str(value) for value in examples[:2])
        lines.append(line)
    return "\n".join(lines)


def build_prompt(
    requirement: str,
    io: Any,
    style: Optional[Dict[str, Any]] = None,
    lessons: Any = None,
    extra: str = "",
) -> str:
    """拼装 LLM user prompt：schema 摘要 + 架构要求 + IO 表 + 风格 + 历史经验 + 需求。"""
    requirement_text = str(requirement or "").strip() or "（需求为空：请只生成最小初始化骨架并在 assumptions 提示补充）"
    example = (
        '{"spec_version":"0.1","project":{"name":"双向红绿灯","cpu":"FX5U","gx_version":"1.082"},'
        '"io":[{"device":"X0","comment":"启动","role":"input"},{"device":"X1","comment":"停止","role":"input"},'
        '{"device":"Y0","comment":"东西红灯","role":"output"},{"device":"Y2","comment":"东西绿灯","role":"output"},'
        '{"device":"Y3","comment":"南北红灯","role":"output"},{"device":"Y5","comment":"南北绿灯","role":"output"}],'
        '"modes":{"run":"M500","stop":"M501","auto":"M521","manual":"M522"},'
        '"segments":['
        '{"title":"初始化","kind":"init","rungs":[{"instructions":[{"op":"LD","operands":["SM402"]},{"op":"SET","operands":["S0"]}]}]},'
        '{"title":"启动停止","kind":"common","rungs":['
        '{"instructions":[{"op":"LD","operands":["X0"]},{"op":"SET","operands":["M500"]}]},'
        '{"instructions":[{"op":"LD","operands":["X1"]},{"op":"RST","operands":["M500"]},{"op":"ZRST","operands":["S10","S69"]},{"op":"SET","operands":["S0"]}]}]},'
        '{"title":"自动流程","kind":"station","step_range":[10,69],"entry_conditions":["S0","M500"],"steps":['
        '{"step":10,"actions":[{"set":["Y2","Y3"],"reset":["Y0","Y1","Y4","Y5"],"instructions":[{"op":"OUT","operands":["T0","K250"]}]}],'
        '"transition":{"conditions":["T0"],"next":20}},'
        '{"step":20,"actions":[{"set":["Y1","Y3"],"reset":["Y0","Y2","Y4","Y5"],"instructions":[{"op":"OUT","operands":["T1","K30"]}]}],'
        '"transition":{"conditions":["T1"],"next":30}},'
        '{"step":30,"actions":[{"set":["Y0","Y3"],"reset":["Y1","Y2","Y4","Y5"],"instructions":[{"op":"OUT","operands":["T2","K20"]}]}],'
        '"transition":{"conditions":["T2"],"next":40}},'
        '{"step":40,"actions":[{"set":["Y0","Y5"],"reset":["Y1","Y2","Y3","Y4"],"instructions":[{"op":"OUT","operands":["T3","K200"]}]}],'
        '"transition":{"conditions":["T3"],"next":50}},'
        '{"step":50,"actions":[{"set":["Y0","Y4"],"reset":["Y1","Y2","Y3","Y5"],"instructions":[{"op":"OUT","operands":["T4","K30"]}]}],'
        '"transition":{"conditions":["T4"],"next":60}},'
        '{"step":60,"actions":[{"set":["Y0","Y3"],"reset":["Y1","Y2","Y4","Y5"],"instructions":[{"op":"OUT","operands":["T5","K20"]}]}],'
        '"transition":{"conditions":["T5","M500"],"next":10}}]}]}'
    )
    parts: List[str] = [
        "## ProgramSpec JSON 结构（必须严格遵守）\n" + SCHEMA_SUMMARY.strip(),
        "## 架构与质量红线（违反即判为失败）\n"
        "- 必须包含：初始化段（SM402→SET S0）、启动停止段（对 modes.run 的 SET/RST）、"
        "自动流程段（kind=station，至少 6 个 step）。\n"
        "- 每个 step 必须有 actions（set/reset/out/instructions 至少一项）与 transition.conditions。\n"
        "- 需求出现“循环/周期/自动循环”：自动流程最后一步 transition.next 必须回到第一步编号。\n"
        "- 需求出现红绿灯/信号灯：至少 4 个相位、每相位一个 OUT Tx Kxx 定时器、东西/南北红灯互锁、"
        "最后全红后回到第一相位；有启动/停止按钮处理。\n"
        "- 需求出现延时/秒/时间：必须用 OUT Tn K(秒×10)。\n"
        "- 需求出现启动/停止：必须对 modes.run 做 SET（启动）与 RST（停止），停止时 ZRST 步号并 SET S0。\n"
        "- 需求出现手动/自动：写入 modes.auto/manual，并生成手自动切换逻辑（可简化）。\n"
        "- 需求出现报警/故障：配置 alarms 或生成报警段；没有传感器就不要编造具体报警位，写进 assumptions。\n"
        "- 只输出 1–2 个空 STL 步会被质量检查拒绝。\n\n"
        "## 参考样例（双向红绿灯，可借鉴结构但必须按本次需求调整）\n" + example,
        "## IO 表（只能使用这些设备号，注释可用于设备匹配）\n" + _format_io(io),
        "## 用户风格画像摘要\n" + style_summary(style),
    ]
    lessons_text = _format_lessons(lessons)
    if lessons_text:
        parts.append("## 历史反馈经验（本次规划必须优先规避同类问题）\n" + lessons_text)
    parts.append("## 本次需求原文\n" + requirement_text)
    parts.append(
        "## 输出要求\n"
        "只输出 JSON（一个 ProgramSpec 对象）：不要解释、不要 Markdown 代码块、不要 JSON 之外的任何文字。"
        "必须符合上面的结构与硬约束，且能通过 ProgramSpec 校验、质量检查（循环/定时/启停/相位）。"
    )
    if extra:
        parts.append("## 上一次输出被拒绝的原因（本次必须修正）\n" + extra)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 响应解析（离线可测）
# ---------------------------------------------------------------------------
def _short(text: Any, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit]


def extract_json(text: Any) -> Dict[str, Any]:
    """从 LLM 回复里稳健提取 JSON 对象（支持 ```json 包裹、前后带解释文字）。"""
    if not isinstance(text, str) or not text.strip():
        raise PlannerError("LLM 响应为空")
    raw = text.strip().lstrip("\ufeff").strip()

    candidates: List[str] = []
    fence = re.search(r"```(?:json|JSON)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        candidates.append(fence.group(1).strip())
    candidates.append(raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start:end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data

    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(raw[index:])
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise PlannerError("LLM 响应不是合法 JSON：%s" % _short(raw))


def _normalize_llm_spec(
    data: Dict[str, Any],
    requirement: str = "",
    io: Any = None,
    style: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise PlannerError("LLM 响应必须是 JSON 对象")
    spec = dict(data)
    spec["spec_version"] = SPEC_VERSION

    project = spec.get("project")
    if not isinstance(project, dict):
        project = {}
    project["cpu"] = CPU
    project.setdefault("gx_version", "1.082")
    project.setdefault("style_profile", (style or {}).get("id") if isinstance(style, dict) else None)
    requested_name = str(name or "").strip()
    if requested_name:
        project["name"] = requested_name
    elif not str(project.get("name") or "").strip():
        project["name"] = "AI 规划项目"
    spec["project"] = project

    io_rows = normalize_io(spec.get("io"))
    if not io_rows:
        io_rows = normalize_io(io)
    spec["io"] = io_rows

    segments = spec.get("segments")
    if not isinstance(segments, list) or not segments:
        raise PlannerError("LLM 返回的 spec 缺少 segments")
    spec["segments"] = segments

    defaults = default_spec(project["name"], io=io_rows)
    if not isinstance(spec.get("modes"), dict) or not spec["modes"]:
        spec["modes"] = defaults["modes"]
    if not isinstance(spec.get("common"), dict):
        spec["common"] = defaults["common"]
    spec.setdefault("alarms", defaults["alarms"])
    spec.setdefault("assumptions", [])
    spec.setdefault("notes", [])
    if not isinstance(spec["assumptions"], list):
        spec["assumptions"] = [str(spec["assumptions"])]
    if not isinstance(spec["notes"], list):
        spec["notes"] = [str(spec["notes"])]

    apply_style_baseline(spec, style)
    try:
        validate_spec(spec)
    except SpecError as exc:
        raise PlannerError("LLM 返回的 spec 未通过校验：%s" % _short(str(exc), 500))
    return spec


def parse_response(
    text: Any,
    requirement: str = "",
    io: Any = None,
    style: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """LLM 文本 → 校验通过的 ProgramSpec。"""
    return _normalize_llm_spec(
        extract_json(text),
        requirement=requirement,
        io=io,
        style=style,
        name=name,
    )


# ---------------------------------------------------------------------------
# HTTP 调用
# ---------------------------------------------------------------------------
def _settings(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    llm = (config or {}).get("llm") if isinstance(config, dict) else None
    if not isinstance(llm, dict):
        llm = {}
    return {
        "base_url": str(llm.get("base_url") or "").strip(),
        "api_key": str(llm.get("api_key") or "").strip(),
        "model": str(llm.get("model") or "").strip(),
        "timeout": float(llm.get("timeout") or 120),
        "temperature": float(llm.get("temperature") if llm.get("temperature") is not None else 0.2),
    }


def llm_configured(config: Optional[Dict[str, Any]]) -> bool:
    settings = _settings(config)
    return bool(settings["base_url"] and settings["model"] and settings["api_key"])


def normalize_endpoint(base_url: str) -> str:
    """base_url → 完整 /chat/completions URL。"""
    url = str(base_url or "").strip()
    if not url:
        return ""
    if "chat/completions" in url:
        return url
    return url.rstrip("/") + "/chat/completions"


def _extract_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise PlannerError("LLM 响应不是 JSON 对象")
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if content is None:
            content = choice.get("text")
        if content is not None:
            if isinstance(content, list):  # 部分兼容接口返回 content parts
                texts = []
                for part in content:
                    if isinstance(part, dict):
                        texts.append(str(part.get("text") or ""))
                    else:
                        texts.append(str(part))
                content = "".join(texts)
            if str(content).strip():
                return str(content)
    for key in ("response", "content", "text", "output"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raise PlannerError("LLM 响应缺少 choices[0].message.content")


def call_llm(prompt: str, config: Optional[Dict[str, Any]]) -> str:
    """POST 到 OpenAI 兼容 /chat/completions，返回文本内容。"""
    settings = _settings(config)
    url = normalize_endpoint(settings["base_url"])
    if not url or not settings["model"] or not settings["api_key"]:
        raise PlannerError("未配置 LLM：请在设置中填写 base_url、model、api_key")
    payload: Dict[str, Any] = {
        "model": settings["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": settings["temperature"],
        "stream": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % settings["api_key"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings["timeout"]) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except OSError:  # pragma: no cover - 读取错误体失败
            detail = ""
        raise PlannerError("LLM 请求失败：HTTP %s %s" % (exc.code, _short(detail)))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PlannerError("LLM 请求失败：%s" % _short(exc))
    try:
        payload_data = json.loads(raw)
    except ValueError:
        raise PlannerError("LLM 响应不是合法 JSON：%s" % _short(raw))
    return _extract_content(payload_data)


def plan_with_llm(
    requirement: str,
    io: Any,
    style: Optional[Dict[str, Any]],
    lessons: Any,
    config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """需求 → LLM → ProgramSpec；校验/质量失败重试一次；未配置时抛 PlannerError。"""
    if not llm_configured(config):
        raise PlannerError("未配置 LLM：请在设置中填写 base_url、model、api_key")
    prompt = build_prompt(requirement, io, style, lessons)
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            content = call_llm(prompt, config)
            spec = parse_response(
                content,
                requirement=requirement,
                io=io,
                style=style,
            )
            quality = check_quality(spec, requirement)
            if quality["errors"]:
                raise PlannerError(
                    "LLM 输出的 ProgramSpec 未通过质量检查：\n"
                    + "\n".join("- " + item for item in quality["errors"])
                )
            return spec
        except (PlannerError, SpecError) as exc:
            last_error = exc
            if attempt == 0:
                prompt = build_prompt(
                    requirement, io, style, lessons,
                    extra=str(exc)[:1500],
                )
    raise PlannerError("LLM 规划失败（已重试 1 次）：%s" % _short(str(last_error), 500))
