# AGENTS.md — 给打开本仓库的 AI agent 的操作手册

> 本文件面向**读取本仓库的 AI agent**（Claude Desktop / DeepSeek Harness / Cursor / Codex 等）。
> 人类用户请先看 [README.md](README.md)。技能包入口：[skills/gx3gen-plc/SKILL.md](skills/gx3gen-plc/SKILL.md)；
> 完整示例会话：[docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md)。

## 0. 这是什么

gx3gen 是面向**三菱 FX5U + GX Works3 1.082** 的 PLC 程序初稿生成器：
把「中文需求 + IO 表 + 用户历史程序风格」变成一版**可读、可改、可在 GX Works3 导入**的
设备段 + SFC(STL) 梯形图程序（程序本体 CSV + 软元件注释 CSV + 网络表 + 生成报告）。

工作方式是**契约式**的：

```
你（AI agent）产出 ProgramSpec(JSON) → 确定性生成器产出 GX 程序 CSV
```

- 你负责：理解需求 → 自己写 ProgramSpec → 校验/生成 → 向用户交付并说明假设。
- 生成器负责：ProgramSpec → 段/步/指令 → 严格格式的 GX CSV（不让模型直接写 CSV/二进制）。
- **不做**：GX Works3 自动编译/仿真/自动验收；标签/FB/ST/FBD/SFC 图；多机型。
  生成的只是**初稿**，编译错误与细节逻辑由工程师在 GX Works3 接手。

## 1. 铁律（每次任务都必须遵守）

1. **ProgramSpec 必须由你（agent）产出**。用户只给中文需求、IO 表、风格 CSV；不要让用户手写 JSON。
2. 动手前先调用 `gx3gen_get_generation_context`（或读本文件 + `docs/`），**不要凭记忆猜字段**。
3. **必须先 `gx3gen_validate_spec` 通过，再 `gx3gen_generate_program`**。禁止跳过校验直接生成。
4. 生成成功后必须向用户报告：**产物路径、段结构/步号、质量与 lint 结果、assumptions、GX Works3 落地步骤**。
5. `quality.ok=false` 时：先按 `quality.errors` 迭代修改 spec；信息不足时**直接问用户**（工艺/设备/延时/互锁），
   不要硬凑、不要删检查项、不要用 1–2 个空步蒙混。
6. 设备号只能用 IO 表或需求原文里出现的合法软元件；不确定的写进 `assumptions`，**绝不发明设备**。
7. **禁止**把 CSV 当成“最终交付”，或声称“已在 GX Works3 编译/仿真通过”。CSV 是待导入初稿，
   导入、全部转换、修错、验证都由工程师在 GX Works3 完成。
8. 不联网上传用户程序/需求；不修改用户 `workspace/` 里的历史项目、风格库与已生成产物。

## 2. 接入（MCP，推荐）

用户视角的完整流程：

1. **用户下载/克隆本仓库**（Python 3.9+，零第三方依赖）。
2. **AI agent 打开仓库**，读 `AGENTS.md`（本文件）与 `skills/gx3gen-plc/SKILL.md`。
3. **配置 AI 客户端连上 MCP server**：把 `mcp.json.example` 复制到客户端配置并改成绝对路径，
   命令是 `python <repo>/run_mcp.py`（stdio MCP，零依赖；stdout 只输出协议 JSON）。
4. **agent 开始生成程序**：走第 3 节工作流，用户只需要用中文描述需求、提供 IO 表。

没有 MCP 能力时，退化为 CLI/Python API：

```powershell
# 在仓库根目录执行；Windows 下也可用 py -3
python run.py plan --req samples/three_station.requirement.md --io samples/three_station.io.csv --out out/spec.json
python run.py gen --spec out/spec.json --out ./out-demo
python run.py style import --program-csv "程序本体.csv" --comment-csv "全局软元件注释.csv" --name "我的风格"
```

## 3. 标准工作流（每次生成程序都走一遍）

**Step 0 收集需求**：工位/动作顺序、互锁、延时、启动/停止/急停、手自动、报警、IO 表与软元件注释。
缺 IO 表就先问用户；用户暂时没有 IO 表时，只用需求里明确的设备号，并在 assumptions 标明。

**Step 1 读上下文**

```json
gx3gen_get_generation_context({"requirement": "<用户需求原文>", "io_csv": "<IO 表 CSV 文本>", "style_id": "<可选>"})
```

返回内容含：ProgramSpec 必需字段与骨架、架构默认（初始化/启动停止/自动流程/手自动/报警）、
质量红线、红绿灯与三工位示例、IO 表、风格摘要、历史 lessons。

**Step 2 你写 ProgramSpec**（不要让用户写）。要点：
`spec_version:"0.1"`、`project.cpu:"FX5U"`、至少 1 个 `init` 段（`SM402 → SET S0`）+ 1 个 `station` 段
（每步 `actions` + `transition`）+ 启动停止 `common` 段；步号 100 起、步内增量 10；延时用 `OUT Tn Kn`
（K = 100ms）；不确定项写 `assumptions`。

**Step 3 校验**

```json
gx3gen_validate_spec({"spec": <你的 spec>, "requirement": "<需求原文>"})
```

`ok` 表示结构合法；`quality.ok` 表示质量红线是否通过。`errors` 逐条修，直到 `ok=true` 且 `quality.ok=true`。

**Step 4 保存项目（可选，推荐）**

如果用户希望项目可回看/可在 Web 软件里审阅，先用 `gx3gen_create_project({name, requirement, io})` 建项目，
再 `gx3gen_save_spec({project_id, spec, requirement})` 写入 workspace（同时生成 `spec.json`）。
之后可用 `gx3gen_load_spec` / `gx3gen_list_projects` 继续工作；Web 软件读取同一份项目与 spec。

**Step 5 生成**

```json
gx3gen_generate_program({"spec": <spec>, "requirement": "<需求原文>", "name": "<项目名>"})
```

成功返回 `program_csv` / `comment_csv` / `network_table` / `report` 路径 + `summary/stats/segments/lint/quality/assumptions`。
质量红线不过会返回 `ok=false + errors`（不写文件）；除非用户明确接受风险，才传 `force=true`。

**Step 6 交付报告**（模板见第 7 节）。

**Step 7（可选）反馈闭环**：用户把 GX 里的错误/期望告诉你后，用
`gx3gen_add_feedback({project_id, category, severity, message, expected})` 记录；
下一轮 `get_generation_context` 会自动带上 lessons。风格学习用 `gx3gen_import_style`。

> **你（agent）就是 LLM。** 项目不调用 LLM、不需要 API Key；不要向用户索要 Key。
> 规则/领域模板只是兜底参考；ProgramSpec 的最终质量责任在你：复核设备、补齐 assumptions、按质量红线修正。

## 4. 质量红线（`quality.ok=false` = 不要交付）

通用红线：

1. 结构完整：≥1 个 init 段（SM402→SET S0）+ ≥1 个 station 段（≥3 个有效步）+ 启动/停止段。
2. 每步必须有动作或转移条件；禁止只有 1–2 个空 STL 步/空动作。
3. 每个输出 Y 至少被 SET/RST/OUT 一次；延时定时器必须出现在转移条件里。
4. 最后一步 `transition.next=null`（回待机）或指回首步（自动循环）。
5. 停止/急停必须复位运行模式位（M500/M521）；报警要有清除路径。
6. 步号段不重叠、定时器/报警位地址不冲突、X/Y 注释覆盖完整。
7. 所有猜测（延时值、按钮、报警位、互锁）写进 `assumptions` 并在回复里告诉用户。

双向红绿灯专属红线：

1. 相位 ≥6：东西绿 → 东西黄 → 全红 → 南北绿 → 南北黄 → 全红（回到相位 1）。
2. 绿/黄/全红都受定时器控制（T0 起，K 按 100ms 基准）；建议绿 8–10s、黄 3s、全红 1s。
3. 互锁：任一方向绿灯时另一方向必须红灯；禁止同方向「绿+黄」同时亮。
4. 自动循环：最后一步 `next` 指回首步；启动后自动循环。
5. 启动/停止映射到模式位（M500/M501/M521），停止后所有灯回到安全状态。

完整清单见 [skills/gx3gen-plc/references/quality_checklist.md](skills/gx3gen-plc/references/quality_checklist.md)。

## 5. MCP 工具速查

| 工具 | 用途 | 关键返回 |
|---|---|---|
| `gx3gen_get_generation_context` | **必读**生成上下文 | Markdown：ProgramSpec/架构/质量红线/示例/IO/风格/lessons |
| `gx3gen_validate_spec` | 校验 spec + 质量检查 | `{ok, errors, warnings, suggestions, quality}` |
| `gx3gen_generate_program` | 生成 4 个产物 | `{ok, program_csv, comment_csv, network_table, report, summary, stats, segments, lint, quality, assumptions}` |
| `gx3gen_create_project` | 建 agent 项目（可选需求/IO） | `{project_id, project_path, spec_path}` |
| `gx3gen_save_spec` | 保存 ProgramSpec 到项目（写 `spec.json`） | `{ok, spec_path, valid, quality, errors}` |
| `gx3gen_load_spec` | 读回项目里的 ProgramSpec | `{ok, spec, requirement, io}` |
| `gx3gen_list_projects` | 列出 workspace 项目 | `{projects:[...]}` |
| `gx3gen_get_examples` | 红绿灯/三工位示例 | `{examples:{traffic_light, three_station}}` |
| `gx3gen_list_styles` | 列出风格画像 | `{styles:[...]}` |
| `gx3gen_import_style` | GX CSV → 风格画像 | `{style:{id,...}}` |
| `gx3gen_add_feedback` | 记录纠错反馈 | `{record:{...}}` |
| `gx3gen_get_lessons` | 读取项目 lessons | `{lessons:[...]}` |

所有工具返回 `{content:[{type:"text", text}], isError}`；除 context 是 Markdown 外，text 内都是 JSON。
工具内部错误（缺参数/未知工具/质量不过）返回 `ok=false + error/errors`，不会让 server 崩溃。

## 6. 常见错误与处理

| 现象 | 处理 |
|---|---|
| `quality.ok=false` / `QualityError` | 按 `quality.errors` 补相位、定时器、启停、动作；不要删除检查项 |
| `SpecError: xxx 必须是 ...` | 按错误路径改 spec：cpu 必须 `FX5U`、设备号合法、step 同段递增、segments 非空 |
| `gx3gen_create_project` / `save_spec` 报项目不存在 | 先 `create_project` 拿 `project_id`，再保存；项目与 Web 共用 `<repo>/workspace` |
| `未知工具 / Method not found` | 检查 MCP 客户端是否连到本项目 `run_mcp.py`；工具名以第 5 节为准 |
| 产物路径不存在 | 检查 `out_dir` / `GX3GEN_WORKSPACE` 写权限；默认落在 `<repo>/workspace/out/` |
| 用户要求“保证能编译” | 说明初稿边界：CSV 需在 GX Works3 导入→全部转换→修错，编译验证由工程师完成 |

## 7. 回复用户的模板

```markdown
已生成（GX Works3 程序初稿）：
- 程序本体：<program_csv>
- 软元件注释：<comment_csv>
- 网络表 / 生成报告：<network_table>、<report>
- 结构：<N> 段 / <M> 步 / <K> 条指令
- 质量：quality <通过/未通过>，lint <告警数>；未通过项：...
- 待现场确认（assumptions）：
  1. ...
GX Works3 落地：打开基座工程副本 → MAIN 程序内 [编辑]→[导入文件] 选程序本体 CSV →
导入软元件注释 → 全部转换（Shift+Alt+F4）→ 修编译错误/补逻辑 → 保存（建议先备份工程）。
```

## 8. 相关文件

- [README.md](README.md)：产品定位、快速开始、当前边界
- [skills/gx3gen-plc/SKILL.md](skills/gx3gen-plc/SKILL.md)：技能包（触发词/路由/工作流/质量/常见错误）
- [skills/gx3gen-plc/references/program_spec.md](skills/gx3gen-plc/references/program_spec.md)：ProgramSpec 字段说明
- [skills/gx3gen-plc/references/quality_checklist.md](skills/gx3gen-plc/references/quality_checklist.md)：质量检查清单
- [docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md)：完整示例会话 + MCP 配置
- [docs/INTERFACES.md](docs/INTERFACES.md)、[docs/CSV_FORMAT.md](docs/CSV_FORMAT.md)：模块接口与 GX CSV 格式
- `mcp.json.example`：MCP 客户端配置模板
