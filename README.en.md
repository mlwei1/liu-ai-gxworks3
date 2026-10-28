# gx3gen — Mitsubishi GX Works3 PLC AI Programming Toolkit

**English** | [中文](README.md)

> Agent-first toolkit for **Mitsubishi FX5U + GX Works3 1.082** PLC programming:
> turn a Chinese/English requirement + IO list (+ your existing programming style) into a
> **readable, editable, GX Works3-importable** ladder program draft
> (device-segment + SFC/STL style: program-body CSV + device-comment CSV + network table + report).

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#requirements)
[![Tests](https://img.shields.io/badge/tests-162%20passing-brightgreen.svg)](#tests)
[![MCP](https://img.shields.io/badge/MCP-stdio-7b61ff.svg)](run_mcp.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Recommended way to use it:** clone/download this repository → open it with an AI agent
(Claude Code / DeepSeek Harness / Cursor / Codex …) → connect the agent to the bundled MCP server →
the agent writes the structured `ProgramSpec`, validates it, generates the CSV files and reports the
results. You only describe the requirement and provide the IO list.

> **No API key required.** The AI coding tool that opens this repository already has a model — that is
> the AI that writes the program. This project does not call an LLM: it provides the contract
> (`ProgramSpec`), the tools (MCP/CLI), the quality gate and the deterministic compiler.
> The optional LLM settings are only for standalone web/CI usage without an agent.

> The AI never writes GX CSV or binary directly. It produces a structured `ProgramSpec`;
> a deterministic compiler renders official GX Works3 CSV files. A quality gate rejects
> trivial outputs (e.g. "one empty STL step"). Compile errors and final logic are completed by
> an engineer in GX Works3.

---

## What's inside

| Layer | Files | Purpose |
|---|---|---|
| **Agent integration** | [`AGENTS.md`](AGENTS.md), [`skills/gx3gen-plc/`](skills/gx3gen-plc/), [`run_mcp.py`](run_mcp.py) | MCP server (13 tools) + skill + agent handbook; works with any MCP-capable agent |
| **Planning layer** | [`src/gx3gen/planner/`](src/gx3gen/planner/) | Rule parser, domain templates; the in-project LLM is only an **optional standalone adapter** (not used in agent mode) |
| **Quality gate** | [`src/gx3gen/quality.py`](src/gx3gen/quality.py) | Structural checks + requirement red lines; blocks junk before generation |
| **GX import audit** | [`src/gx3gen/audit.py`](src/gx3gen/audit.py), [`tools/gx_audit.py`](tools/gx_audit.py) | Post-generation/pre-import checks: BOM, line endings, operand counts, IL sequence, STL-RETSTL, comment title |
| **Domain templates** | [`src/gx3gen/domains.py`](src/gx3gen/domains.py) | Built-in two-way traffic light template (6 phases, timers, loop, start/stop) |
| **Deterministic compiler** | [`src/gx3gen/generator/`](src/gx3gen/generator/), [`src/gx3gen/ilcsv/`](src/gx3gen/ilcsv/) | `ProgramSpec` → segments/STL → IL → official GX CSV (UTF-16LE+BOM, TAB, QUOTE_ALL, CRLF, 7 cols, END) |
| **Style learning** | [`src/gx3gen/style/`](src/gx3gen/style/) | Learn segment structure, step ranges, mode bits, alarms, handshake naming from your own GX CSVs |
| **Feedback loop** | [`src/gx3gen/feedback/`](src/gx3gen/feedback/) | User corrections → lessons / checklist / prompt hints → used in the next generation |
| **Local web UI (experimental)** | [`src/gx3gen/app/`](src/gx3gen/app/), [`web/`](web/) | **Not the main flow**: optional review/visualization/artifact browser; pure-chat users never need it, and it needs no API key |
| **CLI** | [`src/gx3gen/cli.py`](src/gx3gen/cli.py), [`run.py`](run.py) | Headless batch/automation |

![pipeline](docs/architecture.svg)

---

## Requirements

- Windows with Mitsubishi **GX Works3 1.082** installed (for actual import/compile; generation itself does not need it)
- **Python 3.9+**, **no third-party packages** (standard library only; `pytest` is not required)
- Any MCP-capable AI client for the recommended pure-chat workflow (CLI is the fallback when MCP is unavailable)

---

## Quick start

```bash
git clone https://github.com/mlwei1/liu-ai-gxworks3.git
cd liu-ai-gxworks3
python -m unittest discover -s tests -t .     # 162 tests, no network
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
   `gx3gen_get_generation_context` → write `ProgramSpec`
   → optionally `gx3gen_create_project` + `gx3gen_save_spec` (persist to workspace)
   → `gx3gen_validate_spec` → iterate on `quality.errors` → `gx3gen_generate_program`
   → report artifacts, quality, assumptions and GX Works3 landing steps.
4. Answer follow-up questions (timings, IO addresses, interlocks). The agent must not invent devices.
5. Pure chat is enough for the whole loop; **you do not need to open a web UI**. If you want human
   review/diff/download, the experimental web UI reads the same `workspace/projects/<id>/spec.json`.

Available MCP tools:

| Tool | Purpose |
|---|---|
| `gx3gen_get_generation_context` | Required first read: ProgramSpec schema, architecture defaults, quality red lines, examples, IO, style summary, lessons |
| `gx3gen_validate_spec` | Validate structure + run the quality gate |
| `gx3gen_generate_program` | Generate the 4 artifacts (program CSV, comment CSV, network table, report) and run a GX import audit |
| `gx3gen_audit_csv` | Pre-import audit (format/operands/IL/STL/comment title); can check external CSVs |
| `gx3gen_create_project` / `gx3gen_save_spec` / `gx3gen_load_spec` / `gx3gen_list_projects` | Project + `spec.json` handoff (agent and optional web UI share the same workspace) |
| `gx3gen_get_examples` | Traffic-light / three-station examples |
| `gx3gen_list_styles` / `gx3gen_import_style` | Manage style profiles learned from your GX CSVs |
| `gx3gen_add_feedback` / `gx3gen_get_lessons` | Feedback loop |

> The MCP toolset contains **no in-project LLM/planning tool**: the agent is the planner;
> MCP never reads API keys and makes no network calls.

Pure-chat workflow (generation / style import / corrections / GX landing): [docs/CHAT_WORKFLOW.md](docs/CHAT_WORKFLOW.md).
Protocol-level example: [docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md).

### B. CLI (fallback when there is no MCP)

```bash
python run.py plan --req samples/traffic_light.requirement.md --io samples/traffic_light.io.csv --out out/spec.json
python run.py gen  --spec out/spec.json --out ./out-demo
python run.py audit --program ./out-demo/program.csv --comments ./out-demo/comments.csv
python run.py style import --program-csv "program.csv" --comment-csv "comments.csv" --name "MyStyle"
```

> `plan` uses rules/domain templates by default; `plan --llm` is an optional non-agent mode.
> In agent mode the agent writes the ProgramSpec itself.

### C. Experimental: local web review UI (not the main flow)

> **Not required for the main flow; no API key needed.** This layer is only for human review,
> diff, CSV download and feedback capture. Pure-chat users can skip it entirely; it can be removed later.

```bash
python run.py serve          # open http://127.0.0.1:8765
python run.py seed-demo      # create a sample project
```

- Open an agent-created project to review its spec, generated artifacts and feedback.
- Requirement input and "rule parsing" remain available as an offline fallback.
- "Local LLM parsing" is an optional standalone mode; it is not part of the agent flow.

---

## How it works

```
requirement + IO list + style profile
        │
        ▼  AI agent (your AI coding tool, with its own model) produces a ProgramSpec (JSON)
        ▼  quality gate (auto flow / loop / timers / start-stop / traffic-light phases …)
        ▼  deterministic compiler → GX program-body CSV + comment CSV + network table + report
engineer in GX Works3: import CSV → Convert All → fix compile errors / complete logic → save
```

- **The agent is the only LLM**: no in-project model calls, no API key; MCP provides contracts,
  context, validation and compilation only.
- **Planning and rendering are separated**: the agent writes JSON; the compiler guarantees file formats.
- **Quality gate**: blocks structurally incomplete or obviously wrong outputs.
- **Domain templates**: two-way traffic lights work without any model.
- **Style learning / feedback**: generation follows your structure/naming; corrections feed the next round.
- **No simulation / auto-compile loop yet**: the output is a draft for an engineer.

### Example: two-way traffic light (rule template, no LLM, no style)

Input [`samples/traffic_light.requirement.md`](samples/traffic_light.requirement.md) produces:

```
3 segments / 28 networks / 92 instructions / ~261 steps
[Title]初始化 → [Title]启动停止 → [Title]自动流程
6 STL phases S10–S60 + 6 timers (K250/K30/K20/K200/K30/K20)
one RETSTL per STL step (6:6); last phase loops back to S10; quality.ok = true; audit.ok = true
```

See [`samples/traffic_light.spec.json`](samples/traffic_light.spec.json).

---

## GX Works3 landing steps

1. Confirm `audit.ok=true` in the generation result; re-check with
   `python run.py audit --program <program.csv> --comments <comments.csv>` if needed.
2. Open a copy of your base project (FX5U, MAIN program already exists).
3. Open the MAIN program body; place the cursor inside the program (not on the `END` row).
4. `[Edit] → [Import File]` and choose the generated `program.csv`.
5. Import the device comments via the device-comment window (`comments.csv`)
   (comment CSV must use LF and the title must match the target project).
6. `Convert All` (Shift+Alt+F4), fix compile errors/logic, then save.

> 2026-10-28 field test: a generated `program.csv` + `comments.csv` imported successfully into
> GX Works3 1.082. Root causes and fixes for common failures are documented in
> [docs/GX_IMPORT_TROUBLESHOOTING.md](docs/GX_IMPORT_TROUBLESHOOTING.md).
>
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
├── web/                      # experimental single-page review UI (no build, no CDN; not the main flow)
├── tests/                    # 162 unittest cases
└── workspace/                # runtime data (projects/styles/artifacts; git-ignored)
```

## Tests

```bash
python -m unittest discover -s tests -t .
# Ran 162 tests ... OK
```

Coverage includes: GX CSV encoding/quoting/continuation/END, ProgramSpec validation (incl. SET/RST
operand limits, LD/LDI sequence, comment titles), generator segments/STL/comment tables, GX import
audit, style mining, feedback lessons, traffic-light domain + quality gate, HTTP API, and MCP
protocol/tool tests.

### Optional: non-agent mode (standalone web / CI)

Only needed when you are **not** using an AI agent:

- the "Local LLM" endpoint in Web **Settings** (OpenAI-compatible; stored locally), and
- `python run.py plan --llm` in the CLI.

Both use the optional `planner/llm.py` adapter. **The agent path never uses it, never reads that
config, and needs no API key.**

## Status & boundaries

- Target: **FX5U / GX Works3 1.082**, single MAIN POU, device-style + comments, device-segment + STL.
- Not implemented: labels/FB/ST/FBD/SFC graphics, multi-CPU, simulation, automatic GX compile acceptance.
- Generated programs are **drafts**; assumptions must be reviewed by an engineer.
- **Real GX import has been verified**: on 2026-10-28 a press-test-stand project's `program.csv` +
  `comments.csv` imported successfully into GX Works3 1.082; compile errors after Convert All and final
  logic are still handled by an engineer. See [docs/GX_IMPORT_TROUBLESHOOTING.md](docs/GX_IMPORT_TROUBLESHOOTING.md).
- Quality gate is heuristic; it catches obvious junk, not logic errors. Generation also runs a GX import audit.

## More docs

- [AGENTS.md](AGENTS.md) — agent instructions
- [docs/CHAT_WORKFLOW.md](docs/CHAT_WORKFLOW.md) — **pure-chat main flow** (generation / style import / corrections / landing)
- [docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md) — protocol-level example + MCP setup
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture
- [docs/PRODUCT_LOOP.md](docs/PRODUCT_LOOP.md) — product loop
- [docs/CSV_FORMAT.md](docs/CSV_FORMAT.md) — GX Works3 CSV format
- [docs/GX_IMPORT_TROUBLESHOOTING.md](docs/GX_IMPORT_TROUBLESHOOTING.md) — real GX import troubleshooting
- [tools/gx_audit.py](tools/gx_audit.py) — pre-import audit
- [docs/STYLE_LEARNING.md](docs/STYLE_LEARNING.md) — style profiles
- [docs/FEEDBACK_LOOP.md](docs/FEEDBACK_LOOP.md) — feedback loop
- [docs/INTERFACES.md](docs/INTERFACES.md) — module interfaces
- [docs/STATUS.md](docs/STATUS.md) — implementation status

## License

This project is licensed under the **MIT License** — see [LICENSE](LICENSE).

Copyright (c) 2026 mlwei1

