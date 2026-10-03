"""极简内存向量索引（余弦相似度）。

数据规模只有几十~几百条 chunk，纯 Python 线性扫描足够，无需引入向量数据库。
接口保持可替换：之后换 FAISS / pgvector 只需实现同样的 ``add`` / ``search``。
"""
from __future__ import annotations

import math


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class VectorIndex:
    def __init__(self) -> None:
        self._ids: list[int] = []
        self._vectors: list[list[float]] = []
        self.dim = 0

    def add(self, item_id: int, vector: list[float]) -> None:
        self._ids.append(item_id)
        self._vectors.append(vector)
        if vector and not self.dim:
            self.dim = len(vector)

    def __len__(self) -> int:
        return len(self._ids)

    def search(self, query_vector: list[float], k: int) -> list[tuple[int, float]]:
        """返回 ``[(item_id, cosine), ...]``，按相似度降序。"""
        scored = [(_cosine(query_vector, vector), item_id)
                  for item_id, vector in zip(self._ids, self._vectors)]
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [(item_id, score) for score, item_id in scored[:k]]
