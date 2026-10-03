"""业务数据生成器 —— 「假的企业业务系统」。

不接任何真实淘宝 / 京东接口，第一版自己造数据，写入一个 SQLite 库
（默认 ``data/business.db``）：

    1000 users
    3000 products
    5000 orders
    5000 logistics records
    500 after-sale records

Agent 的 Tool 就是在操作这个系统。同一 seed 永远生成同一份数据，便于测试与复现。

数据是有意做成「自洽」的：

* 每张订单都关联真实存在的 user 和 product（经 order_items 关联）；
* 每张订单恰好一条 logistics 记录，状态与订单状态一致；
* after_sale 记录只挂在 delivered 订单上，一部分带 refunds；
* 保留 3 个固定的演示用户，让三个展示问题稳定命中（见 ``DEMO_USERS``）。
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from app.store import DEFAULT_DB_PATH, SCHEMA

DEFAULT_SEED = 20260927
"""默认随机种子 —— 固定它即可复现同一份数据集。"""

DEMO_TODAY = date(2026, 9, 27)
"""数据里的「今天」。政策时效（7 天 / 15 天）以此为准。"""

# ---- 规模 -------------------------------------------------------------
USERS = 1000
PRODUCTS = 3000
ORDERS = 5000
LOGISTICS = 5000
AFTER_SALES = 500

# ---- 固定演示用户：保证三个展示问题能稳定命中 --------------------------
# U10001 -> 已付款待发货        -> "我的订单什么时候发货?"
# U10002 -> 已发货、物流运输中   -> "我的快递到哪里了?"
# U10003 -> 已签收 2 天的耳机    -> "这个耳机能退吗?"
DEMO_USERS: dict[str, dict] = {
    "U10001": {"name": "张三", "question": "我的订单什么时候发货?"},
    "U10002": {"name": "李四", "question": "我的快递到哪里了?"},
    "U10003": {"name": "王五", "question": "这个耳机能退吗?"},
}

# ---- 基础词表 ---------------------------------------------------------
_SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
_GIVEN = "伟芳娜敏静秀丽强磊洋艳勇军杰娟涛明超霞平刚桂英建华文博宇轩浩然"

# 品类 -> (商品基名, 价格区间)
_CATEGORIES: dict[str, tuple[list[str], tuple[int, int]]] = {
    "耳机": (["无线耳机", "降噪耳机", "蓝牙耳机", "入耳式耳机", "头戴式耳机"], (99, 1499)),
    "数码配件": (["机械键盘", "无线鼠标", "氮化镓充电器", "数据线", "显示器支架"], (29, 899)),
    "食品": (["咖啡豆礼盒", "坚果礼盒", "茶叶礼盒", "黑巧克力", "零食大礼包"], (19, 399)),
    "服饰": (["纯棉T恤", "牛仔裤", "运动鞋", "羽绒服", "帆布袋"], (39, 799)),
    "家居": (["香薰蜡烛", "收纳箱", "床品四件套", "护眼台灯", "抱枕"], (25, 599)),
    "美妆": (["保湿面霜", "口红", "氨基酸洗面奶", "补水面膜", "精华液"], (49, 699)),
}
_CATEGORY_NAMES = list(_CATEGORIES)

# 承运商 (code, 中文名)
_CARRIERS = [("SF", "顺丰"), ("YTO", "圆通"), ("ZTO", "中通"), ("JD", "京东物流"), ("EMS", "EMS")]
_CITIES = ["上海浦东", "北京朝阳", "杭州西湖", "广州天河", "成都武侯", "深圳南山",
           "武汉洪山", "西安雁塔", "南京鼓楼", "重庆渝北"]

# 订单状态分布（累计权重）
_STATUS_WEIGHTS = [("delivered", 0.70), ("shipped", 0.12), ("paid", 0.12), ("cancelled", 0.06)]
_AS_TYPES = ["退货", "换货", "退款"]
_AS_STATUS = [("created", 0.35), ("processing", 0.25), ("returned", 0.25), ("rejected", 0.15)]
_AS_REASONS = ["商品与描述不符", "质量问题", "不想要了", "发错货", "尺寸不合适", "收到时已损坏"]


def _weighted(rng: random.Random, pairs: list[tuple[str, float]]) -> str:
    total = sum(w for _, w in pairs)
    point = rng.random() * total
    upto = 0.0
    for value, weight in pairs:
        upto += weight
        if point <= upto:
            return value
    return pairs[-1][0]


def _random_name(rng: random.Random) -> str:
    return rng.choice(_SURNAMES) + rng.choice(_GIVEN) + (rng.choice(_GIVEN) if rng.random() < 0.4 else "")


def build_dataset(seed: int = DEFAULT_SEED, today: date = DEMO_TODAY) -> dict:
    """纯函数式地构造整个数据集，返回按表分组的行列表。"""
    rng = random.Random(seed)

    # ---- users -------------------------------------------------------
    user_ids = [f"U{10001 + i}" for i in range(USERS)]
    users: list[tuple[str, str]] = []
    for uid in user_ids:
        name = DEMO_USERS[uid]["name"] if uid in DEMO_USERS else _random_name(rng)
        users.append((uid, name))

    # ---- products ----------------------------------------------------
    products: list[tuple[str, str, str, float]] = []
    product_meta: dict[str, tuple[str, str, float]] = {}   # id -> (name, category, price)
    # 固定 P10001 为 299 元的无线耳机，供演示订单使用
    products.append(("P10001", "无线耳机", "耳机", 299.0))
    product_meta["P10001"] = ("无线耳机", "耳机", 299.0)
    for i in range(1, PRODUCTS):
        pid = f"P{10001 + i}"
        category = _CATEGORY_NAMES[i % len(_CATEGORY_NAMES)]
        names, (lo, hi) = _CATEGORIES[category]
        base = names[i % len(names)]
        suffix = "" if i % 3 == 0 else f" {'Pro' if i % 3 == 1 else '二代'}"
        name = base + suffix
        price = round(rng.uniform(lo, hi), 2)
        products.append((pid, name, category, price))
        product_meta[pid] = (name, category, price)

    # 挑一个食品商品给 U10002 的演示订单（在途物流）
    food_pid = next(pid for pid, (_, cat, _) in product_meta.items() if cat == "食品")
    food_price = product_meta[food_pid][2]

    order_ids = [f"O{10001 + i}" for i in range(ORDERS)]
    logistics_ids = order_ids[:LOGISTICS]           # 每张订单一条物流
    orders: list[tuple] = []
    order_items: list[tuple] = []
    logistics: list[tuple] = []

    def add_logistics(order_id: str, status: str, when: date | None) -> None:
        if status == "delivered":
            code, cname = rng.choice(_CARRIERS)
            logistics.append((order_id, cname, f"{code}{rng.randint(10**9, 10**10 - 1)}",
                              "已签收", f"{when.isoformat()} 18:30" if when else None,
                              rng.choice(_CITIES)))
        elif status == "shipped":
            code, cname = rng.choice(_CARRIERS)
            logistics.append((order_id, cname, f"{code}{rng.randint(10**9, 10**10 - 1)}",
                              "运输中", f"{(today - timedelta(days=1)).isoformat()} 09:00",
                              f"{rng.choice(_CITIES)}转运中心"))
        elif status == "paid":
            logistics.append((order_id, None, None, "待发货", None, None))
        else:  # cancelled
            logistics.append((order_id, None, None, "已取消", None, None))

    # ---- 3 张固定演示订单 --------------------------------------------
    # O10001: U10001 已付款待发货（耳机）
    orders.append(("O10001", "U10001", "paid", (today - timedelta(days=1)).isoformat(),
                   None, 299.0))
    order_items.append(("O10001", "P10001", 1, 299.0))
    add_logistics("O10001", "paid", None)

    # O10002: U10002 已发货，物流运输中（食品）
    orders.append(("O10002", "U10002", "shipped", (today - timedelta(days=2)).isoformat(),
                   None, food_price))
    order_items.append(("O10002", food_pid, 1, food_price))
    add_logistics("O10002", "shipped", None)

    # O10003: U10003 已签收 2 天的耳机（7 天无理由窗口内）
    orders.append(("O10003", "U10003", "delivered", (today - timedelta(days=7)).isoformat(),
                   (today - timedelta(days=2)).isoformat(), 299.0))
    order_items.append(("O10003", "P10001", 1, 299.0))
    add_logistics("O10003", "delivered", today - timedelta(days=2))

    # ---- 其余随机订单（只用 U10004 起的用户，保持演示用户干净）---------
    filler_users = user_ids[len(DEMO_USERS):]
    filler_products = list(product_meta)
    for i in range(3, ORDERS):
        oid = order_ids[i]
        uid = rng.choice(filler_users)
        pid = rng.choice(filler_products)
        _name, _cat, price = product_meta[pid]
        qty = 1 if rng.random() < 0.85 else 2
        total = round(price * qty, 2)
        status = _weighted(rng, _STATUS_WEIGHTS)
        created = today - timedelta(days=rng.randint(1, 120))
        delivered_at = None
        if status == "delivered":
            delivered = created + timedelta(days=rng.randint(1, 7))
            if delivered >= today:
                delivered = today - timedelta(days=1)
            delivered_at = delivered.isoformat()
        orders.append((oid, uid, status, created.isoformat(), delivered_at, total))
        order_items.append((oid, pid, qty, price))
        add_logistics(oid, status, date.fromisoformat(delivered_at) if delivered_at else None)

    # ---- after-sale：只挂在 delivered 订单上 --------------------------
    delivered_orders = [o for o in orders if o[2] == "delivered"]
    chosen = rng.sample(delivered_orders, min(AFTER_SALES, len(delivered_orders)))
    after_sale: list[tuple] = []
    refunds: list[tuple] = []
    for idx, order in enumerate(chosen, start=1):
        oid, uid, _st, _created, delivered_at, total = order
        rid = f"R{100001 + idx}"
        status = _weighted(rng, _AS_STATUS)
        created = (date.fromisoformat(delivered_at) + timedelta(days=rng.randint(1, 10))).isoformat()
        after_sale.append((rid, oid, rng.choice(_AS_TYPES), rng.choice(_AS_REASONS), status, created))
        if status in ("processing", "returned"):
            refunds.append((f"RF{100001 + idx}", rid, oid, total,
                            "processing" if status == "processing" else "done", created))

    return {
        "users": users,
        "products": products,
        "orders": orders,
        "order_items": order_items,
        "logistics": logistics,
        "after_sale_requests": after_sale,
        "refunds": refunds,
    }


def generate(db_path: str | Path = DEFAULT_DB_PATH, seed: int = DEFAULT_SEED,
             today: date = DEMO_TODAY) -> dict:
    """生成数据集并写入 SQLite，返回统计信息。会覆盖已有的库。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    data = build_dataset(seed=seed, today=today)

    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO users VALUES (?, ?)", data["users"])
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", data["products"])
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", data["orders"])
        conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?)", data["order_items"])
        conn.executemany("INSERT INTO logistics VALUES (?, ?, ?, ?, ?, ?)", data["logistics"])
        conn.executemany("INSERT INTO after_sale_requests VALUES (?, ?, ?, ?, ?, ?)",
                         data["after_sale_requests"])
        conn.executemany("INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?)", data["refunds"])
        conn.commit()
    finally:
        conn.close()

    return {
        "db_path": str(path),
        "seed": seed,
        "today": today.isoformat(),
        "counts": {table: len(rows) for table, rows in data.items()},
        "demo_users": DEMO_USERS,
    }


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    import json

    print(json.dumps(generate(), ensure_ascii=False, indent=2))
