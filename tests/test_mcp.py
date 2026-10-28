"""MCP stdio server 测试（标准库 unittest；不访问网络、不污染真实 workspace）。

测试方式：subprocess 启动 ``python run_mcp.py``，通过 stdin/stdout 按行收发
JSON-RPC 消息（与真实 MCP 客户端一致）。``GX3GEN_WORKSPACE`` 指向临时目录。
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_MCP = REPO_ROOT / "run_mcp.py"

REQUIRED_TOOLS = {
    "gx3gen_get_generation_context",
    "gx3gen_validate_spec",
    "gx3gen_generate_program",
    "gx3gen_audit_csv",
    "gx3gen_manual_list",
    "gx3gen_manual_search",
    "gx3gen_manual_toc",
    "gx3gen_manual_read",
    "gx3gen_create_project",
    "gx3gen_save_spec",
    "gx3gen_load_spec",
    "gx3gen_list_projects",
    "gx3gen_get_examples",
    "gx3gen_list_styles",
    "gx3gen_import_style",
    "gx3gen_add_feedback",
    "gx3gen_get_lessons",
}

# 结构合法的最小 ProgramSpec（用于 validate 断言 ok=True）
LEGAL_SPEC = {
    "spec_version": "0.1",
    "project": {"name": "MCP 测试项目", "cpu": "FX5U", "gx_version": "1.082",
                "style_profile": None},
    "io": [
        {"device": "X0", "comment": "启动按钮", "role": "input"},
        {"device": "X1", "comment": "停止按钮", "role": "input"},
        {"device": "X3", "comment": "气缸到位", "role": "input"},
        {"device": "Y0", "comment": "气缸输出", "role": "output"},
    ],
    "modes": {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522"},
    "common": {
        "handshake_base": 1000, "alarm_base": 2000, "alarm_timer_base": 10,
        "start_step": 0, "station_step_base": 100, "station_step_stride": 100,
        "step_increment": 10, "auto_rungs": True,
    },
    "segments": [
        {
            "title": "初始化", "kind": "init",
            "rungs": [{
                "comment": "上电置待机步",
                "instructions": [{"op": "LD", "operands": ["SM402"]},
                                 {"op": "SET", "operands": ["S0"]}],
            }],
        },
        {
            "title": "测试工位", "kind": "station", "step_range": [100, 199],
            "entry_conditions": ["S0"],
            "steps": [
                {"step": 100,
                 "actions": [{"set": ["Y0"], "comment": "气缸伸出"}],
                 "transition": {"conditions": ["X3"], "next": 110, "comment": "到位"}},
                {"step": 110,
                 "actions": [{"reset": ["Y0"], "comment": "气缸缩回"}],
                 "transition": {"conditions": ["X1"], "next": None, "comment": "回待机"}},
            ],
        },
        {
            "title": "启动停止", "kind": "common",
            "rungs": [
                {"comment": "启动",
                 "instructions": [{"op": "LD", "operands": ["X0"]},
                                  {"op": "SET", "operands": ["M500"]}]},
                {"comment": "停止",
                 "instructions": [{"op": "LD", "operands": ["X1"]},
                                  {"op": "RST", "operands": ["M500"]}]},
            ],
        },
    ],
    "alarms": {"enabled": False, "base": 2000, "timer_base": 10, "cylinders": []},
    "assumptions": ["按钮/延时待现场确认"],
    "notes": [],
}

ILLEGAL_SPEC = {
    "spec_version": "9.9",
    "project": {"name": "", "cpu": "FX3U"},
    "io": [{"device": "not-a-device", "comment": "", "role": "bad"}],
    "segments": [],
}


class McpClient:
    """极简 MCP stdio 客户端（一行一条 JSON-RPC 消息）。"""

    def __init__(self, workspace: Path, timeout: float = 60.0):
        env = dict(os.environ)
        env["GX3GEN_WORKSPACE"] = str(workspace)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        self.timeout = timeout
        self.proc = subprocess.Popen(
            [sys.executable, str(RUN_MCP)],
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        self._messages: "queue.Queue" = queue.Queue()
        self._stderr_lines = []
        self._next_id = 0
        threading.Thread(target=self._read_stdout, name="mcp-stdout", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="mcp-stderr", daemon=True).start()

    # -- IO -----------------------------------------------------------------
    def _read_stdout(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if line:
                    self._messages.put(line)
        finally:
            self._messages.put(None)

    def _read_stderr(self):
        try:
            for line in self.proc.stderr:
                self._stderr_lines.append(line.rstrip())
        except Exception:  # pragma: no cover - 进程已退出
            pass

    def send(self, payload):
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        self.proc.stdin.write(payload + "\n")
        self.proc.stdin.flush()

    def recv(self):
        try:
            item = self._messages.get(timeout=self.timeout)
        except queue.Empty:
            raise AssertionError(
                "等待 MCP 响应超时（stderr 末尾：%s）" % "\n".join(self._stderr_lines[-20:]))
        if item is None:
            raise AssertionError(
                "MCP server 已关闭 stdout（returncode=%r；stderr 末尾：%s）"
                % (self.proc.poll(), "\n".join(self._stderr_lines[-20:])))
        return json.loads(item)

    def request(self, method, params=None, req_id=None):
        if req_id is None:
            self._next_id += 1
            req_id = "test-%d" % self._next_id
        message = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        response = self.recv()
        if response.get("id") != req_id:
            raise AssertionError("响应 id 不匹配：期望 %r，收到 %r（%r）" % (req_id, response.get("id"), response))
        return response

    def call_tool(self, name, arguments=None):
        response = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if "error" in response:
            raise AssertionError("tools/call 返回 JSON-RPC error：%r" % response["error"])
        result = response.get("result") or {}
        content = result.get("content") or []
        text = content[0].get("text") if content else ""
        return result, text or ""

    def close(self):
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:  # pragma: no cover
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.proc.kill()
            self.proc.wait(timeout=10)

    def stderr_tail(self, count=20):
        return "\n".join(self._stderr_lines[-count:])


def _parse_json(text):
    try:
        return json.loads(text)
    except ValueError as exc:
        raise AssertionError("工具返回的 text 不是 JSON：%s\n%s" % (exc, text[:800]))


class McpServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="gx3gen-mcp-test-")
        cls.workspace = Path(cls.tmpdir) / "workspace"
        cls.workspace.mkdir(parents=True, exist_ok=True)
        cls.client = McpClient(cls.workspace)
        cls.initialize = cls.client.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "unittest", "version": "0.0.0"},
        })

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.close()
        finally:
            shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # -- 协议 ---------------------------------------------------------------
    def test_01_initialize_returns_server_info(self):
        result = self.initialize.get("result") or {}
        self.assertEqual(result.get("protocolVersion"), "2024-11-05")
        server_info = result.get("serverInfo") or {}
        self.assertEqual(server_info.get("name"), "gx3gen")
        self.assertEqual(server_info.get("version"), "0.1.0")
        capabilities = result.get("capabilities") or {}
        self.assertIn("tools", capabilities)
        self.assertIn("instructions", result)

    def test_02_tools_list_contains_required_tools(self):
        response = self.client.request("tools/list", {})
        tools = (response.get("result") or {}).get("tools") or []
        names = {tool.get("name") for tool in tools}
        missing = REQUIRED_TOOLS - names
        self.assertFalse(missing, "缺少工具：%s" % sorted(missing))
        for tool in tools:
            self.assertTrue(tool.get("description"), "工具 %s 缺少 description" % tool.get("name"))
            self.assertIsInstance(tool.get("inputSchema"), dict)
            self.assertEqual(tool["inputSchema"].get("type"), "object")

    def test_03_ping(self):
        response = self.client.request("ping", {})
        self.assertEqual(response.get("result"), {})

    def test_04_notification_initialized_is_ignored(self):
        self.client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        response = self.client.request("ping", {}, req_id="after-notification")
        self.assertEqual(response.get("id"), "after-notification")
        self.assertEqual(response.get("result"), {})

    def test_05_unknown_method_returns_32601(self):
        response = self.client.request("resources/list", {}, req_id="unknown-method")
        error = response.get("error") or {}
        self.assertEqual(error.get("code"), -32601)
        self.assertIn("Method not found", error.get("message", ""))

    def test_06_parse_error_does_not_kill_server(self):
        self.client.send("{this is not json")
        response = self.client.recv()
        error = response.get("error") or {}
        self.assertEqual(error.get("code"), -32700)
        # server 仍然存活
        self.assertEqual(self.client.request("ping", {}, req_id="alive-after-parse").get("result"), {})

    def test_06b_bom_prefixed_line_is_tolerated(self):
        # Windows PowerShell 管道可能给 stdin 首行加 UTF-8 BOM
        self.client.send("\ufeff" + json.dumps(
            {"jsonrpc": "2.0", "id": "bom-ping", "method": "ping"}))
        response = self.client.recv()
        self.assertEqual(response.get("id"), "bom-ping")
        self.assertEqual(response.get("result"), {})

    def test_07_unknown_tool_returns_structured_error(self):
        result, text = self.client.call_tool("gx3gen.no_such_tool", {})
        self.assertTrue(result.get("isError"))
        payload = _parse_json(text)
        self.assertFalse(payload.get("ok"))
        self.assertIn("未知工具", payload.get("error", ""))

    def test_08_tools_call_without_name_returns_32602(self):
        response = self.client.request("tools/call", {"arguments": {}}, req_id="no-tool-name")
        error = response.get("error") or {}
        self.assertEqual(error.get("code"), -32602)

    # -- 工具 ---------------------------------------------------------------
    def test_10_generation_context_contains_spec_and_quality(self):
        result, text = self.client.call_tool("gx3gen_get_generation_context", {
            "requirement": "做一个双向红绿灯",
        })
        self.assertFalse(result.get("isError"), result)
        for keyword in ("ProgramSpec", "质量", "红绿灯", "gx3gen_validate_spec",
                        "gx3gen_generate_program"):
            self.assertIn(keyword, text)
        # 上下文应短而可用（给 agent 的提示预算）
        self.assertLess(len(text), 20000)

    def test_11_generation_context_parses_io_csv(self):
        io_csv = "序号,软元件名,注释,类型\n1,X0,启动按钮,输入\n2,Y0,指示灯,输出\n"
        _result, text = self.client.call_tool("gx3gen_get_generation_context", {
            "requirement": "IO 解析测试",
            "io_csv": io_csv,
        })
        self.assertIn("启动按钮", text)
        self.assertIn("指示灯", text)

    def test_11b_generation_context_accepts_nested_req_object(self):
        # 兼容 {"req": {...}} 包装的调用方式（部分 MCP 客户端/agent 会这样传）
        result, text = self.client.call_tool("gx3gen_get_generation_context", {
            "req": {"requirement": "做一个双向红绿灯"},
        })
        self.assertFalse(result.get("isError"), result)
        self.assertIn("ProgramSpec", text)

    def test_12_validate_spec_legal(self):
        result, text = self.client.call_tool("gx3gen_validate_spec", {"spec": LEGAL_SPEC})
        payload = _parse_json(text)
        self.assertFalse(result.get("isError"), payload)
        self.assertTrue(payload.get("ok"), payload)
        self.assertEqual(payload.get("errors"), [])
        self.assertIn("quality", payload)

    def test_13_validate_spec_illegal(self):
        result, text = self.client.call_tool("gx3gen_validate_spec", {"spec": ILLEGAL_SPEC})
        payload = _parse_json(text)
        self.assertTrue(result.get("isError"))
        self.assertFalse(payload.get("ok"))
        self.assertTrue(payload.get("errors"))
        joined = "\n".join(payload["errors"])
        self.assertIn("spec_version", joined)

    def test_14_generate_program_returns_artifacts_or_quality_error(self):
        spec_path = REPO_ROOT / "samples" / "three_station.spec.json"
        if not spec_path.exists():
            self.skipTest("samples/three_station.spec.json 不存在")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        out_dir = str(Path(self.tmpdir) / "out")
        result, text = self.client.call_tool("gx3gen_generate_program", {
            "spec": spec,
            "requirement": "三工位取放",
            "out_dir": out_dir,
        })
        payload = _parse_json(text)
        self.assertIsInstance(payload.get("ok"), bool)
        if payload["ok"]:
            self.assertFalse(result.get("isError"), result)
            self.assertTrue(Path(payload["program_csv"]).exists(), payload["program_csv"])
            self.assertTrue(Path(payload["comment_csv"]).exists(), payload["comment_csv"])
            self.assertIn("summary", payload)
            self.assertIn("quality", payload)
            self.assertIn("assumptions", payload)
            # 产物必须落在传入的 out_dir（不得写真实 workspace）
            self.assertTrue(str(payload["program_csv"]).replace("\\", "/").startswith(
                out_dir.replace("\\", "/")))
        else:
            # 质量红线拦截：必须返回结构化错误而不是崩溃
            self.assertTrue(payload.get("errors"), payload)
            self.assertIn("quality", payload)

    def test_15_get_examples_traffic_light(self):
        result, text = self.client.call_tool("gx3gen_get_examples", {"kind": "traffic_light"})
        payload = _parse_json(text)
        self.assertFalse(result.get("isError"), payload)
        self.assertTrue(payload.get("ok"))
        example = payload["examples"]["traffic_light"]
        self.assertIsInstance(example.get("spec"), dict)
        self.assertTrue(example["spec"].get("segments"))
        self.assertIn("红绿灯", json.dumps(example, ensure_ascii=False))

    def test_16_get_examples_all(self):
        _result, text = self.client.call_tool("gx3gen_get_examples", {"kind": "all"})
        payload = _parse_json(text)
        self.assertIn("traffic_light", payload["examples"])
        self.assertIn("three_station", payload["examples"])

    def test_17_tool_list_excludes_inproject_planner(self):
        response = self.client.request("tools/list", {})
        names = {tool.get("name") for tool in (response.get("result") or {}).get("tools") or []}
        # agent-first：agent 自己是 LLM；MCP 不再提供会调用项目内 LLM 的 plan 工具
        self.assertNotIn("gx3gen_plan", names)
        self.assertIn("gx3gen_create_project", names)
        self.assertIn("gx3gen_save_spec", names)

    def test_17b_project_spec_handoff(self):
        traffic_spec = json.loads(
            (REPO_ROOT / "samples" / "traffic_light.spec.json").read_text(encoding="utf-8"))
        requirement = (REPO_ROOT / "samples" / "traffic_light.requirement.md").read_text(
            encoding="utf-8")
        create, create_text = self.client.call_tool("gx3gen_create_project", {
            "name": "MCP 交接项目",
            "requirement": requirement,
            "io": traffic_spec["io"],
        })
        payload = _parse_json(create_text)
        self.assertFalse(create.get("isError"), payload)
        self.assertTrue(payload.get("ok"), payload)
        project_id = payload["project_id"]
        self.assertTrue(project_id)
        self.assertTrue(payload.get("spec_path"))

        save, save_text = self.client.call_tool("gx3gen_save_spec", {
            "project_id": project_id,
            "requirement": requirement,
            "spec": traffic_spec,
        })
        saved = _parse_json(save_text)
        self.assertFalse(save.get("isError"), saved)
        self.assertTrue(saved.get("ok"), saved)
        self.assertTrue(saved.get("valid"), saved)
        self.assertTrue(saved.get("quality_ok"), saved)
        self.assertTrue(Path(saved["spec_path"]).exists())

        load, load_text = self.client.call_tool("gx3gen_load_spec", {"project_id": project_id})
        loaded = _parse_json(load_text)
        self.assertFalse(load.get("isError"), loaded)
        self.assertTrue(loaded.get("ok"), loaded)
        self.assertEqual(loaded["spec"]["project"]["name"], traffic_spec["project"]["name"])

        listed, list_text = self.client.call_tool("gx3gen_list_projects", {})
        projects = _parse_json(list_text)
        self.assertFalse(listed.get("isError"), projects)
        self.assertTrue(any(item.get("id") == project_id for item in projects.get("projects") or []))

    def test_17c_generation_context_has_no_api_key_requirement(self):
        _result, text = self.client.call_tool("gx3gen_get_generation_context", {
            "requirement": "双向红绿灯",
        })
        lowered = text.lower()
        self.assertNotIn("api_key", lowered)
        self.assertNotIn("gx3gen_plan", lowered)
        self.assertIn("programspec", lowered)

    def test_17d_audit_csv_missing_file_returns_error(self):
        result, text = self.client.call_tool("gx3gen_audit_csv", {
            "program_csv_path": str(Path(self.tmpdir) / "no-such-gx3gen-program.csv"),
        })
        payload = _parse_json(text)
        self.assertTrue(result.get("isError"), payload)
        self.assertFalse(payload.get("ok"), payload)

    def test_17e_generate_includes_audit_and_audit_tool_passes(self):
        spec = json.loads(
            (REPO_ROOT / "samples" / "traffic_light.spec.json").read_text(encoding="utf-8"))
        requirement = (REPO_ROOT / "samples" / "traffic_light.requirement.md").read_text(
            encoding="utf-8")
        out_dir = tempfile.mkdtemp(dir=self.tmpdir)
        result, text = self.client.call_tool("gx3gen_generate_program", {
            "spec": spec,
            "requirement": requirement,
            "out_dir": out_dir,
        })
        payload = _parse_json(text)
        self.assertFalse(result.get("isError"), payload)
        self.assertTrue(payload.get("ok"), payload)
        self.assertTrue((payload.get("audit") or {}).get("ok"), payload.get("audit"))
        audit_result, audit_text = self.client.call_tool("gx3gen_audit_csv", {
            "program_csv_path": payload["program_csv"],
            "comment_csv_path": payload["comment_csv"],
            "expect_comment_title": "Chinese Simplified/简体中文",
        })
        audit_payload = _parse_json(audit_text)
        self.assertFalse(audit_result.get("isError"), audit_payload)
        self.assertTrue(audit_payload.get("ok"), audit_payload)

    def test_17f_manual_list_empty_workspace(self):
        result, text = self.client.call_tool("gx3gen_manual_list", {})
        payload = _parse_json(text)
        self.assertFalse(result.get("isError"), payload)
        self.assertTrue(payload.get("ok"), payload)
        self.assertEqual(payload.get("manuals"), [])

    def test_18_list_styles(self):
        result, text = self.client.call_tool("gx3gen_list_styles", {})
        payload = _parse_json(text)
        self.assertFalse(result.get("isError"), payload)
        self.assertTrue(payload.get("ok"))
        self.assertIsInstance(payload.get("styles"), list)

    def test_19_feedback_on_missing_project_returns_error(self):
        result, text = self.client.call_tool("gx3gen_add_feedback", {
            "project_id": "p-does-not-exist",
            "category": "互锁",
            "severity": "error",
            "message": "缺少互锁",
        })
        payload = _parse_json(text)
        self.assertTrue(result.get("isError"))
        self.assertFalse(payload.get("ok"))
        self.assertIn("项目不存在", payload.get("error", ""))

    def test_20_import_style_missing_file_returns_error(self):
        result, text = self.client.call_tool("gx3gen_import_style", {
            "program_csv_path": str(Path(self.tmpdir) / "no-such.csv"),
        })
        payload = _parse_json(text)
        self.assertTrue(result.get("isError"))
        self.assertFalse(payload.get("ok"))

    def test_21_validate_spec_missing_spec_returns_error(self):
        result, text = self.client.call_tool("gx3gen_validate_spec", {})
        payload = _parse_json(text)
        self.assertTrue(result.get("isError"))
        self.assertFalse(payload.get("ok"))
        self.assertIn("spec", payload.get("error", "").lower())


class RunMCPWithoutInputTest(unittest.TestCase):
    """`python run_mcp.py` 在 stdin 立即 EOF 时不应崩溃/挂起。"""

    def test_no_input_exits_cleanly(self):
        tmpdir = tempfile.mkdtemp(prefix="gx3gen-mcp-eof-")
        try:
            env = dict(os.environ)
            env["GX3GEN_WORKSPACE"] = str(Path(tmpdir) / "workspace")
            env["PYTHONIOENCODING"] = "utf-8"
            completed = subprocess.run(
                [sys.executable, str(RUN_MCP)],
                cwd=str(REPO_ROOT),
                input="",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "", "stdout 只能有协议 JSON")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class AgentFirstAssetsTest(unittest.TestCase):
    """agent-first 交付物（文档/配置）存在且可用。"""

    def test_agents_md_exists_and_has_rules(self):
        path = REPO_ROOT / "AGENTS.md"
        self.assertTrue(path.exists(), "缺少仓库根 AGENTS.md")
        text = path.read_text(encoding="utf-8")
        for keyword in ("ProgramSpec", "不要让用户手写 JSON", "gx3gen_validate_spec",
                        "gx3gen_generate_program", "assumptions", "质量红线"):
            self.assertIn(keyword, text, "AGENTS.md 缺少关键内容：%s" % keyword)

    def test_skill_and_references_exist(self):
        skill = REPO_ROOT / "skills" / "gx3gen-plc" / "SKILL.md"
        self.assertTrue(skill.exists(), "缺少 skills/gx3gen-plc/SKILL.md")
        text = skill.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), "SKILL.md 缺少 YAML frontmatter")
        self.assertIn("name: gx3gen-plc", text)
        for name in ("program_spec.md", "quality_checklist.md"):
            reference = skill.parent / "references" / name
            self.assertTrue(reference.exists(), "缺少技能参考文件：%s" % reference)
            self.assertGreater(len(reference.read_text(encoding="utf-8")), 500)

    def test_agent_workflow_doc_exists(self):
        path = REPO_ROOT / "docs" / "AGENT_WORKFLOW.md"
        self.assertTrue(path.exists(), "缺少 docs/AGENT_WORKFLOW.md")
        text = path.read_text(encoding="utf-8")
        for keyword in ("红绿灯", "run_mcp.py", "gx3gen_generate_program", "mcpServers"):
            self.assertIn(keyword, text)

    def test_mcp_json_example_is_valid(self):
        path = REPO_ROOT / "mcp.json.example"
        self.assertTrue(path.exists(), "缺少 mcp.json.example")
        data = json.loads(path.read_text(encoding="utf-8"))
        server = (data.get("mcpServers") or {}).get("gx3gen") or {}
        self.assertTrue(server.get("command"))
        self.assertTrue(server.get("args"))
        self.assertTrue(str(server["args"][0]).endswith("run_mcp.py"))

    def test_run_mcp_launcher_exists(self):
        self.assertTrue(RUN_MCP.exists(), "缺少仓库根 run_mcp.py")
        self.assertIn("gx3gen.mcp_server", RUN_MCP.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
