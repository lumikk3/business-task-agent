# Step 18 / Step 20 保姆级操作手册（PostgreSQL + Redis + Docker）

> 这份文档就是「照着敲就能跑起来」的步骤。所有命令都在本机（WSL2 / Ubuntu 26.04）验证过，
> 只有 Docker 那节因为本发行版没开 WSL 集成，需要你点一下开关才能验证。

---

## 0. 先看这张表：现在什么状态

| 部分 | 状态 | 说明 |
| --- | --- | --- |
| Step 11~17、19 | ✅ 已验证 | 见 README 各节的实测输出 |
| **Step 18 PostgreSQL + Redis** | ✅ **已验证** | 用 `scripts/dev_services.sh` 在**本机免 root** 起了真实 PostgreSQL 18 + Redis 8，Agent 真的跑在上面（见 §1 实测输出） |
| **Step 20 Docker** | ⚠️ 待你开一个开关 | Docker Desktop 装在 Windows 侧，但**这个 WSL 发行版没开集成**，所以 WSL 里 `docker` 用不了。开启步骤见 §2 |

本机探测到的真实情况（可复现）：

```text
$ sudo -n true
sudo: interactive authentication is required      # → 没有免密 sudo，不能 apt install
$ docker version
The command 'docker' could not be found in this WSL 2 distro.   # → WSL 集成没开
$ ls "/mnt/c/Program Files/Docker"
/mnt/c/Program Files/Docker                        # → Docker Desktop 确实装了
```

---

## 1. Step 18：PostgreSQL + Redis

### 路线 A（推荐）：免 root，不需要 Docker，也不需要 sudo

`scripts/dev_services.sh` 会把 Ubuntu 官方的 `postgresql-18` / `redis-server` 等
`.deb` 包下载下来、用 `dpkg -x` 解到**你的用户目录**，然后以当前用户直接运行真实二进制。
不用 sudo、不用 Docker、不污染系统，`down` 一条命令就能全部停掉。

**A1. 装 Python 驱动**（一次性）

```bash
cd ~/agent-test/business-task-agent
source env/bin/activate          # 或直接用 env/bin/python
pip install -r requirements-db.txt
```

**A2. 起服务**（一次性，之后只需 `up` 检查）

```bash
scripts/dev_services.sh up
```

首次会下载约 20MB 的包并 `initdb`，输出长这样：

```text
[services] 目标包: postgresql-18 postgresql-client-18 libpq5 liblzf1 libnuma1 libicu78 liburing2 redis-server redis-tools
[services] 已下载到 /home/<你>/.local/share/bta-services/debs
[services] 已解包到 /home/<你>/.local/share/bta-services/opt
[services] Redis 已启动: 127.0.0.1:6399
[services] initdb -> /home/<你>/.local/share/bta-services/pgdata
[services] PostgreSQL 已启动: 127.0.0.1:55432 (user=hermes)
[services] 已创建数据库 business
```

**A3. 把连接串导入当前 shell**

```bash
eval "$(scripts/dev_services.sh env)"
```

它会导出三个变量：

```text
export LD_LIBRARY_PATH=".../bta-services/opt/usr/lib/x86_64-linux-gnu:..."
export DATABASE_URL="postgresql://hermes@127.0.0.1:55432/business"
export REDIS_URL="redis://127.0.0.1:6399/0"
```

> 每次开新终端都要重新 `eval` 一次（或者写进你的 shell rc）。

**A4. 把「假企业业务系统」从 SQLite 灌进 PostgreSQL**

```bash
python scripts/migrate_to_pg.py --dry-run    # 先看一眼会写什么
python scripts/migrate_to_pg.py              # 正式迁移（幂等，可反复跑）
```

实测输出：

```text
迁移完成: data/business.db -> postgresql://hermes@127.0.0.1:55432/business
  users                    1000 行
  products                 3000 行
  orders                   5000 行
  order_items              5000 行
  logistics                5000 行
  after_sale_requests       500 行
  refunds                   255 行
  tickets                     0 行
合计 19755 行
```

**A5. 验证：让 Agent 真的跑在 PG + Redis 上**

```bash
python scripts/run_pg_redis_demo.py
```

实测输出（节选）：

```text
后端选择: business_store=postgres  session_memory=redis
业务数据层: app.store_pg.PgStore

Agent 工具链 (跑在 PG 上): query_order -> search_after_sales_policy -> create_return_request
状态: completed
PostgreSQL after_sale_requests: 500 -> 501  (新增 1)
从 PG 读回的退货单: {"request_id": "R20260927001", "order_id": "O10003", ...}

同一查询对比:
  字段一致: True   关键值一致: True

会话记忆层: app.memory.redis_store.RedisSessionMemory
context_block:
  [会话上下文]
  当前用户: U10003
  当前订单: O10003(无线耳机)
  已创建退货申请: R20260927001
Redis 原始 key agent:session:redis-demo (ttl=3600s): {...}
  新实例从 Redis 读回同一上下文 ✓

✓ Agent 完整跑在 PostgreSQL + Redis 上
```

**A6. 跑集成测试**（服务在跑时自动启用，没跑自动 skip）

```bash
python -m pytest -q tests/test_pg_redis_integration.py
# 3 passed
```

**A7. 验证 HTTP 服务也在 PG + Redis 上**

```bash
python -m uvicorn app.api.server:app --port 8080
curl localhost:8080/health
```

```json
{"status":"ok","mode":"llm","business_store":"postgres","session_memory":"redis"}
```

**A8. 用完关掉**

```bash
scripts/dev_services.sh status   # 看状态
scripts/dev_services.sh down     # 停 PG + Redis
```

### 路线 B：有 sudo 的机器（原生 apt 安装）

```bash
sudo apt update
sudo apt install -y postgresql redis-server
sudo systemctl enable --now postgresql redis-server

# 建库建角色（用户名/密码按需改，改完同步改 DATABASE_URL）
sudo -u postgres psql -c "CREATE ROLE hermes LOGIN PASSWORD 'hermes';"
sudo -u postgres createdb -O hermes business

export DATABASE_URL="postgresql://hermes:hermes@127.0.0.1:5432/business"
export REDIS_URL="redis://127.0.0.1:6379/0"

pip install -r requirements-db.txt
python scripts/migrate_to_pg.py
python scripts/run_pg_redis_demo.py
```

> 注意端口：apt 装的是 5432/6379，路线 A 的脚本用 55432/6399（避免和系统服务打架）。
> 代码只认 `DATABASE_URL` / `REDIS_URL`，端口在哪都行。

### 路线 C：有 Docker 的机器（只起这两个服务）

见 §2 先把 docker 打通，然后：

```bash
docker compose up -d postgres redis
export DATABASE_URL="postgresql://hermes:hermes@127.0.0.1:5432/business"
export REDIS_URL="redis://127.0.0.1:6379/0"
python scripts/migrate_to_pg.py && python scripts/run_pg_redis_demo.py
```

### 代码里到底接了什么（出问题先看这里）

| 目标 | 模块 | 切换条件 |
| --- | --- | --- |
| 业务数据后端 | `app/backend.py::open_business_store()` | 设了 `DATABASE_URL` → `app/store_pg.py::PgStore`；否则 SQLite |
| 会话记忆后端 | `app/backend.py::open_session_memory()` | 设了 `REDIS_URL` → `app/memory/redis_store.py::RedisSessionMemory`；否则进程内 dict |
| 表结构 | `app/store_pg.py::DDL`、`docker/postgres/init.sql` | 两处表名由测试 `test_deploy_artifacts.py` 强制一致 |
| HTTP 服务 | `app/api/server.py::AgentService._ensure()` | 走上面两个工厂，`/health` 会回报实际后端 |

### 常见问题

| 症状 | 原因 / 处理 |
| --- | --- |
| `PostgreSQL 支持需要 psycopg` | 没装驱动：`pip install -r requirements-db.txt` |
| `libicuuc.so.78: cannot open shared object file` | 没 `eval "$(scripts/dev_services.sh env)"`，缺 `LD_LIBRARY_PATH` |
| `address already in use` | 55432/6399 被占：`BTA_PG_PORT=55433 BTA_REDIS_PORT=6400 scripts/dev_services.sh up` |
| `apt-get download 失败` | 本机 apt 索引过期；有 sudo 就跑 `sudo apt update`，否则用路线 B/C |
| 迁移后行数不对 | 迁移是「先 TRUNCATE 再灌」，重跑 `python scripts/migrate_to_pg.py` 即可 |
| 退货单号变成 `R20260927002` | 之前的 demo/测试已经给 O10003 创建过一条；重跑迁移就回到干净状态 |

---

## 2. Step 20：Docker

### B1. 前置：给这个 WSL 发行版打开 Docker 集成（一次）

1. Windows 上启动 **Docker Desktop**（托盘图标变绿/稳定）；
2. 打开 **Settings（齿轮）→ Resources → WSL Integration**；
3. 打开 **“Enable integration with my default WSL distro”**；
4. 在列表里把你正在用的这个发行版（Ubuntu-26.04 / Ubuntu）**单独打开**；
5. 点 **Apply & Restart**。

### B2. 校验（在 WSL 里执行）

```bash
docker version --format '{{.Server.Version}}'
docker compose version
```

能打印出版本号就成了。之前那句
`The command 'docker' could not be found in this WSL 2 distro.` 会消失。

### B3. 一键起整套

```bash
cd ~/agent-test/business-task-agent
cp .env.example .env       # 填 LLM_API_KEY（不填则回退确定性 Runtime）
docker compose up --build
```

拓扑（`docker-compose.yml`）：

```text
browser ──> agent-api ──(MCP Client)──> mcp-order / mcp-logistics / mcp-aftersale
                 ├──> postgres   (业务数据 + agent_tasks / agent_traces / eval_cases)
                 └──> redis      (session / agent context / task state)
```

`agent-api` 容器里设置了 `DATABASE_URL` / `REDIS_URL`，配合 §1 的 `app/backend.py`
工厂，容器里就是**真的**在用 Postgres + Redis（不是摆设）。

### B4. 逐项验证

```bash
curl localhost:8077/health
# {"status":"ok","mode":"llm","business_store":"postgres","session_memory":"redis"}

open http://localhost:8077/            # 或在 Windows 浏览器打开
curl -X POST localhost:8077/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"U10003","message":"我的耳机坏了，我想退货","session_id":"s1"}'

docker compose exec postgres psql -U hermes -d business -c \
  "SELECT count(*) FROM after_sale_requests;"     # 应该看到新退货单

docker compose exec redis redis-cli keys 'agent:session:*'
docker compose logs -f agent-api
```

### B5. 收尾

```bash
docker compose down          # 停服务，保留数据卷
docker compose down -v       # 连数据一起删
```

### B6. 常见问题

| 症状 | 处理 |
| --- | --- |
| WSL 里仍然 `docker ... could not be found` | B1 第 4 步没勾上这个发行版；改完必须 Apply & Restart，然后**重开 WSL 终端** |
| `docker compose` 报 `unknown command` | 装的是老版 compose v1；用 `docker-compose up` 或升级 Docker Desktop |
| `postgres` 容器起来但连不上 | 第一次启动要等 `pg_isready`；compose 里已配 healthcheck，`agent-api` 会等它 |
| 端口 8077 被占 | 改 `docker-compose.yml` 里 `agent-api.ports` 的左边 |
| LLM 不生效 | `.env` 里的 `LLM_API_KEY` 要真实；`/health` 的 `mode` 是 `rule` 说明没读到 |

### B7. 不想装 Docker 怎么办

完全可以用 §1 路线 A 起 PG/Redis，再在本机直接跑服务与 MCP：

```bash
eval "$(scripts/dev_services.sh env)"
python -m uvicorn app.api.server:app --port 8077      # 等价于 agent-api 容器
python -m app.mcp.servers.order_server                # 三台 MCP Server 也都能本机直接跑
python scripts/run_mcp_agent.py --probe
```

Docker 那套的价值在于「一次起全套 + 环境一致」，不是唯一路径。

---

## 3. 配置项速查

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `DATABASE_URL` | 有值 → 用 PostgreSQL | 未设置（走 SQLite） |
| `REDIS_URL` | 有值 → 会话记忆用 Redis | 未设置（进程内 dict） |
| `BTA_SERVICES_DIR` | 路线 A 的安装目录 | `~/.local/share/bta-services` |
| `BTA_PG_PORT` / `BTA_REDIS_PORT` | 路线 A 的端口 | `55432` / `6399` |
| `BTA_PG_DB` / `BTA_PG_USER` | 库名 / 角色名 | `business` / `hermes` |
| `AGENT_RAG_EMBEDDINGS` | `glm` → 用 GLM embedding 做检索 | 未设置（离线确定性） |

---

## 4. 我在本机实际执行过的命令（可直接复制）

```bash
cd ~/agent-test/business-task-agent
pip install -r requirements-db.txt
scripts/dev_services.sh up
eval "$(scripts/dev_services.sh env)"
python scripts/migrate_to_pg.py --dry-run
python scripts/migrate_to_pg.py
python scripts/run_pg_redis_demo.py
python -m pytest -q tests/test_pg_redis_integration.py
python -m uvicorn app.api.server:app --port 8080 &
curl localhost:8080/health
scripts/dev_services.sh down
```

---

## 5. 运维韧性：数据库挂了/重启了，API 会怎样（实测）

一键验证脚本（会重启数据库并密集探测，报告失败次数与恢复耗时）：

```bash
# 对 Docker 里的 postgres
python scripts/check_pg_resilience.py \
    --dsn "postgresql://hermes:hermes@127.0.0.1:5433/business" \
    --restart-cmd "docker compose restart postgres"

# 对 scripts/dev_services.sh 起的本地实例
eval "$(scripts/dev_services.sh env)"
python scripts/check_pg_resilience.py \
    --restart-cmd "scripts/dev_services.sh down && scripts/dev_services.sh up"
```

实测行为矩阵（agent-api 容器不重启）：

| 场景 | /health | /api/chat | 恢复 |
| --- | --- | --- | --- |
| 正常 | 200 (16ms) `postgres:true` | 200 | — |
| **PG 重启**（容器还在，端口短暂不可用） | 200 `postgres:false`（诚实） | **200 成功**——请求自己扛过重启窗口 | 自动，无需重启应用 |
| **PG 停机**（容器停掉，主机名解析不了） | 200 (~7.5s) `postgres:false` | **503**（~3.7s，带明确原因） | PG 回来后自动恢复 |
| **agent-api 容器没起来** | — | **502**（Docker 端口代理：端口有映射但无监听） | 容器起来即恢复 |

实现要点（都在 `app/store_pg.py` / `app/api/server.py`）：

1. **执行前预检 + 有界重连退避**：连接已断开就先重连（不重复执行语句），
   `Connection refused` 会退避重试（扛住重启窗口）；**主机名解析失败则不重试**（服务真没了，重试没意义）。
2. **请求准入探测**（`ping_resilient`）：每个请求前用 2 次尝试探一次库，
   数据库真不可用时**快速回 503**，而不是进 Agent 循环干等（曾实测被拖到 60s+）。
3. **/health 用一次性短探测**（`ping(fast=True)`，1s 超时、**新建短连接**）：
   不碰可能"假死"的缓存连接，保证健康检查秒回真相。
4. **不把基础设施故障当工具失败**：`ToolExecutor` 遇到 `ConnectionError` 直接上抛
   （否则 LLM 会拿着注定失败的工具反复重试）；同时修掉了线程池 `wait=True`
   导致「5s 超时实际阻塞十几秒」的坑。

排错速查：

| 现象 | 含义 | 处理 |
| --- | --- | --- |
| 502 | 端口有映射但容器没监听（容器没起来/正在重启） | `docker compose ps` 看容器状态；`docker compose up -d` |
| 503 + `后端数据库不可用` | 数据库不可达（已熔断） | 看 `docker compose ps postgres`；`/health` 的 `checks` 会显示 false |
| /health 里 `postgres:false` 但 chat 正常 | 探测瞬间数据库还没就绪 | 正常，下一个请求即恢复 |
| `AdminShutdown` / `connection closed` | 连接被服务端终止（重启/被杀） | 已自动重连；若仍报错请贴 `check_pg_resilience.py` 输出 |
