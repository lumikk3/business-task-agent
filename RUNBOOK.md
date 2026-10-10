# 运行手册（RUNBOOK）：从零启动 / 日常启动

本手册只回答一件事：**怎么把这个项目跑起来**。分两条路径：

- **A. 初次运行**（全新机器、刚 clone，什么都没装）→ 见 [§1](#1-初次运行全新机器)
- **B. 再次运行**（依赖已装好，只想跑起来）→ 直接看 [§0](#0-六十秒速览已经跑过的人看这里)

> 本机实测环境：WSL2 / Ubuntu 26.04 / Python 3.14.4 / 无 sudo 也能跑通核心。
> 架构与实现细节见 [README.md](README.md)，部署与数据库运维见 [DEPLOY.md](DEPLOY.md)。

---

## 0. 六十秒速览（已经跑过的人看这里）

```bash
cd ~/agent-test/business-task-agent
source env/bin/activate          # 本项目用 env/ 这个虚拟环境

python scripts/run_demo.py       # 离线六场景（不需要任何 Key）
python -m pytest -q              # 121 passed（未起数据库则 117 passed, 4 skipped）
```

想跑真实 LLM 的版本：

```bash
python scripts/run_minimal_agent.py     # 读 .env 里的 LLM_API_KEY
```

---

## 1. 初次运行（全新机器）

### 1.1 前置检查

```bash
python3 -V          # 需要 >= 3.10（本项目用了 X | None 语法；实测 3.14.4）
git --version
```

预期输出（示例）：

```text
Python 3.14.4
git version 2.x.x
```

### 1.2 取代码 + 建虚拟环境

```bash
git clone git@github.com:lumikk3/business-task-agent.git
cd business-task-agent
python3 -m venv env
source env/bin/activate
python -V
```

> 之后所有命令里的 `python` 都指 `env/bin/python`。

### 1.3 安装依赖（分层，按需装）

**核心是零第三方依赖** —— `app/` 下的 agent / tools / rag / memory / store 只用标准库，
不装任何东西也能跑全部离线 Demo 和测试。

按需再装可选层：

```bash
# Step 12 MCP + Step 17 API 需要
pip install -r requirements-optional.txt      # mcp, fastapi, uvicorn

# Step 18 PostgreSQL/Redis 需要
pip install -r requirements-db.txt            # psycopg[binary], redis

# 验证
python -c "import fastapi, mcp, psycopg, redis; print('可选依赖 OK')"
```

预期输出：

```text
可选依赖 OK
```

### 1.4 配置 LLM（**不配也能跑**）

```bash
cp .env.example .env
```

编辑 `.env`，至少填一项：

```ini
LLM_API_KEY=你的key
LLM_BASE_URL=https://api.xiaomimimo.com/v1
LLM_MODEL=mimo-v2-flash
```

- **不填**：自动回退到确定性的 `RuleBasedBrain`，`run_demo.py` / `run_permission_demo.py` /
  `run_fault_demo.py` / `run_trace_demo.py` / `run_planner_demo.py` / `run_async_demo.py`
  以及全部测试都能跑（离线、可复现、不花钱）。
- **填了**：`run_minimal_agent.py` / `run_mcp_agent.py` / API 会走真实 LLM。
- 读取顺序：`~/.hermes/.env` → 当前目录 `.env` → 仓库根 `.env`（已存在的环境变量优先）。

### 1.5 生成业务数据与售后知识库

```bash
python scripts/generate_data.py --stats
python scripts/generate_policies.py
```

预期输出（实测，两个脚本都幂等，可反复跑）：

```text
已生成业务库: .../data/business.db
  seed=20260927  today=2026-09-27
  users 1000 / products 3000 / orders 5000 / order_items 5000
  logistics 5000 / after_sale_requests 500 / refunds 255

知识库目录: .../data/policies
  新增/更新 0 份, 保留 58 份
  当前共 66 份文档
```

### 1.6 验证（三档，由浅入深）

```bash
# ① 离线，无需 Key —— 六场景 + 规划/权限/故障/Trace/并发
python scripts/run_demo.py
python scripts/run_planner_demo.py
python scripts/run_permission_demo.py
python scripts/run_fault_demo.py
python scripts/run_trace_demo.py
python scripts/run_async_demo.py

# ② 真实 LLM（需要 .env 里的 Key）
python scripts/run_minimal_agent.py

# ③ 全量测试
python -m pytest -q
```

预期输出（节选，均为实测）：

```text
# run_permission_demo.py
金额 250 元  ->  CONFIRM   挂起提示: 操作「create_refund_request」需要您确认…
金额 900 元  ->  HUMAN     escalated  工单 T202609270001

# run_async_demo.py
串行 913.2 ms / 并发 310.8 ms  ->  加速比 2.94x

# run_minimal_agent.py（真实 LLM）
退货申请单号: R20260927001   状态: 已创建

# pytest
121 passed in 20.91s        # 起了 PG/Redis
117 passed, 4 skipped       # 未起 PG/Redis（集成测试自动 skip）
```

### 1.7 （可选）起 HTTP 服务

```bash
python -m uvicorn app.api.server:app --port 8077
```

另开一个终端验证：

```bash
curl localhost:8077/health
curl -X POST localhost:8077/api/chat \
     -H 'Content-Type: application/json' \
     -d '{"user_id":"U10003","message":"帮我查一下订单O10003的状态和物流"}'
```

预期输出（实测；`mode` 在第一次对话前是 `uninitialized`，之后变成 `llm`）：

```text
{"status":"ok","mode":"uninitialized","business_store":"sqlite","session_memory":"in-process",
 "checks":{"postgres":null,"redis":null}}
{"task_id":"T2026...","status":"completed","answer":"…已签收(delivered)…签收日期 2026-09-25…"}
```

浏览器打开 <http://localhost:8077/> 可看到极简客服工作台（含权限确认流程）。

### 1.8 （可选）PostgreSQL + Redis

免 root、不需要 Docker：

```bash
scripts/dev_services.sh up                    # 下载官方 deb 解到用户目录并启动
eval "$(scripts/dev_services.sh env)"         # 导出 DATABASE_URL / REDIS_URL / LD_LIBRARY_PATH
python scripts/migrate_to_pg.py               # SQLite -> PostgreSQL（幂等）
python scripts/run_pg_redis_demo.py           # 验证 Agent 真跑在 PG + Redis 上
python -m pytest -q                           # 121 passed
```

预期输出：

```text
[services] PostgreSQL: UP   (127.0.0.1:55432, db=business, user=hermes)
[services] Redis:      UP   (127.0.0.1:6399)
✓ Agent 完整跑在 PostgreSQL + Redis 上
```

用完关掉：`scripts/dev_services.sh down`

### 1.9 （可选）Docker 一键起

前提：Windows 的 Docker Desktop 里开启本 WSL 发行版的集成
（Settings → Resources → WSL Integration → 勾选 → Apply & Restart → 重开终端）。

```bash
cp .env.example .env        # 填 LLM_API_KEY
docker compose up -d --build
curl localhost:8077/health
```

预期输出：

```text
{"status":"ok","business_store":"postgres","session_memory":"redis",
 "checks":{"postgres":true,"redis":true}}
```

停止：`docker compose down`（加 `-v` 连数据一起删）

---

## 2. 再次运行（依赖已装好）

### 2.1 最小启动（四条命令）

```bash
cd ~/agent-test/business-task-agent
source env/bin/activate
python scripts/run_demo.py          # 或换成下表里任意一条
python -m pytest -q
```

### 2.2 想看什么 → 用哪条命令

| 想看什么 | 命令 | 需要 Key |
| --- | --- | --- |
| 完整版六场景（确定性） | `python scripts/run_demo.py` | 否 |
| 最小版 Agent（真实 LLM 调工具） | `python scripts/run_minimal_agent.py` | 是 |
| Step 7 Planner vs Runtime | `python scripts/run_planner_demo.py` | 否 |
| Step 9 多轮记忆 | `python scripts/run_memory_agent.py` | 是 |
| Step 10 故障注入 / 重试 / 人工接管 | `python scripts/run_fault_demo.py` | 否 |
| Step 11 权限门 AUTO/CONFIRM/HUMAN | `python scripts/run_permission_demo.py` | 否 |
| Step 12 工具 MCP 化 | `python scripts/run_mcp_agent.py --probe` | 否 |
| Step 12 全链路（LLM→MCP→DB） | `python scripts/run_mcp_agent.py` | 是 |
| Step 13 Trace 树 | `python scripts/run_trace_demo.py` | 否 |
| Step 14-16 评测 / Bad Case / 回归 | `python -m eval.evaluator.run_eval` | 否 |
| Step 17 HTTP 服务 | `python -m uvicorn app.api.server:app --port 8077` | 是（否则回退确定性） |
| Step 18 数据库韧性验证 | `python scripts/check_pg_resilience.py --restart-cmd "…"` | 否 |
| Step 19 串行 vs 并发 | `python scripts/run_async_demo.py` | 否 |
| 全量测试 | `python -m pytest -q` | 否 |

### 2.3 只跑一部分测试

```bash
python -m pytest -q tests/test_minimal_agent.py            # 单个文件
python -m pytest -q tests/test_pg_redis_integration.py     # 真实 PG/Redis 集成（未起服务则 skip）
python -m pytest -q -k "permission"                        # 按关键字
```

---

## 3. 端口与进程管理

| 端口 | 谁在用 | 备注 |
| --- | --- | --- |
| 8077 | `app.api.server`（Docker 或本地 uvicorn） | compose 里的 agent-api |
| 55432 | `scripts/dev_services.sh` 起的本地 PostgreSQL | 免 root 用户态实例 |
| 6399 | `scripts/dev_services.sh` 起的本地 Redis | |
| 5433 | Docker 里的 PostgreSQL 映射到宿主 | compose 里是 5432→5433 |
| 6379 | Docker 里的 Redis（集群内网） | 未映射到宿主 |

```bash
# 端口被占用时换一个
python -m uvicorn app.api.server:app --port 8090

# 谁占了端口
ss -ltnp | grep -E ':(8077|55432|6399)'

# 起停本地数据库
scripts/dev_services.sh status | env | up | down

# 起停容器栈
docker compose ps
docker compose up -d
docker compose down
```

---

## 4. 排查表

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ModuleNotFoundError: fastapi / mcp / psycopg` | 可选依赖没装 | `pip install -r requirements-optional.txt`（或 `-r requirements-db.txt`） |
| `[error] 未找到 LLM_API_KEY` | `.env` 没配 | 填 `.env`，或改用离线 Demo（`run_demo.py` 等） |
| 真实 LLM 偶发报错后自己好了 | 端点分块响应偶发被截断 | 已内置传输层重试（3 次退避）；仍失败看网络/额度 |
| `LLM HTTP 401/403` | Key 不对或无权限 | 检查 `.env` 的 `LLM_API_KEY` / `LLM_BASE_URL` |
| `Address already in use` | 端口被占（8077 常被 Docker 栈占） | 换端口 `--port 8090`，或先 `docker compose down` |
| `ModuleNotFoundError: psycopg` 但只想跑离线 | 环境里设了 `DATABASE_URL` | `unset DATABASE_URL REDIS_URL` 回到 SQLite 模式 |
| `scripts/dev_services.sh up` 下载失败 | 网络或 apt 源不可用 | 见 [DEPLOY.md](DEPLOY.md) §1 的另外两条路线（apt 原生 / Docker） |
| `/health` 里 `checks.postgres=false` | 数据库不可达 | `docker compose ps` 或 `scripts/dev_services.sh status` |
| `/api/chat` 返回 503 | 数据库不可用（已熔断，会自愈） | 数据库恢复后自动恢复，无需重启应用；见 [DEPLOY.md](DEPLOY.md) §5 |
| `/api/chat` 返回 502 | 端口有映射但容器没监听 | 容器没起来：`docker compose ps` / `docker compose up -d` |
| 测试显示 `4 skipped` | 未起 PG/Redis | 正常；想全绿就起服务（§1.8）后再跑 |

---

## 5. 相关文档

| 文档 | 内容 |
| --- | --- |
| [README.md](README.md) | 项目全貌：架构、Step 1→20 分阶段构建、六个核心场景、测试覆盖 |
| [DEPLOY.md](DEPLOY.md) | Step 18/20 保姆级部署：PostgreSQL/Redis 三条路线、Docker、运维韧性与故障排查 |
| [DESIGN.md](DESIGN.md) | 设计取舍与边界 |
| `eval/reports/*.md` | 评测报告（指标全部来自真实运行） |
