"""向量化（Embedding）：GLM 远程 embedding + 离线确定性回退。

RAG 流水线是 ``Query Rewrite -> Embedding -> Vector Search -> Rerank``。
本模块只负责其中的 Embedding 一环，并保证有两条实现：

* ``GLMEmbedder``     —— 走 GLM 的 ``/embeddings``（OpenAI 兼容，model=embedding-3）；
* ``HashingEmbedder`` —— 纯离线、确定性（signed hashing + L2 归一化），
  无需网络，测试与无 key 环境默认用它，保证流水线形状一致。

``resolve_embedder()`` 负责选择：显式传入 > 环境变量开关 > 有 GLM key 用远程 >
否则离线。默认（不显式要求）用离线实现，避免测试意外打网络。
"""
from __future__ import annotations

import math
import zlib
from typing import Protocol

from app.agent.llm_client import ChatClient, has_llm_credentials
from app.rag.tokenize import tokens

DIM = 256


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover - protocol
        ...


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


class HashingEmbedder:
    """离线确定性 embedding：token 经 crc32 散列到固定维度，带符号累加。

    用 ``zlib.crc32`` 而非内置 ``hash()``，因为后者在进程间随机化，会导致
    同一份数据在不同进程得到不同向量。
    """

    def __init__(self, dim: int = DIM):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in tokens(text):
                digest = zlib.crc32(token.encode("utf-8"))
                index = digest % self.dim
                sign = 1.0 if (digest // self.dim) % 2 == 0 else -1.0
                vector[index] += sign
            vectors.append(_l2_normalize(vector))
        return vectors


class GLMEmbedder:
    """GLM ``/embeddings``（OpenAI 兼容）。dim 在首次调用时确定并缓存。"""

    def __init__(self, client, model: str = "embedding-3"):
        self.client = client
        self.model = model
        self.dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = []
        for start in range(0, len(texts), 32):          # 分批，避免超长请求
            batch = texts[start:start + 32]
            data = self.client.embed(batch, model=self.model)
            vectors.extend(_l2_normalize(item["embedding"]) for item in data)
        if vectors and not self.dim:
            self.dim = len(vectors[0])
        return vectors


def resolve_embedder(prefer_remote: bool | None = None, client=None) -> Embedder:
    """选择 embedding 实现。

    默认离线（HashingEmbedder）。只有显式 ``prefer_remote=True`` 或环境变量
    ``AGENT_RAG_EMBEDDINGS=glm``，并且确实存在 GLM key 时，才走远程。
    """
    import os

    if prefer_remote is None:
        prefer_remote = os.environ.get("AGENT_RAG_EMBEDDINGS", "").lower() == "glm"
    if not prefer_remote:
        return HashingEmbedder()
    if not has_llm_credentials():
        return HashingEmbedder()
    return GLMEmbedder(client or ChatClient())
