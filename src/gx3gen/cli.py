"""gx3gen 命令行入口。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from . import config as gxconfig
from . import workspace


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
def cmd_serve(args: argparse.Namespace) -> int:
    from .app.server import main as serve
    serve(args.host, args.port)
    return 0


def cmd_gen(args: argparse.Namespace) -> int:
    from .spec import load_spec, parse_io_csv
    from .generator import generate
    spec = load_spec(args.spec)
    if args.io:
        spec["io"] = parse_io_csv(Path(args.io).read_bytes())
    style = workspace.load_style(args.style) if args.style else None
    result = generate(spec, style, args.out, args.name)
    print(result["summary"])
    for key in ("program_csv", "comment_csv", "network_table", "report"):
        print("%-16s %s" % (key, result[key]))
    if result["assumptions"]:
        print("\n假设/待确认：")
        for item in result["assumptions"]:
            print("-", item)
    if result["lint"]:
        print("\n检查：")
        for item in result["lint"]:
            print("- [%s] %s" % (item.get("severity"), item.get("message")))
    audit = result.get("audit") or {}
    if audit:
        print("\nGX 导入自检：%s" % ("通过" if audit.get("ok") else "未通过"))
        for item in audit.get("problems") or []:
            print("-", item)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """导入 GX Works3 前自检 program.csv / comments.csv。"""
    from .audit import audit
    result = audit(args.program, args.comments,
                   expected_comment_title=args.expect_comment_title)
    for item in result["problems"]:
        print("!!", item)
    print("GX 导入自检：%s（%d 项问题）"
          % ("通过" if result["ok"] else "未通过", len(result["problems"])))
    return 0 if result["ok"] else 1


def cmd_manual(args: argparse.Namespace) -> int:
    """手册知识库：add/list/search/toc/read/remove。"""
    from .manual import (ManualError, add_manual, list_manuals, read,
                         remove_manual, search, toc)
    from .config import workspace_root
    command = getattr(args, "manual_command", "")
    try:
        if command == "add":
            result = add_manual(args.path, title=args.title, model=args.model or "",
                                version=args.version or "", replace=args.replace,
                                max_pages=args.max_pages)
            if result.get("skipped"):
                print("已存在，跳过：", result["manual"].get("title"))
                return 0
            print("已索引：%s" % result["title"])
            print("  id       :", result["manual_id"])
            print("  页数     :", result["pages"])
            print("  章节/片段:", result["sections"], "/", result["chunks"])
            print("  索引位置 :", Path(workspace_root()) / "manuals" / "manuals.sqlite3")
            return 0
        if command == "list":
            items = list_manuals()
            if not items:
                print("还没有已索引手册。先运行： python run.py manual add --path <手册.pdf>")
                return 0
            for item in items:
                print("%-28s p.%-4s %-6s %s" % (
                    item["id"], item["pages"], item["model"] or "-", item["title"]))
            return 0
        if command == "search":
            hits = search(args.query, manual_id=args.manual, model=args.model,
                          top_k=args.top_k)
            if args.json:
                print(json.dumps(hits, ensure_ascii=False, indent=2))
                return 0
            if not hits:
                print("没有命中。可先 python run.py manual list 查看已索引手册。")
                return 0
            for index, hit in enumerate(hits, start=1):
                print("[%d] %s" % (index, hit["citation"]))
                print("    ", hit["snippet"])
            return 0
        if command == "toc":
            items = toc(args.manual, depth=args.depth)
            if not items:
                print("该手册没有目录。")
                return 0
            for item in items:
                print("%5s %s%s  (p.%s-%s)" % (
                    "#%s" % item["id"], "  " * (max(1, item["level"]) - 1),
                    item["heading"], item["page_start"], item["page_end"]))
            return 0
        if command == "read":
            result = read(args.manual, section_id=args.section, page=args.page,
                          max_chars=args.max_chars)
            print("## %s" % result["heading_path"])
            print("出处：%s p.%s-%s" % (result["title"], result["page_start"], result["page_end"]))
            print()
            print(result["text"])
            if result["truncated"]:
                print("\n…（已截断；可用 --max-chars 调整）")
            return 0
        if command == "remove":
            result = remove_manual(args.manual)
            print("已从索引删除：", result["title"])
            return 0
    except ManualError as exc:
        print("手册知识库错误：", exc, file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print("文件不存在：", exc, file=sys.stderr)
        return 1
    print("未知 manual 子命令：", command, file=sys.stderr)
    return 2


def cmd_lint(args: argparse.Namespace) -> int:
    from .spec import load_spec
    from .generator.build import build_program
    from .generator.comments import collect_comments
    from .lint import lint_program
    spec = load_spec(args.spec)
    program = build_program(spec, None)
    comments = collect_comments(spec, program)
    issues = lint_program(program, spec, comments)
    if not issues:
        print("无告警")
        return 0
    for item in issues:
        print("[%s] %s (%s)" % (item.get("severity"), item.get("message"), item.get("code")))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    from .spec import parse_io_csv, validate_spec
    from .planner.plan import plan
    project = {
        "id": "cli",
        "name": args.name,
        "requirement": Path(args.req).read_text(encoding="utf-8"),
        "io": parse_io_csv(Path(args.io).read_bytes()) if args.io else [],
        "style_profile": args.style,
        "feedback": [],
        "spec": None,
    }
    spec = plan(project, gxconfig.load_config(), use_llm=args.llm)
    validate_spec(spec)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ProgramSpec ->", out)
    for segment in spec.get("segments") or []:
        print("-", segment.get("kind"), segment.get("title"))
    return 0


def cmd_style_import(args: argparse.Namespace) -> int:
    from .spec import decode_text
    from .style.miner import mine
    from .style.profile import summarize
    program_text = decode_text(Path(args.program_csv).read_bytes())
    comment_text = None
    if args.comment_csv:
        comment_text = decode_text(Path(args.comment_csv).read_bytes())
    profile = mine(program_text, comment_text, args.name)
    saved = workspace.save_style(profile)
    print("style id:", saved["id"])
    print(summarize(saved))
    return 0


def cmd_style_list(args: argparse.Namespace) -> int:
    for item in workspace.list_styles():
        print("%-28s %s (%s)" % (item.get("id"), item.get("name"), item.get("segment_count")))
    return 0


def cmd_project_create(args: argparse.Namespace) -> int:
    project = workspace.create_project(args.name)
    print(project["id"])
    return 0


def cmd_project_list(args: argparse.Namespace) -> int:
    for item in workspace.list_projects():
        print("%-28s %-20s spec=%s outputs=%s feedback=%s" % (
            item.get("id"), item.get("name"), item.get("has_spec"),
            item.get("has_outputs"), item.get("feedback_count")))
    return 0


def cmd_feedback_add(args: argparse.Namespace) -> int:
    from .feedback.store import add
    record = add(args.project, {"kind": "segment", "segment": args.segment},
                 args.category, args.severity, args.message)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    from .feedback.optimize import apply_lessons
    project = workspace.load_project(args.project)
    result = apply_lessons(project)
    print(result.get("prompt_hint") or "")
    for item in result.get("checklist") or []:
        print("-", item)
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    """创建示例项目并生成初稿，方便第一次打开 Web 软件时有内容可看。"""
    from .spec import load_spec, parse_io_csv
    from .generator import generate
    root = Path(__file__).resolve().parents[2]
    project = workspace.create_project(args.name or "三工位取放示例")
    project["requirement"] = (root / "samples" / "three_station.requirement.md").read_text(encoding="utf-8")
    project["io"] = parse_io_csv((root / "samples" / "three_station.io.csv").read_bytes())
    spec = load_spec(str(root / "samples" / "three_station.spec.json"))
    project["spec"] = spec
    out_dir = Path(workspace.project_dir(project["id"])) / "out"
    result = generate(spec, None, str(out_dir), project_name=project["name"])
    project["outputs"] = {key: Path(result[key]).name for key in
                          ("program_csv", "comment_csv", "network_table", "report")}
    project["stats"] = result["stats"]
    project["summary"] = result["summary"]
    project["segments"] = result["segments"]
    project["assumptions"] = result["assumptions"]
    project["lint"] = result["lint"]
    workspace.save_project(project)
    print("项目已创建：", project["id"])
    print(result["summary"])
    print("启动软件： python run.py serve   然后打开 http://127.0.0.1:8765")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .spec import load_spec
    from .generator import generate
    root = Path(__file__).resolve().parents[2]
    spec = load_spec(str(root / "samples" / "three_station.spec.json"))
    result = generate(spec, None, args.out)
    print(result["summary"])
    print(result["program_csv"])
    return 0


# ---------------------------------------------------------------------------
def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gx3gen", description="GX Works3 AI 程序初稿生成器")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动本地 Web 软件")
    serve.add_argument("--host", default=gxconfig.load_config()["ui"]["host"])
    serve.add_argument("--port", type=int, default=int(gxconfig.load_config()["ui"]["port"]))
    serve.set_defaults(func=cmd_serve)

    gen = sub.add_parser("gen", help="从 ProgramSpec 生成程序 CSV/注释/报告")
    gen.add_argument("--spec", required=True)
    gen.add_argument("--out", required=True)
    gen.add_argument("--name", default=None)
    gen.add_argument("--style", default=None)
    gen.add_argument("--io", default=None)
    gen.set_defaults(func=cmd_gen)

    lint = sub.add_parser("lint", help="轻量检查 ProgramSpec")
    lint.add_argument("--spec", required=True)
    lint.set_defaults(func=cmd_lint)

    audit = sub.add_parser("audit", help="导入 GX Works3 前自检 program.csv / comments.csv")
    audit.add_argument("--program", required=True, help="程序本体 CSV 路径")
    audit.add_argument("--comments", default=None, help="软元件注释 CSV 路径（可选）")
    audit.add_argument("--expect-comment-title", default=None,
                       help="目标工程的注释标题（如 Chinese Simplified/简体中文）")
    audit.set_defaults(func=cmd_audit)

    manual = sub.add_parser("manual", help="手册知识库（查手册，不需要把整本 PDF 读进上下文）")
    manual_sub = manual.add_subparsers(dest="manual_command", required=True)
    m_add = manual_sub.add_parser("add", help="索引一本手册（PDF/MD/TXT/DOCX）")
    m_add.add_argument("--path", required=True)
    m_add.add_argument("--title", default=None)
    m_add.add_argument("--model", default="", help="适用机型，如 FX5U")
    m_add.add_argument("--version", default="")
    m_add.add_argument("--replace", action="store_true", help="归档同标题旧版本")
    m_add.add_argument("--max-pages", type=int, default=None, help="只索引前 N 页（调试）")
    m_add.set_defaults(func=cmd_manual)
    m_list = manual_sub.add_parser("list", help="列出手册")
    m_list.set_defaults(func=cmd_manual)
    m_search = manual_sub.add_parser("search", help="搜索手册")
    m_search.add_argument("query")
    m_search.add_argument("--manual", default=None)
    m_search.add_argument("--model", default=None)
    m_search.add_argument("--top-k", type=int, default=5)
    m_search.add_argument("--json", action="store_true")
    m_search.set_defaults(func=cmd_manual)
    m_toc = manual_sub.add_parser("toc", help="查看手册目录")
    m_toc.add_argument("manual")
    m_toc.add_argument("--depth", type=int, default=3)
    m_toc.set_defaults(func=cmd_manual)
    m_read = manual_sub.add_parser("read", help="读指定章节/页")
    m_read.add_argument("manual")
    m_read.add_argument("--section", type=int, default=None)
    m_read.add_argument("--page", type=int, default=None)
    m_read.add_argument("--max-chars", type=int, default=6000)
    m_read.set_defaults(func=cmd_manual)
    m_remove = manual_sub.add_parser("remove", help="从索引删除手册（不删原文件）")
    m_remove.add_argument("manual")
    m_remove.set_defaults(func=cmd_manual)

    plan = sub.add_parser("plan", help="需求 + IO 表 -> ProgramSpec（默认规则；--llm 为可选独立模式）")
    plan.add_argument("--req", required=True)
    plan.add_argument("--io", default=None)
    plan.add_argument("--out", required=True)
    plan.add_argument("--name", default="AI 生成项目")
    plan.add_argument("--style", default=None)
    plan.add_argument("--llm", action="store_true",
                      help="可选：使用已配置的本地 LLM（独立模式；agent 模式不需要）")
    plan.set_defaults(func=cmd_plan)

    style = sub.add_parser("style", help="风格库")
    style_sub = style.add_subparsers(dest="style_command", required=True)
    import_cmd = style_sub.add_parser("import")
    import_cmd.add_argument("--program-csv", required=True)
    import_cmd.add_argument("--comment-csv", default=None)
    import_cmd.add_argument("--name", default="我的风格")
    import_cmd.set_defaults(func=cmd_style_import)
    list_cmd = style_sub.add_parser("list")
    list_cmd.set_defaults(func=cmd_style_list)

    project = sub.add_parser("project", help="项目管理")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    create = project_sub.add_parser("create")
    create.add_argument("--name", required=True)
    create.set_defaults(func=cmd_project_create)
    plist = project_sub.add_parser("list")
    plist.set_defaults(func=cmd_project_list)

    feedback = sub.add_parser("feedback", help="反馈")
    feedback_sub = feedback.add_subparsers(dest="feedback_command", required=True)
    add_cmd = feedback_sub.add_parser("add")
    add_cmd.add_argument("--project", required=True)
    add_cmd.add_argument("--segment", default="")
    add_cmd.add_argument("--category", default="其他")
    add_cmd.add_argument("--severity", default="warning")
    add_cmd.add_argument("--message", required=True)
    add_cmd.set_defaults(func=cmd_feedback_add)

    optimize = sub.add_parser("optimize", help="根据反馈生成检查清单")
    optimize.add_argument("--project", required=True)
    optimize.set_defaults(func=cmd_optimize)

    demo = sub.add_parser("demo", help="生成内置示例")
    demo.add_argument("--out", required=True)
    demo.set_defaults(func=cmd_demo)

    seed = sub.add_parser("seed-demo", help="创建示例项目（供 Web 软件首次体验）")
    seed.add_argument("--name", default=None)
    seed.set_defaults(func=cmd_seed_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
