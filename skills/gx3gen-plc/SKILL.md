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

1. **你（agent）产出 ProgramSpec，用户不写 JSON**。用户只给中文需求 / IO 表 / 历史 CSV。
2. **先读上下文再动手**：`gx3gen_get_generation_context` → 你写 spec → `gx3gen_validate_spec`
   → `gx3gen_generate_program`。禁止跳过校验。
3. **不达标不交付**：`quality.ok=false` 时按 errors 迭代；缺信息问用户；不要用 1–2 个空步凑数。
4. **不臆造设备**：设备号必须来自 IO 表/需求；猜测写 `assumptions`。
5. **不许声称已在 GX Works3 编译/仿真通过**。CSV 是待导入初稿，导入→全部转换→修错由工程师完成。

## 工具路由（MCP）

| 场景 | 调用 |
|---|---|
| 第一次生成 / 新需求 | `gx3gen_get_generation_context({requirement, io_csv?, style_id?, project_id?})` |
| 已有需求想先要一版 spec 起点 | `gx3gen_plan({requirement, io, io_csv?, style_id?, use_llm?})` |
| 校验你写的 spec | `gx3gen_validate_spec({spec, requirement})` |
| 生成程序 | `gx3gen_generate_program({spec, requirement, name?, out_dir?, force?})` |
| 看红绿灯/三工位示例 | `gx3gen_get_examples({kind})` |
| 学用户风格 | `gx3gen_import_style({program_csv_path, comment_csv_path?, name?})` → 后续传 `style_id` |
| 记录纠错反馈 | `gx3gen_add_feedback({project_id, target, category, severity, message, expected?})` |
| 看历史经验 | `gx3gen_get_lessons({project_id})` |

工具返回：`{content:[{type:"text", text}], isError}`。context 是 Markdown，其余是 JSON 字符串；
失败返回 `{ok:false,error/errors}`，不会破坏 server。MCP 配置见仓库根 `mcp.json.example`。

没有 MCP 时：`python run.py plan/gen`、`python -m gx3gen.cli`，或直接 import `gx3gen` 包；
流程与字段完全一致。

## 工作流（固定 6 步）

```
0 收集：工位/动作顺序、互锁、延时、启停/急停、手自动、报警、IO 表（缺则问）
1 上下文：gx3gen_get_generation_context（必读）
2 写 ProgramSpec（结构见 references/program_spec.md）
3 校验：gx3gen_validate_spec → 修到 ok=true && quality.ok=true
4 生成：gx3gen_generate_program → 得到 4 个产物路径
5 交付报告：路径 + 结构 + quality/lint + assumptions + GX 落地步骤
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

双向红绿灯（全部满足才可交付）：

- [ ] ≥6 相位：东西绿 → 东西黄 → 全红 → 南北绿 → 南北黄 → 全红 → 回第 1 相位
- [ ] 每个相位有定时器（T0 起，`OUT Tn K`，K=100ms 基准；建议绿 8–10s、黄 3s、全红 1s）
- [ ] 互锁：任一方向绿灯时另一方向红灯；同方向绿+黄不同时亮
- [ ] 自动循环 + 启停（M500/M501/M521），停止后所有灯回安全状态

## 常见错误

| 现象 | 处理 |
|---|---|
| `QualityError` / `quality.ok=false` | 按 `quality.errors` 补相位/定时器/启停/动作；先修再生成，别删检查 |
| `SpecError` 字段路径报错 | cpu 必须 `FX5U`；设备号合法；同段 step 递增不重复；segments 非空 |
| `未知工具` / `Method not found` | MCP 客户端没连到本项目 `run_mcp.py`；工具名见上表 |
| `plan` 未配置 LLM | 规则 spec 当起点，自己复核；或让用户配置 LLM |
| 产物路径不存在 | 检查 `out_dir` / `GX3GEN_WORKSPACE` 写权限；默认 `<repo>/workspace/out/` |
| 用户要“保证编译通过” | 说明初稿边界：GX 导入 → 全部转换 → 修错由工程师完成 |

## 参考文件

- [references/program_spec.md](references/program_spec.md)：ProgramSpec 0.1 全字段、动作/转移、设备规则、示例
- [references/quality_checklist.md](references/quality_checklist.md)：质量 gate 清单与验收方法
- 仓库根 [AGENTS.md](../../AGENTS.md)：agent 操作手册
- [docs/AGENT_WORKFLOW.md](../../docs/AGENT_WORKFLOW.md)：完整示例会话（双向红绿灯）
- [docs/CSV_FORMAT.md](../../docs/CSV_FORMAT.md)：GX Works3 程序 CSV 格式与导入步骤
