---
applyTo: "**"
---

# 架构规范 — Vibe-Trading

> 适用范围：所有代码编写、模块新增、重构操作。严格遵守以下分层约束。

---

## 1. 项目概述

Vibe-Trading 是自然语言驱动的金融研究 AI Agent 平台，`pip install vibe-trading-ai` 即可使用。

- **后端**：FastAPI + LangGraph ReAct Agent，Python 3.11+
- **前端**：React 19 + Vite + TypeScript + Tailwind CSS
- **入口**：CLI (`vibe-trading`)、API Server (`agent/api_server.py`)、MCP Server (`agent/mcp_server.py`)
- **核心能力**：量化回测、Alpha Zoo（452 因子）、多 Agent Swarm、10+ 券商连接器、IM 频道推送、Shadow Account、Research Goal

---

## 2. 目录结构

```
Vibe-Trading/
├── agent/                      ← 后端 & 包代码（Python）
│   ├── api_server.py           # FastAPI 服务入口
│   ├── mcp_server.py           # MCP 服务入口
│   ├── src/                    # 核心源码
│   │   ├── agent/              # Agent 循环、配置
│   │   ├── api/                # FastAPI 路由
│   │   ├── channels/           # IM 频道适配器（Telegram/Slack/Discord 等）
│   │   ├── core/               # 核心业务逻辑
│   │   ├── tools/              # Agent 工具注册表（48+ 工具）
│   │   ├── trading/            # 券商连接器、Mandate、Kill Switch、审计
│   │   ├── backtest/           # 回测引擎 + 数据加载器（注：Agent源码侧）
│   │   ├── swarm/              # 多 Agent Swarm
│   │   ├── memory/             # 持久化记忆
│   │   ├── session/            # 会话管理
│   │   ├── security/           # 安全防护
│   │   ├── factors/            # Alpha Zoo 因子库
│   │   ├── config/             # 配置管理
│   │   ├── goal/               # Research Goal 运行时
│   │   ├── hypotheses/         # 假设注册表
│   │   ├── live/               # 实时数据
│   │   ├── providers/          # LLM provider 配置
│   │   ├── shadow_account/     # Shadow Account
│   │   ├── skills/             # 用户自定义 skill
│   │   └── utils/              # 公共工具
│   ├── cli/                    # CLI 入口包
│   ├── backtest/               # 回测公共模块（loaders/engines/optimizers）
│   └── tests/                  # pytest 测试套件
├── frontend/                   ← 前端（React 19 + Vite）
│   ├── src/                    # 前端源码
│   └── vitest.config.ts        # 前端测试配置
├── wiki/                       ← 公开 wiki（Cloudflare Pages）
├── pyproject.toml              # 包配置 & 依赖
└── docker-compose.yml          # Docker 部署
```

---

## 3. 分层架构

```
┌──────────────────────────────────────────────────────────────┐
│  Entry Layer（入口层）                                        │
│  CLI (agent/cli/)  ·  FastAPI (api_server.py)                │
│  MCP Server (mcp_server.py)                                  │
└──────────────────────┬───────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────┐
│  Agent Layer（Agent 层）  agent/src/agent/                   │
│  LangGraph ReAct loop · 工具调度 · 流式输出                   │
└──────────────────────┬───────────────────────────────────────┘
                       │ 调用
┌──────────────────────▼───────────────────────────────────────┐
│  Tool Layer（工具层）  agent/src/tools/                       │
│  48+ 工具：回测 / 市场数据 / 文档 / 网络 / 交易 / Swarm / Goal │
└────────┬─────────────────────────┬────────────────────────────┘
         │                         │
┌────────▼────────┐   ┌────────────▼──────────────────────────┐
│  Service Layer  │   │  Trading Safety Layer（交易安全层）      │
│  agent/src/     │   │  agent/src/trading/                    │
│  backtest/      │   │  Mandate Gate · Kill Switch · Audit    │
│  swarm/         │   │  Broker Connectors（10+ 券商）          │
│  memory/        │   └───────────────────────────────────────┘
│  session/       │
│  channels/      │
│  security/      │
└────────┬────────┘
         │
┌────────▼────────────────────────────────────────────────────┐
│  Data Layer（数据层）  agent/backtest/loaders/               │
│  tushare · akshare · yfinance · mootdx · ccxt · futu        │
└─────────────────────────────────────────────────────────────┘
```

**依赖方向只能向下，禁止上层被下层反向引用。**

---

## 4. 关键模块职责

### Agent 层（`agent/src/agent/`）
- 实现 LangGraph ReAct 循环，驱动工具调用与流式输出
- 不直接访问数据库、文件系统或券商 API，通过工具层间接操作
- 禁止在 Agent 层嵌入业务逻辑，业务逻辑下沉到 Service 层

### 工具层（`agent/src/tools/`）
- 每个工具是一个独立的 Python 函数，通过 `@tool` 装饰器注册
- 工具只做"协议适配"（参数解析 + 调用 Service），不含业务逻辑实现
- 工具必须有明确的输入校验，拒绝无效输入而不是静默失败

### 交易安全层（`agent/src/trading/`）
- **高风险代码**：所有变更必须经过完整测试和人工审批
- Mandate Gate：强制执行用户提交的交易约束（品种/规模/敞口/杠杆/日限额）
- Kill Switch：文件系统级即时停机，优先于所有其他逻辑
- Audit Ledger：所有下单/撤单操作必须写入审计日志，不得绕过
- Broker Connector：每个券商连接器必须明确区分 paper/live 模式

### 数据层（`agent/backtest/loaders/`）
- 所有数据源通过统一的 Loader Registry 注册
- 数据返回前必须经过归一化（OHLCV 标准格式、strict JSON）
- 缓存在 `~/.vibe-trading/cache/`，**不写入仓库**

---

## 5. 架构约束（强制）

### 禁止的模式

| 禁止 | 原因 | 正确做法 |
|---|---|---|
| Agent 层直接调用 broker API | 绕过安全层 | 通过交易安全层 |
| 工具层实现业务逻辑 | 职责混淆，无法复用 | 逻辑下沉到 Service 层 |
| `agent/src/` 以外的代码访问 `trading/` 内部 | 破坏安全边界 | 只通过工具层暴露的接口 |
| 回测代码直接发起网络请求 | 难以测试，无法缓存 | 通过 Loader Registry |
| 在 `agent/tests/` 中发起实盘 broker 操作 | PR 验证流程高风险 | Mock / paper 模式 |
| 把凭据、`.env`、token 写入仓库 | 安全风险 | 只在本地 `~/.vibe-trading/` |

### 高风险表面（需显式授权）

以下操作**禁止**在自动化流程中执行，必须获得维护者/用户明确批准：

- 下单、撤单、强平等任何 broker 写操作
- 授权 broker / OAuth / MCP / 交易所账户
- 向 `agent/.env`、`~/.vibe-trading/` 写入真实凭据
- 启动对外可访问的 API / MCP / SSE / webhook 服务
- 发布 wiki、发布包、触发 release、修改 CI secret
- 强推分支、删除备份、清除持久化 run / memory 数据

### 新增模块规则

**新增 Agent 工具：**
1. 在 `agent/src/tools/` 下新建文件，用 `@tool` 注册
2. 工具只做参数解析，业务逻辑在 Service 层实现
3. 在 `agent/tests/` 添加对应测试

**新增 Broker 连接器：**
1. 在 `agent/src/trading/` 下实现连接器
2. 必须明确区分 paper/live，无法区分时默认 paper + 只读
3. `place_order` / `cancel_order` 必须通过 Mandate Gate 和 Kill Switch

**新增数据 Loader：**
1. 在 `agent/backtest/loaders/` 下实现
2. 注册到 Loader Registry
3. 返回数据必须符合归一化 OHLCV 格式

---

## 6. 关键文件速查

| 文件 | 用途 |
|---|---|
| `agent/api_server.py` | FastAPI 服务入口 |
| `agent/mcp_server.py` | MCP 服务入口 |
| `agent/cli/__main__.py` | CLI 入口 |
| `agent/src/agent/` | LangGraph ReAct Agent 循环 |
| `agent/src/tools/` | 工具注册表 |
| `agent/src/trading/` | 交易安全层（高风险） |
| `agent/backtest/` | 回测引擎 |
| `agent/src/factors/` | Alpha Zoo 因子库 |
| `agent/src/swarm/` | 多 Agent Swarm |
| `agent/tests/` | pytest 测试套件 |
| `frontend/src/` | React 19 前端 |
| `pyproject.toml` | 包配置 & 依赖 |
| `AGENT_CONTRIBUTOR_GUIDE.md` | Agent 贡献指南（安全边界） |
