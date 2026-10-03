-- Step 18/20：PostgreSQL 初始化。
-- ⚠️ 本机无 Postgres，未在本机验证过。
-- 与 app/store_pg.py::DDL 保持一致（tests/test_deploy_artifacts.py 会校验两者表名一致）。

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price NUMERIC(12,2) NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    status TEXT NOT NULL,
    created_at DATE NOT NULL,
    delivered_at DATE,
    total NUMERIC(12,2) NOT NULL
);
CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL,
    price NUMERIC(12,2) NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE IF NOT EXISTS logistics (
    order_id TEXT PRIMARY KEY REFERENCES orders(order_id),
    carrier TEXT,
    tracking_no TEXT,
    status TEXT NOT NULL,
    updated_at TIMESTAMP,
    location TEXT
);
CREATE TABLE IF NOT EXISTS after_sale_requests (
    request_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    type TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS refunds (
    refund_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    amount NUMERIC(12,2) NOT NULL,
    status TEXT NOT NULL,
    created_at DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    order_id TEXT,
    reason TEXT NOT NULL,
    recommended_action TEXT,
    status TEXT NOT NULL,
    created_at DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT,
    status TEXT NOT NULL,
    intent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_traces (
    trace_id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES agent_tasks(task_id),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS eval_cases (
    case_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bad_cases (
    case_id TEXT PRIMARY KEY,
    run_id TEXT,
    error_type TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
