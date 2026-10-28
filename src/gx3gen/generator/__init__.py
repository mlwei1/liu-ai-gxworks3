"""ProgramSpec → 程序初稿（CSV + 注释 + 网络表 + 报告）。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..ilcsv.emitter import emit_csv
from ..lint import lint_program, lint_summary
from ..quality import QualityError, check_quality, quality_text
from ..spec import validate_spec
from ..audit import audit as audit_csv
from .build import build_program, style_defaults
from .comments import collect_comments
from .report import network_table, report_markdown

__all__ = ["build_program", "style_defaults", "collect_comments", "generate", "QualityError"]


def _segments_summary(program) -> list:
    out = []
    for segment in program.segments:
        out.append({
            "title": segment.title,
            "kind": segment.kind,
            "rungs": len(segment.rungs),
            "step_range": list(segment.step_range) if segment.step_range else None,
        })
    return out


def generate(spec: Dict[str, Any], style: Optional[Dict[str, Any]], out_dir: str,
             project_name: Optional[str] = None, requirement: str = "",
             force: bool = False) -> Dict[str, Any]:
    """生成 4 个产物并返回摘要。

    生成前会跑质量检查（见 gx3gen.quality）：错误会抛 QualityError，
    除非 force=True。requirement 越完整，质量检查越严格（循环/定时/启停/灯位等）。
    """
    validate_spec(spec)
    quality = check_quality(spec, requirement)
    if quality["errors"] and not force:
        raise QualityError(
            "生成前质量检查未通过：\n" + "\n".join("- " + item for item in quality["errors"]),
            quality,
        )
    program = build_program(spec, style)
    if project_name:
        program.project = project_name

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    program_csv = str(out / "program.csv")
    comment_csv = str(out / "comments.csv")
    table_md = str(out / "network_table.md")
    report_md = str(out / "report.md")

    comments = collect_comments(spec, program)
    project_cfg = spec.get("project") or {}
    comment_titles = project_cfg.get("comment_titles")
    if comment_titles is None and project_cfg.get("comment_title"):
        comment_titles = [project_cfg.get("comment_title")]
    stats = emit_csv(program, program_csv, comment_csv, comments,
                     comment_titles=comment_titles)
    issues = lint_program(program, spec, comments)
    # 生成后立刻按 GX 导入规则自检（BOM/行尾/操作数/IL/STL/注释标题）
    csv_audit = audit_csv(program_csv, comment_csv,
                          expected_comment_title=(comment_titles[0] if comment_titles else None))

    style_summary = ""
    if style:
        from ..style.profile import summarize
        style_summary = summarize(style)

    segments = _segments_summary(program)
    audit_note = "；GX 导入自检通过" if csv_audit["ok"] else (
        "；GX 导入自检发现 %d 项问题" % len(csv_audit["problems"]))
    summary = "生成 %d 段 / %d 网络 / %d 指令 / 约 %d 步；%s；%s%s" % (
        stats["segments"], stats["rungs"], stats["instructions"], stats["steps"],
        lint_summary(issues), quality_text(quality).splitlines()[0], audit_note)

    Path(table_md).write_text(network_table(program), encoding="utf-8")
    report_text = report_markdown(program, spec, stats, issues, style_summary, summary,
                                  csv_audit=csv_audit)
    if quality["errors"] or quality["warnings"]:
        report_text += "\n## 质量检查\n\n```\n" + quality_text(quality) + "\n```\n"
    Path(report_md).write_text(report_text, encoding="utf-8")

    return {
        "program_csv": program_csv,
        "comment_csv": comment_csv,
        "network_table": table_md,
        "report": report_md,
        "stats": stats,
        "summary": summary,
        "assumptions": list(spec.get("assumptions") or []),
        "lint": issues,
        "quality": quality,
        "audit": csv_audit,
        "segments": segments,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
