"""StyleProfile 数据结构、摘要与合并（学习逻辑在 miner.py / apply.py）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

PROFILE_VERSION = "0.1"

DEFAULT_MODES = {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"}


def empty_profile(name: str = "") -> Dict[str, Any]:
    return {
        "profile_version": PROFILE_VERSION,
        "name": name or "默认风格",
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source_files": [],
        "segments": [],
        "step_ranges": [],
        "modes": dict(DEFAULT_MODES),
        "alarm": {"base": 2000, "timer_base": 10, "naming": []},
        "handshake": {"naming": [], "examples": []},
        "naming": {},
        "device_ranges": {},
        "instruction_mix": {},
        "comment_stats": {},
        "step_strategy": {"station_start": 100, "station_stride": 100, "increment": 10},
        "templates": [],
    }


def _pick_most_common(values: List[str], default: str = "") -> str:
    counts: Dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return default
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _pick_most_common_int(values: List[int], default: int) -> int:
    counts: Dict[int, int] = {}
    for value in values:
        if value is None:
            continue
        counts[int(value)] = counts.get(int(value), 0) + 1
    if not counts:
        return default
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _merge_mode_maps(profiles: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    collected: Dict[str, List[Dict[str, str]]] = {}
    for profile in profiles:
        for key, value in (profile.get("modes") or {}).items():
            if isinstance(value, dict):
                collected.setdefault(key, []).append(value)
    out: Dict[str, Dict[str, str]] = {}
    for key, items in collected.items():
        device = _pick_most_common([item.get("device", "") for item in items])
        comment = _pick_most_common([item.get("comment", "") for item in items])
        out[key] = {"device": device or DEFAULT_MODES.get(key, ""), "comment": comment}
    for key, device in DEFAULT_MODES.items():
        out.setdefault(key, {"device": device, "comment": ""})
    return out


def merge_profiles(profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    profiles = [profile for profile in (profiles or []) if profile]
    if not profiles:
        return empty_profile()
    if len(profiles) == 1:
        return dict(profiles[0])
    names = " + ".join(str(profile.get("name") or "未命名") for profile in profiles)
    merged = empty_profile("合并风格（%s）" % names)
    merged["source_files"] = [name for profile in profiles for name in (profile.get("source_files") or [])]

    # 段顺序：按出现次数取最常见顺序
    sequences: List[str] = []
    for profile in profiles:
        titles = [item.get("title") for item in (profile.get("segments") or []) if item.get("title")]
        if titles:
            sequences.append("|".join(titles))
    if sequences:
        best = _pick_most_common(sequences)
        merged["segments"] = [{"order": index + 1, "title": title, "network_count": 0}
                              for index, title in enumerate(best.split("|"))]

    # 步号区间直接合并
    step_ranges: List[Dict[str, Any]] = []
    for profile in profiles:
        step_ranges.extend(profile.get("step_ranges") or [])
    merged["step_ranges"] = step_ranges

    merged["modes"] = _merge_mode_maps(profiles)

    # 报警
    bases = [int((profile.get("alarm") or {}).get("base")) for profile in profiles
             if (profile.get("alarm") or {}).get("base") is not None]
    timer_bases = [int((profile.get("alarm") or {}).get("timer_base")) for profile in profiles
                   if (profile.get("alarm") or {}).get("timer_base") is not None]
    merged["alarm"] = {
        "base": _pick_most_common_int(bases, 2000),
        "timer_base": _pick_most_common_int(timer_bases, 10),
        "naming": [pattern for profile in profiles
                   for pattern in ((profile.get("alarm") or {}).get("naming") or [])][:20],
    }

    # 握手（含基址；旧画像没有 base 时回退 1000）
    handshake_bases = [int((profile.get("handshake") or {}).get("base")) for profile in profiles
                       if isinstance((profile.get("handshake") or {}).get("base"), int)]
    merged["handshake"] = {
        "base": _pick_most_common_int(handshake_bases, 1000),
        "naming": [item for profile in profiles
                   for item in ((profile.get("handshake") or {}).get("naming") or [])][:20],
        "examples": [item for profile in profiles
                     for item in ((profile.get("handshake") or {}).get("examples") or [])][:30],
    }

    # 命名
    naming: Dict[str, Any] = {}
    for profile in profiles:
        for key, value in (profile.get("naming") or {}).items():
            if isinstance(value, list):
                naming.setdefault(key, [])
                for item in value:
                    if item not in naming[key]:
                        naming[key].append(item)
            elif key not in naming:
                naming[key] = value
    merged["naming"] = naming

    # 设备范围取并集
    ranges: Dict[str, Dict[str, int]] = {}
    for profile in profiles:
        for cls, info in (profile.get("device_ranges") or {}).items():
            current = ranges.setdefault(cls, {"min": 10 ** 9, "max": -1, "count": 0})
            current["min"] = min(current["min"], int(info.get("min", 0)))
            current["max"] = max(current["max"], int(info.get("max", 0)))
            current["count"] += int(info.get("count", 0))
    merged["device_ranges"] = ranges

    # 指令统计累加
    mix: Dict[str, int] = {}
    for profile in profiles:
        for op, count in (profile.get("instruction_mix") or {}).items():
            mix[op] = mix.get(op, 0) + int(count)
    merged["instruction_mix"] = mix

    # 步号策略取最常见
    strategies = [profile.get("step_strategy") or {} for profile in profiles]
    merged["step_strategy"] = {
        "station_start": min((int(item.get("station_start", 100)) for item in strategies), default=100),
        "station_stride": _pick_most_common([str(item.get("station_stride", 100)) for item in strategies], "100"),
        "increment": _pick_most_common([str(item.get("increment", 10)) for item in strategies], "10"),
    }
    merged["step_strategy"]["station_stride"] = int(merged["step_strategy"]["station_stride"])
    merged["step_strategy"]["increment"] = int(merged["step_strategy"]["increment"])
    merged["templates"] = [item for profile in profiles for item in (profile.get("templates") or [])][:50]
    merged["comment_stats"] = profiles[0].get("comment_stats") or {}
    return merged


def summarize(profile: Dict[str, Any], max_chars: int = 1200) -> str:
    """给 UI/规划器看的中文摘要。"""
    if not profile:
        return "（未使用风格画像，采用内置默认风格）"
    lines: List[str] = []
    lines.append("风格：%s" % profile.get("name", "未命名"))
    segments = profile.get("segments") or []
    if segments:
        titles = [item.get("title") for item in segments if item.get("title")]
        lines.append("段结构（%d 段）：%s" % (len(titles), " → ".join(titles[:20])))
    step_ranges = profile.get("step_ranges") or []
    if step_ranges:
        sample = ["%s:%s-%s" % (item.get("segment"), item.get("start"), item.get("end"))
                  for item in step_ranges[:8]]
        lines.append("SFC 步号段：%s" % "；".join(sample))
    modes = profile.get("modes") or {}
    if modes:
        lines.append("模式位：%s" % "，".join(
            "%s=%s(%s)" % (key, value.get("device"), value.get("comment", ""))
            for key, value in modes.items() if isinstance(value, dict)))
    alarm = profile.get("alarm") or {}
    if alarm:
        lines.append("报警：基址 M%s，定时器 T%s" % (alarm.get("base", 2000), alarm.get("timer_base", 10)))
    handshake = (profile.get("handshake") or {}).get("examples") or []
    if handshake:
        lines.append("握手信号样例：%s" % "；".join(handshake[:5]))
    naming = profile.get("naming") or {}
    if naming:
        lines.append("命名规律：%s" % "，".join(
            "%s=%s" % (key, ("、".join(value[:6]) if isinstance(value, list) else value))
            for key, value in naming.items()))
    mix = profile.get("instruction_mix") or {}
    if mix:
        top = sorted(mix.items(), key=lambda kv: -kv[1])[:10]
        lines.append("常用指令：%s" % "，".join("%s×%d" % item for item in top))
    device_ranges = profile.get("device_ranges") or {}
    if device_ranges:
        lines.append("设备范围：%s" % "，".join(
            "%s %s-%s(%d)" % (cls, info.get("min"), info.get("max"), info.get("count", 0))
            for cls, info in device_ranges.items()))
    text = "\n".join(lines)
    return text[:max_chars]
