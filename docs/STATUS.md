# 实现状态（2026-10-28）

## 最新修正：agent 是唯一 LLM（2026-10-28 下午）

产品定位修正：**用 AI agent 打开项目时，agent 自带模型；项目不调用 LLM、不需要 API Key。**
已完成：

- 从 MCP 工具集中**移除** `gx3gen_plan`（避免"双大脑"与项目内 LLM 依赖）；MCP 不读 LLM 配置、不联网。
- 新增项目/文件交接工具：`gx3gen_create_project`、`gx3gen_save_spec`、`gx3gen_load_spec`、`gx3gen_list_projects`，
  约定 `workspace/projects/<id>/spec.json`：agent 写入，Web 软件只读/审阅。
- Web 界面重新定位为**审阅/可视化/反馈/风格/产物浏览**；「本地 LLM」降级为可选独立模式，设置项默认折叠。
- README（中/英）、AGENTS.md、技能包、架构/接口文档统一改为"**不需要 API Key**"的主流程；
  `planner/llm.py` 明确标记为可选 standalone 适配器（非 agent 模式/CI）。
- 新增 MCP 测试：项目/spec 文件交接、工具集不含项目内规划器、上下文不要求 API Key；全仓 **147 tests OK**。

## 针对"规则生成太弱/没有自动流程"的修复

上一版只有规则解析，用户输入"双向红绿灯"（未配风格、未配 LLM）时只得到 1 个空 STL 步。
本轮修复：

| 问题 | 修复 |
|---|---|
| 没有 AI agent 接入 | ① AI Agent + MCP 方式（agent 自己就是 LLM）；② 规则/领域模板兜底；③ 可选 standalone LLM（仅独立 Web/CI） |
| 规则解析在自然语言上退化 | 新增领域模板 `domains.py`（双向红绿灯：6 相位/6 定时器/循环/启停/手自动）；无法解析的自然语言**明确报错并给改写建议**，不再输出垃圾初稿 |
| 生成结果没有质量门槛 | 新增 `quality.py`：结构错误（空步、无条件转移等）+ 需求红线（循环/定时/启停/灯位/相位）不通过则不生成；Web/API/MCP 均展示 quality 详情 |
| 产品形态不是 agent-first | 新增 MCP stdio server（12 工具）、[AGENTS.md](../AGENTS.md)、技能包 `skills/gx3gen-plc/`、`docs/AGENT_WORKFLOW.md`、`mcp.json.example` |

## 已实现（可运行）

| 模块 | 文件 | 状态 |
|---|---|---|
| AI Agent 接入 | `run_mcp.py`、`src/gx3gen/mcp_server.py`、`AGENTS.md`、`skills/gx3gen-plc/` | ✅ MCP 12 工具：generation_context / validate / generate / create_project / save_spec / load_spec / list_projects / examples / styles / import_style / feedback / lessons（**无项目内 LLM/规划工具**） |
| 领域模板 | `src/gx3gen/domains.py` | ✅ 双向红绿灯（25/3/2/20/3/2 默认时长；灯位按注释匹配，缺失自动分配 Y0–Y5） |
| 质量门槛 | `src/gx3gen/quality.py` | ✅ 结构检查 + 需求红线；`generator.generate(requirement=...)` 硬 gate；`force` 可强制 |
| 本地 Web 软件 | `src/gx3gen/app/`、`web/` | ✅ 审阅/可视化/反馈/风格/产物浏览；「本地 LLM」为可选独立模式（agent 模式不需要） |
| ProgramSpec 契约 | `src/gx3gen/spec.py`、`spec/program_spec.schema.json` | ✅ 校验、IO 表解析、设备查找 |
| 生成器 | `src/gx3gen/generator/` | ✅ spec → 段/STL → IL → CSV/注释/网络表/报告/轻量检查 |
| 程序本体 CSV | `src/gx3gen/ilcsv/` | ✅ UTF-16LE+BOM/TAB/QUOTE_ALL/CRLF/7 列/END；读/写/解析 |
| 风格学习 | `src/gx3gen/style/` | ✅ GX CSV → StyleProfile；真实语料 6 组验证 |
| 规划器 | `src/gx3gen/planner/` | ✅ 规则解析 / 领域模板；`planner/llm.py` 为**可选 standalone 适配器**（agent 模式不使用） |
| 反馈闭环 | `src/gx3gen/feedback/` | ✅ 反馈记录 → lessons/checklist/prompt_hint → 重新规划 |
| CLI | `src/gx3gen/cli.py`、`run.py` | ✅ serve/gen/plan/lint/style/project/feedback/optimize/demo/seed-demo |

## 已独立验证

- `python -m unittest discover -s tests -t .` → **147 tests OK**（含 30 个 MCP 协议/工具测试、8 个领域/质量测试）。
- **双向红绿灯（规则/领域模板，无 agent、无任何模型配置）**：
  输入 `samples/traffic_light.requirement.md` + IO 表 →
  `3 段 / 23 网络 / 87 指令 / 约 256 步`，含 6 个 STL 相位、6 个定时器（K250/K30/K20/K200/K30/K20）、
  启动停止段、末步回到 S10 的自动循环；`quality.ok=true`；程序本体 CSV 为 UTF-16LE+BOM、
  含 `[Title]初始化/启动停止/自动流程`、`STL`/`RETSTL`/`END`。
- **质量门槛**：1 个空步的 spec 被 QualityError 拦截；需求要求循环但 spec 无回环 → 规划/生成失败并给原因。
- **MCP E2E**：initialize → tools/list（12 工具，无 `gx3gen_plan`）→ get_examples → create/save/load spec → validate → generate 全通；
  `python run_mcp.py` 无输入退出 0、stdout 为空；MCP 不读 API Key、不联网。
- **API E2E**：红绿灯项目 `/api/plan` → `/api/generate` 200、quality.ok；无 LLM 的自然语言需求
  `/api/plan` 返回 400 并给出改写建议；坏 spec `/api/generate` 返回 400 + quality.errors。
- **真实语料风格学习**（只读）：12/13/FK/20G/S03/ProgPou → 画像 7–16KB，
  12 号学到 M500/M501/M521/M522、reset M510、报警 M1600、握手模板、步号 S/100/10。

## 尚未验证（下一步）

1. **生成 CSV 在真实 GX Works3 的导入 + 全部转换**（最关键；GX 1.082 + FX5U 基座工程人工走一次）。
2. 生成程序的**逻辑正确性**（本期定位是"初稿"，工程师在 GX 接手；尚无仿真/自动验收）。
3. 真实题目（阀体线/红绿灯）的工程师人工打分与反馈入库。
4. 注解行（CSV 第 7 列）在 GX 导入后的保留行为（未回读验证）。
5. 标签/FB/ST/FBD/SFC、iQ-R、GX 1.1xx —— 均不在本期范围。

## 建议的真机验收步骤

1. GX Works3 1.082 新建 FX5U-32MR/ES 空工程（或复制你现有的 FX5U 工程作为基座），建好 MAIN 程序。
2. 打开 MAIN 梯形图编辑器，光标置于程序内（不要停在 END 行）。
3. `[编辑] → [导入文件]` 选 `workspace/projects/<id>/out/program.csv`。
4. 软元件注释窗口导入 `comments.csv`。
5. `全部转换`（Shift+Alt+F4），记录 Error/Warning 数、网络数、步数，与生成报告对照。
6. 保存后冷重开，再次导出 CSV，回读比对指令序列/注释。
7. 把结果（错误截图/说明）填回软件"审阅/反馈"或 agent 的 `gx3gen_add_feedback`，作为下一轮学习样本。
