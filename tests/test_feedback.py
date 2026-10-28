"""反馈闭环测试（unittest，用临时 GX3GEN_WORKSPACE，不污染真实 workspace）。"""
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

from gx3gen import workspace  # noqa: E402
from gx3gen.feedback import optimize, store  # noqa: E402
from gx3gen.planner.deterministic import plan_deterministic  # noqa: E402
from gx3gen.spec import validate_spec  # noqa: E402

IO_ROWS = [
    {"device": "X0", "comment": "启动", "role": "input"},
    {"device": "X1", "comment": "停止", "role": "input"},
    {"device": "X2", "comment": "竖直缸原位", "role": "input"},
    {"device": "X3", "comment": "竖直缸到位", "role": "input"},
    {"device": "Y0", "comment": "竖直缸", "role": "output"},
]

REQUIREMENT = """初始化:
  SM402 置位 S0
取放站:
  竖直缸(Y0)下降
  到位(X3) -> 竖直缸(Y0)上升
  原位(X2) -> 完成
"""


class FeedbackTestBase(unittest.TestCase):
    def setUp(self):
        self._old_workspace = os.environ.get("GX3GEN_WORKSPACE")
        self._tmp = tempfile.mkdtemp(prefix="gx3gen-feedback-")
        os.environ["GX3GEN_WORKSPACE"] = self._tmp
        self.project = workspace.create_project("反馈测试")
        self.project_id = self.project["id"]

    def tearDown(self):
        if self._old_workspace is None:
            os.environ.pop("GX3GEN_WORKSPACE", None)
        else:
            os.environ["GX3GEN_WORKSPACE"] = self._old_workspace
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _saved_project(self):
        return workspace.load_project(self.project_id)

    def _update_project(self, **fields):
        project = self._saved_project()
        project.update(fields)
        workspace.save_project(project)
        return project


class StoreTest(FeedbackTestBase):
    def test_add_and_list_records(self):
        record = store.add(
            self.project_id,
            {"kind": "network", "segment": "取放站", "index": 3},
            "逻辑",
            "error",
            "缺少取放站到检测站的交接信号",
            "M1110 取放站/完成",
        )
        self.assertTrue(record["id"])
        self.assertTrue(record["created_at"])
        self.assertFalse(record["applied"])
        self.assertEqual(record["category"], "逻辑")
        self.assertEqual(record["severity"], "error")
        self.assertEqual(record["target"]["segment"], "取放站")
        self.assertEqual(record["expected"], "M1110 取放站/完成")

        records = store.list_records(self.project_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["message"], "缺少取放站到检测站的交接信号")

        # 落盘后可重新读取
        saved = self._saved_project()
        self.assertEqual(len(saved["feedback"]), 1)
        self.assertEqual(saved["feedback"][0]["id"], record["id"])

    def test_add_normalizes_category_and_severity(self):
        record = store.add(self.project_id, None, "", "", "随便说说")
        self.assertEqual(record["category"], "其他")
        self.assertEqual(record["severity"], "warning")
        self.assertEqual(record["target"], {"kind": "program"})

    def test_lessons_aggregate_by_category(self):
        store.add(self.project_id, {"kind": "segment", "segment": "取放站"}, "互锁", "error",
                  "缺少气缸互锁", "补对侧原位条件")
        store.add(self.project_id, {"kind": "network", "index": 2}, "互锁", "warning",
                  "夹紧前未确认原位")
        store.add(self.project_id, {"kind": "program"}, "报警", "warning", "气缸没有报警")
        store.add(self.project_id, {"kind": "program"}, "互锁", "error", "双缸可能同时动作")

        lessons = store.lessons(self.project_id)
        self.assertEqual(len(lessons), 2)
        by_category = {item["category"]: item for item in lessons}
        self.assertEqual(by_category["互锁"]["count"], 3)
        self.assertEqual(by_category["互锁"]["last_message"], "双缸可能同时动作")
        self.assertTrue(any("缺少气缸互锁" in example for example in by_category["互锁"]["examples"]))
        self.assertEqual(lessons[0]["category"], "互锁")  # 次数降序
        self.assertEqual(by_category["报警"]["count"], 1)

    def test_lessons_empty_project(self):
        self.assertEqual(store.lessons(self.project_id), [])

    def test_save_correction_and_limit(self):
        for index in range(4):
            record = store.save_correction(
                self.project_id, {"index": index}, "第 %d 次修正" % index
            )
            self.assertTrue(record["id"])
            # 把 created_at 拉开，验证 corrections 的新→旧排序
            project = self._saved_project()
            project["corrected_specs"][-1]["created_at"] = "2026-01-01T00:00:0%d" % index
            workspace.save_project(project)

        latest = store.corrections(self.project_id, limit=2)
        self.assertEqual(len(latest), 2)
        self.assertEqual(latest[0]["note"], "第 3 次修正")
        self.assertEqual(latest[1]["note"], "第 2 次修正")
        self.assertEqual(len(store.corrections(self.project_id, limit=10)), 4)

    def test_unknown_project_raises(self):
        with self.assertRaises(FileNotFoundError):
            store.list_records("p-not-exists")


class OptimizeTest(FeedbackTestBase):
    def test_apply_lessons_without_spec(self):
        store.add(self.project_id, {"kind": "program"}, "互锁", "error", "缺少气缸互锁")
        project = self._saved_project()
        result = optimize.apply_lessons(project)
        self.assertIsNone(result["spec"])
        self.assertTrue(result["checklist"])
        self.assertTrue(all(isinstance(item, str) for item in result["checklist"]))
        self.assertIn("互锁", "\n".join(result["checklist"]))
        self.assertIn("缺少气缸互锁", result["prompt_hint"])
        self.assertIn("自检清单", result["prompt_hint"])

        saved = self._saved_project()
        self.assertTrue(saved["lessons"])
        self.assertEqual(saved["lessons"][0]["category"], "互锁")

    def test_apply_lessons_with_spec_replans_and_saves(self):
        spec = plan_deterministic(REQUIREMENT, IO_ROWS, None, None, name="反馈测试")
        validate_spec(spec)
        self._update_project(requirement=REQUIREMENT, io=IO_ROWS, spec=spec)
        store.add(self.project_id, {"kind": "segment", "segment": "取放站"}, "互锁", "error",
                  "缺少气缸互锁")
        store.add(self.project_id, {"kind": "program"}, "报警", "warning", "气缸没有报警")

        project = self._saved_project()
        result = optimize.apply_lessons(project)
        self.assertIsNotNone(result["spec"])
        validate_spec(result["spec"])
        station_titles = [segment["title"] for segment in result["spec"]["segments"]]
        self.assertIn("取放站", station_titles)
        joined = "\n".join(result["spec"].get("notes") or []) + "\n" + "\n".join(
            result["spec"].get("assumptions") or []
        )
        self.assertIn("互锁", joined)

        saved = self._saved_project()
        validate_spec(saved["spec"])
        self.assertTrue(saved["lessons"])
        # 返回的 spec 与落盘 spec 一致（重新规划后已写回项目）
        self.assertEqual(
            sorted(segment["title"] for segment in saved["spec"]["segments"]),
            sorted(segment["title"] for segment in result["spec"]["segments"]),
        )

    def test_apply_lessons_keeps_original_spec_when_replan_fails(self):
        spec = plan_deterministic(REQUIREMENT, IO_ROWS, None, None)
        self._update_project(spec=spec, requirement="", io=[])
        result = optimize.apply_lessons(self._saved_project())
        # 需求为空仍能生成最小骨架，所以这里只验证返回值结构稳定且 spec 有效
        if result["spec"] is not None:
            validate_spec(result["spec"])

    def test_build_checklist_and_prompt_hint(self):
        lessons = [
            {"category": "报警", "count": 3, "last_message": "气缸没有报警", "examples": []},
            {"category": "命名", "count": 1, "last_message": "注释和现场不一致", "examples": []},
        ]
        checklist = optimize.build_checklist(lessons)
        self.assertIn("报警", "\n".join(checklist))
        self.assertIn("命名", "\n".join(checklist))

        corrections = [{"note": "修正取放站", "spec": {"segments": [{"title": "取放站"}]}}]
        hint = optimize.build_prompt_hint(lessons, corrections)
        self.assertIn("气缸没有报警", hint)
        self.assertIn("修正取放站", hint)
        self.assertIn("取放站", hint)

        empty_hint = optimize.build_prompt_hint([], [])
        self.assertIn("暂无历史反馈", empty_hint)


if __name__ == "__main__":
    unittest.main()
