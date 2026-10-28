import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gx3gen.generator import build_program, generate  # noqa: E402
from gx3gen.ilcsv.rows import (iter_instructions, iter_markers,  # noqa: E402
                               read_comment_csv, read_program_csv)
from gx3gen.spec import SpecError, load_spec  # noqa: E402


class GeneratorTest(unittest.TestCase):
    def setUp(self):
        self.spec = load_spec(str(ROOT / "samples" / "three_station.spec.json"))

    def test_generate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate(self.spec, None, tmp)
            for key in ("program_csv", "comment_csv", "network_table", "report"):
                self.assertTrue(Path(result[key]).exists(), key)
            self.assertGreater(result["stats"]["instructions"], 20)
            self.assertGreaterEqual(result["stats"]["segments"], 4)
            prog = read_program_csv(result["program_csv"])
            markers = [item["title"] for item in iter_markers(prog) if item["title"]]
            self.assertIn("初始化", markers)
            self.assertIn("取放站", markers)
            self.assertIn("启动停止", markers)
            self.assertIn("报警", markers)
            instructions = iter_instructions(prog)
            ops = [item["op"] for item in instructions]
            self.assertIn("STL", ops)
            self.assertIn("RETSTL", ops)
            self.assertEqual(ops[-1], "END")
            steps = [item["operands"][0] for item in instructions if item["op"] == "STL"]
            self.assertEqual(steps, ["S100", "S110", "S120", "S130", "S140", "S150", "S160"])
            comments = read_comment_csv(result["comment_csv"])
            self.assertEqual(comments.get("X3"), "竖直缸到位")
            self.assertEqual(comments.get("M1000"), "取放站/完成")
            self.assertIn("M2000", comments)
            self.assertEqual(result["segments"][1]["kind"], "station")

    def test_auto_init_when_missing(self):
        spec = copy.deepcopy(self.spec)
        spec["segments"] = [segment for segment in spec["segments"] if segment["kind"] != "init"]
        program = build_program(spec, None)
        self.assertEqual(program.segments[0].title, "初始化")
        text = " ".join(" ".join(ins.operands) for ins in program.segments[0].rungs[0].instructions)
        self.assertIn("SM402", text)
        self.assertIn("S0", text)

    def test_alarm_segment_generated(self):
        program = build_program(self.spec, None)
        titles = [segment.title for segment in program.segments]
        self.assertIn("报警", titles)
        alarm = [segment for segment in program.segments if segment.title == "报警"][0]
        instructions = [ins for rung in alarm.rungs for ins in rung.instructions]
        ops = [ins.op for ins in instructions]
        self.assertIn("OUT", ops)
        self.assertIn("ZRST", ops)
        self.assertTrue(any(ins.operands and ins.operands[0].startswith("M20") for ins in instructions))

    def test_invalid_spec_raises(self):
        spec = copy.deepcopy(self.spec)
        spec["project"]["cpu"] = "Q"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SpecError):
                generate(spec, None, tmp)

    def test_report_and_network_table_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate(self.spec, None, tmp)
            table = Path(result["network_table"]).read_text(encoding="utf-8")
            self.assertIn("## 取放站", table)
            self.assertIn("STL S100", table)
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("GX Works3", report)
            self.assertIn("导入文件", report)
            self.assertIn("取放站", report)


if __name__ == "__main__":
    unittest.main()
