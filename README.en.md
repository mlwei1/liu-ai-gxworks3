# gx3gen — Mitsubishi GX Works3 PLC AI Programming Toolkit

**English** | [中文](README.md)

> Agent-first toolkit for **Mitsubishi FX5U + GX Works3 1.082** PLC programming:
> turn a Chinese/English requirement + IO list (+ your existing programming style) into a
> **readable, editable, GX Works3-importable** ladder program draft
> (device-segment + SFC/STL style: program-body CSV + device-comment CSV + network table + report).

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#requirements)
[![Tests](https://img.shields.io/badge/tests-145%20passing-brightgreen.svg)](#tests)
[![MCP](https://img.shields.io/badge/MCP-stdio-7b61ff.svg)](run_mcp.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Recommended way to use it:** clone/download this repository → open it with an AI agent
(Claude Code / DeepSeek Harness / Cursor / Codex …) → connect the agent to the bundled MCP server →
the agent writes the structured `ProgramSpec`, validates it, generates the CSV files and reports the
results. You only describe the requirement and provide the IO list.

> The AI never writes GX CSV or binary directly. It produces a structured `ProgramSpec`;
> a deterministic compiler renders official GX Works3 CSV files. A quality gate rejects
> trivial outputs (e.g. "one empty STL step"). Compile errors and final logic are completed by
> an engineer in GX Works3.

---

## What's inside

| Layer | Files | Purpose |
|---|---|---|
| **Agent integration** | [`AGENTS.md`](AGENTS.md), [`skills/gx3gen-plc/`](skills/gx3gen-plc/), [`run_mcp.py`](run_mcp.py) | MCP server (9 tools) + skill + agent handbook; works with any MCP-capable agent |
| **AI planning** | [`src/gx3gen/planner/`](src/gx3gen/planner/) | Rule parser, domain templates, OpenAI-compatible LLM planner (quality-aware retry) |
| **Quality gate** | [`src/gx3gen/quality.py`](src/gx3gen/quality.py) | Structural checks + requirement red lines; blocks junk before generation |
| **Domain templates** | [`src/gx3gen/domains.py`](src/gx3gen/domains.py) | Built-in two-way traffic light template (6 phases, timers, loop, start/stop) |
| **Deterministic compiler** | [`src/gx3gen/generator/`](src/gx3gen/generator/), [`src/gx3gen/ilcsv/`](src/gx3gen/ilcsv/) | `ProgramSpec` → segments/STL → IL → official GX CSV (UTF-16LE+BOM, TAB, QUOTE_ALL, CRLF, 7 cols, END) |
| **Style learning** | [`src/gx3gen/style/`](src/gx3gen/style/) | Learn segment structure, step ranges, mode bits, alarms, handshake naming from your own GX CSVs |
| **Feedback loop** | [`src/gx3gen/feedback/`](src/gx3gen/feedback/) | User corrections → lessons / checklist / prompt hints → used in the next generation |
| **Local web UI** | [`src/gx3gen/app/`](src/gx3gen/app/), [`web/`](web/) | Optional single-page UI (stdlib HTTP, no CDN): projects, style, requirement, generate, review, feedback |
| **CLI** | [`src/gx3gen/cli.py`](src/gx3gen/cli.py), [`run.py`](run.py) | Headless batch/automation |

![pipeline](docs/architecture.svg)

---

## Requirements

- Windows with Mitsubishi **GX Works3 1.082** installed (for actual import/compile; generation itself does not need it)
- **Python 3.9+**, **no third-party packages** (standard library only; `pytest` is not required)
- Any MCP-capable AI client for the recommended agent workflow (or use the CLI/web UI directly)

---

## Quick start

```bash
git clone https://github.com/mlwei1/liu-ai-gxworks3.git
cd liu-ai-gxworks3
python -m unittest discover -s tests -t .     # 145 tests, no network
```

### A. AI agent + MCP (recommended)

1. Open the repository with your AI agent. The agent reads [`AGENTS.md`](AGENTS.md) and
   [`skills/gx3gen-plc/SKILL.md`](skills/gx3gen-plc/SKILL.md) automatically (agent handbooks).
2. Register the MCP server in your client using [`mcp.json.example`](mcp.json.example):
   ```json
   {
     "mcpServers": {
       "gx3gen": {
         "command": "python",
         "args": ["<absolute-path-to-repo>/run_mcp.py"],
         "env": { "GX3GEN_WORKSPACE": "<absolute-path-to-repo>/workspace" }
       }
     }
   }
   ```
3. Ask the agent in natural language, e.g. *"做一个双向红绿灯：启动后自动循环……"*.
   The agent will:
   `gx3gen_get_generation_context` → write `ProgramSpec` → `gx3gen_validate_spec`
   → iterate on `quality.errors` → `gx3gen_generate_program` → report artifacts, quality,
   assumptions and GX Works3 landing steps.
4. Answer follow-up questions (timings, IO addresses, interlocks). The agent must not invent devices.

Available MCP tools:

| Tool | Purpose |
|---|---|
| `gx3gen_get_generation_context` | Required first read: ProgramSpec schema, architecture defaults, quality red lines, examples, IO, style summary, lessons |
| `gx3gen_validate_spec` | Validate structure + run the quality gate |
| `gx3gen_generate_program` | Generate the 4 artifacts (program CSV, comment CSV, network table, report) |
| `gx3gen_plan` | Optional rule/LLM planning starting point |
| `gx3gen_get_examples` | Traffic-light / three-station examples |
| `gx3gen_list_styles` / `gx3gen_import_style` | Manage style profiles learned from your GX CSVs |
| `gx3gen_add_feedback` / `gx3gen_get_lessons` | Feedback loop |

See [docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md) for a full example session.

### B. Local web UI (optional)

```bash
python run.py serve          # open http://127.0.0.1:8765
python run.py seed-demo      # create a sample project (three-station pick & place)
```

Workflow tabs: **① Style → ② Requirement/IO → ③ Architecture confirmation → ④ Generate → ⑤ Review/Feedback → ⑥ Settings**.

- **Rule parsing** works offline and includes domain templates (two-way traffic light).
- **AI parsing** uses an OpenAI-compatible endpoint configured in **Settings** (`base_url`, `model`, `api_key`; stored locally).
  Even without an LLM the app still handles structured requirements and the built-in traffic-light template;
  unrecognized natural language now returns a clear error with rewriting suggestions instead of junk output.

### C. CLI

```bash
python run.py plan --req samples/traffic_light.requirement.md --io samples/traffic_light.io.csv --out out/spec.json
python run.py gen  --spec out/spec.json --out ./out-demo
python run.py style import --program-csv "program.csv" --comment-csv "comments.csv" --name "MyStyle"
python run.py serve
```

---

## How it works

```
requirement + IO list + style profile
        │
        ▼  AI agent / LLM produces a ProgramSpec (JSON, human-readable)
        ▼  quality gate (auto flow / loop / timers / start-stop / traffic-light phases …)
        ▼  deterministic compiler → GX program-body CSV + comment CSV + network table + report
engineer in GX Works3: import CSV → Convert All → fix compile errors / complete logic → save
```

- **Separate planning from rendering.** LLMs plan in JSON; the compiler guarantees file formats.
- **Quality gate** blocks empty/degenerate output before files are written.
- **Domain templates** cover common patterns (two-way traffic light) without any LLM.
- **Style learning** makes generation follow your own segment structure, step ranges, naming and
  handshake conventions; **feedback** records corrections and feeds the next iteration.
- **No simulation / auto-compile loop yet.** The generated program is a draft for an engineer.

### Example: two-way traffic light (rule template, no LLM, no style)

Input [`samples/traffic_light.requirement.md`](samples/traffic_light.requirement.md) produces:

```
3 segments / 23 networks / 87 instructions / ~256 steps
[Title]初始化 → [Title]启动停止 → [Title]自动流程
6 STL phases S10–S60 + 6 timers (K250/K30/K20/K200/K30/K20)
last phase loops back to S10; quality.ok = true
```

See [`samples/traffic_light.spec.json`](samples/traffic_light.spec.json).

---

## GX Works3 landing steps

1. Open a copy of your base project (FX5U, MAIN program already exists).
2. Open the MAIN program body; place the cursor inside the program (not on the `END` row).
3. `[Edit] → [Import File]` and choose the generated `program.csv`.
4. Import the device comments via the device-comment window (`comments.csv`).
5. `Convert All` (Shift+Alt+F4), fix compile errors/logic, then save.

> Official limitation: CSV import can only **replace an existing program body** (it cannot create a new POU/program),
> requires `END`, and the program stays "unconverted" until you run Convert. Always work on a backup/copy.

---

## Repository layout

```
.
├── AGENTS.md                 # Agent handbook (rules, workflow, quality red lines, tool table)
├── README.md / README.en.md  # Chinese / English docs
├── run.py / run_mcp.py       # CLI entry / MCP stdio entry
├── mcp.json.example          # MCP client config template
├── skills/gx3gen-plc/        # Agent skill (trigger words, routing, references)
├── docs/                     # architecture, product loop, CSV format, agent workflow, status …
├── spec/                     # ProgramSpec / StyleProfile JSON schemas
├── samples/                  # three-station + two-way traffic-light examples
├── src/gx3gen/               # Python package (planner, generator, ilcsv, style, feedback, app, MCP)
├── web/                      # single-page web UI (no build, no CDN)
├── tests/                    # 145 unittest cases
└── workspace/                # runtime data (projects/styles/artifacts; git-ignored)
```

## Tests

```bash
python -m unittest discover -s tests -t .
# Ran 145 tests ... OK
```

Coverage includes: GX CSV encoding/quoting/continuation/END, ProgramSpec validation, generator
segments/STL/comment tables, style mining, feedback lessons, traffic-light domain + quality gate,
HTTP API, and 28 MCP protocol/tool tests.

## Status & boundaries

- Target: **FX5U / GX Works3 1.082**, single MAIN POU, device-style + comments, device-segment + STL.
- Not implemented: labels/FB/ST/FBD/SFC graphics, multi-CPU, simulation, automatic GX compile acceptance.
- Generated programs are **drafts**; assumptions must be reviewed by an engineer.
- Real-GX import/Convert acceptance is still pending (see [docs/STATUS.md](docs/STATUS.md)).
- Quality gate is heuristic; it catches obvious junk, not logic errors.

## More docs

- [AGENTS.md](AGENTS.md) — agent instructions
- [docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md) — full example session + MCP setup
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture
- [docs/PRODUCT_LOOP.md](docs/PRODUCT_LOOP.md) — product loop
- [docs/CSV_FORMAT.md](docs/CSV_FORMAT.md) — GX Works3 CSV format
- [docs/STYLE_LEARNING.md](docs/STYLE_LEARNING.md) — style profiles
- [docs/FEEDBACK_LOOP.md](docs/FEEDBACK_LOOP.md) — feedback loop
- [docs/INTERFACES.md](docs/INTERFACES.md) — module interfaces
- [docs/STATUS.md](docs/STATUS.md) — implementation status

## License

This project is licensed under the **MIT License** — see [LICENSE](LICENSE).

Copyright (c) 2026 mlwei1

