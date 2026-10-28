# 架构（MVP v0.1）

## 目标

把"中文需求 + IO 表 + 用户风格"变成一版 **可读、可改、可在 GX Works3 导入** 的梯形图/STL 程序初稿。
编译错误与细节逻辑由工程师在 GX Works3 接手，本软件不建仿真/自动验收闭环。

## 分层

```
┌──────────────────────────────────────────────────────────┐
│ app/          实验性可选审阅界面（非主流程；纯对话不需要）  │
├──────────────────────────────────────────────────────────┤
│ planner/      需求 → ProgramSpec（规则/领域模板；LLM 仅可选适配器） │
│ feedback/     纠错记录 → 经验 lessons / 修正样例             │
│ style/        用户历史程序 → StyleProfile                   │
├──────────────────────────────────────────────────────────┤
│ generator/    ProgramSpec → 段/网络/STL → 指令 IR           │
│ ilcsv/        指令 IR → GX 程序本体 CSV / 注释 CSV          │
│ spec.py       ProgramSpec 契约 + 校验 + IO 表解析           │
├──────────────────────────────────────────────────────────┤
│ 文件层        workspace/（项目、spec、产物、反馈、风格库）    │
└──────────────────────────────────────────────────────────┘
```

依赖方向只能向下：`app → planner/style/feedback → generator/ilcsv → spec`。
`generator` 与 `ilcsv` 不依赖 LLM，可完全确定性测试。

## 关键契约

1. **ProgramSpec（JSON）**：程序级语义契约，见 `spec/program_spec.schema.json`。
   人工确认的对象就是它；LLM 也只允许产出它。
2. **Instruction IR**（`generator/ir.py`）：`Instruction(op, operands)` 的线性列表 +
   `Rung(comment, instructions)` + `Segment(title, kind, rungs, steps)`。
   这是编译器内部格式，LLM 不直接接触。
3. **GX CSV**（`ilcsv/`）：官方导入格式。
   UTF-16LE + BOM、TAB 分隔、全字段双引号、CRLF、7 列、END 结尾。
4. **StyleProfile（JSON）**：从用户历史程序提取的段结构/步号段/模式位/命名/握手/指令画像。
5. **FeedbackRecord（JSONL）**：用户对某段/某网络的纠错；派生 lessons 与修正样例。

## 生成流程（确定性）

```
ProgramSpec
  ├─ 初始化段（SM402 → SET 首步）
  ├─ 各设备段： [Title]站名
  │     STL Sx
  │     LD Sx / 动作（SET/RST/OUT…）
  │     LD Sx / AND 条件 / SET 下一步
  │     RETSTL（每个 STL 步块一个）
  ├─ 报警段（每气缸两报警位 + 定时器）
  ├─ 公共段（启动停止/模式/手自动）
  └─ END
→ 指令 IR → 行模型（步号/行间声明/指令/操作数/注解）→ CSV
→ 设备注释表 → 注释 CSV
→ GX 导入自检（audit）→ 网络表 / 报告
```

## 为什么主输出 CSV 而不是 .gx3

- CSV 是 GX Works3 官方支持的"程序本体"导入格式，转换时由 GX 重算几何与步号；
- 生成器不受 `.gx3` 几何/StepSize 逆向细节约束，可覆盖更多指令形状；
- 文本可读、可被工程师/Excel 直接修改；
- 不依赖 GX 私有格式，版本升级风险低；
- 本期不做 `.gx3` 直写；后续可作为"加速通道"加入（同一 IR 再写一个 emitter）。

## Agent-first 层（2026-10-28 新增）

```
用户下载仓库 → AI agent 打开（读 AGENTS.md / skills/gx3gen-plc）→ 配置 MCP
        │
        ▼  gx3gen_get_generation_context（schema/架构默认/质量红线/示例/IO/风格/lessons）
   agent（LLM）自己产出 ProgramSpec
        ▼  gx3gen_validate_spec（结构校验 + 质量门槛）
        ▼  gx3gen_generate_program（确定性编译器 → CSV/注释/网络表/报告）
        ▼  交付报告（产物路径/质量/assumptions/GX 落地步骤）
```

这层的意义：
- **agent 是唯一大脑**：agent 即 LLM，项目内不调用模型、不需要 API Key；
  MCP 只提供契约、上下文、校验与编译（不会调用项目内 LLM，也没有 `gx3gen_plan` 之类工具）。
- 规则/领域模板作为兜底（如双向红绿灯），没有 agent 也能生成合格初稿；
- 项目与 Web/CLI 通过 `workspace/projects/<id>/spec.json` 解耦：agent 写入，Web 只读/审阅；
- 质量门槛与 `Assumptions` 保证 agent 不会用"1–2 个空 STL 步"蒙混交付。

> `planner/llm.py` 与 Web「设置」里的 LLM 接口只是**可选 standalone 适配器**（没有 AI agent 时的独立运行/CI 模式），
> 不属于 agent 主路径，也不出现在默认操作流程里。

## 非目标（本期）

- 不自动调用 GX 编译/仿真（人接手）；
- 不做标签/FB/ST/SFC/FBD；
- 不做多 CPU/多 GX 版本；
- 不做云端/多人协作。
