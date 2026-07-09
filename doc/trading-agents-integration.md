# TradingAgents 多 Agent 工作流分析 — Vibe-Trading 集成

> 全面分析 TradingAgents 项目的 11-Agent 辩论式工作流架构，
> 并展示如何将其核心设计模式扩展到 Vibe-Trading 的 Swarm 系统中。

---

## 目录
1. [TradingAgents 架构总览](#1-tradingagents-架构总览)
2. [11 个 Agent 角色详解](#2-11-个-agent-角色详解)
3. [LangGraph 工作流 DAG](#3-langgraph-工作流-dag)
4. [辩论循环机制](#4-辩论循环机制)
5. [数据流与状态管理](#5-数据流与状态管理)
6. [结构化输出系统](#6-结构化输出系统)
7. [决策反思机制](#7-决策反思机制)
8. [与 Vibe-Trading 的映射](#8-与-vibe-trading-的映射)
9. [新模式 TradingAgents 工作流](#9-新模式-tradingagents-工作流)
10. [扩展指南](#10-扩展指南)

---

## 1. TradingAgents 架构总览

### 架构目标

TradingAgents 是一个**研究导向的多 Agent 投资决策框架**。核心设计理念：

> **"多角度辩论 → 管理者裁决"** 而非 "投票或平均"。

### 6 阶段流水线

```
┌─────────────────────────────────────────────────────────────┐
│  TradingAgents 完整工作流 (11 Agents, 4 阶段串行)            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  STAGE 1: Research (4 Analysts, 串行)                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  Market  │→ │Sentiment │→ │  News    │→ │Fundament.│   │
│  │  Analyst │  │ Analyst  │  │  Analyst │  │  Analyst │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                           ↓                                 │
│  STAGE 2: Investment Debate (3 Agents, 循环)               │
│  ┌──────────┐  ⇄  ┌──────────┐                              │
│  │   Bull   │     │   Bear   │                              │
│  │Researcher│ ⇄   │Researcher│ → N rounds                   │
│  └──────────┘     └──────────┘                              │
│       ↓       (N rounds complete)     ↓                     │
│  ┌──────────────────────────────────┐                        │
│  │        Research Manager          │ → Judge               │
│  └──────────────────────────────────┘                        │
│                           ↓                                 │
│  STAGE 3: Trade Proposal (1 Agent)                         │
│  ┌──────────────────────────────────┐                        │
│  │             Trader               │ → Convert plan to action │
│  └──────────────────────────────────┘                        │
│                           ↓                                 │
│  STAGE 4: Risk Debate (4 Agents, 循环)                     │
│  ┌──────────┐  ⇄  ┌──────────┐  ⇄  ┌──────────┐           │
│  │Aggressive│     │Conservative│     │  Neutral │           │
│  │  Analyst │     │  Analyst   │     │  Analyst │           │
│  └──────────┘     └──────────┘     └──────────┘           │
│       ↓       (N rounds complete)     ↓                     │
│  ┌──────────────────────────────────┐                        │
│  │      Portfolio Manager           │ → Final Decision      │
│  └──────────────────────────────────┘                        │
│                                                              │
│  输出: Buy / Overweight / Hold / Underweight / Sell          │
│       + 执行摘要 + 价格目标 + 时间框架                        │
└─────────────────────────────────────────────────────────────┘
```

### 关键特性

| 特性 | 说明 |
|---|---|
| **Agent 数** | 11 个固定角色 |
| **LLM 策略** | 2 级：`quick_think` (分析师/辩论者) + `deep_think` (管理者) |
| **框架** | LangGraph StateGraph（显式 DAG） |
| **辩论模式** | 双层辩论：投资辩论 (2方) + 风险辩论 (3方) |
| **裁决机制** | 单一裁判制（管理者 LLM 裁决，非投票） |
| **输出格式** | Pydantic Structured Output + 降级到自由文本 |
| **记忆系统** | 决策日志 + 事后反思 + 跨标的教训传递 |
| **工具** | 12 个工具，分组到 4 个 ToolNode |

---

## 2. 11 个 Agent 角色详解

### Stage 1: 研究团队 (4 Analysts, 串行)

| Agent | 文件 | 核心职责 | 工具 | LLM |
|---|---|---|---|---|
| **Market Analyst** | `analysts/market_analyst.py` | 技术分析: 选最多 8 个互补指标, 趋势/动量/波动 | `get_stock_data`, `get_indicators`, `get_verified_market_snapshot` | quick_think |
| **Sentiment Analyst** | `analysts/sentiment_analyst.py` | 多源情绪: 新闻+StockTwits+Reddit, 结构化输出 0-10 分 | 无 (数据预注入 prompt) | quick_think |
| **News Analyst** | `analysts/news_analyst.py` | 新闻+宏观: 公司新闻/全球新闻/宏观指标/预测市场 | `get_news`, `get_global_news`, `get_macro_indicators`, `get_prediction_markets` | quick_think |
| **Fundamentals Analyst** | `analysts/fundamentals_analyst.py` | 基本面: 财报/资产/现金流/利润表 | `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement` | quick_think |

**串行原因**：每个分析师的后继输出都依赖前一个的 `messages` 中的工具调用历史。

### Stage 2: 投资辩论 (3 Agents, 循环)

| Agent | 文件 | 核心职责 | LLM |
|---|---|---|---|
| **Bull Researcher** | `researchers/bull_researcher.py` | 做多论证: 增长潜力/竞争优势/正面信号 | quick_think |
| **Bear Researcher** | `researchers/bear_researcher.py` | 做空论证: 风险/挑战/负面信号 | quick_think |
| **Research Manager** | `managers/research_manager.py` | 辩论裁决: 输出 5 级投资评级 | **deep_think** |

### Stage 3: 交易提案

| Agent | 文件 | 核心职责 | LLM |
|---|---|---|---|
| **Trader** | `trader/trader.py` | 投资计划 → 交易提案: Buy/Hold/Sell + 仓位 + 止损止盈 | quick_think |

### Stage 4: 风险辩论 (4 Agents, 循环)

| Agent | 文件 | 核心职责 | LLM |
|---|---|---|---|
| **Aggressive Analyst** | `risk_mgmt/aggressive_debator.py` | 高风险/高回报: 强调增长机会 | quick_think |
| **Conservative Analyst** | `risk_mgmt/conservative_debator.py` | 风险控制: 强调资本保全 | quick_think |
| **Neutral Analyst** | `risk_mgmt/neutral_debator.py` | 平衡观点: 综合两方 | quick_think |
| **Portfolio Manager** | `managers/portfolio_manager.py` | 最终决策: 5 级评级 + 执行摘要 + 价格目标 | **deep_think** |

---

## 3. LangGraph 工作流 DAG

### 核心 DAG (setup.py)

```python
workflow = StateGraph(AgentState)

# Stage 1: 串行分析师 (条件边控制工具调用循环)
workflow.add_edge(START, "Market Analyst")
workflow.add_conditional_edges("Market Analyst", should_continue_market, [...])
workflow.add_edge("MsgClearMarket", "Sentiment Analyst")
# ... (同理 Sentiment → News → Fundamentals)

# Stage 2: 辩论循环
workflow.add_edge("MsgClearFundamentals", "Bull Researcher")
workflow.add_conditional_edges("Bull Researcher", should_continue_debate, {
    "Bear Researcher": "Bear Researcher",
    "Research Manager": "Research Manager",
})
workflow.add_conditional_edges("Bear Researcher", should_continue_debate, {
    "Bull Researcher": "Bull Researcher",
    "Research Manager": "Research Manager",
})

# Stage 3: 交易
workflow.add_edge("Research Manager", "Trader")

# Stage 4: 风险辩论循环
workflow.add_edge("Trader", "Aggressive Analyst")
workflow.add_conditional_edges("Aggressive Analyst", should_continue_risk_analysis, {
    "Conservative Analyst": "Conservative Analyst",
    "Portfolio Manager": "Portfolio Manager",
})
# ... (同理 Conservative → Neutral → Aggressive)

workflow.add_edge("Portfolio Manager", END)
```

### 条件边逻辑

```python
# 辩论循环控制 (conditional_logic.py)
def should_continue_debate(self, state):
    if state["investment_debate_state"]["count"] >= self.max_debate_rounds * 2:
        return "Research Manager"  # 辩论结束 → 裁决
    return "Bear Researcher" if last_speaker == "Bull" else "Bull Researcher"

# 风险辩论循环控制
def should_continue_risk_analysis(self, state):
    if state["risk_debate_state"]["count"] >= self.max_risk_discuss_rounds * 3:
        return "Portfolio Manager"  # 辩论结束 → 决策
    return "next_risk_speaker"  # 轮转到下一风险分析师
```

### 执行流水线可视化

```
t=0:     [Market starts]
         ↓ tools? → ToolNode → Market → tools? → ... → done
t=t1:    [Sentiment starts]
         ↓ (data pre-injected, no tools) → done
t=t2:    [News starts]
         ↓ tools? → ToolNode → News → tools? → ... → done
t=t3:    [Fundamentals starts]
         ↓ tools? → ToolNode → Fnd → tools? → ... → done
t=t4:    [Bull R1] → [Bear R1] → [Bull R2] → [Bear R2] → ... → done
t=t5:    [Research Manager judges] → [Trader proposes]
t=t6:    [Aggressive R1] → [Conservative R1] → [Neutral R1] →
         [Aggressive R2] → [Conservative R2] → [Neutral R2]
t=t7:    [Portfolio Manager decides] → END
```

---

## 4. 辩论循环机制

### 投资辩论 (Bull ↔ Bear)

```python
# 循环逻辑
debate_state = {
    "bull_history": "",      # Bull 每次发言追加
    "bear_history": "",      # Bear 每次发言追加  
    "history": "",           # 完整对话历史
    "count": 0,              # 总发言次数 (max = rounds * 2)
    "current_response": "",  # 当前轮次的回复
    "judge_decision": "",    # 裁决结果
}

# 每轮：
# 1. Bull 发言 → 追加到 bull_history + history, count++
# 2. Bear 发言 → 追加到 bear_history + history, count++
# 3. 条件边检查 count >= max_rounds * 2 ? → Research Manager : 继续

# 配置文件
max_debate_rounds = 1  # 每个辩论者各发言 1 次 = 总共 2 次交换
```

### 风险辩论 (Aggressive ↔ Conservative ↔ Neutral)

```python
risk_state = {
    "aggressive_history": "",
    "conservative_history": "",
    "neutral_history": "",
    "history": "",
    "count": 0,              # 总发言次数 (max = rounds * 3)
    "latest_speaker": "",    # 最近发言者
}

max_risk_discuss_rounds = 1  # 每人各发言 1 次 = 总共 3 次交换
# 轮转顺序: Aggressive → Conservative → Neutral → Aggressive → ...
```

### 裁决机制 (非投票制)

```python
# 不是 "3 个分析师 2 票同意" 的投票模型
# 而是 "单一 deep_think LLM 阅读全部辩论历史 + 分析师报告 + 交易提案" 后综合裁决

# Research Manager 的 prompt 包含:
# - 全部 analyst reports
# - 全部 bull_history + bear_history
# → 输出 structured ResearchPlan

# Portfolio Manager 的 prompt 包含:
# - 全部上述内容 + 风险辩论历史
# - TradingMemoryLog 的 past_context (历史教训)
# → 输出 structured PortfolioDecision
```

---

## 5. 数据流与状态管理

### AgentState 结构

```python
class AgentState(TypedDict):
    messages: list[Any]                    # LangGraph 消息历史
    company_of_interest: str               # 当前分析标的
    trade_date: str                        # 交易日期
    asset_type: str                        # 资产类型
    instrument_context: str                # 解析后的标的身份信息
    past_context: str                      # 历史决策教训 (TradingMemoryLog)
    market_report: str                     # Market Analyst 输出
    sentiment_report: str                  # Sentiment Analyst 输出
    news_report: str                       # News Analyst 输出
    fundamentals_report: str              # Fundamentals Analyst 输出
    investment_debate_state: dict         # 投资辩论状态
    risk_debate_state: dict               # 风险辩论状态
```

### 数据流路径

```
Analyst Reports (4个) → 写入各自字段
         ↓
Bull/Bear 读取 → 辩论 → 写入 investment_debate_state
         ↓
Research Manager 读取所有 + 辩论历史 → 写入 ResearchPlan
         ↓
Trader 读取 ResearchPlan → 写入 TraderProposal
         ↓
风险三方读取所有 + TraderProposal → 写入 risk_debate_state
         ↓
Portfolio Manager 读取全部 → 写入 PortfolioDecision
```

---

## 6. 结构化输出系统

TradingAgents 使用 Pydantic `with_structured_output` 模式：

```python
# 1. 绑定结构化 Schema 到 LLM
structured_llm = llm.with_structured_output(PortfolioDecision, method="json_schema")

# 2. 尝试结构化输出，失败则降级
def invoke_structured_or_freetext(structured_llm, llm, messages, render_fn, name):
    try:
        result = structured_llm.invoke(messages)
        return render_fn(result)  # Pydantic → markdown
    except:
        # 降级到自由文本
        response = llm.invoke(messages)
        return response.content
```

支持的 Provider:

| Provider | 结构化方法 | Schema 兼容性 |
|---|---|---|
| OpenAI / xAI | `json_schema` | ✅ 完全 |
| Anthropic | `tool_use` (function calling) | ✅ 完全 |
| Google Gemini | `response_schema` | ✅ 完全 |

---

## 7. 决策反思机制 (TradingMemoryLog)

这是 TradingAgents 中最具差异化的功能之一：

```python
class TradingMemoryLog:
    """
    生命周期:
    Run N: store_decision(pending)
        → 将决策写入 ~/.trading_agents/memory/ 下的 markdown 文件
    Run N+1 (同一标的):
        → yfinance 获取实际收益
        → LLM 反思: "决策正确吗? 学到了什么?"
        → update_with_outcome(resolved) — 追加实际结果
    Run N+1 (跨标的):
        → get_past_context()
        → 将历史教训注入 Portfolio Manager 的 prompt
    """
    def get_past_context(self, ticker, lookback_days=365):
        # 从 memory/ 目录读取该标的的历史决策文件
        # 提取教训和结果
        return "Past lessons learned from trading {...}"
```

---

## 8. 与 Vibe-Trading 的映射

### 架构对比

| 维度 | TradingAgents | Vibe-Trading Swarm |
|---|---|---|
| **核心框架** | LangGraph StateGraph (显式 DAG + 条件边) | LangGraph ReAct (隐式循环 + YAML Preset) |
| **Agent 数量** | 11 个固定角色 | 按 Preset 定义 (17 个在扩展版本) |
| **工作流定义** | Python 代码 (setup.py + conditional_logic.py) | YAML 配置 (presets/*.yaml) |
| **执行模型** | 串行分析师 + 循环辩论 | DAG 拓扑层 + 同层并行 |
| **辩论循环** | 原生支持 (条件边循环) | 通过显式任务层模拟 (无循环) |
| **工具绑定** | 按 Agent 分组 (4 个 ToolNode) | 全局注册表, 按 Agent 声明 |
| **安全层** | ❌ 无 | Mandate Gate + Kill Switch + Audit |
| **数据源/Loader** | `route_to_vendor()` 多供应商 | Loader Registry 统一注册 |
| **结构化输出** | Pydantic + with_structured_output | 自由文本 (prompt 中约束格式) |
| **前端** | 简单聊天页面 | React 19 + Vite 完整 UI |
| **入口** | CLI + Web 后端 | CLI + FastAPI + MCP Server |
| **记忆/反思** | TradingMemoryLog | session + memory (不同实现) |
| **因子库** | ❌ 无 | ✅ Alpha Zoo (452 因子) |
| **回测** | ❌ 无 | ✅ 完整回测引擎 |

### 可移植的核心模式

| 模式 | TradingAgents 实现 | Vibe-Trading 实现方式 |
|---|---|---|
| **双层辩论** | LangGraph 条件边循环 | YAML 显式层级 (层 1-2 投资辩论, 层 5-6 风险辩论) |
| **管理者裁决** | deep_think LLM + 所有历史注入 | research_manager / portfolio_manager 接收 `{upstream_context}` |
| **结构化输出** | Pydantic structured_output | 系统提示词中嵌入输出格式约束 |
| **数据流注入** | AgentState 共享字段 | `input_from` + `{upstream_context}` 模板变量 |
| **分析师并行** | ❌ 串行 (消息历史依赖) | ✅ **并行** (Layer 0 同时启动 4 个分析师) |
| **决策记忆** | TradingMemoryLog 文件系统 | `~/.vibe-trading/memory/` + 会话持久化 |
| **辩论轮次** | `max_debate_rounds` 变量控制 | YAML 中为每轮创建独立任务 |

### 关键改进: 并行分析师

Vibe-Trading 实现的一个关键优势：**将 TradingAgents 的串行分析师改为并行**。

```
TradingAgents (串行):                    Vibe-Trading (并行):
─────────────────────────               ─────────────────────────
t=0: Market starts                      t=0: Market   Sentiment
t=10: Market done, Sentiment starts          News     Fundamentals
t=12: Sentiment done, News starts            (同时启动!)
t=22: News done, Fundamentals starts   t=30: 全部完成 ✓
t=35: Fundamentals done
                                                
总耗时: ~35 分钟                        总耗时: ~30 分钟 (更快!)
工具复用: 不能复用                      工具调用: 独立执行
```

---

## 9. 新模式: TradingAgents 工作流

### 文件结构

```
agent/src/swarm/presets/
├── analyst_research_team.yaml        ← 新 Preset (17 agents, 17 tasks, 8 layers)
└── ...

agent/src/swarm/
├── schemas_analyst_research_team.py           ← 结构化输出 Schema 参考
└── ...
```

### 8 层执行拓扑

```
┌─────────────────────────────────────────────────────────────────────┐
│  TradingAgents Workflow — Vibe-Trading Swarm 实现                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│ Layer 0: 4 parallel analysts    ┌─ Market ─┐  ┌─ Sentiment ─┐      │
│          (独立无依赖)            ├─ News ───┤  ├─ Fundaments ┤      │
│                                 └──────────┘  └────────────┘      │
│                                        ↓                            │
│ Layer 1: Bull/Bear R1           ┌─ Bull R1 ──┐  ┌─ Bear R1 ──┐    │
│          (依赖所有分析师)        └────────────┘  └────────────┘    │
│                                        ↓                            │
│ Layer 2: Bull/Bear R2           ┌─ Bull R2 ──┐  ┌─ Bear R2 ──┐    │
│          (依赖对方 R1)          └────────────┘  └────────────┘    │
│                                        ↓                            │
│ Layer 3: Research Manager       ┌──────────────┐                    │
│          (依赖双方 R2)          │  Manager     │                    │
│                                 └──────────────┘                    │
│                                        ↓                            │
│ Layer 4: Trader                 ┌──────────────┐                    │
│          (依赖 Manager)         │   Trader     │                    │
│                                 └──────────────┘                    │
│                                        ↓                            │
│ Layer 5: Risk Debate R1        ┌─ Aggr ─┐  ┌─ Cons ─┐  ┌─ Neu ─┐ │
│          (3 并行, 依赖 Trader) └────────┘  └────────┘  └────────┘ │
│                                        ↓                            │
│ Layer 6: Risk Debate R2        ┌─ Aggr2 ─┐  ┌─ Cons2 ┐  ┌─ Neu2 ┐ │
│          (三角形互依赖)         └─────────┘  └────────┘  └────────┘ │
│                                        ↓                            │
│ Layer 7: Portfolio Manager      ┌──────────────┐                    │
│          (依赖全部 R2)          │      PM      │                    │
│                                 └──────────────┘                    │
│                                        ↓                            │
│                          ✅ 最终决策                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Agent 列表

| ID | 角色 | 层 | 超时 | 工具 |
|---|---|---|---|---|
| `market_analyst` | 技术分析 | Layer 0 | 15 min | market_data |
| `sentiment_analyst` | 情绪分析 | Layer 0 | 15 min | market_data |
| `news_analyst` | 新闻宏观 | Layer 0 | 15 min | market_data |
| `fundamentals_analyst` | 基本面 | Layer 0 | 15 min | market_data |
| `bull_researcher` | 多头辩论 R1 | Layer 1 | 10 min | load_skill |
| `bear_researcher` | 空头辩论 R1 | Layer 1 | 10 min | load_skill |
| `bull_researcher_r2` | 多头辩论 R2 | Layer 2 | 10 min | load_skill |
| `bear_researcher_r2` | 空头辩论 R2 | Layer 2 | 10 min | load_skill |
| `research_manager` | 辩论裁决 | Layer 3 | 10 min | load_skill |
| `trader` | 交易提案 | Layer 4 | 10 min | market_data |
| `aggressive_analyst` | 激进风险 R1 | Layer 5 | 7.5 min | load_skill |
| `conservative_analyst` | 保守风险 R1 | Layer 5 | 7.5 min | load_skill |
| `neutral_analyst` | 中性风险 R1 | Layer 5 | 7.5 min | load_skill |
| `aggressive_analyst_r2` | 激进风险 R2 | Layer 6 | 7.5 min | load_skill |
| `conservative_analyst_r2` | 保守风险 R2 | Layer 6 | 7.5 min | load_skill |
| `neutral_analyst_r2` | 中性风险 R2 | Layer 6 | 7.5 min | load_skill |
| `portfolio_manager` | 最终决策 | Layer 7 | 15 min | backtest |

### 运行命令

```bash
# 基础运行
vibe-trading --swarm-run analyst_research_team \
  '{"target":"AAPL","market":"US"}'

# 复杂分析
vibe-trading --swarm-run analyst_research_team \
  '{"target":"BTC-USDT","market":"crypto"}'

# 中概股
vibe-trading --swarm-run analyst_research_team \
  '{"target":"600519.SH","market":"A-share"}'

# 查看运行状态
vibe-trading swarm-status <run_id>
```

---

## 10. 扩展指南

### 修改辩论轮次

要增加辩论轮数 (例如 3 轮)，只需在 YAML 中添加额外轮次：

```yaml
# 现有: Layer 1 → Layer 2 (2 轮)
# 要 3 轮: Layer 1 → Layer 2 → Layer 3 (新增)

tasks:
  # ... Layer 0 (analysts) ...
  
  # Layer 1: Round 1
  - id: task-bull-r1
    depends_on: [task-market-analysis, ...]
  - id: task-bear-r1
    depends_on: [task-market-analysis, ...]
  
  # Layer 2: Round 2
  - id: task-bull-r2
    depends_on: [task-bear-r1]
  - id: task-bear-r2
    depends_on: [task-bull-r1]
  
  # Layer 3: Round 3 (新增)
  - id: task-bull-r3
    depends_on: [task-bear-r2]
    input_from: {opponent_r2: task-bear-r2}
  - id: task-bear-r3
    depends_on: [task-bull-r2]
    input_from: {opponent_r2: task-bull-r2}
  
  # Layer 4: Research Manager (原 Layer 3)
  - id: task-research-manager
    depends_on: [task-bull-r3, task-bear-r3]
  # ...
```

### 新增分析维度

如需增加新的分析师 (如 On-Chain 分析师)：

1. **定义 Agent**:
```yaml
  - id: onchain_analyst
    role: "On-Chain Analyst — Blockchain Data"
    system_prompt: |
      Analyze on-chain metrics for {target}...
    tools: [bash, read_file, write_file, load_skill, get_market_data]
    skills: [onchain-analysis]
    max_iterations: 30
    timeout_seconds: 900
```

2. **添加到 Layer 0**:
```yaml
tasks:
  - id: task-onchain
    agent_id: onchain_analyst
    prompt_template: "Conduct on-chain analysis on {target}..."
    depends_on: []  # Layer 0, 与其他分析师并行
```

3. **更新下游依赖**:
```yaml
  - id: task-bull-r1
    depends_on: [task-market, task-sentiment, task-news, task-fundamentals, task-onchain]
    input_from:
      onchain_report: task-onchain
      # ...
```

### 整合 Vibe-Trading 独有功能

| Vibe-Trading 功能 | 集成方式 |
|---|---|
| **alpha 因子库** | 在 agent 的 tools 中添加 `factor_analysis` |
| **回测引擎** | portfolio_manager 的 tools 中添加 `backtest` (已包含) |
| **数据加载器** | 通过 `get_market_data` 工具自动访问 Loader Registry |
| **交易安全层** | trader/PM 输出可通过 Mandate Gate 验证 (需额外集成) |
| **Shadow Account** | 可将 PM 决策直接路由到 Shadow Account 模拟执行 |

---

## 参考资源

| 资源 | 路径 |
|---|---|
| **TradingAgents Preset** | `agent/src/swarm/presets/analyst_research_team.yaml` |
| **结构化 Schema** | `agent/src/swarm/schemas_analyst_research_team.py` |
| **Vibe-Trading 投资委员会** | `doc/investment-committee-workflow.md` |
| **Swarm 扩展指南** | `doc/extend-swarm-workflow.md` |
| **TradingAgents 架构** | `/workspaces/fw_mc_dev/trade/TradingAgents/` |
| **TradingAgents 源码分析** | `tradingagents/graph/setup.py` (DAG) |
| | `tradingagents/agents/schemas.py` (结构化输出) |
| | `tradingagents/graph/conditional_logic.py` (辩论逻辑) |
| | `tradingagents/graph/propagation.py` (状态管理) |
| | `tradingagents/agents/utils/agent_states.py` (状态类型) |
