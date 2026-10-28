"""GX Works3 CSV 导入前自检（不依赖 GX，按 FX5U 指令集模拟导入检查）。

从 `tools/gx_audit.py` 抽出为包内模块，供 CLI / MCP / 生成器复用：
- 程序本体 CSV：BOM / 行尾 / 表头列数 / END / 操作数个数 / rung 内 IL 序列 / STL-RETSTL 配对
- 软元件注释 CSV：BOM / 行尾 LF / 标题行 / 注释标题（可选按目标工程校验）

背景与真实报错案例见 docs/GX_IMPORT_TROUBLESHOOTING.md。
"""
from __future__ import annotations

import csv
import io
import pathlib
import sys
from typing import Dict, List, Optional

# FX5U 指令 → 允许的操作数个数 (min, max)。
# SET/RST 必须 1 个操作数；多设备要拆成多条指令（GX 报「指令不正确」）。
LIMITS = {
    "LD": (1, 1), "LDI": (1, 1), "AND": (1, 1), "ANI": (1, 1),
    "OR": (1, 1), "ORI": (1, 1), "ORB": (0, 0), "ANB": (0, 0),
    "MPS": (0, 0), "MRD": (0, 0), "MPP": (0, 0), "INV": (0, 0),
    "OUT": (1, 2), "SET": (1, 1), "RST": (1, 1),
    "ALT": (1, 1), "PLS": (1, 1), "PLF": (1, 1),
    "STL": (1, 1), "RETSTL": (0, 0),
    "MOV": (2, 2), "MOVP": (2, 2), "DMOV": (2, 2), "ZRST": (2, 2),
    "CMP": (3, 3), "CMPP": (3, 3),
    "ADD": (3, 3), "SUB": (3, 3), "MUL": (3, 3), "DIV": (3, 3),
    # 下面这些在生成器里可能用到，暂不做个数约束（只要求是合法指令）
    "MC": None, "MCR": None, "CJ": None, "JMP": None, "CALL": None,
    "SRET": None, "FOR": None, "NEXT": None, "NOP": None,
    "END": (0, 0),
}
LOAD_OPS = {"LD", "LDI"}
BRANCH_OPS = {"ORB", "ANB", "MPS", "MRD", "MPP"}


def unq(value: str) -> str:
    return value.strip().strip('"').strip()


def read_text(path: pathlib.Path) -> str:
    """按 GX 常见编码读取：UTF-16LE+BOM / UTF-8 BOM / 回退 UTF-8。"""
    raw = pathlib.Path(path).read_bytes()
    if raw[:2] == b"\xff\xfe":
        return raw[2:].decode("utf-16-le")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    return raw.decode("utf-8", "replace")


def _valid_op_set() -> set:
    try:
        from .spec import VALID_OPS
        return set(VALID_OPS)
    except Exception:  # pragma: no cover - 独立运行兜底
        return set(LIMITS)


def audit_program(path) -> List[str]:
    """检查程序本体 CSV；返回问题列表（空列表 = 通过）。"""
    path = pathlib.Path(path)
    problems: List[str] = []
    raw = path.read_bytes()
    if raw[:2] != b"\xff\xfe":
        problems.append("缺少 UTF-16LE BOM（GX 期望 FF FE）")
    text = raw[2:].decode("utf-16-le") if raw[:2] == b"\xff\xfe" else read_text(path)
    if "\n" in text.replace("\r\n", ""):
        problems.append("存在裸 LF 行尾（程序本体 CSV 应全程 CRLF）")

    rows = []
    for index, line in enumerate(text.split("\r\n"), start=1):
        if not line.strip():
            continue  # 忽略文件末尾空行
        cols = line.split("\t")
        if len(cols) != 7:
            if index > 3:
                problems.append("第 %d 行列数异常: %d 列" % (index, len(cols)))
            continue
        rows.append((index, unq(cols[1]), unq(cols[2]), unq(cols[3]), unq(cols[6])))

    # 逐条指令：操作数个数 + IL 序列
    # rung 边界：注解行（第 7 列非空）标记一个 rung 结束，其后的指令是新 rung 的开头；
    # 段标题行同样是边界。因此只在**同一 rung 内**检查 LD/LDI 连续问题。
    instructions = []
    current = None
    new_rung = True
    for index, decl, instr, io, note in rows:
        if decl:
            current = None
            new_rung = True
            continue
        if instr:
            current = {"line": index, "op": instr, "operands": [], "new_rung": new_rung}
            instructions.append(current)
            new_rung = False
            if io:
                current["operands"].append(io)
            continue
        if io:
            if current is None:
                problems.append("第 %d 行：续行没有对应的指令行" % index)
                continue
            current["operands"].append(io)
            continue
        if note:
            current = None
            new_rung = True

    valid_ops = _valid_op_set()
    for item in instructions:
        op = item["op"]
        count = len(item["operands"])
        if op not in valid_ops and op not in LIMITS:
            problems.append("第 %d 行：未知指令 %s" % (item["line"], op))
            continue
        limits = LIMITS.get(op)
        if limits is None:
            continue
        low, high = limits
        if not (low <= count <= high):
            expect = str(low) if low == high else "%d~%d" % (low, high)
            problems.append("第 %d 行：%s 操作数 %d 个（应为 %s）-> %s"
                            % (item["line"], op, count, expect, " ".join(item["operands"])))

    # IL 序列：同一 rung 内 LD/LDI 后不得紧跟另一个 LD/LDI
    prev_load = ""
    prev_desc = ""
    prev_line = 0
    for item in instructions:
        op = item["op"]
        desc = ("%s %s" % (op, " ".join(item["operands"]))).strip()
        if item.get("new_rung"):
            prev_load = ""  # 新 rung，之前的载入指令不影响本 rung
        if op in LOAD_OPS:
            if prev_load in LOAD_OPS:
                problems.append(
                    "第 %d 行：`%s` 紧跟在第 %d 行 `%s` 之后：LD/LDI 是载入指令，"
                    "会丢弃前一条结果并开启新支路（悬空支路 → 「梯形图配置不正确」）；"
                    "串联条件用 AND，串联取反用 ANI"
                    % (item["line"], desc, prev_line, prev_desc))
            prev_load, prev_desc, prev_line = op, desc, item["line"]
        elif op in BRANCH_OPS:
            prev_load, prev_desc, prev_line = "", desc, item["line"]
        else:
            prev_desc, prev_line = desc, item["line"]

    stl = [item for item in instructions if item["op"] == "STL"]
    retstl = [item for item in instructions if item["op"] == "RETSTL"]
    if len(stl) != len(retstl):
        problems.append("STL(%d) 与 RETSTL(%d) 数量不等：每个 STL 步块应配一个 RETSTL"
                        % (len(stl), len(retstl)))
    if not any(item["op"] == "END" for item in instructions):
        problems.append("缺少 END 行")
    return problems


def audit_comment(path, expected_title: Optional[str] = None) -> List[str]:
    """检查软元件注释 CSV；expected_title 给定时还校验注释标题名。"""
    path = pathlib.Path(path)
    problems: List[str] = []
    raw = path.read_bytes()
    if raw[:2] != b"\xff\xfe":
        problems.append("缺少 UTF-16LE BOM")
    text = raw[2:].decode("utf-16-le") if raw[:2] == b"\xff\xfe" else read_text(path)
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter="\t"))
    if len(rows) < 2:
        problems.append("内容过少，缺少工程名行与标题行")
        return problems
    if len(rows[0]) != 1:
        problems.append("第 1 行应为单字段工程名，实际 %d 列" % len(rows[0]))
    titles = [c.strip() for c in rows[1][1:] if c.strip()]
    if not titles:
        problems.append("第 2 行缺少注释标题")
    elif len(rows[1]) != 2:
        problems.append("第 2 行应为「软元件名」+ 注释标题共 2 列，实际 %d 列" % len(rows[1]))
    if rows[1] and "软元件名" not in rows[1][0]:
        problems.append("第 2 行第 1 列应为「软元件名」，实际 %r" % rows[1][0])
    if expected_title and expected_title.strip() not in titles:
        problems.append("注释标题不匹配：期望 %r，实际 %r（GX 会报「注释标题不存在」）"
                        % (expected_title.strip(), titles))
    # 行尾：LF 风格（CRLF 占比低）
    if rows and text.count("\r\n") >= max(1, len(rows)) * 0.5:
        problems.append("行尾疑似 CRLF：GX 注释 CSV 导出样例为 LF，"
                        "CRLF 会让单元格取值残留 \\r → 报「注释标题不存在」")
    return problems


def audit(program_path, comment_path=None,
          expected_comment_title: Optional[str] = None) -> Dict[str, object]:
    """自检入口：返回 {ok, problems, program_problems, comment_problems}。"""
    program_problems = audit_program(program_path)
    comment_problems: List[str] = []
    if comment_path:
        comment_problems = audit_comment(comment_path, expected_title=expected_comment_title)
    problems = list(program_problems) + list(comment_problems)
    return {
        "ok": not problems,
        "problems": problems,
        "program_problems": program_problems,
        "comment_problems": comment_problems,
    }


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 2:
        print(__doc__)
        return 2
    program = pathlib.Path(argv[1])
    comments = None
    expected_title = None
    if "--check" in argv:
        index = argv.index("--check")
        if index + 1 < len(argv):
            comments = pathlib.Path(argv[index + 1])
    if "--expect-title" in argv:
        index = argv.index("--expect-title")
        if index + 1 < len(argv):
            expected_title = argv[index + 1]

    print("=== 程序本体 CSV: %s ===" % program.name)
    program_problems = audit_program(program)
    if program_problems:
        for item in program_problems:
            print("   !!", item)
    else:
        print("   OK：格式 / 操作数个数 / IL 序列 / STL 配对 均通过")

    comment_problems: List[str] = []
    if comments is not None:
        print()
        print("=== 软元件注释 CSV: %s ===" % comments.name)
        comment_problems = audit_comment(comments, expected_title=expected_title)
        if comment_problems:
            for item in comment_problems:
                print("   !!", item)
        else:
            print("   OK：格式 / 标题行 / 行尾 均通过")

    problems = program_problems + comment_problems
    print()
    if problems:
        print("共发现 %d 个问题（详见 docs/GX_IMPORT_TROUBLESHOOTING.md）" % len(problems))
        return 1
    print("全部检查通过")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
