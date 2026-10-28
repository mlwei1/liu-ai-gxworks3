# 实现状态（2026-10-28）

## 最新修正：真实 GX Works3 导入排障回归（2026-10-28 晚）

真实项目（压装测试台，FX5U）三次导入失败后定位并修复 4 个独立根因；最终版本
`program.csv` + `comments.csv` 在 GX Works3 1.082 **导入成功**。完整复盘见
[docs/GX_IMPORT_TROUBLESHOOTING.md](GX_IMPORT_TROUBLESHOOTING.md)。

| 根因 | 修复 |
|---|---|
| `SET`/`RST` 多操作数 →「指令不正确」 | `spec.py` 新增 `OP_OPERAND_LIMITS` + `_check_operand_count`；`init`/`common` 段补上指令校验（`_check_rungs`） |
| rung 内 `LDI` 承接 `LD` →「梯形图配置不正确」 | `spec.py` 新增 `_check_il_sequence`；禁止 station 步动作以 LD/LDI 开头 |
| 6 个 STL 只配 1 个 RETSTL | `generator/build.py` 改为每步块一个 RETSTL |
| 注释 CSV CRLF + 标题错误 →「注释标题不存在」 | `ilcsv/rows.py` 注释 CSV 改 LF；默认标题 `Chinese Simplified/简体中文`；`project.comment_title(s)` 可配置；多列读取容错 |

新增：
- `src/gx3gen/audit.py` + `tools/gx_audit.py` + `python run.py audit`：导入前自检（格式/操作数/IL/STL/注释）；
- 生成器自动跑 audit，结果写入生成结果 `audit` 与 `report.md`；MCP 新增 `gx3gen_audit_csv`；
- 回归测试覆盖上述规则；全仓 **162 tests OK**。

## 修正：纯对话优先（2026-10-28 晚）

产品主流程收敛为**用户与 AI agent 纯对话**，Web 界面不再是需要打开的入口：

- 新增 [docs/CHAT_WORKFLOW.md](CHAT_WORKFLOW.md)：生成 / 导入自己程序学风格 / 纠正 / GX 落地 的完整对话流程；
- README（中/英）快速开始改为 pure-chat；Web 界面降级为"实验性（非主流程）"，从 Quick Start 移到附录；
- AGENTS.md 明确：**不要要求用户打开 Web 界面、不要索要 API Key**；项目可用 `create_project`/`save_spec` 持久化；
- Web 首页加"实验性/可选审阅界面"提示，并补 `web/README.md` 说明；
- 架构/接口/AGENT_WORKFLOW 文档同步标注 app/ 层为实验性可选；
- 代码生成/质量/测试逻辑未改，仍为 **162 tests OK**。

## 修正：agent 是唯一 LLM（2026-10-28 下午）

产品定位修正：**用 AI agent 打开项目时，agent 自带模型；项目不调用 LLM、不需要 API Key。**
已完成：

- 从 MCP 工具集中**移除** `gx3gen_plan`（避免"双大脑"与项目内 LLM 依赖）；MCP 不读 LLM 配置、不联网。
- 新增项目/文件交接工具：`gx3gen_create_project`、`gx3gen_save_spec`、`gx3gen_load_spec`、`gx3gen_list_projects`，
  约定 `workspace/projects/<id>/spec.json`：agent 写入，Web 软件只读/审阅。
- Web 界面重新定位为**审阅/可视化/反馈/风格/产物浏览**；「本地 LLM」降级为可选独立模式，设置项默认折叠。
- README（中/英）、AGENTS.md、技能包、架构/接口文档统一改为"**不需要 API Key**"的主流程；
  `planner/llm.py` 明确标记为可选 standalone 适配器（非 agent 模式/CI）。
- 新增 MCP 测试：项目/spec 文件交接、工具集不含项目内规划器、上下文不要求 API Key；全仓 **162 tests OK**。

## 针对"规则生成太弱/没有自动流程"的修复

上一版只有规则解析，用户输入"双向红绿灯"（未配风格、未配 LLM）时只得到 1 个空 STL 步。
本轮修复：

| 问题 | 修复 |
|---|---|
| 没有 AI agent 接入 | ① AI Agent + MCP 方式（agent 自己就是 LLM）；② 规则/领域模板兜底；③ 可选 standalone LLM（仅独立 Web/CI） |
| 规则解析在自然语言上退化 | 新增领域模板 `domains.py`（双向红绿灯：6 相位/6 定时器/循环/启停/手自动）；无法解析的自然语言**明确报错并给改写建议**，不再输出垃圾初稿 |
| 生成结果没有质量门槛 | 新增 `quality.py`：结构错误（空步、无条件转移等）+ 需求红线（循环/定时/启停/灯位/相位）不通过则不生成；Web/API/MCP 均展示 quality 详情 |
| 产品形态不是 agent-first | 新增 MCP stdio server（13 工具，含 audit）、[AGENTS.md](../AGENTS.md)、技能包 `skills/gx3gen-plc/`、`docs/AGENT_WORKFLOW.md`、`mcp.json.example` |

## 已实现（可运行）

| 模块 | 文件 | 状态 |
|---|---|---|
| AI Agent 接入 | `run_mcp.py`、`src/gx3gen/mcp_server.py`、`AGENTS.md`、`skills/gx3gen-plc/` | ✅ MCP 13 工具：generation_context / validate / generate / audit_csv / create_project / save_spec / load_spec / list_projects / examples / styles / import_style / feedback / lessons（**无项目内 LLM/规划工具**） |
| 领域模板 | `src/gx3gen/domains.py` | ✅ 双向红绿灯（25/3/2/20/3/2 默认时长；灯位按注释匹配，缺失自动分配 Y0–Y5） |
| 质量门槛 | `src/gx3gen/quality.py` | ✅ 结构检查 + 需求红线；`generator.generate(requirement=...)` 硬 gate；`force` 可强制 |
| GX 导入自检 | `src/gx3gen/audit.py`、`tools/gx_audit.py` | ✅ 格式/操作数/IL/STL/注释标题；生成器自动调用，MCP/CLI 可复核 |
| 实验性 Web 审阅界面 | `src/gx3gen/app/`、`web/` | ⚗️ 非主流程：审阅/可视化/反馈/产物浏览；「本地 LLM」为可选独立模式（agent 模式不需要） |
| ProgramSpec 契约 | `src/gx3gen/spec.py`、`spec/program_spec.schema.json` | ✅ 校验（含 SET/RST 操作数、LD/LDI 序列、comment_title）、IO 表解析、设备查找 |
| 生成器 | `src/gx3gen/generator/` | ✅ spec → 段/STL → IL → CSV/注释/网络表/报告/轻量检查/audit |
| 程序本体 CSV | `src/gx3gen/ilcsv/` | ✅ 程序 CSV：UTF-16LE+BOM/TAB/QUOTE_ALL/CRLF/7 列/END；注释 CSV：LF + 可配置标题 |
| 风格学习 | `src/gx3gen/style/` | ✅ GX CSV → StyleProfile；真实语料 6 组验证 |
| 规划器 | `src/gx3gen/planner/` | ✅ 规则解析 / 领域模板；`planner/llm.py` 为**可选 standalone 适配器**（agent 模式不使用） |
| 反馈闭环 | `src/gx3gen/feedback/` | ✅ 反馈记录 → lessons/checklist/prompt_hint → 重新规划 |
| CLI | `src/gx3gen/cli.py`、`run.py` | ✅ serve/gen/plan/lint/style/project/feedback/optimize/demo/seed-demo |

## 已独立验证

- `python -m unittest discover -s tests -t .` → **162 tests OK**（含 MCP 协议/工具测试、GX 导入规则回归、8 个领域/质量测试）。
- **双向红绿灯（规则/领域模板，无 agent、无任何模型配置）**：
  输入 `samples/traffic_light.requirement.md` + IO 表 →
  `3 段 / 28 网络 / 92 指令 / 约 261 步`，含 6 个 STL 相位、6 个定时器（K250/K30/K20/K200/K30/K20）、
  每个 STL 步块一个 RETSTL、启动停止段、末步回到 S10 的自动循环；`quality.ok=true`、`audit.ok=true`；
  程序本体 CSV 为 UTF-16LE+BOM、CRLF；注释 CSV 为 LF、标题 `Chinese Simplified/简体中文`。
- **真实 GX Works3 导入（2026-10-28）**：压装测试台项目最终版 `program.csv` + `comments.csv`
  在 GX Works3 1.082 导入成功；此前三轮失败根因均已固化为 `spec.py`/`ilcsv`/`generator` 校验与回归测试。
- **GX 导入自检**：历史版本回归——`173031` 检出 9 项、`174039` 5 项、`174921` 1 项、最终版 0 项；
  与 GX 实际报错一致。
- **质量门槛**：1 个空步的 spec 被 QualityError 拦截；需求要求循环但 spec 无回环 → 规划/生成失败并给原因。
- **MCP E2E**：initialize → tools/list（13 工具，无 `gx3gen_plan`）→ get_examples → create/save/load spec →
  validate → generate（含 audit）→ audit_csv 全通；`python run_mcp.py` 无输入退出 0、stdout 为空；
  MCP 不读 API Key、不联网。
- **API E2E**：红绿灯项目 `/api/plan` → `/api/generate` 200、quality.ok、audit.ok；无 LLM 的自然语言需求
  `/api/plan` 返回 400 并给出改写建议；坏 spec `/api/generate` 返回 400 + quality.errors。
- **真实语料风格学习**（只读）：12/13/FK/20G/S03/ProgPou → 画像 7–16KB，
  12 号学到 M500/M501/M521/M522、reset M510、报警 M1600、握手模板、步号 S/100/10。

## 尚未验证（下一步）

1. **全部转换后的编译错误与逻辑正确性**（导入已通过；本期仍是"初稿"，工程师在 GX 接手；无仿真/自动验收）。
2. 不同目标工程的**注释标题**差异（已支持 `project.comment_title`，但尚未覆盖更多现场工程）。
3. 真实题目（阀体线/红绿灯/压装）的工程师人工打分与反馈入库。
4. 注解行（CSV 第 7 列）在 GX 导入后的保留行为（未回读验证）。
5. 标签/FB/ST/FBD/SFC、iQ-R、GX 1.1xx —— 均不在本期范围。

## 建议的真机验收步骤

1. 生成后确认 `audit.ok=true`；或运行 `python run.py audit --program ... --comments ...`。
2. GX Works3 1.082 新建 FX5U-32MR/ES 空工程（或复制你现有的 FX5U 工程作为基座），建好 MAIN 程序。
3. 打开 MAIN 梯形图编辑器，光标置于程序内（不要停在 END 行）。
4. `[编辑] → [导入文件]` 选 `workspace/projects/<id>/out/program.csv`。
5. 软元件注释窗口导入 `comments.csv`（注释 CSV 必须 LF；标题按目标工程设置）。
6. `全部转换`（Shift+Alt+F4），记录 Error/Warning 数、网络数、步数，与生成报告对照。
7. 保存后冷重开，再次导出 CSV，回读比对指令序列/注释。
8. 把结果（错误截图/说明）填回软件"审阅/反馈"或 agent 的 `gx3gen_add_feedback`，作为下一轮学习样本。
