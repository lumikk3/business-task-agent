"""RAG 流水线测试：Embedding(离线/远程) + 向量检索 + Rerank + 知识库规模。"""
from __future__ import annotations

import math

from app.rag.embeddings import GLMEmbedder, HashingEmbedder, resolve_embedder
from app.rag.policy_rag import PolicyRAG
from app.rag.vector_store import VectorIndex


# ---- Embedding -------------------------------------------------------
def test_hashing_embedder_is_deterministic_and_normalized():
    embedder = HashingEmbedder(dim=64)
    first = embedder.embed(["耳机质量问题退货"])[0]
    second = embedder.embed(["耳机质量问题退货"])[0]
    assert first == second
    assert len(first) == 64
    assert math.isclose(sum(v * v for v in first), 1.0, rel_tol=1e-9)


def test_hashing_embedder_similar_texts_score_higher():
    embedder = HashingEmbedder()
    a, b, c = embedder.embed(["耳机质量问题退货", "耳机质量问题换货", "食品退货运费规则"])

    def dot(x, y):
        return sum(i * j for i, j in zip(x, y))

    assert dot(a, b) > dot(a, c)


class _FakeEmbedClient:
    def __init__(self):
        self.calls: list = []

    def embed(self, texts, model=None):
        self.calls.append((tuple(texts), model))
        # 3 维假向量,方便断言
        return [{"embedding": [float(len(t)), 1.0, 0.5]} for t in texts]


def test_glm_embedder_uses_client_and_normalizes():
    client = _FakeEmbedClient()
    embedder = GLMEmbedder(client, model="embedding-3")
    vectors = embedder.embed(["ab", "abcd"])
    assert client.calls and client.calls[0][1] == "embedding-3"
    assert len(vectors) == 2 and embedder.dim == 3
    assert all(math.isclose(sum(v * v for v in vec), 1.0, rel_tol=1e-9) for vec in vectors)


def test_resolve_embedder_defaults_offline(monkeypatch):
    monkeypatch.delenv("AGENT_RAG_EMBEDDINGS", raising=False)
    # 凭据可能来自三处（.env 会在 import 期写入 os.environ），都要清掉才算「没凭据」
    for name in ("LLM_API_KEY", "MIMO_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(resolve_embedder(), HashingEmbedder)
    # 显式要求远程但没 key -> 仍然回退离线
    assert isinstance(resolve_embedder(prefer_remote=True), HashingEmbedder)


def test_resolve_embedder_uses_glm_when_opted_in(monkeypatch):
    monkeypatch.setenv("AGENT_RAG_EMBEDDINGS", "glm")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    assert isinstance(resolve_embedder(), GLMEmbedder)


# ---- Vector store ----------------------------------------------------
def test_vector_index_orders_by_cosine():
    index = VectorIndex()
    index.add(0, [1.0, 0.0])
    index.add(1, [0.0, 1.0])
    index.add(2, [0.9, 0.1])
    hits = index.search([1.0, 0.0], k=3)
    assert [item_id for item_id, _ in hits] == [0, 2, 1]
    assert hits[0][1] > hits[-1][1]


# ---- 知识库 + 流水线 -------------------------------------------------
def test_knowledge_base_has_enough_documents():
    rag = PolicyRAG()
    assert rag.doc_count >= 50, "Step 8 要求 50~100 份规则文档"
    assert rag.doc_count <= 100


def test_pipeline_returns_vector_and_rerank_fields():
    rag = PolicyRAG()                       # 默认离线 embedder
    hits = rag.retrieve("耳机用了5天还能退吗")
    assert hits
    assert all({"policy", "section", "text", "score"} <= set(h) for h in hits)
    assert hits[0]["score"] >= hits[-1]["score"]


def test_pipeline_works_with_injected_embedder():
    rag = PolicyRAG(embedder=HashingEmbedder())
    hits = rag.retrieve("食品类商品的退回运费谁承担")
    assert hits
    assert any("食品" in h["policy"] for h in hits)
