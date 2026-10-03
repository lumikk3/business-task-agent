# Business Task Agent Runtime

## 企业业务任务型 Agent 执行平台

### 1. 项目概述

#### 1.1 项目背景

传统大模型应用通常采用:

> 用户提问 → LLM → 生成答案

这种模式能够完成信息问答,但无法真正完成复杂业务任务。

例如电商售后场景中,用户说:

> "我的耳机昨天收到就坏了,我想退货。"

系统不仅需要理解用户意图,还需要:

1. 判断用户具体诉求;
2. 获取用户订单;
3. 查询商品和订单状态;
4. 检索对应售后政策;
5. 判断是否满足退货条件;
6. 决定下一步需要调用什么工具;
7. 创建退货申请;
8. 获取工具执行结果;
9. 根据结果继续执行或处理异常;
10. 对高风险操作进行权限控制;
11. 必要时转人工;
12. 记录完整执行过程;
13. 对 Agent 的执行结果进行评测。

因此,本项目设计一个面向企业业务任务的 Agent Runtime。

项目首先以"电商售后"作为 Demo 场景,验证 Agent 在真实业务任务中的自主决策和工具执行能力。

---

## 2. 项目目标

### 2.1 核心目标

构建一个能够完成以下闭环的 Agent:

```text
用户任务
   ↓
任务理解
   ↓
任务规划
   ↓
上下文获取
   ↓
知识检索
   ↓
工具选择
   ↓
工具调用
   ↓
观察工具结果
   ↓
继续决策
   ↓
异常恢复 / 权限判断
   ↓
任务完成 / 人工接管
   ↓
Trace记录
   ↓
Eval评测
```

---

### 2.2 项目最终需要证明的能力

本项目最终需要证明的不只是"会调用大模型"。

而是证明:

#### Agent能力

* Prompt Engineering
* Agent Loop
* Task Planning
* Tool Calling
* Function Calling
* Context Management
* Memory
* RAG
* MCP
* Workflow / State Machine
* Multi-step Task Execution

#### Agent工程能力

* Tool Registry
* Agent Runtime
* Retry
* Timeout
* Fallback
* Error Handling
* Permission Control
* Human-in-the-loop
* Async Task
* Logging
* Trace
* Monitoring

#### Agent质量保障

* Eval Dataset
* 自动化评测
* Bad Case分析
* Regression Test
* Tool Call Accuracy
* Task Success Rate
* Policy Compliance
* Hallucination检测

#### 后端工程能力

* Python
* FastAPI
* PostgreSQL
* Redis
* Docker
* Linux
* Git
* Async / Concurrency

---

## 3. 目标用户

第一阶段不需要真的面向消费者上线。

主要用户是:

### 3.1 电商客服

用于处理:

* 查询订单
* 查询物流
* 售后政策咨询
* 退货
* 换货
* 退款
* 人工转接

### 3.2 客服管理人员

关注:

* Agent完成了多少任务
* 哪些任务失败
* 哪些问题经常转人工
* 哪些工具容易出错
* Agent是否错误执行了业务操作

### 3.3 Agent开发人员

关注:

* Agent执行Trace
* Tool Call过程
* Prompt版本
* Eval结果
* Bad Case
* Regression结果
* Latency
* Token Cost

因此最终系统实际上有两个视角:

```text
业务使用端
    ↓
客服 / 用户
    ↓
Agent

工程管理端
    ↓
开发者 / 管理员
    ↓
Trace / Eval / Bad Case / Monitoring
```

---

## 4. 核心业务场景

第一版不要做几十个场景,只做下面6个。

### 场景1:查询订单

用户:

> 我的订单什么时候发货?

Agent:

```text
识别 → 查询订单 → 查询物流 → 返回结果
```

---

### 场景2:查询物流

用户:

> 我的快递到哪里了?

Agent:

```text
识别物流查询
    ↓
获取订单
    ↓
查询物流
    ↓
返回物流状态
```

---

### 场景3:售后政策咨询

用户:

> 耳机用了5天还能退吗?

Agent:

```text
识别售后问题
    ↓
获取商品信息
    ↓
RAG检索售后政策
    ↓
结合订单状态判断
    ↓
回答
```

---

### 场景4:申请退货

用户:

> 耳机坏了,我要退货。

```text
理解任务
 ↓
查询订单
 ↓
查询商品
 ↓
检索售后政策
 ↓
判断是否符合条件
 ↓
创建退货申请
 ↓
返回申请结果
```

这个场景是整个项目的核心。

---

### 场景5:退款

用户:

> 商品已经退回去了,什么时候退款?

Agent:

```text
查询订单
 ↓
查询退货状态
 ↓
查询退款状态
 ↓
返回退款信息
```

---

### 场景6:异常情况人工接管

例如:

> "商品已经超过售后期限,但是商品确实坏了,而且退款金额超过1000元。"

Agent不能直接执行。

应该:

```text
识别高风险
 ↓
检查权限
 ↓
无法自动执行
 ↓
创建人工工单
 ↓
记录原因
 ↓
通知用户
```

这个场景非常重要。

因为它能够体现:

> **Agent不是无限制自主行动,而是在权限和业务规则约束下行动。**

---

## 5. 功能需求

### 5.1 Agent入口

用户输入自然语言:

```text
我的耳机坏了,我想退货
```

系统创建:

```text
Task ID
Session ID
User ID
```

然后进入 Agent Runtime。

### 5.2 意图识别

Agent需要识别:

```text
ORDER_QUERY
LOGISTICS_QUERY
AFTERSALE_POLICY
RETURN_REQUEST
REFUND_QUERY
HUMAN_SERVICE
UNKNOWN
```

例如:

```text
"我的订单什么时候发货" → ORDER_QUERY
"快递在哪里"          → LOGISTICS_QUERY
"耳机坏了我要退"       → RETURN_REQUEST
```

### 5.3 Task Planning

对于复杂任务生成执行计划。

例如:

```text
用户:我的耳机坏了,我想退货

Planner:
Step 1 查询订单
Step 2 查询商品
Step 3 查询售后政策
Step 4 判断退货资格
Step 5 创建退货申请
Step 6 返回结果
```

注意:

**Planner不是简单生成文字计划。后续 Runtime 必须真正执行这些步骤。**

### 5.4 Tool Calling

第一版实现6个工具:

```python
query_order()
query_logistics()
search_after_sales_policy()
create_return_request()
create_refund_request()
create_human_ticket()
```

例如:

```json
{
  "tool": "query_order",
  "arguments": {
    "user_id": "U10001"
  }
}
```

Tool返回:

```json
{
  "order_id": "O202609001",
  "status": "delivered",
  "product": "无线耳机",
  "price": 299,
  "delivered_at": "2026-09-23"
}
```

Agent继续根据Observation进行决策。

### 5.5 RAG

知识库主要保存售后规则:

* 7天无理由退货规则
* 质量问题售后规则
* 耳机类商品售后规则
* 食品类商品售后规则
* 特殊商品售后规则
* 退款规则
* 退货运费规则
* 人工审核规则

Agent遇到 "这个商品能不能退?" 不能直接让LLM凭记忆回答。而是:

```text
用户问题
 ↓
Query Rewrite
 ↓
Vector Search
 ↓
Rerank
 ↓
Policy Context
 ↓
LLM判断
```

### 5.6 Memory / Context

保存当前任务上下文:

```text
user_id
session_id
order_id
product_id
current_intent
current_task
previous_tool_results
current_step
risk_level
```

例如:

第一次:"我的耳机坏了。"
第二次:"就是昨天买的那个。"

Agent应该能够结合上下文理解。

### 5.7 MCP

后期将业务工具改造成MCP Server。设计三个MCP Server:

```text
Order MCP        ├── query_order        └── query_order_items
Logistics MCP    ├── query_logistics    └── query_delivery_status
AfterSale MCP    ├── create_return      ├── create_refund      └── create_human_ticket
```

Agent通过MCP动态发现工具。

### 5.8 Permission Control

不同操作具有不同风险等级:

| 操作     | 风险     |
| -------- | -------- |
| 查询订单 | LOW      |
| 查询物流 | LOW      |
| 查询政策 | LOW      |
| 创建退货 | MEDIUM   |
| 创建退款 | HIGH     |
| 大额退款 | CRITICAL |

例如:

```text
退款金额 < 100元   → Agent可以自动执行
100~500元          → 需要二次确认
>500元             → 人工审批
```

具体阈值只是Demo中的业务规则,可以配置化。

### 5.9 Error Handling

工具调用不能假设永远成功。例如 Order API Timeout:

```text
第一次请求 → Timeout → Retry → 仍失败 → Fallback → 人工处理
```

需要实现:

* Timeout
* Retry
* Exponential Backoff
* Maximum Retry
* Fallback
* Error Classification

### 5.10 Human-in-the-loop

以下情况进入人工:

```text
高金额退款
政策冲突
用户投诉
身份验证失败
工具连续失败
Agent无法确定
```

创建:

```text
Ticket ID
Reason
User
Order
Agent Trace
Recommended Action
```

客服可以继续处理。

### 5.11 Trace

每一次Agent执行都生成Trace:

```text
Trace ID: T202609270001

User Input → Intent Recognition → Planner → query_order
→ search_after_sales_policy → Policy Decision → create_return_request
→ Tool Result → Final Response
```

记录:

```text
timestamp, node, input, output, tool, arguments, latency, token, error, retry_count
```

最终形成Agent执行链路。

### 5.12 Eval

建立测试数据集(100~300条测试Case)。每条包括:

```json
{
  "input": "我的耳机坏了我要退货",
  "expected_intent": "RETURN_REQUEST",
  "expected_tools": ["query_order", "search_after_sales_policy", "create_return_request"],
  "expected_result": "return_created"
}
```

评测:

* Intent Accuracy
* Tool Selection Accuracy
* Tool Argument Accuracy
* Task Success Rate
* Policy Compliance
* Hallucination Rate
* Escalation Accuracy
* Latency
* Token Usage

### 5.13 Bad Case

如果出现 "用户要退货,Agent却调用退款工具",自动记录:

```text
Bad Case
Input / Expected / Actual / Trace / Error Type / Root Cause / Prompt Version / Model Version
```

然后修复后重新测试。

### 5.14 Regression

```text
Prompt V1 → 100 cases → Success Rate 87%
修改Prompt
Prompt V2 → 100 cases → Success Rate 91%

但是:Case #37 / #82 / #96 出现回归
```

不能只看总体准确率。

---

## 6. 非功能需求

* **可观测性** —— 每个Agent任务必须可以追踪。
* **稳定性** —— 工具失败不能直接导致整个Agent崩溃。
* **可扩展性** —— 工具通过Registry注册,新增 Coupon/Inventory/Payment/CRM 工具不需要重写Agent核心逻辑。
* **安全性** —— Authentication、Authorization、Tool Permission、Sensitive Operation Confirmation。

---

## 7. 系统总体架构

```text
                    Frontend
                       │
                       ▼
                  FastAPI API
                       │
                       ▼
                Agent Runtime
                       │
       ┌───────────────┼────────────────┐
       ▼               ▼                ▼
    Planner         Context          Memory
       │               │                │
       └───────────────┼────────────────┘
                       ▼
                 Tool Router
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
          Order      Logistics  AfterSale
           MCP         MCP        MCP
             │         │         │
             └─────────┼─────────┘
                       ▼
                  Observation
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
          Continue             Failure
                                 │
                        Retry/Fallback
                                 │
                        Human-in-loop
                       ↓
                    Trace
                       ↓
                  PostgreSQL / Redis
                       ↓
                   Eval Engine
                       ↓
               Dashboard / Reports
```

---

## 8. 数据库设计

第一版至少设计以下表:

```text
users, orders, order_items, products, logistics,
after_sale_requests, refunds, tickets,
agent_tasks, agent_traces, tool_calls,
eval_cases, eval_runs, bad_cases
```

核心关系:

```text
User └── Order ├── Product
               ├── Logistics
               └── AfterSale └── Refund

AgentTask └── AgentTrace └── ToolCall
```

---

## 9. 技术架构(V1 → V5 路线)

* **V1** Python / OpenAI-compatible LLM API / SQLite / 简单Tool Calling / RAG
* **V2** FastAPI / PostgreSQL / Redis / Agent Runtime / Trace
* **V3** MCP / Permission / Human-in-the-loop / Retry / Fallback
* **V4** Eval / Bad Case / Regression / Dashboard
* **V5** Docker / Async / Monitoring / Production-style deployment

---

## 10. 项目最终页面

1. **客服工作台** —— 对话式处理退货等任务
2. **Agent Trace** —— Task → Intent → Plan → RAG → Tool Call → Observation → Decision → Final
3. **Eval Dashboard** —— Task Success Rate / Tool Accuracy / Intent Accuracy / Policy Compliance / Hallucination Rate / Latency / Token Cost
4. **Bad Case** —— Case ID / User Input / Expected / Actual / Root Cause / Trace / Prompt Version / Status

---

## 11. 项目成功标准

* **简单任务**:查询订单、查询物流、查询政策
* **复杂任务**:退货、退款、售后
* **异常任务**:工具超时、工具返回错误、权限不足、高风险操作、人工接管
* **工程能力**:Trace、Eval、Bad Case、Regression、MCP、Retry、Fallback、Permission

最终形成:

> **一个可以真实展示 Agent 自主执行业务任务过程的工程化 Demo。**
