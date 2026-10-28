# ProgramSpec 0.1 字段说明（agent 写 JSON 时照此）

ProgramSpec 是 AI 与确定性生成器之间的唯一契约。官方 JSON Schema：`spec/program_spec.schema.json`。
校验函数：`gx3gen.spec.validate_spec`（MCP 工具 `gx3gen_validate_spec`）。

> 原则：**能确定的结构写进 spec；不确定的工艺/设备/延时写进 `assumptions`**，不要发明设备。

## 顶层结构

```json
{
  "spec_version": "0.1",
  "project": {"name": "项目名", "cpu": "FX5U", "gx_version": "1.082", "style_profile": null},
  "io": [{"device": "X0", "comment": "启动按钮", "role": "input"}],
  "modes": {"run": "M500", "stop": "M501", "auto": "M521", "manual": "M522", "reset": "M510"},
  "common": {
    "handshake_base": 1000, "alarm_base": 2000, "alarm_timer_base": 10,
    "start_step": 0, "station_step_base": 100, "station_step_stride": 100,
    "step_increment": 10, "auto_rungs": true
  },
  "segments": [],
  "alarms": {"enabled": false, "base": 2000, "timer_base": 10, "delay": 20,
             "reset_device": null, "cylinders": []},
  "assumptions": ["所有待现场确认项"],
  "notes": []
}
```

| 字段 | 规则 |
|---|---|
| `spec_version` | 固定 `"0.1"` |
| `project.cpu` | 固定 `"FX5U"`（本期唯一支持） |
| `project.name` | 非空字符串（生成器用作程序名前缀） |
| `project.style_profile` | 可选，风格画像 id（来自 `gx3gen_list_styles` / `gx3gen_import_style`） |
| `io[]` | 每个 item：`device`（合法软元件）、`comment`（非空中文）、`role`（`input/output/inout/internal`） |
| `modes` | 模式位设备，键任意；生成器读 `run/stop/auto/manual/reset` |
| `common` | 步号/握手/报警基址等整数配置，见上 |
| `segments[]` | 至少 1 个；见下 |
| `alarms` | 气缸报警；`enabled=true` 且 `cylinders` 非空才会生成报警段 |
| `assumptions[]` | 必须写全所有猜测（按钮、延时、报警位、互锁等） |

## 段（segments）

段类型 `kind` 只能是 `init | station | common | rungs | statement`。

### init（初始化段，必需）

```json
{"title": "初始化", "kind": "init",
 "rungs": [{"comment": "上电置待机步",
            "instructions": [{"op": "LD", "operands": ["SM402"]},
                             {"op": "SET", "operands": ["S0"]}]}]}
```

约定：`SM402`（初始脉冲）→ `SET S0`（待机步）。没有 init 段时生成器会自动补一段，但建议显式写。

### station（设备/自动流程段，核心）

```json
{
  "title": "取放站",
  "kind": "station",
  "step_range": [100, 199],
  "entry_conditions": ["S0", "X0"],
  "handshake": {"out": [{"device": "M1000", "comment": "取放站/完成"}], "shift": ["M1001"]},
  "steps": [
    {"step": 100, "comment": "竖直缸下降",
     "actions": [{"set": ["Y0"], "reset": ["Y1"], "comment": "下降"},
                 {"instructions": [{"op": "OUT", "operands": ["T0", "K20"]}], "comment": "延时2s"}],
     "transition": {"conditions": ["X3"], "next": 110, "comment": "到位 → 下一步"}},
    {"step": 110, "comment": "完成回待机",
     "actions": [{"reset": ["Y0"], "comment": "上升"}],
     "transition": {"conditions": ["X2"], "next": null, "comment": "回待机"}}
  ]
}
```

- `step_range`：`[start, end]`，同段 step 必须**递增不重复**（建议起点 100、增量 10）。
- `entry_conditions`（可选）：进入条件，生成器生成 `LD 条件… → SET 首步` 网络。
- `handshake`（可选）：`in`/`out` 为 `[{device, comment}]`（也可字符串），`shift` 为设备数组；
  生成器在段尾生成交接/移位信号网络。
- `steps[].actions[]`：动作，至少一个字段：
  - `set`: 设备数组（→ `SET`）
  - `reset`: 设备数组（→ `RST`）
  - `out`: 设备数组（→ `OUT`）
  - `instructions`: `[{op, operands}]`（如 `{"op": "OUT", "operands": ["T0","K20"]}` 延时 2s）
  - `comment`: 中文动作说明（建议必填）
- `steps[].transition`：`conditions`（设备数组，AND 关系）+ `next`（下一步整数，或 `null`=回待机 S0）
  + `comment`。有 next 时 conditions 必须有；最后一步 `next=null` 表示完成回待机。
- **自动循环**：最后一步 `next` 指回本段首步（如 60 → 10），即形成循环。
- 每个 step 建议「动作 + 转移」成对出现；不要只写动作没有转移（除非最后一步 next=null）。

### common（启动/停止/手自动/公共逻辑）

```json
{"title": "启动停止", "kind": "common",
 "rungs": [
   {"comment": "启动 → 运行中 + 自动",
    "instructions": [{"op": "LD", "operands": ["X0"]},
                     {"op": "SET", "operands": ["M500"]},
                     {"op": "SET", "operands": ["M521"]}]},
   {"comment": "停止 → 复位",
    "instructions": [{"op": "LD", "operands": ["X1"]},
                     {"op": "RST", "operands": ["M500"]},
                     {"op": "RST", "operands": ["M521"]}]}
 ]}
```

`rungs` / `common` 段的 `instructions` 是线性指令序列：`LD/LDI/AND/ANI/OR/ORI/ORB/ANB/INV/OUT/SET/RST/ALT/PLS/PLF/MOV/ZRST/END` 等。

### statement

`{"title": "说明", "kind": "statement", "statements": ["文本1", "文本2"]}`（只生成文本行，不产生逻辑）。

## 报警（alarms）

```json
"alarms": {
  "enabled": true, "base": 2000, "timer_base": 10, "delay": 20, "reset_device": "X1",
  "cylinders": [{"name": "竖直缸", "output": "Y0", "on_sensor": "X3", "home_sensor": "X2"}]
}
```

生成器为每个气缸生成：到位/原位检测延时（`T10` 起，`delay` 单位 100ms，默认 K20=2s）→
报警位 `M2000` 起（每缸两位）；`reset_device`（通常为停止按钮）触发 `ZRST` 清除。

## 设备与操作数规则

- 合法软元件：`X/Y/M/D/T/C/S/SM/SD/ZR/Z/V/L/B/W` 等 + 数字；`U1\G5950` 链接寄存器。
- 立即数：`K20`（十进制）、`H1F`（十六进制）、`E1.5`（浮点）、`"文本"`。
- X（输入）只能作条件，不能作为 SET/RST/OUT 的目标；Y（输出）是动作目标。
- 定时器：`OUT Tn K100` = 100ms 基准 × 100 = 10s；转移条件写 `Tn`（触点）。
  本包约定延时定时器 `T0` 起，报警定时器 `T10` 起。
- 设备号必须来自 IO 表或需求原文；不在 IO 表的设备会触发 lint `UNDEFINED_DEVICE` 告警。
- `SM402`：初始脉冲（上电第一个扫描周期 ON），用于初始化。

## 生成器对 spec 的默认处理（写 spec 时不必重复）

- 没有 `init` 段且 `common.auto_rungs=true` 时自动补 `SM402 → SET S0`；
- `alarms.cylinders` 非空时自动追加「报警」段；
- 全局最后自动追加 `END`；每个 station 段以 `STL Sx` 开始、`RETSTL` 结束；
- 段标题写 `[Title]段名`；注释来自 spec/IO/动作 comment；
- 步号只用于可读性，GX Works3 导入后会重算。

## 最小可运行示例（结构完整版）

参考仓库 `samples/three_station.spec.json`（三工位序列）与 `samples/traffic_light.spec.json`（双向红绿灯，
6 相位 + 定时器 + 循环 + 启停），MCP 里用 `gx3gen_get_examples` 获取。

## 校验失败常见原因

| 报错 | 修正 |
|---|---|
| `spec_version: 必须为 '0.1'` | 改成 `"0.1"` |
| `project.cpu: 本期只支持 'FX5U'` | CPU 改为 `"FX5U"` |
| `io[i].device: 非法软元件名` | 用 `X0/Y0/M100` 等，不要写中文/注释 |
| `segments[i].steps[j].step: 同段内步号重复` | 同段 step 唯一且递增 |
| `segments[i].kind: 必须是 (...)` | 只允许 init/station/common/rungs/statement |
| `segments: 至少需要一个段` | 至少给 init + station |
