"""StyleProfile（用户风格画像）→ 生成器默认值 / 规划器提示。

对应 docs/INTERFACES.md 的 ``gx3gen.style.apply``：生成器/规划器只通过本模块消费画像。

    generator_defaults(profile) -> dict
        键固定为：segment_order / step_base / step_stride / modes /
                  alarm_base / alarm_timer_base / handshake_base / naming
        画像为 None / 字段缺失 / 类型不对时返回安全默认值，保证生成器可以直接使用。

    planner_hint(profile) -> str
        ≤1200 字的中文摘要，注入 LLM/规则规划器，说明"学到了什么、生成时怎么用"。

画像本身由 ``miner.mine`` 产出（结构见 spec/style_profile.schema.json），也允许人工编辑。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .miner import normalize_width, segment_category

# 生成器安全默认值（与 spec.default_spec() 的 common 缺省保持一致）
DEFAULT_SEGMENT_ORDER = ["初始化", "设备段", "报警"]
DEFAULT_STEP_BASE = 100
DEFAULT_STEP_STRIDE = 100
DEFAULT_STEP_INCREMENT = 10
DEFAULT_MODES = {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"}
DEFAULT_ALARM_BASE = 2000
DEFAULT_ALARM_TIMER_BASE = 10
DEFAULT_HANDSHAKE_BASE = 1000

MODE_KEYS = ("run", "stop", "auto", "manual", "reset")

_DEVICE_RE = re.compile(r"^[A-Za-z]{1,3}\d+(?:\.\d+)?$")
_NAMING_KEYS = (
    "segment_prefixes",
    "title_templates",
    "comment_patterns",
    "alarm_templates",
    "handshake_templates",
)

_NAME_HINT_MAX = 1200


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _int_or(value: Any, default: int, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if minimum is not None and value < minimum:
        return default
    return value


def _string_list(values: Any, limit: int = 12) -> List[str]:
    """把任意 list（str / {"pattern": ...} / {"key": ...}）整理成去重字符串列表。"""
    out: List[str] = []
    for value in values or []:
        candidate = ""
        if isinstance(value, str):
            candidate = value.strip()
        elif isinstance(value, dict):
            for key in ("pattern", "template", "key", "description", "name"):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    candidate = item.strip()
                    break
        if candidate and candidate not in out:
            out.append(candidate)
        if len(out) >= limit:
            break
    return out


def _profile_modes(profile: Dict[str, Any]) -> Dict[str, str]:
    """画像 modes（{key: {device, comment}} 或 {key: device}）→ {key: device}。"""
    result = dict(DEFAULT_MODES)
    raw = profile.get("modes")
    if not isinstance(raw, dict):
        return result
    for key in MODE_KEYS:
        item = raw.get(key)
        device = ""
        if isinstance(item, dict):
            candidate = item.get("device")
            device = candidate.strip() if isinstance(candidate, str) else ""
        elif isinstance(item, str):
            device = item.strip()
        if device and _DEVICE_RE.match(device):
            result[key] = device
    return result


def _range_starts(profile: Dict[str, Any]) -> List[int]:
    starts: List[int] = []
    for item in profile.get("step_ranges") or []:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        if isinstance(start, int) and not isinstance(start, bool) and start >= 0:
            starts.append(start)
    return starts


def _step_base(profile: Dict[str, Any]) -> int:
    strategy = profile.get("step_strategy")
    if isinstance(strategy, dict):
        value = strategy.get("station_start")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    starts = _range_starts(profile)
    return min(starts) if starts else DEFAULT_STEP_BASE


def _step_stride(profile: Dict[str, Any]) -> int:
    strategy = profile.get("step_strategy")
    if isinstance(strategy, dict):
        value = strategy.get("station_stride")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    starts = sorted(set(_range_starts(profile)))
    diffs = [later - earlier for earlier, later in zip(starts, starts[1:]) if later > earlier]
    if diffs:
        counts: Dict[int, int] = {}
        for diff in diffs:
            counts[diff] = counts.get(diff, 0) + 1
        value, count = max(counts.items(), key=lambda item: (item[1], -item[0]))
        if count >= 2:
            return value
    return DEFAULT_STEP_STRIDE


def _step_increment(profile: Dict[str, Any]) -> int:
    strategy = profile.get("step_strategy")
    if isinstance(strategy, dict):
        return _int_or(strategy.get("increment"), DEFAULT_STEP_INCREMENT, minimum=1)
    return DEFAULT_STEP_INCREMENT


# ---------------------------------------------------------------------------
# 段顺序
# ---------------------------------------------------------------------------
def segment_order(profile: Optional[Dict[str, Any]]) -> List[str]:
    """画像段结构 → 生成器应采用的规范段类别顺序（中文类别名）。"""
    if not isinstance(profile, dict):
        return list(DEFAULT_SEGMENT_ORDER)
    ordered: List[str] = []
    for segment in profile.get("segments") or []:
        title = segment.get("title") if isinstance(segment, dict) else ""
        category = segment_category(title)
        if category and category not in ordered:
            ordered.append(category)
    if not ordered:
        return list(DEFAULT_SEGMENT_ORDER)
    # 规范化骨架：初始化始终第一、设备段紧随其后，其余保持用户程序里的相对顺序
    if "初始化" in ordered:
        ordered.remove("初始化")
    ordered.insert(0, "初始化")
    if "设备段" in ordered:
        ordered.remove("设备段")
    ordered.insert(1, "设备段")
    if "报警" not in ordered:
        index = len(ordered)
        for position, category in enumerate(ordered):
            if category in ("配方", "手动", "驱动", "显示"):
                index = position
                break
        ordered.insert(index, "报警")
    return ordered


# ---------------------------------------------------------------------------
# 命名（生成器可直接使用的模板字符串）
# ---------------------------------------------------------------------------
def _naming(profile: Dict[str, Any]) -> Dict[str, List[str]]:
    raw = profile.get("naming") if isinstance(profile.get("naming"), dict) else {}
    alarm = profile.get("alarm") if isinstance(profile.get("alarm"), dict) else {}
    handshake = profile.get("handshake") if isinstance(profile.get("handshake"), dict) else {}
    return {
        "segment_prefixes": _string_list(raw.get("segment_prefixes")),
        "title_templates": _string_list(raw.get("title_templates")),
        "comment_patterns": _string_list(raw.get("comment_patterns")),
        "alarm_templates": _string_list(alarm.get("naming")),
        "handshake_templates": _string_list(handshake.get("naming")),
    }


# ---------------------------------------------------------------------------
# 对生成器的唯一入口
# ---------------------------------------------------------------------------
def generator_defaults(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把画像翻译成生成器可直接消费的默认值 dict（键固定，缺省安全）。"""
    defaults: Dict[str, Any] = {
        "segment_order": list(DEFAULT_SEGMENT_ORDER),
        "step_base": DEFAULT_STEP_BASE,
        "step_stride": DEFAULT_STEP_STRIDE,
        "modes": dict(DEFAULT_MODES),
        "alarm_base": DEFAULT_ALARM_BASE,
        "alarm_timer_base": DEFAULT_ALARM_TIMER_BASE,
        "handshake_base": DEFAULT_HANDSHAKE_BASE,
        "naming": {key: [] for key in _NAMING_KEYS},
    }
    if not isinstance(profile, dict):
        return defaults

    defaults["segment_order"] = segment_order(profile)
    defaults["step_base"] = _step_base(profile)
    defaults["step_stride"] = _step_stride(profile)
    defaults["modes"] = _profile_modes(profile)

    alarm = profile.get("alarm") if isinstance(profile.get("alarm"), dict) else {}
    defaults["alarm_base"] = _int_or(alarm.get("base"), DEFAULT_ALARM_BASE, minimum=0)
    defaults["alarm_timer_base"] = _int_or(
        alarm.get("timer_base"), DEFAULT_ALARM_TIMER_BASE, minimum=0)

    handshake = profile.get("handshake") if isinstance(profile.get("handshake"), dict) else {}
    defaults["handshake_base"] = _int_or(
        handshake.get("base"), DEFAULT_HANDSHAKE_BASE, minimum=0)

    defaults["naming"] = _naming(profile)
    return defaults


# ---------------------------------------------------------------------------
# 规划器提示
# ---------------------------------------------------------------------------
def _default_hint() -> str:
    modes = "，".join("%s=%s" % (key, device) for key, device in DEFAULT_MODES.items())
    lines = [
        "【风格画像】未提供历史程序，按 gx3gen 安全默认值生成。",
        "段顺序：初始化 → 设备段 → 报警（可覆盖）。",
        "步号：工位起始 S%d，段步长 %d，步内增量 %d；第 i 个工位段从 S(base+i*stride) 开始。"
        % (DEFAULT_STEP_BASE, DEFAULT_STEP_STRIDE, DEFAULT_STEP_INCREMENT),
        "模式位：%s。" % modes,
        "报警：M%d 起，定时器 T%d 起；每气缸两报警位（原位/到位）。"
        % (DEFAULT_ALARM_BASE, DEFAULT_ALARM_TIMER_BASE),
        "握手：M%d 起。" % DEFAULT_HANDSHAKE_BASE,
        "命名：段标题写 [Title]段名；注释用简体中文短句；握手注释写“源->目标/动作”。",
        "结构：设备段以 STL 开始、RETSTL 结束；全局最后 END。",
    ]
    return _limit("\n".join(lines))


def _limit(text: str, max_chars: int = _NAME_HINT_MAX) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def planner_hint(profile: Optional[Dict[str, Any]]) -> str:
    """把画像压缩成 ≤1200 字中文摘要，供 LLM/规则规划器注入。"""
    if not isinstance(profile, dict) or not profile:
        return _default_hint()

    defaults = generator_defaults(profile)
    name = str(profile.get("name") or "未命名风格")
    segments = [item for item in (profile.get("segments") or []) if isinstance(item, dict)]
    step_ranges = [item for item in (profile.get("step_ranges") or []) if isinstance(item, dict)]
    increment = _step_increment(profile)

    lines: List[str] = []
    lines.append("【风格画像】%s：%d 段，其中 %d 个含 STL 步号段。"
                 % (name, len(segments), len(step_ranges)))

    titles = [str(item.get("title")) for item in segments if item.get("title")]
    if titles:
        lines.append("段标题顺序：" + " → ".join(titles[:16]) + ("…" if len(titles) > 16 else ""))
    lines.append("生成段顺序：" + " → ".join(defaults["segment_order"]))
    lines.append(
        "步号：工位起始 S%d，段步长 %d，步内增量 %d；第 i 个设备段建议从 S%d 开始。"
        % (defaults["step_base"], defaults["step_stride"], increment,
           defaults["step_base"] + increment)
    )
    if step_ranges:
        samples = [
            "%s:S%s-S%s" % (item.get("segment"), item.get("start"), item.get("end"))
            for item in step_ranges[:6]
        ]
        lines.append("步号段样例：" + "；".join(samples) + ("…" if len(step_ranges) > 6 else ""))
    lines.append("模式位：" + "，".join(
        "%s=%s" % (key, device) for key, device in defaults["modes"].items()) + "。")
    lines.append("报警：M%d 起，定时器 T%d 起。"
                 % (defaults["alarm_base"], defaults["alarm_timer_base"]))
    lines.append("握手：M%d 起。" % defaults["handshake_base"])

    naming = defaults["naming"]
    if naming["segment_prefixes"]:
        lines.append("段标题前缀：" + "、".join(naming["segment_prefixes"][:8]))
    if naming["title_templates"]:
        lines.append("段标题模板：" + "、".join(naming["title_templates"][:5]))
    if naming["alarm_templates"]:
        lines.append("报警注释模板：" + " / ".join(naming["alarm_templates"][:4]))
    if naming["handshake_templates"]:
        lines.append("握手注释模板：" + " / ".join(naming["handshake_templates"][:4]))

    patterns = naming["comment_patterns"][:5]
    if patterns:
        lines.append("注释习惯：" + "；".join(patterns))

    comment_stats = profile.get("comment_stats")
    if isinstance(comment_stats, dict) and comment_stats.get("total"):
        lines.append(
            "注释样式：%d 条，最长 %s 字，CJK 占比 %s，%s全角写法。"
            % (comment_stats.get("total"), comment_stats.get("max_len"),
               comment_stats.get("cjk_ratio"),
               "含" if comment_stats.get("has_fullwidth") else "不含")
        )

    mix = profile.get("instruction_mix")
    if isinstance(mix, dict) and mix:
        top = sorted(mix.items(), key=lambda item: -int(item[1]))[:8]
        lines.append("常用指令：" + "，".join("%s×%s" % (op, count) for op, count in top))

    device_ranges = profile.get("device_ranges")
    if isinstance(device_ranges, dict) and device_ranges:
        parts = []
        for cls in ("X", "Y", "M", "D", "T", "S"):
            info = device_ranges.get(cls)
            if isinstance(info, dict) and info.get("max") is not None:
                parts.append("%s%d-%s" % (cls, info.get("min", 0), info.get("max")))
        if parts:
            lines.append("已用设备范围：" + "，".join(parts) + "；新项目沿用同类分配。")

    lines.append("生成要求：沿用上述段顺序/步号/模式位/报警与握手基址；段标题与注释按学到的命名模板写；"
                 "不复制具体逻辑，只沿用结构习惯。")
    return _limit("\n".join(lines))


__all__ = [
    "generator_defaults",
    "planner_hint",
    "segment_order",
    "DEFAULT_SEGMENT_ORDER",
    "DEFAULT_STEP_BASE",
    "DEFAULT_STEP_STRIDE",
    "DEFAULT_MODES",
    "DEFAULT_ALARM_BASE",
    "DEFAULT_ALARM_TIMER_BASE",
    "DEFAULT_HANDSHAKE_BASE",
]
