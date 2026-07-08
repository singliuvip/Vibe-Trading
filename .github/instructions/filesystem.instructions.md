---
applyTo: "**"
---

# 文件系统规范 — Vibe-Trading

> 适用范围：所有涉及文件读写、路径引用的代码。严格遵守以下目录布局与访问规则。

---

## 1. 项目目录结构

```
Vibe-Trading/
├── agent/                      ← 后端包代码（受版本控制）
│   ├── api_server.py
│   ├── mcp_server.py
│   ├── src/                    # 核心源码
│   ├── cli/                    # CLI 包
│   ├── backtest/               # 回测引擎
│   └── tests/                  # pytest 测试
├── frontend/                   ← 前端代码（受版本控制）
├── wiki/                       ← 公开 Wiki
├── pyproject.toml
└── docker-compose.yml
```

---

## 2. 运行时数据目录（`~/.vibe-trading/`）

所有运行时生成的持久化数据存放在用户主目录下，**禁止写入仓库**：

```
~/.vibe-trading/
├── agent.json              # Agent 配置（LLM provider、broker 等）
├── .env                    # 真实凭据（API Keys、broker token）
├── cache/                  # 数据源缓存（tushare/yfinance/akshare 等）
│   └── <loader>/<symbol>/  # 按 loader 和 symbol 组织
├── runs/                   # 回测运行结果
│   └── <run_id>/
│       ├── run_card.json   # 回测元数据
│       ├── run_card.md     # 人读版报告
│       └── artifacts/      # 图表、CSV 等
├── sessions/               # 会话历史（JSONL）
│   └── <session_id>.jsonl
├── memory/                 # 持久化 Agent 记忆
├── shadow_accounts/        # Shadow Account 数据
├── audit/                  # 交易审计日志（高敏感）
│   └── <broker>_audit.jsonl
├── mandate/                # 用户提交的交易约束
│   └── <broker>_mandate.json
└── kill_switch/            # Kill Switch 状态文件
    └── <broker>.halt       # 存在此文件 → 该 broker 立即停机
```

---

## 3. 各路径访问规范

| 路径 | 读 | 写 | 负责模块 | 说明 |
|---|---|---|---|---|
| `~/.vibe-trading/agent.json` | ✅ | ✅ | `src/config/` | 用户配置，不含凭据 |
| `~/.vibe-trading/.env` | ✅ | ⚠️ | 仅用户手动 | 真实凭据，程序只读取，不写入 |
| `~/.vibe-trading/cache/` | ✅ | ✅ | `backtest/loaders/` | OHLCV 缓存，按需写入 |
| `~/.vibe-trading/runs/` | ✅ | ✅ | `backtest/runner.py` | 回测结果 |
| `~/.vibe-trading/sessions/` | ✅ | ✅ | `src/session/` | 会话 JSONL，每次追加 |
| `~/.vibe-trading/memory/` | ✅ | ✅ | `src/memory/` | 持久化记忆 |
| `~/.vibe-trading/audit/` | ✅ | ✅ | `src/trading/` | 审计日志，只追加 |
| `~/.vibe-trading/mandate/` | ✅ | ✅ | `src/trading/` | 交易约束文件 |
| `~/.vibe-trading/kill_switch/` | ✅ | ✅ | `src/trading/` | Kill Switch 状态 |
| 仓库代码目录（`agent/`、`frontend/`） | ✅ | ⚠️ | 仅开发模式 | 不得写入运行时数据 |

---

## 4. 路径使用规范

```python
# ✅ 正确：通过配置模块获取基础路径
from src.config import get_data_dir, get_cache_dir, get_runs_dir

cache_dir = get_cache_dir()          # ~/.vibe-trading/cache/
runs_dir = get_runs_dir()            # ~/.vibe-trading/runs/

# ✅ 正确：Kill Switch 文件检查
kill_switch_path = get_data_dir() / "kill_switch" / f"{broker_name}.halt"
if kill_switch_path.exists():
    raise KillSwitchError(f"{broker_name} is halted")

# ❌ 错误：硬编码绝对路径
path = "/home/user/.vibe-trading/cache/tushare/AAPL.parquet"

# ❌ 错误：写入仓库目录
with open("agent/data/cache.json", "w") as f: ...
```

---

## 5. 安全边界

- **`.env` 文件禁止被程序写入**，只能由用户手动编辑
- **审计日志只追加**，禁止修改或删除历史记录
- **Kill Switch 优先**：任何 broker 写操作前必须检查 kill switch 文件
- **缓存不含凭据**：缓存目录内容可被安全删除，不含任何敏感信息
- 程序生成的文件禁止写入 `agent/` 仓库目录（运行时数据 ≠ 代码）
- `runs/` 目录受 Docker named volume 保护，`docker compose up --build` 不会清除

---

## 6. 禁止的模式

| 禁止 | 原因 | 正确做法 |
|---|---|---|
| 硬编码 `~/.vibe-trading/` 字符串 | 路径变更时需全局修改 | 使用 `get_data_dir()` 等配置函数 |
| 将 `.env` / `agent.json` 提交到仓库 | 凭据泄露风险 | `.gitignore` 已排除，不手动 add |
| 在 `agent/` 目录写入回测结果或缓存 | 污染版本控制 | 写入 `~/.vibe-trading/` |
| 修改或删除 `audit/` 下的历史记录 | 破坏审计完整性 | 只追加 |
| 跳过 Kill Switch 检查直接下单 | 绕过安全层 | 通过 Mandate Gate 统一检查 |
