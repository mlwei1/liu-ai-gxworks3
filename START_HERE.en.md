# Start Here: first successful run in 5 minutes

**English** | [中文](START_HERE.md)

> Human-facing onboarding. Agent rules are in [AGENTS.md](AGENTS.md);
> the skill is in [skills/gx3gen-plc/SKILL.md](skills/gx3gen-plc/SKILL.md).

## Three things to remember

1. **No API key is required.** The AI coding tool you use to open this repository already has a model —
   that model is the one that writes the program.
2. **No web UI is required.** The main flow is a chat with your agent; the local web UI is only an
   experimental review surface.
3. **The generated CSV is a draft.** It must be imported into GX Works3, converted, and finished by an engineer.

---

## 1. First run (recommended)

### Step 1: download and open

- Download the ZIP or `git clone` the repository.
- Open the **whole repository directory** in an AI coding tool (Claude Code / DeepSeek Harness / Cursor / Codex…).
- Make sure Python ≥ 3.9 is available (`python` or `py -3` on Windows).

### Step 2: say this to your agent

```text
Please read AGENTS.md and START_HERE.md, check the local Python environment,
then walk me through a two-way traffic-light example:
generate the program, run the GX import audit, and show me the artifact paths and GX landing steps.
No API key is needed.
```

The agent will check, generate and audit by itself. **You do not need to configure MCP or write JSON.**
If your client supports MCP, see [mcp.json.example](mcp.json.example); if it does not, the agent can use the CLI.

### Step 3: inspect the first artifacts

```text
workspace/out/...
├── program.csv        # GX program-body CSV
├── comments.csv       # device-comment CSV
├── network_table.md   # network table
└── report.md          # report (structure / quality / GX import audit)
```

Success criteria: `quality.ok = true`, `audit.ok = true`, and the agent reports the 4 artifact paths.

### Step 4: import into GX Works3 (manual)

1. Open a **copy of an FX5U base project** (MAIN program exists).
2. Open the MAIN program body; place the cursor inside the program (not on `END`).
3. `[Edit] → [Import File]` and select `program.csv`.
4. Import `comments.csv` in the device-comment window.
5. `Convert All` (Shift+Alt+F4), fix compile errors/logic, then save.

> CSV import only replaces an existing program body; always use a backup/copy.
> Real GX Works3 1.082 import has been verified; see
> [docs/GX_IMPORT_TROUBLESHOOTING.md](docs/GX_IMPORT_TROUBLESHOOTING.md) for common failures.

---

## 2. Four things you can do next

### A. Generate a new program

Describe the sequence, start/stop/emergency stop, auto/manual, interlocks, timings, alarms and IO list.
If you do not have an IO list, describe the devices you know; the agent writes guesses into `assumptions`.

### B. Learn from your existing programs

1. Export `program-body CSV` and `device-comment CSV` from GX Works3.
2. Tell the agent their paths, e.g.:

```text
Learn my style from C:\work\my-project\program.csv and comments.csv; call it "MyStyle".
```

> Current scope: import is for **style learning**, not for editing an existing `.gx3` project.

### C. Look up manuals

Manuals are for **lookup**, not for dumping whole PDFs into context.

```bash
python -m pip install -r requirements-manuals.txt   # optional, indexing only
python run.py manual add --path "docs/官方资料/your-manual.pdf" --model FX5U
python run.py manual list
```

Then ask: *"How many operands does ZRST take? Which manual page?"*
The agent searches and cites **manual / section / page**. Manuals and the index stay local and are git-ignored.

### D. Correct and iterate

Tell the agent what is wrong (missing interlock, timings, stop behavior, compile errors from GX).
It records feedback, updates the ProgramSpec, and regenerates.

---

## 3. What you need

| Item | Required? | Notes |
|---|---|---|
| AI coding tool | Yes | Chat is enough; MCP is optional |
| Python 3.9+ | Yes | Core has no third-party deps; only manual indexing needs optional PyMuPDF |
| GX Works3 + FX5U base project | For import | Not needed for generation |
| IO list | Recommended | Guesses go into `assumptions` |
| Your historical GX CSVs | Optional | Style learning |
| Manual PDFs | Optional | Lookup only; generation works without them |

---

## 4. FAQ

**Do I need an API key?** No. The project does not call an LLM; your agent's model does the planning.

**Do I have to configure MCP?** No. MCP is an enhancement; the CLI fallback works without it.

**Do I need the web UI?** No. It is optional and experimental.

**Is the generated program finished?** No. It is a draft; import/convert/fix in GX Works3 with an engineer.

**Why can't I find a manual?** Run `manual list` first; PDFs must be indexed. Some diagram/table text
in PDFs is embedded with special fonts and may need OCR; normal instruction/prose sections work.

---

## 5. Related docs

- [README.md](README.md) / [README.en.md](README.en.md) — product overview
- [AGENTS.md](AGENTS.md) — agent handbook
- [docs/CHAT_WORKFLOW.md](docs/CHAT_WORKFLOW.md) — pure-chat workflow
- [docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md) — MCP setup and full example
- [docs/MANUALS.md](docs/MANUALS.md) — manual knowledge base
- [docs/GX_IMPORT_TROUBLESHOOTING.md](docs/GX_IMPORT_TROUBLESHOOTING.md) — import troubleshooting
