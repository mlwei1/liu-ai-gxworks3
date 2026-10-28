"""规划器测试（unittest，不访问网络、不写真实 workspace）。

覆盖：
- deterministic：段顺序、步号段、动作/转移、延时定时器、IO 注释解析、assumptions；
- style/lessons 对规划结果的影响；
- plan(project, config)：不依赖 LLM 也能跑、use_llm 未配置时回退；
- LLM 适配层：prompt 拼装、响应 JSON 提取/校验、未配置报错（不做网络调用）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gx3gen.planner import deterministic, llm  # noqa: E402
from gx3gen.planner.plan import PlannerError, plan  # noqa: E402
from gx3gen.spec import parse_io_csv, validate_spec  # noqa: E402

SAMPLE_IO = [
    {"device": "X0", "comment": "启动", "role": "input"},
    {"device": "X1", "comment": "停止", "role": "input"},
    {"device": "X2", "comment": "竖直缸原位", "role": "input"},
    {"device": "X3", "comment": "竖直缸到位", "role": "input"},
    {"device": "X4", "comment": "水平缸原位", "role": "input"},
    {"device": "X5", "comment": "水平缸到位", "role": "input"},
    {"device": "X6", "comment": "夹爪原位", "role": "input"},
    {"device": "X7", "comment": "夹爪到位", "role": "input"},
    {"device": "Y0", "comment": "竖直缸", "role": "output"},
    {"device": "Y1", "comment": "水平缸", "role": "output"},
    {"device": "Y2", "comment": "夹爪", "role": "output"},
]

REQUIREMENT = """初始化:
  SM402 置位 S0

取放站:
  步号段 100-199
  竖直缸(Y0)下降
  到位(X3) -> 夹紧(Y2)
  到位(X7) -> 延时2s
  竖直缸(Y0)上升
  原位(X2) -> 水平缸(Y1)右移
  到位(X5) -> 松开(Y2)
  原位(X6) -> 水平缸(Y1)左移
  原位(X4) -> 完成，回待机

报警:
  气缸: 竖直缸(Y0) 到位(X3) 原位(X2)
  气缸: 水平缸(Y1) 到位(X5) 原位(X4)
  气缸: 夹爪(Y2) 到位(X7) 原位(X6)
"""


def _station_spec(spec):
    for segment in spec["segments"]:
        if segment.get("kind") == "station":
            return segment
    raise AssertionError("spec 缺少 station 段")


def _steps_by_number(spec):
    return {step["step"]: step for step in _station_spec(spec)["steps"]}


class PlannerTestBase(unittest.TestCase):
    def setUp(self):
        self._old_workspace = os.environ.get("GX3GEN_WORKSPACE")
        self._tmp = tempfile.mkdtemp(prefix="gx3gen-planner-")
        os.environ["GX3GEN_WORKSPACE"] = self._tmp

    def tearDown(self):
        if self._old_workspace is None:
            os.environ.pop("GX3GEN_WORKSPACE", None)
        else:
            os.environ["GX3GEN_WORKSPACE"] = self._old_workspace
        shutil.rmtree(self._tmp, ignore_errors=True)


class DeterministicPlannerTest(PlannerTestBase):
    def test_spec_is_valid_with_expected_segments(self):
        spec = deterministic.plan_deterministic(
            REQUIREMENT, SAMPLE_IO, None, None, name="三工位取放"
        )
        validate_spec(spec)
        self.assertEqual(spec["spec_version"], "0.1")
        self.assertEqual(spec["project"]["name"], "三工位取放")
        self.assertEqual(spec["project"]["cpu"], "FX5U")
        kinds = [segment["kind"] for segment in spec["segments"]]
        self.assertEqual(kinds[0], "init")
        self.assertIn("station", kinds)
        station = _station_spec(spec)
        self.assertEqual(station["title"], "取放站")
        self.assertEqual(station["step_range"], [100, 199])
        self.assertEqual(
            [step["step"] for step in station["steps"]],
            [100, 110, 120, 130, 140, 150, 160],
        )
        self.assertEqual(station["entry_conditions"], ["S0", "X0"])

    def test_actions_and_transitions(self):
        spec = deterministic.plan_deterministic(REQUIREMENT, SAMPLE_IO, None, None)
        steps = _steps_by_number(spec)
        self.assertEqual(steps[100]["actions"][0].get("set"), ["Y0"])
        self.assertEqual(steps[100]["transition"]["conditions"], ["X3"])
        self.assertEqual(steps[100]["transition"]["next"], 110)
        self.assertEqual(steps[110]["actions"][0].get("set"), ["Y2"])
        self.assertEqual(steps[130]["actions"][0].get("reset"), ["Y0"])
        self.assertEqual(steps[150]["actions"][0].get("reset"), ["Y2"])
        self.assertEqual(steps[160]["actions"][0].get("reset"), ["Y1"])
        self.assertIsNone(steps[160]["transition"]["next"])

    def test_delay_becomes_timer_instruction_and_condition(self):
        spec = deterministic.plan_deterministic(REQUIREMENT, SAMPLE_IO, None, None)
        steps = _steps_by_number(spec)
        delay_step = steps[120]
        instruction = delay_step["actions"][0]["instructions"][0]
        self.assertEqual(instruction["op"], "OUT")
        self.assertEqual(instruction["operands"][0], "T0")
        self.assertTrue(instruction["operands"][1].startswith("K"))
        self.assertEqual(delay_step["transition"]["conditions"], ["T0"])

    def test_init_and_common_segments(self):
        spec = deterministic.plan_deterministic(REQUIREMENT, SAMPLE_IO, None, None)
        init = spec["segments"][0]
        self.assertEqual(init["kind"], "init")
        instructions = init["rungs"][0]["instructions"]
        self.assertEqual(instructions[0], {"op": "LD", "operands": ["SM402"]})
        self.assertEqual(instructions[1], {"op": "SET", "operands": ["S0"]})
        common = [segment for segment in spec["segments"] if segment["kind"] == "common"]
        self.assertTrue(common)
        flat = [ins for rung in common[0]["rungs"] for ins in rung["instructions"]]
        self.assertTrue(any(ins["operands"] == ["M500"] for ins in flat if ins["op"] == "SET"))

    def test_alarms_from_requirement(self):
        spec = deterministic.plan_deterministic(REQUIREMENT, SAMPLE_IO, None, None)
        alarms = spec["alarms"]
        self.assertTrue(alarms["enabled"])
        by_output = {item["output"]: item for item in alarms["cylinders"]}
        self.assertEqual(set(by_output), {"Y0", "Y1", "Y2"})
        self.assertEqual(by_output["Y0"]["on_sensor"], "X3")
        self.assertEqual(by_output["Y0"]["home_sensor"], "X2")
        self.assertEqual(by_output["Y2"]["name"], "夹爪")
        self.assertEqual(alarms["reset_device"], "X1")

    def test_alarms_from_io_when_requirement_has_no_alarm_section(self):
        requirement = "初始化:\n  SM402 置位 S0\n取放站:\n  竖直缸(Y0)下降\n  到位(X3) -> 完成"
        spec = deterministic.plan_deterministic(requirement, SAMPLE_IO, None, None)
        validate_spec(spec)
        self.assertTrue(spec["alarms"]["enabled"])
        self.assertEqual(len(spec["alarms"]["cylinders"]), 3)

    def test_device_resolution_from_io_comment(self):
        requirement = "初始化:\n  SM402 置位 S0\n取放站:\n  水平缸右移\n  夹爪夹紧\n  到位(X5) -> 完成"
        spec = deterministic.plan_deterministic(requirement, SAMPLE_IO, None, None)
        validate_spec(spec)
        steps = _steps_by_number(spec)
        set_devices = {
            device
            for step in steps.values()
            for action in step["actions"]
            for device in action.get("set") or []
        }
        self.assertIn("Y1", set_devices)
        self.assertIn("Y2", set_devices)

    def test_unresolved_devices_are_recorded_as_assumptions(self):
        requirement = (
            "初始化:\n  SM402 置位 S0\n"
            "取放站:\n  神秘机构动作\n  等待 压装->入料/可送料 -> 水平缸右移"
        )
        spec = deterministic.plan_deterministic(requirement, SAMPLE_IO, None, None)
        validate_spec(spec)
        joined = "\n".join(spec["assumptions"])
        self.assertIn("神秘机构", joined)
        self.assertIn("压装", joined)

    def test_init_default_when_requirement_missing_init(self):
        requirement = "取放站:\n  竖直缸(Y0)下降\n  到位(X3) -> 夹紧(Y2)"
        spec = deterministic.plan_deterministic(requirement, SAMPLE_IO, None, None)
        validate_spec(spec)
        init = spec["segments"][0]
        self.assertEqual(init["kind"], "init")
        operands = [ins["operands"] for ins in init["rungs"][0]["instructions"]]
        self.assertIn(["SM402"], operands)
        self.assertIn(["S0"], operands)

    def test_style_step_strategy_is_used(self):
        style = {
            "profile_version": "0.1",
            "name": "我的风格",
            "step_strategy": {"station_start": 300, "station_stride": 50, "increment": 5},
            "modes": {"run": {"device": "M600", "comment": "运行中"}},
        }
        requirement = "初始化:\n  SM402 置位 S0\n取放站:\n  竖直缸(Y0)下降\n  到位(X3) -> 夹紧(Y2)"
        spec = deterministic.plan_deterministic(requirement, SAMPLE_IO, style, None)
        validate_spec(spec)
        station = _station_spec(spec)
        self.assertEqual(station["step_range"], [300, 349])
        self.assertEqual([step["step"] for step in station["steps"]], [300, 305])
        self.assertEqual(spec["modes"]["run"], "M600")

    def test_multiple_stations_do_not_overlap(self):
        requirement = (
            "初始化:\n  SM402 置位 S0\n"
            "上料站:\n  水平缸(Y1)右移\n  到位(X5) -> 完成\n"
            "取放站:\n  竖直缸(Y0)下降\n  到位(X3) -> 完成"
        )
        spec = deterministic.plan_deterministic(requirement, SAMPLE_IO, None, None)
        validate_spec(spec)
        stations = [segment for segment in spec["segments"] if segment["kind"] == "station"]
        self.assertEqual(len(stations), 2)
        first, second = stations[0]["step_range"], stations[1]["step_range"]
        self.assertLess(first[1], second[0])

    def test_init_section_without_condition_uses_sm402(self):
        requirement = "初始化:\n  置位 S0\n取放站:\n  竖直缸(Y0)下降\n  到位(X3) -> 完成"
        spec = deterministic.plan_deterministic(requirement, SAMPLE_IO, None, None)
        validate_spec(spec)
        init = spec["segments"][0]
        operands = [ins["operands"] for ins in init["rungs"][0]["instructions"]]
        self.assertEqual(operands[0], ["SM402"])
        self.assertEqual(operands[1], ["S0"])

    def test_input_device_is_used_as_condition_not_action(self):
        requirement = (
            "初始化:\n  SM402 置位 S0\n"
            "取放站:\n  启动(X0)\n  竖直缸(Y0)下降\n  到位(X3) -> 完成"
        )
        spec = deterministic.plan_deterministic(requirement, SAMPLE_IO, None, None)
        validate_spec(spec)
        steps = _steps_by_number(spec)
        first = steps[min(steps)]
        self.assertEqual(first["actions"], [])
        self.assertIn("X0", first["transition"]["conditions"])
        driven = [
            device
            for step in steps.values()
            for action in step["actions"]
            for key in ("set", "reset", "out")
            for device in action.get(key) or []
        ]
        self.assertNotIn("X0", driven)

    def test_lessons_are_recorded_in_notes_or_assumptions(self):
        lessons = [
            {"category": "互锁", "count": 2, "last_message": "缺少气缸互锁", "examples": []},
            {"category": "停止", "count": 1, "last_message": "缺少停止复位", "examples": []},
        ]
        spec = deterministic.plan_deterministic(REQUIREMENT, SAMPLE_IO, None, lessons)
        validate_spec(spec)
        joined = "\n".join(spec["notes"] + spec["assumptions"])
        self.assertIn("缺少气缸互锁", joined)
        self.assertIn("缺少停止复位", joined)

    def test_io_normalization_fills_missing_comment(self):
        io = [{"device": "Y0"}, {"device": "X3", "comment": "到位"}, {"device": "BAD", "comment": "坏行"}]
        requirement = "初始化:\n  SM402 置位 S0\n取放站:\n  竖直缸(Y0)下降\n  到位(X3) -> 完成"
        spec = deterministic.plan_deterministic(requirement, io, None, None)
        validate_spec(spec)
        by_device = {row["device"]: row for row in spec["io"]}
        self.assertIn("Y0", by_device)
        self.assertEqual(by_device["Y0"]["comment"], "Y0")
        self.assertNotIn("BAD", by_device)
        self.assertTrue(any("缺少注释" in item for item in spec["assumptions"]))


class PlanEntryTest(PlannerTestBase):
    def _project(self, requirement=REQUIREMENT, io=None):
        return {
            "id": "p-test",
            "name": "测试项目",
            "requirement": requirement,
            "io": list(io if io is not None else SAMPLE_IO),
            "style_profile": None,
            "feedback": [],
            "spec": None,
        }

    def test_plan_without_llm_config(self):
        config = {"llm": {"base_url": "", "api_key": "", "model": ""}}
        spec = plan(self._project(), config)
        validate_spec(spec)
        self.assertEqual(spec["project"]["name"], "测试项目")

    def test_plan_use_llm_falls_back_when_unconfigured(self):
        config = {"llm": {"base_url": "", "api_key": "", "model": ""}}
        spec = plan(self._project(), config, use_llm=True)
        validate_spec(spec)
        self.assertEqual(_station_spec(spec)["title"], "取放站")

    def test_plan_reads_style_from_project_dict(self):
        project = self._project(requirement="取放站:\n  竖直缸(Y0)下降\n  到位(X3) -> 夹紧(Y2)")
        project["style_profile"] = {
            "id": "s-1",
            "profile_version": "0.1",
            "name": "内嵌风格",
            "step_strategy": {"station_start": 500, "station_stride": 100, "increment": 10},
        }
        spec = plan(project, {"llm": {}})
        validate_spec(spec)
        self.assertEqual(spec["project"]["style_profile"], "s-1")
        self.assertEqual(_station_spec(spec)["step_range"], [500, 599])

    def test_plan_loads_style_from_workspace(self):
        from gx3gen import workspace

        style = workspace.save_style({
            "profile_version": "0.1",
            "name": "风格库风格",
            "step_strategy": {"station_start": 700, "station_stride": 100, "increment": 10},
        })
        project = workspace.create_project("风格联动")
        project["requirement"] = "取放站:\n  竖直缸(Y0)下降\n  到位(X3) -> 夹紧(Y2)"
        project["io"] = list(SAMPLE_IO)
        project["style_profile"] = style["id"]
        workspace.save_project(project)

        spec = plan(workspace.load_project(project["id"]), {"llm": {}})
        validate_spec(spec)
        self.assertEqual(spec["project"]["style_profile"], style["id"])
        self.assertEqual(_station_spec(spec)["step_range"], [700, 799])
        self.assertIn("风格库风格", "\n".join(spec["notes"]))

    def test_plan_reads_lessons_from_feedback_store(self):
        from gx3gen import workspace
        from gx3gen.feedback import store

        project = workspace.create_project("反馈联动")
        project["requirement"] = REQUIREMENT
        project["io"] = list(SAMPLE_IO)
        workspace.save_project(project)
        store.add(project["id"], {"kind": "program"}, "互锁", "error", "缺少气缸互锁")

        spec = plan(workspace.load_project(project["id"]), {"llm": {}})
        validate_spec(spec)
        joined = "\n".join(spec["notes"] + spec["assumptions"])
        self.assertIn("缺少气缸互锁", joined)


class LLMAdapterTest(PlannerTestBase):
    def test_llm_not_configured_raises(self):
        config = {"llm": {"base_url": "", "api_key": "", "model": ""}}
        with self.assertRaises(PlannerError) as context:
            llm.plan_with_llm(REQUIREMENT, SAMPLE_IO, None, None, config)
        self.assertIn("未配置 LLM", str(context.exception))

    def test_partial_llm_config_raises(self):
        config = {"llm": {"base_url": "http://127.0.0.1:11434/v1", "api_key": "", "model": "qwen"}}
        self.assertFalse(llm.llm_configured(config))
        with self.assertRaises(PlannerError):
            llm.plan_with_llm("x", [], None, None, config)

    def test_normalize_endpoint(self):
        self.assertEqual(
            llm.normalize_endpoint("https://api.example.com/v1"),
            "https://api.example.com/v1/chat/completions",
        )
        self.assertEqual(
            llm.normalize_endpoint("https://api.example.com/v1/chat/completions"),
            "https://api.example.com/v1/chat/completions",
        )
        self.assertEqual(llm.normalize_endpoint(""), "")

    def test_build_prompt_contains_schema_io_style_lessons_requirement(self):
        style = {"profile_version": "0.1", "name": "我的风格", "step_strategy": {}}
        lessons = [{"category": "互锁", "count": 2, "last_message": "缺少气缸互锁", "examples": []}]
        prompt = llm.build_prompt(REQUIREMENT, SAMPLE_IO, style, lessons)
        self.assertIn("ProgramSpec", prompt)
        self.assertIn("X0", prompt)
        self.assertIn("竖直缸", prompt)
        self.assertIn("我的风格", prompt)
        self.assertIn("缺少气缸互锁", prompt)
        self.assertIn("只输出 JSON", prompt)
        self.assertIn("取放站", prompt)

    def test_prompt_without_io_or_style(self):
        prompt = llm.build_prompt("初始化: SM402 置位 S0", [], None, None)
        self.assertIn("未提供 IO 表", prompt)
        self.assertIn("内置默认风格", prompt)

    def test_extract_json_handles_fences_and_noise(self):
        payload = {"spec_version": "0.1", "hello": "world"}
        fenced = "结果如下：\n```json\n%s\n```\n以上。" % json.dumps(payload)
        self.assertEqual(llm.extract_json(fenced), payload)
        self.assertEqual(llm.extract_json(json.dumps(payload)), payload)
        self.assertEqual(
            llm.extract_json("说明文字 " + json.dumps(payload) + " 结尾文字"), payload
        )
        with self.assertRaises(PlannerError):
            llm.extract_json("这不是 JSON")

    def test_parse_response_returns_valid_spec(self):
        sample_path = ROOT / "samples" / "three_station.spec.json"
        if not sample_path.exists():
            self.skipTest("缺少 samples/three_station.spec.json")
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        response = "```json\n%s\n```" % json.dumps(sample, ensure_ascii=False)
        spec = llm.parse_response(response, requirement=REQUIREMENT, io=SAMPLE_IO, name="AI项目")
        validate_spec(spec)
        self.assertEqual(spec["project"]["cpu"], "FX5U")
        self.assertEqual(spec["project"]["name"], "AI项目")

    def test_parse_response_rejects_invalid_spec(self):
        with self.assertRaises(PlannerError):
            llm.parse_response('{"spec_version": "0.1"}', requirement="x", io=SAMPLE_IO)
        with self.assertRaises(PlannerError):
            llm.parse_response('{"spec_version": "0.1", "project": {"name": "", "cpu": "FX5U"}, '
                               '"io": [], "segments": []}', requirement="x", io=SAMPLE_IO)

    def _configured(self):
        return {"llm": {"base_url": "http://127.0.0.1:11434/v1",
                        "api_key": "test-key", "model": "test-model", "timeout": 5}}

    def test_plan_with_llm_retries_once(self):
        from unittest import mock

        sample_path = ROOT / "samples" / "three_station.spec.json"
        if not sample_path.exists():
            self.skipTest("缺少 samples/three_station.spec.json")
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        calls = {"count": 0}

        def fake_call(prompt, config):
            calls["count"] += 1
            if calls["count"] == 1:
                return "这不是 JSON"
            return "```json\n%s\n```" % json.dumps(sample, ensure_ascii=False)

        with mock.patch.object(llm, "call_llm", side_effect=fake_call):
            spec = llm.plan_with_llm(REQUIREMENT, SAMPLE_IO, None, None, self._configured())
        validate_spec(spec)
        self.assertEqual(calls["count"], 2)

    def test_plan_with_llm_retry_exhausted_raises(self):
        from unittest import mock

        calls = {"count": 0}

        def fake_call(prompt, config):
            calls["count"] += 1
            return "仍然不是 JSON"

        with mock.patch.object(llm, "call_llm", side_effect=fake_call):
            with self.assertRaises(PlannerError) as context:
                llm.plan_with_llm(REQUIREMENT, SAMPLE_IO, None, None, self._configured())
        self.assertEqual(calls["count"], 2)
        self.assertIn("已重试", str(context.exception))

    def test_plan_use_llm_with_configured_llm(self):
        from unittest import mock

        sample_path = ROOT / "samples" / "three_station.spec.json"
        if not sample_path.exists():
            self.skipTest("缺少 samples/three_station.spec.json")
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        with mock.patch.object(
            llm, "call_llm", return_value=json.dumps(sample, ensure_ascii=False)
        ):
            project = {
                "id": "p-llm",
                "name": "AI 项目",
                "requirement": REQUIREMENT,
                "io": list(SAMPLE_IO),
                "style_profile": None,
                "lessons": [],
            }
            spec = plan(project, self._configured(), use_llm=True)
        validate_spec(spec)
        self.assertEqual(spec["project"]["name"], "AI 项目")
        self.assertTrue(any("LLM" in item for item in spec["assumptions"]))


class SamplesEndToEndTest(PlannerTestBase):
    def test_sample_files_plan_deterministic(self):
        requirement_path = ROOT / "samples" / "three_station.requirement.md"
        io_path = ROOT / "samples" / "three_station.io.csv"
        if not requirement_path.exists() or not io_path.exists():
            self.skipTest("缺少 samples 需求/IO 示例")
        requirement = requirement_path.read_text(encoding="utf-8")
        io_rows = parse_io_csv(io_path.read_bytes())
        spec = deterministic.plan_deterministic(
            requirement, io_rows, None, None, name="三工位取放"
        )
        validate_spec(spec)
        station = _station_spec(spec)
        self.assertEqual(station["step_range"], [100, 199])
        self.assertEqual(len(station["steps"]), 7)
        self.assertEqual(station["steps"][-1]["step"], 160)
        self.assertEqual(len(spec["alarms"]["cylinders"]), 3)
        self.assertEqual(
            [segment["kind"] for segment in spec["segments"]][:2], ["init", "station"]
        )


if __name__ == "__main__":
    unittest.main()
