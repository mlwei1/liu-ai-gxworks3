import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gx3gen import spec as specmod  # noqa: E402


class SpecTest(unittest.TestCase):
    def test_load_sample_spec(self):
        data = json.loads((ROOT / "samples" / "three_station.spec.json").read_text(encoding="utf-8"))
        specmod.validate_spec(data)
        self.assertEqual(data["project"]["cpu"], "FX5U")
        self.assertEqual(len(data["io"]), 11)

    def test_invalid_cpu(self):
        data = {"spec_version": "0.1",
                "project": {"name": "x", "cpu": "Q"},
                "io": [],
                "segments": [{"title": "a", "kind": "statement", "statements": ["x"]}]}
        with self.assertRaises(specmod.SpecError) as ctx:
            specmod.validate_spec(data)
        self.assertIn("cpu", str(ctx.exception))

    def test_invalid_device(self):
        data = {"spec_version": "0.1",
                "project": {"name": "x", "cpu": "FX5U"},
                "io": [{"device": "X", "comment": "启动"}],
                "segments": [{"title": "a", "kind": "statement", "statements": ["x"]}]}
        with self.assertRaises(specmod.SpecError):
            specmod.validate_spec(data)

    def test_parse_io_csv(self):
        text = (ROOT / "samples" / "three_station.io.csv").read_text(encoding="utf-8")
        rows = specmod.parse_io_csv(text)
        self.assertEqual(len(rows), 11)
        first = rows[0]
        self.assertEqual(first["device"], "X0")
        self.assertEqual(first["comment"], "启动")
        self.assertEqual(first["role"], "input")

    def test_decode_utf16_bom(self):
        text = "步号\t行间声明\n1\t[Title]X"
        encoded = b"\xff\xfe" + text.encode("utf-16-le")
        self.assertEqual(specmod.decode_text(encoded), text)

    def test_lookup_device_by_comment(self):
        data = json.loads((ROOT / "samples" / "three_station.spec.json").read_text(encoding="utf-8"))
        self.assertEqual(specmod.lookup_device(data, "竖直缸到位"), "X3")
        self.assertEqual(specmod.lookup_device(data, "x3"), "X3")
        self.assertIsNone(specmod.lookup_device(data, "不存在的信号"))


class GXImportRuleTest(unittest.TestCase):
    """GX Works3 导入硬约束（真实故障回归，见 docs/GX_IMPORT_TROUBLESHOOTING.md）。"""

    def _base(self):
        return {
            "spec_version": "0.1",
            "project": {"name": "GX 规则测试", "cpu": "FX5U"},
            "io": [
                {"device": "X0", "comment": "启动", "role": "input"},
                {"device": "Y0", "comment": "输出", "role": "output"},
            ],
            "segments": [
                {"title": "初始化", "kind": "init", "rungs": [{"instructions": [
                    {"op": "LD", "operands": ["SM402"]},
                    {"op": "SET", "operands": ["S0"]},
                ]}]},
                {"title": "手动", "kind": "rungs", "rungs": [{"instructions": [
                    {"op": "LD", "operands": ["X0"]},
                    {"op": "OUT", "operands": ["Y0"]},
                ]}]},
            ],
        }

    def _assert_invalid(self, spec, fragment):
        with self.assertRaises(specmod.SpecError) as ctx:
            specmod.validate_spec(spec)
        self.assertIn(fragment, str(ctx.exception))

    def test_set_multiple_operands_rejected(self):
        spec = self._base()
        spec["segments"][1]["rungs"][0]["instructions"] = [
            {"op": "LD", "operands": ["X0"]},
            {"op": "SET", "operands": ["Y0", "M0"]},
        ]
        self._assert_invalid(spec, "SET")

    def test_rst_multiple_operands_in_init_rejected(self):
        spec = self._base()
        spec["segments"][0]["rungs"] = [{"instructions": [
            {"op": "LD", "operands": ["SM402"]},
            {"op": "RST", "operands": ["M0", "M1"]},
        ]}]
        self._assert_invalid(spec, "RST")

    def test_ld_followed_by_ldi_rejected(self):
        spec = self._base()
        spec["segments"][1]["rungs"][0]["instructions"] = [
            {"op": "LD", "operands": ["X0"]},
            {"op": "LDI", "operands": ["X1"]},
            {"op": "OUT", "operands": ["Y0"]},
        ]
        self._assert_invalid(spec, "LDI")

    def test_ld_then_and_is_valid(self):
        spec = self._base()
        spec["segments"][1]["rungs"][0]["instructions"] = [
            {"op": "LD", "operands": ["X0"]},
            {"op": "AND", "operands": ["X1"]},
            {"op": "OUT", "operands": ["Y0"]},
        ]
        specmod.validate_spec(spec)  # 不应抛异常

    def test_station_action_starting_with_load_rejected(self):
        spec = json.loads((ROOT / "samples" / "traffic_light.spec.json").read_text(encoding="utf-8"))
        station = next(item for item in spec["segments"] if item["kind"] == "station")
        station["steps"][0]["actions"][0]["instructions"] = [
            {"op": "LDI", "operands": ["X0"]},
        ]
        self._assert_invalid(spec, "LD/LDI")

    def test_comment_title_validation(self):
        spec = self._base()
        spec["project"]["comment_title"] = ""
        self._assert_invalid(spec, "comment_titles")
        spec["project"]["comment_title"] = "注释"
        specmod.validate_spec(spec)  # 合法
        spec["project"]["comment_titles"] = []
        self._assert_invalid(spec, "comment_titles")


if __name__ == "__main__":
    unittest.main()
