# 模块接口契约（实现必须遵守，避免并行开发踩接口）

> 所有模块只依赖 Python 3.9 标准库。公共包名 `gx3gen`，源码在 `src/`（运行前需 `PYTHONPATH=src`，或使用 `python -m gx3gen.cli` 时通过 `src` 目录内包结构；测试统一把 `src` 加进 `sys.path`）。

## gx3gen.config

```python
DEFAULT_CONFIG: dict   # {"llm": {"base_url": "", "api_key": "", "model": "", "timeout": 120}, "ui": {"host": "127.0.0.1", "port": 8765}}
def config_path() -> str
def load_config() -> dict
def save_config(cfg: dict) -> None
def workspace_root() -> str     # 环境变量 GX3GEN_WORKSPACE 优先，默认 <repo>/workspace
```

## gx3gen.workspace

```python
def list_projects() -> list[dict]
def create_project(name: str) -> dict
def load_project(project_id: str) -> dict          # FileNotFoundError
def save_project(project: dict) -> None
def project_dir(project_id: str) -> str
def artifact_path(project_id: str, filename: str) -> str
def list_styles() -> list[dict]
def save_style(profile: dict) -> dict              # 分配 id、created_at
def load_style(style_id: str) -> dict | None
```

`project` 结构：

```json
{
  "id": "p-20261027-xxxx", "name": "阀体线", "created_at": "...",
  "requirement": "中文需求原文", "io": [{"device":"X0","comment":"...","role":"input"}],
  "spec": null, "style_profile": null,
  "outputs": {"program_csv": "...", "comment_csv": "...", "network_table": "...", "report": "..."},
  "stats": {}, "feedback": [], "corrected_specs": []
}
```

## gx3gen.spec

```python
class SpecError(ValueError): ...
def decode_text(data) -> str               # bytes/str；BOM 自动识别 UTF-16LE/BE、UTF-8、GBK
def load_spec(path: str) -> dict
def validate_spec(spec: dict) -> None      # 不合法抛 SpecError（含字段路径）
def default_spec() -> dict
def parse_io_csv(text: str) -> list[dict]  # 列：序号,软元件名,注释,类型,原位位置（顺序不敏感，有表头）
def io_map(spec: dict) -> dict[str, dict]  # 设备 -> io 行
def lookup_device(spec: dict, token: str) -> str | None  # 设备号或注释 → 设备号
```

ProgramSpec 结构见 `spec/program_spec.schema.json`。生成器只保证下列字段语义：

- `project.name/cpu/gx_version/style_profile`
- `io[]`
- `modes{}`（run/stop/auto/manual/reset 等设备）
- `segments[]`：`title`、`kind`（`init|station|common|rungs|statement`）、
  - `station`：`step_range:[start,end]`、`steps[]`（`step:int`、`actions[]`、`transition{}`）、
    可选 `entry_conditions[]`（进入条件，生成器会生成 `SET 首步` 网络）、可选 `handshake`
  - `rungs`：`rungs[]{comment?, instructions[{op,operands[]}]}`
  - `statement`：`statements[]`
- `alarms{enabled, base, timer_base, cylinders[]}`
- `assumptions[]`

Action 结构：`{comment?, set?:[dev], reset?:[dev], out?:[dev], instructions?:[{op,operands}]}`。
Transition：`{conditions:[dev], next:int|null, comment?}`。

## gx3gen.ir

```python
@dataclass Instruction: op: str; operands: list[str] = []
@dataclass Rung: instructions: list[Instruction]; comment: str = ""
@dataclass Segment: title: str; kind: str; rungs: list[Rung]; step_range: tuple|None = None
@dataclass Program: project: str; cpu: str; segments: list[Segment]
    def rungs(self) -> list[Rung]              # 平铺
    def all_instructions(self) -> list[Instruction]
```

## gx3gen.ilcsv

`rows.py`：

```python
CSV_HEADER = ["步号","行间声明","指令","I/O(软元件)","空白栏","PI声明","注解"]
@dataclass CsvRow: step=""; decl=""; instr=""; io=""; blank=""; pi=""; note=""
@dataclass ProgramCsv: project=""; cpu="FX5U"; rows=list
def read_program_csv(path_or_text) -> ProgramCsv
def write_program_csv(prog: ProgramCsv, path: str) -> None
def read_comment_csv(path_or_text) -> dict[str, str]     # 设备 -> 注释
def write_comment_csv(project: str, comments: dict[str, str], path: str) -> None
def iter_instructions(prog: ProgramCsv) -> list[dict]    # {step, decl, op, operands, note, line_no}
```

`emitter.py`：

```python
def emit(program: gx3gen.ir.Program) -> ProgramCsv
def emit_csv(program, program_csv_path, comment_csv_path, comments: dict[str,str]) -> dict
    # 返回 {"rows": int, "instructions": int, "segments": int, "steps": int}
```

## gx3gen.generator

```python
def build_program(spec: dict, style: dict | None = None) -> gx3gen.ir.Program
def generate(spec: dict, style: dict | None, out_dir: str, project_name: str | None = None,
             requirement: str = "", force: bool = False) -> dict
    # 写 4 个文件：program.csv / comments.csv / network_table.md / report.md
    # 生成前跑 quality.check_quality(spec, requirement)；有 errors 且 force=False 时抛 QualityError
    # 返回 {"program_csv","comment_csv","network_table","report","stats":{...},"summary":str,
    #        "assumptions":[...], "lint":[...], "quality":{...}, "segments":[...]}
    # 注意：app/API 同时支持契约中文名（程序本体.csv/软元件注释.csv）的下载别名。
```

## gx3gen.quality

```python
class QualityError(RuntimeError): quality: dict   # .quality 带详细检查结果
def check_quality(spec: dict, requirement: str = "") -> dict
    # {"ok": bool, "errors": [str], "warnings": [str], "suggestions": [str],
    #  "stats": {segments, station_steps, instructions, has_cycle, has_timer, has_start_stop, lamps, outputs}}
def quality_text(quality: dict) -> str
```

## gx3gen.domains

```python
def detect_domain(requirement: str) -> str | None       # 当前支持 "traffic_light"
def build_traffic_light_spec(requirement: str, io, name: str | None = None) -> dict
def build_domain_spec(domain: str, requirement: str, io, name: str | None = None) -> dict
```

## gx3gen.mcp_server（agent-first 入口）

stdio + 一行一条 JSON-RPC；由 `run_mcp.py` 启动。工具：
`gx3gen_get_generation_context`、`gx3gen_validate_spec`、`gx3gen_generate_program`、
`gx3gen_plan`、`gx3gen_get_examples`、`gx3gen_list_styles`、`gx3gen_import_style`、
`gx3gen_add_feedback`、`gx3gen_get_lessons`。调用说明见 `AGENTS.md` 与 `skills/gx3gen-plc/`。

## gx3gen.planner

```python
class PlannerError(RuntimeError): ...
def plan(project: dict, config: dict, use_llm: bool = False) -> dict
def plan_with_quality(project: dict, config: dict | None = None,
                      use_llm: bool = False) -> dict
    # {"spec": spec, "quality": {...}, "planner": "llm"|"deterministic"}
def plan_deterministic(requirement: str, io: list[dict], style: dict | None, lessons: list[str]) -> dict
    # 领域模板优先（双向红绿灯），否则走半结构化解析；完全提不出动作时抛 PlannerError
def plan_with_llm(requirement: str, io: list[dict], style: dict | None, lessons: list[str], config: dict) -> dict
    # prompt 含架构/质量红线 + 红绿灯样例；parse 后跑 quality，失败重试 1 次
```

## gx3gen.feedback

## gx3gen.style

`profile.py`：

```python
PROFILE_VERSION = "0.1"
def empty_profile(name: str = "") -> dict
def summarize(profile: dict) -> str          # 供 UI/规划器使用的中文摘要（<= 1200 字）
def merge_profiles(profiles: list[dict]) -> dict
```

`miner.py`（负责实现）：

```python
def mine(program_csv_text: str, comment_csv_text: str | None, name: str) -> dict  # StyleProfile
```

`apply.py`（负责实现）：

```python
def generator_defaults(profile: dict | None) -> dict
# 键：segment_order(list[str]) / step_base(int) / step_stride(int) /
#     modes(dict[str,str]) / alarm_base(int) / alarm_timer_base(int) / handshake_base(int) /
#     naming(dict[str,list[str]]) 
def planner_hint(profile: dict | None) -> str
```

## gx3gen.planner

```python
class PlannerError(RuntimeError): ...
def plan(project: dict, config: dict, use_llm: bool = False) -> dict
def plan_with_quality(project: dict, config: dict | None = None,
                      use_llm: bool = False) -> dict
    # {"spec": spec, "quality": {...}, "planner": "llm"|"deterministic"}
def plan_deterministic(requirement: str, io: list[dict], style: dict | None, lessons: list[str]) -> dict
    # 领域模板优先（双向红绿灯），否则走半结构化解析；完全提不出动作时抛 PlannerError
def plan_with_llm(requirement: str, io: list[dict], style: dict | None, lessons: list[str], config: dict) -> dict
    # prompt 含架构/质量红线 + 红绿灯样例；parse 后跑 quality，失败重试 1 次
```

## gx3gen.feedback

`store.py`：

```python
def add(project_id: str, target: dict, category: str, severity: str, message: str) -> dict
def list_records(project_id: str) -> list[dict]
def lessons(project_id: str) -> list[dict]      # [{category,count,last_message,examples:[]}]
def save_correction(project_id: str, spec: dict, note: str) -> dict
def corrections(project_id: str, limit: int = 3) -> list[dict]
```

`optimize.py`：

```python
def apply_lessons(project: dict) -> dict
    # 返回 {"spec": spec, "checklist": [str], "prompt_hint": str}
```

## gx3gen.app

`server.py`：

```python
def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer
def main(host: str = "127.0.0.1", port: int = 8765) -> None
```

HTTP API（JSON，除静态文件与下载）：

```
GET  /api/state                          -> {config(无 key), projects, styles}
POST /api/projects                       {name}
GET  /api/projects/<id>
PUT  /api/projects/<id>                  {requirement?, io?, spec?, style_profile?}
POST /api/plan                           {project_id, use_llm?} -> {spec, planner, quality, checklist, prompt_hint}
POST /api/generate                       {project_id, force?} -> {outputs, stats, summary, quality, artifacts}
    # 质量不通过返回 400 {error, quality:{errors:[...]}}；force=true 可强制生成（不推荐）
GET  /api/projects/<id>/artifact/<name>  -> 文件下载
POST /api/feedback                       {project_id, target, category, severity, message, expected?}
GET  /api/feedback?project_id=<id>
POST /api/optimize                       {project_id} -> {spec, checklist, prompt_hint}
POST /api/style/import                   {name, files:[{filename, base64}]} -> profile
GET  /api/styles ; GET /api/styles/<id>
GET/POST /api/config                     LLM 配置（GET 去掉 api_key）
```

## 测试约定

- 一律 `unittest`（本机无 pytest）。
- 测试不访问网络、不依赖外部语料目录、不写 `workspace/`（用临时目录，通过 `GX3GEN_WORKSPACE` 环境变量重定向）。
- 至少覆盖：CSV 格式（BOM/引号/续行/END）、spec 校验、生成器段/STL 结构、注释表、风格画像、反馈 lessons、HTTP API 冒烟。
