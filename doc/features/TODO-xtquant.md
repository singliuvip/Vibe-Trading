# TODO — miniqmt/xtquant 集成未修复问题

> 基于 `doc/features/xtquant-integration.md` §9 问题清单
> 更新日期：2026-07-07

---

## 当前架构

Vibe-Trading 使用**原生 `import xtquant` 模式**，QMT Bridge HTTP 中间层已完全移除：

```
Vibe-Trading (Windows)
    │
    └── import xtquant ──→ XtQuantTrader ──IPC──→ miniQMT (本地进程)
         import xtdata   ──→ 本地缓存数据
```

已完成事项：
| 事项 | 轮次 |
|---|---|
| ✅ QMT Bridge 完全清理（docker-compose + 代码 + 测试） | 本轮 |
| ✅ T+1 规则实现（`a_stock_guard.py` + `sdk_order_gate.py` + `get_today_trades()`） | 本轮 |
| ✅ 测试文件修复（移除 HTTP bridge 测试 + 补 T+1 测试 + 28 passed） | 本轮 |
| ✅ 定时心跳监测（`probe_connection()` + 后台心跳线程） | 本轮 |
| ✅ `XtQuantConfig` 补充 `from_mapping`/`is_live`/`environment`/`with_overrides` | 本轮 |

---

## 🟡 P1 — 重要（5 项）

### 1. Kill Switch 无第二层防护

| 字段 | 值 |
|---|---|
| 引用 | §9.3.2 |
| 描述 | Kill Switch 仅依赖文件系统哨兵（`~/.vibe-trading/kill_switch/xtquant.halt`），文件被删除即失效 |
| 涉及文件 | `agent/src/live/halt.py`、`agent/src/live/sdk_order_gate.py` |
| 实现要点 | - 在 `halt.py` 中增加内存级 `_memory_halt: dict[str, bool]`<br>- `halt_broker()` 同时写入文件和内存<br>- `is_halted()` 检查内存 \|\| 文件<br>- 审计日志记录 Kill Switch 状态变更 |
| 估算工时 | 1h |

### 2. MCP Server 权限控制

| 字段 | 值 |
|---|---|
| 引用 | §9.3.3 |
| 描述 | MCP Server 暴露的工具中如果包含 `place_order`，外部 AI 客户端可绕过安全链直接调用 |
| 涉及文件 | `agent/mcp_server.py` |
| 实现要点 | - MCP Server 默认不暴露 `place_order`/`cancel_order`<br>- 需 `VIBE_MCP_ENABLE_TRADING=true` 显式启用<br>- 启用后仍须经过完整安全链 |
| 估算工时 | 1h |

### 3. 全局锁并发回测瓶颈

| 字段 | 值 |
|---|---|
| 引用 | §9.5.2 |
| 描述 | `_xtdata_lock` 是全局锁，多标的回测场景（如 300 只 A 股）所有操作串行执行 |
| 涉及文件 | `agent/backtest/loaders/xtdata_loader.py` |
| 实现要点 | - 批量下载：`download_history_data` 支持多股票并发<br>- 预下载缓存预热：回测开始前一次性下载所有标的数据<br>- 细化锁粒度：download 和 read 分离 |
| 估算工时 | 2h |

### 4. Paper 读操作仍暴露实盘数据

| 字段 | 值 |
|---|---|
| 引用 | §9.2.2 |
| 描述 | Paper profile 的 `get_positions`/`get_account_snapshot` 在纯 paper（只读）模式下仍走 xtquant SDK 读取真实账户 |
| 涉及文件 | `agent/src/trading/connectors/xtquant/sdk.py` |
| 实现要点 | - Paper 只读模式下 `get_positions`/`get_account_snapshot` 也应返回空/模拟数据<br>- 仅 `get_quote`/`get_historical_bars` 保留走 xtquant SDK |
| 估算工时 | 1h |

### 5. Profile override 可绕过 paper/live 隔离

| 字段 | 值 |
|---|---|
| 引用 | §9.3.1 |
| 描述 | `build_config` 的 overrides 包含 `profile`，调用者可通过 overrides 覆盖 profile 值 |
| 涉及文件 | `agent/src/trading/connectors/xtquant/sdk.py` |
| 实现要点 | - 从 `build_config` 的 override 键中移除 `profile`<br>- 或在 paper 路径中增加 `assert config.profile == "paper"` |
| 估算工时 | 0.5h |

---

## 💡 P2 — 建议（10 项）

### 6. 条件单不支持

| 字段 | 值 |
|---|---|
| 引用 | §9.2.3 |
| 描述 | Paper Engine 无法处理止损、止盈等条件单，limit 单也无后台价格监听触发机制 |
| 涉及文件 | `agent/src/trading/connectors/xtquant/paper_engine.py` |
| 实现要点 | - 短期：limit 单以当前行情判断，不能成交标记 `pending`，不支持条件单返回明确错误<br>- 长期：引入后台线程定期检查 pending 订单 |
| 估算工时 | 3h（长期方案） |

### 7. PaperEngine market_value 始终为 0

| 字段 | 值 |
|---|---|
| 描述 | `PaperEngine.get_account_snapshot()` 中 `market_value` 始终为 0，因为 `get_positions()` 也没有市价计算 |
| 涉及文件 | `agent/src/trading/connectors/xtquant/paper_engine.py` |
| 实现要点 | - `get_positions()` 从 xtdata 获取当前价格计算 market_value<br>- `get_account_snapshot()` 汇总持仓市值 |
| 估算工时 | 1h |

### 8. 缓存管理 CLI 缺失

| 字段 | 值 |
|---|---|
| 引用 | §9.5.3 |
| 描述 | xtdata 本地缓存可能膨胀到数 GB，无管理手段 |
| 涉及文件 | `agent/cli/commands/` |
| 实现要点 | 提供 `vibe-trading cache info --source xtdata` 和 `vibe-trading cache clear --source xtdata` |
| 估算工时 | 1h |

### 9. 首次设置引导缺失

| 字段 | 值 |
|---|---|
| 引用 | §9.7.2 |
| 描述 | 用户首次使用 xtquant 时需手动创建配置文件，无交互式引导 |
| 涉及文件 | `agent/cli/onboard.py` |
| 实现要点 | 自动扫描常见 QMT 安装路径、引导确认、自动发现账号 |
| 估算工时 | 2h |

### 10. 多账号支持缺失

| 字段 | 值 |
|---|---|
| 引用 | §9.7.1 |
| 描述 | `XtQuantConfig` 只支持单 `account_id` |
| 涉及文件 | `agent/src/trading/connectors/xtquant/sdk.py` |
| 实现要点 | `xtquant.json` 支持 `accounts` 数组，通过 CLI 参数切换 |
| 估算工时 | 2h |

### 11. 审计日志无 environment 字段

| 字段 | 值 |
|---|---|
| 引用 | §9.6.2 |
| 描述 | Paper/Live 操作无法从审计日志区分 |
| 涉及文件 | `agent/src/live/audit.py`、`agent/src/live/sdk_order_gate.py` |
| 实现要点 | 审计记录增加 `environment` 字段 |
| 估算工时 | 0.5h |

### 12. Loader 复权方式不一致

| 字段 | 值 |
|---|---|
| 引用 | §9.5.1 |
| 描述 | tushare/akshare/xtdata 复权方式不同导致回测结果不可比较 |
| 涉及文件 | `agent/backtest/loaders/xtdata_loader.py`、`agent/backtest/loaders/registry.py` |
| 实现要点 | 标准化 `adjust` 参数映射到 `dividend_type` |
| 估算工时 | 1h |

### 13. 版本兼容检查缺失

| 字段 | 值 |
|---|---|
| 引用 | §9.8.1 |
| 描述 | `sdk.py` 中 xtquant 属性名硬编码，版本升级可能改变属性名 |
| 涉及文件 | `agent/src/trading/connectors/xtquant/sdk.py` |
| 实现要点 | `check_status` 中检测 xtquant 版本，对未知版本输出警告 |
| 估算工时 | 1h |

### 14. `order_stock` 参数组合未验证

| 字段 | 值 |
|---|---|
| 引用 | §9.8.2 |
| 描述 | `order_stock` 调用中常量组合是否正确未经验证 |
| 涉及文件 | `agent/src/trading/connectors/xtquant/sdk.py` |
| 实现要点 | 添加 `_validate_order_params` 调用前验证参数组合 |
| 估算工时 | 1h |

### 15. 条件单在 Paper 模式无法工作

| 字段 | 值 |
|---|---|
| 引用 | §9.2.3 |
| 描述 | Paper Engine 无法处理止损/止盈条件单 |
| 涉及文件 | `agent/src/trading/connectors/xtquant/paper_engine.py` |
| 实现要点 | limit 单标记 pending，后台线程定期检查触发 |
| 估算工时 | 3h |

---

## 修复路线图

```
阶段 1 — 安全加固 P1（~3.5h）
├── Kill Switch 内存级第二层防护          (~1h)
├── MCP 交易工具默认禁用                 (~1h)
├── Paper 读操作隔离                     (~1h)
└── Profile override 加固                (~0.5h)

阶段 2 — 性能优化 P1（~2h）
└── 全局锁并发回测瓶颈优化               (~2h)

阶段 3 — 功能完整 P2（~11.5h）
├── PaperEngine market_value 补全        (~1h)
├── 缓存管理 CLI                         (~1h)
├── 首次设置引导                         (~2h)
├── 多账号支持                           (~2h)
├── 审计日志 environment 字段            (~0.5h)
├── Loader 复权方式标准化                (~1h)
├── 版本兼容检查                         (~1h)
├── order_stock 参数验证                  (~1h)
└── 条件单支持（远期）                   (~3h)
```

---

*本文档由 Dispatcher 生成，基于 Main Agent (Claude Opus 4.6) 的架构分析和 Review Agent 的审查报告。*
