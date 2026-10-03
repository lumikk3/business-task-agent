"""售后政策 RAG（DESIGN.md 5.5）。

流水线：

    Query Rewrite -> Embedding -> Vector Search -> Rerank -> Policy Context

* **Query Rewrite**：去停用词，保留内容关键词（``app/rag/tokenize.py``）。
* **Embedding**：``app/rag/embeddings.py``（GLM 远程 / 离线确定性两实现）。
* **Vector Search**：``app/rag/vector_store.py`` 的余弦索引。
* **Rerank**：把向量相似度与「词面命中（标题/章节加权）」融合，稀有词权重更高。

embedder 采用依赖注入：不传就用离线实现，测试与无 key 环境保持确定性；
需要真·语义检索时由调用方注入 ``GLMEmbedder``（见 ``resolve_embedder``）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from app.rag.embeddings import Embedder, HashingEmbedder
from app.rag.tokenize import rewrite as rewrite_query
from app.rag.tokenize import tokens
from app.rag.vector_store import VectorIndex

_DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"

# 存在于超过这个比例 chunk 里的 token 几乎没有区分度（语料级停用词），降权。
# 0.15 而非更大值：像「无理由退货」拆出的 无理/理由/由退、以及「运费/退货/退款」
# 这类高频 bigram 出现在 ~20-50% 的 chunk 里，只有压下去，稀有词（食品 8%、
# 耳机 8%）才不会被它们的重复计分淹没。
_DF_COMMON_FRACTION = 0.15
_COMMON_TOKEN_WEIGHT = 0.3

# Rerank 融合权重：向量相似度 + 词面命中分。
_VECTOR_WEIGHT = 8.0
_TITLE_BOOST = 1.5


@dataclass
class PolicyChunk:
    policy: str      # 文件名 stem, e.g. "7天无理由退货规则"
    section: str     # "##" 标题，或 ""
    text: str

    def to_dict(self) -> dict:
        return {"policy": self.policy, "section": self.section, "text": self.text}


class PolicyRAG:
    def __init__(self, policy_dir: Path | str | None = None,
                 embedder: Embedder | None = None):
        self.policy_dir = Path(policy_dir) if policy_dir else _DEFAULT_POLICY_DIR
        self.embedder: Embedder = embedder or HashingEmbedder()
        self.chunks: list[PolicyChunk] = []
        self.load()

    # ---- loading / index ----------------------------------------------
    def load(self) -> None:
        self.chunks = []
        for path in sorted(self.policy_dir.glob("*.md")):
            policy = path.stem
            section, buffer = "", []

            def flush() -> None:
                text = "\n".join(buffer).strip()
                if text:                       # 只有标题、没有正文的区块不产出 chunk
                    self.chunks.append(PolicyChunk(policy, section, text))

            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("## "):
                    flush()
                    buffer = []
                    section = line[3:].strip()
                elif line.startswith("# "):
                    continue                   # H1 是文档标题,已由 policy 字段承载
                else:
                    buffer.append(line)
            flush()
        self._build_index()

    def _build_index(self) -> None:
        # 词面：每 chunk 的 token 集合 + 文档频率（供 rerank 用）
        self._chunk_tokens = [
            tokens(chunk.policy + " " + chunk.section + " " + chunk.text)
            for chunk in self.chunks
        ]
        df: dict[str, int] = {}
        for chunk_tokens in self._chunk_tokens:
            for token in chunk_tokens:
                df[token] = df.get(token, 0) + 1
        self._df = df

        # 向量：一次批量为所有 chunk 编码
        texts = [f"{c.policy} {c.section} {c.text}" for c in self.chunks]
        self._index = VectorIndex()
        for i, vector in enumerate(self.embedder.embed(texts)):
            self._index.add(i, vector)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def doc_count(self) -> int:
        return len({chunk.policy for chunk in self.chunks})

    # ---- query ---------------------------------------------------------
    def rewrite(self, query: str) -> str:
        return rewrite_query(query)

    def retrieve(self, query: str, k: int = 3) -> list[dict]:
        rewritten = self.rewrite(query)
        query_tokens = tokens(rewritten)
        if not query_tokens:
            return []

        n = max(len(self.chunks), 1)
        common_cutoff = max(2, int(n * _DF_COMMON_FRACTION))

        def weight(token: str) -> float:
            freq = self._df.get(token, 1)
            base = math.log(1 + n / freq)          # 越稀有权重越高
            if freq > common_cutoff:
                base *= _COMMON_TOKEN_WEIGHT
            return base

        # Vector Search：先取一批候选，再融合词面分做 Rerank。
        candidate_k = min(len(self.chunks), max(k * 5, 15))
        query_vector = self.embedder.embed([rewritten])[0]
        candidates = self._index.search(query_vector, candidate_k)

        scored: list[tuple[float, PolicyChunk]] = []
        for item_id, cosine in candidates:
            chunk = self.chunks[item_id]
            chunk_tokens = self._chunk_tokens[item_id]
            overlap = query_tokens & chunk_tokens
            if not overlap and cosine <= 0:
                continue
            title_tokens = tokens(chunk.policy + " " + chunk.section)
            lexical = (sum(weight(t) for t in overlap)
                       + _TITLE_BOOST * sum(weight(t) for t in query_tokens & title_tokens))
            score = _VECTOR_WEIGHT * cosine + lexical
            scored.append((score, chunk))

        scored.sort(key=lambda item: (-item[0], item[1].policy, item[1].section))
        return [{**chunk.to_dict(), "score": round(score, 2)}
                for score, chunk in scored[:k]]
