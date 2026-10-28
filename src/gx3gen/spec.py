"""ProgramSpec 契约：解码、校验、IO 表解析、设备查找。"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Dict, List, Optional

SPEC_VERSION = "0.1"
CPU = "FX5U"

# 软元件：X/Y/M/D/T/C/S/SM/SD/ZR/Z/V/L/B/W/SB/SW 等 + 可带 U\G 链接寄存器/位指定
DEVICE_RE = re.compile(r"^(?:[A-Za-z]{1,3}\d+(?:[.]\d+)?|U\d*\\\\?G\d+(?:[.]\d+)?|SM\d+|SD\d+)$")
CONST_RE = re.compile(r"^(?:K-?\d+|H[0-9A-Fa-f]+|E[-+]?\d+(?:[.]\d+)?|\"[^\"]*\"|'[^']*'|TRUE|FALSE)$")
OPERAND_RE = re.compile(DEVICE_RE.pattern + r"|" + CONST_RE.pattern.replace("^", "").replace("$", ""))
IO_DEVICE_RE = re.compile(r"^[A-Za-z]{1,3}\d+(?:[.]\d+)?$")

VALID_KINDS = ("init", "station", "common", "rungs", "statement")
VALID_ROLES = ("input", "output", "inout", "internal")
VALID_OPS = tuple("""
LD LDI AND ANI OR ORI ORB ANB MPS MRD MPP INV
OUT SET RST ALT PLS PLF
STL RETSTL
MOV MOVP DMOV BMOV FMOV ZRST
CMP CMPP > < >= <= = <> 
ADD SUB MUL DIV INC DEC
MC MCR CJ JMP CALL SRET FOR NEXT
NOP END
""".split())

# FX5U 指令操作数个数约束：(最少, 最多)。未列出的指令不做限制。
# 注意：SET/RST 每条指令只接受 1 个操作数——多个设备必须拆成多条指令，
# 否则 GX Works3 导入时报「指令不正确」。
OP_OPERAND_LIMITS = {
    "SET": (1, 1),
    "RST": (1, 1),
    "OUT": (1, 2),
    "ZRST": (2, 2),
    "MOV": (2, 2),
    "MOVP": (2, 2),
    "DMOV": (2, 2),
    "CMP": (3, 3),
    "CMPP": (3, 3),
    "ADD": (3, 3),
    "SUB": (3, 3),
    "MUL": (3, 3),
    "DIV": (3, 3),
    "STL": (1, 1),
    "RETSTL": (0, 0),
    "END": (0, 0),
}


def _check_operand_count(errors: List[str], ibase: str, op: str,
                         operands: List[Any]) -> None:
    """按 FX5U 指令集校验操作数个数（SET/RST 必须 1 个，多设备要拆成多条）。"""
    limits = OP_OPERAND_LIMITS.get(op)
    if not limits:
        return
    low, high = limits
    count = len(operands)
    if low <= count <= high:
        return
    expect = ("%d" % low) if low == high else ("%d~%d" % (low, high))
    _err(errors, ibase + ".operands",
         "%s 指令操作数个数应为 %s，当前 %d 个（%s）；请拆成多条指令"
         % (op, expect, count, "、".join(str(item) for item in operands)))


def _check_rungs(errors: List[str], base: str, rungs: Any, required: bool = True) -> None:
    """校验 rungs 网络结构（init/common/rungs 三种段共用）。"""
    if rungs is None:
        if required:
            _err(errors, base + ".rungs", "rungs 段至少需要一条网络")
        return
    if not isinstance(rungs, list) or not rungs:
        _err(errors, base + ".rungs", "rungs 段至少需要一条网络")
        return
    for r_index, rung in enumerate(rungs):
        rbase = "%s.rungs[%d]" % (base, r_index)
        if not isinstance(rung, dict) or not isinstance(rung.get("instructions"), list):
            _err(errors, rbase, "需要 instructions 数组")
            continue
        for i_index, ins in enumerate(rung["instructions"]):
            ibase = "%s.instructions[%d]" % (rbase, i_index)
            if not isinstance(ins, dict) or not isinstance(ins.get("op"), str) \
                    or not ins.get("op", "").strip():
                _err(errors, ibase, "缺少 op")
                continue
            if ins["op"].strip().upper() not in VALID_OPS:
                _err(errors, ibase + ".op", "未知指令: %r" % ins["op"])
            _check_operand_count(errors, ibase, ins["op"].strip().upper(),
                                ins.get("operands") or [])
            for operand in ins.get("operands") or []:
                if not isinstance(operand, str) or not operand.strip():
                    _err(errors, ibase + ".operands", "操作数必须是非空字符串")
        _check_il_sequence(errors, rbase, rung.get("instructions") or [])


# 三菱 IL 规则：LD/LDI 是「载入」指令，会丢弃之前的结果并开启新支路。
# 串联条件必须用 AND/ANI/OR/ORI；LD 之后紧跟另一个 LD/LDI 会让 GX 转梯形图时
# 产生悬空支路 → 导入报「梯形图配置不正确」。
LOAD_OPS = {"LD", "LDI"}
BRANCH_OPS = {"ORB", "ANB", "MPS", "MRD", "MPP"}


def _check_il_sequence(errors: List[str], rbase: str, instructions: List[Any]) -> None:
    """校验一条 rung 内的 IL 指令序列（逻辑载入不能紧接逻辑载入）。"""
    prev_load = ""
    prev_desc = ""
    prev_index = -1
    for index, ins in enumerate(instructions):
        if not isinstance(ins, dict):
            continue
        op = str(ins.get("op") or "").strip().upper()
        operands = ins.get("operands") or []
        desc = "%s %s" % (op, " ".join(str(x) for x in operands)) if operands else op
        if op in LOAD_OPS:
            if prev_load in LOAD_OPS:
                _err(errors, "%s.instructions[%d].op" % (rbase, index),
                     "%s 紧跟在第 %d 条 `%s` 之后：LD/LDI 是载入指令，会丢弃前一条结果"
                     "并开启新支路；串联条件请用 AND，串联取反请用 ANI"
                     % (desc, prev_index, prev_desc))
            prev_load = op
            prev_desc = desc
            prev_index = index
        elif op in BRANCH_OPS:
            prev_load = ""
            prev_desc = desc
            prev_index = index
        elif op:
            prev_desc = desc
            prev_index = index


class SpecError(ValueError):
    """ProgramSpec 不合法。"""


# ---------------------------------------------------------------------------
# 文本解码（GX 导出多为 UTF-16LE+BOM；用户 IO 表可能是 UTF-8/GBK）
# ---------------------------------------------------------------------------
def decode_text(data: Any) -> str:
    if isinstance(data, str):
        return data
    if data is None:
        return ""
    raw = bytes(data)
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le")
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    for encoding in ("utf-8-sig", "utf-8", "gbk", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 设备 / 操作数
# ---------------------------------------------------------------------------
def is_device(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if CONST_RE.match(text):  # K/H/E/字符串常量不是设备
        return False
    return bool(DEVICE_RE.match(text))


def is_operand(value: str) -> bool:
    return bool(value) and (is_device(value) or bool(CONST_RE.match(value.strip())))


def device_class(device: str) -> str:
    match = re.match(r"^([A-Za-z]+)", device.strip())
    return match.group(1).upper() if match else ""


def device_number(device: str) -> Optional[int]:
    match = re.search(r"(\d+)", device.strip())
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# IO 表
# ---------------------------------------------------------------------------
_IO_HEADER_KEYS = {
    "device": ("软元件", "设备", "device", "地址"),
    "comment": ("注释", "comment", "说明", "名称"),
    "role": ("类型", "type", "方向", "role"),
    "position": ("原位", "位置", "position", "备注"),
}


def _header_index(header: List[str]) -> Optional[Dict[str, int]]:
    mapping: Dict[str, int] = {}
    for idx, cell in enumerate(header):
        text = (cell or "").strip().lower()
        if not text:
            continue
        for key, words in _IO_HEADER_KEYS.items():
            if key in mapping:
                continue
            if any(word.lower() in text for word in words):
                mapping[key] = idx
                break
    if "device" in mapping:
        return mapping
    return None


def _role_of(device: str, raw: str = "") -> str:
    raw = (raw or "").strip().lower()
    if "入" in raw or "input" in raw:
        return "input"
    if "出" in raw or "output" in raw:
        return "output"
    if "内" in raw or "internal" in raw:
        return "internal"
    cls = device_class(device)
    if cls == "X":
        return "input"
    if cls == "Y":
        return "output"
    return "internal"


def parse_io_csv(text: Any) -> List[Dict[str, Any]]:
    """解析 IO 表（GX 顺序表或自定义表头 CSV）。"""
    content = decode_text(text)
    rows = list(csv.reader(io.StringIO(content)))
    if not rows:
        return []
    header = _header_index(rows[0])
    start = 1 if header else 0
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows[start:]:
        row = [cell.strip() for cell in row] + [""] * 6
        if header:
            device = row[header.get("device", 1)] if header.get("device", 1) < len(row) else ""
            comment = row[header.get("comment", 2)] if header.get("comment", 2) < len(row) else ""
            raw_role = row[header.get("role", 3)] if header.get("role", 3) < len(row) else ""
            position = row[header.get("position", 4)] if header.get("position", 4) < len(row) else ""
        else:
            device, comment, raw_role, position = row[1], row[2], row[3], row[4]
        device = (device or "").strip()
        if not IO_DEVICE_RE.match(device):
            continue
        if device.upper() in seen:
            continue
        seen.add(device.upper())
        item = {
            "device": device.upper() if device.upper() == device else device,
            "comment": comment,
            "role": _role_of(device, raw_role),
        }
        if position:
            item["type"] = position
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Spec 校验
# ---------------------------------------------------------------------------
def default_spec(name: str = "新项目", io: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "project": {"name": name, "cpu": CPU, "gx_version": "1.082", "style_profile": None},
        "io": list(io or []),
        "modes": {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"},
        "common": {
            "handshake_base": 1000,
            "alarm_base": 2000,
            "alarm_timer_base": 10,
            "start_step": 0,
            "station_step_base": 100,
            "station_step_stride": 100,
            "step_increment": 10,
            "auto_rungs": True,
        },
        "segments": [],
        "alarms": {"enabled": False, "base": 2000, "timer_base": 10, "cylinders": []},
        "assumptions": [],
        "notes": [],
    }


def _err(errors: List[str], path: str, message: str) -> None:
    errors.append("%s: %s" % (path, message))


def _check_device(errors: List[str], path: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        _err(errors, path, "设备号必须是非空字符串")
    elif not is_device(value) and not CONST_RE.match(value.strip()):
        _err(errors, path, "非法设备号/操作数: %r" % value)


def validate_spec(spec: Any) -> None:
    """校验 ProgramSpec；不合法抛 SpecError（包含全部错误路径）。"""
    errors: List[str] = []
    if not isinstance(spec, dict):
        raise SpecError("spec 必须是 JSON 对象")
    if spec.get("spec_version") != SPEC_VERSION:
        _err(errors, "spec_version", "必须为 %r" % SPEC_VERSION)

    project = spec.get("project")
    if not isinstance(project, dict):
        _err(errors, "project", "缺少 project 对象")
    else:
        if not isinstance(project.get("name"), str) or not project.get("name", "").strip():
            _err(errors, "project.name", "必须是非空字符串")
        if project.get("cpu") != CPU:
            _err(errors, "project.cpu", "本期只支持 %r" % CPU)
        # 注释标题（GX 工程 [视图]→[多注释显示设置] 里的注释标题名）。
        # 不填则用内置候选集；填字符串表示只用一个标题，填列表表示给出多个候选列。
        titles = project.get("comment_titles")
        if titles is None and project.get("comment_title") is not None:
            titles = [project.get("comment_title")]
        if titles is not None:
            if not isinstance(titles, list) or not titles:
                _err(errors, "project.comment_titles", "必须是非空字符串数组")
            elif not all(isinstance(item, str) and item.strip() for item in titles):
                _err(errors, "project.comment_titles", "每个标题必须是非空字符串")

    io = spec.get("io")
    if not isinstance(io, list):
        _err(errors, "io", "必须是数组")
    else:
        seen = set()
        for index, item in enumerate(io):
            if not isinstance(item, dict):
                _err(errors, "io[%d]" % index, "必须是对象")
                continue
            device = item.get("device")
            if not isinstance(device, str) or not IO_DEVICE_RE.match(device.strip()):
                _err(errors, "io[%d].device" % index, "非法软元件名: %r" % device)
                continue
            if device.strip().upper() in seen:
                _err(errors, "io[%d].device" % index, "重复软元件: %s" % device)
            seen.add(device.strip().upper())
            if not isinstance(item.get("comment"), str) or not item.get("comment", "").strip():
                _err(errors, "io[%d].comment" % index, "注释不能为空")
            if item.get("role") is not None and item.get("role") not in VALID_ROLES:
                _err(errors, "io[%d].role" % index, "必须是 %s" % (VALID_ROLES,))

    modes = spec.get("modes") or {}
    if not isinstance(modes, dict):
        _err(errors, "modes", "必须是对象")
    else:
        for key, value in modes.items():
            _check_device(errors, "modes.%s" % key, value)

    common = spec.get("common") or {}
    if not isinstance(common, dict):
        _err(errors, "common", "必须是对象")
    else:
        for key in ("handshake_base", "alarm_base", "alarm_timer_base",
                    "start_step", "station_step_base", "station_step_stride", "step_increment"):
            if key in common and not isinstance(common[key], int):
                _err(errors, "common.%s" % key, "必须是整数")

    segments = spec.get("segments")
    if not isinstance(segments, list) or not segments:
        _err(errors, "segments", "至少需要一个段")
    else:
        for s_index, segment in enumerate(segments):
            base = "segments[%d]" % s_index
            if not isinstance(segment, dict):
                _err(errors, base, "必须是对象")
                continue
            if not isinstance(segment.get("title"), str):
                _err(errors, base + ".title", "必须是字符串")
            kind = segment.get("kind")
            if kind not in VALID_KINDS:
                _err(errors, base + ".kind", "必须是 %s" % (VALID_KINDS,))
                continue
            step_range = segment.get("step_range")
            if step_range is not None:
                if (not isinstance(step_range, list) or len(step_range) != 2
                        or not all(isinstance(item, int) for item in step_range)
                        or step_range[0] > step_range[1]):
                    _err(errors, base + ".step_range", "必须是 [start, end] 且 start<=end")
            if kind == "station":
                steps = segment.get("steps")
                if not isinstance(steps, list) or not steps:
                    _err(errors, base + ".steps", "station 段至少需要一个 step")
                else:
                    seen_steps = set()
                    for step_index, step in enumerate(steps):
                        sbase = "%s.steps[%d]" % (base, step_index)
                        if not isinstance(step, dict) or not isinstance(step.get("step"), int):
                            _err(errors, sbase + ".step", "必须是整数")
                            continue
                        if step["step"] in seen_steps:
                            _err(errors, sbase + ".step", "同段内步号重复: %d" % step["step"])
                        seen_steps.add(step["step"])
                        for action_index, action in enumerate(step.get("actions") or []):
                            abase = "%s.actions[%d]" % (sbase, action_index)
                            if not isinstance(action, dict):
                                _err(errors, abase, "必须是对象")
                                continue
                            for key in ("set", "reset", "out"):
                                for device in action.get(key) or []:
                                    _check_device(errors, abase + "." + key, device)
                            for i_index, item in enumerate(action.get("instructions") or []):
                                ibase = "%s.instructions[%d]" % (abase, i_index)
                                if not isinstance(item, dict) or not isinstance(item.get("op"), str) \
                                        or not item.get("op", "").strip():
                                    _err(errors, ibase, "缺少 op")
                                    continue
                                if item["op"].strip().upper() not in VALID_OPS:
                                    _err(errors, ibase + ".op", "未知指令: %r" % item["op"])
                                _check_operand_count(errors, ibase, item["op"].strip().upper(),
                                                    item.get("operands") or [])
                                for operand in item.get("operands") or []:
                                    if not isinstance(operand, str) or not operand.strip():
                                        _err(errors, ibase + ".operands", "操作数必须是非空字符串")
                            # 生成器会在动作指令前自动加 `LD S<step>` 作为步条件，
                            # 动作里再出现 LD/LDI 会覆盖步条件并造成悬空支路。
                            action_ins = action.get("instructions") or []
                            if action_ins and isinstance(action_ins[0], dict) \
                                    and str(action_ins[0].get("op") or "").strip().upper() in LOAD_OPS:
                                _err(errors, abase + ".instructions[0].op",
                                     "步内动作不能以 LD/LDI 开头：生成器已自动加 `LD S<step>` 作为步条件，"
                                     "再载入会覆盖它并让 GX 报「梯形图配置不正确」")
                            _check_il_sequence(errors, abase, action_ins)
                        transition = step.get("transition") or {}
                        if not isinstance(transition, dict):
                            _err(errors, sbase + ".transition", "必须是对象")
                        else:
                            for device in transition.get("conditions") or []:
                                _check_device(errors, sbase + ".transition.conditions", device)
                            if transition.get("next") is not None and not isinstance(transition["next"], int):
                                _err(errors, sbase + ".transition.next", "必须是整数或 null")
            elif kind == "rungs":
                _check_rungs(errors, base, segment.get("rungs"), required=True)
            elif kind in ("init", "common"):
                # init/common 段的 rungs 可选（init 缺省时生成器自动补一条），
                # 但一旦写了就必须逐条校验指令与操作数。
                _check_rungs(errors, base, segment.get("rungs"), required=False)
            elif kind == "statement":
                statements = segment.get("statements")
                if not isinstance(statements, list) or not statements:
                    _err(errors, base + ".statements", "statement 段至少需要一条语句")

    alarms = spec.get("alarms") or {}
    if not isinstance(alarms, dict):
        _err(errors, "alarms", "必须是对象")
    else:
        if alarms.get("base") is not None and not isinstance(alarms["base"], int):
            _err(errors, "alarms.base", "必须是整数")
        for c_index, cylinder in enumerate(alarms.get("cylinders") or []):
            cbase = "alarms.cylinders[%d]" % c_index
            if not isinstance(cylinder, dict):
                _err(errors, cbase, "必须是对象")
                continue
            if not cylinder.get("name"):
                _err(errors, cbase + ".name", "不能为空")
            _check_device(errors, cbase + ".output", cylinder.get("output"))

    if errors:
        raise SpecError("ProgramSpec 校验失败:\n- " + "\n- ".join(errors))


def load_spec(path: str) -> Dict[str, Any]:
    from pathlib import Path
    text = Path(path).read_text(encoding="utf-8")
    spec = json.loads(text)
    validate_spec(spec)
    return spec


# ---------------------------------------------------------------------------
# 设备查找
# ---------------------------------------------------------------------------
def io_map(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {(item.get("device") or "").upper(): item for item in spec.get("io") or []}


def lookup_device(spec: Dict[str, Any], token: str) -> Optional[str]:
    """设备号或注释 → 设备号。优先精确设备号，再精确注释，再包含匹配。"""
    if not token:
        return None
    token = token.strip()
    mapping = io_map(spec)
    if token.upper() in mapping:
        return mapping[token.upper()]["device"]
    if is_device(token):
        return token.upper() if token.upper() != token else token
    for item in spec.get("io") or []:
        if (item.get("comment") or "").strip() == token:
            return item["device"]
    for item in spec.get("io") or []:
        comment = (item.get("comment") or "").strip()
        if token in comment or (comment and comment in token):
            return item["device"]
    return None


def resolve_operand(spec: Dict[str, Any], token: str) -> str:
    """操作数：设备/常数原样返回；注释文本尽量转设备，转不了原样返回（留给 lint 报警）。"""
    token = (token or "").strip()
    if is_operand(token):
        return token
    found = lookup_device(spec, token)
    return found or token
