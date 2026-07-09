# Copilot Instructions — Vibe-Trading

## Project Overview

Vibe-Trading 是一个自然语言驱动的金融研究 AI Agent 平台（`pip install vibe-trading-ai`）。

- **后端**：FastAPI + LangGraph ReAct Agent，Python 3.11+
- **前端**：React 19 + Vite + TypeScript + Tailwind CSS
- **入口**：CLI (`vibe-trading`)、API Server (`api_server.py`)、MCP Server (`mcp_server.py`)
- **核心能力**：量化回测、Alpha Zoo（452 因子）、多 Agent Swarm、10+ 券商连接器、IM 频道推送、Shadow Account、Research Goal

---

## Architecture & Project Conventions

详见专属指令文件：[.github/instructions/architecture.instructions.md](.github/instructions/architecture.instructions.md)

（对所有文件类型自动生效，包含分层架构、模块职责、禁止模式。）

---

## Coding Conventions

详见专属指令文件：[.github/instructions/coding-conventions.instructions.md](.github/instructions/coding-conventions.instructions.md)

（编写或修改任何 `.py` 文件时，该文件会自动生效。）

---

## File System

详见专属指令文件：[.github/instructions/filesystem.instructions.md](.github/instructions/filesystem.instructions.md)

（对所有文件类型自动生效，包含数据目录规范、用户数据路径、安全边界。）

---

## Design & Implementation Workflow

本项目采用 **调度层 + 子 Agent 调用** 架构：用户只与 Dispatcher 对话，Dispatcher 按需调用子 agent（Main/Develop/Review），节约高级模型 token。

### 架构

```
用户（单个 Chat 窗口）
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│  Dispatcher（调度层）· DeepSeek V4 Flash                  │
│  唯一用户入口 · 任务路由 · 按需调用子 agent · 报告汇总 · 审批 │
└──┬──────────────┬──────────────────┬────────────────────┘
   │ runSubagent  │ runSubagent      │ runSubagent
   ▼              ▼                  ▼
┌──────────┐ ┌──────────┐ ┌──────────────┐
│ Main     │ │ Develop  │ │ Review       │
│ Agent    │ │ Agent    │ │ Agent        │
│ GPT-5.5  │ │ DeepSeek │ │ DeepSeek     │
│          │ │ V4 Pro   │ │ V4 Pro       │
│ 仅架构    │ │ 代码实现  │ │ 审查+构建+测试 │
└──────────┘ └──────────┘ └──────────────┘
```

### Token 节约策略

| 模型 | 角色 | 调用方式 | 频率 |
|---|---|---|---|
| **DeepSeek V4 Flash** | Dispatcher | 用户直接对话 | 每轮必用 |
| **GPT-5.5**（首选） | Main Agent | Dispatcher 通过 `runSubagent` 调用 | **仅必要时** |
| **DeepSeek V4 Pro** | Develop Agent | Dispatcher 通过 `runSubagent` 调用 | 实现轮次 |
| **DeepSeek V4 Pro** | Review Agent | Dispatcher 通过 `runSubagent` 调用 | 审查轮次 |

> GPT-5.5 仅在以下场景调用：新需求首次分析、Bug 定位与根因分析、性能瓶颈分析与优化、跨模块修改、架构冲突、用户驳回方案调整。

### Agent 定义文件

| Agent | 文件 | 模型 | 职责 | 用户可见 |
|---|---|---|---|---|
| **Dispatcher** | `.github/agents/dispatcher.agent.md` | DeepSeek V4 Flash | 调度入口、任务路由、报告汇总、用户交互 | ✅ 唯一直连 |
| Main Agent | `.github/agents/main.agent.md` | **GPT-5.5** | 架构设计、边界识别、任务拆解、冲突裁决、问题定位、性能优化 | ❌ 子 agent |
| Develop Agent | `.github/agents/develop.agent.md` | DeepSeek V4 Pro | 聚焦代码实现、局部验证、修复问题 | ❌ 子 agent |
| Review Agent | `.github/agents/review.agent.md` | DeepSeek V4 Pro | 代码审查、构建验证、规范检查、测试 | ❌ 子 agent |

### 工作流规则

1. 用户在 **Dispatcher**（唯一入口）中输入需求。
2. **Dispatcher** 判断是否需要架构分析：
   - 需要 → 通过 `runSubagent` 调用 **Main Agent（GPT-5.5）**，获取架构方案后提交用户审批。
   - 不需要 → 直接进入实现。
3. **Dispatcher** 通过 `runSubagent` 调用 **Develop Agent** 实现。
4. **Develop Agent** 完成实现并运行局部验证后，返回实现报告。
5. **Dispatcher** 必须通过 `runSubagent` 调用 **Review Agent** 审查，**不得跳过**。
6. **Review Agent** 执行审查，生成审查报告。**不修改代码**。
7. **Dispatcher** 汇总本轮结果，生成**轮次报告**，提交用户最终审核。
8. **用户审核**：批准 → 下一轮 / 驳回 → Dispatcher 判断调用 Main Agent 还是 Develop Agent。
9. **禁止在用户批准前推进到下一轮。**

### 轮次报告规范

每一轮（架构设计 / 单步实现+审查）结束时，Dispatcher 必须：
1. 向用户输出结构化的**轮次报告**
2. **将报告保存为 `doc/round-N-report.md`**（N 为轮次号）

格式如下：

```
## 📋 第 N 轮报告

**轮次目标：** [本轮要完成的事项]

### 执行概要
- 步骤：第 X/Y 步
- 状态：[待审批]

### 架构方案（首轮必含）
- 影响范围
- 修改文件清单
- 依赖关系

### 实现结果（实现轮次含）
- Develop Agent 修改文件及行为
- 局部验证结果

### 审查结果（审查轮次含）
- Review Agent 审查结论
- 致命/警告/提示问题统计
- 构建与测试结果

### 裁决建议
- 建议动作：[批准进入下一步 / 需要修正 / 方案需调整]
- 风险提示

---
👤 **请审核本轮结果，批准后进入下一轮。**
```

### 用户审批指令

用户在每轮报告后使用以下指令：

| 用户指令 | 含义 |
|---|---|
| `批准` / `通过` / `继续` | 批准本轮，进入下一轮 |
| `驳回：<原因>` | 驳回本轮，Main Agent 根据原因调整 |
| `查看详情：<项目>` | 要求展开某个检查项的详细信息 |

---

## Build & Test

```bash
# 安装依赖（开发模式）
pip install -e ".[dev]"

# 运行后端测试（排除 e2e 和实盘）
pytest --ignore=agent/tests/e2e_backtest --ignore=agent/tests/test_e2e_harness_v2.py --tb=short -q

# 针对特定模块的测试
pytest agent/tests/test_sdk_order_gate.py agent/tests/test_mandate_enforcement.py -q

# 因子 Zoo 测试
pytest agent/tests/factors/test_alpha_purity.py agent/tests/factors/test_lookahead.py -q

# 前端构建与测试
cd frontend && npm ci && npm run build
cd frontend && npx vitest run

# 启动开发模式（后端 + 前端）
vibe-trading dev
```

### 高风险操作（需明确用户授权才能执行）

以下操作**禁止**在 PR 验证流程中自动执行，必须先获得维护者或用户明确批准：

- 下单、撤单、强平等任何实盘/模拟盘 broker 写操作
- 授权 broker / OAuth / MCP / 交易所账户
- 向 `agent/.env`、`~/.vibe-trading/` 写入真实凭据
- 启动对外可访问的 API / MCP / SSE / webhook 服务
- 发布 wiki、发布包、触发 release、修改 CI secret
- 强推分支、删除备份、清除持久化 run / memory 数据

---
