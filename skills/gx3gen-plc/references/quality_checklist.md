# 质量检查清单（gate：不过 = 不交付）

生成前必须让 `gx3gen_validate_spec` 返回 **`ok=true` 且 `quality.ok=true`**。
`gx3gen_generate_program` 默认也会再跑一次质量检查，不过则返回 `ok=false + errors` 且不写文件
（除非显式 `force=true`，并且必须向用户说明风险）。

## A. 通用红线（所有程序）

| # | 检查项 | 判定方法 |
|---|---|---|
| A1 | 结构完整：≥1 个 init 段（`SM402→SET S0`）+ ≥1 个 station 段 + 启动/停止段 | `segments` 里 `kind` 统计 |
| A2 | station 段步骤 ≥3 且有实际动作；流程类需求不得只给 1–2 步 | 数 `steps`、看 `actions` |
| A3 | 每个 step 都有动作或有效转移条件；无空动作步 | `actions` 非空；有 `next` 时 `conditions` 非空 |
| A4 | 每个输出 Y 至少被 SET/RST/OUT 驱动一次 | 扫 spec 动作 + IO 输出 |
| A5 | 延时/定时器出现在转移条件里（`OUT Tn K` → 条件 `Tn`） | 扫 instructions 与 transition |
| A6 | 最后一步 `next=null`（回待机）或指回首步（自动循环） | 看最后一步 transition |
| A7 | 停止/急停能复位运行模式位（M500/M521） | common 段有对 run/auto 的 RST |
| A8 | 有报警需求时：每气缸有到位/原位报警位与延时定时器，并有清除路径 | `alarms.enabled/cylinders/reset_device` |
| A9 | 步号段不重叠；定时器/报警位地址不冲突 | `lint_program` 的 STEP_RANGE_OVERLAP / 地址检查 |
| A10 | X/Y 注释覆盖；设备都在 IO 表或配置中 | `lint` 的 COMMENT_COVERAGE / UNDEFINED_DEVICE（warning） |
| A11 | `assumptions` 写全所有猜测，并在回复里逐条告诉用户 | 通读 assumptions |

## B. 双向红绿灯专属红线（缺一不可）

| # | 检查项 | 判定方法 |
|---|---|---|
| B1 | **≥6 相位**：东西绿 → 东西黄 → 全红 → 南北绿 → 南北黄 → 全红（回到相位 1） | 6 个 station steps，最后一步 next 指回首步 |
| B2 | 每个相位都有定时器（T0 起，`OUT Tn K`，K=100ms 基准） | 6 个 `instructions` OUT T |
| B3 | 时长合理：绿 8–10s（K80–K100）、黄 3s（K30）、全红 1s（K10） | 核对 K 值；需求给出时长则按需求 |
| B4 | **互锁**：任一方向绿灯时另一方向必须红灯；同方向绿+黄不同时亮 | 每相位 set/reset 覆盖 6 路灯 |
| B5 | 自动循环 + 启停：M500 运行、M501/停止复位、M521 自动；停止后所有灯回安全状态 | common 段 SET/RST + 复位灯 |
| B6 | 至少 3 个灯输出（红/黄/绿），且 IO 注释含方向（东西/南北） | `_lamp_outputs` 识别 |

## C. 工位序列（取放/装配/压装）补充

- 每个工位段：`entry_conditions` 明确（如 `["S0","X0"]` 或上一工位完成信号）。
- 动作序列覆盖：下降/前进 → 夹紧/吸取 → 延时确认 → 上升/后退 → 松开/放料 → 回原位。
- 每个动作都有对应的到位/原位传感器作转移条件（没有传感器的写 assumptions 并以定时器兜底）。
- 多工位之间要有交接/握手信号（`handshake.out` 或显式设备），不要让下一段无条件直接进入。
- 停止/急停：复位运行位、复位所有输出、步号回待机；报警清除。
- 气缸报警：每个气缸都有 `on_sensor`/`home_sensor`，报警延时 2s 起步。

## D. 工具层检查（agent 自动执行）

1. `gx3gen_validate_spec` → `ok=true`（结构合法），`quality.ok=true`（红线通过）；
2. `gx3gen_generate_program` → 返回 `ok=true` 且 4 个产物路径都存在；
3. 检查返回的 `lint`：error 必须消灭；warning 逐条判断是否可接受（写进回复）；
4. 对照 `stats`：segments/instructions/steps 是否与需求规模相符；
5. 回复用户必须包含：产物路径、段结构、quality/lint、assumptions、GX 落地步骤。

## E. 质量不达标时怎么办

| 情况 | 动作 |
|---|---|
| 缺相位/步数不足 | 按需求补 phase/step，不要删检查或缩水 |
| 缺定时器 | 在相位/等待步加 `OUT Tn K` 并把 `Tn` 写进 transition.conditions |
| 缺启动/停止 | 在 common 段补 `LD X0 → SET M500/M521`、`LD X1 → RST ...` |
| 缺报警 | 在 `alarms.cylinders` 补气缸（output/on_sensor/home_sensor） |
| 设备不确定 | 写 `assumptions` 并问用户，不要发明设备号 |
| 需求本身有歧义（时长/手自动/急停方式） | **停下来问用户**，不要硬凑一版“看起来完整”的程序 |
