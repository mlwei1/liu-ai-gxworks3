"""确定性规划器：半结构化中文需求 + IO 表 + 风格画像 + 历史经验 → ProgramSpec。

不调用模型、不联网；输出必须能通过 :func:`gx3gen.spec.validate_spec`。

需求书写约定（与 samples/three_station.requirement.md 一致）：

    初始化:
      SM402 置位 S0
    取放站:
      步号段 100-199
      竖直缸(Y0)下降
      到位(X3) -> 夹紧(Y2)
      到位(X7) -> 延时2s
      ...
    报警:
      气缸: 竖直缸(Y0) 到位(X3) 原位(X2)

解析原则：
- 括号里的设备号/注释优先；其次 IO 表注释包含匹配；再次独立设备号正则；
- 解析不出来的设备/条件只写进 assumptions，绝不臆造设备；
- 一个"动作相"会和紧随其后的"条件相"合并成一个 SFC 步（动作 + 转移条件），
  这与 samples/three_station.spec.json 的结构一致。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..spec import (
    IO_DEVICE_RE,
    VALID_ROLES,
    default_spec,
    is_device,
    lookup_device,
)
from ..domains import build_domain_spec, detect_domain
from .errors import PlannerError

__all__ = [
    "DEFAULT_STEP_BASE",
    "apply_lessons_to_spec",
    "apply_style_baseline",
    "lesson_guidance",
    "normalize_io",
    "normalize_lessons",
    "plan_deterministic",
    "style_baseline",
    "style_summary",
]

# ---------------------------------------------------------------------------
# 默认值（style/apply.py 未就绪时也可独立运行）
# ---------------------------------------------------------------------------
DEFAULT_STEP_BASE = 100
DEFAULT_STEP_STRIDE = 100
DEFAULT_STEP_INCREMENT = 10
DEFAULT_HANDSHAKE_BASE = 1000
DEFAULT_ALARM_BASE = 2000
DEFAULT_ALARM_TIMER_BASE = 10
DEFAULT_ALARM_DELAY = 20
DEFAULT_MODES = {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"}

# 设备：SM/SD 必须排在 S 前面，否则 "SM402" 会被当成 "M402"
DEVICE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:SM|SD|ZR|X|Y|M|D|T|C|S|B|W|L|V|F)\d+(?:[.]\d+)?",
    re.IGNORECASE,
)
DEVICE_TOKEN_RE = re.compile(
    r"^(?:SM|SD|ZR|X|Y|M|D|T|C|S|B|W|L|V|F)\d+(?:[.]\d+)?$", re.IGNORECASE
)
PAREN_RE = re.compile(r"[（(]([^（()）]{1,40})[)）]")
ARROW_RE = re.compile(r"\s*(?:->|→|⇒|=>)\s*")
STEP_RANGE_RE = re.compile(r"步号段?\s*[：:]?\s*(\d{1,4})\s*[-~—–]\s*(\d{1,4})")
DELAY_RE = re.compile(
    r"(?:延时|延迟|等待|持续)\s*[：:]?\s*(\d+(?:[.]\d+)?)\s*(ms|毫秒|s|秒|S)?",
    re.IGNORECASE,
)
SECTION_TITLE_RE = re.compile(
    r"^\s*(?:[#>*\-\u2022•]+\s*)?(?:\d+[.、)）\]]\s*)?([^：:\n]{1,24})\s*[：:]\s*(.*)$"
)
INIT_PAIR_RE = re.compile(
    r"(?P<cond>(?:SM|SD|X|Y|M|D)\d+)\s*"
    r"(?P<verb>置位|置ON|SET|复位|RST|启动|输出)\s*"
    r"(?P<target>(?:S|M|Y|D)\d+)",
    re.IGNORECASE,
)
INIT_TARGET_RE = re.compile(
    r"(?P<verb>置位|置ON|SET|启动)\s*(?P<target>(?:S|M|Y)\d+)", re.IGNORECASE
)
CYLINDER_LINE_RE = re.compile(r"^\s*(?:气缸|液压缸|夹爪|气缸报警)\s*[：:]")
HANDSHAKE_TEXT_RE = re.compile(
    r"[A-Za-z0-9_\u4e00-\u9fff（）()+\-]+(?:->|→)[^\s，,;；、]+/[^\s，,;；、]+"
)
MASK_TOKEN_RE = re.compile(r"\x01(\d+)\x02")

ACTION_WORDS = (
    "启动", "置位", "复位", "输出", "关闭", "打开", "下降", "上升", "伸出", "缩回",
    "夹紧", "松开", "右移", "左移", "前进", "后退", "返回", "吸取", "吹气", "运行",
    "动作", "停止", "SET", "RST", "START", "STOP",
)
CONDITION_WORDS = (
    "到位", "原位", "直到", "等待", "检测", "信号", "完成", "可送料", "可放料",
    "可拿走", "收到", "允许", "确认", "条件", "若", "当",
)
RESET_WORDS = (
    "复位", "RST", "关闭", "断开", "OFF", "停止", "上升", "缩回", "松开",
    "左移", "后退", "返回", "关",
)
SET_WORDS = (
    "置位", "SET", "启动", "打开", "ON", "下降", "伸出", "夹紧", "右移",
    "前进", "输出",
)
SKIP_WORDS = ("完成", "回待机", "结束", "待机", "跳转", "循环", "暂停")
HANDSHAKE_HINTS = ("交接", "握手", "完成信号", "送料完成", "可送料信号")
TITLE_REJECT_WORDS = ("等待", "延时", "延迟", "到位", "原位", "直到", "条件")
HOME_WORDS = ("原位", "缩回", "返回", "后位", "左位", "关")
ON_WORDS = ("到位", "伸出", "前位", "右位", "下降", "上升", "开", "夹紧")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _as_int(value: Any, default: int) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _short(text: Any, limit: int = 48) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit]


def _dedupe(items: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _has_action_word(text: str) -> bool:
    upper = (text or "").upper()
    return any(word.upper() in upper for word in ACTION_WORDS)


def _has_condition_word(text: str) -> bool:
    return any(word in (text or "") for word in CONDITION_WORDS)


def _action_op(text: str) -> str:
    upper = (text or "").upper()
    if any(word.upper() in upper for word in RESET_WORDS):
        return "reset"
    return "set"


def _infer_role(device: str) -> str:
    match = re.match(r"^([A-Za-z]+)", str(device or ""))
    cls = match.group(1).upper() if match else ""
    if cls == "X":
        return "input"
    if cls == "Y":
        return "output"
    return "internal"


# ---------------------------------------------------------------------------
# IO / 风格 / 经验
# ---------------------------------------------------------------------------
def normalize_io(io_rows: Any) -> List[Dict[str, Any]]:
    """清洗 IO 表：只留合法软元件、补注释、补 role，去重。"""
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in io_rows or []:
        if isinstance(item, str):
            item = {"device": item, "comment": ""}
        if not isinstance(item, dict):
            continue
        device = str(item.get("device") or "").strip().upper()
        if not IO_DEVICE_RE.match(device) or device in seen:
            continue
        seen.add(device)
        comment = str(item.get("comment") or "").strip() or device
        role = str(item.get("role") or "").strip()
        if role not in VALID_ROLES:
            role = _infer_role(device)
        row: Dict[str, Any] = {"device": device, "comment": comment, "role": role}
        if item.get("type"):
            row["type"] = str(item.get("type"))
        out.append(row)
    return out


def style_baseline(style: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把 StyleProfile 压成规划器默认值；优先用 style/apply.py，缺失时回退画像字段。"""
    baseline: Dict[str, Any] = {
        "step_base": DEFAULT_STEP_BASE,
        "step_stride": DEFAULT_STEP_STRIDE,
        "step_increment": DEFAULT_STEP_INCREMENT,
        "handshake_base": DEFAULT_HANDSHAKE_BASE,
        "alarm_base": DEFAULT_ALARM_BASE,
        "alarm_timer_base": DEFAULT_ALARM_TIMER_BASE,
        "mode_devices": dict(DEFAULT_MODES),
        "style_id": None,
        "style_name": "",
    }
    if not isinstance(style, dict) or not style:
        return baseline

    applied: Dict[str, Any] = {}
    try:  # 局部 import：并行开发期 style/apply 可能尚未就绪
        from ..style.apply import generator_defaults  # type: ignore

        applied = generator_defaults(style) or {}
        if not isinstance(applied, dict):
            applied = {}
    except Exception:  # pragma: no cover - 兜底路径
        applied = {}

    strategy = style.get("step_strategy") or {}
    if not isinstance(strategy, dict):
        strategy = {}
    alarm = style.get("alarm") or {}
    if not isinstance(alarm, dict):
        alarm = {}

    baseline["step_base"] = _as_int(
        applied.get("step_base", strategy.get("station_start")), DEFAULT_STEP_BASE
    )
    baseline["step_stride"] = _as_int(
        applied.get("step_stride", strategy.get("station_stride")), DEFAULT_STEP_STRIDE
    )
    baseline["step_increment"] = _as_int(
        applied.get("step_increment", strategy.get("increment")), DEFAULT_STEP_INCREMENT
    )
    baseline["alarm_base"] = _as_int(
        applied.get("alarm_base", alarm.get("base")), DEFAULT_ALARM_BASE
    )
    baseline["alarm_timer_base"] = _as_int(
        applied.get("alarm_timer_base", alarm.get("timer_base")), DEFAULT_ALARM_TIMER_BASE
    )
    baseline["handshake_base"] = _as_int(
        applied.get("handshake_base"), DEFAULT_HANDSHAKE_BASE
    )

    modes = applied.get("modes")
    if not isinstance(modes, dict):
        modes = style.get("modes") or {}
    if isinstance(modes, dict):
        merged = dict(baseline["mode_devices"])
        for key, value in modes.items():
            device = value.get("device") if isinstance(value, dict) else value
            device = str(device or "").strip()
            if device and is_device(device):
                merged[str(key)] = device
        baseline["mode_devices"] = merged

    if style.get("id"):
        baseline["style_id"] = str(style.get("id"))
    baseline["style_name"] = str(style.get("name") or "")
    return baseline


def style_summary(style: Optional[Dict[str, Any]]) -> str:
    """风格摘要：优先 style/apply.planner_hint，其次 profile.summarize，最后本地兜底。"""
    if not isinstance(style, dict) or not style:
        return "（未使用风格画像，采用内置默认风格）"
    try:
        from ..style.apply import planner_hint  # type: ignore

        text = planner_hint(style)
        if text:
            return str(text)
    except Exception:  # pragma: no cover - 兜底路径
        pass
    try:
        from ..style.profile import summarize  # type: ignore

        text = summarize(style)
        if text:
            return str(text)
    except Exception:  # pragma: no cover - 兜底路径
        pass
    lines: List[str] = ["风格：%s" % (style.get("name") or "未命名")]
    titles = [
        str(item.get("title"))
        for item in (style.get("segments") or [])
        if isinstance(item, dict) and item.get("title")
    ]
    if titles:
        lines.append("段结构：%s" % " → ".join(titles[:20]))
    baseline = style_baseline(style)
    lines.append(
        "步号策略：起点 %d，步长 %d，步内增量 %d"
        % (baseline["step_base"], baseline["step_stride"], baseline["step_increment"])
    )
    lines.append("模式位：%s" % "，".join(
        "%s=%s" % (key, value) for key, value in baseline["mode_devices"].items()
    ))
    return "\n".join(lines)[:1200]


def apply_style_baseline(spec: Dict[str, Any], style: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把风格默认值合入 spec（LLM 路径也要保证模式位/步号策略可用）。"""
    if not isinstance(spec, dict):
        return spec
    baseline = style_baseline(style)
    modes = spec.setdefault("modes", {})
    if isinstance(modes, dict):
        for key, device in baseline["mode_devices"].items():
            if not modes.get(key):
                modes[key] = device
    common = spec.setdefault("common", {})
    if isinstance(common, dict):
        defaults = {
            "handshake_base": baseline["handshake_base"],
            "alarm_base": baseline["alarm_base"],
            "alarm_timer_base": baseline["alarm_timer_base"],
            "start_step": 0,
            "station_step_base": baseline["step_base"],
            "station_step_stride": baseline["step_stride"],
            "step_increment": baseline["step_increment"],
            "auto_rungs": True,
        }
        for key, value in defaults.items():
            if key not in common or common[key] is None:
                common[key] = value
            elif isinstance(value, int) and not isinstance(common[key], int):
                common[key] = _as_int(common[key], value)
    if baseline.get("style_id"):
        project = spec.setdefault("project", {})
        if isinstance(project, dict) and not project.get("style_profile"):
            project["style_profile"] = baseline["style_id"]
    return spec


def normalize_lessons(lessons: Any) -> List[Dict[str, Any]]:
    """把 feedback.store.lessons 输出（或简单字符串列表）统一成 [{category,message,examples}]。"""
    out: List[Dict[str, Any]] = []
    for item in lessons or []:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append({"category": "其他", "message": text, "examples": [], "count": 1})
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
        out.append({
            "category": category,
            "message": message,
            "examples": examples[:5],
            "count": _as_int(item.get("count"), max(1, len(examples) or 1)),
        })
    return out


def lesson_guidance(lessons: Any) -> Dict[str, List[str]]:
    """把历史经验转成"规划注意事项"，只做提示/假设，不臆造设备。"""
    notes: List[str] = []
    assumptions: List[str] = []
    for item in normalize_lessons(lessons):
        category = item["category"]
        message = item["message"]
        blob = category + message
        if any(word in blob for word in ("互锁", "interlock", "Interlock")):
            assumptions.append(
                "历史反馈（%s）：%s；请在动作步补充对侧原位/夹紧/安全互锁条件（规划器不臆造设备）"
                % (category, message)
            )
        elif any(word in blob for word in ("报警", "alarm", "Alarm")):
            notes.append("历史反馈（%s）：%s；本轮按 IO 气缸自动补报警段，请核对报警位/延时" % (category, message))
        elif any(word in blob for word in ("停止", "急停", "stop", "Stop")):
            assumptions.append(
                "历史反馈（%s）：%s；请确认停止/急停能复位运行步与输出" % (category, message)
            )
        elif any(word in blob for word in ("交接", "握手", "信号", "handshake")):
            assumptions.append(
                "历史反馈（%s）：%s；请核对段间交接/移位信号是否齐全" % (category, message)
            )
        elif any(word in blob for word in ("顺序", "结构", "段")):
            notes.append(
                "历史反馈（%s）：%s；本轮按 初始化→设备段→启动停止→报警 生成，请复核段顺序"
                % (category, message)
            )
        else:
            notes.append("历史反馈（%s）：%s" % (category, message))
    return {"notes": _dedupe(notes), "assumptions": _dedupe(assumptions)}


def apply_lessons_to_spec(spec: Dict[str, Any], lessons: Any) -> Dict[str, Any]:
    """把 lessons 写进 spec.notes / spec.assumptions（有缸时报警提示进 notes）。"""
    if not isinstance(spec, dict):
        return spec
    guidance = lesson_guidance(lessons)
    notes = list(guidance["notes"])
    assumptions = list(guidance["assumptions"])
    has_cylinders = bool((spec.get("alarms") or {}).get("cylinders"))
    if has_cylinders:
        notes = [text for text in notes if "未识别到气缸" not in text]
    else:
        assumptions.extend(
            text for text in notes
            if "报警" in text and "本轮按 IO 气缸" in text
        )
        notes = [text for text in notes if "本轮按 IO 气缸" not in text]
    spec.setdefault("notes", []).extend(notes)
    spec.setdefault("assumptions", []).extend(assumptions)
    return spec


# ---------------------------------------------------------------------------
# 文本保护 / 切分
# ---------------------------------------------------------------------------
def _protect_text(text: str, io_rows: Sequence[Dict[str, Any]]) -> Tuple[str, List[Tuple[str, str]]]:
    """把 IO 注释、'X->Y/Z' 式交接信号名替换成占位符，避免按箭头/斜杠误切。"""
    protected: List[Tuple[str, str]] = []
    text = str(text or "")
    rows = sorted(io_rows or [], key=lambda row: -len(str(row.get("comment") or "")))
    for row in rows:
        comment = str(row.get("comment") or "").strip()
        if len(comment) < 2 or "\x01" in comment:
            continue
        if comment not in text:
            continue
        placeholder = "\x01%d\x02" % len(protected)
        text = text.replace(comment, placeholder)
        protected.append((placeholder, comment))

    def _protect_handshake(match: "re.Match[str]") -> str:
        placeholder = "\x01%d\x02" % len(protected)
        protected.append((placeholder, match.group(0)))
        return placeholder

    text = HANDSHAKE_TEXT_RE.sub(_protect_handshake, text)
    return text, protected


def _unmask(text: str, protected: Sequence[Tuple[str, str]]) -> str:
    if not text:
        return ""

    def _sub(match: "re.Match[str]") -> str:
        index = int(match.group(1))
        if 0 <= index < len(protected):
            return protected[index][1]
        return match.group(0)

    return MASK_TOKEN_RE.sub(_sub, text)


def _needs_merge(left: str, right: str) -> bool:
    """判断 '等待 压装' + '入料/可送料' 这种被箭头误切的交接信号是否应合并。"""
    if not any(word in left for word in ("等待", "直到", "收到", "接收到", "检测到", "信号")):
        return False
    if not right:
        return False
    if _has_action_word(right) or DELAY_RE.search(right):
        return False
    if PAREN_RE.search(right) or DEVICE_RE.search(right):
        return False
    return True


def _split_phases(text: str) -> List[str]:
    """按行 + 箭头切相；被箭头误切的交接信号名再合并回去。"""
    phases: List[str] = []
    for line in str(text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in ARROW_RE.split(line) if part.strip()]
        merged: List[str] = []
        for part in parts:
            if merged and _needs_merge(merged[-1], part):
                merged[-1] = merged[-1] + "->" + part
            else:
                merged.append(part)
        for part in merged:
            if phases and _needs_merge(phases[-1], part):
                phases[-1] = phases[-1] + "->" + part
            else:
                phases.append(part)
    return phases


def _split_clauses(phase: str) -> List[str]:
    if "->" in phase or "→" in phase:
        pattern = r"[,，;；、\n]+"
    else:
        pattern = r"[,，;；、/\n]+"
    return [piece.strip() for piece in re.split(pattern, phase) if piece.strip()]


def _split_mixed_clause(text: str) -> List[str]:
    """'夹紧(Y2) 到位(X7)' → ['夹紧(Y2)', '到位(X7)']。"""
    match = re.search(r"(?=到位|直到|等待|检测到|原位|可送料|可放料|收到|信号)", text or "")
    if not match or match.start() <= 0:
        return [text]
    head = text[: match.start()].strip(" ,，;；:：、")
    tail = text[match.start():].strip()
    if not head or not tail:
        return [text]
    if not (_has_action_word(head) and not _has_condition_word(head)):
        return [text]
    if not (PAREN_RE.search(head) or DEVICE_RE.search(head)
            or PAREN_RE.search(tail) or DEVICE_RE.search(tail)):
        return [text]
    return [head, tail]


# ---------------------------------------------------------------------------
# 需求分段
# ---------------------------------------------------------------------------
def _clean_title(text: str) -> str:
    return str(text or "").strip().strip(" \t*#_`>•-").strip()


def _looks_like_title(candidate: str) -> bool:
    title = _clean_title(candidate)
    if not title or len(title) > 24:
        return False
    if any(word in title for word in TITLE_REJECT_WORDS):
        return False
    if DEVICE_RE.search(title) and not STEP_RANGE_RE.search(title):
        return False
    return True


def _section_kind(title: str) -> str:
    text = str(title or "")
    lower = text.lower()
    if any(word in text for word in ("初始化", "初始", "上电")) or lower in ("init", "initial"):
        return "init"
    if any(word in text for word in ("报警", "警报")) or "alarm" in lower:
        return "alarm"
    if any(
        word in text
        for word in ("启动停止", "启动/停止", "公共", "模式", "手动", "自动", "安全", "急停", "复位", "停止")
    ):
        return "common"
    return "station"


def _prepare_text(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    # 单行/分号串联需求：'; 段名:' 处断成新行
    def _replace(match: "re.Match[str]") -> str:
        candidate = match.group(1)
        if _looks_like_title(candidate):
            return "\n" + candidate + match.group(2)
        return match.group(0)

    text = re.sub(r"[；;]\s*([^：:；;，,\n]{1,24})([：:])", _replace, text)
    return text


def _is_cylinder_entry_line(line: str) -> bool:
    return bool(CYLINDER_LINE_RE.match(line or ""))


def _split_sections(requirement: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """需求 → [{title,kind,rest,lines}] + 开头说明行。"""
    sections: List[Dict[str, Any]] = []
    preamble: List[str] = []
    current: Optional[Dict[str, Any]] = None
    for raw_line in _prepare_text(requirement).split("\n"):
        line = raw_line.strip()
        if not line or line.startswith(">") or line.startswith("```") or line.startswith("|"):
            continue
        if line.startswith("---"):
            continue
        if _is_cylinder_entry_line(line):
            if current is None or current.get("kind") != "alarm":
                current = {"title": "报警", "kind": "alarm", "rest": "", "lines": []}
                sections.append(current)
            current["lines"].append(line)
            continue
        match = SECTION_TITLE_RE.match(line)
        if match and _looks_like_title(match.group(1)):
            title = _clean_title(match.group(1))
            current = {
                "title": title,
                "kind": _section_kind(title),
                "rest": match.group(2).strip(),
                "lines": [],
            }
            sections.append(current)
            continue
        if current is None:
            preamble.append(line)
        else:
            current["lines"].append(line)
    return sections, preamble


def _section_text(section: Dict[str, Any]) -> str:
    parts: List[str] = []
    rest = str(section.get("rest") or "").strip()
    if rest:
        parts.append(rest)
    for line in section.get("lines") or []:
        text = str(line or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _find_step_range(section: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """从段标题/段正文里找“步号段 100-199”这类显式步号区间。"""
    haystack = str(section.get("title") or "") + "\n" + _section_text(section)
    match = STEP_RANGE_RE.search(haystack)
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if start > end:
        start, end = end, start
    return start, end


def _merge_preamble(
    sections: List[Dict[str, Any]],
    preamble: List[str],
    assumptions: List[str],
    notes: List[str],
) -> List[Dict[str, Any]]:
    text = "\n".join(line for line in preamble if str(line).strip()).strip()
    if not text:
        return sections
    has_device = bool(re.search(r"(?:SM|SD|X|Y|M|D|T|C|S)\d+", text, re.IGNORECASE))
    if has_device and any(word in text for word in ("启动", "停止")):
        if not any(section.get("kind") == "common" for section in sections):
            sections.insert(0, {"title": "公共", "kind": "common", "rest": "", "lines": list(preamble)})
            notes.append("需求开头识别到启动/停止说明，已生成公共段")
    elif has_device:
        sections.insert(0, {"title": "主流程", "kind": "station", "rest": "", "lines": list(preamble)})
        notes.append("需求开头未命名内容按第一个工位段处理")
    else:
        notes.append("已忽略需求开头 %d 行说明性文字" % len([line for line in preamble if str(line).strip()]))
    return sections


# ---------------------------------------------------------------------------
# 设备解析
# ---------------------------------------------------------------------------
def _resolve_token(token: str, io_spec: Dict[str, Any]) -> Optional[str]:
    token = str(token or "").strip()
    if not token:
        return None
    if is_device(token):
        return token.upper()
    found = lookup_device(io_spec, token)
    return found or None


def _find_devices(
    text: str,
    io_spec: Dict[str, Any],
    io_rows: Sequence[Dict[str, Any]],
) -> List[str]:
    """从一段文字里解析设备：括号优先 → 设备号正则 → IO 注释包含 → lookup 宽松匹配。"""
    text = str(text or "")
    found: List[str] = []

    def _add(device: Optional[str]) -> None:
        if device and device not in found:
            found.append(device)

    for content in PAREN_RE.findall(text):
        _add(_resolve_token(content, io_spec))
    for match in DEVICE_RE.finditer(text):
        _add(match.group(0).upper())
    if found:
        return found

    matches: List[Tuple[int, str]] = []
    for row in io_rows or []:
        comment = str(row.get("comment") or "").strip()
        if len(comment) >= 2 and comment in text:
            matches.append((len(comment), str(row.get("device") or "")))
    for _, device in sorted(matches, reverse=True):
        _add(device)
    if found:
        return found

    cleaned = re.sub(r"[，,。;；:：、（）()\s]+", "", text)
    if cleaned:
        _add(lookup_device(io_spec, cleaned))
    return found


def _find_input_devices(
    io_rows: Sequence[Dict[str, Any]],
    keywords: Sequence[str],
    exclude: Sequence[str] = (),
) -> List[str]:
    out: List[str] = []
    for row in io_rows or []:
        device = str(row.get("device") or "").upper()
        role = row.get("role") or ""
        if role != "input" and not device.startswith("X"):
            continue
        comment = str(row.get("comment") or "")
        if not any(word in comment for word in keywords):
            continue
        if any(word in comment for word in exclude):
            continue
        if device not in out:
            out.append(device)
    return out


def _find_input(io_rows: Sequence[Dict[str, Any]], keywords: Sequence[str],
                exclude: Sequence[str] = ()) -> Optional[str]:
    devices = _find_input_devices(io_rows, keywords, exclude)
    return devices[0] if devices else None


# ---------------------------------------------------------------------------
# 延时 / 定时器
# ---------------------------------------------------------------------------
def _parse_delay(text: str) -> Optional[Tuple[str, int]]:
    match = DELAY_RE.search(str(text or ""))
    if not match:
        return None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None
    unit = (match.group(2) or "s").lower()
    if unit in ("ms", "毫秒"):
        ticks = int(math.ceil(value / 100.0))
    else:
        ticks = int(math.ceil(value * 10))
    return match.group(0).strip(), max(1, ticks)


def _allocate_delay_timer(timer_state: Dict[str, int], baseline: Dict[str, Any],
                          assumptions: List[str]) -> str:
    index = int(timer_state.get("delay_index") or 0)
    timer_state["delay_index"] = index + 1
    alarm_timer_base = max(1, _as_int(baseline.get("alarm_timer_base"), DEFAULT_ALARM_TIMER_BASE))
    if index < alarm_timer_base:
        number = index
    else:  # 避开报警定时器区，从 T100 起继续分配
        number = 100 + (index - alarm_timer_base)
    return "T%d" % number


# ---------------------------------------------------------------------------
# 段构建
# ---------------------------------------------------------------------------
def _build_init_segment(
    sections: Sequence[Dict[str, Any]],
    common: Dict[str, Any],
    assumptions: List[str],
) -> Dict[str, Any]:
    start_step = _as_int((common or {}).get("start_step"), 0)
    start_device = "S%d" % start_step
    default_rung = {
        "comment": "初始脉冲置待机步",
        "instructions": [
            {"op": "LD", "operands": ["SM402"]},
            {"op": "SET", "operands": [start_device]},
        ],
    }
    if not sections:
        return {"title": "初始化", "kind": "init", "rungs": [default_rung]}

    section = sections[0]
    normalized = ARROW_RE.sub(" ", _section_text(section))
    rungs: List[Dict[str, Any]] = []
    for match in INIT_PAIR_RE.finditer(normalized):
        verb = match.group("verb").upper()
        op = "SET" if verb in ("置位", "置ON", "SET", "启动", "输出") else "RST"
        rungs.append({
            "comment": match.group(0).strip(),
            "instructions": [
                {"op": "LD", "operands": [match.group("cond").upper()]},
                {"op": op, "operands": [match.group("target").upper()]},
            ],
        })
    if not rungs:
        # 兼容只写 "置位 S0" / "SET S0" 的初始化段：默认用首次扫描脉冲 SM402 作条件
        for match in INIT_TARGET_RE.finditer(normalized):
            target = match.group("target").upper()
            if target in ("S0", start_device) or target.startswith(("S", "M")):
                rungs.append({
                    "comment": match.group(0).strip(),
                    "instructions": [
                        {"op": "LD", "operands": ["SM402"]},
                        {"op": "SET", "operands": [target]},
                    ],
                })

    sets_start = any(
        instruction.get("op") == "SET" and start_device in (instruction.get("operands") or [])
        for rung in rungs
        for instruction in rung.get("instructions") or []
    )
    if not rungs:
        rungs = [default_rung]
        assumptions.append("初始化段未解析出有效指令，已使用默认 SM402→SET %s" % start_device)
    elif not sets_start:
        rungs.append(default_rung)
        assumptions.append("初始化段未设置待机步 %s，已补充 SM402→SET %s" % (start_device, start_device))
    return {
        "title": str(section.get("title") or "初始化").strip() or "初始化",
        "kind": "init",
        "rungs": rungs,
    }


def _build_rung_from_clause(clause: str, io_spec: Dict[str, Any],
                            io_rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    devices = _find_devices(clause, io_spec, io_rows)
    if len(devices) < 2:
        return None
    op = _action_op(clause).upper()
    instructions = [{"op": "LD", "operands": [devices[0]]}]
    for target in devices[1:]:
        instructions.append({"op": op, "operands": [target]})
    return {"comment": _short(clause, 60), "instructions": instructions}


def _build_common_segment(
    sections: Sequence[Dict[str, Any]],
    io_spec: Dict[str, Any],
    io_rows: Sequence[Dict[str, Any]],
    modes: Dict[str, str],
    start_input: Optional[str],
    stop_input: Optional[str],
    assumptions: List[str],
) -> Optional[Dict[str, Any]]:
    if sections:
        rungs: List[Dict[str, Any]] = []
        title = str(sections[0].get("title") or "启动停止").strip() or "启动停止"
        for section in sections:
            for line in re.split(r"\n+", _section_text(section)):
                for clause in re.split(r"[,，;；、]+", line):
                    clause = clause.strip()
                    if not clause:
                        continue
                    rung = _build_rung_from_clause(clause, io_spec, io_rows)
                    if rung:
                        rungs.append(rung)
                    else:
                        assumptions.append("公共段片段未解析：%s" % _short(clause))
        if rungs:
            return {"title": title, "kind": "common", "rungs": rungs}
        return None

    rungs = []
    run_device = modes.get("run") or DEFAULT_MODES["run"]
    auto_device = modes.get("auto") or DEFAULT_MODES["auto"]
    if start_input:
        rungs.append({
            "comment": "启动 → 运行中/自动",
            "instructions": [
                {"op": "LD", "operands": [start_input]},
                {"op": "SET", "operands": [run_device]},
                {"op": "SET", "operands": [auto_device]},
            ],
        })
    if stop_input:
        rungs.append({
            "comment": "停止 → 复位运行/自动",
            "instructions": [
                {"op": "LD", "operands": [stop_input]},
                {"op": "RST", "operands": [run_device]},
                {"op": "RST", "operands": [auto_device]},
            ],
        })
    if not rungs:
        return None
    assumptions.append(
        "启动/停止按钮由 IO 表注释识别：启动=%s，停止=%s，待现场确认"
        % (start_input or "未识别", stop_input or "未识别")
    )
    return {"title": "启动停止", "kind": "common", "rungs": rungs}


def _is_pure_skip(text: str) -> bool:
    cleaned = re.sub(r"[，,。;；:：、\s]+", "", str(text or ""))
    if not cleaned:
        return True
    if any(word in cleaned for word in ("信号", "等待", "直到", "到位", "检测", "收到")):
        return False
    for word in SKIP_WORDS:
        if cleaned == word:
            return True
        if cleaned.startswith(word):
            return True
    return False


def _is_input_device(device: str, io_rows: Sequence[Dict[str, Any]]) -> bool:
    """X 点或 IO 表 role=input 的设备只能作为条件，不能被动作驱动。"""
    device = str(device or "").upper()
    if device.startswith("X"):
        return True
    for row in io_rows or []:
        if str(row.get("device") or "").upper() == device:
            return str(row.get("role") or "") == "input"
    return False


def _classify_clause(text: str, io_spec: Dict[str, Any],
                     io_rows: Sequence[Dict[str, Any]]) -> str:
    if _parse_delay(text):
        return "delay"
    if _is_pure_skip(text):
        return "skip"
    devices = _find_devices(text, io_spec, io_rows)
    has_condition = _has_condition_word(text)
    has_action = _has_action_word(text)
    if devices:
        if all(_is_input_device(device, io_rows) for device in devices):
            return "condition"
        if has_condition and not has_action:
            return "condition"
        return "action"
    if has_condition and not has_action:
        return "condition"
    if has_action:
        return "action"
    return "unknown"


def _detect_handshake(
    section: Dict[str, Any],
    io_spec: Dict[str, Any],
    io_rows: Sequence[Dict[str, Any]],
    assumptions: List[str],
) -> Optional[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for line in re.split(r"\n+", _section_text(section)):
        if not any(word in line for word in HANDSHAKE_HINTS):
            continue
        devices = _find_devices(line, io_spec, io_rows)
        if not devices:
            assumptions.append(
                "需求提到交接信号“%s”，但 IO 表中未找到对应设备，请补充" % _short(line, 40)
            )
            continue
        device = devices[0]
        if device in seen:
            continue
        seen.add(device)
        out.append({"device": device, "comment": _short(line, 40)})
    return {"out": out} if out else None


def _build_station_segment(
    section: Dict[str, Any],
    io_spec: Dict[str, Any],
    io_rows: Sequence[Dict[str, Any]],
    start: int,
    end: int,
    baseline: Dict[str, Any],
    timer_state: Dict[str, int],
    assumptions: List[str],
) -> Dict[str, Any]:
    title = str(section.get("title") or "设备段").strip() or "设备段"
    body = STEP_RANGE_RE.sub(" ", _section_text(section))
    masked, protected = _protect_text(body, io_rows)

    phases: List[Dict[str, Any]] = []
    for phase_text in _split_phases(masked):
        actions: List[Dict[str, Any]] = []
        conditions: List[str] = []
        condition_texts: List[str] = []
        delays: List[Tuple[str, int]] = []
        texts: List[str] = []
        for clause in _split_clauses(phase_text):
            raw = _unmask(clause, protected).strip()
            if not raw:
                continue
            texts.append(raw)
            for sub in _split_mixed_clause(raw):
                kind = _classify_clause(sub, io_spec, io_rows)
                if kind == "delay":
                    parsed = _parse_delay(sub)
                    if parsed:
                        delays.append(parsed)
                    continue
                devices = _find_devices(sub, io_spec, io_rows)
                if kind == "condition":
                    if devices:
                        for device in devices:
                            if device not in conditions:
                                conditions.append(device)
                        condition_texts.append(sub)
                    else:
                        assumptions.append(
                            "未解析出转移条件：%s（请补充设备号或 IO 注释）" % _short(sub)
                        )
                elif kind == "action":
                    if devices:
                        actions.append({_action_op(sub): list(devices), "comment": _short(sub, 60)})
                    else:
                        assumptions.append(
                            "未解析出动作设备：%s（请补充设备号或 IO 注释）" % _short(sub)
                        )
                elif kind == "skip":
                    continue
                else:
                    if not _is_pure_skip(sub):
                        assumptions.append("未识别需求片段：%s" % _short(sub))
        phases.append({
            "actions": actions,
            "conditions": conditions,
            "condition_texts": condition_texts,
            "delays": delays,
            "text": "；".join(texts),
        })

    steps: List[Dict[str, Any]] = []
    skipped_texts: List[str] = []
    index = 0
    while index < len(phases):
        phase = phases[index]
        index += 1
        actions = list(phase["actions"])
        conditions = list(phase["conditions"])
        condition_texts = list(phase["condition_texts"])
        for delay_text, ticks in phase["delays"]:
            ticks = min(ticks, 32767)
            timer = _allocate_delay_timer(timer_state, baseline, assumptions)
            actions.append({
                "instructions": [{"op": "OUT", "operands": [timer, "K%d" % ticks]}],
                "comment": "%s（%s，K%d，100ms 基准）" % (delay_text, timer, ticks),
            })
            if timer not in conditions:
                conditions.append(timer)
            condition_texts.append(delay_text)
        if not actions and not conditions:
            skipped_texts.append(phase["text"])
            continue
        # 动作步 + 紧随其后的纯条件步 → 合并为一个 SFC 步
        if actions and not conditions and index < len(phases):
            following = phases[index]
            if following["conditions"] and not following["actions"] and not following["delays"]:
                conditions.extend(following["conditions"])
                condition_texts.extend(following["condition_texts"])
                index += 1
        steps.append({
            "actions": actions,
            "conditions": conditions,
            "condition_texts": condition_texts,
            "comment": phase["text"],
        })

    increment = max(1, _as_int(baseline.get("step_increment"), DEFAULT_STEP_INCREMENT))
    if not steps:
        steps = [{
            "actions": [],
            "conditions": [],
            "condition_texts": [],
            "comment": "占位：未识别到动作，请补充需求",
        }]
        assumptions.append("工位段“%s”未识别到动作，已生成占位步，请补充需求" % title)

    if start > end:
        start, end = end, start
    for offset, step in enumerate(steps):
        step["step"] = start + offset * increment
    last_step = start + (len(steps) - 1) * increment
    if last_step > end:
        assumptions.append(
            "工位段“%s”步数超出步号段 %d-%d，已扩展结束步号到 %d，请确认"
            % (title, start, end, last_step)
        )
        end = last_step

    built_steps: List[Dict[str, Any]] = []
    for offset, step in enumerate(steps):
        next_step = steps[offset + 1]["step"] if offset + 1 < len(steps) else None
        transition: Dict[str, Any] = {
            "conditions": list(dict.fromkeys(step["conditions"])),
            "next": next_step,
            "comment": _short("；".join(step["condition_texts"]), 60),
        }
        if not transition["conditions"] and next_step is not None:
            assumptions.append(
                "工位段“%s”第 %d 步未识别到转移条件，将按无条件跳转生成，请补充"
                % (title, step["step"])
            )
        built_step: Dict[str, Any] = {
            "step": step["step"],
            "actions": step["actions"],
            "transition": transition,
        }
        if step["comment"]:
            built_step["comment"] = _short(step["comment"], 60)
        built_steps.append(built_step)

    if skipped_texts and built_steps:
        last_transition = built_steps[-1]["transition"]
        merged_comment = "；".join(
            text for text in [last_transition.get("comment") or ""] + skipped_texts if text
        )
        last_transition["comment"] = _short(merged_comment, 60)

    segment: Dict[str, Any] = {
        "title": title,
        "kind": "station",
        "step_range": [start, end],
        "steps": built_steps,
    }
    handshake = _detect_handshake(section, io_spec, io_rows, assumptions)
    if handshake:
        segment["handshake"] = handshake
    return segment


# ---------------------------------------------------------------------------
# 报警（气缸）
# ---------------------------------------------------------------------------
def _parse_cylinder_line(
    line: str,
    io_spec: Dict[str, Any],
    io_rows: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    text = re.sub(r"^\s*(?:气缸|液压缸|夹爪|气缸报警)\s*[：:]\s*", "", str(line or "")).strip()
    if not text:
        return None
    match = re.search(r"[(（]\s*(Y\d+)\s*[)）]", text) or re.search(r"(?<![A-Za-z0-9])(Y\d+)", text)
    output: Optional[str] = None
    name = ""
    rest = text
    if match:
        output = match.group(1).upper()
        name = text[: match.start()].strip(" ,，;；:：、")
        rest = text[match.end():]
    name = re.sub(r"^(?:气缸|液压缸|气缸报警)\s*[：:]?\s*", "", name).strip(" ,，;；:：、")
    if not name:
        name = "气缸"
    if not output:
        return None
    on_sensor: Optional[str] = None
    home_sensor: Optional[str] = None
    pending = ""
    for chunk in re.split(r"[\s,，;；、]+", rest):
        if not chunk:
            continue
        if any(word in chunk for word in HOME_WORDS):
            pending = "home"
        elif any(word in chunk for word in ON_WORDS):
            pending = "on"
        x_match = re.search(r"(?<![A-Za-z0-9])(X\d+)", chunk)
        if x_match:
            device = x_match.group(1).upper()
            if pending == "home" and not home_sensor:
                home_sensor = device
            elif pending == "on" and not on_sensor:
                on_sensor = device
            pending = ""
    if not on_sensor and not home_sensor:
        derived = {str(item["output"]): item for item in _cylinders_from_io(io_rows)}
        if output in derived:
            on_sensor = derived[output].get("on_sensor")
            home_sensor = derived[output].get("home_sensor")
    return {"name": name, "output": output, "on_sensor": on_sensor, "home_sensor": home_sensor}


def _cylinders_from_io(io_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    outputs = [row for row in io_rows or [] if str(row.get("device") or "").upper().startswith("Y")]
    inputs = [row for row in io_rows or [] if str(row.get("device") or "").upper().startswith("X")]
    cylinders: List[Dict[str, Any]] = []
    for row in outputs:
        name = str(row.get("comment") or "").strip()
        if not name or not any(word in name for word in ("气缸", "液压缸", "夹爪", "缸")):
            continue
        on_sensor: Optional[str] = None
        home_sensor: Optional[str] = None
        for input_row in inputs:
            comment = str(input_row.get("comment") or "").strip()
            if not comment or name not in comment:
                continue
            device = str(input_row.get("device") or "").upper()
            if any(word in comment for word in HOME_WORDS) and not home_sensor:
                home_sensor = device
            elif any(word in comment for word in ON_WORDS) and not on_sensor:
                on_sensor = device
        cylinders.append({
            "name": name,
            "output": str(row.get("device") or "").upper(),
            "on_sensor": on_sensor,
            "home_sensor": home_sensor,
        })
    return cylinders


def _derive_alarm_cylinders(
    alarm_sections: Sequence[Dict[str, Any]],
    io_rows: Sequence[Dict[str, Any]],
    io_spec: Dict[str, Any],
    assumptions: List[str],
) -> List[Dict[str, Any]]:
    derived = _cylinders_from_io(io_rows)
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for cylinder in derived:
        output = str(cylinder.get("output") or "").upper()
        if output and output not in merged:
            merged[output] = cylinder
            order.append(output)

    for section in alarm_sections:
        for line in re.split(r"\n+", _section_text(section)):
            line = line.strip()
            if not line:
                continue
            cylinder = _parse_cylinder_line(line, io_spec, io_rows)
            if not cylinder:
                if "报警" in line:
                    continue
                assumptions.append("报警段片段未解析：%s" % _short(line))
                continue
            output = cylinder["output"]
            if output in merged:
                merged[output].update({key: value for key, value in cylinder.items() if value})
            else:
                merged[output] = cylinder
                order.append(output)
    return [merged[output] for output in order]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def plan_deterministic(
    requirement: str,
    io: Any,
    style: Optional[Dict[str, Any]] = None,
    lessons: Any = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """需求 + IO + 风格 + 历史经验 → ProgramSpec（确定性，无网络）。"""
    if requirement is None:
        requirement = ""
    if not isinstance(requirement, str):
        requirement = str(requirement)

    io_rows = normalize_io(io)
    io_spec: Dict[str, Any] = {"io": io_rows}
    baseline = style_baseline(style)
    assumptions: List[str] = []
    notes: List[str] = []

    # 0) 领域模板优先：常见需求（双向红绿灯等）直接展开为完整自动流程，
    #    避免规则解析在自然语言上退化成"只有几个空 STL 步"。
    domain = detect_domain(requirement)
    if domain:
        spec = build_domain_spec(domain, requirement, io_rows, name=name)
        if style:
            apply_style_baseline(spec, style)
        apply_lessons_to_spec(spec, lessons)
        from ..spec import validate_spec as _validate_spec
        _validate_spec(spec)
        return spec

    if not io_rows:
        assumptions.append("未提供 IO 表：设备仅按需求原文解析，请补充 IO 表后复核")
    else:
        missing_comments = sum(1 for row in io_rows if row["comment"] == row["device"])
        if missing_comments:
            assumptions.append("IO 表有 %d 行缺少注释，已用设备号占位" % missing_comments)

    project_name = str(name or "").strip() or "自动生成项目"
    spec = default_spec(project_name, io=io_rows)
    spec["modes"] = dict(baseline["mode_devices"])
    common = spec["common"]
    common["start_step"] = _as_int(common.get("start_step"), 0)
    common["station_step_base"] = baseline["step_base"]
    common["station_step_stride"] = baseline["step_stride"]
    common["step_increment"] = baseline["step_increment"]
    common["handshake_base"] = baseline["handshake_base"]
    common["alarm_base"] = baseline["alarm_base"]
    common["alarm_timer_base"] = baseline["alarm_timer_base"]
    common["auto_rungs"] = True
    if baseline.get("style_id"):
        spec["project"]["style_profile"] = baseline["style_id"]

    sections, preamble = _split_sections(requirement)
    sections = _merge_preamble(sections, preamble, assumptions, notes)

    init_sections = [section for section in sections if section.get("kind") == "init"]
    station_sections = [section for section in sections if section.get("kind") == "station"]
    common_sections = [section for section in sections if section.get("kind") == "common"]
    alarm_sections = [section for section in sections if section.get("kind") == "alarm"]

    if not station_sections:
        station_sections = [{
            "title": "主流程",
            "kind": "station",
            "rest": "",
            "lines": [],
            "_placeholder": True,
        }]
        assumptions.append("需求中未识别到工位段，已生成占位工位段“主流程”，请补充动作")

    start_input = _find_input(io_rows, ("启动",), exclude=("停止", "急停"))
    stop_input = _find_input(io_rows, ("停止", "急停"), exclude=("启动",))
    start_step = _as_int(common.get("start_step"), 0)

    segments: List[Dict[str, Any]] = [_build_init_segment(init_sections, common, assumptions)]
    timer_state: Dict[str, int] = {"delay_index": 0}
    next_base = baseline["step_base"]
    stride = max(1, _as_int(baseline["step_stride"], DEFAULT_STEP_STRIDE))

    for index, section in enumerate(station_sections):
        explicit_range = None
        if not section.get("_placeholder"):
            explicit_range = _find_step_range(section)
        if explicit_range:
            start, end = explicit_range
        else:
            start, end = next_base, next_base + stride - 1
        segment = _build_station_segment(
            section, io_spec, io_rows, start, end, baseline, timer_state, assumptions
        )
        entry_conditions = ["S%d" % start_step]
        if index == 0:
            if start_input:
                entry_conditions.append(start_input)
        else:
            assumptions.append(
                "多工位段“%s”的进入/交接条件未识别，暂按待机步 S%d 直接进入，请补充互锁或交接信号"
                % (segment.get("title"), start_step)
            )
        segment["entry_conditions"] = entry_conditions
        segments.append(segment)
        next_base = max(int(end) + 1, int(start) + stride)

    common_segment = _build_common_segment(
        common_sections, io_spec, io_rows, spec["modes"], start_input, stop_input, assumptions
    )
    if common_segment:
        segments.append(common_segment)

    alarm_cylinders = _derive_alarm_cylinders(alarm_sections, io_rows, io_spec, assumptions)
    reset_device = stop_input
    alarms = {
        "enabled": bool(alarm_cylinders),
        "base": baseline["alarm_base"],
        "timer_base": baseline["alarm_timer_base"],
        "delay": DEFAULT_ALARM_DELAY,
        "reset_device": reset_device,
        "cylinders": alarm_cylinders,
    }
    if alarm_cylinders:
        assumptions.append(
            "气缸报警：每缸到位/原位各 1 个报警位（M%d 起）与 1 个延时定时器（T%d 起），"
            "延时 2s（K%d），待确认"
            % (baseline["alarm_base"], baseline["alarm_timer_base"], DEFAULT_ALARM_DELAY)
        )
        if reset_device:
            assumptions.append("停止按钮 %s 兼作报警清除，待现场确认" % reset_device)
    elif alarm_sections:
        assumptions.append(
            "需求提到报警，但 IO 表/报警段未识别到气缸（注释需含“气缸/缸/夹爪”），报警段未启用"
        )

    spec["segments"] = segments
    spec["alarms"] = alarms
    if style:
        notes.insert(0, "已应用风格画像：%s" % (baseline.get("style_name") or "未命名"))

    apply_lessons_to_spec(spec, lessons)
    spec["assumptions"] = _dedupe(assumptions + list(spec.get("assumptions") or []))
    spec["notes"] = _dedupe(notes + list(spec.get("notes") or []))

    # 如果规则解析完全没有提取到动作，明确报错（而不是给一两个空 STL 步的垃圾初稿）。
    station_steps = [
        step
        for segment in segments
        if segment.get("kind") == "station"
        for step in (segment.get("steps") or [])
    ]

    def _step_has_content(step: Dict[str, Any]) -> bool:
        for action in step.get("actions") or []:
            if any(action.get(key) for key in ("set", "reset", "out", "instructions")):
                return True
        return False

    if station_steps and not any(_step_has_content(step) for step in station_steps):
        raise PlannerError(
            "规则解析无法从这段需求中提取动作（需求更像自然语言描述，而不是“工位: 动作 → 条件”格式）。"
            "请改用 AI Agent / LLM 生成 ProgramSpec，或把需求改写成：\n"
            "  初始化: SM402 置位 S0\n"
            "  工位名: 动作(Y0)启动 → 到位(X3) → 下一步动作 …\n"
            "本次未生成任何程序，避免输出不可用初稿。"
        )

    from ..spec import validate_spec

    validate_spec(spec)
    return spec
