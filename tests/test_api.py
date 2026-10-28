"""HTTP API 冒烟测试（标准库 unittest，不访问外部网络）。

- 用临时 ``GX3GEN_WORKSPACE`` 隔离真实 workspace；
- ``create_server(port=0)`` 起在后台线程，urllib 直连 127.0.0.1；
- generator / planner / feedback / style 等核心模块由其他任务并行实现，
  未就绪时对应用例自动 skip；就绪后自动覆盖完整链路。
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 必须在 import gx3gen.* 之前重定向 workspace，避免污染真实 workspace/
_TEMP_WORKSPACE = tempfile.mkdtemp(prefix="gx3gen-api-test-")
os.environ["GX3GEN_WORKSPACE"] = _TEMP_WORKSPACE

from gx3gen.app.server import create_server  # noqa: E402


def _module_ready(module_name: str, attr: str) -> bool:
    import importlib
    try:
        module = importlib.import_module("gx3gen." + module_name)
        if callable(getattr(module, attr, None)):
            return True
    except Exception:
        pass
    # 兼容实现放在子模块里的布局，例如 gx3gen.planner.plan.plan
    try:
        submodule = importlib.import_module("gx3gen.%s.%s" % (module_name, attr))
        return callable(getattr(submodule, attr, None))
    except Exception:
        return False


GENERATOR_READY = _module_ready("generator", "generate")
PLANNER_READY = _module_ready("planner", "plan")
FEEDBACK_ADD_READY = _module_ready("feedback.store", "add")
FEEDBACK_OPTIMIZE_READY = _module_ready("feedback.optimize", "apply_lessons")
STYLE_MINE_READY = _module_ready("style.miner", "mine")

INLINE_SPEC = {
    "spec_version": "0.1",
    "project": {
        "name": "接口测试项目",
        "cpu": "FX5U",
        "gx_version": "1.082",
        "style_profile": None,
    },
    "io": [
        {"device": "X0", "comment": "启动按钮", "role": "input"},
        {"device": "X1", "comment": "停止按钮", "role": "input"},
        {"device": "X3", "comment": "上料到位", "role": "input"},
        {"device": "Y0", "comment": "上料气缸", "role": "output"},
    ],
    "modes": {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"},
    "common": {
        "handshake_base": 1000,
        "alarm_base": 2000,
        "alarm_timer_base": 10,
        "start_step": 0,
        "station_step_base": 100,
        "station_step_stride": 100,
        "step_increment": 10,
        "auto_rungs": True,
    },
    "segments": [
        {
            "title": "初始化",
            "kind": "init",
            "rungs": [
                {
                    "comment": "上电初始化",
                    "instructions": [
                        {"op": "LD", "operands": ["SM402"]},
                        {"op": "SET", "operands": ["S0"]},
                    ],
                }
            ],
        },
        {
            "title": "上料站",
            "kind": "station",
            "step_range": [100, 190],
            "steps": [
                {
                    "step": 100,
                    "comment": "上料气缸伸出",
                    "actions": [{"set": ["Y0"], "comment": "伸出上料气缸"}],
                    "transition": {"conditions": ["X3"], "next": 110, "comment": "到位"},
                },
                {
                    "step": 110,
                    "comment": "上料完成",
                    "actions": [{"reset": ["Y0"]}],
                    "transition": {"conditions": ["X0"], "next": None},
                },
            ],
        },
        {
            "title": "公共控制",
            "kind": "common",
            "rungs": [
                {
                    "comment": "运行指示",
                    "instructions": [
                        {"op": "LD", "operands": ["X0"]},
                        {"op": "OUT", "operands": ["M500"]},
                    ],
                }
            ],
        },
    ],
    "alarms": {"enabled": False, "base": 2000, "timer_base": 10, "cylinders": []},
    "assumptions": ["以实际接线为准", "急停回路由硬件实现"],
    "notes": [],
}

IO_CSV_TABS = (
    "序号\t软元件名\t注释\t类型\n"
    "1\tX0\t启动按钮\t输入\n"
    "2\tX1\t停止按钮\t输入\n"
    "3\tY0\t上料气缸\t输出\n"
)


def _make_program_csv_bytes() -> bytes:
    """构造一个最小但结构完整的 GX Works3 程序本体 CSV（UTF-16LE + BOM）。"""
    rows = [
        ["接口测试程序"],
        ["机型信息:", "FX5CPU FX5U"],
        ["步号", "行间声明", "指令", "I/O(软元件)", "空白栏", "PI声明", "注解"],
        ["0", "[Title]INIT", "", "", "", "", ""],
        ["8", "", "LD", "SM402", "", "", ""],
        ["10", "", "SET", "S0", "", "", ""],
        ["13", "", "STL", "S0", "", "", ""],
        ["16", "", "LD", "S0", "", "", ""],
        ["18", "", "OUT", "Y0", "", "", ""],
        ["20", "", "LD", "S0", "", "", ""],
        ["22", "", "AND", "X0", "", "", ""],
        ["24", "", "SET", "S110", "", "", ""],
        ["27", "", "RETSTL", "", "", "", ""],
        ["28", "", "END", "", "", "", ""],
    ]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-16")


def _make_comment_csv_bytes() -> bytes:
    rows = [
        ["接口测试程序"],
        ["软元件名", "注释"],
        ["SM402", "初始脉冲"],
        ["S0", "初始步"],
        ["X0", "启动按钮"],
        ["Y0", "上料气缸"],
    ]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-16")


class _Client:
    """极简 HTTP 客户端（只访问本机测试服务）。"""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def raw(self, method: str, path: str, data=None, headers=None):
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def json(self, method: str, path: str, body=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        status, response_headers, payload = self.raw(method, path, data, headers)
        try:
            parsed = json.loads(payload.decode("utf-8")) if payload else {}
        except ValueError:
            parsed = {"_raw": payload.decode("utf-8", "replace")}
        return status, parsed, response_headers


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["GX3GEN_WORKSPACE"] = _TEMP_WORKSPACE
        cls.server = create_server("127.0.0.1", 0)
        cls.server.quiet = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = _Client("http://127.0.0.1:%d" % cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        status, data, _headers = self.client.json(
            "POST", "/api/projects", {"name": "测试项目-" + self._testMethodName})
        self.assertEqual(status, 200, data)
        self.project = data["project"]
        self.project_id = self.project["id"]
        self.project_path = "/api/projects/%s" % self.project_id

    # -- 基础 --------------------------------------------------------------
    def test_health(self):
        status, data, _headers = self.client.json("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertIn("version", data)

    def test_state_returns_config_projects_styles_without_api_key(self):
        status, data, _headers = self.client.json("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("config", data)
        self.assertIn("projects", data)
        self.assertIn("styles", data)
        self.assertIsInstance(data["projects"], list)
        self.assertNotIn("api_key", data["config"].get("llm", {}))
        project_ids = [item.get("id") for item in data["projects"]]
        self.assertIn(self.project_id, project_ids)

    def test_index_and_static_assets(self):
        status, headers, payload = self.client.raw("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("gx3gen", payload.decode("utf-8"))

        status, headers, payload = self.client.raw("GET", "/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("Content-Type", ""))
        self.assertGreater(len(payload), 1000)

        status, headers, _payload = self.client.raw("GET", "/static/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("Content-Type", ""))

    def test_static_traversal_is_blocked(self):
        for path in (
            "/static/..%2F..%2Fsrc%2Fgx3gen%2Fconfig.py",
            "/static/..%2F..%2FREADME.md",
            "/static/not-exist.js",
        ):
            status, _headers, payload = self.client.raw("GET", path)
            self.assertIn(status, (400, 403, 404), path)
            self.assertNotIn(b"def workspace_root", payload)

    def test_unknown_api_and_method_not_allowed(self):
        status, data, _headers = self.client.json("GET", "/api/not-a-real-endpoint")
        self.assertEqual(status, 404)
        self.assertIn("error", data)

        status, data, _headers = self.client.json("DELETE", "/api/state")
        self.assertEqual(status, 405)
        self.assertIn("error", data)

        status, data, _headers = self.client.json("POST", "/api/state", {})
        self.assertEqual(status, 405)

    def test_bad_json_body(self):
        status, headers, payload = self.client.raw(
            "POST", "/api/projects", b"{not json",
            {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(payload.decode("utf-8")))

    def test_head_request(self):
        status, headers, payload = self.client.raw("HEAD", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, b"")
        self.assertGreater(int(headers.get("Content-Length", "0")), 0)

    # -- 项目 --------------------------------------------------------------
    def test_create_and_get_project(self):
        status, data, _headers = self.client.json("GET", self.project_path)
        self.assertEqual(status, 200)
        project = data["project"]
        self.assertEqual(project["id"], self.project_id)
        self.assertIn("测试项目-", project["name"])
        self.assertEqual(project.get("io"), [])
        self.assertIsNone(project.get("spec"))

        status, data, _headers = self.client.json("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == self.project_id for item in data["projects"]))

    def test_get_missing_project_returns_404(self):
        status, data, _headers = self.client.json("GET", "/api/projects/p-does-not-exist")
        self.assertEqual(status, 404)
        self.assertIn("error", data)

    def test_create_project_requires_name(self):
        status, data, _headers = self.client.json("POST", "/api/projects", {"name": "   "})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_update_project_requirement_io_and_style(self):
        from gx3gen import workspace as workspace_mod
        style = workspace_mod.save_style({
            "profile_version": "0.1",
            "name": "测试风格-A",
            "segments": [{"order": 1, "title": "INIT", "network_count": 3, "has_stl": False}],
        })
        status, data, _headers = self.client.json("PUT", self.project_path, {
            "requirement": "三工位装配线需求",
            "io_csv": IO_CSV_TABS,
            "style_profile": style["id"],
        })
        self.assertEqual(status, 200, data)
        project = data["project"]
        self.assertEqual(project["requirement"], "三工位装配线需求")
        self.assertEqual(len(project["io"]), 3)
        self.assertEqual(project["io"][0]["device"], "X0")
        self.assertEqual(project["io"][2]["role"], "output")
        self.assertEqual(project["style_profile"]["id"], style["id"])

        status, data, _headers = self.client.json("GET", self.project_path)
        self.assertEqual(data["project"]["style_profile"]["id"], style["id"])

        # 移除风格
        status, data, _headers = self.client.json("PUT", self.project_path, {"style_profile": None})
        self.assertEqual(status, 200)
        self.assertIsNone(data["project"]["style_profile"])

    def test_update_project_rejects_invalid_spec(self):
        status, data, _headers = self.client.json("PUT", self.project_path, {
            "spec": {"spec_version": "0.1", "project": {"name": "x", "cpu": "FX9U"}, "io": []},
        })
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_io_parse_endpoint(self):
        status, data, _headers = self.client.json("POST", "/api/io/parse", {"text": IO_CSV_TABS})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["io"][0]["device"], "X0")
        self.assertEqual(data["io"][2]["comment"], "上料气缸")

    def test_spec_validate_endpoint(self):
        status, data, _headers = self.client.json("POST", "/api/spec/validate", {"spec": INLINE_SPEC})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)

        status, data, _headers = self.client.json(
            "POST", "/api/spec/validate", {"spec": {"spec_version": "9.9"}})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertTrue(data["error"])

    def test_config_does_not_expose_api_key(self):
        from gx3gen import config as config_mod
        secret = "SECRET-KEY-123"
        status, data, _headers = self.client.json("POST", "/api/config", {
            "llm": {
                "base_url": "http://127.0.0.1:9999/v1",
                "model": "test-model",
                "api_key": secret,
                "timeout": 33,
            }
        })
        self.assertEqual(status, 200, data)
        self.assertEqual(data["config"]["llm"]["base_url"], "http://127.0.0.1:9999/v1")
        self.assertNotIn("api_key", data["config"]["llm"])

        status, state, _headers = self.client.json("GET", "/api/state")
        self.assertEqual(status, 200)
        raw_state = json.dumps(state, ensure_ascii=False)
        self.assertNotIn(secret, raw_state)
        self.assertNotIn("api_key", state["config"]["llm"])
        self.assertEqual(state["config"]["llm"]["timeout"], 33)

        saved = config_mod.load_config()
        self.assertEqual(saved["llm"]["api_key"], secret)
        self.assertEqual(saved["llm"]["timeout"], 33)

        # 留空 api_key 表示「不修改」
        status, data, _headers = self.client.json("POST", "/api/config", {
            "llm": {"model": "test-model-2", "api_key": ""}})
        self.assertEqual(status, 200)
        self.assertEqual(config_mod.load_config()["llm"]["api_key"], secret)
        self.assertEqual(config_mod.load_config()["llm"]["model"], "test-model-2")

    # -- 产物下载 ----------------------------------------------------------
    def test_artifact_download_and_traversal(self):
        from gx3gen import workspace as workspace_mod
        path = workspace_mod.artifact_path(self.project_id, "程序本体.csv")
        Path(path).write_bytes("下载测试".encode("utf-16"))

        encoded = urllib.parse.quote("程序本体.csv")
        status, headers, payload = self.client.raw(
            "GET", "%s/artifact/%s" % (self.project_path, encoded))
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers.get("Content-Type", ""))
        disposition = headers.get("Content-Disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn("filename*=UTF-8''", disposition)
        self.assertEqual(payload.decode("utf-16"), "下载测试")

        # 生成器若输出 program.csv / report.md 等实际文件名，也要能用契约中文名下载
        generator_path = workspace_mod.artifact_path(self.project_id, "report.md")
        Path(generator_path).write_bytes("# 生成器报告".encode("utf-8"))
        status, headers, payload = self.client.raw(
            "GET", "%s/artifact/%s" % (self.project_path, urllib.parse.quote("生成报告.md")))
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertEqual(payload.decode("utf-8"), "# 生成器报告")

        status, _headers, payload = self.client.raw(
            "GET", "%s/artifact/%s" % (self.project_path, "not-exist.csv"))
        self.assertEqual(status, 404)

        status, _headers, payload = self.client.raw(
            "GET", "%s/artifact/..%%2Fproject.json" % self.project_path)
        self.assertIn(status, (400, 403, 404))
        self.assertNotIn(b"\"id\"", payload)

    def test_generate_without_spec_is_rejected(self):
        status, data, _headers = self.client.json("POST", "/api/generate", {
            "project_id": self.project_id})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_optimize_without_spec_is_rejected(self):
        status, data, _headers = self.client.json("POST", "/api/optimize", {
            "project_id": self.project_id})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    # -- 核心模块就绪后的完整链路 -------------------------------------------
    @unittest.skipUnless(STYLE_MINE_READY, "style.miner.mine 尚未实现")
    def test_style_import_and_list(self):
        program_b64 = base64.b64encode(_make_program_csv_bytes()).decode("ascii")
        comment_b64 = base64.b64encode(_make_comment_csv_bytes()).decode("ascii")
        status, profile, _headers = self.client.json("POST", "/api/style/import", {
            "name": "接口测试风格",
            "files": [
                {"filename": "测试程序本体.csv", "base64": program_b64},
                {"filename": "测试软元件注释.csv", "base64": comment_b64},
            ],
        })
        self.assertEqual(status, 200, profile)
        self.assertTrue(profile.get("id"), profile)
        self.assertEqual(profile.get("name"), "接口测试风格")
        self.assertIn("segments", profile)

        status, data, _headers = self.client.json("GET", "/api/styles")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == profile["id"] for item in data["styles"]))

        status, data, _headers = self.client.json(
            "GET", "/api/styles/%s" % profile["id"])
        self.assertEqual(status, 200)
        self.assertEqual(data["style"]["id"], profile["id"])

        status, data, _headers = self.client.json("PUT", self.project_path, {
            "style_profile": profile["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(data["project"]["style_profile"]["id"], profile["id"])

    def test_style_import_rejects_bad_base64(self):
        status, data, _headers = self.client.json("POST", "/api/style/import", {
            "name": "坏文件",
            "files": [{"filename": "bad.csv", "base64": "!!!not-base64!!!"}],
        })
        self.assertEqual(status, 400)
        self.assertIn("error", data)

        status, data, _headers = self.client.json("POST", "/api/style/import", {
            "name": "没有文件", "files": []})
        self.assertEqual(status, 400)

    @unittest.skipUnless(PLANNER_READY, "planner.plan 尚未实现")
    def test_plan_endpoint(self):
        status, _data, _headers = self.client.json("PUT", self.project_path, {
            "requirement": (
                "三工位装配线：上料站按下启动后上料气缸伸出，到位后进入压装站；"
                "压装站压装到位后进入检测站；检测站合格放行，不合格报警。"
                "需要启动/停止/急停、自动与手动模式、运行指示灯与报警灯。"
            ),
            "io_csv": IO_CSV_TABS,
        })
        self.assertEqual(status, 200)
        status, data, _headers = self.client.json("POST", "/api/plan", {
            "project_id": self.project_id, "use_llm": False})
        self.assertEqual(status, 200, data)
        self.assertIn("spec", data)
        self.assertIn("checklist", data)
        self.assertIn("prompt_hint", data)
        spec = data["spec"]
        self.assertEqual(spec.get("spec_version"), "0.1")
        self.assertIsInstance(spec.get("segments"), list)
        self.assertTrue(spec["segments"])

        status, data, _headers = self.client.json("GET", self.project_path)
        self.assertIsNotNone(data["project"]["spec"])

    @unittest.skipUnless(GENERATOR_READY, "generator.generate 尚未实现")
    def test_generate_with_inline_spec(self):
        status, data, _headers = self.client.json(
            "PUT", self.project_path, {"spec": INLINE_SPEC})
        self.assertEqual(status, 200, data)

        status, data, _headers = self.client.json("POST", "/api/generate", {
            "project_id": self.project_id})
        self.assertEqual(status, 200, data)
        outputs = data.get("outputs") or {}
        self.assertIn("program_csv", outputs)
        self.assertTrue(data.get("artifacts"), data)
        self.assertIn("stats", data)
        self.assertIn("summary", data)
        self.assertTrue(data.get("segments"))

        status, data, _headers = self.client.json("GET", self.project_path)
        project = data["project"]
        self.assertTrue(project["outputs"])
        self.assertTrue(project["stats"])

        name = outputs["program_csv"]
        status, headers, payload = self.client.raw(
            "GET", "%s/artifact/%s" % (self.project_path, urllib.parse.quote(name)))
        self.assertEqual(status, 200)
        self.assertGreater(len(payload), 0)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))

    @unittest.skipUnless(FEEDBACK_ADD_READY, "feedback.store.add 尚未实现")
    def test_feedback_roundtrip(self):
        target = {"kind": "segment", "segment": "上料站"}
        status, data, _headers = self.client.json("POST", "/api/feedback", {
            "project_id": self.project_id,
            "target": target,
            "category": "逻辑",
            "severity": "error",
            "message": "缺少上料站到压装站的交接信号",
        })
        self.assertEqual(status, 200, data)
        self.assertIn("record", data)
        self.assertIn("缺少上料站到压装站的交接信号", data["record"].get("message", ""))

        status, data, _headers = self.client.json(
            "GET", "/api/feedback?project_id=%s" % self.project_id)
        self.assertEqual(status, 200)
        messages = [item.get("message", "") for item in data["feedback"]
                    if isinstance(item, dict)]
        self.assertTrue(any("交接信号" in message for message in messages), data)

    @unittest.skipUnless(FEEDBACK_ADD_READY and FEEDBACK_OPTIMIZE_READY,
                         "feedback.store.add / feedback.optimize.apply_lessons 尚未实现")
    def test_optimize_after_feedback(self):
        status, _data, _headers = self.client.json(
            "PUT", self.project_path, {"spec": INLINE_SPEC})
        self.assertEqual(status, 200)
        status, _data, _headers = self.client.json("POST", "/api/feedback", {
            "project_id": self.project_id,
            "target": {"kind": "program"},
            "category": "结构",
            "severity": "warning",
            "message": "段顺序应为：初始化 → 设备段 → 公共段",
        })
        self.assertEqual(status, 200)
        status, data, _headers = self.client.json("POST", "/api/optimize", {
            "project_id": self.project_id})
        self.assertEqual(status, 200, data)
        self.assertIn("spec", data)
        self.assertIn("checklist", data)
        self.assertIn("prompt_hint", data)


if __name__ == "__main__":
    unittest.main()
