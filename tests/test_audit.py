"""gx3gen.audit 与生成器 GX 导入自检的回归测试。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gx3gen.audit import audit, audit_comment, audit_program  # noqa: E402
from gx3gen.generator import generate  # noqa: E402
from gx3gen.spec import load_spec  # noqa: E402


def write_program(path: Path, rows) -> None:
    lines = []
    for row in rows:
        lines.append("\t".join('"%s"' % str(cell) for cell in row))
    text = "\r\n".join(lines) + "\r\n"
    path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))


def program_rows(extra_stl: bool = True):
    rows = [
        ["测试项目"],
        ["设备信息:", "FX5CPU FX5U"],
        ["步号", "行间声明", "指令", "I/O(软元件)", "空白列", "PI信息", "注解"],
        ["0", "[Title]初始化", "", "", "", "", ""],
        ["15", "", "LD", "SM402", "", "", ""],
        ["17", "", "SET", "M1", "", "", ""],
        ["", "", "", "M2", "", "", ""],                # SET 续行：多操作数（坏）
        ["19", "", "LD", "X0", "", "", ""],
        ["20", "", "LDI", "X1", "", "", ""],            # LD 后紧跟 LDI（坏）
        ["22", "", "OUT", "Y0", "", "", ""],
        ["", "", "", "", "", "", "rung 结束"],
        ["24", "", "STL", "S10", "", "", ""],
        ["26", "", "LD", "S10", "", "", ""],
        ["28", "", "SET", "S20", "", "", ""],
        ["30", "", "RETSTL", "", "", "", ""],
        ["", "", "", "", "", "", "rung 结束 2"],
    ]
    if extra_stl:
        rows.extend([
            ["32", "", "STL", "S30", "", "", ""],       # 多一个 STL 无 RETSTL（坏）
            ["", "", "", "", "", "", "rung 结束 3"],
        ])
    rows.append(["100", "", "END", "", "", "", ""])
    return rows


class AuditTest(unittest.TestCase):
    def test_generated_traffic_light_passes_audit(self):
        spec = load_spec(str(ROOT / "samples" / "traffic_light.spec.json"))
        requirement = (ROOT / "samples" / "traffic_light.requirement.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            result = generate(spec, None, tmp, requirement=requirement)
            self.assertTrue(result["audit"]["ok"], result["audit"])
            self.assertIn("GX 导入自检通过", result["summary"])
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("GX 导入自检", report)
            self.assertIn("通过", report)
            self.assertFalse(audit_program(result["program_csv"]))
            self.assertFalse(audit_comment(result["comment_csv"],
                                          expected_title="Chinese Simplified/简体中文"))

    def test_bad_program_csv_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "program.csv"
            write_program(path, program_rows())
            problems = audit_program(str(path))
        joined = "\n".join(problems)
        self.assertIn("SET 操作数", joined)
        self.assertIn("LDI", joined)
        self.assertIn("STL", joined)

    def test_bad_comment_csv_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "comments.csv"
            lines = ['"测试项目"', '"软元件名"\t"注释"', '"Y0"\t"灯"']
            path.write_bytes(b"\xff\xfe" + "\r\n".join(lines).encode("utf-16-le") + b"\r\n")
            problems = audit_comment(str(path), expected_title="Chinese Simplified/简体中文")
        joined = "\n".join(problems)
        self.assertIn("CRLF", joined)
        self.assertIn("注释标题不匹配", joined)

    def test_audit_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "program.csv"
            write_program(program, program_rows(extra_stl=False))
            comments = Path(tmp) / "comments.csv"
            comments.write_bytes(b"\xff\xfe" + '"项目"\n"软元件名"\t"注释"\n"Y0"\t"灯"\n'.encode("utf-16-le"))
            result = audit(str(program), str(comments),
                           expected_comment_title="Chinese Simplified/简体中文")
        self.assertFalse(result["ok"])
        self.assertTrue(result["program_problems"])
        self.assertTrue(result["comment_problems"])
        self.assertEqual(len(result["problems"]),
                         len(result["program_problems"]) + len(result["comment_problems"]))


if __name__ == "__main__":
    unittest.main()
