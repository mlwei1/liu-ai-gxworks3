"""设备注释收集：IO 表 + 模式位 + 握手信号 + 报警/定时器。"""
from __future__ import annotations

from typing import Any, Dict, List

from ..ir import Program
from ..spec import is_device

MODE_COMMENTS = {"run": "运行中", "stop": "停止中", "auto": "自动", "manual": "手动",
                 "reset": "复位", "alarm_reset": "报警清除"}


def _put(comments: Dict[str, str], device: Any, comment: str) -> None:
    if not device or not comment:
        return
    device = str(device).strip()
    if is_device(device) and device not in comments:
        comments[device] = comment.strip()


def collect_comments(spec: Dict[str, Any], program: Program) -> Dict[str, str]:
    comments: Dict[str, str] = {}

    for item in spec.get("io") or []:
        _put(comments, item.get("device"), str(item.get("comment") or ""))

    for key, device in (spec.get("modes") or {}).items():
        _put(comments, device, MODE_COMMENTS.get(key, str(key)))

    for segment in spec.get("segments") or []:
        title = str(segment.get("title") or "工位")
        handshake = segment.get("handshake") or {}
        for direction, suffix in (("in", "进入条件"), ("out", "完成信号")):
            for item in handshake.get(direction) or []:
                if isinstance(item, dict):
                    device, comment = item.get("device"), item.get("comment")
                else:
                    device, comment = item, ""
                _put(comments, device, comment or "%s/%s" % (title, suffix))
        for device in handshake.get("shift") or []:
            _put(comments, device, "移位标志：%s" % title)

    alarms = spec.get("alarms") or {}
    if alarms.get("enabled"):
        base = int(alarms.get("base") or 2000)
        used = 0
        timer_base = int(alarms.get("timer_base") or 10)
        for cylinder in alarms.get("cylinders") or []:
            name = str(cylinder.get("name") or "气缸")
            _put(comments, cylinder.get("alarm_on") or "M%d" % (base + used),
                 "气缸报警：%s到位" % name)
            used += 1
            _put(comments, cylinder.get("alarm_home") or "M%d" % (base + used),
                 "气缸报警：%s原位" % name)
            used += 1
        for offset in range(max(1, len(alarms.get("cylinders") or []) * 2)):
            _put(comments, "T%d" % (timer_base + offset), "报警延时")

    # 生成结果里出现的定时器，补可见注释（系统继电器 SM/SD 由 GX 自带注释，不覆盖）
    for instruction in program.all_instructions():
        for operand in instruction.operands:
            if not is_device(operand):
                continue
            if operand.upper().startswith("T") and operand not in comments:
                _put(comments, operand, "延时")
    return comments
