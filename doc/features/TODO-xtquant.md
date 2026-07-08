# TODO — miniqmt/xtquant 集成未修复问题

> 基于 `doc/features/xtquant-integration.md` §9 问题清单
> 更新日期：2026-07-07

---

## 架构变更说明（v3 — 2026-07-07）

当前实现已**移除 QMT Bridge HTTP 中间层**，恢复为**原生 `import xtquant` 模式**：

| 变更 | 说明 |
|---|---|
| ✅ 原生 xtquant | 已恢复。直接在 Vibe-Trading 进程中 `import xtquant` 调用本地 miniQMT |
| ❌ HTTP Bridge | 已移除。不再需要 QMT Bridge 中间服务 |
| ❌ `qmt_bridge/server.py` | 已删除 |
| ✅ PaperEngine | 保留。Paper 下单/持仓在客户端侧本地模拟 |
| ❌ `sdk_http.py` | 已删除（上一轮） |

架构示意：
```
Vibe-Trading (Windows)
    │
    └── import xtquant ──→ XtQuantTrader ──IPC──→ miniQMT (本地进程)
         import xtdata   ──→ 本地缓存数据
```

桌面打包时，xtquant SDK 直接随应用分发，无需额外 HTTP 服务。

| 变更 | 说明 |
|---|---|
| ❌ 原生 xtquant | 已移除。不再在 Vibe-Trading 进程中直接 import xtquant |
| ✅ HTTP Bridge | 唯一模式。Vibe-Trading 通过宿主机上的 QMT Bridge HTTP 服务间接访问 miniQMT |
| ✅ PaperEngine | 保留。Paper 下单/持仓在客户端侧本地模拟（PaperEngine），不经过 Bridge |

架构示意：
```
┌──────────────────────┐        HTTP POST        ┌──────────────────────┐
│  Vibe-Trading        │ ──────────────────────►  │  QMT Bridge (:8888)  │
│  (Docker / Linux)    │ ◄──────────────────────  │  (宿主机 Windows)     │
│  sdk.py (HTTP only)  │        JSON响应          │  └─ xtquant → miniQMT │
└──────────────────────┘                          └──────────────────────┘
```

Paper 模式（下单/持仓/撤单）在 Vibe-Trading 客户端侧通过 PaperEngine 本地模拟，不经过 Bridge。

---

## 🔴 P0 — 致命（2 项）

### 1. T+1 规则未实现

| 字段 | 值 |
|---|---|
| 引用 | §9.3.4 |
| 描述 | A 股卖出操作缺少 T+1 检查——当日买入的股票不应允许当日卖出 |
| 根因 | Order Guard 是无状态的纯函数，T+1 需要跨请求的持仓买入日期缓存 |
| 涉及文件 | `agent/src/live/a_stock_guard.py`、`agent/src/live/sdk_order_gate.py` |
| 前置依赖 | 需要 xtquant 连接器已注册到 live gate（`XTQUANT_TOOL_CLASS` 已在 `live/registry.py` 中注册 ✅） |
| 实现要点 | - 查询 xtquant 当日委托成交记录判断是否有今日买入<br>- ETF（如 510050.SH）支持 T+0，需区分品种<br>- 重启后缓存丢失，需从 xtquant 重新查询 |
| 估算工时 | 2h |

---

## 🟡 P1 — 重要（3 项）

### 2. Kill Switch 无第二层防护

| 字段 | 值 |
|---|---|
| 引用 | §9.3.2 |
| 描述 | Kill Switch 仅依赖文件系统哨兵（`~/.vibe-trading/kill_switch/xtquant.halt`），文件被删除即失效 |
| 涉及文件 | `agent/src/live/halt.py`、`agent/src/live/sdk_order_gate.py` |
| 实现要点 | - 在 `halt.py` 中增加内存级 `_memory_halt: dict[str, bool]`<br>- `halt_broker()` 同时写入文件和内存<br>- `is_halted()` 检查内存 || 文件<br>- 审计日志记录 Kill Switch 状态变更 |
| 估算工时 | 1h |

### 3. MCP Server 权限控制

| 字段 | 值 |
|---|---|
| 引用 | §9.3.3 |
| 描述 | MCP Server 暴露的工具中如果包含 `place_order`，外部 AI 客户端可绕过安全链直接调用 |
| 涉及文件 | `agent/mcp_server.py` |
| 实现要点 | - MCP Server 默认不暴露 `place_order`/`cancel_order`<br>- 需 `VIBE_MCP_ENABLE_TRADING=true` 显式启用<br>- 启用后仍须经过完整安全链 |
| 估算工时 | 1h |

### 4. 全局锁并发回测瓶颈

| 字段 | 值 |
|---|---|
| 引用 | §9.5.2 |
| 描述 | `_xtdata_lock` 是全局锁，多标的回测场景（如 300 只 A 股）所有操作串行执行 |
| 涉及文件 | `agent/backtest/loaders/xtdata_loader.py` |
| 实现要点 | - 批量下载：`download_history_data` 支持多股票并发<br>- 预下载缓存预热：回测开始前一次性下载所有标的数据<br>- 细化锁粒度：download 和 read 分离 |
| 估算工时 | 2h |

---

## 💡 P2 — 建议（1 项）

### 5. 条件单不支持

| 字段 | 值 |
|---|---|
| 引用 | §9.2.3 |
| 描述 | Paper Engine 无法处理止损、止盈等条件单，limit 单也无后台价格监听触发机制 |
| 涉及文件 | `agent/src/trading/connectors/xtquant/paper_engine.py` |
| 实现要点 | - 短期：limit 单以当前行情判断，不能成交标记 `pending`，不支持条件单返回明确错误<br>- 长期：引入后台线程定期检查 pending 订单 |
| 估算工时 | 3h（长期方案） |

---

## 修复路线图

```
阶段 1 — Kill Switch + MCP 权限（安全加固）
├── Kill Switch 内存级第二层防护  (~1h)
└── MCP 交易工具默认禁用          (~1h)

阶段 2 — T+1 规则（A 股合规）
└── a_stock_guard.py T+1 实现     (~2h)

阶段 3 — 性能优化
└── 全局锁并发瓶颈优化            (~2h)

阶段 4 — 功能完善
└── 条件单支持（远期）            (~3h)
```

---

*本文档由 Dispatcher 生成，基于 Main Agent (Claude Opus 4.6) 的架构分析和 Review Agent 的审查报告。*
