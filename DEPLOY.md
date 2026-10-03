# 部署说明（Step 18 / Step 20）

## 本机验证状态（重要）

| 部分 | 状态 |
| --- | --- |
| Step 11 权限门 / 13 Trace / 14-16 Eval / 17 API / 19 并发 / 12 MCP | ✅ 已在本机跑通（见 README 各 Step 的实测输出） |
| Step 18 PostgreSQL / Redis | ⚠️ **未在本机验证** —— 本机（WSL2）没有 Postgres / Redis 服务，只有代码与 DDL |
| Step 20 Docker | ⚠️ **未在本机验证** —— WSL2 发行版内没有 `docker`（只有 Windows 端 Docker Desktop） |

这两部分只保证「代码/配置是完整、可读、与已实现部分一致的」，
不保证「已经在某个环境里跑起来」。在有 Docker/Postgres 的机器上按下面步骤验证。

## 一键起（需要 Docker）

```bash
cp .env.example .env          # 填 GLM_API_KEY（可选，不填则回退确定性 Runtime）
docker compose up --build
# 工作台: http://localhost:8077/
# 健康检查: curl localhost:8077/health
```

## 拓扑

```
browser ──> agent-api ──(MCP Client)──> mcp-order
                 │                      mcp-logistics
                 │                      mcp-aftersale
                 ├──> postgres   (业务数据 + agent_tasks / agent_traces / eval_cases)
                 └──> redis      (session / agent context / task state)
```

## Postgres / Redis 在代码里的接入点

| 目标 | 模块 | 接入方式 |
| --- | --- | --- |
| 业务数据换 Postgres | `app/store_pg.py::PgStore` | 与 `app/store.py::Store` 同方法签名，`BusinessTools` 无需改动；DDL 见 `DDL` 常量与 `docker/postgres/init.sql` |
| 会话换 Redis | `app/memory/redis_store.py::RedisSessionMemory` | 与 `app/memory/session.py::SessionMemory` 同接口，多副本部署时共享 Session |
| 连接串 | 环境变量 | `DATABASE_URL` / `REDIS_URL` |

依赖：`pip install -r requirements-db.txt`（psycopg、redis；均为惰性导入，不装也不影响核心运行）。

## 本机自测（不含上述两项）

```bash
python -m pytest -q
python -m eval.evaluator.run_eval
python -m uvicorn app.api.server:app --port 8077     # 然后 curl localhost:8077/health
python scripts/run_mcp_agent.py --probe
```
