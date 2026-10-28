"""风格学习：把用户的 GX Works3 程序本体 CSV（+ 软元件注释 CSV）蒸馏成 StyleProfile。

对应 docs/STYLE_LEARNING.md 与 spec/style_profile.schema.json：

- 输入：GX Works3“程序本体 CSV”（UTF-16LE+BOM / TAB / 7 列）与可选“软元件注释 CSV”。
- 输出：可直接 JSON 序列化的画像 dict（字段名严格使用 schema / docs 里的名字），
  每个自动推断字段尽量附 ``evidence`` / ``examples``，让 UI 能解释“学到了什么”。
- 原则：只学结构/步号段/命名/地址规律，不复制用户的具体控制逻辑。

对外接口（docs/INTERFACES.md）：

    mine(program_csv_text, comment_csv_text, name) -> dict

实现约定：
- 输入可以是已解码的 str，也可以是原始 bytes（UTF-16/UTF-8/GBK 由 spec.decode_text 识别），
  甚至可以是文件路径（便于 app 层直接传路径）。
- 任何情况下都不抛异常：空/损坏输入返回带 ``warnings`` 的最小画像。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..ilcsv.parser import extract_segments
from ..ilcsv.rows import iter_instructions, read_comment_csv, read_program_csv
from .profile import PROFILE_VERSION, empty_profile

# ---------------------------------------------------------------------------
# 全角 → 半角（GX 工程里存在 “ＳＱ１ / ＩＮＩＴ / １” 这类全角写法）
# ---------------------------------------------------------------------------
_FULLWIDTH_MAP: Dict[int, int] = {}
for _index in range(10):
    _FULLWIDTH_MAP[0xFF10 + _index] = ord(str(_index))
for _index in range(26):
    _FULLWIDTH_MAP[0xFF21 + _index] = ord(chr(ord("A") + _index))
    _FULLWIDTH_MAP[0xFF41 + _index] = ord(chr(ord("a") + _index))
_FULLWIDTH_MAP.update({
    0xFF01: ord("!"), 0xFF03: ord("#"), 0xFF05: ord("%"), 0xFF08: ord("("),
    0xFF09: ord(")"), 0xFF0C: ord(","), 0xFF0D: ord("-"), 0xFF0E: ord("."),
    0xFF0F: ord("/"), 0xFF1A: ord(":"), 0xFF1B: ord(";"), 0xFF1C: ord("<"),
    0xFF1D: ord("="), 0xFF1E: ord(">"), 0xFF1F: ord("?"), 0xFF3F: ord("_"),
    0xFF3B: ord("["), 0xFF3D: ord("]"), 0x3000: ord(" "),
})

FULLWIDTH_RE = re.compile(r"[\uFF01-\uFF5E\u3000]")
CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]")


def normalize_width(text: Any) -> str:
    """全角字母/数字/常用标点 → 半角；用于标题与注释的稳定比较。"""
    return ("" if text is None else str(text)).translate(_FULLWIDTH_MAP)


def has_fullwidth(text: Any) -> bool:
    return bool(FULLWIDTH_RE.search("" if text is None else str(text)))


# ---------------------------------------------------------------------------
# 指令 / 设备基础解析
# ---------------------------------------------------------------------------
_LOAD_OPS = {"LD", "LDI", "LDP", "LDF", "LDB", "LDT"}
_CONSTANT_CLASSES = {"K", "H", "E"}
_DEVICE_RE = re.compile(r"^([A-Za-z]{1,3})(\d{1,5})(?:\.(\d+))?$")
_DEVICE_CLASS_ORDER = (
    "X", "Y", "M", "D", "T", "C", "S", "SM", "SD", "ZR", "Z",
    "L", "B", "W", "R", "SB", "SW", "F",
)
_DEVICE_CLASSES = set(_DEVICE_CLASS_ORDER)

_OP_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)([<>=!]{1,2})?$")
_FB_BLOCK_TITLE_RE = re.compile(r"^\*")
_STEP_NUM_RE = re.compile(r"^[Ss](\d+)$")


def op_key(raw_op: Any) -> str:
    """把 CSV 里的指令列归一化成统计用 mnemonic。

    - ``$MOVP`` → ``MOVP``；``AND$=`` → ``AND=``（去掉字符串比较前缀标记）；
    - ``ANDD=`` / ``OR<=`` 这类比较指令保留比较后缀；
    - FB 实参行（如 ``i_bEN IN:BOOL``）返回 ``""``，不计入指令构成；
    - ``FBCALL M+FX5SSC...`` 只取 ``FBCALL``。
    """
    raw = ("" if raw_op is None else str(raw_op)).strip()
    if not raw:
        return ""
    text = raw.replace("$", "")
    if " " in text and ":" in text:  # FB 调用实参行
        return ""
    token = text.split()[0]
    match = _OP_RE.match(token)
    if not match:
        return ""
    base = match.group(1)
    if base.lower().startswith(("i_", "o_")):
        return ""
    return base.upper() + (match.group(2) or "")


def is_load(op: str) -> bool:
    """判断是否为“一条网络/梯级”的起始触点指令。"""
    if op in _LOAD_OPS:
        return True
    return op.startswith("LD") and any(symbol in op for symbol in "<>=!")


def parse_device(operand: Any) -> Optional[Tuple[str, int, str]]:
    """软元件操作数 → (类别, 数值, 位号)；常数/链接寄存器/FB 实例名返回 None。"""
    text = ("" if operand is None else str(operand)).strip()
    match = _DEVICE_RE.match(text)
    if not match:
        return None
    cls = match.group(1).upper()
    if cls in _CONSTANT_CLASSES or cls not in _DEVICE_CLASSES:
        return None
    return cls, int(match.group(2)), match.group(3) or ""


def _normalize_instruction(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    op = op_key(item.get("op"))
    if not op:
        return None
    operands = []
    for operand in item.get("operands") or []:
        text = ("" if operand is None else str(operand)).strip()
        if text:
            operands.append(text)
    return {
        "op": op,
        "operands": operands,
        "step": ("" if item.get("step") is None else str(item.get("step"))).strip(),
        "line": item.get("line"),
    }


def _devices_of(instruction: Dict[str, Any]) -> List[str]:
    out = []
    for operand in instruction.get("operands") or []:
        if parse_device(operand):
            out.append(operand.upper())
    return out


def _stl_numbers(instructions: Iterable[Dict[str, Any]]) -> List[int]:
    numbers: List[int] = []
    for instruction in instructions:
        if instruction.get("op") != "STL":
            continue
        operands = instruction.get("operands") or []
        if not operands:
            continue
        match = _STEP_NUM_RE.match(operands[0])
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def _network_count(instructions: List[Dict[str, Any]]) -> int:
    """CSV 没有网络边界；按“触点/STL 起始”估算网络数（可解释的近似值）。"""
    if not instructions:
        return 0
    count = sum(1 for item in instructions if is_load(item["op"]) or item["op"] == "STL")
    return count or 1


# ---------------------------------------------------------------------------
# 模式位 / 报警 / 握手识别
# ---------------------------------------------------------------------------
_M_DEVICE_RE = re.compile(r"^M(\d+)$")
_T_DEVICE_RE = re.compile(r"^T(\d+)$")

_MODE_CONVENTION = {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"}
_MODE_ORDER = ("run", "stop", "auto", "manual", "reset")
_MODE_LABELS = {
    "run": "运行中", "stop": "停止中", "auto": "自动", "manual": "手动", "reset": "复位",
}


def _mode_score(mode: str, raw_comment: str) -> Tuple[int, str]:
    """给某条注释在某模式下打分；0 表示不像。返回 (score, 说明)。"""
    comment = normalize_width(raw_comment).strip()
    if not comment:
        return 0, ""
    if "->" in comment or "<-" in comment:  # 握手/通信注释，不是面板模式位
        return 0, ""

    if mode == "run":
        if "停止" in comment or "报警" in comment:
            return 0, ""
        if comment == "运行中":
            return 100, "注释精确匹配「运行中」"
        if comment.endswith("运行中"):
            if "自动" in comment:
                return 70, "注释以「运行中」结尾（含「自动」，降权）"
            return 86, "注释以「运行中」结尾"
        if comment == "运行":
            return 72, "注释精确匹配「运行」"
        if "运行中" in comment:
            return 62, "注释包含「运行中」"

    if mode == "stop":
        if "启动" in comment or "报警" in comment:
            return 0, ""
        if comment == "停止中":
            return 100, "注释精确匹配「停止中」"
        if comment.endswith("停止中"):
            return 86, "注释以「停止中」结尾"
        if comment == "停止":
            return 66, "注释精确匹配「停止」"
        if "停止中" in comment:
            return 60, "注释包含「停止中」"

    if mode == "auto":
        if "手自动" in comment or "报警" in comment or "停止" in comment:
            return 0, ""
        if comment == "自动":
            return 100, "注释精确匹配「自动」"
        if comment in ("自动模式", "自动运行"):
            return 86, "注释匹配「%s」" % comment
        if len(comment) <= 6 and "自动" in comment:
            return 60, "注释短且包含「自动」"

    if mode == "manual":
        if "手自动" in comment or "脉冲" in comment or "提示" in comment or "切换" in comment:
            return 0, ""
        if comment == "手动":
            return 100, "注释精确匹配「手动」"
        if comment in ("手动模式", "手动运行"):
            return 86, "注释匹配「%s」" % comment
        if len(comment) <= 6 and "手动" in comment:
            return 60, "注释短且包含「手动」"

    if mode == "reset":
        if comment in ("复位", "系统复位", "复位按钮", "报警复位"):
            return 100, "注释匹配复位按钮「%s」" % comment
        if comment in ("系统复归", "复归"):
            return 88, "注释匹配「%s」" % comment
        if len(comment) <= 6 and comment.endswith("复位"):
            return 78, "注释短且以「复位」结尾"
    return 0, ""


def _infer_modes(
    comments: Dict[str, str],
    usage: Counter,
) -> Dict[str, Dict[str, Any]]:
    """从注释关键词识别 run/stop/auto/manual/reset 对应的 M 位。"""
    m_comments = {
        device: comment
        for device, comment in comments.items()
        if _M_DEVICE_RE.match(device) and (comment or "").strip()
    }
    modes: Dict[str, Dict[str, Any]] = {}
    for mode in _MODE_ORDER:
        candidates: List[Dict[str, Any]] = []
        for device, comment in m_comments.items():
            score, reason = _mode_score(mode, comment)
            if score <= 0:
                continue
            number = int(_M_DEVICE_RE.match(device).group(1))
            used = usage.get(device.upper(), 0)
            candidates.append({
                "device": device.upper(),
                "comment": comment.strip(),
                "score": score,
                "used": used,
                "reason": reason,
                "number": number,
            })
        if not candidates:
            continue
        candidates.sort(key=lambda item: (-item["score"], -item["used"], item["number"]))
        best = candidates[0]
        confidence = {100: 0.95, 88: 0.88, 86: 0.85, 78: 0.8}.get(best["score"], 0.65)
        if not best["used"]:
            confidence *= 0.9
        evidence = [best["reason"]]
        if best["used"]:
            evidence.append("该设备在程序指令中出现 %d 次" % best["used"])
        else:
            evidence.append("该设备未在程序指令中出现（可能仅用于 HMI/注释）")
        alternatives = [
            {"device": item["device"], "comment": item["comment"]}
            for item in candidates[1:4]
        ]
        modes[mode] = {
            "device": best["device"],
            "comment": best["comment"],
            "confidence": round(confidence, 2),
            "used": best["used"],
            "evidence": evidence,
            "alternatives": alternatives,
        }

    # 未学到的常用模式位保留内置默认（低置信度），让画像始终可被生成器直接使用
    fallback_reason = (
        "未提供软元件注释 CSV" if not comments
        else "注释中未找到该模式位的可靠关键词"
    )
    for mode, device in _MODE_CONVENTION.items():
        if mode in modes:
            continue
        used = usage.get(device, 0)
        modes[mode] = {
            "device": device,
            "comment": "",
            "confidence": 0.3 if used else 0.2,
            "used": used,
            "evidence": [
                "%s，沿用内置默认 %s（需人工确认）" % (fallback_reason, device),
                ("该设备在程序指令中出现 %d 次" % used) if used
                else "该设备未在程序指令中出现",
            ],
            "alternatives": [],
        }
    return {mode: modes[mode] for mode in _MODE_ORDER if mode in modes}


def _strip_own_device(comment: str, device: str) -> str:
    """去掉注释开头自带的设备号标签，如 ``M1610气缸报警：…`` → ``气缸报警：…``。"""
    text = normalize_width(comment).strip()
    if device:
        text = re.sub(r"^%s\s*" % re.escape(device.upper()), "", text, flags=re.IGNORECASE).strip()
    return text


def _alarm_template(body: str) -> str:
    """把一条报警注释抽象成命名模板。"""
    text = body.strip()
    if "气缸报警" in text:
        if "原位" in text:
            return "气缸报警：{name}原位{sensor}"
        if "到位" in text:
            return "气缸报警：{name}到位{sensor}"
        return "气缸报警：{name}"
    if "自动报警" in text:
        return "自动报警：{name}"
    if "原位报警" in text:
        return "{name}原位报警"
    if "到位报警" in text:
        return "{name}到位报警"
    if "伺服" in text and text.endswith("报警"):
        return "{name}报警"
    if text.endswith("报警"):
        head = text[: -len("报警")].strip(" ：:-")
        return (head + "报警") if head and len(head) <= 10 else "{name}报警"
    return "{name}报警"


def _group_numbers(numbers: Iterable[int], gap: int = 20) -> List[Dict[str, int]]:
    """把一组号段按间隔聚成 [{"start","end","count"}]。"""
    ordered = sorted(set(numbers))
    ranges: List[Dict[str, int]] = []
    for number in ordered:
        if ranges and number - ranges[-1]["end"] <= gap:
            ranges[-1]["end"] = number
            ranges[-1]["count"] += 1
        else:
            ranges.append({"start": number, "end": number, "count": 1})
    return ranges


def _format_example(device: str, comment: str) -> str:
    """样例统一格式：注释已自带设备号时不重复拼设备号。"""
    text = (comment or "").strip()
    if text.upper().startswith(device.upper()):
        return text
    return "%s %s" % (device, text)


def _infer_alarm(comments: Dict[str, str], usage: Counter) -> Dict[str, Any]:
    """识别报警位段（基址/命名/样例）与报警延时定时器基址。"""
    candidates: List[Tuple[int, str, str, str]] = []
    for device, raw_comment in comments.items():
        match = _M_DEVICE_RE.match(device)
        if not match:
            continue
        raw = ("" if raw_comment is None else str(raw_comment)).strip()
        comment = normalize_width(raw).strip()
        if "报警" not in comment:
            continue
        if "->" in comment or "<-" in comment:  # 通信/握手注释
            continue
        if any(word in comment for word in ("复位", "清除", "解除")):
            continue
        candidates.append((int(match.group(1)), device.upper(), raw, comment))

    result: Dict[str, Any] = {
        "base": 2000,
        "timer_base": 10,
        "naming": [],
        "examples": [],
        "ranges": [],
        "count": len(candidates),
        "evidence": [],
    }
    if not candidates:
        result["evidence"].append("注释中未发现“报警”类 M 位；报警基址沿用默认值 M2000 / T10")
    else:
        blocks = Counter(number // 100 * 100 for number, _, _, _ in candidates)
        base = sorted(blocks.items(), key=lambda item: (-item[1], item[0]))[0][0]
        result["base"] = base
        result["ranges"] = _group_numbers(number for number, _, _, _ in candidates)
        result["evidence"].append(
            "报警注释设备共 %d 个；最密集百位块为 M%d（%d 个），作为报警基址"
            % (len(candidates), base, blocks[base])
        )
        template_counter: Counter = Counter()
        for _, device, _raw, comment in candidates:
            body = _strip_own_device(comment, device)
            template_counter[_alarm_template(body)] += 1
        result["naming"] = [name for name, _ in template_counter.most_common(5)]
        ordered = sorted(candidates)
        result["examples"] = [
            _format_example(device, raw) for _, device, raw, _comment in ordered[:8]
        ]
        unused = [device for _, device, _raw, _comment in ordered if usage.get(device, 0) == 0]
        result["evidence"].append(
            "报警位在程序指令中被使用 %d/%d 个"
            % (len(ordered) - len(unused), len(ordered))
        )

    timers: List[Tuple[int, str, str, str]] = []
    for device, raw_comment in comments.items():
        match = _T_DEVICE_RE.match(device)
        if not match:
            continue
        raw = ("" if raw_comment is None else str(raw_comment)).strip()
        comment = normalize_width(raw).strip()
        if any(word in comment for word in ("报警", "延时", "超时")):
            timers.append((int(match.group(1)), device.upper(), raw, comment))
    if timers:
        timers.sort()
        timer_base = timers[0][0] // 10 * 10
        result["timer_base"] = timer_base
        result["timer_examples"] = [
            _format_example(device, raw) for _, device, raw, _comment in timers[:5]
        ]
        result["evidence"].append(
            "报警/延时类定时器 %d 个，最小 T%d，取十位基址 T%d"
            % (len(timers), timers[0][0], timer_base)
        )
    else:
        result["evidence"].append("注释中未发现报警延时定时器；定时器基址沿用默认值 T10")
    return result


def _infer_handshake(
    comments: Dict[str, str],
    segment_usage: Dict[str, List[str]],
    usage: Counter,
) -> Dict[str, Any]:
    """识别握手/交接信号：``A->B/序号/动作`` 与 ``移位标志：工位`` 两类。"""
    arrow: List[Dict[str, Any]] = []
    shift: List[Dict[str, Any]] = []
    for device, raw_comment in comments.items():
        if not _M_DEVICE_RE.match(device):
            continue
        raw = ("" if raw_comment is None else str(raw_comment)).strip()
        comment = normalize_width(raw).strip()
        if not comment:
            continue
        item = {"device": device.upper(), "comment": comment, "raw_comment": raw}
        if "->" in comment:
            arrow.append(item)
        elif "移位标志" in comment or "转盘标志" in comment:
            shift.append(item)
    candidates = arrow + shift

    result: Dict[str, Any] = {
        "base": 1000,
        "naming": [],
        "examples": [],
        "reused_signals": [],
        "count": len(candidates),
        "evidence": [],
    }
    if not candidates:
        result["evidence"].append("注释中未发现握手/移位标志；握手基址沿用默认值 M1000")
        return result

    blocks = Counter(int(_M_DEVICE_RE.match(item["device"]).group(1)) // 100 * 100 for item in candidates)
    base = sorted(blocks.items(), key=lambda item: (-item[1], item[0]))[0][0]
    result["base"] = base
    result["evidence"].append(
        "握手类信号 %d 个（箭头 %d / 移位标志 %d）；最密集百位块为 M%d（%d 个）"
        % (len(candidates), len(arrow), len(shift), base, blocks[base])
    )

    template_counter: Counter = Counter()
    for item in arrow:
        comment = item["comment"]
        if re.match(r"^[^/]+->[^/]+/\d+/", comment):
            template_counter["{src}->{dst}/{index}/{action}"] += 1
        elif "->" in comment:
            template_counter["{src}->{dst}/{action}"] += 1
    for item in shift:
        comment = item["comment"]
        if "移位标志" in comment:
            template_counter["移位标志：{station}"] += 1
        else:
            template_counter["转盘标志：{station}"] += 1
    result["naming"] = [name for name, _ in template_counter.most_common(5)]
    result["examples"] = [
        _format_example(item["device"], item.get("raw_comment") or item["comment"])
        for item in sorted(candidates, key=lambda entry: entry["device"])[:8]
    ]

    reused: List[Dict[str, Any]] = []
    for item in candidates:
        titles = segment_usage.get(item["device"], [])
        if len(titles) >= 2:
            reused.append({
                "device": item["device"],
                "comment": item.get("raw_comment") or item["comment"],
                "segment_count": len(titles),
                "segments": titles[:6],
                "used": usage.get(item["device"], 0),
            })
    reused.sort(key=lambda entry: (-entry["segment_count"], entry["device"]))
    result["reused_signals"] = reused[:10]
    if reused:
        result["evidence"].append(
            "跨段复用信号 %d 个，例如 %s 出现在 %d 个段"
            % (len(reused), reused[0]["device"], reused[0]["segment_count"])
        )
    else:
        result["evidence"].append("未发现同一手信号跨多个段使用（仍保留命名模板）")
    return result


# ---------------------------------------------------------------------------
# 命名 / 段标题 / 注释风格
# ---------------------------------------------------------------------------
_CATEGORY_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("初始化", ("INIT", "初始化")),
    ("报警", ("报警", "ALARM")),
    ("配方", ("配方", "RECIPE")),
    ("手动", ("手动", "MANUAL")),
    ("通信", ("通信", "通讯", "机器人", "视觉", "ENET", "RS232", "串口", "CC-LINK")),
    ("显示", ("显示", "HMI", "GT", "触摸屏")),
    ("驱动", ("驱动", "伺服", "DRIVE", "SERVO")),
    ("系统复归", ("复归", "回原点", "原点回归")),
    ("启动停止", ("启动停止", "启动", "停止")),
)


def segment_category(title: Any) -> str:
    """把段标题映射成生成器可用的规范类别（无法判断返回 ""）。"""
    text = normalize_width(title).strip()
    if not text:
        return ""
    upper = text.upper()
    if re.match(r"^SQ\d*", upper):
        return "设备段"
    for category, keywords in _CATEGORY_RULES:
        for keyword in keywords:
            if keyword.isascii():
                if keyword in upper:
                    return category
            elif keyword in text:
                return category
    station_words = ("工位", "上料", "下料", "搬运", "装配", "检测", "转盘", "压装",
                     "焊接", "铆压", "测量", "拧紧", "测试", "皮带线", "夹爪")
    if any(word in text for word in station_words):
        return "设备段"
    return ""


def _segment_prefix(title: str) -> str:
    """段标题前缀：``SQ1四联``→SQ、``INIT``→INIT、``报警``→报警；其它返回 ""。"""
    text = normalize_width(title).strip()
    if not text:
        return ""
    match = re.match(r"^([A-Za-z]+)", text)
    if match and len(match.group(1)) >= 2:
        return match.group(1).upper()
    if 1 <= len(text) <= 2 and not re.match(r"^[A-Za-z0-9]", text):
        return text
    return ""


def _infer_naming(
    segments: List[Dict[str, Any]],
    comments: Dict[str, str],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """段标题前缀 / 标题模板 / 注释常见形状。

    返回 (naming, comment_pattern_details)：
    - ``naming`` 里全是 str 列表/标量，保证 style.profile.summarize / 生成器可安全 join；
    - 详细统计（regex/count/examples）放在 profile 顶层 ``comment_patterns``，供 UI 解释。
    """
    prefix_counter: Counter = Counter()
    prefix_first_seen: Dict[str, int] = {}
    title_template_counter: Counter = Counter()
    for index, segment in enumerate(segments):
        title = segment.get("title") or ""
        prefix = _segment_prefix(title)
        if prefix:
            prefix_counter[prefix] += 1
            prefix_first_seen.setdefault(prefix, index)
        normalized = normalize_width(title).strip()
        template_match = re.match(r"^([A-Za-z]{1,})(\d+)(.*)$", normalized)
        if template_match:
            title_template_counter["%s{n}{station}" % template_match.group(1).upper()] += 1
    ordered_prefixes = sorted(
        prefix_counter, key=lambda name: (-prefix_counter[name], prefix_first_seen.get(name, 0))
    )

    text_comments = [str(comment).strip() for comment in comments.values() if (comment or "").strip()]
    pattern_defs = [
        ("device_prefix", r"^(?:[A-Za-z]{1,3}\d+)\s+", "注释以自身设备号开头（如“M1600 伺服压机报警”）"),
        ("fullwidth_colon", r"[：:]", "使用冒号分级（“气缸报警：xxx”）"),
        ("arrow", r"->", "使用箭头表达方向（“机器人->PLC/…”）"),
        ("slash", r"/", "使用斜杠分级（“…/序号/动作”）"),
        ("device_ref", r"[A-Za-z]{1,3}\d+", "注释中引用软元件号（X10/M500…）"),
        ("fullwidth", r"[\uFF01-\uFF5E\u3000]", "夹杂全角字符"),
        ("dash", r"[－-]", "使用短横连接说明"),
        ("paren", r"[（(]", "使用括号补充说明"),
    ]
    comment_patterns = []
    for key, regex, description in pattern_defs:
        matched = [text for text in text_comments if re.search(regex, text)]
        if not matched:
            continue
        comment_patterns.append({
            "key": key,
            "description": description,
            "regex": regex,
            "count": len(matched),
            "examples": matched[:3],
        })
    comment_patterns.sort(key=lambda item: -item["count"])

    total_chars = sum(len(text) for text in text_comments)
    cjk_chars = sum(len(CJK_RE.findall(text)) for text in text_comments)
    naming = {
        "segment_prefixes": ordered_prefixes[:12],
        "title_templates": [name for name, _ in title_template_counter.most_common(8)],
        # 简述即可，供 summarize/生成器直接拼字符串；细节在顶层 comment_patterns
        "comment_patterns": [item["description"] for item in comment_patterns[:8]],
        "has_fullwidth": any(has_fullwidth(comment) for comment in comments.values()),
        # 任务要求的注释风格统计（与 comment_stats 重复但便于命名相关模块就近读取）
        "comment_max_len": max((len(text) for text in text_comments), default=0),
        "comment_cjk_ratio": round(cjk_chars / total_chars, 2) if total_chars else 0.0,
    }
    return naming, comment_patterns[:8]


# ---------------------------------------------------------------------------
# 步号策略
# ---------------------------------------------------------------------------
_STEP_NON_STATION_RE = re.compile(
    r"INIT|初始化|系统复归|复归|启动停止|手动|自动|驱动|显示|报警|配方|通信|机器人|视觉|ENET|RS232|串口"
)


def _infer_stride(starts: List[int]) -> int:
    unique = sorted(set(starts))
    if len(unique) < 2:
        return 100
    diffs = [later - earlier for earlier, later in zip(unique, unique[1:]) if later > earlier]
    if not diffs:
        return 100
    if len(unique) <= 3 and len(set(diffs)) == 1:
        return diffs[0]
    counted = Counter(diffs)
    value, count = max(counted.items(), key=lambda item: (item[1], -item[0]))
    if count >= 2:
        return value
    for stride in (100, 50, 200, 1000):
        rest = Counter(number % stride for number in unique)
        if rest.most_common(1)[0][1] >= max(2, len(unique) * 0.6):
            return stride
    return 100


def _infer_step_strategy(
    step_ranges: List[Dict[str, Any]],
    step_values: Dict[str, List[int]],
) -> Dict[str, Any]:
    station_ranges = [
        item for item in step_ranges
        if not _STEP_NON_STATION_RE.search(normalize_width(item.get("segment") or ""))
    ]
    if not station_ranges:
        station_ranges = list(step_ranges)
    if not station_ranges:
        return {
            "station_start": 100,
            "station_stride": 100,
            "increment": 10,
            "station_count": 0,
            "evidence": ["未发现 STL 步序，工位步号策略沿用默认值 S100 / 步长 100 / 增量 10"],
        }

    starts = [int(item["start"]) for item in station_ranges]
    stride = _infer_stride(starts)
    station_start = starts[0]
    station_titles = {item["segment"] for item in station_ranges}

    increments: List[int] = []
    for name, values in step_values.items():
        if name not in station_titles:
            continue
        ordered = sorted(set(values))
        increments.extend(
            later - earlier for earlier, later in zip(ordered, ordered[1:]) if later > earlier
        )
    if increments:
        increment = max(Counter(increments).items(), key=lambda item: (item[1], -item[0]))[0]
    else:
        increment = 10

    return {
        "station_start": station_start,
        "station_stride": stride,
        "increment": increment,
        "station_count": len(station_ranges),
        "evidence": [
            "含 STL 的工位段 %d 个，首个工位起始 S%d" % (len(station_ranges), station_start),
            "工位起始值序列 %s…，段步长推断为 %d"
            % (", ".join(str(value) for value in sorted(set(starts))[:8]), stride),
            "步内相邻 S 步号增量众数为 %d" % increment,
        ],
    }


# ---------------------------------------------------------------------------
# 结构模板（STL 步 / 报警网络）
# ---------------------------------------------------------------------------
def _split_rungs(instructions: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    rungs: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for instruction in instructions:
        if is_load(instruction["op"]) and current:
            rungs.append(current)
            current = [instruction]
        else:
            current.append(instruction)
    if current:
        rungs.append(current)
    return rungs


def _rung_pattern(rung: List[Dict[str, Any]]) -> str:
    ops = [instruction["op"] for instruction in rung][:8]
    return "+".join(ops)


def _split_stl_steps(instructions: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    steps: List[List[Dict[str, Any]]] = []
    current: Optional[List[Dict[str, Any]]] = None
    for instruction in instructions:
        op = instruction["op"]
        if op == "STL":
            if current:
                steps.append(current)
            current = [instruction]
        elif op == "RETSTL":
            if current:
                steps.append(current)
            current = None
        elif current is not None:
            current.append(instruction)
    if current:
        steps.append(current)
    return steps


def _step_pattern(step: List[Dict[str, Any]]) -> Tuple[str, ...]:
    pattern: List[str] = ["STL"]
    for rung in _split_rungs(step[1:]):
        text = _rung_pattern(rung)
        if text:
            pattern.append(text)
        if len(pattern) >= 7:
            break
    return tuple(pattern)


def _build_templates(
    segments: List[Dict[str, Any]], segment_instructions: List[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    step_counter: Counter = Counter()
    alarm_counter: Counter = Counter()
    for segment, instructions in zip(segments, segment_instructions):
        for step in _split_stl_steps(instructions):
            step_counter[_step_pattern(step)] += 1
        if "报警" in normalize_width(segment.get("title") or "") or "ALARM" in (segment.get("title") or "").upper():
            for rung in _split_rungs(instructions):
                text = _rung_pattern(rung)
                if text:
                    alarm_counter[text] += 1
    templates: List[Dict[str, Any]] = []
    for pattern, count in step_counter.most_common(6):
        templates.append({"kind": "stl_step", "pattern": list(pattern), "count": count})
    for pattern, count in alarm_counter.most_common(3):
        templates.append({"kind": "alarm_rung", "pattern": [pattern], "count": count})
    return templates


# ---------------------------------------------------------------------------
# 设备范围 / 指令构成 / 注释统计
# ---------------------------------------------------------------------------
def _device_ranges(instructions: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    collected: Dict[str, Dict[str, Any]] = {}
    for instruction in instructions:
        for operand in instruction.get("operands") or []:
            parsed = parse_device(operand)
            if not parsed:
                continue
            cls, number, _bit = parsed
            token = operand.upper()
            info = collected.setdefault(cls, {"min": number, "max": number, "count": 0, "seen": set()})
            if token not in info["seen"]:
                info["seen"].add(token)
                info["count"] += 1
            info["min"] = min(info["min"], number)
            info["max"] = max(info["max"], number)
    result: Dict[str, Dict[str, int]] = {}
    for cls in _DEVICE_CLASS_ORDER:
        if cls in collected:
            info = collected[cls]
            result[cls] = {"min": info["min"], "max": info["max"], "count": info["count"]}
    return result


def _instruction_mix(
    instructions: List[Dict[str, Any]], limit: int = 40
) -> Tuple[Dict[str, int], int]:
    counter = Counter(instruction["op"] for instruction in instructions)
    total = sum(counter.values())
    ordered = counter.most_common(limit)
    mix = {name: count for name, count in ordered}
    rest = total - sum(mix.values())
    if rest > 0:
        mix["_other"] = rest
    return mix, total


def _comment_stats(
    comments: Dict[str, str], used_devices: Iterable[str]
) -> Dict[str, Any]:
    raw_texts = [str(comment).strip() for comment in comments.values() if (comment or "").strip()]
    total = len(raw_texts)
    if not total:
        return {
            "total": 0, "max_len": 0, "avg_len": 0.0, "cjk_ratio": 0.0,
            "has_fullwidth": False, "fullwidth_count": 0, "device_comment_coverage": 0.0,
        }
    lengths = [len(text) for text in raw_texts]
    chars = sum(lengths)
    cjk = sum(len(CJK_RE.findall(text)) for text in raw_texts)
    fullwidth_count = sum(1 for text in raw_texts if has_fullwidth(text))
    comment_keys = {str(key).upper() for key in comments}
    used = {str(device).upper() for device in used_devices}
    covered = sum(1 for device in used if device in comment_keys)
    return {
        "total": total,
        "max_len": max(lengths),
        "avg_len": round(chars / total, 1),
        "cjk_ratio": round(cjk / chars, 2) if chars else 0.0,
        "has_fullwidth": fullwidth_count > 0,
        "fullwidth_count": fullwidth_count,
        "device_comment_coverage": round(covered / len(used), 2) if used else 0.0,
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def _empty_profile(name: str) -> Dict[str, Any]:
    """以 ``style.profile.empty_profile`` 为基底，补齐 miner 额外字段。"""
    profile = empty_profile(name)
    profile.setdefault("project", {})
    profile.setdefault("stats", {
        "rows": 0, "instructions": 0, "segments": 0,
        "comments": 0, "stl_segments": 0, "fb_blocks": 0,
    })
    profile.setdefault("explain", [])
    profile.setdefault("warnings", [])
    profile.setdefault("comment_patterns", [])
    handshake = profile.get("handshake")
    if not isinstance(handshake, dict):
        handshake = {}
        profile["handshake"] = handshake
    handshake.setdefault("base", 1000)
    return profile


def _read_comments(comment_csv_text: Any) -> Dict[str, str]:
    if comment_csv_text is None:
        return {}
    value = comment_csv_text
    if isinstance(value, str) and "\t" not in value and "\n" not in value:
        path = Path(value)
        try:
            if path.exists():
                value = path.read_bytes()
        except OSError:
            pass
    return read_comment_csv(value)


def _read_program(program_csv_text: Any):
    value = program_csv_text
    if isinstance(value, str) and "\t" not in value and "\n" not in value:
        path = Path(value)
        try:
            if path.exists():
                value = path.read_bytes()
        except OSError:
            pass
    return read_program_csv(value)


def mine(program_csv_text: Any, comment_csv_text: Any, name: str) -> Dict[str, Any]:
    """从一组 GX Works3 CSV 学习风格画像（StyleProfile dict）。

    参数：
        program_csv_text: 程序本体 CSV 的文本/字节/路径。
        comment_csv_text: 软元件注释 CSV 的文本/字节/路径；可为 None。
        name: 画像名称（用户/项目风格名）。
    """
    profile = _empty_profile(name)
    warnings: List[str] = profile["warnings"]
    try:
        program = _read_program(program_csv_text)
        comments = _read_comments(comment_csv_text)
    except Exception as exc:  # 用户输入 CSV 不可预测，宁可返回空画像也不崩溃
        warnings.append("程序/注释 CSV 读取失败：%s: %s" % (type(exc).__name__, exc))
        return profile

    try:
        _analyze(profile, program, comments)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        warnings.append("画像分析中断：%s: %s" % (type(exc).__name__, exc))
    return profile


def _analyze(profile: Dict[str, Any], program: Any, comments: Dict[str, str]) -> None:
    warnings: List[str] = profile["warnings"]

    instructions: List[Dict[str, Any]] = []
    for item in iter_instructions(program):
        normalized = _normalize_instruction(item)
        if normalized:
            instructions.append(normalized)

    raw_segments = extract_segments(program)
    segments: List[Dict[str, Any]] = []
    segment_instructions: List[List[Dict[str, Any]]] = []
    fb_block_count = 0
    for raw in raw_segments:
        title = (raw.get("title") or "").strip()
        current = [item for item in (
            _normalize_instruction(raw_instruction)
            for raw_instruction in (raw.get("instructions") or [])
        ) if item]
        if _FB_BLOCK_TITLE_RE.match(title):
            fb_block_count += 1
            continue
        if not title and not current:
            continue
        segments.append({
            "order": len(segments) + 1,
            "title": title,
            "kind": raw.get("kind") or "body",
            "network_count": _network_count(current),
            "instruction_count": len(current),
            "has_stl": any(item["op"] == "STL" for item in current),
        })
        segment_instructions.append(current)

    step_ranges: List[Dict[str, Any]] = []
    step_values: Dict[str, List[int]] = {}
    for segment, current in zip(segments, segment_instructions):
        numbers = _stl_numbers(current)
        if not numbers:
            continue
        title = segment["title"] or "（未命名段）"
        step_ranges.append({
            "segment": title,
            "start": min(numbers),
            "end": max(numbers),
            "count": len(set(numbers)),
        })
        step_values[title] = numbers

    usage: Counter = Counter()
    for instruction in instructions:
        for device in _devices_of(instruction):
            usage[device] += 1

    segment_usage: Dict[str, List[str]] = {}
    for segment, current in zip(segments, segment_instructions):
        seen = set()
        for instruction in current:
            for device in _devices_of(instruction):
                if device in seen:
                    continue
                seen.add(device)
                segment_usage.setdefault(device, []).append(segment["title"])

    used_devices: List[str] = list(usage.keys())
    device_ranges = _device_ranges(instructions)
    instruction_mix, instruction_total = _instruction_mix(instructions)
    naming, comment_pattern_details = _infer_naming(segments, comments)
    comment_stats = _comment_stats(comments, used_devices)
    modes = _infer_modes(comments, usage)
    alarm = _infer_alarm(comments, usage)
    handshake = _infer_handshake(comments, segment_usage, usage)
    step_strategy = _infer_step_strategy(step_ranges, step_values)
    templates = _build_templates(segments, segment_instructions)

    profile["segments"] = segments
    profile["step_ranges"] = step_ranges
    profile["modes"] = modes
    profile["alarm"] = alarm
    profile["handshake"] = handshake
    profile["naming"] = naming
    profile["comment_patterns"] = comment_pattern_details
    profile["device_ranges"] = device_ranges
    profile["instruction_mix"] = instruction_mix
    profile["comment_stats"] = comment_stats
    profile["step_strategy"] = step_strategy
    profile["templates"] = templates
    profile["project"] = {"name": program.project, "cpu": program.cpu}
    profile["stats"] = {
        "rows": len(program.rows),
        "instructions": instruction_total,
        "segments": len(segments),
        "comments": len(comments),
        "stl_segments": len(step_ranges),
        "fb_blocks": fb_block_count,
    }

    # ---- 可解释摘要 ----
    explain: List[str] = []
    if segments:
        station_like = sum(1 for segment in segments if segment["kind"] == "title")
        explain.append(
            "识别到 %d 个段（[Title] 标题段 %d 个，语句行段 %d 个），其中 %d 个含 STL。"
            % (len(segments), station_like, len(segments) - station_like, len(step_ranges))
        )
    if segments:
        titles = [segment["title"] for segment in segments if segment["title"]][:6]
        explain.append("段标题样例：" + " / ".join(titles) + (" …" if len(segments) > 6 else ""))
    if step_ranges:
        explain.append(
            "STL 步号段 %d 个，首个工位起始 S%s，段步长 %s，步内增量 %s。"
            % (len(step_ranges),
               step_strategy.get("station_start"),
               step_strategy.get("station_stride"),
               step_strategy.get("increment"))
        )
    if modes:
        explain.append("模式位：" + "，".join(
            "%s=%s" % (mode, info.get("device", "")) for mode, info in modes.items()
        ))
    if alarm.get("naming") or alarm.get("examples"):
        explain.append(
            "报警段：M%d 起，命名模板 %s，样例 %s。"
            % (alarm["base"], "、".join(alarm.get("naming") or []),
               (alarm.get("examples") or [""])[0])
        )
    elif alarm.get("count"):
        explain.append("报警段：M%d 起（未归纳出命名模板）。" % alarm["base"])
    if handshake.get("naming") or handshake.get("examples"):
        explain.append(
            "握手段：M%d 起，模板 %s，样例 %s。"
            % (handshake["base"], "、".join(handshake.get("naming") or []),
               (handshake.get("examples") or [""])[0])
        )
    if naming.get("segment_prefixes"):
        explain.append("段标题前缀：" + "、".join(naming["segment_prefixes"][:8]))
    if comment_pattern_details:
        explain.append("注释形状：" + "、".join(
            "%s×%d" % (item["key"], item["count"]) for item in comment_pattern_details[:5]
        ))
    if comment_stats.get("total"):
        explain.append(
            "注释 %d 条：最长 %d 字，CJK 比例 %.2f，%s全角写法。"
            % (comment_stats["total"], comment_stats["max_len"],
               comment_stats["cjk_ratio"], "含" if comment_stats["has_fullwidth"] else "不含")
        )
    profile["explain"] = explain

    # ---- 告警/说明 ----
    if not instructions:
        warnings.append("程序 CSV 未解析出任何指令，请确认导入的是 GX Works3 导出的“程序本体 CSV”。")
    if not comments:
        warnings.append(
            "未提供软元件注释 CSV：模式位/报警/握手/命名只能按指令使用情况与常用约定推断，"
            "建议同时导入“软元件注释 CSV”以提升画像准确度。"
        )
    if fb_block_count:
        warnings.append(
            "检测到 %d 个 FB 块标记（*;FB BLK START/END），已从段结构统计中排除。" % fb_block_count
        )
    if segments:
        warnings.append("CSV 无网络边界，segments[].network_count 为按 LD/STL 起始指令估算的近似值。")
    if not step_ranges:
        warnings.append("未发现 STL 步序（可能为纯梯形图程序），步号策略无法学习，将使用默认值。")


# ---------------------------------------------------------------------------
# 对外导出
# ---------------------------------------------------------------------------
__all__ = [
    "PROFILE_VERSION",
    "mine",
    "normalize_width",
    "has_fullwidth",
    "op_key",
    "segment_category",
    "parse_device",
    "is_load",
]
