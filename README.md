# Business Task Agent Runtime

> 企业业务任务型 Agent 执行平台 —— 以「电商售后」为场景的可运行 Demo。

传统大模型应用是 `用户提问 → LLM → 生成答案`，能答问题，但完不成真实业务任务。
本项目实现的是一个真正会**规划、调用工具、观察结果、按业务规则与权限约束自主决策**
的 Agent Runtime：用户说一句「耳机坏了，我要退货」，系统会自己去查订单、查商品、
检索售后政策、判断退货资格，然后在权限允许的范围内创建退货申请，或者转人工。

---

## 目录

- [核心能力](#核心能力)
- [快速开始](#快速开始)
- [业务数据（假的企业业务系统）](#业务数据假的企业业务系统)
- [分阶段构建（Step 3 → Step 10）](#分阶段构建step-3--step-10)
- [Step 11 → 20：权限 / MCP / Trace / Eval / 服务化](#step-11--20从能调工具到可上线的-agent-服务)
- [六个核心场景](#六个核心场景)
- [执行流程 / Agent Loop](#执行流程--agent-loop)
- [目录结构](#目录结构)
- [核心模块](#核心模块)
- [工具与风险等级](#工具与风险等级)
- [权限分级](#权限分级)
- [错误处理](#错误处理)
- [Trace 执行链路](#trace-执行链路)
- [配置项](#配置项)
- [测试](#测试)
- [路线图](#路线图)
- [与 DESIGN.md 的关系](#与-designmd-的关系)

---

## 核心能力

| 能力 | 实现 |
| --- | --- |
| 意图识别 | 7 类意图，有序正则规则，确定性、可测试 |
| 任务规划 | 按意图生成执行步骤，Runtime 真正逐步执行（不是装饰性文字） |
| 工具调用 | Tool Registry + JSON Schema，可直接对接 OpenAI function calling |
| RAG | 售后知识库检索：query rewrite → embedding（GLM / 离线）→ 向量检索 → rerank |
| 记忆 / 上下文 | 会话级 Memory，多轮输入可解析「昨天买的那个」这类指代 |
| 权限控制 | 按风险等级 + 金额阈值分级：自动 / 二次确认 / 人工审批 |
| Human-in-the-loop | 支持挂起任务后 `resume(confirm=True/False)` 继续或取消 |
| 错误处理 | Timeout / Retry / 指数退避 / 最大重试 / 错误分类 / Fallback 转人工 |
| LLM 决策 | 可插拔 Brain：确定性 `RuleBasedBrain` 或 `OpenAIBrain`（同一下游接口） |
| Trace | 每次执行生成完整链路 JSONL（node / tool / arguments / latency / error / retry） |

**零第三方依赖**：`app/` 只用 Python 标准库（`sqlite3` / `urllib` / `threading` 等），
无需 `pip install` 即可运行与测试。

---

## 快速开始

要求：Python 3.10+（开发环境使用 3.14）。

```bash
# 1) 准备业务数据 —— 生成"假的企业业务系统"（1000 users / 5000 orders / …）
python scripts/generate_data.py --stats

# 2) 建立售后知识库（50~100 份规则文档）
python scripts/generate_policies.py

# 3) Step 3~6：最小版 Agent（3+1 个工具、3 个基础问题 + 退货任务），需要 GLM_API_KEY
python scripts/run_minimal_agent.py

# 4) Step 7~13：Planner / 记忆 / 异常恢复 / 权限 / MCP / Trace
python scripts/run_planner_demo.py
python scripts/run_memory_agent.py        # 需 GLM_API_KEY
python scripts/run_fault_demo.py
python scripts/run_permission_demo.py
python scripts/run_trace_demo.py
python scripts/run_mcp_agent.py --probe   # 完整版去掉 --probe,需 GLM_API_KEY

# 5) Step 14~16：Eval / Bad Case / Regression（真实跑 200 条）
python -m eval.evaluator.run_eval

# 6) Step 17 / 19：HTTP 服务 与 并发
python -m uvicorn app.api.server:app --port 8077   # http://localhost:8077/
python scripts/run_async_demo.py

# 7) 完整版六场景 Demo（确定性 RuleBasedBrain，无需 API Key）
python scripts/run_demo.py

# 8) 运行测试
python -m pytest -q
```

> Step 12/17 需要可选依赖：`pip install -r requirements-optional.txt`（mcp、fastapi、uvicorn）。
> Agent 核心（agent/tools/rag/memory/store）保持**零第三方依赖**。

Demo 输出示例：

```text
=== 场景4 申请退货 ===
意图   : RETURN_REQUEST
计划   : 查询订单 -> 查询商品 -> 检索售后政策 -> 判断退货资格 -> 创建退货申请或转人工 -> 返回结果
工具链 : query_order -> search_after_sales_policy -> create_return_request
状态   : completed   风险: LOW   Trace: T2026...
回答   : 退货申请已创建:申请单号R20260927001,订单O202609001(无线耳机),原因:耳机坏了,我要退货…
```

每次运行的完整 Trace 会追加写入 `traces/agent_traces.jsonl`。

### 使用 LLM 决策（可选）

设置 `OPENAI_API_KEY` 后，`run_demo.py` 会自动切换到 `OpenAIBrain`，通过
OpenAI 兼容的 function calling 选择下一步动作：

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1   # 可选，兼容任意 OpenAI 协议端点
export OPENAI_MODEL=gpt-4o-mini                    # 可选
python scripts/run_demo.py
```

未配置 Key 时自动回退到确定性 Brain，Demo 与测试始终可离线复现。

---

## 业务数据（假的企业业务系统）

不接任何真实淘宝 / 京东接口 —— 第一版自己造数据，写进一个 SQLite 库
（`data/business.db`）。Agent 的 Tool 就是在操作这个系统。

```bash
python scripts/generate_data.py --stats        # 生成 + 打印分布
python scripts/generate_data.py --seed 1       # 换种子，得到另一份数据
```

规模（`app/data/generator.py`，确定性生成，同 seed 必得同一份数据）：

| 表 | 行数 |
| --- | --- |
| `users` | 1000 |
| `products` | 3000 |
| `orders` | 5000 |
| `order_items` | 5000 |
| `logistics` | 5000 |
| `after_sale_requests` | 500 |
| `refunds` | ~255 |

数据是**自洽**的：每张订单都关联真实存在的 user / product，每张订单恰好一条
logistics（状态与订单一致），after_sale 只挂在已签收订单上，一部分带 refunds。

> 你给的示例 JSON（`order_id / user_id / product_id / status / amount`，
> `order_id / status / company / tracking_no`）在内部落成规范化的表：
> 商品经 `order_items` 关联、金额字段为 `total`、承运商字段为 `carrier`，
> 这样 `query_order` 一次就能带出商品、物流概要、售后与退款记录。

固定 3 个演示用户，保证展示问题稳定命中：

| 用户 | 订单状态 | 对应问题 |
| --- | --- | --- |
| `U10001` 张三 | 已付款待发货 | 我的订单什么时候发货？ |
| `U10002` 李四 | 已发货、物流运输中 | 我的快递到哪里了？ |
| `U10003` 王五 | 已签收 2 天的耳机 | 这个耳机能退吗？ |

（这三个用户各自只有这一张订单，随机订单使用 `U10004` 起的用户，互不干扰。）

---

## 分阶段构建（Step 3 → Step 10）

项目按阶段往上长，每一步都有可运行的 demo。完整版（Planner + 6 工具 + 权限 + 人工接管 +
Trace）在 `app/agent/runtime.py`，最小版与它共用同一套工具层与数据层。

GLM 接入（OpenAI 兼容）：`GLM_API_KEY` 必填，`GLM_BASE_URL` 默认
`https://open.bigmodel.cn/api/paas/v4`，`GLM_MODEL` 默认 `glm-4.6`（见「配置项」）。

### Step 3 / 4：最基础 Agent + 三个工具

```text
User  →  LLM  →  Tool  →  Tool Result  →  LLM  →  Answer
```

```bash
python scripts/run_minimal_agent.py
```

只暴露 `query_order` / `query_logistics` / `search_after_sales_policy`
（`build_minimal_registry`），**没有 Planner**；LLM 通过 function calling 自己决定下一步
（`app/agent/minimal_agent.py`）。跑通三个问题：订单什么时候发货 / 快递到哪里 / 这个耳机能退吗。

### Step 5：加入退货工具，从「回答」到「完成任务」

```text
query_order → search_after_sales_policy → 判断资格 → create_return_request
```

工具集升级为 4 个（`RETURN_TOOLS`）。实测（`glm-4.6`）：

```text
=== 问题4｜U10003｜我的耳机坏了,我想退货。 ===
  step1  query_order({'user_id': 'U10003'})                            -> ok
  step2  search_after_sales_policy({'query': '质量问题退货期限和规则'})   -> ok
  step5  create_return_request({'order_id': 'O10003', ...})            -> ok
  回答   : 已为您成功创建退货申请,申请单号 R20260927001 …
```

> 这是项目第一次体现：**Agent 不是回答，而是在完成任务**。实测 `glm-4-flash` 只会给建议、
> 反问用户，不执行工具，所以默认模型选 `glm-4.6`。

### Step 6：Agent Loop —— 流程不写死

代码里没有 `query_order(); search_policy(); create_return()` 这种固定调用。循环是：

```text
Think → Act → Observe → Think → Act → …
```

下一步调什么由 LLM 决定。同一套工具、不同问题会走出完全不同的工具链：问题1 只查订单，
问题2 查订单+物流，问题4 查订单+政策+建退货——这就是 Agent 与 Workflow 的区别。

### Step 7：Planner（复杂任务先规划）

```bash
python scripts/run_planner_demo.py
```

```text
用户   : 我的耳机坏了,而且订单好像也找不到了,我想退货。
[Planner] 生成的执行计划:
  1. 查询订单  2. 查询商品  3. 检索售后政策
  4. 判断退货资格  5. 创建退货申请或转人工  6. 返回结果
[Runtime] 实际执行的工具链:
  query_order -> search_after_sales_policy -> create_return_request
```

一句话：**Planner 决定「做什么」，Runtime 决定「怎么可靠地执行」**（权限校验 / 重试 /
人工接管 / Trace）。

### Step 8：RAG 知识库（50~100 份规则文档 + 真 Embedding）

```bash
python scripts/generate_policies.py     # -> data/policies/ 共 66 份
```

检索流水线：`Query Rewrite → Embedding → Vector Search → Rerank → Policy Context`。

* `app/rag/tokenize.py` —— 中文 bigram + 拉丁词的分词与 query rewrite
* `app/rag/embeddings.py` —— `GLMEmbedder`（GLM `/embeddings`，embedding-3，2048 维）
  与离线确定性的 `HashingEmbedder`
* `app/rag/vector_store.py` —— 余弦相似度向量索引
* `app/rag/policy_rag.py` —— 融合「向量相似度」与「词面命中（标题加权）」的 Rerank

默认用离线 embedder（测试确定、不依赖网络）；要真·语义检索：

```bash
export AGENT_RAG_EMBEDDINGS=glm
```

### Step 9：Context / Memory —— 记住任务状态

```bash
python scripts/run_memory_agent.py
```

```text
第1轮: 我的耳机坏了。      -> 记住 当前用户 / 当前订单 / 当前商品
第2轮: 就是昨天那个订单。   -> 注入 [会话上下文] 后直接复用,不再反问用户
```

`app/memory/session.py` 的 `SessionMemory` 第一版就是 Python dict（之后可替换 Redis）。

### Step 10：异常恢复 —— 故意制造错误

```bash
python scripts/run_fault_demo.py
```

对工具注入 10% timeout / 5% error，然后观察：

```text
Tool Call → Timeout → Retry → Retry → 仍失败 → Fallback → Human Ticket
```

实测（连跑 20 次）：11 次出现重试，1 次重试耗尽转为人工工单；强制 100% 超时时链路：

```text
query_order          attempts=3  -> FAIL(timeout)
create_human_ticket  attempts=1  -> ok
状态   : escalated
回答   : 您的情况需要人工处理,已创建工单T202609270001 …
```

> 面试可用的一句话：不是把 Agent 做成理想环境下的 Demo，而是针对真实业务中的工具失败，
> 设计了 Retry、Fallback 和人工接管机制。

---

## Step 11 → 20：从「能调工具」到「可上线的 Agent 服务」

> 状态说明：11 / 12 / 13 / 14-16 / 17 / 19 都在本机真实跑通过（下面都是实测输出）；
> **18（Postgres/Redis）和 20（Docker）本机没有服务/Docker，只产出了代码与配置，
> 未在本机验证**，详见 [DEPLOY.md](DEPLOY.md)。

### Step 11：工具权限系统（受控地调用工具）

```bash
python scripts/run_permission_demo.py     # 离线,不需要 LLM
```

`Agent → PermissionManager → {YES → Tool | CONFIRM → 用户确认 | NO → Human}`，
金额阈值可配置（`PermissionPolicy(auto_max, confirm_max)`）。实测：

```text
金额 50 元  ->  AUTO     工具链=['create_refund_request']  status=completed
金额 250 元 ->  CONFIRM  挂起:操作「create_refund_request」需要您确认(金额250.0元需用户二次确认)
                        用户确认后 工具链=['create_refund_request'] status=completed
金额 900 元 ->  HUMAN    工具链=['create_human_ticket']  status=escalated
                        已创建工单T202609270001,原因:金额900.0元超过500元,需人工审批
```

| 操作 | 模式 |
| --- | --- |
| `query_order` / `query_logistics` / `search_after_sales_policy` | AUTO |
| `create_return_request` | AUTO |
| `create_refund_request`（100~500 元） | CONFIRM（human-in-the-loop） |
| `create_refund_request`（>500 元） | HUMAN_APPROVAL |

最小版 Agent 也接了同一道门（`app/agent/minimal_agent.py`），所以 LLM 路径同样受控。

### Step 12：把工具改造成 MCP

```bash
python scripts/run_mcp_agent.py --probe   # 只探测工具,不需要 LLM
python scripts/run_mcp_agent.py           # 完整 Agent -> MCP -> DB
```

三个 MCP Server（独立进程，stdio）：`order-mcp` / `logistics-mcp` / `aftersale-mcp`。
实测：

```text
[mcp] 已连接 3 个 MCP Server
  - order-mcp: query_order, query_order_items
  - logistics-mcp: query_logistics, query_delivery_status
  - aftersale-mcp: create_return_request, create_refund_request, create_human_ticket
用户: 我的耳机坏了,我想退货。
  step1 [MCP] query_order({'user_id': 'U10003'}) -> ok
  step2 [MCP] create_return_request({'order_id': 'O10003', ...}) -> ok
  回答: 退货申请单号 R20260927001
```

关键是 Agent 侧**完全无感**：`MCPToolBridge.to_registry()` 把 MCP 工具转成标准
`ToolRegistry`，所以权限门 / Trace / 并发照常工作。

### Step 13：Trace（记录整个生命周期）

```bash
python scripts/run_trace_demo.py
```

```text
T202610034EFC36   (total 5.84ms)
├── user_input
├── intent  (RETURN_REQUEST)
├── plan
├── decision  (query_order)
├── permission  (mode=auto)
├── tool  query_order  (1.38ms, retry=0)
├── decision  (search_after_sales_policy)
├── tool  search_after_sales_policy  (3.6ms, retry=0)
├── decision  (create_return_request)
├── tool  create_return_request  (0.52ms, retry=0)
├── decision  (answer)
└── final  (退货申请已创建…)
```

每条 span 记录 timestamp / node / input / output / tool / arguments / latency / tokens /
error / retry_count，落 `traces/agent_traces.jsonl`。

### Step 14 / 15 / 16：Eval / Bad Case / Regression

```bash
python -m eval.evaluator.run_eval                      # 200 cases,离线,确定性 brain
python -m eval.evaluator.run_eval --limit 20 --brain llm   # 真 LLM 子集
python -m eval.evaluator.run_eval --show-bad-cases
```

200 条 Case（7 类意图 × 多措辞 × 演示用户），跑的就是真实 Agent。基线实测：

```text
| Intent Accuracy   | 100.0% |
| Tool Selection    | 100.0% |
| Tool Arguments    | 100.0% |
| Task Success      | 100.0% |
| Policy Compliance | 100.0% |
| Overall Pass      | 100.0% |
| Avg Latency       | 1.55 ms |
```

> 这些数字来自本次真实运行（报告落 `eval/reports/`），不是编的。第一次跑基线是
> **89.5% / 91.5% / 83.5%**，失败项暴露了 2 个真实缺陷（意图识别缺词、以及评测规格里
> HUMAN 的禁区写错）—— 修完才到 100%。

**Regression**：把「质量问题 15 天」这个业务规则改坏（改成 1 天）再跑一次并对比：

```text
baseline : rule(quality=15)  passed=200
candidate: rule(quality=1)   passed=196
  tool_selection_accuracy    ▼ -2.0%
  task_success_rate          ▼ -2.0%
新增回归 (pass -> fail): 4 ['case080', 'case153', 'case160', 'case190']
⚠ 只看总体通过率会漏掉回归 —— 上面这些用例是变差的,必须逐条看。
```

Bad Case 会自动归类（Intent Classification / Tool Selection / Tool Argument /
Task Execution / Policy Violation）并给出根因提示，落 `eval/bad_cases/bad_cases.jsonl`。

### Step 17：FastAPI 服务

```bash
python -m uvicorn app.api.server:app --port 8077
# 工作台: http://localhost:8077/    健康检查: /health
```

| 端点 | 说明 |
| --- | --- |
| `POST /api/chat` | `{user_id, message}` → `{task_id, status, answer, tools, pending}` |
| `POST /api/confirm` | 处理权限门的二次确认 |
| `GET /api/tasks/{task_id}` | 任务详情 |
| `GET /api/traces/{task_id}` | 该任务的完整 trace |
| `GET /api/eval/runs` | 历史评测报告 |
| `GET /api/bad-cases` | Bad Case 列表 |
| `GET /` | 极简客服工作台（含确认流程） |

实测（LLM 模式）：

```text
POST /api/chat  {"user_id":"U10001","message":"请帮我给订单O10001退款200元"}
-> {"status":"awaiting_confirmation","pending":{"tool":"create_refund_request",...},
    "tools":["query_order"]}
POST /api/confirm {"session_id":"s9","confirm":true}
-> {"status":"completed","answer":"退款申请已成功创建…退款单号 RF20260927001",
    "tools":["query_order","create_refund_request"]}
```

### Step 18：PostgreSQL + Redis ⚠️ 未在本机验证

- `app/store_pg.py` —— `PgStore`，与 SQLite 版 `Store` **同方法签名**（`BusinessTools`
  无需改动），含全部业务表 + `agent_tasks` / `agent_traces` / `eval_cases` / `bad_cases` DDL。
- `app/memory/redis_store.py` —— `RedisSessionMemory`，与 `SessionMemory` 同接口，
  多副本共享 Session / Agent Context / Task State。
- 依赖 `requirements-db.txt`（psycopg / redis，均为惰性导入）。

### Step 19：并发执行

```bash
python scripts/run_async_demo.py
```

```text
串行 (Order -> Logistics -> Policy) :   909.0 ms
并发 线程池 gather                  :   306.7 ms
并发 asyncio.gather                 :   309.4 ms
加速比: 串行/并发 = 2.96x
```

`app/agent/async_tools.py` 提供 `gather()` / `gather_async()`；最小版 Agent 打开
`parallel_tools=True` 后，同一轮里多个「自动放行」的工具调用会并发执行，结果仍按原顺序
回灌（有 confirm/human 的批次自动退回串行）。

### Step 20：Docker ⚠️ 未在本机验证

`docker-compose.yml`：`agent-api` + `postgres` + `redis` + `mcp-order` +
`mcp-logistics` + `mcp-aftersale`（+ 由 API 直接托管的极简前端）。配
`docker/Dockerfile.api`、`docker/Dockerfile.mcp`、`docker/postgres/init.sql`。

```bash
docker compose up --build      # 目标形态;本机(WSL2)无 docker,未验证
```

---

## 六个核心场景

| # | 场景 | 示例输入 | Agent 动作 |
| --- | --- | --- | --- |
| 1 | 查询订单 | 我的订单什么时候发货？ | `query_order` → 返回订单与发货信息 |
| 2 | 查询物流 | 我的快递到哪里了？ | `query_order` → `query_logistics` → 返回物流状态 |
| 3 | 售后政策咨询 | 耳机用了 5 天还能退吗？ | `query_order` → `search_after_sales_policy` → 结合订单状态给结论 |
| 4 | 申请退货 | 耳机坏了，我要退货。 | 查订单 → 检索政策 → 判断资格 → `create_return_request`（核心场景） |
| 5 | 退款查询 | 商品已经退回去了，什么时候退款？ | 查订单 → 查退货/退款记录 → 返回退款信息 |
| 6 | 异常人工接管 | 超过售后期限但确实坏了，金额超 1000 | 识别高风险 → 权限校验 → `create_human_ticket` |

场景 6 体现项目的核心主张：**Agent 不是无限制自主行动，而是在权限和业务规则约束下行
动。**

---

## 执行流程 / Agent Loop

```text
用户输入
   ↓
意图识别 (RuleBasedIntentRecognizer)
   ↓
任务规划 (build_plan)
   ↓
┌─ Agent Loop（最多 max_steps 步）────────────────────┐
│  Brain.decide(ctx) → tool / answer / escalate        │
│    ├─ tool     : 权限校验 → ToolExecutor.execute      │
│    │              → 观察结果 → 进入下一轮决策          │
│    ├─ answer   : 输出最终回答                         │
│    └─ escalate : 创建人工工单                         │
└──────────────────────────────────────────────────────┘
   ↓
Trace 落盘 (traces/agent_traces.jsonl)
```

每一步的工具结果都真实进入下一步决策的上下文——这正是 LLM Brain 必须遵守的同一份
`decide(ctx) -> Decision` 契约。

任务状态可能为：`completed` / `escalated` / `awaiting_confirmation` / `cancelled` / `failed`。

---

## 目录结构

```text
business-task-agent/
├── DESIGN.md                     # 完整设计文档（背景、需求、架构、路线图）
├── app/
│   ├── agent/
│   │   ├── intents.py            # 意图识别（7 类，正则规则）
│   │   ├── planner.py            # 任务规划（意图 → 执行步骤）
│   │   ├── brain.py              # RuleBasedBrain：确定性决策
│   │   ├── llm_brain.py          # OpenAIBrain：LLM function calling 决策
│   │   ├── llm_client.py         # OpenAI 兼容 Chat/Embeddings 客户端（GLM 接入）
│   │   ├── minimal_agent.py      # 最小版 Agent：无 Planner 的 LLM↔Tool 循环
│   │   ├── permissions.py        # 权限分级（金额阈值 + 风险等级）
│   │   └── runtime.py            # AgentRuntime：执行主循环 + 人工接管
│   ├── tools/
│   │   ├── registry.py           # ToolRegistry + ToolExecutor（超时/重试/退避/分类）
│   │   ├── business.py           # 6 个业务工具 + 最小版 3/4 工具注册表
│   │   └── faults.py             # 故障注入（10% timeout / 5% error）
│   ├── data/generator.py         # 业务数据生成器（1000/3000/5000/5000/500）
│   ├── rag/
│   │   ├── tokenize.py           # 中文 bigram 分词 + query rewrite
│   │   ├── embeddings.py         # GLMEmbedder / HashingEmbedder
│   │   ├── vector_store.py       # 余弦向量索引
│   │   └── policy_rag.py         # RAG 流水线（rewrite→embed→search→rerank）
│   ├── memory/
│   │   ├── context.py            # TaskContext + MemoryStore（完整版）
│   │   └── session.py            # SessionMemory（最小版，dict→可换 Redis）
│   ├── trace/
│   │   ├── tracer.py             # Tracer / Span（JSONL 落盘）
│   │   └── view.py               # Trace 树状渲染
│   ├── api/server.py             # Step 17：FastAPI 服务（+ static/ 工作台）
│   ├── mcp/
│   │   ├── bridge.py             # Step 12：MCP Client 桥接 -> ToolRegistry
│   │   └── servers/              # order / logistics / aftersale 三台 MCP Server
│   ├── store.py                  # SQLite 数据层（种子 / 生成库 / 临时副本）
│   └── store_pg.py               # Step 18：PostgreSQL 版（未在本机验证）
├── data/
│   ├── business.db               # 生成的"假企业业务系统"（SQLite）
│   └── policies/*.md             # 售后知识库（66 份规则文档）
├── eval/                         # Step 14~16：dataset / evaluator / reports / bad_cases
├── scripts/
│   ├── generate_data.py          # 生成业务数据
│   ├── generate_policies.py      # 生成售后知识库
│   ├── run_minimal_agent.py      # Step 3~6：最小版 Agent
│   ├── run_planner_demo.py       # Step 7：Planner
│   ├── run_memory_agent.py       # Step 9：多轮记忆
│   ├── run_fault_demo.py         # Step 10：故障注入 / Retry / 人工接管
│   ├── run_permission_demo.py    # Step 11：AUTO / CONFIRM / HUMAN
│   ├── run_mcp_agent.py          # Step 12：Agent -> MCP -> DB
│   ├── run_trace_demo.py         # Step 13：Trace 树
│   ├── run_async_demo.py         # Step 19：串行 vs 并发
│   └── run_demo.py               # 完整版六场景 Demo 入口
├── tests/                        # 94 个测试
├── docker/                       # Step 20：Dockerfile + postgres/init.sql（未验证）
├── docker-compose.yml            # Step 20：一键起（未验证）
├── requirements-optional.txt     # mcp / fastapi / uvicorn
├── requirements-db.txt           # psycopg / redis（未验证）
└── DEPLOY.md                     # 部署说明 + 本机验证状态
```

---

## 核心模块

### 意图识别 `app/agent/intents.py`
支持 `ORDER_QUERY` / `LOGISTICS_QUERY` / `AFTERSALE_POLICY` / `RETURN_REQUEST` /
`REFUND_QUERY` / `HUMAN_SERVICE` / `UNKNOWN` 七类意图。V1 用有序正则规则，首个命中即
返回（优先级：转人工 > 退货 > 退款 > 政策 > 物流 > 订单），确定性且可测试；接口与实现
分离，后续可替换为 LLM 分类器。

### 任务规划 `app/agent/planner.py`
按意图输出执行步骤模板，例如退货场景：查询订单 → 查询商品 → 检索售后政策 →
判断退货资格 → 创建退货申请或转人工 → 返回结果。这些步骤由 Runtime 真实执行。

### 决策大脑 `app/agent/brain.py`、`llm_brain.py`
两者实现同一契约 `decide(ctx) -> Decision`，`Decision.kind` 为 `tool` / `answer` / `escalate`：
- `RuleBasedBrain`：纯函数式决策，完全确定性，用于 Demo 与测试。
- `OpenAIBrain`：把注册表里的工具 schema 交给 LLM，解析 `tool_calls` 或最终回答；
  同时兼容模型把 `{"tool": ...}` 当文本吐出的情况。

### 工具层 `app/tools/registry.py`、`business.py`
`ToolRegistry` 集中声明工具（名称、JSON Schema、handler、风险等级），可直接导出
OpenAI function-calling schema；`ToolExecutor` 为每次调用包裹超时、重试、指数退避与
错误分类，使 Agent 从不假设工具一定成功。

### 权限控制 `app/agent/permissions.py`
按风险等级与金额阈值返回 `auto` / `confirm` / `human`；`confirm` 时任务挂起，
由 `AgentRuntime.resume(session_id, confirm=...)` 继续或取消。阈值可由构造函数配置，
属于业务配置而非硬编码。

### RAG `app/rag/`
流水线：`Query Rewrite → Embedding → Vector Search → Rerank`。
`tokenize.py` 做中文 bigram 分词与 rewrite；`embeddings.py` 提供 `GLMEmbedder`
（GLM `/embeddings`）与离线确定性的 `HashingEmbedder`；`vector_store.py` 是余弦索引；
`policy_rag.py` 把「向量相似度」与「词面命中（标题/章节加权，语料级停用词降权）」融合
成最终 Rerank 分。embedder 用依赖注入：默认离线，需要真语义检索时注入 `GLMEmbedder`。

### 记忆 `app/memory/`
`context.py`：`TaskContext` 保存意图、计划、槽位（order_id / product / risk_level）、工具
观测与历史；`MemoryStore` 以会话为粒度，新一轮继承上一轮的槽位与历史。`session.py`：
最小版用的 `SessionMemory`，从工具结果里抽取任务槽位并渲染成注入下一轮的 `[会话上下文]`
——第一版就是 Python dict，接口不变即可换成 Redis。

### 数据层 `app/store.py`
SQLite（默认内存库）+ 种子数据，表结构对齐 DESIGN.md 第 8 节
（users / products / orders / order_items / logistics / after_sale_requests / refunds /
tickets）。V2 换成真实 PostgreSQL DAO 时无需改动工具层。Demo 的「今天」固定为
`2026-09-27` 以保证时效判断可复现。

---

## 工具与风险等级

| 工具 | 作用 | 风险 |
| --- | --- | --- |
| `query_order` | 查询用户订单（含商品、物流概要、售后与退款记录） | LOW |
| `query_logistics` | 查询订单物流状态、承运商与最新轨迹 | LOW |
| `search_after_sales_policy` | RAG 检索售后政策知识库 | LOW |
| `create_return_request` | 创建退货申请 | MEDIUM |
| `create_refund_request` | 创建退款申请（金额受分级约束） | HIGH |
| `create_human_ticket` | 创建人工工单，转交人工客服 | LOW |

---

## 权限分级

退款金额阈值（可在 `PermissionPolicy(auto_max, confirm_max)` 配置）：

```text
< 100 元      → auto      自动执行
100 ~ 500 元  → confirm   需用户二次确认（human-in-the-loop）
> 500 元      → human     需人工审批
```

此外，风险等级为 `HIGH` / `CRITICAL` 的操作一律走人工审批。
退货场景还叠加业务规则：大额商品（≥ 1000 元）退货、超期但主张质量问题的退货，都会
转入人工审核，而不是直接执行。

---

## 错误处理

工具失败不会让整个 Agent 崩溃（DESIGN.md 5.9）：

```text
超时 / 可重试错误  → Retry → Exponential Backoff → 仍失败 → Fallback 转人工
```

- 错误分类：`timeout` / `not_found` / `validation` / `execution` / `permission`
- **可重试**错误（如超时、临时故障）按 `backoff_base * 2^n` 退避重试，受
  `max_retries` 与 `backoff_max` 约束
- **不可重试**错误（参数校验失败、未知工具）立即返回，不浪费重试
- handler 内的意外异常按 `execution` 记录，不重试
- 重试耗尽后由 Runtime 自动创建人工工单（Fallback）

---

## Trace 执行链路

每次运行生成一条 Trace，追加写入 `traces/agent_traces.jsonl`：

```json
{
  "trace_id": "T20260927XXXXXX",
  "total_latency_ms": 3.1,
  "spans": [
    {"node": "user_input", "input": "耳机坏了,我要退货。"},
    {"node": "intent",     "output": "RETURN_REQUEST"},
    {"node": "plan",       "output": ["查询订单", "..."]},
    {"node": "decision",   "output": {"kind": "tool", "tool": "query_order"}},
    {"node": "permission", "output": {"mode": "auto", "risk_level": "LOW"}},
    {"node": "tool",       "tool": "query_order", "latency_ms": 1.2, "retry_count": 0},
    {"node": "final",      "output": "退货申请已创建…"}
  ]
}
```

记录字段：`timestamp / node / input / output / tool / arguments / latency_ms / tokens /
error / retry_count`，供后续 Eval 引擎与 Bad Case 分析回放整条执行链路。

---

## 配置项

| 环境变量 | 作用 | 默认 |
| --- | --- | --- |
| `GLM_API_KEY` | 最小版 Agent 调 GLM 的密钥（优先） | 未设置 |
| `GLM_BASE_URL` | GLM 端点 | `https://open.bigmodel.cn/api/paas/v4` |
| `GLM_MODEL` | GLM 模型名 | `glm-4.6`（可选 glm-4-flash / glm-4-plus / glm-4-air …） |
| `AGENT_RAG_EMBEDDINGS` | 设为 `glm` 用 GLM embedding 做语义检索，否则离线 | 离线 `HashingEmbedder` |
| `OPENAI_API_KEY` | 回退密钥；设置后 `run_demo.py` 使用 `OpenAIBrain` | 未设置 |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | 回退端点 / 模型 | `https://api.openai.com/v1` / `gpt-4o-mini` |
| `AGENT_DB_PATH` | 业务库路径（`open_store()`） | `data/business.db` |
| `DATABASE_URL` / `REDIS_URL` | Step 18：Postgres / Redis 连接串（⚠️ 未在本机验证） | 见 `DEPLOY.md` |
| `AGENT_DEMO_TODAY` | 覆盖 Demo 的「今天」（`YYYY-MM-DD`），影响时效判断 | `2026-09-27` |

运行时参数（如 `AgentRuntime(max_steps=8)`、`ToolExecutor(max_retries, timeout_s,
backoff_base, backoff_max)`、`PermissionPolicy(auto_max, confirm_max)`）均可由构造函数
注入，便于测试与调参。

---

## 测试

```bash
python -m pytest -q          # 94 passed
```

覆盖范围（`tests/`）：

| 文件 | 覆盖 |
| --- | --- |
| `test_agent_runtime.py` | 六个场景端到端、多轮记忆、权限分级、二次确认后 resume、工具失败 Fallback、Trace 链路完整性 |
| `test_minimal_agent.py` | 最小版 3/4 工具、无 Planner 的 LLM↔Tool 循环、工具结果回灌、未知工具容错、最大步数、动态提示词、跨轮记忆、**权限门（AUTO/CONFIRM/HUMAN）、同轮并发** |
| `test_data_generator.py` | 精确规模（1000/3000/5000/5000/500）、确定性、引用完整性、演示订单干净、重复生成不累加 |
| `test_rag_pipeline.py` | HashingEmbedder 确定性与相似度、GLMEmbedder（假 client）、余弦索引排序、知识库规模、RAG 流水线 |
| `test_faults.py` | 故障注入不改动原注册表、重试耗尽 → 人工工单、只影响指定工具、seed 可复现 |
| `test_trace_and_async.py` | Trace 树渲染、并发保序且结果一致、并发确实更快、asyncio 版本 |
| `test_mcp_bridge.py` | 真实拉起 3 台 MCP Server、工具发现、入参 schema 正确、stdio 往返调用（无 mcp 包时 skip） |
| `test_eval_harness.py` | 数据集规模/确定性、真实评测指标、Bad Case 分类、Regression pass→fail 检测 |
| `test_deploy_artifacts.py` | DDL 与 init.sql 表名一致、PgStore 与 Store 同接口、无依赖也能 import、compose/Dockerfile/requirements 齐备 |
| `test_intents.py` | 七类意图识别与优先级 |
| `test_llm_brain.py` | LLM Brain 解析 tool_calls / 最终回答（本地 fake server，无网络） |
| `test_policy_rag.py` | query rewrite、检索与 rerank、政策命中 |
| `test_registry.py` | 注册表、schema 导出、超时/重试/退避/错误分类 |
| `test_tools.py` | 六个业务工具的行为与边界 |

---

## 路线图

按 DESIGN.md 第 9 节分阶段演进，当前 V1 已完成。构建路径（也是本仓库的分阶段交付）：

- **数据准备** ✅ 自己造"假的企业业务系统"（不接真实淘宝/京东）
- **Step 3** ✅ 接入 GLM，最小 Agent 循环（无 Planner）
- **Step 4** ✅ 只做三个工具，跑通三个展示问题
- **Step 5** ✅ 加入 create_return_request，真正「完成任务」而非「回答」
- **Step 6** ✅ Agent Loop：下一步由 LLM 决定，流程不写死
- **Step 7** ✅ Planner：复杂任务先规划，Runtime 负责可靠执行
- **Step 8** ✅ RAG：66 份规则文档 + Embedding/向量检索/Rerank
- **Step 9** ✅ Context / Memory：跨轮记住任务状态
- **Step 10** ✅ 异常恢复：故障注入 + Retry + Fallback + 人工接管
- **Step 11** ✅ 权限系统：AUTO / CONFIRM / HUMAN_APPROVAL
- **Step 12** ✅ 工具 MCP 化：Order / Logistics / AfterSale 三台 MCP Server
- **Step 13** ✅ Trace：整个生命周期的树状链路
- **Step 14** ✅ Eval：200 条 Case + Intent/Tool/Success/Compliance 指标
- **Step 15** ✅ Bad Case：自动归类 + 根因提示
- **Step 16** ✅ Regression：pass→fail 逐条对比
- **Step 17** ✅ FastAPI：`/api/chat` 等 6 个端点 + 工作台
- **Step 18** ⚠️ PostgreSQL + Redis：代码与 DDL 已备，**本机无服务，未验证**
- **Step 19** ✅ 异步：并发执行独立工具调用（2.96x）
- **Step 20** ⚠️ Docker：compose 与 Dockerfile 已备，**本机无 docker，未验证**
- **V1（已完成）** Python / OpenAI 兼容 LLM API / SQLite / 简单 Tool Calling / RAG
- **V2** FastAPI / PostgreSQL / Redis / Agent Runtime / Trace
- **V3** MCP / Permission / Human-in-the-loop / Retry / Fallback
- **V4** Eval / Bad Case / Regression / Dashboard
- **V5** Docker / Async / Monitoring / 生产级部署

后续将业务工具改造成 MCP Server（Order / Logistics / AfterSale），由 Agent 动态发现
工具；并补齐 Eval Dataset（100~300 条）、Bad Case 自动记录与 Regression 评测。

---

## 与 DESIGN.md 的关系

[`DESIGN.md`](DESIGN.md) 是本项目的完整设计与需求文档（背景、目标、核心场景、功能与非
功能需求、总体架构、数据库设计、V1→V5 路线图）。本 README 描述**已经落地的 V1 实现**；
各模块 docstring 中标注了对应的 DESIGN.md 章节（如 `5.9` 错误处理、`5.8` 权限控制）。
