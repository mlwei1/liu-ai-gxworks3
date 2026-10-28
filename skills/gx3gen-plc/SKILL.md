---
name: gx3gen-plc
description: 用 gx3gen 工具包为三菱 FX5U / GX Works3 生成 PLC 程序初稿（ProgramSpec → 程序本体 CSV + 软元件注释 CSV）。当用户提到 PLC、GX Works3、GXW3、FX5U、梯形图、STL/SFC、红绿灯、交通灯、工位/取放/气缸顺序、IO 表 → 程序、或要把中文需求变成可导入的程序 CSV 时使用。触发词：PLC 程序生成、GX Works3、FX5U、红绿灯、交通灯、梯形图、SFC/STL、IO 表、段/步/动作/转移、程序初稿、软元件注释。
---

# gx3gen-plc — PLC（FX5U / GX Works3）程序初稿生成技能

## 何时使用

- 用户要把**中文需求 + IO 表**变成 Mitsubishi FX5U / GX Works3 可导入的 PLC 程序初稿；
- 典型题目：**双向红绿灯**、多工位取放/装配/检测序列、气缸/夹爪动作流程、启停/手自动/报警；
- 用户已用 GX Works3 导出过程序 CSV，想学习其编程风格再生成同类程序；
- 用户要审阅/纠错上一版生成结果，让下一轮规避同类问题。

不适用：真实 GX Works3 编译/仿真/在线调试、标签/FB/ST/FBD/SFC 图、多机型（Q/L/iQ-R）、
直接写 `.gx3` 工程文件。本技能只生成**初稿 CSV**，编译与验收由工程师在 GX Works3 完成。

## 核心原则

1. **你（agent）就是 LLM**：项目不调用 LLM、不需要 API Key；不要向用户索要 Key。
   你只负责产出 ProgramSpec，确定性编译器负责格式与质量门槛。
2. **你（agent）产出 ProgramSpec，用户不写 JSON**。用户只给中文需求 / IO 表 / 历史 CSV。
3. **先读上下文再动手**：`gx3gen_get_generation_context` → 你写 spec → `gx3gen_validate_spec`
   → `gx3gen_generate_program`。禁止跳过校验。
4. **不达标不交付**：`quality.ok=false` 时按 errors 迭代；缺信息问用户；不要用 1–2 个空步凑数。
5. **不臆造设备**：设备号必须来自 IO 表/需求；猜测写 `assumptions`。
6. **不许声称已在 GX Works3 编译/仿真通过**。CSV 是待导入初稿，导入→全部转换→修错由工程师完成。

## 工具路由（MCP）

| 场景 | 调用 |
|---|---|
| 第一次生成 / 新需求 | `gx3gen_get_generation_context({requirement, io_csv?, style_id?, project_id?})` |
| 建项目（可选，便于回看/Web 审阅） | `gx3gen_create_project({name, requirement?, io?})` |
| 保存你写的 spec 到项目（可选） | `gx3gen_save_spec({project_id, spec, requirement?, io?})` |
| 读回项目 spec | `gx3gen_load_spec({project_id})` / `gx3gen_list_projects({})` |
| 校验你写的 spec | `gx3gen_validate_spec({spec, requirement})` |
| 生成程序 | `gx3gen_generate_program({spec, requirement, name?, out_dir?, force?})` → 结果含 `audit` |
| GX 导入前自检 | `gx3gen_audit_csv({program_csv_path, comment_csv_path?, expect_comment_title?})` |
| 看红绿灯/三工位示例 | `gx3gen_get_examples({kind})` |
| 学用户风格 | `gx3gen_import_style({program_csv_path, comment_csv_path?, name?})` → 后续传 `style_id` |
| 记录纠错反馈 | `gx3gen_add_feedback({project_id, target, category, severity, message, expected?})` |
| 看历史经验 | `gx3gen_get_lessons({project_id})` |

工具返回：`{content:[{type:"text", text}], isError}`。context 是 Markdown，其余是 JSON 字符串；
失败返回 `{ok:false,error/errors}`，不会破坏 server。MCP 配置见仓库根 `mcp.json.example`。

没有 MCP 时：`python run.py gen`、`python -m gx3gen.cli`，或直接 import `gx3gen` 包；
`run.py plan` 是规则/领域模板兜底（不调用 LLM），可用于结构化需求或双向红绿灯。

## 工作流（固定 6 步）

```
0 收集：工位/动作顺序、互锁、延时、启停/急停、手自动、报警、IO 表（缺则问）
1 上下文：gx3gen_get_generation_context（必读）
2 写 ProgramSpec（结构见 references/program_spec.md）
2.5（可选）create_project + save_spec：项目可回看、Web 可审阅
3 校验：gx3gen_validate_spec → 修到 ok=true && quality.ok=true
4 生成：gx3gen_generate_program → 得到 4 个产物 + audit（GX 导入自检）
   - audit.ok=false 时先按 problems 修 spec，不要交付未通过自检的 CSV
5 交付报告：路径 + 结构 + quality/lint/audit + assumptions + GX 落地步骤
（6 反馈：gx3gen_add_feedback → 下轮 lessons 自动生效）
```

## 质量清单（gate，详细版见 references/quality_checklist.md）

通用：

- [ ] ≥1 个 `init` 段（`SM402 → SET S0`）+ ≥1 个 `station` 段（≥3 个有效步）+ 启动/停止段
- [ ] 每步都有动作或转移条件；没有空步/空动作
- [ ] 每个输出 Y 至少被驱动一次；延时定时器出现在转移条件中
- [ ] 最后一步 `next=null`（回待机）或指回首步（循环）
- [ ] 停止/急停复位运行模式位（M500/M521）；报警有清除路径
- [ ] 步号段不重叠、地址不冲突、注释覆盖 X/Y
- [ ] assumptions 写全所有猜测

GX 导入硬约束（违反会导入失败，详见 docs/GX_IMPORT_TROUBLESHOOTING.md）：

- [ ] `SET`/`RST` 每条只有 1 个操作数（多设备拆多条）
- [ ] rung 内 `LD`/`LDI` 后不紧跟另一个 `LD`/`LDI`（串联用 `AND`/`ANI`）
- [ ] 一个 `STL` 步块配一个 `RETSTL`
- [ ] 程序本体 CSV CRLF；软元件注释 CSV **LF**；注释标题与目标工程一致

双向红绿灯（全部满足才可交付）：

- [ ] ≥6 相位：东西绿 → 东西黄 → 全红 → 南北绿 → 南北黄 → 全红 → 回第 1 相位
- [ ] 每个相位有定时器（T0 起，`OUT Tn K`，K=100ms 基准；建议绿 8–10s、黄 3s、全红 1s）
- [ ] 互锁：任一方向绿灯时另一方向红灯；同方向绿+黄不同时亮
- [ ] 自动循环 + 启停（M500/M501/M521），停止后所有灯回安全状态

## 常见错误

| 现象 | 处理 |
|---|---|
| `QualityError` / `quality.ok=false` | 按 `quality.errors` 补相位/定时器/启停/动作；先修再生成，别删检查 |
| `audit.ok=false` | 按 `audit.problems` 修 SET/RST 多操作数、LD/LDI 连续、STL 配对、注释 LF/标题 |
| `SpecError` 报 `SET/RST 操作数个数` | 拆成多条单操作数指令（`SET M500` + `SET M521`） |
| `SpecError` 报 `LD/LDI 紧跟在...` | 串联改 `AND`、串联取反改 `ANI`；步内动作别以 LD/LDI 开头 |
| 注释导入报「注释标题不存在」 | 注释 CSV 用 LF；`project.comment_title` 设为目标工程实际标题 |
| `SpecError` 字段路径报错 | cpu 必须 `FX5U`；设备号合法；同段 step 递增不重复；segments 非空 |
| `未知工具` / `Method not found` | MCP 客户端没连到本项目 `run_mcp.py`；工具名见上表 |
| `plan`/`create_project` 报错 | 先 `gx3gen_create_project` 建项目；不需要 API Key（agent 就是 LLM） |
| 产物路径不存在 | 检查 `out_dir` / `GX3GEN_WORKSPACE` 写权限；默认 `<repo>/workspace/out/` |
| 用户要“保证编译通过” | 说明初稿边界：GX 导入 → 全部转换 → 修错由工程师完成 |

## 参考文件

- [references/program_spec.md](references/program_spec.md)：ProgramSpec 0.1 全字段、动作/转移、设备规则、示例
- [references/quality_checklist.md](references/quality_checklist.md)：质量 gate 清单与验收方法
- 仓库根 [AGENTS.md](../../AGENTS.md)：agent 操作手册
- [docs/AGENT_WORKFLOW.md](../../docs/AGENT_WORKFLOW.md)：完整示例会话（双向红绿灯）
- [docs/CSV_FORMAT.md](../../docs/CSV_FORMAT.md)：GX Works3 程序 CSV 格式与导入步骤
- [docs/GX_IMPORT_TROUBLESHOOTING.md](../../docs/GX_IMPORT_TROUBLESHOOTING.md)：GX 导入真实排障复盘
- [tools/gx_audit.py](../../tools/gx_audit.py)：GX 导入前自检
