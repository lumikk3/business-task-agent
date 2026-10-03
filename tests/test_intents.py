from app.agent.intents import Intent, RuleBasedIntentRecognizer


def test_spec_examples_from_design_doc():
    recognizer = RuleBasedIntentRecognizer()
    assert recognizer.recognize("我的订单什么时候发货") == Intent.ORDER_QUERY
    assert recognizer.recognize("快递在哪里") == Intent.LOGISTICS_QUERY
    assert recognizer.recognize("耳机坏了我要退") == Intent.RETURN_REQUEST


def test_all_demo_scenarios_classified():
    recognizer = RuleBasedIntentRecognizer()
    cases = {
        "我的订单什么时候发货?": Intent.ORDER_QUERY,
        "我的快递到哪里了?": Intent.LOGISTICS_QUERY,
        "耳机用了5天还能退吗?": Intent.AFTERSALE_POLICY,
        "耳机坏了,我要退货。": Intent.RETURN_REQUEST,
        "商品已经退回去了,什么时候退款?": Intent.REFUND_QUERY,
        "耳机超过售后期限了,但确实坏了,我要退货退款,金额超过1000元。": Intent.RETURN_REQUEST,
    }
    for text, expected in cases.items():
        assert recognizer.recognize(text) == expected, text


def test_human_service_and_unknown():
    recognizer = RuleBasedIntentRecognizer()
    assert recognizer.recognize("转人工客服") == Intent.HUMAN_SERVICE
    assert recognizer.recognize("我要投诉") == Intent.HUMAN_SERVICE
    assert recognizer.recognize("今天天气怎么样") == Intent.UNKNOWN
