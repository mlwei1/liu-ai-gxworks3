# web/ — 实验性可选审阅界面

> **这不是主流程。** 主流程是用户在 AI 编程工具里与 agent 纯对话完成
> （见仓库根 [docs/CHAT_WORKFLOW.md](../docs/CHAT_WORKFLOW.md)）。
>
> 本界面不需要 API Key，也不需要打开；它只是可选的人工审阅台。

## 用途

- 浏览 agent 创建的项目 / `spec.json` / 生成产物；
- 下载 `program.csv`、`comments.csv`、`network_table.md`、`report.md`；
- 记录反馈（写回项目 lessons）；
- 规则解析 / 本地 LLM 解析作为**没有 agent 时**的独立模式兜底。

## 启动

```bash
python run.py serve      # http://127.0.0.1:8765
python run.py seed-demo  # 可选：创建一个示例项目
```

## 状态

- 实验性：接口/界面可能随主流程调整；
- 如果只需要纯对话，可以完全不启动本目录；
- 本目录不属于 agent 主流程，也不参与生成/质量/风格核心逻辑。
