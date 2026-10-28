import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gx3gen.ilcsv import rows as iorows  # noqa: E402
from gx3gen.ilcsv.emitter import emit, emit_csv, instruction_steps  # noqa: E402
from gx3gen.ir import Instruction, Program, Rung, Segment  # noqa: E402


def sample_program():
    return Program(project="测试项目", cpu="FX5CPU FX5U", segments=[
        Segment(title="初始化", kind="init", rungs=[
            Rung([Instruction("LD", ["SM402"]), Instruction("SET", ["S0"])], "初始")]),
        Segment(title="流程", kind="station", rungs=[
            Rung([Instruction("STL", ["S10"])], "STL S10"),
            Rung([Instruction("LD", ["S10"]), Instruction("SET", ["Y0"])], "输出"),
            Rung([Instruction("LD", ["S10"]), Instruction("AND", ["X3"]),
                  Instruction("SET", ["S20"])], "转移"),
            Rung([Instruction("RETSTL", [])], "结束"),
        ]),
    ])


class ILCSVTest(unittest.TestCase):
    def test_step_sizes(self):
        self.assertEqual(instruction_steps(Instruction("LD", ["X0"])), 2)
        self.assertEqual(instruction_steps(Instruction("STL", ["S10"])), 3)
        self.assertEqual(instruction_steps(Instruction("RETSTL", [])), 1)
        self.assertEqual(instruction_steps(Instruction("SET", ["S10"])), 3)
        self.assertEqual(instruction_steps(Instruction("SET", ["Y0"])), 2)
        self.assertEqual(instruction_steps(Instruction("OUT", ["T0", "K20"])), 5)
        self.assertEqual(instruction_steps(Instruction("END", [])), 1)

    def test_emit_and_read_back(self):
        program = sample_program()
        prog, stats = emit(program)
        self.assertGreater(stats["instructions"], 0)
        self.assertEqual(prog.rows[-1].instr, "END")
        # 末尾 END 的步号 = 之前累计步数
        self.assertEqual(prog.rows[-1].step, str(stats["steps"] - 1))
        # 第一个段标题
        self.assertEqual(prog.rows[0].decl, "[Title]初始化")
        # 多操作数续行（构造一个 OUT T）
        program2 = sample_program()
        program2.segments[1].rungs.append(
            Rung([Instruction("LD", ["S20"]), Instruction("OUT", ["T0", "K20"])], "延时"))
        prog2, _ = emit(program2)
        continuation = [row for row in prog2.rows if row.instr == "" and row.io == "K20"]
        self.assertEqual(len(continuation), 1)
        self.assertEqual(continuation[0].step, "")

    def test_write_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            program_csv = str(Path(tmp) / "program.csv")
            comment_csv = str(Path(tmp) / "comments.csv")
            stats = emit_csv(sample_program(), program_csv, comment_csv,
                             {"X0": "启动", "Y0": "竖直缸", "M500": "运行中"})
            raw = Path(program_csv).read_bytes()
            self.assertTrue(raw.startswith(b"\xff\xfe"), "必须 UTF-16LE+BOM")
            self.assertIn(b"\r\x00\n\x00", raw, "必须 CRLF")
            prog = iorows.read_program_csv(program_csv)
            self.assertEqual(prog.project, "测试项目")
            self.assertEqual(prog.cpu, "FX5CPU FX5U")
            instructions = iorows.iter_instructions(prog)
            self.assertEqual(instructions[0]["op"], "LD")
            self.assertEqual(instructions[0]["operands"], ["SM402"])
            markers = iorows.iter_markers(prog)
            self.assertEqual(markers[0]["title"], "初始化")
            comments = iorows.read_comment_csv(comment_csv)
            self.assertEqual(comments["Y0"], "竖直缸")
            self.assertEqual(stats["comments"], 3)

    def test_quotes_are_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = Program(project='项目 "A"', cpu="FX5CPU FX5U", segments=[
                Segment(title="段", kind="rungs", rungs=[
                    Rung([Instruction("LD", ["X0"])], '含"引号"的注释')])])
            path = str(Path(tmp) / "p.csv")
            emit_csv(program, path, str(Path(tmp) / "c.csv"), {})
            prog = iorows.read_program_csv(path)
            self.assertEqual(prog.project, '项目 "A"')
            notes = [row.note for row in prog.rows if row.note]
            self.assertIn('含"引号"的注释', notes)

    def test_comment_csv_default_title_and_lf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "comments.csv")
            iorows.write_comment_csv("测试项目", {"Y0": "灯", "X0": "启动"}, path)
            raw = Path(path).read_bytes()
            self.assertTrue(raw.startswith(b"\xff\xfe"), "必须 UTF-16LE+BOM")
            self.assertNotIn(b"\r\n", raw, "注释 CSV 行尾必须为 LF（GX 1.082 真实导出）")
            text = raw[2:].decode("utf-16-le")
            self.assertIn("Chinese Simplified/简体中文", text)
            comments = iorows.read_comment_csv(path)
            self.assertEqual(comments["Y0"], "灯")

    def test_comment_csv_custom_titles_and_multicolumn_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "comments.csv")
            iorows.write_comment_csv("测试项目", {"Y0": "灯"}, path,
                                     comment_titles=["注释", "Chinese Simplified/简体中文"])
            text = Path(path).read_bytes()[2:].decode("utf-16-le")
            self.assertIn('"软元件名"\t"注释"\t"Chinese Simplified/简体中文"', text)
            comments = iorows.read_comment_csv(path)
            self.assertEqual(comments["Y0"], "灯")

    def test_comment_csv_default_title_constant(self):
        self.assertEqual(iorows.COMMENT_TITLE, "Chinese Simplified/简体中文")


if __name__ == "__main__":
    unittest.main()
