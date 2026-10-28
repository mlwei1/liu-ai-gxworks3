# GX Works3 程序本体 CSV / 软元件注释 CSV 格式（实测基线）

> 来源：GX Works3 1.082 导出样例（12/13/20G/FK/S03、ProgPou）与官方手册
> "Importing/exporting ladder programs"。生成器必须严格按本文件输出。

## 程序本体 CSV

- 编码：**UTF-16LE + BOM**（`FF FE`），TAB 分隔，**所有字段双引号包裹**，行尾 `CRLF`，文件末尾换行。
- 共 7 列：`步号 | 行间声明 | 指令 | I/O(软元件) | 空白栏 | PI声明 | 注解`。
- 第 1 行：项目名（单字段）。
- 第 2 行：`"机型信息:"` + TAB + CPU 字符串（FX5U 用 `FX5CPU FX5U`）。
- 第 3 行：表头。
- 第 4 行起：数据行。

### 行类型

| 类型 | 写法 |
|---|---|
| 指令行 | 步号 + 指令 + 第一个操作数 |
| 续行 | 步号/指令/行间声明/注解为空，操作数每行一个（多操作数指令） |
| 段标题 | `行间声明 = "[Title]段名"`，步号=当前累计步，其余空 |
| 语句行 | `行间声明 = 任意文本`（如 `报警`、`手动`），步号=当前累计步，其余空 |
| 注解行 | 步号/指令/IO 空，仅第 7 列（紧跟被注释指令） |
| END | 普通指令行，最后一行 |

- 步号 = 累计步数，续行/注解行不占步号。
- CSV 里没有网络边界；GX 在"全部转换"时按指令流重建梯形图并重算步号。
- 必须存在 `END`；GX 导入后程序为"未转换"，需要手动"全部转换"。
- GX 导入范围：仅梯形图；只能替换已存在的程序本体（不能新建程序/POU）；监控/仿真中不能导入。

### 常用指令与操作数写法（生成范围）

`LD/LDI/AND/ANI/OR/ORI/ORB/ANB/MPS/MRD/MPP/OUT/SET/RST/ALT/PLS/PLF`
`STL/RETSTL`（显示文本 RETSTL）、`OUT T0 K20`（续行放 K20）
`MOV/BMOV/ZRST`（多操作数用续行）、`END`。

### IL 序列规则（GX 导入的硬约束）

1. **LD/LDI 是「载入」指令**：会丢弃此前的逻辑结果并开启新支路。
   一条 rung 里 `LD` 之后只能接 `AND`/`ANI`/`OR`/`ORI` 等串联/并联触点或输出类指令。
   `LD A` 后紧跟 `LDI B` 是非法的（会留下悬空支路，GX 报「梯形图配置不正确」）；
   串联取反必须用 **`ANI`**，不是 `LDI`。
2. **操作数个数**：FX5U 的 `SET`/`RST` 每条只接受 1 个操作数，多个设备要拆成多条指令
   （`SET M500` + `SET M521`）；`OUT` 1~2 个（`OUT Y0` / `OUT T0 K100`）；`ZRST` 2 个；
   `CMP`/`ADD`/`SUB`/`MUL`/`DIV` 3 个；`MOV` 2 个。
3. **STL 与 RETSTL 配对**：一个 STL 步块配一个 RETSTL（FX5U 标准写法）。

## 软元件注释 CSV

- 编码/分隔/引号同上；但**行尾用 LF**（GX Works3 1.082 真实导出样例即为 LF）。
- 第 1 行：工程名（单字段）；第 2 行：`软元件名` + TAB + **注释标题**；第 3 行起：`设备号` + TAB + `注释`。
- 设备号写法：`X0`、`Y10`、`M500`、`D200`、`T10`、`SM400`、`U1\G5950` 等。
- 标签注释不在本文件内（本期不做标签）。

> **注释标题必须是目标工程里存在的那个**（[视图]→[多注释显示设置] 里的注释标题名）。
> 实测取值：GX Works3 中文版默认工程导出的是 `Chinese Simplified/简体中文`；
> 另有工程把标题自定义成 `注释`（FK 阀内漏测试设备样例）。
> 标题对不上时 GX 报「"XXX"相应的注释标题不存在，因此未读取」+「文件格式有误」。
> 可用 `spec.project.comment_title`（单个）或 `spec.project.comment_titles`（列表）覆盖。
> **不要输出多个候选标题**：GX 逐列检查，不存在的标题会各报一次错。

> **行尾必须是 LF**：用 CRLF 时，每个单元格的取值会残留一个 `\r`，
> 标题行变成 `注释\r`，GX 同样会报「注释标题不存在」与「文件格式有误」。
> （程序本体 CSV 用 CRLF 可以正常导入，两者行尾要求不同。）

## 生成器输出约定

- 程序名前缀使用项目名；CPU 固定 `FX5CPU FX5U`（本期）。
- 每个设备段以 `[Title]段名` 开始。
- STL 序列：`STL Sn` → 动作 `LD Sn` + `SET/RST` → 转移 `LD Sn` + `AND 条件...` + `SET Snext`
  → 每个步块后紧跟一个 `RETSTL`（一个 STL 对应一个 RETSTL）。
- 全局最后 `END`。
- 步号由生成器按简化步长表计算（触点/线圈 2、STL 3、RETSTL 1、SET/RST S 3、OUT T K 5、
  段标题/语句 5+len(文本)、END 1）；GX 转换时会重算，生成值仅用于可读性。

## 人工落地步骤

1. 打开基座工程副本（FX5U，已建 MAIN 程序）。
2. 打开 MAIN 程序本体（梯形图编辑器），光标放在程序内（不要停在 END 行）。
3. `[编辑] → [导入文件]`，选择 `程序本体.csv`。
4. 软元件注释窗口用导入功能载入 `软元件注释.csv`。
5. `全部转换`（Shift+Alt+F4），处理错误后保存。

## 导入前自检

生成器会自动跑 `audit`；也可手动运行：

```bash
python run.py audit --program <program.csv> --comments <comments.csv>
# 或
python tools/gx_audit.py <program.csv> --check <comments.csv> --expect-title "Chinese Simplified/简体中文"
```

检查项：BOM/行尾/操作数个数/rung 内 IL 序列/STL-RETSTL/注释标题。真实故障复盘见
[docs/GX_IMPORT_TROUBLESHOOTING.md](GX_IMPORT_TROUBLESHOOTING.md)。
