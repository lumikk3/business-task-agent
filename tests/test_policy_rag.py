from app.rag.policy_rag import PolicyRAG


def test_retrieve_returns_ranked_policy_chunks():
    rag = PolicyRAG()
    hits = rag.retrieve("耳机用了5天还能退吗")
    assert hits, "expected policy hits for a return question"
    policies = [h["policy"] for h in hits]
    assert any("耳机" in p or "7天无理由" in p for p in policies)
    assert all("score" in h and "text" in h and "section" in h for h in hits)
    assert hits[0]["score"] >= hits[-1]["score"]


def test_food_policy_retrieval():
    rag = PolicyRAG()
    hits = rag.retrieve("咖啡豆食品可以无理由退货吗")
    policies = [h["policy"] for h in hits]
    assert any("食品" in p for p in policies)


def test_refund_threshold_policy_retrieval():
    rag = PolicyRAG()
    hits = rag.retrieve("退款金额超过500元需要什么审批")
    policies = [h["policy"] for h in hits]
    assert any("退款" in p for p in policies)


def test_rewrite_drops_stopwords():
    rag = PolicyRAG()
    rewritten = rag.rewrite("我的耳机还能退吗")
    assert "我的" not in rewritten
    assert "耳机" in rewritten


def test_unrelated_query_returns_empty_or_low_relevance():
    rag = PolicyRAG()
    assert rag.retrieve("") == []
