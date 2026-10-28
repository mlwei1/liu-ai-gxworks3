"""ProgramSpec 质量门槛：防止"只有一两个空 STL 步"的垃圾程序被当成生成成功。

规则分两类：
1. 结构规则（总是检查）：每个步必须有动作；转移必须有条件；有工位段且非空；
2. 需求规则（提供 requirement 时）：循环/定时/启停/红绿灯/手动/报警等关键词必须落实。

设计原则：只拦截"明显不成立"的情况，不追求完美逻辑（最终由工程师在 GX 接手）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


class QualityError(RuntimeError):
    """生成前质量检查未通过。"""

    def __init__(self, message: str, quality: Dict[str, Any]):
        super().__init__(message)
        self.quality = quality


PROCESS_KEYWORDS = (
    "自动", "流程", "循环", "周期", "顺序", "工位", "动作", "启动", "停止",
    "传送", "传送带", "气缸", "电机", "电机", "检测", "报警", "红绿灯", "交通灯",
    "信号灯", "灯", "阀", "泵", "机械手", "取放", "装配", "上料", "下料",
)
LOOP_KEYWORDS = ("循环", "周期", "周而复始", "反复", "连续运行", "自动循环", "往复")
TIMER_KEYWORDS = ("延时", "定时", "时间", "秒", "s)", "分钟")
START_KEYWORDS = ("启动", "开始", "开机")
STOP_KEYWORDS = ("停止", "急停", "暂停")
LAMP_KEYWORDS = ("红绿灯", "交通灯", "信号灯", "红灯", "绿灯", "黄灯", "灯")
MANUAL_KEYWORDS = ("手动", "自动模式", "手/自动", "手自动")
ALARM_KEYWORDS = ("报警", "故障", "异常", "检测")


def _looks_process(text: str) -> bool:
    text = str(text or "")
    if len(text.strip()) < 4:
        return False
    return any(keyword in text for keyword in PROCESS_KEYWORDS)


def _steps(station: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [step for step in (station.get("steps") or []) if isinstance(step, dict)]


def _step_actions(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [action for action in (step.get("actions") or []) if isinstance(action, dict)]


def _step_instructions(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for action in _step_actions(step):
        out.extend(action.get("instructions") or [])
    return out


def _iter_all_instructions(spec: Dict[str, Any]) -> Iterable[Tuple[str, List[str]]]:
    for segment in spec.get("segments") or []:
        for rung in segment.get("rungs") or []:
            for instruction in rung.get("instructions") or []:
                if isinstance(instruction, dict):
                    yield str(instruction.get("op") or "").upper(), list(instruction.get("operands") or [])
        for step in segment.get("steps") or []:
            for action in _step_actions(step):
                for instruction in action.get("instructions") or []:
                    if isinstance(instruction, dict):
                        yield str(instruction.get("op") or "").upper(), list(instruction.get("operands") or [])


def _iter_actions(spec: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for segment in spec.get("segments") or []:
        for step in segment.get("steps") or []:
            for action in _step_actions(step):
                yield action


def _has_cycle(stations: List[Dict[str, Any]]) -> bool:
    for station in stations:
        steps = _steps(station)
        if len(steps) < 2:
            continue
        numbers = [int(step.get("step", 0)) for step in steps]
        first = min(numbers)
        for step in steps:
            transition = step.get("transition") or {}
            next_step = transition.get("next")
            if next_step is None:
                continue
            try:
                next_value = int(next_step)
                current_value = int(step.get("step", 0))
            except (TypeError, ValueError):
                continue
            if next_value <= current_value and next_value >= first:
                return True
    return False


def _has_timer(spec: Dict[str, Any]) -> bool:
    for op, operands in _iter_all_instructions(spec):
        if op in ("OUT", "OUTP") and operands and str(operands[0]).upper().startswith("T"):
            return True
        if op in ("TON", "TOF", "TMON", "TIMER"):
            return True
        if any(str(item).upper().startswith("K") for item in operands):
            return True
    return False


def _has_start_stop(spec: Dict[str, Any]) -> bool:
    modes = spec.get("modes") or {}
    run_device = modes.get("run")
    if not run_device:
        return False
    set_seen = False
    reset_seen = False
    for op, operands in _iter_all_instructions(spec):
        if not operands or str(operands[0]).upper() != str(run_device).upper():
            continue
        if op == "SET":
            set_seen = True
        elif op == "RST":
            reset_seen = True
    for action in _iter_actions(spec):
        for device in action.get("set") or []:
            if str(device).upper() == str(run_device).upper():
                set_seen = True
        for device in action.get("reset") or []:
            if str(device).upper() == str(run_device).upper():
                reset_seen = True
    return set_seen and reset_seen


def _output_devices(spec: Dict[str, Any]) -> List[str]:
    devices: List[str] = []
    for item in spec.get("io") or []:
        device = str(item.get("device") or "").upper()
        if device.startswith("Y") and device not in devices:
            devices.append(device)
    return devices


def _lamp_outputs(spec: Dict[str, Any]) -> List[str]:
    lamps: List[str] = []
    for item in spec.get("io") or []:
        device = str(item.get("device") or "").upper()
        comment = str(item.get("comment") or "")
        if device.startswith("Y") and any(color in comment for color in ("红", "黄", "绿", "灯")):
            if device not in lamps:
                lamps.append(device)
    return lamps


def check_quality(spec: Any, requirement: str = "") -> Dict[str, Any]:
    """返回 {ok, errors, warnings, suggestions, stats}。"""
    errors: List[str] = []
    warnings: List[str] = []
    suggestions: List[str] = []
    text = str(requirement or "")
    process_like = _looks_process(text)

    if not isinstance(spec, dict):
        return {"ok": False, "errors": ["spec 必须是 JSON 对象"], "warnings": [],
                "suggestions": [], "stats": {}}

    segments = [segment for segment in spec.get("segments") or [] if isinstance(segment, dict)]
    stations = [segment for segment in segments if segment.get("kind") == "station"]
    station_steps = [step for station in stations for step in _steps(station)]
    total_instructions = sum(1 for _ in _iter_all_instructions(spec))

    if not segments:
        errors.append("没有任何段（segments 为空）")
    if process_like and not stations:
        errors.append("需求是流程类描述，但规划结果没有设备段/自动流程（只有初始化或语句段）")
    if stations:
        if not station_steps:
            errors.append("设备段没有步骤（steps 为空）")
        if process_like and len(station_steps) < 2:
            errors.append("自动流程过于简单：只有 %d 步（至少需要 2 步动作/转移）" % len(station_steps))
        for station in stations:
            for step in _steps(station):
                step_no = step.get("step", "?")
                actions = _step_actions(step)
                has_action = bool(actions) or bool(step.get("actions"))
                if not has_action:
                    errors.append("步 S%s 没有任何动作（actions 为空）" % step_no)
                transition = step.get("transition") or {}
                next_step = transition.get("next")
                conditions = [item for item in (transition.get("conditions") or []) if str(item).strip()]
                if next_step is not None and not conditions:
                    errors.append("步 S%s 的转移没有条件，却要跳到 S%s" % (step_no, next_step))
        if process_like and len(station_steps) < 4 and any(word in text for word in LAMP_KEYWORDS):
            errors.append("红绿灯/灯控程序至少需要 4 个相位（当前 %d 步）" % len(station_steps))

    if process_like:
        if any(word in text for word in LOOP_KEYWORDS) and not _has_cycle(stations):
            errors.append("需求要求自动循环，但自动流程没有回到起始步的转移（形不成循环）")
        if any(word in text for word in TIMER_KEYWORDS) and not _has_timer(spec):
            errors.append("需求提到延时/时间，但程序里没有定时器（OUT T）或 K 时间常数")
        if any(word in text for word in START_KEYWORDS + STOP_KEYWORDS) and not _has_start_stop(spec):
            errors.append("需求提到启动/停止，但程序里没有对运行位（modes.run）的 SET/RST 处理")
        if any(word in text for word in LAMP_KEYWORDS):
            lamps = _lamp_outputs(spec)
            if len(lamps) < 3:
                errors.append("红绿灯程序至少需要 3 个灯输出（红/黄/绿），当前识别到 %d 个" % len(lamps))
        if any(word in text for word in MANUAL_KEYWORDS):
            modes = spec.get("modes") or {}
            if not modes.get("manual") or not modes.get("auto"):
                warnings.append("需求提到手动/自动，但 spec.modes 缺少 auto/manual 模式位")
            if not any("手动" in str(segment.get("title") or "") for segment in segments):
                suggestions.append("可补充“手动”段（点动/单步/复位），本期模板未自动生成")
        if any(word in text for word in ALARM_KEYWORDS):
            alarms = spec.get("alarms") or {}
            has_alarm_segment = any("报警" in str(segment.get("title") or "") for segment in segments)
            if not alarms.get("enabled") and not has_alarm_segment:
                warnings.append("需求提到报警/故障，但 spec 未启用报警段或报警配置")

    if not spec.get("io"):
        warnings.append("IO 表为空：设备号只能来自需求文本，建议补充 IO 表")
    if total_instructions and total_instructions < 8 and process_like:
        errors.append("程序规模过小（只有 %d 条指令），不符合流程类需求" % total_instructions)

    stats = {
        "segments": len(segments),
        "station_steps": len(station_steps),
        "instructions": total_instructions,
        "has_cycle": _has_cycle(stations),
        "has_timer": _has_timer(spec),
        "has_start_stop": _has_start_stop(spec),
        "lamps": len(_lamp_outputs(spec)),
        "outputs": len(_output_devices(spec)),
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "suggestions": suggestions, "stats": stats}


def quality_text(quality: Dict[str, Any]) -> str:
    """把质量结果转成一段人/agent 可读文本。"""
    lines: List[str] = []
    stats = quality.get("stats") or {}
    lines.append("质量：%s（段 %s / 工位步 %s / 指令 %s / 循环 %s / 定时 %s / 启停 %s）" % (
        "通过" if quality.get("ok") else "未通过",
        stats.get("segments"), stats.get("station_steps"), stats.get("instructions"),
        "有" if stats.get("has_cycle") else "无",
        "有" if stats.get("has_timer") else "无",
        "有" if stats.get("has_start_stop") else "无"))
    for label, key in (("错误", "errors"), ("警告", "warnings"), ("建议", "suggestions")):
        for item in quality.get(key) or []:
            lines.append("- [%s] %s" % (label, item))
    return "\n".join(lines)
