# 📋 第 7 轮报告 — 桌面端客户端架构分析

**轮次目标：** 分析将 Vibe-Trading 打包为桌面端客户端的架构方案
**关键约束：** ① 服务端运行在宿主机，不运行在 Docker；② 重量级依赖安装时下载

**分析人：** Main Agent (Claude Opus 4.6) | **调度：** Dispatcher
**日期：** 2026-07-07

---

## 执行概要

- **步骤**：架构分析（第 1 轮）
- **状态**：待审批
- **下一轮**：按用户批准的 Phase 进入实现

---

## 1. 方案对比矩阵

| 维度 | A: Electron | B: Tauri | C: PyInstaller/Nuitka | D: PWA + pip | **E: Tauri + uv (推荐)** |
|---|---|---|---|---|---|
| **安装包体积** | ~150MB (Chromium) | ~5MB (系统 WebView) | ~200-400MB (冻结) | 极小 | **~5MB + 按需下载** |
| **跨平台** | ✅ Win/Mac/Linux | ✅ Win/Mac/Linux | ⚠️ C 扩展问题多 | ✅ 体验差 | ✅ Win/Mac/Linux |
| **依赖按需下载** | ✅ sidecar 可实现 | ✅ sidecar 可实现 | ❌ 必须预冻结 | ✅ pip 天然支持 | ✅ **uv + optional-deps** |
| **自动更新** | ✅ electron-updater | ✅ tauri-updater 内置 | ⚠️ 需自建 | ❌ 手动 pip upgrade | ✅ tauri-updater + pip |
| **开发成本** | 中 | 中 | 高（调试难） | 低 | **中** |
| **用户体验** | 好（独立窗口） | **好（更原生）** | 差 | 差（浏览器标签页） | **好** |
| **内存占用** | 高 (~200MB+) | **低 (~30MB)** | 取决于打包 | 低 | **低** |
| **与 QMT Bridge 兼容** | ✅ | ✅ | ✅ | ✅ | **✅ 可取消 Bridge** |

> **推荐：方案 E — Tauri 2 壳 + uv 管理 Python sidecar**

---

## 2. 推荐方案架构

```
┌─────────────────────────────────────────────────┐
│  Tauri 2 Shell (Rust, ~5MB)                     │
│  ┌─────────────────────────────────────────────┐│
│  │  WebView (系统原生)                          ││
│  │  加载 frontend/dist → React 19 SPA          ││
│  │  fetch("http://127.0.0.1:8899/...")         ││
│  └───────────────────┬─────────────────────────┘│
│                      │ HTTP/WS (localhost)       │
│  ┌───────────────────▼─────────────────────────┐│
│  │  Python Sidecar (子进程)                     ││
│  │  uv venv + pip install vibe-trading-ai      ││
│  │  → vibe-trading serve --port 8899           ││
│  │  FastAPI + LangGraph ReAct Agent            ││
│  └─────────────────────────────────────────────┘│
│                                                  │
│  Lifecycle Manager (Rust)                        │
│  · 首次启动: 检测/安装 Python → uv create venv  │
│  · 启动: spawn sidecar → 健康检查 → 加载 WebView│
│  · 退出: graceful shutdown sidecar              │
│  · 更新: tauri-updater (壳) + pip upgrade (后端)│
└─────────────────────────────────────────────────┘
```

### 核心优势

| 维度 | 说明 |
|---|---|
| **体积小** | Tauri 使用系统 WebView，壳 ~5MB，vs Electron 的 150MB |
| **依赖按需下载** | uv 首次启动创建 venv 并安装，满足"安装时下载"需求 |
| **前端零改动** | 现有 React 19 + Vite 前端直接复用 |
| **后端零改动** | 通过 `vibe-trading serve` 子进程，与 Docker 模式一致 |
| **QMT Bridge 可简化** | 桌面端运行在宿主机，可直接访问本地 xtquant |
| **自动更新内置** | Tauri 2 自带 updater 插件 |

---

## 3. 依赖管理策略

### 3.1 依赖分组设计

```toml
[project.optional-dependencies]
# ── 数据源组 ──
data-cn = ["tushare>=1.2.89", "akshare>=1.12.0"]
data-global = ["yfinance>=0.2.30", "ccxt>=4.0.0"]

# ── 因子/ML 组 ──
factors = ["scikit-learn>=1.3.0", "joblib>=1.3.0", "bottleneck>=1.3.7"]

# ── 可视化/报告组 ──
report = ["matplotlib>=3.7.0", "weasyprint>=60.0"]

# ── 文档解析组 ──
docs = ["openpyxl>=3.1.0", "python-docx>=1.1.0", "python-pptx>=0.6.23", "pypdfium2>=4.0.0"]

# ── 桌面全功能（安装器默认选中）──
desktop = ["vibe-trading-ai[data-cn,data-global,factors,report,docs]"]

# ── 完整安装 ──
all = ["vibe-trading-ai[desktop,ibkr,dingtalk]"]
```

### 3.2 需移入可选组的依赖

| 当前在 dependencies | 移至 | 理由 |
|---|---|---|
| `tushare`, `akshare` | `data-cn` | 中国市场专用 |
| `yfinance`, `ccxt` | `data-global` | 全球市场专用 |
| `scikit-learn`, `joblib`, `bottleneck` | `factors` | ML 因子计算 |
| `matplotlib`, `weasyprint` | `report` | 报告生成 |
| `openpyxl`, `python-docx`, `python-pptx`, `pypdfium2` | `docs` | 文档解析 |

精简后核心依赖约 **25 包**：LangChain/LangGraph + FastAPI/uvicorn + pandas/numpy/scipy + httpx + pydantic + rich + duckdb。

### 3.3 懒导入机制

新增 `agent/src/utils/lazy_import.py`，所有使用可选依赖的模块改为懒导入 + 友好报错：

```python
def _require(package: str, group: str) -> None:
    try:
        importlib.import_module(package)
    except ImportError:
        raise ImportError(
            f"'{package}' is required. Install with: "
            f"pip install 'vibe-trading-ai[{group}]'"
        )
```

**影响文件**：`agent/src/tools/*.py`、`agent/backtest/loaders/*.py`、`agent/src/factors/`

---

## 4. 与现有架构兼容性

| 现有入口 | 影响 | 说明 |
|---|---|---|
| Docker Compose | ✅ 共存 | 桌面端是独立分发渠道 |
| CLI (`vibe-trading`) | ✅ 无影响 | 桌面端内部调用 `vibe-trading serve` |
| MCP Server | ✅ 无影响 | 独立进程 |
| QMT Bridge | ✅ **可简化** | 桌面端可直接加载 xtquant |
| `~/.vibe-trading/` | ✅ 无影响 | 共享同一数据目录 |
| `pip install vibe-trading-ai` | ✅ 无影响 | 桌面端安装器内部执行 pip install |

### 需修改的文件

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | 依赖分组，核心依赖精简 |
| `agent/src/utils/lazy_import.py` | **新增**：懒导入工具 |
| `agent/src/tools/*.py` | 可选依赖懒导入 |
| `agent/backtest/loaders/*.py` | 可选依赖懒导入 |
| `agent/src/factors/*.py` | 可选依赖懒导入 |
| `agent/api_server.py` | CORS 增加 `tauri://localhost` |
| `frontend/src/config.ts` | API URL 检测逻辑 |
| `desktop/` | **新增**：Tauri 项目目录 |

---

## 5. 分步实施计划

| Phase | 内容 | 工时 | 风险 |
|---|---|---|---|
| **Phase 1** | 依赖分组 + 懒导入 + 测试 | 1 周 | 低 |
| **Phase 2** | Tauri 项目脚手架 + 前端加载 | 1 周 | 低 |
| **Phase 3** | Python Sidecar 生命周期管理 | 2 周 | 中 |
| **Phase 4** | 打包分发 + CI/CD | 1 周 | 中 |
| **Phase 5** | QMT 直连优化（可选） | 1 周 | 低 |

### Phase 1 详细范围

1. 修改 `pyproject.toml`：拆分 optional-dependencies
2. 新增 `agent/src/utils/lazy_import.py`：懒导入辅助函数
3. 修改 loaders / tools / factors：改为懒导入
4. 验收：`pip install vibe-trading-ai && vibe-trading serve` 成功启动

---

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| weasyprint 系统依赖（Pango/Cairo） | 移入 `[report]` 可选组，默认不装 |
| Python 版本不兼容 (< 3.11) | uv 可自动下载管理 Python 版本 |
| 安装时网络问题 | 重试机制 + 离线包支持 |
| 杀毒软件误报 | 代码签名 + 白名单指南 |
| 多 Python 环境冲突 | uv 创建隔离 venv 在 `~/.vibe-trading/venv/` |

---

## 裁决建议

- **建议动作：** 批准进入 Phase 1 实现（依赖分组 + 懒导入）
- **风险提示：** Phase 1 为低风险 Python 侧改造，不涉及 Rust/Tauri，可独立验证

---
👤 **请审核本轮分析结果，批准后进入 Phase 1 实现。**
