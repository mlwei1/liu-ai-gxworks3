# 手册知识库（官方手册 / 用户上传手册）

> 目标：**手册是用来“查”的，不是用来“读”的。**
> 项目不会把整本 PDF 放进 AI 上下文，而是建一个本地可检索索引；agent 按需查询，
> 只拿 top-k 片段和出处（手册/章节/页码），回答用户时标注引用。

## 1. 它是什么

```
docs/官方资料/*.pdf 或用户自己的 PDF/DOCX/MD/TXT
        │  python run.py manual add
        ▼
workspace/manuals/
  manuals.sqlite3     # SQLite：手册 / 章节 / 片段 / FTS5 全文索引
  manifest.json       # 可读的手册清单
        │  查询（只用标准库 sqlite3）
        ▼
CLI / MCP 工具 / AI agent 对话
  manual_list → manual_toc → manual_search → manual_read
```

- 手册原文/索引都在本地，**不上传网络**；
- `docs/官方资料/` 和 `workspace/manuals/` 都不提交 git（版权 + 体积）；
- 查询零第三方依赖；只有“建索引”需要可选 PDF 解析器。

## 2. 安装可选依赖

```bash
python -m pip install -r requirements-manuals.txt   # pymupdf，仅建索引需要
```

没有安装时，`manual add` 会给出明确提示；已经建好的索引仍可正常查询。

## 3. 索引手册

```bash
# 单个文件（PDF / MD / TXT / DOCX）
python run.py manual add --path "docs/官方资料/xxx.pdf" --title "FX5 编程手册(指令/通用FUN/FB篇)" --model FX5U

# 只索引前 N 页（调试/快速验证）
python run.py manual add --path "xxx.pdf" --max-pages 50

# 新版本：归档同标题旧版本
python run.py manual add --path "新版.pdf" --replace

# 查看 / 删除（删除只删索引，不删原文件）
python run.py manual list
python run.py manual remove <manual_id>
```

也可以把新手册放到 `workspace/manuals/inbox/` 后让 agent 执行上面的命令。

## 4. 查询

```bash
# 搜索（默认 5 段，输出手册/章节/页码 + 片段）
python run.py manual search "SET 操作数"
python run.py manual search "高速计数器 最大频率" --top-k 3
python run.py manual search "电源规格" --model FX5U --json

# 看目录 → 读指定章节
python run.py manual toc <manual_id> --depth 3
python run.py manual read <manual_id> --section 1234 --max-chars 6000
python run.py manual read <manual_id> --page 238
```

索引内容包括：PDF 书签目录（结构感知切块）、Markdown 标题、TXT 段落、DOCX 标题。
每个片段带 `manual / heading_path / page_start-page_end`，用于回答时引用。

## 5. Agent 怎么用（MCP）

MCP 新增 4 个工具：

| 工具 | 作用 |
|---|---|
| `gx3gen_manual_list` | 列出已索引手册（id/标题/页数/机型/版本） |
| `gx3gen_manual_search` | 搜索 top-k 片段（`{query, manual_id?, model?, top_k?}`） |
| `gx3gen_manual_toc` | 看某本手册目录 |
| `gx3gen_manual_read` | 读指定 `section_id` 或 `page`（带出处） |

AGENTS.md 中的规则：

- 手册是**查**的，不是读的；**不要**用文件读取工具去读整本 PDF；
- 查指令/设备/错误码/模块用法时先 `manual_search`，必要时再 `manual_read`；
- 回答必须给 `手册名 + 章节 + 页码`；
- 没有索引时告诉用户怎么建库，不要凭空猜手册内容。

`gx3gen_get_generation_context` 会返回“已索引手册/未索引”的提示，但**不会把手册正文塞进上下文**。

## 6. 设计要点

- **结构感知切块**：优先按 PDF 书签目录切 section，保留标题路径和页码范围；过长 section 再按段落切成带 overlap 的 chunk。
- **中文检索**：FTS5 + 中文 bigram 预切词（`高速计数器` → `高速 速计 计数 数器`），不依赖 jieba。
- **指令符号加权**：查询含 `SET/ZRST/STL/MOV` 等大写符号时，精确匹配和“指令参考页”加权；附录/索引/步数表降权。
- **出处可核验**：命中片段带页码，用户可以回 GX 手册对应页确认。
- **增量**：按文件 sha256 去重；重复添加不会重复建索引；`--replace` 归档旧版。

## 7. 已知限制（Phase 1）

- PDF 中**图形/表格内部的文字**可能来自特殊字体，文本层提取会乱码甚至丢失（例如应用篇高速计数器规格表）；这类页需要 OCR（Phase 2）。
- `manual_read` 按章节/页返回正文；跨页长内容会截断，用 `max_chars` 调整。
- 目前是词法检索 + 规则加权；如果召回不够，后续可加本地 embedding 混合检索。
- 新手册需要手动 `manual add`；没有做 Web 上传（agent 可通过终端执行命令）。

## 8. 隐私与版权

- 官方手册/公司内部手册不要提交到仓库；`.gitignore` 已忽略 `docs/官方资料/` 和 `workspace/`。
- 索引只存在本机 `workspace/manuals/`；不会上传到任何服务。
- 若需要团队共享索引，请自行确认手册分发许可后，放到内部共享目录，不要把索引提交到公开仓库。
