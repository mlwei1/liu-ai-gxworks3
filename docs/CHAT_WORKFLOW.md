# 纯对话工作流（主流程）

> **本项目的主流程就是：用户和 AI agent 对话。**
> 不需要打开网页，不需要配置 API Key——打开仓库的 AI 编程工具（Claude Code / DeepSeek Harness /
> Cursor / Codex…）自带模型，它就是生成程序的 AI。
>
> Web 界面只是**实验性**的可选审阅界面，不属于主流程（见文末）。

## 0. 前提

- 一个支持 **MCP** 且能访问本机文件系统的 AI 编程工具；
- Python 3.9+（零第三方依赖）；
- 实际导入/编译需要 Windows + GX Works3 1.082（生成阶段不需要）。

## 1. 一次性配置

1. 下载/克隆本仓库。
2. 用 AI agent 打开仓库。agent 会自动读取 [`AGENTS.md`](../AGENTS.md) 与
   [`skills/gx3gen-plc/SKILL.md`](../skills/gx3gen-plc/SKILL.md)。
3. 按 [`mcp.json.example`](../mcp.json.example) 把 MCP server 配进 AI 客户端（`python <repo>/run_mcp.py`）。
4. 确认 agent 能看到 12 个 `gx3gen_*` 工具。

之后所有工作都在对话里完成。

---

## 2. 场景 A：从需求生成程序（最常用）

### 用户要说的话

```text
做一个双向红绿灯：
东西/南北各红黄绿三灯；启动后自动循环：
东西绿 10s → 东西黄 3s → 全红 1s → 南北绿 10s → 南北黄 3s → 全红 1s → 循环；
任一方向绿灯时另一方向必须红灯；停止按钮复位所有灯。
启动按钮 X0，停止按钮 X1；灯输出 Y0–Y5（东西红/黄/绿，南北红/黄/绿）。
```

如果暂时没有 IO 表也可以先描述；agent 会把自动分配的地址写进 `assumptions` 让你确认。

### agent 内部会做什么（用户不用管）

1. `gx3gen_get_generation_context` 读取 ProgramSpec 结构/架构默认/质量红线/示例；
2. 自己写 `ProgramSpec`；
3. `gx3gen_validate_spec` 校验结构 + 质量门槛，不过就按 `errors` 迭代或问你；
4. `gx3gen_generate_program` 生成 4 个产物，并自动做 GX 导入自检（`audit`）；
5. 把产物路径、段/步结构、质量结果、audit、assumptions、GX 落地步骤告诉你。

### 用户会看到什么

- `program.csv`（程序本体）、`comments.csv`（软元件注释）、`network_table.md`、`report.md` 的路径；
- 段结构（初始化 / 启动停止 / 自动流程）与步号；
- 质量检查结果（质量门槛是否通过）；
- **GX 导入自检** `audit`（操作数个数 / IL 序列 / STL-RETSTL / 注释标题与行尾）；
- 待确认假设（时长、自动分配的地址、互锁等）。

**这时不需要打开任何网页。** 想看文件内容，让 agent 读给你听或直接打开文件即可。

---

## 3. 场景 B：导入自己的程序学习风格（可选）

1. 在 GX Works3 里打开你的工程：`程序本体 → 导出`，再导出`软元件注释`，得到两个 CSV；
2. 把文件放到本机任意目录（也建议放到 `workspace/import/`）；
3. 对 agent 说：

   ```text
   用 C:\work\我的工程\程序本体.csv 和 C:\work\我的工程\全局软元件注释.csv 学我的编程风格，名字叫"我的风格"。
   ```

4. agent 调 `gx3gen_import_style`，得到风格画像（段结构/步号段/模式位/报警基址/握手命名/指令构成）；
5. 后续生成时 agent 会传入 `style_id`，产出更贴近你的写法。

> 边界：本期**不支持直接打开/修改现有 `.gx3` 工程**。导入自己的程序只用于"学风格"，
> 不是"在旧工程上改逻辑"；旧工程改动仍需在 GX Works3 里人工完成。

---

## 3.5 场景 B2：查官方/自己上传的手册（按需，不读整本）

手册是"查"的，不是"看"的。项目会把 PDF 建成本地索引，agent 按需查片段并给页码。

### 建索引（一次性，用户可以自己操作）

```bash
python -m pip install -r requirements-manuals.txt   # 可选，仅建索引需要
python run.py manual add --path "docs/官方资料/你的手册.pdf" --model FX5U
python run.py manual list
```

以后新增手册：把文件放进 `workspace/manuals/inbox/`，或直接 `manual add --path ...`。

### 对话里的用法

```text
ZRST 指令接受几个操作数？在手册哪一页？
FX5U 高速计数器的最大频率是多少？请给出手册页码。
```

agent 会：

1. `gx3gen_manual_search({query, model:"FX5U", top_k:5})`；
2. 只使用命中片段，回答时引用 **手册 / 章节 / 页码**；
3. 片段不够时 `gx3gen_manual_read({manual_id, section_id})` 读指定章节；
4. 没有索引时告诉你运行 `manual add`，不会凭空猜手册内容。

> 不要直接用文件读取工具打开整本 PDF（会撑爆上下文）；索引位于 `workspace/manuals/`，不进 git。

---

## 4. 场景 C：纠正与迭代

在对话里直接指出问题即可，例如：

```text
第 3 段缺少急停复位；压装站必须等检测站"可收料"才能放料；停止时灯要全灭。
```

agent 会：

1. `gx3gen_add_feedback` 记录（类别/严重级/期望）；
2. 修改 ProgramSpec（或先和你确认）；
3. 重新 `validate → generate`（生成结果含 `audit`，未通过不要交付）；
4. 下一轮自动带上 lessons（`gx3gen_get_lessons`），同类问题不再重复。

也可以把 **GX Works3 全部转换后的编译错误**直接贴给 agent，它会记录并调整 spec/assumptions。

### 项目连续性（可选）

如果希望多轮/多天继续同一个项目，让 agent：

1. `gx3gen_create_project({name, requirement})` 建项目；
2. `gx3gen_save_spec({project_id, spec})` 保存 spec（同时写 `spec.json`）；
3. 下次 `gx3gen_list_projects` → `gx3gen_load_spec` 继续。

项目文件就是状态，不需要前端数据库。

---

## 5. 场景 D：落地到 GX Works3（人工，必须）

1. 确认生成结果的 `audit.ok=true`；不确定时运行：
   `python run.py audit --program <program.csv> --comments <comments.csv>`
2. 打开基座工程副本（FX5U，已有 MAIN 程序）；
3. 打开 MAIN 程序本体，光标放程序内（不要停在 END 行）；
4. `[编辑] → [导入文件]` 选 `program.csv`；
5. 软元件注释窗口导入 `comments.csv`；
6. `全部转换`（Shift+Alt+F4）→ 修编译错误/补逻辑 → 保存。

> 2026-10-28 实测：按上述约束生成的程序本体 + 注释已在 GX Works3 1.082 导入成功；
> 常见失败原因（SET/RST 多操作数、LDI 误用、STL/RETSTL 不配对、注释 LF/标题）见
> [GX_IMPORT_TROUBLESHOOTING.md](GX_IMPORT_TROUBLESHOOTING.md)。

agent **不能**替你做 GX Works3 的导入/编译/仿真（官方没有编程 API），这是流程中唯一必须人工的步骤。

---

## 6. 可选/实验性：本地 Web 审阅界面

```bash
python run.py serve     # http://127.0.0.1:8765
```

- 定位：**实验性**的人工审阅台——浏览 agent 生成的项目/spec/产物、下载 CSV、记录反馈；
- 不进入主流程，不需要打开，也不需要 API Key；
- 页面上的「规则解析 / 本地 LLM」是给"没有 agent 的独立使用"准备的兜底，不是 agent 流程的一部分；
- 如果项目要精简，这一层可以直接不启动或后续移除。

---

## 7. agent 不应该做的事

- 不要向用户索要 API Key（agent 自己就是 LLM）；
- 不要要求用户打开 Web 界面才能继续；
- 不要用文件读取工具读整本 PDF；手册用 `gx3gen_manual_search/read`，只引用片段和页码；
- 不要声称已在 GX Works3 编译/仿真通过（CSV 只是待导入初稿）；
- 不要发明 IO 表/需求里没有的设备。
