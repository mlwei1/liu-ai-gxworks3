"""ProgramSpec → Program IR。

生成风格（来自真实项目 / 简单搬运手）：
    [Title]初始化
    SM402 ─ SET S0
    [Title]设备段
    (进入条件) ─ SET S100
    STL S100
    S100 ─ SET Y0
    S100 & 条件 ─ SET S110
    ...
    RETSTL
    [Title]报警
    ...
    END
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..ir import Instruction, Program, Rung, Segment
from ..spec import lookup_device, resolve_operand
from .templates import ins, is_sfc_step, resolve, rung, rung_from_rules

DEFAULT_MODES = {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"}
MODE_COMMENTS = {"run": "运行中", "stop": "停止中", "auto": "自动", "manual": "手动", "reset": "复位"}


def _fallback_defaults() -> Dict[str, Any]:
    return {
        "segment_order": ["初始化", "设备段", "报警", "手动", "结束"],
        "step_base": 100,
        "step_stride": 100,
        "step_increment": 10,
        "modes": dict(DEFAULT_MODES),
        "alarm_base": 2000,
        "alarm_timer_base": 10,
        "handshake_base": 1000,
        "naming": {},
    }


def style_defaults(style: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """优先使用 style/apply.generator_defaults；模块不存在时回退默认值。"""
    defaults = _fallback_defaults()
    if not style:
        return defaults
    try:
        from ..style.apply import generator_defaults  # type: ignore
        applied = generator_defaults(style) or {}
    except Exception:
        applied = {}
    defaults.update({key: value for key, value in applied.items() if value is not None})
    strategy = style.get("step_strategy") or {}
    if isinstance(strategy, dict) and strategy.get("increment"):
        try:
            defaults["step_increment"] = int(strategy["increment"])
        except (TypeError, ValueError):
            pass
    return defaults


def _mode_device(spec: Dict[str, Any], defaults: Dict[str, Any], key: str) -> str:
    value = (spec.get("modes") or {}).get(key)
    if value:
        return str(value)
    mode = (defaults.get("modes") or {}).get(key)
    if isinstance(mode, dict):
        return str(mode.get("device") or DEFAULT_MODES.get(key, ""))
    if isinstance(mode, str):
        return mode
    return DEFAULT_MODES.get(key, "")


def _init_segment(spec: Dict[str, Any], defaults: Dict[str, Any]) -> Segment:
    start = int((spec.get("common") or {}).get("start_step", 0))
    return Segment(
        title="初始化",
        kind="init",
        rungs=[
            rung(ins("LD", "SM402"), ins("SET", "S%d" % start), comment="初始脉冲置待机步"),
        ],
    )


def _entry_rung(spec: Dict[str, Any], segment: Dict[str, Any], first_step: int) -> Optional[Rung]:
    conditions = [str(item) for item in (segment.get("entry_conditions") or [])]
    handshake = segment.get("handshake") or {}
    for item in handshake.get("in") or []:
        device = item.get("device") if isinstance(item, dict) else item
        if device and device not in conditions:
            conditions.append(str(device))
    if not conditions:
        return None
    instructions = [ins("LD", resolve(spec, conditions[0]))]
    for condition in conditions[1:]:
        instructions.append(ins("AND", resolve(spec, condition)))
    instructions.append(ins("SET", "S%d" % first_step))
    return Rung(instructions=instructions,
                comment="进入%s（%s）" % (segment.get("title", ""), " & ".join(conditions)))


def _build_station(spec: Dict[str, Any], segment: Dict[str, Any], defaults: Dict[str, Any],
                   station_index: int) -> Segment:
    title = str(segment.get("title") or "设备段")
    steps = sorted([step for step in (segment.get("steps") or []) if isinstance(step, dict)],
                   key=lambda step: int(step.get("step", 0)))
    if not steps:
        return Segment(title=title, kind="station", rungs=[])

    start_step = int(steps[0]["step"])
    handshake = segment.get("handshake") or {}
    handshake_out = [item.get("device") if isinstance(item, dict) else item
                     for item in handshake.get("out") or [] if item]
    shift_flags = [str(item) for item in handshake.get("shift") or []]
    run_device = _mode_device(spec, defaults, "run")
    home_step = int((spec.get("common") or {}).get("start_step", 0))

    rungs: List[Rung] = []
    entry = _entry_rung(spec, segment, start_step)
    if entry:
        rungs.append(entry)

    for index, step in enumerate(steps):
        step_no = int(step["step"])
        step_device = "S%d" % step_no
        rungs.append(Rung([ins("STL", step_device)],
                          comment="STL %s%s" % (step_device,
                                                ("（%s）" % step.get("comment")) if step.get("comment") else "")))
        # 步内动作
        for action in step.get("actions") or []:
            instructions: List[Instruction] = [ins("LD", step_device)]
            for device in action.get("set") or []:
                instructions.append(ins("SET", resolve(spec, device)))
            for device in action.get("reset") or []:
                instructions.append(ins("RST", resolve(spec, device)))
            for device in action.get("out") or []:
                instructions.append(ins("OUT", resolve(spec, device)))
            for rule in action.get("instructions") or []:
                operands = [resolve(spec, item) for item in (rule.get("operands") or [])]
                instructions.append(ins(str(rule.get("op") or ""), *operands))
            if len(instructions) > 1:
                rungs.append(Rung(instructions, comment=str(action.get("comment") or "")))
        # 转移
        transition = step.get("transition") or {}
        conditions = [resolve(spec, item) for item in (transition.get("conditions") or [])]
        next_step = transition.get("next")
        instructions = [ins("LD", step_device)]
        for condition in conditions:
            instructions.append(ins("AND", condition))
        if next_step is not None:
            instructions.append(ins("SET", "S%d" % int(next_step)))
        else:
            if home_step is not None:
                instructions.append(ins("SET", "S%d" % home_step))
            if run_device:
                instructions.append(ins("RST", run_device))
        comment = str(transition.get("comment") or "")
        if not comment:
            target = "S%d" % int(next_step) if next_step is not None else "S%d（回待机）" % home_step
            comment = "转移：%s → %s" % (" & ".join(conditions) or step_device, target)
        rungs.append(Rung(instructions, comment=comment))

    # 段完成交接信号
    last_step_device = "S%d" % int(steps[-1]["step"])
    if handshake_out:
        instructions = [ins("LD", last_step_device)]
        for device in handshake_out:
            instructions.append(ins("SET", resolve(spec, device)))
        rungs.append(Rung(instructions, comment="交接信号：%s" % "、".join(handshake_out)))
    if shift_flags:
        instructions = [ins("LD", last_step_device)]
        for device in shift_flags:
            instructions.append(ins("SET", resolve(spec, device)))
        rungs.append(Rung(instructions, comment="移位标志"))

    rungs.append(Rung([ins("RETSTL")], comment="STL 块结束"))
    step_range = segment.get("step_range")
    step_range_tuple = (int(step_range[0]), int(step_range[1])) if step_range else (start_step, int(steps[-1]["step"]))
    return Segment(title=title, kind="station", rungs=rungs, step_range=step_range_tuple)


def _build_alarm_segment(spec: Dict[str, Any], defaults: Dict[str, Any]) -> Optional[Segment]:
    alarms = spec.get("alarms") or {}
    if not alarms.get("enabled") or not alarms.get("cylinders"):
        return None
    base = int(alarms.get("base") or defaults.get("alarm_base") or 2000)
    timer_base = int(alarms.get("timer_base") or defaults.get("alarm_timer_base") or 10)
    delay = int(alarms.get("delay") or 20)
    reset_device = alarms.get("reset_device") or _mode_device(spec, defaults, "reset")
    rungs: List[Rung] = []
    used_bits = 0
    timer_index = 0
    for cylinder in alarms.get("cylinders") or []:
        name = str(cylinder.get("name") or "气缸")
        output = resolve(spec, cylinder.get("output"))
        on_sensor = cylinder.get("on_sensor")
        home_sensor = cylinder.get("home_sensor")
        alarm_on = cylinder.get("alarm_on") or "M%d" % (base + used_bits)
        used_bits += 1
        alarm_home = cylinder.get("alarm_home") or "M%d" % (base + used_bits)
        used_bits += 1
        if on_sensor:
            timer_on = "T%d" % (timer_base + timer_index)
            timer_index += 1
            rungs.append(Rung([ins("LD", output), ins("ANI", resolve(spec, on_sensor)),
                               ins("OUT", timer_on, "K%d" % delay)],
                              comment="%s 到位检测延时" % name))
            rungs.append(Rung([ins("LD", timer_on), ins("SET", resolve(spec, alarm_on))],
                              comment="气缸报警：%s到位" % name))
        if home_sensor:
            timer_home = "T%d" % (timer_base + timer_index)
            timer_index += 1
            rungs.append(Rung([ins("LDI", output), ins("ANI", resolve(spec, home_sensor)),
                               ins("OUT", timer_home, "K%d" % delay)],
                              comment="%s 原位检测延时" % name))
            rungs.append(Rung([ins("LD", timer_home), ins("SET", resolve(spec, alarm_home))],
                              comment="气缸报警：%s原位" % name))
    if reset_device and used_bits:
        rungs.append(Rung([ins("LD", resolve(spec, reset_device)),
                           ins("ZRST", "M%d" % base, "M%d" % (base + used_bits - 1))],
                          comment="报警清除"))
    return Segment(title="报警", kind="common", rungs=rungs)


def _build_rungs_segment(spec: Dict[str, Any], segment: Dict[str, Any]) -> Segment:
    rungs = [rung_from_rules(spec, rung.get("instructions") or [], comment=str(rung.get("comment") or ""))
             for rung in segment.get("rungs") or []]
    return Segment(title=str(segment.get("title") or ""), kind=str(segment.get("kind") or "rungs"),
                   rungs=rungs)


def _build_statement_segment(spec: Dict[str, Any], segment: Dict[str, Any]) -> Segment:
    return Segment(title=str(segment.get("title") or ""),
                   kind="statement",
                   statements=[str(item) for item in segment.get("statements") or []])


def build_program(spec: Dict[str, Any], style: Optional[Dict[str, Any]] = None) -> Program:
    defaults = style_defaults(style)
    program = Program(project=str((spec.get("project") or {}).get("name") or "未命名项目"),
                      cpu="FX5CPU FX5U")

    segments = spec.get("segments") or []
    has_init = any(segment.get("kind") == "init" for segment in segments)
    station_index = 0

    if not has_init and (spec.get("common") or {}).get("auto_rungs", True):
        program.segments.append(_init_segment(spec, defaults))

    for segment in segments:
        kind = segment.get("kind")
        if kind == "init":
            if segment.get("rungs"):
                built = _build_rungs_segment(spec, segment)
            else:
                built = _init_segment(spec, defaults)
            program.segments.append(built)
        elif kind == "station":
            program.segments.append(_build_station(spec, segment, defaults, station_index))
            station_index += 1
        elif kind == "statement":
            program.segments.append(_build_statement_segment(spec, segment))
        elif kind in ("common", "rungs"):
            if segment.get("rungs"):
                built = _build_rungs_segment(spec, segment)
            else:
                built = Segment(title=str(segment.get("title") or ""), kind=kind,
                                statements=[str(item) for item in segment.get("statements") or []])
            program.segments.append(built)
        else:
            program.segments.append(Segment(title=str(segment.get("title") or ""), kind=str(kind or "common")))

    # 报警段（仅当 spec 没手写报警段时自动补）
    has_alarm = any(str(segment.get("title") or "").strip() == "报警" for segment in segments)
    if not has_alarm:
        alarm_segment = _build_alarm_segment(spec, defaults)
        if alarm_segment:
            program.segments.append(alarm_segment)

    return program
