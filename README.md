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
- [最小版 Agent（Step 3 / Step 4）](#最小版-agentstep-3--step-4)
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
| RAG | 售后政策知识库检索（query rewrite → 关键词打分 → rerank），无需 embedding |
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

# 2) 最小版 Agent（Step 3/4）：3 个工具 + 3 个问题，需要 GLM_API_KEY
python scripts/run_minimal_agent.py

# 3) 完整版六场景 Demo（默认确定性 RuleBasedBrain，无需任何 API Key）
python scripts/run_demo.py

# 4) 运行测试
python -m pytest -q
```

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

## 最小版 Agent（Step 3 / Step 4）

设计里的那张图，就是一个完整实现 —— **没有 Planner**：

```text
User  →  LLM  →  Tool  →  Tool Result  →  LLM  →  Answer
```

```bash
python scripts/run_minimal_agent.py
```

* 只暴露 3 个工具：`query_order` / `query_logistics` / `search_after_sales_policy`
  （`app/tools/business.py::build_minimal_registry`）。
* LLM 由 function calling 自主决定调哪个工具；工具结果以标准 `role: tool` 消息回灌，
  直到模型给出最终回答（`app/agent/minimal_agent.py`）。
* 无执行计划、无权限门、无人工接管 —— 这些在完整版 `AgentRuntime` 里，最小版刻意省略。

**接入 GLM**（OpenAI 兼容，沿用最熟悉的方式）：

```bash
export GLM_API_KEY=...                                   # 或写进 ~/.hermes/.env
export GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4 # 默认值
export GLM_MODEL=glm-4-flash                             # 可选 glm-4.6 / glm-4-plus
```

实测输出（真实调用 `glm-4-flash`）：

```text
[llm] model=glm-4-flash base_url=https://open.bigmodel.cn/api/paas/v4
[tools] query_order, query_logistics, search_after_sales_policy

=== 问题1｜U10001｜我的订单什么时候发货? ===
  step1  tool  query_order({'user_id': 'U10001'})  -> ok
  状态   : completed   轮次: 2
  回答   : 您的订单O10001已经支付，但尚未发货…

=== 问题2｜U10002｜我的快递到哪里了? ===
  step1  tool  query_order({'user_id': 'U10002'})        -> ok
  step2  tool  query_logistics({'order_id': 'O10002'})   -> ok
  状态   : completed   轮次: 3
  回答   : 您的快递正在运输中，目前位于武汉洪山转运中心…

=== 问题3｜U10003｜这个耳机能退吗? ===
  step1  tool  query_order({'user_id': 'U10003'})                        -> ok
  step2  tool  search_after_sales_policy({'query': '耳机退货政策'})       -> ok
  状态   : completed   轮次: 3
  回答   : 根据查询到的信息，您购买的无线耳机支持7天无理由退货…
```

这一步跑通之后，就有了第一个可以展示的 Agent。完整版（Planner + 6 工具 + 权限 +
人工接管 + Trace）仍然保留在 `app/agent/runtime.py`，两者共用同一套工具层与数据层。

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
│   │   ├── llm_client.py         # OpenAI 兼容 Chat 客户端（GLM 接入）
│   │   ├── minimal_agent.py      # 最小版 Agent：无 Planner 的 LLM↔Tool 循环
│   │   ├── permissions.py        # 权限分级（金额阈值 + 风险等级）
│   │   └── runtime.py            # AgentRuntime：执行主循环 + 人工接管
│   ├── tools/
│   │   ├── registry.py           # ToolRegistry + ToolExecutor（超时/重试/退避/分类）
│   │   └── business.py           # 6 个业务工具 + 最小版 3 工具注册表
│   ├── data/generator.py         # 业务数据生成器（1000/3000/5000/5000/500）
│   ├── rag/policy_rag.py         # 售后政策 RAG（rewrite → 打分 → rerank）
│   ├── memory/context.py         # TaskContext + MemoryStore（会话级记忆）
│   ├── trace/tracer.py           # Tracer / Span（JSONL 落盘）
│   └── store.py                  # SQLite 数据层（种子数据 / 打开生成的业务库）
├── data/
│   ├── business.db               # 生成的"假企业业务系统"（SQLite）
│   └── policies/*.md             # 售后政策知识库（8 个规则文档）
├── scripts/
│   ├── generate_data.py          # 生成业务数据
│   ├── run_minimal_agent.py      # 最小版 Agent：3 工具 + 3 问题
│   └── run_demo.py               # 完整版六场景 Demo 入口
├── tests/                        # 49 个测试
├── eval/                         # 评测数据集 / 评测器 / 报告（预留）
└── docker/                       # 容器化（预留）
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

### RAG `app/rag/policy_rag.py`
对 `data/policies/*.md` 做确定性检索：query rewrite（去停用词）→ 中文 bigram + 拉丁词
打分（越稀有的词权重越高，语料级停用词降权）→ rerank（标题/章节命中加权）。无需
embedding，`retrieve()` 即后续替换为向量检索 + rerank 模型的接入点。

### 记忆 `app/memory/context.py`
`TaskContext` 保存意图、计划、槽位（order_id / product / risk_level）、工具观测与历史；
`MemoryStore` 以会话为粒度，新一轮继承上一轮的槽位与历史，从而支持「昨天买的那个」
这类跨轮指代。

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
| `GLM_MODEL` | GLM 模型名 | `glm-4-flash`（另有 glm-4.6 / glm-4-plus / glm-4-air …） |
| `OPENAI_API_KEY` | 回退密钥；设置后 `run_demo.py` 使用 `OpenAIBrain` | 未设置 |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | 回退端点 / 模型 | `https://api.openai.com/v1` / `gpt-4o-mini` |
| `AGENT_DB_PATH` | 业务库路径（`open_store()`） | `data/business.db` |
| `AGENT_DEMO_TODAY` | 覆盖 Demo 的「今天」（`YYYY-MM-DD`），影响时效判断 | `2026-09-27` |

运行时参数（如 `AgentRuntime(max_steps=8)`、`ToolExecutor(max_retries, timeout_s,
backoff_base, backoff_max)`、`PermissionPolicy(auto_max, confirm_max)`）均可由构造函数
注入，便于测试与调参。

---

## 测试

```bash
python -m pytest -q          # 49 passed
```

覆盖范围（`tests/`）：

| 文件 | 覆盖 |
| --- | --- |
| `test_agent_runtime.py` | 六个场景端到端、多轮记忆、权限分级、二次确认后 resume、工具失败 Fallback、Trace 链路完整性 |
| `test_minimal_agent.py` | 最小版 3 工具、无 Planner 的 LLM↔Tool 循环、工具结果回灌、未知工具容错、最大步数（本地 fake server，无网络） |
| `test_data_generator.py` | 精确规模（1000/3000/5000/5000/500）、确定性、引用完整性、演示用户、重复生成不累加 |
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
