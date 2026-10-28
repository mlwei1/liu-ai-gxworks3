# Agent 工作流与完整示例会话

本文件给「打开本仓库的 AI agent」提供可直接照做的端到端流程。前置阅读：
[AGENTS.md](../AGENTS.md)（铁律/工作流）、[skills/gx3gen-plc/SKILL.md](../skills/gx3gen-plc/SKILL.md)（技能路由）。

## 1. 前提

- Python **3.9+**，**零第三方依赖**；下文命令用 `python`（Windows 也可用 `py -3`）。
- 运行期数据默认在 `<repo>/workspace/`，可用环境变量 `GX3GEN_WORKSPACE` 重定向（测试/多项目隔离）。
- 仓库根启动器：`python run_mcp.py`（stdio MCP server；stdout 只输出协议 JSON，日志走 stderr）。

## 2. 配置 MCP 客户端

把 [`mcp.json.example`](../mcp.json.example) 复制到客户端配置文件，把占位符替换成本机绝对路径：

```json
{
  "mcpServers": {
    "gx3gen": {
      "command": "python",
      "args": ["<absolute-path-to-repo>/run_mcp.py"],
      "env": {
        "GX3GEN_WORKSPACE": "<absolute-path-to-repo>/workspace"
      }
    }
  }
}
```

| 客户端 | 配置位置 |
|---|---|
| Claude Desktop (Windows) | `%APPDATA%\\Claude\\claude_desktop_config.json` |
| Cursor | 项目 `.cursor/mcp.json` 或全局 Cursor Settings → MCP |
| DeepSeek Harness / 其他 | 支持 `mcpServers` 的 MCP 配置（command+args+env） |

验证：重启客户端后应看到 9 个 `gx3gen_*` 工具。手动冒烟（不依赖客户端）：

```powershell
cd <repo>
@(
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}',
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
) -join "`n" | python run_mcp.py
```

应看到 `serverInfo {"name":"gx3gen","version":"0.1.0"}` 与 9 个工具（stdout 里只有 JSON 行）。
`python run_mcp.py` 在 stdin 无输入（立即 EOF）时应正常退出（exit code 0），不崩溃、不挂起。

## 3. 完整示例会话：双向红绿灯

### 用户

> 做一个双向红绿灯（东西向、南北向）：启动后自动循环，东西绿→东西黄→全红→南北绿→南北黄→全红→循环；
> 绿灯 10s、黄灯 3s、全红 1s；任一方向绿灯时另一方向必须红灯；停止按钮复位所有灯。

### Agent 第 1 步：读生成上下文

```json
// tools/call
{"name": "gx3gen_get_generation_context",
 "arguments": {"requirement": "做一个双向红绿灯（东西向、南北向）：启动后自动循环，……"}}
```

返回（Markdown，约 6–10KB，关键内容）：

- ProgramSpec 0.1 骨架与硬约束；架构默认（初始化 `SM402→SET S0`、启动停止 M500/M501/M521、自动流程、手自动、报警）。
- 质量红线：红绿灯 ≥6 相位、每相位定时器、方向互锁、自动循环、停止复位。
- 双向红绿灯示例 spec（`samples/traffic_light.spec.json`，6 相位，S10→S20→…→S60→S10，T0–T5）。
- IO 表（若传了 `io_csv`）、风格摘要（若传 `style_id`）、历史 lessons（若传 `project_id`）。

也可以先要一版机器生成的起点：

```json
// tools/call gx3gen_plan
{"name": "gx3gen_plan",
 "arguments": {"requirement": "做一个双向红绿灯……", "name": "双向红绿灯"}}
```

返回 `{ok:true, spec:{...6 相位...}, planner:{engine:"plan_with_quality"}, quality:{ok:true,...}}`。
**注意**：起点 spec 只作参考，你仍要复核相位/时长/互锁/IO，并按现场信息修 `assumptions`。

### Agent 第 2 步：你产出 ProgramSpec（不要让用户写）

按上下文/示例写完整 spec，关键片段：

```json
{
  "spec_version": "0.1",
  "project": {"name": "双向红绿灯", "cpu": "FX5U", "gx_version": "1.082", "style_profile": null},
  "io": [
    {"device": "X0", "comment": "启动按钮", "role": "input"},
    {"device": "X1", "comment": "停止按钮", "role": "input"},
    {"device": "Y0", "comment": "东西红灯", "role": "output"},
    {"device": "Y1", "comment": "东西黄灯", "role": "output"},
    {"device": "Y2", "comment": "东西绿灯", "role": "output"},
    {"device": "Y3", "comment": "南北红灯", "role": "output"},
    {"device": "Y4", "comment": "南北黄灯", "role": "output"},
    {"device": "Y5", "comment": "南北绿灯", "role": "output"}
  ],
  "segments": [
    {"title": "初始化", "kind": "init",
     "rungs": [{"comment": "上电置待机步",
                "instructions": [{"op": "LD", "operands": ["SM402"]},
                                 {"op": "SET", "operands": ["S0"]}]}]},
    {"title": "红绿灯循环", "kind": "station", "step_range": [10, 69],
     "entry_conditions": ["S0", "M500"],
     "steps": [
       {"step": 10, "actions": [{"set": ["Y2", "Y3"], "reset": ["Y0", "Y1", "Y4", "Y5"]},
                                {"instructions": [{"op": "OUT", "operands": ["T0", "K100"]}]}],
        "transition": {"conditions": ["T0"], "next": 20}},
       {"step": 20, "actions": [{"set": ["Y1"], "reset": ["Y2"]},
                                {"instructions": [{"op": "OUT", "operands": ["T1", "K30"]}]}],
        "transition": {"conditions": ["T1"], "next": 30}},
       {"step": 30, "actions": [{"set": ["Y0", "Y3"], "reset": ["Y1", "Y4"]},
                                {"instructions": [{"op": "OUT", "operands": ["T2", "K10"]}]}],
        "transition": {"conditions": ["T2"], "next": 40}},
       {"step": 40, "actions": [{"set": ["Y0", "Y5"], "reset": ["Y2", "Y3", "Y4"]},
                                {"instructions": [{"op": "OUT", "operands": ["T3", "K100"]}]}],
        "transition": {"conditions": ["T3"], "next": 50}},
       {"step": 50, "actions": [{"set": ["Y0", "Y4"], "reset": ["Y5"]},
                                {"instructions": [{"op": "OUT", "operands": ["T4", "K30"]}]}],
        "transition": {"conditions": ["T4"], "next": 60}},
       {"step": 60, "actions": [{"set": ["Y0", "Y3"], "reset": ["Y4"]},
                                {"instructions": [{"op": "OUT", "operands": ["T5", "K10"]}]}],
        "transition": {"conditions": ["T5", "M500"], "next": 10}}
     ]},
    {"title": "启动停止", "kind": "common",
     "rungs": [
       {"comment": "启动 → 运行中 + 自动",
        "instructions": [{"op": "LD", "operands": ["X0"]}, {"op": "SET", "operands": ["M500"]},
                         {"op": "SET", "operands": ["M521"]}]},
       {"comment": "停止 → 复位运行/自动与所有灯",
        "instructions": [{"op": "LD", "operands": ["X1"]}, {"op": "RST", "operands": ["M500"]},
                         {"op": "RST", "operands": ["M521"]},
                         {"op": "RST", "operands": ["Y0", "Y1", "Y2", "Y3", "Y4", "Y5"]}]}
     ]}
  ],
  "alarms": {"enabled": false, "base": 2000, "timer_base": 10, "cylinders": []},
  "assumptions": ["绿灯 10s / 黄灯 3s / 全红 1s 为按需求设定；现场如有交规要求请调整",
                  "停止时所有灯复位为安全状态（全灭），如需“全红等待”请修改停止段"]
}
```

### Agent 第 3 步：校验

```json
// tools/call
{"name": "gx3gen_validate_spec", "arguments": {"spec": {...}, "requirement": "做一个双向红绿灯……"}}
```

期望返回：

```json
{"ok": true,
 "errors": [],
 "warnings": ["IO 表为空：设备号只能来自需求文本，建议补充 IO 表"],  // 视情况
 "quality": {"available": true, "ok": true, "score": null,
             "errors": [], "warnings": [], "suggestions": [],
             "stats": {"segments": 3, "station_steps": 6, "instructions": 16,
                       "has_cycle": true, "has_timer": true, "has_start_stop": true,
                       "lamps": 6, "outputs": 6}},
 "quality_ok": true}
```

`ok=false` 或 `quality.ok=false` 时按 `errors` 逐条修改后重试；**不要跳过校验直接生成**。

### Agent 第 4 步：生成

```json
// tools/call
{"name": "gx3gen_generate_program",
 "arguments": {"spec": {...}, "requirement": "做一个双向红绿灯……", "name": "双向红绿灯"}}
```

期望返回（路径在 workspace/out 下，示例）：

```json
{"ok": true,
 "summary": "生成 3 段 / 23 网络 / 87 指令 / 约 256 步；轻量检查：…；质量检查：通过（…）",
 "out_dir": ".../workspace/out/双向红绿灯-20261028-…",
 "program_csv": ".../out/双向红绿灯-…/program.csv",
 "comment_csv": ".../out/双向红绿灯-…/comments.csv",
 "network_table": ".../out/双向红绿灯-…/network_table.md",
 "report": ".../out/双向红绿灯-…/report.md",
 "stats": {"rows": ..., "instructions": 87, "segments": 3, "rungs": 23, "steps": 256},
 "segments": [{"title": "初始化", "kind": "init", "rungs": 1},
              {"title": "红绿灯循环", "kind": "station", "rungs": 12, "step_range": [10, 69]},
              {"title": "启动停止", "kind": "common", "rungs": 2}],
 "quality": {"ok": true, ...},
 "quality_ok": true,
 "lint": [...],
 "assumptions": [...]}
```

- 质量红线不过时：`{"ok": false, "error": "质量红线未通过，未生成文件。…", "errors": [...], "quality": {...}}`（不写文件）。
- 仅当用户明确接受风险时才传 `"force": true`，并在回复中说明。
- 失败不要猜“是不是成功了”——看 `ok` 与 `errors`。

### Agent 第 5 步：回复用户（模板）

```markdown
已生成（GX Works3 程序初稿，3 段 / 6 相位 / 约 256 步）：
- 程序本体：workspace/out/双向红绿灯-…/program.csv
- 软元件注释：workspace/out/双向红绿灯-…/comments.csv
- 网络表 / 生成报告：…/network_table.md、…/report.md
- 质量：quality 通过（6 相位、6 个定时器、互锁与自动循环齐全）；lint 告警 N 条：...
- 待现场确认（assumptions）：
  1. 绿灯 10s / 黄灯 3s / 全红 1s 为按需求设定；
  2. 停止时所有灯复位为安全状态；
GX Works3 落地：打开基座工程副本 → MAIN 程序内 [编辑]→[导入文件] 选 program.csv →
导入软元件注释 → 全部转换（Shift+Alt+F4）→ 修编译错误/补逻辑 → 保存（先备份工程）。
```

## 4. 变体 A：工位序列（带 IO 表）

1. 用户给中文流程 + IO 表 CSV；
2. `gx3gen_get_generation_context({requirement, io_csv})` 解析 IO 并给上下文；
3. 写 spec：初始化 → 工位段（动作 + 到位条件 + 延时 + 回位/交接）→ 启动停止 → 报警；
4. `gx3gen_validate_spec` → `gx3gen_generate_program`；
5. 反馈现场问题 → `gx3gen_add_feedback({project_id, category:"互锁", severity:"error", message:"...", expected:"..."})`，
   下一轮 lessons 自动进入上下文。

参考 `samples/three_station.*`（需求/IO/spec）与 `tests/test_api.py` 的 INLINE_SPEC。

## 5. 变体 B：学习用户风格

1. 用户提供 GX Works3 导出的「程序本体 CSV」（必需）+「软元件注释 CSV」（可选）；
2. `gx3gen_import_style({program_csv_path, comment_csv_path, name:"我的风格"})` → 返回 `style.id`；
3. 后续 `gx3gen_get_generation_context({requirement, style_id})` 与
   `gx3gen_generate_program({spec, style_id?})`（或 spec.project.style_profile）自动套用风格：
   段顺序、步号策略、模式位、报警基址、命名/注释习惯。

## 6. Agent 交付前自检（固定）

- [ ] 先读过 `gx3gen_get_generation_context`（不是凭记忆写 spec）
- [ ] `gx3gen_validate_spec` → `ok=true` 且 `quality.ok=true`
- [ ] `gx3gen_generate_program` → `ok=true`，4 个产物路径真实存在
- [ ] `lint` 的 error 全部消除；warning 逐条向用户说明
- [ ] 回复里包含：产物路径、段结构/步号、quality/lint、assumptions、GX 落地步骤
- [ ] 没有声称“GX Works3 编译/仿真通过”
- [ ] 没有把用户代码/需求上传到网络

## 7. 排查

| 问题 | 处理 |
|---|---|
| 客户端看不到工具 | 检查 `command`/`args` 是否为绝对路径；`python run_mcp.py` 手动能否启动 |
| 工具全部报 “未知工具” | 客户端连的是旧配置/别的 server；重启客户端 |
| `python run_mcp.py` 立即退出 | 用 `python -c "import sys; print(sys.version)"` 确认 ≥3.9；看 stderr 的导入错误 |
| stdout 出现非 JSON | 只可能来自工具里的 stray print（server 已把工具 stdout 重定向到 stderr 兜底）；报 bug |
| workspace 不可写 | 设 `GX3GEN_WORKSPACE` 到可写目录；默认 `<repo>/workspace` |
| 质量总是不过 | 按 `quality.errors` 补相位/定时器/启停/动作；仍不确定就问用户，不要 `force=true` 糊弄 |
