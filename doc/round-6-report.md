# 📋 第 6 轮报告

**轮次目标：** Docker 迁移 + 宿主机 xtquant 集成架构设计 & 实现

**日期：** 2026-07-06 | **状态：** ✅ 已完成

---

## 实现结果

### 新增文件（5 个）

| 文件 | 说明 | 行数 |
|---|---|---|
| `agent/qmt_bridge/__init__.py` | QMT Bridge 包入口 | 5 |
| `agent/qmt_bridge/server.py` | FastAPI 服务端，8 个 endpoint + Kill Switch + Bearer 鉴权 | ~450 |
| `agent/src/trading/connectors/xtquant/http_config.py` | HTTP 连接配置（环境变量驱动） | ~110 |
| `agent/src/trading/connectors/xtquant/sdk_http.py` | HTTP 桥接版 connector，镜像 sdk.py 签名 | ~300 |
| `agent/tests/test_xtquant_http_connector.py` | 25 个测试（mock HTTP） | ~380 |

### 修改文件（7 个）

| 文件 | 改动 |
|---|---|
| `agent/src/trading/types.py` | Transport 新增 `broker_http` |
| `agent/src/trading/connectors/xtquant/__init__.py` | 更新 docstring 说明两种 transport |
| `agent/src/trading/connectors/xtquant/profiles.py` | 新增 4 个 `broker_http` profile |
| `agent/src/trading/service.py` | 6 处 `broker_sdk` → `in ("broker_sdk", "broker_http")` + `_SDK_CONNECTOR_MODULES` 新增 `xtquant-http` + `_CONNECTOR_INSTRUMENT` 新增 |
| `agent/backtest/loaders/xtdata_loader.py` | 新增 HTTP fallback 路径 |
| `docker-compose.yml` | 新增 6 个 xtquant 环境变量 |

### 测试结果

```
✅ 新增测试: 25/25 passed
✅ 已有测试: 28/28 passed (test_sdk_order_gate.py)
✅ 零回归
```

### 关键设计

1. **零破坏** — `sdk.py` 原有代码一字节未改
2. **无缝切换** — `broker_sdk`（Windows 原生）↔ `broker_http`（Docker/HTTP）
3. **Kill Switch 双通道** — Bridge 侧 + Docker volume 同步
4. **回退只需改一行** — transport 配置切换即可

### 使用方式

**宿主机启动 QMT Bridge：**
```powershell
cd C:\Project\Vibe-Trading\agent
$env:PYTHONPATH='.'
python -m qmt_bridge.server --host 127.0.0.1 --port 8888
```

**Docker 启动 Vibe-Trading：**
```bash
docker compose up --build
```
（环境变量已配置 `XTQUANT_BRIDGE_URL=http://host.docker.internal:8888`）

### 新增 HTTP Profiles

| Profile ID | 环境 | 能力 |
|---|---|---|
| `xtquant-http-paper` | 模拟 | 只读 |
| `xtquant-http-paper-trade` | 模拟 | 只读 + 下单 |
| `xtquant-http-live-readonly` | 实盘 | 只读 |
| `xtquant-http-live-trade` | 实盘 | 只读 + 下单（需 Mandate） |

---

## 原始架构方案（供参考）

---

## 架构方案

### 核心问题

Vibe-Trading Docker 容器运行在 Linux（`python:3.11-slim`），而 xtquant 是 Windows-only C 扩展，通过本地 IPC 与 miniQMT 进程通信。两者无法在同一容器内共存。

### 推荐方案：QMT Bridge HTTP 代理模式（方案 A）

```
┌──────────────────────────────────────────────────────┐
│  Windows 宿主机                                       │
│  ┌──────────┐  IPC   ┌──────────────────────────┐   │
│  │ miniQMT   │◄──────│ QMT Bridge :8888          │   │
│  │ (华泰QMT) │       │ FastAPI · Bearer Token    │   │
│  └──────────┘        │ 仅监听 127.0.0.1          │   │
│                       └──────────┬───────────────┘   │
│                                  │ HTTP (lo)          │
│  ┌───────────────────────────────▼────────────────┐  │
│  │ Docker 容器 (Linux)                             │  │
│  │ host.docker.internal:8888 → sdk_http.py        │  │
│  │ → service.py → 工具层 → Agent 层               │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

**选择理由**：
- ✅ 用户已有 QMT Bridge (FastAPI, `:8888`)，可直接复用
- ✅ 与 Vibe-Trading 已有的 Robinhood `remote_mcp` transport 架构一致
- ✅ `sdk.py` 原有代码**零修改**，纯增量 `sdk_http.py`
- ✅ 回退只需改一行配置（`transport: "native"`→回 Windows 本地）
- ✅ 实施周期 4 天

### 方案对比

| 维度 | A: HTTP Bridge ⭐ | B: RPC/子进程 | C: 混合模式 | D: Windows 容器 |
|---|---|---|---|---|
| 复杂度 | ⭐⭐ 低 | ⭐⭐⭐⭐ 高 | ⭐⭐⭐ 中 | ⭐⭐⭐⭐⭐ 极高 |
| 已有基础 | ✅ QMT Bridge | ❌ 从零搭建 | ⚠️ 部分 | ❌ 需Windows Server |
| 回退安全性 | ✅ 一行配置 | ❌ 复杂 | ⚠️ | ❌ |
| 工期 | 4 天 | 5-7 天 | 3-4 天 | 不可行 |

---

## 改动范围

### 新增文件（4 个）

| 文件 | 说明 |
|---|---|
| `agent/src/trading/connectors/xtquant/sdk_http.py` | HTTP 桥接版 connector（~500行） |
| `agent/src/trading/connectors/xtquant/http_config.py` | HTTP 连接配置（~80行） |
| `agent/qmt_bridge/server.py` | QMT Bridge 补齐 8 个 endpoint（如需） |
| `agent/tests/test_xtquant_http_connector.py` | Mock HTTP 测试 |

### 修改文件（7 个）

| 文件 | 改动 |
|---|---|
| `agent/src/trading/service.py` | `_SDK_CONNECTOR_MODULES` 增加 http 变体 |
| `agent/src/trading/connectors/xtquant/__init__.py` | 条件导出（native vs http） |
| `agent/src/trading/connectors/xtquant/profiles.py` | 新增 `transport="broker_http"` |
| `agent/src/trading/types.py` | 可能新增 transport 枚举值 |
| `agent/backtest/loaders/xtdata_loader.py` | HTTP fallback 路径 |
| `docker-compose.yml` | 新增环境变量 |
| `agent/src/trading/connectors/xtquant/sdk.py` | 不改逻辑，仅加 platform check 标记 |

---

## 关键安全设计

### Kill Switch 双通道

```
宿主机 touch ~/.vibe-trading/live/HALT
       │
       ├──► QMT Bridge 定期检查 → 所有请求返回 blocked
       └──► Docker volume vibe-home 同步 → 容器内 halt_flag_set()
```

### 网络暴露控制

- QMT Bridge 仅监听 `127.0.0.1:8888`
- Docker 通过 `host.docker.internal` 访问宿主机 lo 接口
- 容器 `127.0.0.1:8899` 不对外暴露

---

## 实现步骤（5 个 Phase）

| Phase | 内容 | 工期 |
|---|---|---|
| **Phase 1** | QMT Bridge 补齐 endpoint + 鉴权强化 + Kill Switch 集成 | 1 天 |
| **Phase 2** | `sdk_http.py` + `http_config.py` + 条件导出 | 1 天 |
| **Phase 3** | `xtdata_loader.py` HTTP fallback | 0.5 天 |
| **Phase 4** | docker-compose 环境变量 + 配置模板 | 0.5 天 |
| **Phase 5** | 测试套件 + 文档 | 1 天 |

---

## 风险与回退

| 风险 | 概率 | 缓解 |
|---|---|---|
| QMT Bridge 现有 endpoint 不完整 | 中 | Phase 1 先审查补齐 |
| Kill Switch 文件同步延迟 | 低 | 双通道检查（Bridge + 容器内） |
| xtquant 并发未加锁 | 中 | Bridge 侧同样 `threading.Lock()` |
| Bridge 进程退出 | 低 | 容器内自动重连 |

**回退方案**：改 `transport: "http"` → `"native"`，在 Windows 直接运行 vibe-trading。原有代码**一字节未改**。

---

## 裁决建议

- **建议动作：** 批准进入 Phase 1 实现
- **风险提示：** QMT Bridge 需要先审查现有 endpoint 完整度，可能需要补齐 3-5 个 endpoint
- **高风险操作：** 无（本阶段仅涉及只读操作，不涉及下单/撤单）

---

👤 **请审核本轮架构方案，批准后进入实现阶段。**
