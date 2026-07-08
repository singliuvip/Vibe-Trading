# 📋 第 8 轮报告 — XTQuant 4 项修复

**轮次目标：** ① 清理 QMT Bridge 残留  ② T+1 规则实现  ③ 修复测试文件  ④ 定时心跳监测

**日期：** 2026-07-07

---

## 执行概要

- **步骤**：实现 + 审查 + 二次修复
- **状态**：✅ **待审批**

---

## 架构方案

Main Agent (Claude Opus 4.6) 分析结论：

| 任务 | 层 | 风险 | 影响文件 |
|---|---|---|---|
| 清理 QMT Bridge 残留 | Entry Layer | 低 | `docker-compose.yml` |
| T+1 规则实现 | **交易安全层** | **高 (P0)** | `a_stock_guard.py`, `sdk_order_gate.py` |
| 修复测试文件 | 测试层 | 中 | `test_xtquant_connector.py` |
| 定时心跳监测 | Service + Entry | 中 | `sdk.py`, `__init__.py` |

---

## 实现结果

### 修改文件清单

| 文件 | 修改说明 |
|---|---|
| `docker-compose.yml` | 删除 `BRIDGE_URL`、`BEARER_TOKEN`、`HTTP_TIMEOUT` 环境变量 |
| `agent/src/live/a_stock_guard.py` | 新增 `_T0_PREFIXES`、`is_t0_eligible()`、`check_t_plus_1()` |
| `agent/src/live/sdk_order_gate.py` | 导入并使用 `check_t_plus_1`；T+1 检查改用 `get_today_trades()` |
| `agent/src/trading/connectors/xtquant/sdk.py` | ① 新增 `from_mapping`/`is_live`/`environment`/`with_overrides`  ② `load_config` 对无效 JSON 抛 `XtQuantConfigError`  ③ 新增 `get_today_trades()`  ④ 新增 `probe_connection()` + 心跳线程  ⑤ `_disconnect()` 中调用 `stop_heartbeat()` |
| `agent/src/trading/connectors/xtquant/__init__.py` | 导出 `probe_connection`、`start_heartbeat`、`stop_heartbeat`、`get_heartbeat_status`、`get_today_trades` |
| `agent/tests/test_xtquant_connector.py` | ① 修复导入  ② 删除 13 个 HTTP bridge 测试类  ③ 新增 `TestTPlusOneCheck`(6) + `TestGetTodayTrades`(2) |

### 关键行为

| 功能 | 行为 |
|---|---|
| **T+1 规则** | Sell 方向 A 股（非 ETF/可转债）检查当日买入记录。ETF 前缀 `51/159/512/513/515/518/52` 和可转债 `11/12` 豁免。无法获取成交记录时 fail-closed 拒绝 |
| **T+1 数据源** | `get_today_trades()` 使用 `trader.get_order_stock_trades()` 获取真实成交，替代之前无效的 `get_open_orders(include_executions=True)`（xtquant 无 executions 端点） |
| **心跳线程** | `daemon=True` 后台线程，默认 30s 间隔，连续 3 次失败自动重连。心跳状态通过 `get_heartbeat_status()` 查询 |
| **测试修复** | 删除全部 HTTP bridge 测试（~13 个类），修复导入，补充 T+1 测试（8 个） |

### 清除的 QMT Bridge 残留

| 位置 | 处理 |
|---|---|
| `docker-compose.yml` 的 `XTQUANT_BRIDGE_URL` | ✅ 删除 |
| `docker-compose.yml` 的 `XTQUANT_BEARER_TOKEN` | ✅ 删除 |
| `docker-compose.yml` 的 `XTQUANT_HTTP_TIMEOUT` | ✅ 删除 |
| `test_xtquant_connector.py` 的 HTTP bridge 测试 | ✅ 删除 |
| `sdk.py` 的不存在导入引用 | ✅ 修复 |

---

## 审查结果

### 首轮审查（Review Agent）

| 严重度 | 数量 | 处理 |
|---|---|---|
| ❌ 致命 | 5 | 全部修复 |
| ⚠️ 警告 | 3 | 已采纳部分 |

### 致命问题修复记录

| # | 问题 | 修复方式 |
|---|---|---|
| 1 | `XtQuantConfig.from_mapping()` 不存在 | 添加 `@classmethod from_mapping` |
| 2 | `XtQuantConfig.with_overrides()` 不存在 | 添加 `with_overrides(self, **overrides)` |
| 3 | `is_live`/`environment` 属性不存在 | 添加 `@property is_live` 和 `@property environment` |
| 4 | `load_config` 不抛异常 | `JSONDecodeError` → 抛 `XtQuantConfigError` |
| 5 | T+1 形同虚设（空 executions） | 新增 `get_today_trades()` 使用 `trader.get_order_stock_trades()` |

### 二次审查验证

| 验证项 | 结果 |
|---|---|
| `pytest test_xtquant_connector.py` | **28 passed ✅** |
| `docker-compose.yml` 无 bridge 残留 | **CLEAN ✅** |
| `from ... import probe_connection, start_heartbeat, ...` | **OK ✅** |
| `XtQuantConfig.from_mapping().is_live` / `.environment` / `.with_overrides()` | **ALL OK ✅** |

---

## 未完成事项 → TODO 文档

以下问题已移入 `doc/features/TODO-xtquant.md`：

**P1（5 项）：**
- Kill Switch 内存级第二层防护
- MCP Server 交易工具权限控制
- 全局锁并发回测瓶颈
- Paper 读操作暴露实盘数据
- Profile override 可绕过 paper/live 隔离

**P2（10 项）：**
- 条件单不支持、PaperEngine market_value=0、缓存管理 CLI、首次设置引导、多账号支持、审计日志 environment 字段、Loader 复权方式不一致、版本兼容检查、order_stock 参数验证、条件单问题

---

## 裁决建议

- **建议动作：** ✅ **批准进入下一轮**
- **风险提示：** 无阻塞项。28 个测试全绿，所有验证通过。T+1 规则 fail-closed 设计，ETF T+0 白名单覆盖主要品种。

---
👤 **请审核本轮结果，批准后进入下一轮。**
