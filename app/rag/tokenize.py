"""中文/拉丁分词与 query rewrite（RAG 与向量化共用）。

抽出来单独放，避免 policy_rag 与 embeddings 相互 import。CJK 用 bigram，
拉丁/数字用 word —— 一种确定性的「穷人分词器」，无需第三方库。
"""
from __future__ import annotations

import re

_STOPWORDS = set(
    "的 了 吗 呢 吧 啊 呀 我 你 您 他 她 它 们 是 在 有 和 与 或 能 可以 还 也 就 都 "
    "要 会 想 请 问 个 么 什么 怎么 如何 这 那 一下 帮我".split()
)

# 在 CJK 连续串内做子串剔除，所以多字停用词先（长的优先），再单字。
_MULTI_STOPWORDS = tuple(sorted((w for w in _STOPWORDS if len(w) > 1), key=len, reverse=True))
_SINGLE_STOPWORDS = frozenset(w for w in _STOPWORDS if len(w) == 1)

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_WORD = re.compile(r"[a-zA-Z0-9]+")


def tokens(text: str) -> set[str]:
    """CJK bigram + 拉丁/数字词。"""
    result: set[str] = set()
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            result.add(run)
        result.update(run[i:i + 2] for i in range(len(run) - 1))
    result.update(w.lower() for w in _LATIN_WORD.findall(text))
    return result


def rewrite(query: str) -> str:
    """Query rewrite：去掉停用词与标点，保留内容关键词。"""
    parts: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", query):
        if not run[0].isascii():                       # CJK 串 —— 剔除停用词
            for word in _MULTI_STOPWORDS:
                run = run.replace(word, "")
            run = "".join(ch for ch in run if ch not in _SINGLE_STOPWORDS)
        if run:
            parts.append(run)
    return " ".join(parts) or query
