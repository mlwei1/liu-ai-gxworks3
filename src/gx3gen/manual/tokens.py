"""中文/英文混合检索词切分（标准库，无 jieba 依赖）。

FTS5 的 unicode61 不会把连续中文切成词，所以索引和查询都先转成：
- 拉丁字母/数字：整词（小写），如 ``set``、``m500``、``fx5u``
- 连续中文：bigram（二字切片），如 ``高速计数器`` → ``高速 速计 计数 数器``
- 单字中文：保留单字
"""
from __future__ import annotations

import re
from typing import List


LATIN_RE = re.compile(r"[A-Za-z0-9_]+")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def cjk_bigrams(text: str) -> List[str]:
    padded = " " + text + " "
    out: List[str] = []
    for match in CJK_RE.finditer(padded):
        run = match.group(0)
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[index:index + 2] for index in range(len(run) - 1))
    return out


def tokens(text: str) -> List[str]:
    """把文本转成小写 token 列表（用于 FTS5 的预切词字段）。"""
    text = str(text or "")
    out: List[str] = []
    for match in LATIN_RE.finditer(text):
        out.append(match.group(0).lower())
    out.extend(cjk_bigrams(text))
    # 去掉纯标点/空 token，去重保序
    seen = set()
    result: List[str] = []
    for token in out:
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def token_field(text: str) -> str:
    return " ".join(tokens(text))
