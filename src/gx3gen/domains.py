"""领域模板：把常见需求（如双向红绿灯）直接展开成完整的 ProgramSpec。

用途：
- 没有配置 LLM / 使用规则解析时，也能给出"有自动流程、定时器、循环、启停"的初稿，
  而不是只有一两个空 STL 步；
- 作为 AI Agent 的参考样例（MCP gx3gen_get_generation_context / get_examples）；
- 模板产出的 spec 会标注 assumptions，供工程师按现场修改。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .spec import default_spec

TRAFFIC_KEYWORDS = ("红绿灯", "交通灯", "信号灯", "红绿黄", "交通信号", "红绿灯控制")
DEFAULT_TIMINGS = [25, 3, 2, 20, 3, 2]  # 东西绿 / 东西黄 / 全红 / 南北绿 / 南北黄 / 全红


def detect_domain(requirement: str) -> Optional[str]:
    text = str(requirement or "")
    if any(keyword in text for keyword in TRAFFIC_KEYWORDS):
        return "traffic_light"
    if "灯" in text and ("红" in text or "绿" in text) and ("东西" in text or "南北" in text):
        return "traffic_light"
    return None


# ---------------------------------------------------------------------------
# IO 工具
# ---------------------------------------------------------------------------
def _norm_io(io: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for item in io or []:
        if not isinstance(item, dict):
            continue
        device = str(item.get("device") or "").strip().upper()
        if not device or device in seen:
            continue
        seen.add(device)
        rows.append({
            "device": device,
            "comment": str(item.get("comment") or device),
            "role": str(item.get("role") or ("input" if device.startswith("X") else
                                            "output" if device.startswith("Y") else "internal")),
        })
    return rows


def _comment_of(io: List[Dict[str, Any]], device: str) -> str:
    for row in io:
        if row["device"] == device:
            return row["comment"]
    return ""


def _find(io: List[Dict[str, Any]], includes: Tuple[str, ...], excludes: Tuple[str, ...] = (),
          prefix: str = "") -> Optional[str]:
    for row in io:
        device = row["device"]
        if prefix and not device.startswith(prefix):
            continue
        comment = row["comment"]
        if all(word in comment for word in includes) and not any(word in comment for word in excludes):
            return device
    return None


def _find_any(io: List[Dict[str, Any]], includes: Tuple[str, ...], prefix: str = "") -> Optional[str]:
    for row in io:
        device = row["device"]
        if prefix and not device.startswith(prefix):
            continue
        comment = row["comment"]
        if any(word in comment for word in includes):
            return device
    return None


def _next_device(io: List[Dict[str, Any]], prefix: str, start: int = 0) -> str:
    used = set()
    for row in io:
        match = re.match(r"^%s(\d+)$" % re.escape(prefix), row["device"])
        if match:
            used.add(int(match.group(1)))
    index = start
    while index in used:
        index += 1
    return "%s%d" % (prefix, index)


def _ensure(io: List[Dict[str, Any]], device: str, comment: str, role: str) -> None:
    for row in io:
        if row["device"] == device:
            if (row.get("comment") or "") in ("", device):
                row["comment"] = comment
            return
    io.append({"device": device, "comment": comment, "role": role})


def _find_lamp(io: List[Dict[str, Any]], directions: Tuple[str, ...], color: str) -> Optional[str]:
    for row in io:
        if not row["device"].startswith("Y"):
            continue
        comment = row["comment"]
        if color in comment and any(word in comment for word in directions):
            return row["device"]
    return None


def _parse_timings(requirement: str) -> Tuple[List[float], str]:
    numbers = re.findall(r"(\d+(?:\.\d+)?)\s*(?:秒|s|S|sec)", str(requirement or ""))
    seconds = [float(value) for value in numbers[:6]]
    if len(seconds) >= 6:
        return seconds, "从需求解析"
    if len(seconds) >= 3:
        seconds.extend(DEFAULT_TIMINGS[len(seconds):])
        return seconds, "部分从需求解析，其余用默认值"
    return list(DEFAULT_TIMINGS), "未在需求中识别到完整时长，使用默认值"


def _k(seconds: float) -> int:
    value = int(round(float(seconds) * 10))
    return max(1, value)


# ---------------------------------------------------------------------------
# 双向红绿灯模板
# ---------------------------------------------------------------------------
def build_traffic_light_spec(requirement: str, io: Any, name: Optional[str] = None) -> Dict[str, Any]:
    rows = _norm_io(io)
    assumptions: List[str] = []
    notes: List[str] = ["领域模板：双向红绿灯（规则生成；AI Agent 可在此基础上按现场修改）"]

    # 启动/停止
    start = _find_any(rows, ("启动", "开始"), prefix="X")
    stop = _find_any(rows, ("停止", "急停"), prefix="X") or _find_any(rows, ("急停", "停止"), prefix="M")
    if not start:
        start = _next_device(rows, "X")
        _ensure(rows, start, "启动按钮", "input")
        assumptions.append("未在 IO 表找到启动按钮，已分配 %s；请按现场修改" % start)
    if not stop:
        stop = _next_device(rows, "X")
        _ensure(rows, stop, "停止按钮", "input")
        assumptions.append("未在 IO 表找到停止按钮，已分配 %s；请按现场修改" % stop)

    # 六路灯：先按注释匹配，匹配不到的按 Y 顺序回退/新增
    lamp_specs = [
        ("ew_r", ("东西", "东向", "主路", "A路", "EW", "东"), "红", "东西红灯"),
        ("ew_y", ("东西", "东向", "主路", "A路", "EW", "东"), "黄", "东西黄灯"),
        ("ew_g", ("东西", "东向", "主路", "A路", "EW", "东"), "绿", "东西绿灯"),
        ("ns_r", ("南北", "支路", "B路", "NS", "南", "北"), "红", "南北红灯"),
        ("ns_y", ("南北", "支路", "B路", "NS", "南", "北"), "黄", "南北黄灯"),
        ("ns_g", ("南北", "支路", "B路", "NS", "南", "北"), "绿", "南北绿灯"),
    ]
    lamps: Dict[str, str] = {}
    fallback_used = False
    for key, directions, color, comment in lamp_specs:
        device = _find_lamp(rows, directions, color)
        if device:
            lamps[key] = device
        else:
            fallback_used = True
            device = _next_device(rows, "Y")
            _ensure(rows, device, comment, "output")
            lamps[key] = device
    if fallback_used:
        assumptions.append(
            "部分灯位未在 IO 注释中匹配到，已按 Y 顺序自动分配（东西红/黄/绿=%s/%s/%s，南北红/黄/绿=%s/%s/%s）；请核对现场点位"
            % (lamps["ew_r"], lamps["ew_y"], lamps["ew_g"], lamps["ns_r"], lamps["ns_y"], lamps["ns_g"]))

    timings, timing_source = _parse_timings(requirement)
    durations = [_k(value) for value in timings]
    assumptions.append(
        "相位时长（秒）：东西绿 %.0f / 东西黄 %.0f / 全红 %.0f / 南北绿 %.0f / 南北黄 %.0f / 全红 %.0f（%s，K 值=秒×10）"
        % (timings[0], timings[1], timings[2], timings[3], timings[4], timings[5], timing_source))
    assumptions.append(
        "停止处理：%s 复位运行中 M500、ZRST S10–S69 并回待机步 S0；未包含黄闪、夜间模式、灯故障检测/报警，如需请补充"
        % stop)
    assumptions.append("自动流程为单一循环（S10→S20→S30→S40→S50→S60→S10），如需行人请求/紧急车辆优先请补充 IO 与工艺")

    ew_on = {
        "g": [lamps["ew_g"]], "y": [lamps["ew_y"]], "r": [lamps["ew_r"]],
    }
    ns_on = {"g": [lamps["ns_g"]], "y": [lamps["ns_y"]], "r": [lamps["ns_r"]]}
    all_lamps = [lamps[key] for key in ("ew_r", "ew_y", "ew_g", "ns_r", "ns_y", "ns_g")]

    def phase(step: int, ew: str, ns: str, timer: str, duration: int, comment: str,
              next_step: Optional[int]) -> Dict[str, Any]:
        on = ew_on[ew] + ns_on[ns]
        off = [device for device in all_lamps if device not in on]
        return {
            "step": step,
            "comment": comment,
            "actions": [{
                "comment": "%s，%ds" % (comment, duration // 10),
                "set": on,
                "reset": off,
                "instructions": [{"op": "OUT", "operands": [timer, "K%d" % duration]}],
            }],
            "transition": {"conditions": [timer], "next": next_step,
                           "comment": "定时到 → S%d" % (next_step if next_step is not None else step)},
        }

    steps = [
        phase(10, "g", "r", "T0", durations[0], "东西绿/南北红", 20),
        phase(20, "y", "r", "T1", durations[1], "东西黄/南北红", 30),
        phase(30, "r", "r", "T2", durations[2], "全红", 40),
        phase(40, "r", "g", "T3", durations[3], "东西红/南北绿", 50),
        phase(50, "r", "y", "T4", durations[4], "东西红/南北黄", 60),
        phase(60, "r", "r", "T5", durations[5], "全红（循环前）", 10),
    ]
    # 最后一步回到第一步，形成自动循环；加入 M500 防止停止后自行循环
    steps[-1]["transition"] = {"conditions": ["T5", "M500"], "next": 10,
                               "comment": "全红结束 → 回到 S10（自动循环）"}

    init_rungs = [{
        "comment": "初始脉冲置待机步",
        "instructions": [{"op": "LD", "operands": ["SM402"]}, {"op": "SET", "operands": ["S0"]}],
    }]
    common_rungs = [
        {"comment": "启动：置运行中/自动",
         "instructions": [{"op": "LD", "operands": [start]},
                          {"op": "SET", "operands": ["M500"]},
                          {"op": "SET", "operands": ["M521"]}]},
        {"comment": "停止：复位运行中/自动，复位步并回待机",
         "instructions": [{"op": "LD", "operands": [stop]},
                          {"op": "RST", "operands": ["M500"]},
                          {"op": "RST", "operands": ["M521"]},
                          {"op": "ZRST", "operands": ["S10", "S69"]},
                          {"op": "SET", "operands": ["S0"]}]},
    ]
    auto_select = _find_any(rows, ("自动", "自动模式"), prefix="X")
    manual_select = _find_any(rows, ("手动", "手动模式"), prefix="X")
    if auto_select and manual_select:
        common_rungs.append({
            "comment": "手自动切换",
            "instructions": [{"op": "LD", "operands": [auto_select]},
                             {"op": "SET", "operands": ["M521"]},
                             {"op": "RST", "operands": ["M522"]}],
        })
        common_rungs.append({
            "comment": "手动模式",
            "instructions": [{"op": "LD", "operands": [manual_select]},
                             {"op": "SET", "operands": ["M522"]},
                             {"op": "RST", "operands": ["M521"]}],
        })
    else:
        assumptions.append("未识别到手/自动切换旋钮；如需手动模式请补充 IO 与动作")

    project_name = str(name or "").strip() or "双向红绿灯"
    spec = default_spec(project_name, io=rows)
    spec["modes"] = {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"}
    spec["common"]["start_step"] = 0
    spec["segments"] = [
        {"title": "初始化", "kind": "init", "rungs": init_rungs},
        {"title": "启动停止", "kind": "common", "rungs": common_rungs},
        {"title": "自动流程", "kind": "station", "step_range": [10, 69],
         "entry_conditions": ["S0", "M500"], "steps": steps},
    ]
    spec["alarms"] = {"enabled": False, "base": 2000, "timer_base": 10, "cylinders": []}
    spec["assumptions"] = assumptions
    spec["notes"] = notes
    return spec


def build_domain_spec(domain: str, requirement: str, io: Any,
                      name: Optional[str] = None) -> Dict[str, Any]:
    if domain == "traffic_light":
        return build_traffic_light_spec(requirement, io, name=name)
    raise ValueError("未知领域模板: %r" % domain)
