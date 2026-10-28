"""手册检索：SQLite FTS5（中英混合 bigram）+ 词法重排 + 片段/出处。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .store import connect
from .tokens import tokens

SNIPPET_CHARS = 480


def _normalize(text: str) -> str:
    text = str(text or "").lower()
    text = re.sub(r"\s+", "", text)
    return text.strip()


def _fts_query(query_tokens: List[str], use_and: bool = True) -> str:
    parts = []
    for token in query_tokens[:14]:
        token = token.replace('"', " ").strip()
        if token:
            parts.append('"%s"' % token)
    return (" AND " if use_and else " OR ").join(parts)


def _code_terms(query: str) -> List[str]:
    """提取 SET/ZRST/STL 这类大写指令/设备符号（用于精确加权）。"""
    terms = re.findall(r"\b[A-Z][A-Z0-9]{1,8}\b", str(query or ""))
    return list(dict.fromkeys(terms))


def _instruction_like(body: str, heading: str) -> float:
    """给“指令参考页”加权，压过附录/索引/步数表。"""
    text = str(body or "")
    score = 0.0
    for marker in ("指令符号", "设置数据", "可以使用的软元件", "执行条件", "梯形图", "FX5S"):
        if marker in text:
            score += 0.8
    # 指令参考页通常有“FX5S FX5UJ FX5U FX5UC”机型行，权重最高
    if re.search(r"FX5S\s+FX5UJ\s+FX5U\s+FX5UC", text):
        score += 2.5
    if "注意事项" in heading:
        score -= 1.5
    if "指令" in heading:
        score += 0.8
    negative = ("附录", "指令索引", "指令步数", "步数表", "索引")
    if any(marker in heading for marker in negative):
        score -= 8.0
    return score



def _snippet(body: str, query: str) -> str:
    body = re.sub(r"\s+", " ", str(body or "")).strip()
    if not body:
        return ""
    lowered = body.lower()
    query_clean = str(query or "").strip().lower()
    index = lowered.find(query_clean) if query_clean else -1
    if index < 0:
        for token in tokens(query):
            if len(token) < 2:
                continue
            index = lowered.find(token)
            if index >= 0:
                break
    if index < 0:
        return body[:SNIPPET_CHARS] + ("…" if len(body) > SNIPPET_CHARS else "")
    start = max(0, index - SNIPPET_CHARS // 3)
    end = min(len(body), start + SNIPPET_CHARS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return prefix + body[start:end].strip() + suffix


def search(query: str, manual_id: Optional[str] = None, model: Optional[str] = None,
           top_k: int = 5) -> List[Dict[str, Any]]:
    """搜索手册片段；返回按相关性排序的命中（带出处）。"""
    query = str(query or "").strip()
    query_tokens = tokens(query)
    if not query or not query_tokens:
        return []
    top_k = max(1, min(int(top_k or 5), 20))
    fts_query = _fts_query(query_tokens, use_and=True)
    if not fts_query:
        return []

    conn = connect()
    try:
        def _query(match: str) -> List[Any]:
            sql = (
                "SELECT c.id AS chunk_id, c.section_id, c.manual_id, c.heading_path, "
                "c.page_start, c.page_end, c.body, m.title AS manual_title, m.model, m.version, "
                "bm25(chunks_fts, 1.0, 2.2) AS rank "
                "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
                "JOIN manuals m ON m.id = c.manual_id "
                "WHERE chunks_fts MATCH ? AND m.status='indexed'"
            )
            params: List[Any] = [match]
            if manual_id:
                sql += " AND c.manual_id=?"
                params.append(manual_id)
            if model:
                sql += " AND m.model LIKE ?"
                params.append("%" + model + "%")
            sql += " ORDER BY rank LIMIT 80"
            return conn.execute(sql, params).fetchall()

        rows = _query(fts_query)
        if not rows:  # 中文长句 AND 过严时回退 OR
            rows = _query(_fts_query(query_tokens, use_and=False))

        # FTS 无命中时回退到 LIKE（中文短词/标点分词失败时兜底）
        if not rows:
            like_sql = (
                "SELECT c.id AS chunk_id, c.section_id, c.manual_id, c.heading_path, "
                "c.page_start, c.page_end, c.body, m.title AS manual_title, m.model, m.version, "
                "0.0 AS rank "
                "FROM chunks c JOIN manuals m ON m.id=c.manual_id "
                "WHERE m.status='indexed' AND (c.body LIKE ? OR c.heading_path LIKE ?)"
            )
            like_params: List[Any] = ["%" + query + "%", "%" + query + "%"]
            if manual_id:
                like_sql += " AND c.manual_id=?"
                like_params.append(manual_id)
            if model:
                like_sql += " AND m.model LIKE ?"
                like_params.append("%" + model + "%")
            like_sql += " LIMIT 80"
            rows = conn.execute(like_sql, like_params).fetchall()
    finally:
        conn.close()

    unique_tokens = set(query_tokens)
    normalized_query = _normalize(query)
    code_terms = _code_terms(query)
    phrase_terms = [item for item in re.split(r"\s+", str(query or "").strip())
                    if len(_normalize(item)) >= 2]
    scored: List[Dict[str, Any]] = []
    for row in rows:
        body = str(row["body"] or "")
        heading = str(row["heading_path"] or "")
        body_tokens = set(tokens(body))
        heading_tokens = set(tokens(heading))
        coverage = len(unique_tokens & body_tokens) / max(1, len(unique_tokens))
        heading_coverage = len(unique_tokens & heading_tokens) / max(1, len(unique_tokens))
        exact = 1.0 if normalized_query and normalized_query in _normalize(body + heading) else 0.0
        phrase_hits = 0
        for term in phrase_terms:
            normalized_term = _normalize(term)
            if normalized_term and normalized_term in _normalize(body + heading):
                phrase_hits += 1
        code_hits = 0
        heading_code_hits = 0
        code_near_start = 0.0
        for term in code_terms:
            # 指令/设备符号区分大小写；前后不能接字母数字，避免 SET 命中 SETP/SETUP
            pattern = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
            match = re.search(pattern, body)
            if match:
                code_hits += 1
                if match.start() < 200:
                    code_near_start = 3.0
                elif match.start() < 600:
                    code_near_start = max(code_near_start, 1.0)
            if re.search(pattern, heading):
                heading_code_hits += 1
        # 只有查询里出现 SET/ZRST/STL 这类指令符号时，才偏置“指令参考页”；
        # 泛化问题（如“输入滤波”“电源规格”）不应该被指令手册压过应用/硬件手册。
        instruction_score = _instruction_like(body, heading) if code_terms else 0.0
        section_bias = 0.0
        if code_terms:
            # 单条指令的“主参考页”章节优先
            if "步进梯形图" in heading:
                section_bias += 2.5
            if "位处理指令" in heading:
                section_bias += 1.5
            if "输出指令" in heading:
                section_bias += 1.5
            if any(marker in heading for marker in
                   ("基本指令", "数据传送指令", "应用指令")):
                section_bias += 0.5
            if "报警" in heading and "报警" not in query:
                section_bias -= 1.2
            if ("SFC" in heading or "SFC" in body[:120]) and \
                    "SFC" not in query.upper() and "步进" not in query:
                section_bias -= 1.0
        manual_title = str(row["manual_title"] or "")
        manual_bias = 0.0
        # 没有具体指令符号的泛化查询，优先应用/硬件手册，而不是“指令手册”里的顺带提及
        if not code_terms and ("指令" in manual_title and "编程" in manual_title):
            manual_bias -= 2.0
        rank = float(row["rank"] or 0.0)
        # BM25 只做很小的 tie-breaker：否则附录/注意事项里高频出现的关键词会压过指令参考页
        rank_bonus = min(0.4, max(0.0, -rank) * 0.03)
        score = (coverage * 4.0 + heading_coverage * 2.0 + exact * 2.0
                 + phrase_hits * 1.6
                 + code_hits * 3.0 + heading_code_hits * 2.5 + code_near_start
                 + instruction_score + section_bias + manual_bias + rank_bonus)
        scored.append({
            "chunk_id": row["chunk_id"],
            "section_id": row["section_id"],
            "manual_id": row["manual_id"],
            "manual_title": row["manual_title"],
            "model": row["model"] or "",
            "version": row["version"] or "",
            "heading_path": heading,
            "page_start": row["page_start"],
            "page_end": row["page_end"],
            "snippet": _snippet(body, query),
            "score": round(score, 4),
            "citation": "%s / %s / p.%s" % (
                row["manual_title"], heading or "未命名章节", row["page_start"]),
        })
    scored.sort(key=lambda item: (item["score"], -int(item["page_start"] or 0)), reverse=True)
    # 同一章节/同一页只保留最相关的一个片段，避免 top-k 全是重复窗口
    seen_sections = set()
    seen_pages = set()
    diverse: List[Dict[str, Any]] = []
    for item in scored:
        page_marker = (item.get("manual_id"), item.get("page_start"))
        section_marker = (item.get("manual_id"), item.get("section_id"))
        if section_marker in seen_sections or page_marker in seen_pages:
            continue
        seen_sections.add(section_marker)
        seen_pages.add(page_marker)
        diverse.append(item)
        if len(diverse) >= top_k:
            break
    return diverse
