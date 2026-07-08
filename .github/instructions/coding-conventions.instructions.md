---
applyTo: "**/*.py"
---

# 编码规范 — Vibe-Trading

**适用范围：** 所有 Python 源文件（`agent/` 目录下）。编写或修改代码时必须严格遵守以下规范。

---

## 1. 通用原则

- Python 3.11+，遵循 PEP 8
- 行宽限制：**120 字符**（与项目 ruff 配置一致）
- 使用 **4 空格缩进**，禁止 Tab
- 字符串优先使用双引号 `"`
- 导入顺序：标准库 → 第三方库 → 本地模块，各组之间空一行

---

## 2. 命名规范

| 标识符类型 | 命名格式 | 示例 |
|---|---|---|
| 模块 / 文件名 | `snake_case` | `order_gate.py`, `kill_switch.py` |
| 类名 | `PascalCase` | `MandateGate`, `BacktestRunner` |
| 函数 / 方法 | `snake_case` | `place_order()`, `run_backtest()` |
| 变量 | `snake_case` | `session_id`, `broker_name` |
| 常量 | `UPPER_SNAKE_CASE` | `MAX_RETRY_COUNT`, `DEFAULT_TIMEOUT` |
| 私有成员 | 单下划线前缀 | `_validate_mandate()`, `_audit_log` |
| Agent 工具函数 | `snake_case`（清晰描述动作） | `run_backtest`, `get_market_data` |

---

## 3. 类型注解

- 所有**公共函数签名**必须有类型注解（参数 + 返回值）
- 使用 `from __future__ import annotations` 避免前向引用问题（在需要时）
- 复杂类型使用 `from typing import` 或 `collections.abc`

```python
# ✅ 正确
def place_order(symbol: str, qty: float, side: str) -> dict[str, Any]:
    ...

# ❌ 错误：缺少类型注解
def place_order(symbol, qty, side):
    ...
```

---

## 4. Agent 工具注册规范

- 每个工具用 `@tool` 装饰器注册，放在 `agent/src/tools/` 下独立文件
- 工具 docstring 第一行为工具描述（LLM 可读），必须清晰简洁
- 工具只做参数解析和 Service 调用，**不实现业务逻辑**
- 工具必须在输入边界做校验，拒绝无效参数

```python
# ✅ 正确示例
@tool
def run_backtest(symbol: str, start_date: str, end_date: str) -> str:
    """Run a vectorized backtest for the given symbol and date range.

    Args:
        symbol: Ticker symbol, e.g. 'AAPL' or '600036.SH'
        start_date: ISO date string, e.g. '2023-01-01'
        end_date: ISO date string, e.g. '2023-12-31'
    """
    if not symbol or not symbol.strip():
        return "Error: symbol is required"
    return BacktestService.run(symbol, start_date, end_date)
```

---

## 5. 错误处理

- 在系统边界（API 路由、工具入口、CLI 命令）做输入校验和异常捕获
- 内部模块不捕获通用异常（`except Exception`），让错误向上传播
- 使用具体异常类型，避免裸 `except:` 或 `except Exception as e: pass`
- 交易安全层的错误必须记录到审计日志，不得静默失败

```python
# ✅ 正确：具体异常
try:
    result = broker.place_order(order)
except BrokerConnectionError as e:
    audit_log.record_error(order, e)
    raise

# ❌ 错误：静默失败
try:
    result = broker.place_order(order)
except Exception:
    pass
```

---

## 6. 安全规范

- **禁止**在代码中硬编码凭据、API Key、token（即使是测试值）
- 凭据通过环境变量或 `~/.vibe-trading/` 路径读取
- API 路由参数必须验证，防止路径遍历（`../`）、注入等攻击
- 生成代码在执行前必须经过预检（`preflight.py`）
- 文件操作限制在允许的根路径内，禁止任意路径写入

---

## 7. 测试规范

- 测试文件放在 `agent/tests/`，命名格式：`test_<模块名>.py`
- 每个测试函数以 `test_` 开头
- 不在测试中发起真实 broker 写操作（使用 Mock / paper 模式）
- 不在测试中访问真实外部 API（使用 `pytest-mock` 或 `responses`）
- 因子 Zoo 测试必须通过 `test_alpha_purity.py` 和 `test_lookahead.py`

```python
# ✅ 正确：Mock broker
def test_place_order_calls_mandate_gate(mocker):
    mock_gate = mocker.patch("src.trading.mandate_gate.check")
    mock_gate.return_value = True
    ...
```

---

## 8. 禁止的模式

| 禁止 | 原因 |
|---|---|
| `from src.trading import *` | 破坏安全层封装 |
| 在 `tools/` 中写业务逻辑 | 职责混淆 |
| 回测 Loader 中直接 `requests.get(...)` | 绕过缓存和 Registry |
| 在 `agent/` 下写入 `.env` 文件 | 安全风险 |
| `except Exception: pass` | 静默吞掉错误 |
| 测试中 `time.sleep(...)` 等待异步结果 | 不稳定，使用 mock |
