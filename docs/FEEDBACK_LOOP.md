# 反馈闭环

## 用户动作

在软件"审阅"页看到生成结果（段 → 网络 → 指令/注释）后，对错误位置标注：

```json
{
  "target": {"kind": "network|segment|program", "segment": "入料站", "index": 3},
  "category": "结构|命名|逻辑|互锁|报警|停止|其他",
  "severity": "error|warning|suggestion",
  "message": "缺少压装站到检测站的交接信号",
  "expected": "M1110 压装->检测/送料完成"
}
```

## 软件处理

1. 写入项目 `feedback[]`（JSONL/JSON 内嵌）。
2. `feedback/optimize.py` 派生：
   - `lessons`：按类别聚合的高频问题 + 最近一次说明 + 例子；
   - `checklist`：本轮生成必须自检的清单；
   - `prompt_hint`：注入规划器提示词（LLM）与规则解析。
3. 用户点"根据反馈重新生成"时：
   - 把 lessons/checklist 加入生成前置条件；
   - 若用户同时保存了修正后的 spec，则保存为 `corrected_specs[]`，作为 LLM few-shot 与模板候选；
   - 生成报告列出"本轮已规避的历史问题"。
4. 风格画像联动：结构/命名类反馈可更新 StyleProfile（如段顺序、命名模板）。

## 不做

- 不自动训练模型；
- 不静默修改用户已有程序；
- 不把反馈当作绝对规则——只作为优先级更高的提示与检查项。
