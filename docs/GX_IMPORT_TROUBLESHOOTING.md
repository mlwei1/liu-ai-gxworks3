# GX Works3 导入排障复盘：压装测试台程序三次导入失败

> 日期：2026-10-28　｜　项目：压装测试台（FX5U）　｜　项目 ID：`p-20261028172940-c4fdb2`
>
> 一句话结论：**GX Works3 报「指令不正确」「梯形图配置不正确」「注释标题不存在」，
> 根因全在 CSV 与 GX 指令集/CSV 格式的硬约束不符**，与 ProgramSpec 的业务逻辑无关。
> 共 4 个独立根因，分 3 轮定位修复。

---

## 1. 故障时间线

| 轮次 | GX 报错 | 根因 | 修复 |
|---|---|---|---|
| 1 | `指令不正确` ×4（行 136/138/145/151）+ `梯形图配置不正确` ×1 | `SET`/`RST` 写成多操作数 | 拆成单操作数独立指令 |
| 2 | `梯形图配置不正确` ×1 | rung 内误用 `LDI` 做串联取反 | 改用 `ANI`；STL/RETSTL 改为 1:1 配对 |
| 3 | `注释标题不存在` + `文件格式有误` ×2 | 注释 CSV 行尾 CRLF + 注释标题名不对 | 行尾改 LF；标题改 `Chinese Simplified/简体中文` |

最终版本 `压装测试台-20261028-181949`：**程序本体导入成功 + 注释导入成功**。

---

## 2. 根因一：`SET` / `RST` 多操作数

### 现象

```
1 Error program.csv 列表指令 指令不正确。请修改指令。      136 行
2 Error program.csv 列表指令 指令不正确。请修改指令。      138 行
3 Error program.csv 列表指令 指令不正确。请修改指令。      145 行
4 Error program.csv 列表指令 指令不正确。请修改指令。      151 行
```

### 根因

spec 里写了多操作数的 `SET`/`RST`：

| 行 | 原始写法 | 应有写法 |
|---|---|---|
| 136 | `SET M500 M521` | `SET M500` + `SET M521` |
| 138 | `RST M140 M146 M147 Y3 Y4` | 5 条独立 `RST` |
| 145 | `RST M500 M521 M130 M140 M146 M147` | 6 条独立 `RST` |
| 151 | `RST Y0 Y1 Y2 Y3 Y4 Y5` | 6 条独立 `RST` |

**FX5U 的 `SET`/`RST` 每条指令只接受 1 个操作数**。生成器把一条指令的多余操作数
渲染成「续行」（`ilcsv/emitter.py`），续行格式本身没问题，但 `SET` 的语义只允许一个。

> 对照：`OUT` 1~2 个（`OUT Y0` / `OUT T0 K100`）、`ZRST` 2 个、`CMP`/`ADD`/`SUB` 3 个、
> `MOV` 2 个——这些多操作数指令用续行是合法的。

### 修复

- spec 里拆成多条单操作数指令
- `src/gx3gen/spec.py` 新增 `OP_OPERAND_LIMITS` + `_check_operand_count()`，
  在**生成前**按 FX5U 指令集校验操作数个数
- 顺带补上校验漏洞：`init` / `common` 段的 `rungs` 之前**完全没有指令校验**
  （只有 `rungs` 和 `station` 两种段有），抽出 `_check_rungs()` 统一处理

---

## 3. 根因二：rung 内误用 `LDI` 做串联取反

### 现象

第 1 轮修完后，4 条「指令不正确」消失，只剩：

```
1 Error program.csv 列表指令 梯形图配置不正确。已中断处理。…
```

GX 不给行号——因为它是在**把 IL 指令流重建为梯形图**时失败的。

### 根因

spec 里写了：

```
LD M130
LDI M120      ← 错
ANI M121
SET M140
```

**三菱 IL 里 `LD`/`LDI` 是「载入」指令**：载入新的逻辑结果，并**丢弃此前的结果**、
**开启新支路**。串联条件必须用 `AND`/`ANI`/`OR`/`ORI`。

所以 `LD M130` 后面紧跟 `LDI M120` 的效果是：

1. **结构问题**：`LD M130` 成了没有输出指令的**悬空支路**，GX 转梯形图时报「梯形图配置不正确」
2. **逻辑问题**：`M130`（测量窗口使能）被丢弃，合格判定实际没受测量窗口约束

正确写法是 `LD M130; ANI M120; ANI M121; SET M140`（`ANI` = AND NOT）。

**受影响的三处**（全在「直径测量与判定」段）：合格判定、判定完成、不合格→红灯。

### 顺带修复：STL / RETSTL 配对

原来一个 station 段里 **6 个 `STL` 只配 1 个 `RETSTL`**。FX5U 标准写法是
**一个 STL 步块配一个 RETSTL**，改为 6:6 配对（`generator/build.py`）。

### 修复

- spec 里 3 处 `LDI` → `ANI`
- `spec.py` 新增 `_check_il_sequence()`：同一 rung 内 `LD`/`LDI` 后紧跟另一个
  `LD`/`LDI` → 生成前报错；并禁止 station 段 `step.actions.instructions` 以
  `LD`/`LDI` 开头（生成器已自动加 `LD S<step>` 作为步条件）
- `lint.py` 增加 `STL`/`RETSTL` 数量不等告警
- `docs/CSV_FORMAT.md` 补「IL 序列规则」一节

---

## 4. 根因三：软元件注释 CSV 的行尾与注释标题

### 现象

程序本体导入成功后，注释导入报错：

```
1 Error comments.csv 软元件注释 "注释"相应的注释标题不存在，因此未读取。请确认注释标题。
2 Error comments.csv 软元件注释 文件格式有误，因此未读取部分信息。已中断处理。
```

### 定位方法

用**用户提供的真实 GX 导出样例**做逐字节对照，这是决定性的一步。
对照结果（`tools/gx_audit.py` / 对比脚本）：

| 特征 | GX 真实导出 | gx3gen 生成 | 一致 |
|---|---|---|---|
| UTF-16LE+BOM | ✓ | ✓ | ✓ |
| 第 1 行 | `['工程名']` 单列 | `['压装测试台']` | ✓ |
| 第 2 行 | `['软元件名', 'Chinese Simplified/简体中文']` | `['软元件名', '注释']` | ✗ |
| 列数分布 | {1:1, 2:N} | {1:1, 2:N} | ✓ |
| **行尾** | **LF** | **CRLF** | ✗ |

### 两个独立原因

**(a) 行尾必须是 LF。** GX 导出注释 CSV 用 LF；gx3gen 用 CRLF，于是每个单元格的
**取值尾部残留一个 `\r`**，标题行实际变成 `注释\r` → GX 匹配不到标题 → 报「注释标题不存在」。

**(b) 注释标题必须与目标工程一致。** GX 在 `[视图]→[多注释显示设置]` 里为每个工程
配置注释标题，导入时按标题名匹配。实测取值：

| 工程 | 注释标题 |
|---|---|
| 中文版默认工程（压装测试台用） | `Chinese Simplified/简体中文` |
| FK 阀内漏测试设备 | `注释`（自定义） |

所以「注释」不是固定值，**必须按目标工程取**。

> **注意**：不要图省事输出多个候选标题。GX 是**逐列检查**的，不存在的标题会各报一次错。

### 修复

- `ilcsv/rows.py`：`_write_rows()` 增加 `lineterminator` 参数；
  `write_comment_csv()` 改用 `\n`，并新增 `COMMENT_TITLE = "Chinese Simplified/简体中文"`
- `spec.py` 新增 `project.comment_title` / `project.comment_titles` 校验与透传
  （`generator/__init__.py`、`ilcsv/emitter.py`）
- `read_comment_csv()` 改为多列容错（取第一个非空注释列）
- `docs/CSV_FORMAT.md` 补「注释标题」与「行尾」两条约束

---

## 5. GX Works3 格式硬约束速查

### 程序本体 CSV

| # | 约束 | 违反时的报错 |
|---|---|---|
| 1 | UTF-16LE + BOM、TAB 分隔、全字段双引号、CRLF、7 列 | 格式类错误 |
| 2 | 第 1 行工程名（单字段）/ 第 2 行机型信息 / 第 3 行表头 | 格式类错误 |
| 3 | 末行必须是 `END` | 格式类错误 |
| 4 | `SET`/`RST` 每条 **1 个**操作数 | 指令不正确 |
| 5 | `OUT` 1~2 / `ZRST` 2 / `CMP`·`ADD`·`SUB`·`MUL`·`DIV` 3 / `MOV` 2 | 指令不正确 |
| 6 | 多操作数用**续行**承载（步号/指令列留空，仅 I/O 列有值） | 指令不正确 |
| 7 | 同一 rung 内 `LD`/`LDI` 后不得紧跟另一个 `LD`/`LDI`（串联用 `AND`/`ANI`） | **梯形图配置不正确** |
| 8 | 一个 `STL` 步块配一个 `RETSTL` | 梯形图配置不正确 |

> **rung 边界**：CSV 里没有网络边界，**注解行（第 7 列非空）标记一个 rung 结束**，
> 其后的指令行是新 rung 的开头。检查 IL 序列必须按 rung 划分，不能全文件连续检查。

### 软元件注释 CSV

| # | 约束 | 违反时的报错 |
|---|---|---|
| 1 | UTF-16LE + BOM、TAB 分隔、全字段双引号 | 格式类错误 |
| 2 | **行尾用 LF**（与程序本体 CSV 不同！） | 注释标题不存在 + 文件格式有误 |
| 3 | 第 1 行工程名单字段 / 第 2 行 `软元件名` + **注释标题** | 注释标题不存在 |
| 4 | 注释标题必须是**目标工程里存在**的那个 | 注释标题不存在 |

---

## 6. 生成器加固清单

| 位置 | 加固内容 |
|---|---|
| `spec.py` `OP_OPERAND_LIMITS` | 按 FX5U 指令集校验操作数个数 |
| `spec.py` `_check_rungs()` | 抽出共用函数，**补上 `init`/`common` 段此前完全缺失的指令校验** |
| `spec.py` `_check_il_sequence()` | rung 内 `LD`/`LDI` 连续 → 报错 |
| `spec.py` station 段 | 禁止 `step.actions.instructions` 以 `LD`/`LDI` 开头（会覆盖步条件） |
| `spec.py` `project.comment_title(s)` | 注释标题可配置 + 校验 |
| `generator/build.py` | 每个 STL 步块配一个 RETSTL |
| `lint.py` | `STL`/`RETSTL` 数量不等告警 |
| `ilcsv/rows.py` | 注释 CSV 行尾改 LF；标题可配置；多列读取容错 |
| `docs/CSV_FORMAT.md` | 补 IL 序列规则 + 注释 CSV 两条约束 |

---

## 7. 自检工具：`gx_audit.py` / `gx3gen.audit`

按 FX5U 指令集从 CSV 反向解析（模拟 GX 导入时的检查）；核心在 `src/gx3gen/audit.py`，
`tools/gx_audit.py` 是命令行入口；生成器也会自动跑同一套检查（结果字段 `audit`）。

```powershell
$env:PYTHONIOENCODING = "utf-8"
python tools/gx_audit.py "<产物目录>\program.csv" --check "<产物目录>\comments.csv" `
  --expect-title "Chinese Simplified/简体中文"

# 或统一走 CLI
python run.py audit --program "<产物目录>\program.csv" --comments "<产物目录>\comments.csv"
```

检查项：文件格式 / 操作数个数 / rung 内 IL 序列 / STL-RETSTL 配对 / 注释标题与行尾。
`--expect-title` 可选：给了才校验目标工程注释标题名；不给只检查标题行非空。

**回归验证**（对本次各历史版本）：

| 版本 | 内容 | 检出 |
|---|---|---|
| `173031` | 原始版 | 8 项：4×SET/RST 多操作数 + 3×LDI 误用 + STL 配对不符 ✓ 与 GX 报错一致 |
| `174039` | 修 SET/RST 后 | 4 项：3×LDI 误用 + STL 配对不符 ✓ |
| `174921` | 修 LDI + STL 配对后 | 0 项 ✓ |
| `181949` | 最终版 | 0 项 ✓ |

> 注：审计脚本最初的版本把全文件当成一条连续指令序列检查 IL，导致**每个 rung 开头
> 的 `LD` 被误报**。修正方式是用注解行划分 rung 后才逐 rung 检查。

---

## 8. 经验教训

1. **GX 的报错信息有层级**：「指令不正确」是**逐条指令**的内容错误（会给行号）；
   「梯形图配置不正确」是**重建梯形图**时的结构错误（不给行号，只能靠推理 + 自检脚本定位）。
2. **拿到真实导出样例价值极高**：前两轮靠推理，第四轮拿到用户从当前工程导出的注释
   CSV，直接定位到「注释标题名」这个根本原因——搜索和文档都查不到这个值。
3. **CSV 行尾这类"看不见的差异"必须逐字节比**：肉眼看到的结构完全一致，只有
   `crlf_ratio` 这种指标能暴露问题。
4. **不要"多写几个候选"绕过校验**：GX 是逐列匹配的，多余列会各自报错。
5. **踩过的坑要固化成校验**，而不是只在本次 spec 里改对——否则下次换个 agent、
   换个项目还会再犯。
6. **生成器自己的盲区也要查**：本次发现的 `init`/`common` 段完全没做指令校验，
   就是靠这次故障暴露出来的。

---

## 9. 相关文件

- `docs/CSV_FORMAT.md` — GX CSV 格式规范（含本次补充的硬约束）
- `src/gx3gen/audit.py` / `tools/gx_audit.py` — 导入前自检（生成器自动调用）
- `tests/test_audit.py`、`tests/test_spec.py` — 本次约束的回归测试
- `src/gx3gen/spec.py` — ProgramSpec 校验（含全部新增规则）
- `src/gx3gen/ilcsv/rows.py` — 注释 CSV 写出（LF 行尾 + 标题）
- `src/gx3gen/generator/build.py` — STL/RETSTL 配对
- 项目 lessons：3 条 feedback 记录（`f-20261028174242-1c5338` / `f-20261028175131-4598cc` / 注释格式一条）
