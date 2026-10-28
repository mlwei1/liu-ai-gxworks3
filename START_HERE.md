# 新手上路：下载后 5 分钟跑通

[English](START_HERE.en.md) | **中文**

> 这是给**人类用户**的首次使用说明。给 AI agent 的规则在 [AGENTS.md](AGENTS.md)；
> 技能包在 [skills/gx3gen-plc/SKILL.md](skills/gx3gen-plc/SKILL.md)。

## 0. 先记住三件事

1. **不需要 API Key**：打开仓库的 AI 编程工具（Claude Code / DeepSeek Harness / Cursor / Codex…）
   自带模型，它才是“写程序的 AI”。
2. **不需要打开网页**：主流程是纯对话；Web 界面只是实验性审阅工具。
3. **生成的 CSV 是初稿**：最终要在 GX Works3 里导入 → 全部转换 → 工程师修编译错误/补逻辑。

---

## 1. 五分钟跑通（推荐第一次就做这个）

### 第 1 步：下载并打开

- 下载 ZIP 解压，或 `git clone` 本仓库；
- 用 AI 编程工具打开**整个仓库目录**（不要只打开某个文件）；
- 确认本机 Python ≥ 3.9（Windows 可用 `python` 或 `py -3`）。

### 第 2 步：对 agent 说第一句话

可以直接复制：

```text
请先读 AGENTS.md 和 START_HERE.md，检查本机 Python 环境，
然后带我跑通一次双向红绿灯示例：生成程序、做 GX 导入自检、告诉我产物路径和 GX 落地步骤。
不需要 API Key。
```

agent 会自己执行检查、生成和自检；**不需要你配 MCP、不需要你写 JSON**。
如果你的 AI 工具支持 MCP，可以按 [mcp.json.example](mcp.json.example) 配；
不支持也没关系，agent 可以直接用命令行。

### 第 3 步：看第一次产物

生成目录通常在：

```text
workspace/out/...
├── program.csv        # GX 程序本体 CSV
├── comments.csv       # 软元件注释 CSV
├── network_table.md   # 网络表
└── report.md          # 生成报告（含结构/质量/GX 导入自检）
```

第一次成功的标准：

- `quality.ok = true`；
- `audit.ok = true`；
- 报告里能看到段结构/步号/定时器/循环；
- agent 告诉你 4 个文件路径。

### 第 4 步：导入 GX Works3（人工）

1. 打开一个 **FX5U 基座工程副本**（已有 MAIN 程序）；
2. 打开 MAIN 程序本体，光标放在程序内（不要停在 `END` 行）；
3. `[编辑] → [导入文件]` 选 `program.csv`；
4. 软元件注释窗口导入 `comments.csv`；
5. `全部转换`（Shift+Alt+F4）→ 修编译错误/补逻辑 → 保存。

> CSV 导入只能替换已存在的程序本体；导入前请先备份/使用副本工程。
> 真实 GX 1.082 导入已实测通过；常见失败原因见
> [docs/GX_IMPORT_TROUBLESHOOTING.md](docs/GX_IMPORT_TROUBLESHOOTING.md)。

---

## 2. 之后可以做的四类任务

### A. 生成新程序

在对话里说清：

- 工位/动作顺序；
- 启动/停止/急停、手自动；
- 互锁、延时；
- 报警需求；
- IO 表（软元件号/注释；没有 IO 表也能先描述，agent 会把猜测写进 assumptions 让你确认）。

agent 会产出 `ProgramSpec` → 校验 → 生成 → GX 自检 → 报告。

### B. 学你自己程序的风格

1. 在 GX Works3 里导出：`程序本体 CSV` + `软元件注释 CSV`；
2. 把文件放到本机任意目录；
3. 对 agent 说：

```text
用 C:\work\我的工程\程序本体.csv 和 C:\work\我的工程\全局软元件注释.csv 学我的编程风格，名字叫"我的风格"。
```

以后生成会套用你的段结构、步号段、命名、握手/报警习惯。

> 边界：本期不支持直接打开/修改现有 `.gx3` 工程；导入自己的程序只用于学风格。

### C. 查官方/自己的手册

手册是**查**的，不是读的；项目不会把整本 PDF 塞进 AI 上下文。

1. 把 PDF/DOCX/MD/TXT 放到本机（例如 `docs/官方资料/`）；
2. 让 agent 建索引，或自己运行：

```bash
python -m pip install -r requirements-manuals.txt   # 可选，仅建索引需要
python run.py manual add --path "docs/官方资料/你的手册.pdf" --model FX5U
python run.py manual list
```

3. 之后直接在对话里问：

```text
ZRST 指令接受几个操作数？在手册哪一页？
FX5U 高速计数器的最大频率是多少？
```

agent 会检索并引用 **手册 / 章节 / 页码**。
官方手册不要提交 git；索引在 `workspace/manuals/`，只存在本机。

### D. 纠正与迭代

直接在对话里指出问题，例如：

```text
第 3 段缺少急停复位；压装站要等检测站可收料才能放料；停止时灯要全灭。
```

agent 会记录反馈、修改 `ProgramSpec`、重新生成；GX 里的编译错误也可以贴给 agent。

---

## 3. 你需要准备什么

| 事项 | 是否必须 | 说明 |
|---|---|---|
| AI 编程工具 | 必须 | 支持对话即可；支持 MCP 更好，不支持可用 CLI |
| Python 3.9+ | 必须 | 零第三方依赖；仅手册建索引需可选 PyMuPDF |
| GX Works3 + FX5U 基座工程 | 导入时必需 | 生成程序不需要 GX |
| IO 表 | 建议 | 没有也能先描述，agent 会写 assumptions |
| 自己的历史程序 CSV | 可选 | 用于学风格 |
| 官方/公司手册 PDF | 可选 | 用于查资料；不建索引也能生成程序 |

---

## 4. 常见问题

**Q：要配 API Key 吗？**
不要。项目不调用 LLM；打开仓库的 AI 工具就是模型。只有“完全不用 agent、把 Web 当独立工具”时，
才需要可选的本地 LLM 配置。

**Q：一定要配置 MCP 吗？**
不强制。MCP 让工具调用更规范；配不上时 agent 可以直接用 `python run.py ...` 命令完成。
先跑示例不需要 MCP。

**Q：要打开 Web 界面吗？**
不需要。主流程是纯对话；Web 只是实验性审阅界面。

**Q：生成后是不是就完成了？**
不是。CSV 只是初稿；必须在 GX Works3 导入、全部转换，由工程师修编译错误和细节逻辑。

**Q：手册为什么搜不到？**
先确认已经 `manual list` 看到手册；PDF 需要先建索引。部分 PDF 的图形/表格内部文字
来自特殊字体，文本层可能乱码，需要后续 OCR；正文/指令章节通常可检索。

**Q：MCP 工具列表应该有几个？**
当前 17 个：生成/校验/自检/项目、4 个手册工具、风格/反馈等。

---

## 5. 相关文件

- [README.md](README.md)：产品定位、功能、命令
- [AGENTS.md](AGENTS.md)：给 AI agent 的操作手册
- [docs/CHAT_WORKFLOW.md](docs/CHAT_WORKFLOW.md)：纯对话完整流程（生成/学风格/纠正/落地）
- [docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md)：MCP 配置与完整示例会话
- [docs/MANUALS.md](docs/MANUALS.md)：手册知识库
- [docs/CSV_FORMAT.md](docs/CSV_FORMAT.md)：GX CSV 格式
- [docs/GX_IMPORT_TROUBLESHOOTING.md](docs/GX_IMPORT_TROUBLESHOOTING.md)：GX 导入排障复盘
- [docs/STATUS.md](docs/STATUS.md)：实现状态与边界
