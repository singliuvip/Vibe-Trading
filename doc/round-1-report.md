# 📋 第 1 轮报告 — XTQuant/miniQMT 实盘接入架构分析

**轮次目标**：深度分析 XTQuant 集成方案，评估 wendao qmt_bridge 复用价值，产出详细分步实施方案

**分析人**：Main Agent (Claude Opus 4.6) | **调度**：Dispatcher
**日期**：2026-07-06

---

## 执行概要

- **步骤**：架构分析（第 1 轮）
- **状态**：待审批
- **下一轮**：委派 Develop Agent 按步骤 S0-S10 实施

---

## 1. 架构方案

### 1.1 最终推荐：Path A（直接 SDK 集成）为主体

经深度对比，推荐以 **Path A（broker_sdk transport，直接 import xtquant）** 为主体方案。核心理由：

| 维度 | 结论 |
|---|---|
| **架构同构性** | xtquant 与 Futu 完全同构（本地进程 + Python SDK IPC），复用现有 broker_sdk 分发逻辑**零侵入** |
| **安全链适配** | `service.py` → `sdk_order_gate.py`（Kill Switch / Mandate / Audit）的安全链**无需任何修改即可覆盖 xtquant** |
| **wendao 代码复用** | wendao qmt_bridge 是 HTTP Bridge 架构，代码不可直接移植。但其 **5 个工程知识点**极具借鉴价值 |
| **远期扩展** | 如需 Linux/macOS 远程调用，Path B（HTTP 桥接）可作为独立 transport 扩展 |

### 1.2 wendao 复用评估（回答用户第 1 个问题）

wendao qmt_bridge 是完整的 **HTTP/WebSocket Bridge** 架构（FastAPI 服务端 + 零依赖客户端，已发布 PyPI）。逐模块评估：

| wendao 模块 | 可复用？ | 复用方式 |
|---|---|---|
| `server/trading/manager.py` | ⚠️ 借鉴逻辑 | `session_id = os.urandom(2)`、账号脱敏、断线重连指数退避 — 翻译到 sdk.py |
| `server/xtdata_lock.py` | ✅ 直接借鉴 | BSON 并发崩溃的修复方案：`threading.Lock()` 包裹所有 xtdata 调用 |
| `server/downloader.py` | ✅ 远期复用 | K 线下载逻辑借鉴到 xtdata_loader.py；完整调度器远期需要时引入 |
| `server/config.py` | ⚠️ 借鉴逻辑 | `discover_account_ids()`（扫描 userdata_mini 目录）— 借鉴到 build_config() |
| `server/security.py` | ❌ 不适用 | wendao 是 HTTP 层 API Key 认证，Vibe-Trading 安全在交易安全层 |
| `client/base.py` | ❌ 不适用 | HTTP 客户端，Path A 直接 import xtquant |
| `client/market.py` / `trading.py` | ❌ 不适用 | 同上 |
| `server/routers/*` | ❌ 不适用 | FastAPI 路由层，Vibe-Trading 无对等层 |

**核心结论**：wendao 的直接代码不可移植，但其 **5 个工程知识点**可直接借鉴：
1. BSON 并发锁 (`threading.Lock`)
2. `session_id` 生成 (`os.urandom(2)`)
3. `account_id` 自动发现
4. 断线重连指数退避
5. 账号脱敏日志

### 1.3 与现有框架的集成（回答用户第 2 个问题）

```
┌──────────────────────────────────────────────────────────────┐
│  Entry Layer                                                 │
│  CLI / API Server / MCP Server — 无变更                      │
└──────────────────────┬───────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────┐
│  Agent Layer（agent/src/agent/）— 无变更                      │
└──────────────────────┬───────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────┐
│  Tool Layer（agent/src/tools/）— 无变更                       │
│  通过 service.py 统一接口调用，xtquant 对其他层透明             │
└──────────────────────┬───────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────┐
│  Trading Safety Layer（agent/src/trading/）                   │
│  ⭐ 新增：connectors/xtquant/sdk.py    ← 9 个统一接口实现      │
│  ⭐ 新增：connectors/xtquant/profiles.py ← 4 个 Profile       │
│  ⭐ 修改：profiles.py (+2行)     ← 注册 XTQUANT_PROFILES      │
│  ⭐ 修改：service.py (+2行)      ← SDK_CONNECTOR_MODULES      │
│  ⭐ 新增：src/live/a_stock_guard.py ← A 股规则校验            │
│  现有安全链（Kill Switch / Mandate / Audit）完全覆盖 xtquant   │
└──────────────────────┬───────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────┐
│  Data Layer（agent/backtest/loaders/）                        │
│  ⭐ 新增：xtdata_loader.py   ← xtdata 数据源 Loader           │
│  ⭐ 修改：registry.py (+2行) ← VALID_SOURCES & _loader_modules│
└─────────────────────────────────────────────────────────────┘
```

**关键集成点**：
- xtquant 作为第 11 个 broker_sdk connector，与 Futu 模式完全一致
- `_CONNECTOR_INSTRUMENT["xtquant"] = ("equity", None)` → `.SH/.SZ` 后缀自动识别为 CN_EQUITY
- 现有安全链（Kill Switch / Mandate Gate / Audit Ledger）对 xtquant **零修改覆盖**

---

## 2. 详细分步实施方案（10 步）

| 步骤 | 内容 | 高风险 | 预估工时 | 新增/修改文件 |
|---|---|---|---|---|
| **S0** | 包骨架：`__init__.py` + `classification.py` | 否 | 0.5h | `connectors/xtquant/__init__.py`, `classification.py` |
| **S1** | `XtQuantConfig` + `build_config` + `load_config` | 否 | 1h | `sdk.py` (配置部分) |
| **S2** | 模块级单例 + 连接管理 + xtdata 锁 | 否 | 2h | `sdk.py` (连接部分) |
| **S3** | 5 个读操作：check_status / account / positions / orders / quote | 否 | 3h | `sdk.py` (读操作部分) |
| **S4** | `get_historical_bars`（xtdata OHLCV） | 否 | 1h | `sdk.py` (历史数据部分) |
| **S5** | `place_order` + `cancel_order`（Paper→Shadow / Live→安全链） | 🔴 **是** | 3h | `sdk.py` (写操作部分) |
| **S6** | `profiles.py` 创建 + 注册到 `profiles.py` + `service.py` | 否 | 0.5h | `profiles.py`(新增) + 2 处修改 |
| **S7** | `xtdata_loader.py` + Loader Registry 注册 | 否 | 1h | `xtdata_loader.py` + `registry.py` 修改 |
| **S8** | 完整测试套件（全 mock，不依赖 xtquant） | 否 | 2h | `test_xtquant_connector.py`, `test_xtdata_loader.py` |
| **S9** | A 股 Order Guard（整手/T+1/涨跌停/价格精度） | 🔴 **是** | 2h | `a_stock_guard.py` + `sdk_order_gate.py` 修改 |
| **S10** | Loader xtdata 并发安全 | 否 | 0.5h | `xtdata_loader.py` |

**依赖关系**：S0 → S1 → S2 → S3/S4 → S5/S6/S7 → S8/S9/S10

---

## 3. 安全链验证（关键）

xtquant 的写操作安全链（**零修改覆盖**）：

```
place_order (service.py)
  │
  ├── environment == "paper"
  │   └── sdk.place_order(config, ...) → _paper_place_order() → Shadow Account
  │       绝不 import xtquant，绝不触及实盘
  │
  └── environment == "live"
      └── execute_live_order(broker="xtquant", ...)  ← sdk_order_gate.py
            ├── ① halt_flag_set("xtquant")              ← Kill Switch
            ├── ② [A 股 Order Guard]                      ← 新增 🔴
            ├── ③ load_mandate("xtquant")               ← Mandate Gate
            ├── ④ check_mandate(...)                     ← 品种/限额/杠杆
            ├── ⑤ sdk.place_order(config, ...)           ← xtquant SDK
            └── ⑥ audit: write_live_action(...)          ← Audit Ledger
```

---

## 4. 风险矩阵

| # | 风险 | 严重度 | 缓解措施 |
|---|---|---|---|
| R1 | Paper 模式误调用实盘 xtquant | 🔴 致命 | `profile=="paper"` 硬编码分支，测试断言不 import xtquant |
| R2 | xtdata BSON 并发崩溃 | 🔴 致命 | `threading.Lock()` 保护所有 xtdata 调用（借鉴 wendao） |
| R3 | Kill Switch 被绕过 | 🔴 致命 | sdk_order_gate 第一环 |
| R4 | session_id 多进程冲突 | 🟡 重要 | `os.urandom(2)` 而非 hash(path)（借鉴 wendao） |
| R5 | 断线重连导致重复下单 | 🟡 重要 | sdk_order_gate 出错时审计为 order_rejected，不静默重试 |
| R6 | account_id 泄露到日志 | 🟡 重要 | 脱敏日志 |
| R7 | miniqmt 进程未启动 | 🟢 次要 | check_status 返回清晰错误 |
| R8 | 非 Windows 平台 | 🟢 次要 | _check_platform() 提前报错 |

---

## 5. 文件清单汇总

### 新增（9 个文件）

| 文件 | 行数 | 来源 |
|---|---|---|
| `agent/src/trading/connectors/xtquant/__init__.py` | ~5 | 全新 |
| `agent/src/trading/connectors/xtquant/sdk.py` | ~500 | 全新（借鉴 wendao 知识点） |
| `agent/src/trading/connectors/xtquant/profiles.py` | ~60 | 全新（模板来自 futu/profiles.py） |
| `agent/src/trading/connectors/xtquant/classification.py` | ~30 | 全新（模板来自 futu/classification.py） |
| `agent/src/live/a_stock_guard.py` | ~120 | 全新 |
| `agent/backtest/loaders/xtdata_loader.py` | ~120 | 全新 |
| `agent/tests/test_xtquant_connector.py` | ~400 | 全新 |
| `agent/tests/test_xtdata_loader.py` | ~100 | 全新 |
| `agent/tests/test_a_stock_guard.py` | ~150 | 全新 |

### 修改（5 个文件）

| 文件 | 变更 |
|---|---|
| `agent/src/trading/profiles.py` | +2 行（导入 + 注册） |
| `agent/src/trading/service.py` | +2 行（SDK_CONNECTOR_MODULES + CONNECTOR_INSTRUMENT） |
| `agent/backtest/loaders/registry.py` | +2 行（VALID_SOURCES + _loader_modules） |
| `agent/src/live/sdk_order_gate.py` | ~15 行（A 股 Order Guard 集成） |
| `pyproject.toml` | +2 行（xtquant optional dependency） |

---

## 裁决建议

- **建议动作**：批准架构方案，进入实施轮次
- **风险提示**：S5（下单/撤单）和 S9（A 股 Order Guard）为高风险步骤，需 Develop Agent 特别关注安全边界；实施后需 Review Agent 严格审查 paper/live 分支隔离

---

👤 **请审核本轮架构方案。批准后进入第 2 轮（S0-S4 基础实现），或指示调整方向。**
