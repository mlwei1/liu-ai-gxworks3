# 风格学习（StyleProfile）

## 输入

用户在 GX Works3 中导出：
- `程序本体.csv`（一个程序一个文件）
- `软元件注释.csv`（同工程）

可一次导入多组（多项目/多程序），合并成一个画像。

## 输出（StyleProfile 摘要）

```json
{
  "profile_version": "0.1",
  "name": "用户风格-A",
  "source_files": ["12程序本体.csv", "12全局软元件注释.csv"],
  "segments": [{"order": 1, "title": "INIT", "network_count": 8}, ...],
  "step_ranges": [{"segment": "SQ1四联", "start": 100, "end": 190, "count": 9}],
  "modes": {"run": {"device": "M500", "comment": "运行中"}, "...": {}},
  "alarm": {"base": 2000, "timer_base": 10, "naming": ["气缸报警：{name}原位{sensor}"]},
  "handshake": {"naming": ["{src}->{dst}/{action}"], "examples": ["M5000 夹爪->压紧片装配/放料完成"]},
  "naming": {"segment_prefixes": ["SQ", "INIT", "报警"], "comment_patterns": [...]},
  "device_ranges": {"X": {"min": 0, "max": 7, "count": 8}, "Y": {...}, "M": {...}},
  "instruction_mix": {"LD": 1200, "AND": 900, "SET": 500, "...": 0},
  "comment_stats": {"max_len": 38, "cjk_ratio": 0.92, "has_fullwidth": true},
  "step_strategy": {"station_start": 100, "station_stride": 100, "increment": 10},
  "templates": [{"kind": "stl_step", "pattern": ["STL", "LD+SET", "LD+AND+SET"], "count": 120}]
}
```

## 用途

1. **生成器默认值**（`style/apply.py`）：
   - 默认段顺序（初始化 → 设备段 → 报警 → 手动 → 显示…）
   - 每个设备段的 SFC 步号段分配（start/stride）
   - 模式位映射（运行中/停止中/自动/手动）
   - 报警基址、定时器基址
   - 握手信号命名模板与基址
   - 注释命名风格（全角/半角、`->`、`/`、`SQx` 前缀等）
2. **规划器提示**（`planner_hint`）：把画像压缩成 ≤1200 字中文摘要，注入 LLM/规则解析。
3. **UI 展示**：让用户看到软件"学到了什么"，并可手动修正/确认。

## 原则

- 只学统计规律、命名模板、结构模板，不复制用户具体逻辑到别的项目。
- 画像可手工编辑（JSON）并可合并。
- 反馈会更新画像（例如用户说"段顺序应为 启动停止 → 设备段 → 报警"，则更新 segment_order）。
