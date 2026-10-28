"""轻量检查：只出告警，不阻塞生成（本期验证由工程师在 GX Works3 接手）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .ir import Program
from .spec import DEVICE_RE, is_device

X_Y_RE = re.compile(r"^([XY])(\d+)$")


def _known_devices(spec: Dict[str, Any]) -> Dict[str, str]:
    known: Dict[str, str] = {}
    for item in spec.get("io") or []:
        if item.get("device"):
            known[str(item["device"]).upper()] = str(item.get("comment") or "IO")
    for key, device in (spec.get("modes") or {}).items():
        if device:
            known[str(device).upper()] = str(key)
    for segment in spec.get("segments") or []:
        handshake = segment.get("handshake") or {}
        for direction in ("in", "out"):
            for item in handshake.get(direction) or []:
                device = item.get("device") if isinstance(item, dict) else item
                if device:
                    known[str(device).upper()] = "握手信号"
        for device in handshake.get("shift") or []:
            known[str(device).upper()] = "移位标志"
    alarms = spec.get("alarms") or {}
    at_base = int(alarms.get("base") or 2000)
    timer_base = int(alarms.get("timer_base") or 10)
    for cylinder in alarms.get("cylinders") or []:
        for key in ("output", "on_sensor", "home_sensor"):
            if cylinder.get(key):
                known[str(cylinder[key]).upper()] = "报警检测"
    # 自动分配的报警位/定时器（最多 100 个）
    for offset in range(100):
        known.setdefault("M%d" % (at_base + offset), "报警/内部")
        known.setdefault("T%d" % (timer_base + offset), "报警延时")
    return known


def _is_system(device: str) -> bool:
    return device.upper().startswith(("SM", "SD"))


def lint_program(program: Program, spec: Dict[str, Any],
                 comments: Dict[str, str] | None = None) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    comments = comments or {}
    known = _known_devices(spec)

    used: List[str] = []
    stl_count = 0
    retstl_count = 0
    out_targets: Dict[str, int] = {}
    for instruction in program.all_instructions():
        op = (instruction.op or "").upper()
        if op == "STL":
            stl_count += 1
        if op in ("RETSTL", "STLRET"):
            retstl_count += 1
        for operand in instruction.operands:
            if not is_device(operand):
                continue
            device = operand.upper()
            if device not in used:
                used.append(device)
            if op == "OUT":
                out_targets[device] = out_targets.get(device, 0) + 1

    for device in used:
        upper = device.upper()
        if upper.startswith("S") and re.match(r"^S\d+$", upper):
            continue  # 步号
        match = X_Y_RE.match(upper)
        if match:
            digits = match.group(2)
            if any(char in "89" for char in digits) or int(digits) > 15:
                issues.append({
                    "severity": "warning", "code": "FX5U_XY_ADDRESS",
                    "message": "X/Y %s 超出 FX5U-32MR 16 点范围或含非法八进制数字（GX 可能报 0x12011067）；请核对基座工程点数与地址写法" % upper,
                })
            continue
        if _is_system(upper):
            continue
        if upper in known or upper in comments:
            continue
        if upper.startswith("M") and int(re.sub(r"\D", "", upper) or 0) >= 5000:
            issues.append({
                "severity": "warning", "code": "UNDEFINED_DEVICE",
                "message": "%s 不在 IO 表/模式位/握手信号中，GX 转换或可编译，但语义不明确" % upper,
            })
            continue
        issues.append({
            "severity": "warning", "code": "UNDEFINED_DEVICE",
            "message": "%s 未在 IO 表或配置中定义/注释" % upper,
        })

    # STL 配对：有 STL 的段应该以 RETSTL 结束（一个 RETSTL 可结束多步的 STL 块）
    for segment in program.segments:
        ops = [(instruction.op or "").upper() for rung in segment.rungs for instruction in rung.instructions]
        if "STL" in ops and "RETSTL" not in ops and "STLRET" not in ops:
            issues.append({
                "severity": "warning", "code": "STL_PAIR",
                "message": "段“%s”含 STL 但没有 RETSTL，GX 转换可能报错（STL 块需 RETSTL 结束）" % (segment.title or "无标题"),
            })

    if retstl_count > stl_count:
        issues.append({
            "severity": "warning", "code": "STL_PAIR",
            "message": "RETSTL 数量(%d)多于 STL 数量(%d)，请检查是否有孤立 RETSTL" % (retstl_count, stl_count),
        })

    for device, count in out_targets.items():
        if count > 1:
            issues.append({
                "severity": "warning", "code": "DUPLICATE_OUT",
                "message": "%s 被 OUT 指令重复输出 %d 次（双线圈），请确认是否符合预期" % (device, count),
            })

    # 段步号范围重叠
    ranges = []
    for segment in (spec.get("segments") or []):
        if segment.get("kind") == "station" and segment.get("step_range"):
            start, end = segment["step_range"]
            ranges.append((int(start), int(end), str(segment.get("title") or "")))
    ranges.sort()
    for index in range(1, len(ranges)):
        if ranges[index][0] <= ranges[index - 1][1]:
            issues.append({
                "severity": "error", "code": "STEP_RANGE_OVERLAP",
                "message": "步号段重叠：%s(%d-%d) 与 %s(%d-%d)" % (
                    ranges[index - 1][2], ranges[index - 1][0], ranges[index - 1][1],
                    ranges[index][2], ranges[index][0], ranges[index][1]),
            })

    # 注释覆盖率
    devices = [device for device in used if DEVICE_RE.match(device) and not device.startswith("S")]
    missing = [device for device in devices if device not in comments and device not in known]
    if missing:
        issues.append({
            "severity": "suggestion", "code": "COMMENT_COVERAGE",
            "message": "%d 个设备没有注释：%s" % (len(missing), "、".join(missing[:10])),
        })

    return issues


def lint_summary(issues: List[Dict[str, str]]) -> str:
    if not issues:
        return "轻量检查：无告警"
    errors = sum(1 for item in issues if item.get("severity") == "error")
    warnings = sum(1 for item in issues if item.get("severity") == "warning")
    suggestions = sum(1 for item in issues if item.get("severity") == "suggestion")
    return "轻量检查：%d 错误 / %d 告警 / %d 建议" % (errors, warnings, suggestions)
