"""gx3gen.style.miner / gx3gen.style.apply 的单元测试（unittest）。

约束：
- 不访问网络；
- 不依赖外部语料目录；
- 不写真实 workspace（导入前把 GX3GEN_WORKSPACE 指向临时目录）。

程序 CSV / 注释 CSV 均按 docs/CSV_FORMAT.md 的 GX Works3 格式在内存中构造：
UTF-16 仅用于少量“字节/文件”路径测试，普通 case 直接传文本。
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_TMP_WORKSPACE = tempfile.mkdtemp(prefix="gx3gen-style-ws-")
os.environ["GX3GEN_WORKSPACE"] = _TMP_WORKSPACE

from gx3gen.ilcsv.rows import CSV_HEADER  # noqa: E402
from gx3gen.style import apply, miner  # noqa: E402


# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------
def _write_tsv(rows) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    writer.writerows(rows)
    return buffer.getvalue()


def build_program_csv(rows, project="测试设备", cpu="FX5CPU FX5U") -> str:
    lines = [[project], ["机型信息:", cpu], list(CSV_HEADER)]
    lines.extend([list(row) for row in rows])
    return _write_tsv(lines)


def build_comment_csv(comments, project="测试设备") -> str:
    lines = [[project], ["软元件名", "注释"]]
    for device, comment in comments.items():
        lines.append([device, comment])
    return _write_tsv(lines)


def title(step, name):
    return [str(step), "[Title]" + name, "", "", "", "", ""]


def statement(step, text):
    return [str(step), text, "", "", "", "", ""]


def ins(step, instr, operand=""):
    return [str(step), "", instr, operand, "", "", ""]


def cont(operand):
    return ["", "", "", operand, "", "", ""]


PROGRAM_ROWS = [
    title(0, "INIT"),
    ins(16, "LD", "SM402"),
    ins(18, "SET", "M500"),
    ins(21, "LD", "M500"),
    ins(23, "OUT", "Y0"),
    ins(26, "LD", "M500"),
    ins(28, "MOV", "K10"),
    cont("D100"),
    ins(32, "LD", "M500"),
    ins(34, "OUT", "T10"),
    cont("K50"),
    title(40, "SQ1四联"),
    ins(100, "STL", "S100"),
    ins(103, "LD", "S100"),
    ins(105, "OUT", "Y10"),
    ins(108, "LD", "S100"),
    ins(110, "AND", "X0"),
    ins(113, "SET", "S110"),
    ins(116, "STL", "S110"),
    ins(119, "LD", "S110"),
    ins(121, "SET", "M110"),
    ins(124, "LD", "M1200"),
    ins(126, "OUT", "Y11"),
    ins(129, "STL", "S120"),
    ins(132, "LD", "S120"),
    ins(134, "AND", "X1"),
    ins(136, "SET", "S200"),
    ins(139, "RETSTL"),
    title(150, "ＳＱ２二联"),  # 全角，测试宽度归一化
    ins(200, "STL", "S200"),
    ins(203, "LD", "S200"),
    ins(205, "OUT", "Y20"),
    ins(208, "LD", "S200"),
    ins(210, "AND", "X2"),
    ins(213, "SET", "S210"),
    ins(216, "STL", "S210"),
    ins(219, "LD", "S210"),
    ins(221, "SET", "M210"),
    ins(224, "RETSTL"),
    title(300, "报警"),
    ins(2000, "LD", "M1610"),
    ins(2002, "OUT", "Y30"),
    ins(2005, "LD", "M1611"),
    ins(2007, "OUT", "Y31"),
    ins(2010, "LD", "M1200"),
    ins(2012, "OUT", "Y32"),
    statement(290, "手动"),
    ins(3000, "LD", "M522"),
    ins(3002, "OUT", "Y40"),
]

COMMENTS = {
    "X0": "四联竖直缸1原位",
    "X1": "四联竖直缸1到位",
    "X2": "二联竖直缸1原位",
    "M110": "SQ1动作标志",
    "M210": "SQ2动作标志",
    "M500": "运行中",
    "M501": "停止中",
    "M510": "系统复归",
    "M521": "自动",
    "M522": "手动",
    "M1200": "机器人->PLC/1/OK",
    "M1201": "机器人->PLC/2/夹爪1ON",
    "M1600": "M1600 伺服压机报警",
    "M1610": "M1610气缸报警：四联竖直缸1原位X10",
    "M1611": "M1611气缸报警：四联竖直缸1到位X11",
    "M1740": "M1740 自动报警：压紧片装配工位未感应到产品",
    "M3000": "移位标志：压紧片装配",
    "M3001": "移位标志：阀体缩口",
    "T10": "驱动输出辅助",
    "T70": "无O型圈报警延时",
}


def _mined_fixture_profile():
    return miner.mine(build_program_csv(PROGRAM_ROWS), build_comment_csv(COMMENTS), "测试风格")


# ---------------------------------------------------------------------------
# miner：结构 / 步号
# ---------------------------------------------------------------------------
class TestMinerStructure(unittest.TestCase):
    def setUp(self):
        self.profile = _mined_fixture_profile()

    def test_profile_basics(self):
        self.assertEqual(self.profile["profile_version"], "0.1")
        self.assertEqual(self.profile["name"], "测试风格")
        self.assertEqual(self.profile["project"]["cpu"], "FX5CPU FX5U")
        self.assertIsInstance(self.profile["warnings"], list)
        self.assertTrue(self.profile["explain"])

    def test_segments_order_and_counts(self):
        titles = [segment["title"] for segment in self.profile["segments"]]
        self.assertEqual(titles, ["INIT", "SQ1四联", "ＳＱ２二联", "报警", "手动"])
        segments = {segment["title"]: segment for segment in self.profile["segments"]}
        self.assertEqual([segment["order"] for segment in self.profile["segments"]],
                         [1, 2, 3, 4, 5])
        self.assertEqual(segments["INIT"]["network_count"], 4)  # 4 个 LD 起头
        self.assertFalse(segments["INIT"]["has_stl"])
        self.assertTrue(segments["SQ1四联"]["has_stl"])
        self.assertFalse(segments["报警"]["has_stl"])
        self.assertGreaterEqual(segments["SQ1四联"]["network_count"], 3)

    def test_step_ranges(self):
        ranges = self.profile["step_ranges"]
        self.assertEqual([item["start"] for item in ranges], [100, 200])
        self.assertEqual(ranges[0]["segment"], "SQ1四联")
        self.assertEqual(ranges[0]["end"], 120)
        self.assertEqual(ranges[0]["count"], 3)
        self.assertEqual(ranges[1]["end"], 210)
        self.assertEqual(ranges[1]["count"], 2)

    def test_step_strategy(self):
        strategy = self.profile["step_strategy"]
        self.assertEqual(strategy["station_start"], 100)
        self.assertEqual(strategy["station_stride"], 100)
        self.assertEqual(strategy["increment"], 10)
        self.assertTrue(strategy["evidence"])

    def test_device_ranges(self):
        ranges = self.profile["device_ranges"]
        self.assertEqual(ranges["X"], {"min": 0, "max": 2, "count": 3})
        self.assertEqual(ranges["T"], {"min": 10, "max": 10, "count": 1})
        self.assertEqual(ranges["D"], {"min": 100, "max": 100, "count": 1})
        self.assertEqual(ranges["Y"]["count"], 8)
        self.assertIn("S", ranges)
        self.assertEqual(ranges["S"]["min"], 100)
        self.assertEqual(ranges["S"]["max"], 210)

    def test_instruction_mix(self):
        mix = self.profile["instruction_mix"]
        self.assertEqual(mix["STL"], 5)
        self.assertGreaterEqual(mix["LD"], 8)
        self.assertGreaterEqual(mix["OUT"], 6)
        self.assertGreater(self.profile["stats"]["instructions"], 0)

    def test_templates(self):
        templates = self.profile["templates"]
        kinds = {item["kind"] for item in templates}
        self.assertIn("stl_step", kinds)
        stl_steps = [item for item in templates if item["kind"] == "stl_step"]
        self.assertGreaterEqual(sum(item["count"] for item in stl_steps), 5)
        for item in templates:
            self.assertIsInstance(item["pattern"], list)
            self.assertGreater(item["count"], 0)

    def test_fb_block_markers_are_skipped(self):
        rows = [
            statement(0, "*;FB BLK START"),
            ins(2, "LD", "M1"),
            ins(4, "OUT", "Y1"),
            statement(6, "*;FB BLK END"),
            title(10, "SQ1工位"),
            ins(12, "STL", "S10"),
            ins(14, "LD", "S10"),
            ins(16, "SET", "M2"),
            ins(18, "RETSTL"),
        ]
        profile = miner.mine(build_program_csv(rows), None, "FB")
        titles = [segment["title"] for segment in profile["segments"]]
        self.assertEqual(titles, ["SQ1工位"])
        self.assertEqual(profile["stats"]["fb_blocks"], 2)
        self.assertTrue(any("FB 块" in warning for warning in profile["warnings"]))


# ---------------------------------------------------------------------------
# miner：模式位 / 报警 / 握手 / 命名
# ---------------------------------------------------------------------------
class TestMinerComments(unittest.TestCase):
    def setUp(self):
        self.profile = _mined_fixture_profile()

    def test_modes(self):
        modes = self.profile["modes"]
        self.assertEqual(modes["run"]["device"], "M500")
        self.assertEqual(modes["run"]["comment"], "运行中")
        self.assertEqual(modes["stop"]["device"], "M501")
        self.assertEqual(modes["auto"]["device"], "M521")
        self.assertEqual(modes["manual"]["device"], "M522")
        self.assertEqual(modes["reset"]["device"], "M510")
        for key in ("run", "stop", "auto", "manual", "reset"):
            self.assertTrue(modes[key]["evidence"])
            self.assertGreater(modes[key]["confidence"], 0)

    def test_modes_fallback_without_comments(self):
        profile = miner.mine(build_program_csv(PROGRAM_ROWS), None, "无注释")
        modes = profile["modes"]
        self.assertEqual(modes["run"]["device"], "M500")
        self.assertLessEqual(modes["run"]["confidence"], 0.35)
        self.assertTrue(any("未提供软元件注释" in warning for warning in profile["warnings"]))

    def test_alarm(self):
        alarm = self.profile["alarm"]
        self.assertEqual(alarm["base"], 1600)
        self.assertEqual(alarm["timer_base"], 70)  # T70 报警延时
        self.assertIn("气缸报警：{name}原位{sensor}", alarm["naming"])
        self.assertIn("气缸报警：{name}到位{sensor}", alarm["naming"])
        self.assertTrue(alarm["examples"])
        self.assertTrue(alarm["evidence"])
        self.assertGreaterEqual(alarm["count"], 4)

    def test_handshake(self):
        handshake = self.profile["handshake"]
        self.assertEqual(handshake["base"], 1200)
        self.assertIn("{src}->{dst}/{index}/{action}", handshake["naming"])
        self.assertIn("移位标志：{station}", handshake["naming"])
        self.assertTrue(handshake["examples"])
        reused = {item["device"]: item for item in handshake["reused_signals"]}
        self.assertIn("M1200", reused)
        self.assertEqual(reused["M1200"]["segment_count"], 2)

    def test_naming(self):
        naming = self.profile["naming"]
        self.assertIn("SQ", naming["segment_prefixes"])
        self.assertIn("INIT", naming["segment_prefixes"])
        self.assertIn("SQ{n}{station}", naming["title_templates"])
        # naming 里必须全是可 join 的字符串（profile.summarize 会直接拼接）
        for value in naming["comment_patterns"]:
            self.assertIsInstance(value, str)
        self.assertTrue(naming["comment_patterns"])
        keys = {item["key"] for item in self.profile["comment_patterns"]}
        self.assertIn("device_prefix", keys)
        self.assertIn("arrow", keys)
        self.assertIn("fullwidth_colon", keys)
        for item in self.profile["comment_patterns"]:
            self.assertIsInstance(item["count"], int)
            self.assertTrue(item["examples"])
        # naming 里附带的注释风格统计（任务要求）
        self.assertGreaterEqual(naming["comment_max_len"], 15)
        self.assertGreater(naming["comment_cjk_ratio"], 0.3)
        self.assertIsInstance(naming["has_fullwidth"], bool)

    def test_comment_stats(self):
        stats = self.profile["comment_stats"]
        self.assertEqual(stats["total"], len(COMMENTS))
        self.assertGreaterEqual(stats["max_len"], 15)
        self.assertGreater(stats["cjk_ratio"], 0.3)
        self.assertTrue(stats["has_fullwidth"])
        self.assertGreater(stats["fullwidth_count"], 0)
        self.assertGreater(stats["device_comment_coverage"], 0.0)


# ---------------------------------------------------------------------------
# miner：边界 / 输入格式
# ---------------------------------------------------------------------------
class TestMinerInputs(unittest.TestCase):
    def test_uses_apply_defaults_for_empty_input(self):
        profile = miner.mine("", None, "空")
        self.assertEqual(profile["segments"], [])
        self.assertEqual(profile["step_ranges"], [])
        self.assertTrue(profile["warnings"])
        self.assertEqual(apply.generator_defaults(profile), apply.generator_defaults(None))

    def test_malformed_text_does_not_raise(self):
        profile = miner.mine("这不是CSV内容\n第二行", "坏注释", "坏")
        self.assertEqual(profile["profile_version"], "0.1")
        self.assertEqual(profile["segments"], [])
        self.assertTrue(profile["warnings"])

    def test_bytes_and_path_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            program_path = Path(tmp) / "程序本体.csv"
            comment_path = Path(tmp) / "软元件注释.csv"
            program_path.write_bytes(
                build_program_csv(PROGRAM_ROWS).encode("utf-16"))
            comment_path.write_bytes(
                build_comment_csv(COMMENTS).encode("utf-16"))

            from_bytes = miner.mine(program_path.read_bytes(), comment_path.read_bytes(), "字节")
            from_path = miner.mine(str(program_path), str(comment_path), "路径")
            for profile, expected_name in ((from_bytes, "字节"), (from_path, "路径")):
                self.assertEqual(profile["name"], expected_name)
                self.assertEqual(len(profile["segments"]), 5)
                self.assertEqual(profile["modes"]["run"]["device"], "M500")
                self.assertEqual(profile["alarm"]["base"], 1600)

    def test_does_not_write_workspace(self):
        miner.mine(build_program_csv(PROGRAM_ROWS), build_comment_csv(COMMENTS), "无写盘")
        self.assertEqual(os.listdir(_TMP_WORKSPACE), [])


# ---------------------------------------------------------------------------
# apply：生成器默认值
# ---------------------------------------------------------------------------
class TestGeneratorDefaults(unittest.TestCase):
    def setUp(self):
        self.profile = _mined_fixture_profile()

    def test_none_profile_safe_defaults(self):
        defaults = apply.generator_defaults(None)
        self.assertEqual(defaults["segment_order"], ["初始化", "设备段", "报警"])
        self.assertEqual(defaults["step_base"], 100)
        self.assertEqual(defaults["step_stride"], 100)
        self.assertEqual(defaults["modes"], {
            "run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"})
        self.assertEqual(defaults["alarm_base"], 2000)
        self.assertEqual(defaults["alarm_timer_base"], 10)
        self.assertEqual(defaults["handshake_base"], 1000)
        self.assertEqual(set(defaults["naming"]),
                         {"segment_prefixes", "title_templates", "comment_patterns",
                          "alarm_templates", "handshake_templates"})
        for value in defaults["naming"].values():
            self.assertIsInstance(value, list)

    def test_mined_profile_overrides(self):
        defaults = apply.generator_defaults(self.profile)
        # INIT→初始化、SQ→设备段、报警、手动（报警应排在手动之前）
        self.assertEqual(defaults["segment_order"],
                         ["初始化", "设备段", "报警", "手动"])
        self.assertEqual(defaults["step_base"], 100)
        self.assertEqual(defaults["step_stride"], 100)
        self.assertEqual(defaults["modes"]["run"], "M500")
        self.assertEqual(defaults["modes"]["reset"], "M510")
        self.assertEqual(defaults["alarm_base"], 1600)
        self.assertEqual(defaults["alarm_timer_base"], 70)
        self.assertEqual(defaults["handshake_base"], 1200)
        naming = defaults["naming"]
        self.assertIn("SQ", naming["segment_prefixes"])
        self.assertIn("气缸报警：{name}原位{sensor}", naming["alarm_templates"])
        self.assertIn("{src}->{dst}/{index}/{action}", naming["handshake_templates"])

    def test_segment_order_normalizes_init_first(self):
        # 有些真实工程把 INIT 段写在设备段之后；生成器默认值仍应 初始化 → 设备段
        profile = {
            "segments": [
                {"order": 1, "title": "SQ1四联"},
                {"order": 2, "title": "报警"},
                {"order": 3, "title": "INIT"},
                {"order": 4, "title": "手动"},
            ]
        }
        self.assertEqual(apply.segment_order(profile),
                         ["初始化", "设备段", "报警", "手动"])

    def test_bad_profile_types_are_safe(self):
        defaults = apply.generator_defaults(None)
        for bad in (None, {}, "bad", {"segments": "x", "modes": [], "alarm": 3,
                                      "handshake": None, "step_strategy": "y"}):
            self.assertEqual(apply.generator_defaults(bad), defaults)

    def test_contract_keys_are_exact(self):
        expected = {"segment_order", "step_base", "step_stride", "modes",
                    "alarm_base", "alarm_timer_base", "handshake_base", "naming"}
        self.assertEqual(set(apply.generator_defaults(self.profile)), expected)
        self.assertEqual(set(apply.generator_defaults(None)), expected)

    def test_contract_types(self):
        defaults = apply.generator_defaults(self.profile)
        self.assertIsInstance(defaults["segment_order"], list)
        for key in ("step_base", "step_stride", "alarm_base",
                    "alarm_timer_base", "handshake_base"):
            self.assertIsInstance(defaults[key], int)
        self.assertIsInstance(defaults["modes"], dict)
        self.assertIsInstance(defaults["naming"], dict)


# ---------------------------------------------------------------------------
# apply：规划器提示
# ---------------------------------------------------------------------------
class TestPlannerHint(unittest.TestCase):
    def setUp(self):
        self.profile = _mined_fixture_profile()

    def test_hint_contains_learned_facts(self):
        hint = apply.planner_hint(self.profile)
        self.assertIsInstance(hint, str)
        self.assertLessEqual(len(hint), 1200)
        for token in ("SQ1四联", "M500", "1600", "步"):
            self.assertIn(token, hint)

    def test_default_hint_when_profile_missing(self):
        hint = apply.planner_hint(None)
        self.assertLessEqual(len(hint), 1200)
        self.assertIn("M500", hint)
        self.assertIn("S100", hint)
        self.assertEqual(hint, apply.planner_hint({}))

    def test_hint_truncated_for_long_profile(self):
        profile = {
            "profile_version": "0.1",
            "name": "超长",
            "segments": [{"order": index, "title": "工位%d-%s" % (index, "长" * 60)}
                         for index in range(1, 120)],
            "step_ranges": [{"segment": "工位%d" % index, "start": index * 100,
                             "end": index * 100 + 90, "count": 10} for index in range(1, 80)],
        }
        hint = apply.planner_hint(profile)
        self.assertLessEqual(len(hint), 1200)
        self.assertTrue(hint.endswith("…"))


# ---------------------------------------------------------------------------
# 与 style.profile / 下游消费方的集成（防止画像结构漂移）
# ---------------------------------------------------------------------------
class TestProfileIntegration(unittest.TestCase):
    def test_summarize_and_merge_accept_mined_profile(self):
        from gx3gen.style.profile import merge_profiles, summarize

        profile = _mined_fixture_profile()
        text = summarize(profile)
        self.assertTrue(text.strip())
        self.assertLessEqual(len(text), 1200)

        merged = merge_profiles([profile, miner.mine("", None, "空风格")])
        self.assertIsInstance(merged, dict)
        self.assertIn("naming", merged)
        self.assertLessEqual(len(summarize(merged)), 1200)
        # 合并后的画像仍然可直接喂给生成器
        defaults = apply.generator_defaults(merged)
        self.assertEqual(set(defaults), {
            "segment_order", "step_base", "step_stride", "modes",
            "alarm_base", "alarm_timer_base", "handshake_base", "naming"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
