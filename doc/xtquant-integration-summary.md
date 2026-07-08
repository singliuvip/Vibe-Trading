# XTQuant/miniQMT 实盘接入 — 会话总结

**日期**：2026-07-06 | **会话时长**：~3 小时 | **轮次**：5 轮

---

## 一、架构分析（第 1 轮）

- 阅读 `doc/features/xtquant-integration.md` 架构文档
- 深入分析 `C:\wendao` 已实现的 qmt_bridge（FastAPI HTTP Bridge），识别 **5 个可借鉴知识点**：
  1. BSON 并发锁 (`threading.Lock`)
  2. `session_id` 生成 (`os.urandom(2)`)
  3. `account_id` 自动发现
  4. 断线重连指数退避
  5. 账号脱敏日志
- **决策**：Path A（broker_sdk 直连），与 Futu 模式同构，安全链**零修改**覆盖

### wendao 复用评估

| wendao 模块 | 可复用？ | 复用方式 |
|---|---|---|
| `server/trading/manager.py` | ⚠️ 借鉴逻辑 | session_id、脱敏、重连 → 翻译到 sdk.py |
| `server/xtdata_lock.py` | ✅ 直接借鉴 | `threading.Lock` 方案 |
| `server/downloader.py` | ✅ 远期复用 | 下载逻辑借鉴到 xtdata_loader.py |
| `client/*` (HTTP) | ❌ 不适用 | Path A 直接 import xtquant，不走 HTTP |

---

## 二、10 步实施（第 2-5 轮）

### 新增文件（9 个）

| 文件 | 行数 | 职责 |
|---|---|---|
| `agent/src/trading/connectors/xtquant/__init__.py` | 48 | 包入口 |
| `agent/src/trading/connectors/xtquant/classification.py` | 36 | READ/WRITE 分类 |
| `agent/src/trading/connectors/xtquant/sdk.py` | ~1200 | 9 个统一接口 + 连接管理 |
| `agent/src/trading/connectors/xtquant/profiles.py` | 60 | 4 个 TradingProfile |
| `agent/src/live/a_stock_guard.py` | 120 | A 股规则校验 |
| `agent/backtest/loaders/xtdata_loader.py` | 120 | xtdata 数据源 |
| `agent/tests/test_xtquant_connector.py` | 450 | 33 全 mock 测试 |
| `agent/tests/test_xtdata_loader.py` | 100 | 6 全 mock 测试 |
| `agent/tests/test_a_stock_guard.py` | 150 | 20 单元测试 |

### 修改文件（6 个）

| 文件 | 变更 |
|---|---|
| `agent/src/trading/profiles.py` | +2 行：导入 + 注册 XTQUANT_PROFILES |
| `agent/src/trading/service.py` | +4 行：SDK_CONNECTOR_MODULES + CONNECTOR_INSTRUMENT + profile_supports_live_runner |
| `agent/backtest/loaders/registry.py` | +2 行：VALID_SOURCES + _loader_modules |
| `agent/src/live/sdk_order_gate.py` | ~15 行：A 股 Order Guard 集成 + _safe_read 扩展 + _allow extra_checks |
| `agent/api_server.py` | ~80 行：_known_live_brokers 扩展、sdk_status、心跳、stop/start runner |
| `pyproject.toml` | +2 行：xtquant 可选依赖 |

### 前端修改（2 个）

| 文件 | 变更 |
|---|---|
| `frontend/src/lib/api.ts` | `LiveBrokerStatus` 添加 `sdk_status` 类型 |
| `frontend/src/pages/Runtime.tsx` | SDK 连接面板 + deriveRiskState 扩展 |

### 测试

```
87/87 全量测试通过（全 mock，零 xtquant 依赖）
33  test_xtquant_connector.py
 6  test_xtdata_loader.py
20  test_a_stock_guard.py
28  test_sdk_order_gate.py (回归)
```

---

## 三、真实连接与调试

### 关键问题修复

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | `connect()` 返回 -1 | `mini_qmt_path` 需指向 `userdata_mini` | 配置修正 |
| 2 | 余额/持仓为 0 | xtquant 使用**英文**属性名（非中文 `总资产` 等） | `total_asset`/`cash`/`stock_code` 等全部修正 |
| 3 | 账号发现为空 | 华泰 QMT 账号在 `users/` 子目录（非顶层数字目录） | 扩展 `_discover_account_ids` |
| 4 | `XtQuantTrader` 不存在 | 子模块 `xtquant.xttrader` 未加载 | `_import_xtquant` 预加载 xttrader/xtdata/xttype |
| 5 | trader API 类型错误 | 传 string 而非 `StockAccount` 对象 | 添加 `_to_stock_account()` helper |
| 6 | 编码错误 | 中文路径 GBK 编码 | `_read_config_file` utf-8 → gbk 回退 |

### 连接参数

```
QMT 路径: C:\Users\51403\迅投极速策略交易系统交易终端 华泰证券QMT模拟\userdata_mini
Account:  1000003 (htqmt_test1)
SDK:      xtquant (pip 安装)
```

---

## 四、前端集成问题修复

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | Runtime 不显示 xtquant | `_known_live_brokers` 仅返回 MCP broker | 扩展到 broker_sdk |
| 2 | 余额显示为 0 | 属性名中→英修正 | 18.75 万亿模拟金 |
| 3 | 状态"休眠"(Dormant) | `deriveRiskState` 要求 mandate | SDK 连接器专属 "Idle" 判断 |
| 4 | "auth missing" | SDK 无 OAuth | `sdk_status=ok` → `oauth_token_present=true` |
| 5 | Stop Runner 无效 | 仅支持 `remote_mcp` transport | 扩展 SDK connector 的 start/stop 逻辑 |
| 6 | 无 mandate | 未创建 | 全面 A 股交易约束 mandate |

---

## 五、最终状态

```
┌─────────────────────────────────────────────┐
│ ★ xtquant — Active (绿色)                   │
│ ● auth present  ● runner alive ● mandate    │
│                                              │
│ SDK:          ok                             │
│ Platform:     Windows                        │
│ Account:      1000003 (华泰QMT模拟)           │
│ Balance:      ¥18,754,478,480,083            │
│                                               │
│ Mandate:                                       │
│   单笔上限:   ¥50,000                         │
│   总敞口:     ¥500,000                        │
│   杠杆:       1.0× (现金)                      │
│   每日交易:   10 次                            │
│   品种:       equity, etf                     │
│   市场:       A 股 (cn_equity)                 │
│   到期:       2026-08-05 (30天)               │
│   Halt平仓:   是                               │
│                                               │
│ Profile:      xtquant-paper-trade             │
│               (Shadow Account 模拟下单)        │
│                                               │
│ Tests:        87/87 ✅                         │
└─────────────────────────────────────────────┘
```

### 四个 Profile

| Profile | 下单 | 机制 |
|---|---|---|
| `xtquant-paper` | ❌ | 只读 |
| `xtquant-paper-trade` | ✅ | Shadow Account 模拟撮合 |
| `xtquant-live-readonly` | ❌ | 实盘只读 |
| `xtquant-live-trade` | ✅ | 实盘下单（需 Mandate） |

---

## 六、产出文件清单

### 架构文档（5 个）

| 文件 |
|---|
| `doc/round-1-report.md` — 架构分析与方案 |
| `doc/round-2-report.md` — S0-S4 基础实现 |
| `doc/round-3-report.md` — S5-S6 下单+注册 |
| `doc/round-4-report.md` — S7-S8 Loader+测试 |
| `doc/round-5-report.md` — S9-S10 A股Guard+收尾 |

### 核心代码

```
agent/src/trading/connectors/xtquant/   ← Trading Safety Layer
  __init__.py / sdk.py / profiles.py / classification.py

agent/src/live/a_stock_guard.py         ← A 股规则校验（通用模块）

agent/backtest/loaders/xtdata_loader.py ← Data Layer

agent/tests/
  test_xtquant_connector.py
  test_xtdata_loader.py
  test_a_stock_guard.py
```

### 修改的现有文件

```
agent/api_server.py                     ← live/status 端点增强
agent/src/trading/profiles.py           ← XTQUANT_PROFILES 注册
agent/src/trading/service.py            ← SDK 模块注册 + runner 支持
agent/backtest/loaders/registry.py      ← xtdata loader 注册
agent/src/live/sdk_order_gate.py        ← A 股 Order Guard 集成
pyproject.toml                          ← xtquant 可选依赖
frontend/src/lib/api.ts                 ← sdk_status 类型
frontend/src/pages/Runtime.tsx          ← SDK 面板 + 风险状态
```

---

## 七、安全边界确认

| 检查项 | 状态 |
|---|---|
| Kill Switch | ✅ `halt_flag_set("xtquant")` |
| Mandate Gate | ✅ `check_mandate()` → CN_EQUITY |
| A 股 Order Guard | ✅ 整手/涨跌停/精度 |
| Audit Ledger | ✅ `write_live_action()` |
| Paper/Live 隔离 | ✅ Paper 绝不 import xtquant |
| 配置文件路径 | ✅ `~/.vibe-trading/xtquant.json` |
| 87 测试全 mock | ✅ 零真实 SDK 依赖 |
