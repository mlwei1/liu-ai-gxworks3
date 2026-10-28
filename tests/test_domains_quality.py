import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gx3gen.domains import build_traffic_light_spec, detect_domain  # noqa: E402
from gx3gen.generator import generate  # noqa: E402
from gx3gen.ilcsv.rows import (iter_instructions, iter_markers,  # noqa: E402
                               read_program_csv)
from gx3gen.planner.deterministic import plan_deterministic  # noqa: E402
from gx3gen.planner.errors import PlannerError  # noqa: E402
from gx3gen.quality import QualityError, check_quality  # noqa: E402
from gx3gen.spec import validate_spec  # noqa: E402

REQUIREMENT = (ROOT / "samples" / "traffic_light.requirement.md").read_text(encoding="utf-8")
IO = [
    {"device": "X0", "comment": "启动", "role": "input"},
    {"device": "X1", "comment": "停止", "role": "input"},
    {"device": "Y0", "comment": "东西红灯", "role": "output"},
    {"device": "Y1", "comment": "东西黄灯", "role": "output"},
    {"device": "Y2", "comment": "东西绿灯", "role": "output"},
    {"device": "Y3", "comment": "南北红灯", "role": "output"},
    {"device": "Y4", "comment": "南北黄灯", "role": "output"},
    {"device": "Y5", "comment": "南北绿灯", "role": "output"},
]


class DomainTest(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(detect_domain(REQUIREMENT), "traffic_light")
        self.assertIsNone(detect_domain("普通输送线控制"))

    def test_traffic_light_spec_structure(self):
        spec = build_traffic_light_spec(REQUIREMENT, IO)
        validate_spec(spec)
        self.assertEqual([segment["kind"] for segment in spec["segments"]],
                         ["init", "common", "station"])
        station = spec["segments"][2]
        self.assertEqual(station["title"], "自动流程")
        self.assertEqual(len(station["steps"]), 6)
        self.assertEqual(station["steps"][0]["step"], 10)
        self.assertEqual(station["steps"][-1]["transition"]["next"], 10)  # 循环
        self.assertEqual(station["entry_conditions"], ["S0", "M500"])
        # 六路灯 + 定时器
        lamps = [item["device"] for item in spec["io"] if item["device"].startswith("Y")]
        self.assertEqual(len(lamps), 6)
        timers = [ins for step in station["steps"] for action in step["actions"]
                  for ins in action["instructions"] if ins["op"] == "OUT"]
        self.assertEqual(len(timers), 6)

    def test_quality_passes(self):
        spec = build_traffic_light_spec(REQUIREMENT, IO)
        quality = check_quality(spec, REQUIREMENT)
        self.assertTrue(quality["ok"], quality["errors"])
        self.assertTrue(quality["stats"]["has_cycle"])
        self.assertTrue(quality["stats"]["has_timer"])
        self.assertTrue(quality["stats"]["has_start_stop"])
        self.assertEqual(quality["stats"]["lamps"], 6)


class TrafficLightGenerationTest(unittest.TestCase):
    def test_generate_traffic_light_program(self):
        spec = plan_deterministic(REQUIREMENT, IO)
        with tempfile.TemporaryDirectory() as tmp:
            result = generate(spec, None, tmp, requirement=REQUIREMENT)
            self.assertTrue(Path(result["program_csv"]).exists())
            self.assertTrue(result["quality"]["ok"])
            prog = read_program_csv(result["program_csv"])
            markers = [item["text"] for item in iter_markers(prog) if item["title"]]
            self.assertIn("[Title]自动流程", markers)
            instructions = iter_instructions(prog)
            ops = [item["op"] for item in instructions]
            self.assertEqual(ops.count("STL"), 6)
            # FX5U 标准写法：一个 STL 步块配一个 RETSTL
            self.assertEqual(ops.count("RETSTL"), 6)
            timers = [item["operands"] for item in instructions
                      if item["op"] == "OUT" and item["operands"]
                      and str(item["operands"][0]).startswith("T")]
            self.assertEqual(len(timers), 6)
            self.assertEqual(timers[0], ["T0", "K250"])
            self.assertEqual(ops[-1], "END")
            # 自动循环：S10 既是进入步也被最后一步重新置位
            set_s10 = [item for item in instructions
                       if item["op"] == "SET" and item["operands"] == ["S10"]]
            self.assertGreaterEqual(len(set_s10), 2)
            # 段标题、启动停止、软元件注释
            raw = Path(result["program_csv"]).read_bytes()
            self.assertTrue(raw.startswith(b"\xff\xfe"))

    def test_structured_requirement_still_works(self):
        requirement = (ROOT / "samples" / "three_station.requirement.md").read_text(encoding="utf-8")
        io = [
            {"device": "X0", "comment": "启动", "role": "input"},
            {"device": "X1", "comment": "停止", "role": "input"},
            {"device": "X3", "comment": "竖直缸到位", "role": "input"},
            {"device": "Y0", "comment": "竖直缸", "role": "output"},
            {"device": "Y2", "comment": "夹爪", "role": "output"},
        ]
        spec = plan_deterministic(requirement, io)
        with tempfile.TemporaryDirectory() as tmp:
            result = generate(spec, None, tmp, requirement=requirement)
            self.assertTrue(result["quality"]["ok"], result["quality"]["errors"])

    def test_natural_language_without_domain_raises(self):
        with self.assertRaises(PlannerError):
            plan_deterministic("做一个电机控制程序，按启动按钮电机运行，按停止按钮电机停止。", IO)

    def test_quality_blocks_empty_steps(self):
        bad = {
            "spec_version": "0.1",
            "project": {"name": "bad", "cpu": "FX5U"},
            "io": [],
            "segments": [{
                "title": "自动流程", "kind": "station", "step_range": [10, 99],
                "steps": [{"step": 10, "actions": [],
                           "transition": {"conditions": [], "next": None}}],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(QualityError):
                generate(bad, None, tmp, requirement="自动流程")

    def test_quality_catches_missing_cycle(self):
        spec = build_traffic_light_spec(REQUIREMENT, IO)
        # 破坏循环：最后一步不再回到 S10
        station = spec["segments"][2]
        station["steps"][-1]["transition"]["next"] = None
        quality = check_quality(spec, REQUIREMENT)
        self.assertFalse(quality["ok"])
        self.assertTrue(any("循环" in item for item in quality["errors"]))


if __name__ == "__main__":
    unittest.main()
