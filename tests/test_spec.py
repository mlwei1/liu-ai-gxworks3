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


if __name__ == "__main__":
    unittest.main()
