"""Policy RAG (DESIGN.md 5.5).

Pipeline: query rewrite -> retrieval -> rerank -> policy context.

V1 uses deterministic keyword scoring over `data/policies/*.md` (CJK bigrams +
latin words) instead of embeddings. The `retrieve()` interface is the swap
point for vector search + rerank models in V2.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"

_STOPWORDS = set(
    "的 了 吗 呢 吧 啊 呀 我 你 您 他 她 它 们 是 在 有 和 与 或 能 可以 还 也 就 都 "
    "要 会 想 请 问 个 么 什么 怎么 如何 这 那 一下 帮我 退货 退款".split()
)

# Applied inside a CJK run (a whole sentence is one run), so multi-char entries
# are stripped first, longest first, then single-char entries.
_MULTI_STOPWORDS = tuple(sorted((w for w in _STOPWORDS if len(w) > 1),
                                key=len, reverse=True))
_SINGLE_STOPWORDS = frozenset(w for w in _STOPWORDS if len(w) == 1)

# A token present in more than this fraction of chunks carries almost no signal
# (a corpus-level stopword) and is down-weighted by the reranker.
_DF_COMMON_FRACTION = 0.25
_COMMON_TOKEN_WEIGHT = 0.3


def _tokens(text: str) -> set[str]:
    """CJK bigrams + latin/number words — a poor man's tokenizer, deterministic."""
    result: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            result.add(run)
        result.update(run[i:i + 2] for i in range(len(run) - 1))
    result.update(re.findall(r"[a-zA-Z0-9]+", text.lower()))
    return result


@dataclass
class PolicyChunk:
    policy: str      # file stem, e.g. "7天无理由退货规则"
    section: str     # "##" heading or ""
    text: str

    def to_dict(self) -> dict:
        return {"policy": self.policy, "section": self.section, "text": self.text}


class PolicyRAG:
    def __init__(self, policy_dir: Path | str | None = None):
        self.policy_dir = Path(policy_dir) if policy_dir else _DEFAULT_POLICY_DIR
        self.chunks: list[PolicyChunk] = []
        self.load()

    def load(self) -> None:
        self.chunks = []
        for path in sorted(self.policy_dir.glob("*.md")):
            policy = path.stem
            section, buffer = "", []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("## "):
                    if buffer:
                        self.chunks.append(PolicyChunk(policy, section, "\n".join(buffer).strip()))
                        buffer = []
                    section = line[3:].strip()
                else:
                    buffer.append(line)
            if buffer:
                self.chunks.append(PolicyChunk(policy, section, "\n".join(buffer).strip()))
        self._build_index()

    def _build_index(self) -> None:
        """Precompute per-chunk tokens and document frequency for the reranker."""
        self._chunk_tokens = [
            _tokens(chunk.policy + " " + chunk.section + " " + chunk.text)
            for chunk in self.chunks
        ]
        df: dict[str, int] = {}
        for tokens in self._chunk_tokens:
            for token in tokens:
                df[token] = df.get(token, 0) + 1
        self._df = df

    def rewrite(self, query: str) -> str:
        """Query rewrite: drop stopwords/punctuation, keep content keywords.

        A CJK sentence arrives as a single run, so stopwords are stripped as
        substrings rather than matched against the whole run.
        """
        parts: list[str] = []
        for run in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", query):
            if not run[0].isascii():                   # CJK run — strip stopwords
                for word in _MULTI_STOPWORDS:
                    run = run.replace(word, "")
                run = "".join(ch for ch in run if ch not in _SINGLE_STOPWORDS)
            if run:
                parts.append(run)
        return " ".join(parts) or query

    def retrieve(self, query: str, k: int = 3) -> list[dict]:
        rewritten = self.rewrite(query)
        query_tokens = _tokens(rewritten)
        if not query_tokens:
            return []
        n = max(len(self.chunks), 1)
        common_cutoff = max(2, int(n * _DF_COMMON_FRACTION))

        def weight(token: str) -> float:
            freq = self._df.get(token, 1)
            base = math.log(1 + n / freq)          # rarer terms score higher
            if freq > common_cutoff:               # corpus stopword — low signal
                base *= _COMMON_TOKEN_WEIGHT
            return base

        scored: list[tuple[float, PolicyChunk]] = []
        for chunk, chunk_tokens in zip(self.chunks, self._chunk_tokens):
            overlap = query_tokens & chunk_tokens
            if not overlap:
                continue
            # rerank: title/section matches weigh more than body matches
            title_tokens = _tokens(chunk.policy + " " + chunk.section)
            score = (sum(weight(t) for t in overlap)
                     + 1.5 * sum(weight(t) for t in query_tokens & title_tokens))
            scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].policy, item[1].section))
        return [{**chunk.to_dict(), "score": round(score, 2)}
                for score, chunk in scored[:k]]
