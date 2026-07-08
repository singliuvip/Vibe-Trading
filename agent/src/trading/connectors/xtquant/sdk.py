"""XTQuant / miniQMT connector — native xtquant SDK (Windows only).

Architecture
------------
Vibe-Trading imports ``xtquant`` directly on Windows hosts.  miniQMT runs as a
local process on the same machine; the SDK communicates via IPC (no HTTP bridge).

    ┌──────────────────────────────────────────┐
    │  Vibe-Trading (Windows 宿主机)            │
    │  sdk.py ── import xtquant ──► miniQMT    │
    └──────────────────────────────────────────┘

- Paper mode (``profile == "paper"`` or ``"paper-trade"``) is handled **locally**
  via PaperEngine — no SDK call for paper trades.
- Live mode (``profile == "live-readonly"`` or ``"live-trade"``) routes through
  ``XtQuantTrader``, and the resulting live orders pass through the Vibe-Trading
  mandate gate + kill switch + audit ledger (enforced by the caller
  ``service.py``).

Environment variables
--------------------
``XTQUANT_MINI_QMT_PATH``     miniQMT installation path (default ``""``).
``XTQUANT_ACCOUNT_ID``        Account ID (empty = auto-discover).
``XTQUANT_ACCOUNT_TYPE``      ``STOCK`` (default), ``FUTURES``, etc.
``XTQUANT_PROFILE``           ``paper`` / ``paper-trade`` / ``live-readonly`` / ``live-trade``.
``XTQUANT_TIMEOUT``           Connection timeout in seconds (default ``10.0``).
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import platform
import re
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

from src.config.paths import get_runtime_root

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class XtQuantDependencyError(RuntimeError):
    """Raised when the optional ``xtquant`` package is not installed."""


class XtQuantPlatformError(RuntimeError):
    """Raised when running on a non-Windows platform (xtquant requires Windows)."""


class XtQuantConnectionError(RuntimeError):
    """Raised when the connector cannot connect to the local miniQMT process."""


class XtQuantConfigError(RuntimeError):
    """Raised when the connector configuration is missing or invalid."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_FILENAME = "xtquant.json"


@dataclass(frozen=True)
class XtQuantConfig:
    """XTQuant connector configuration — native SDK transport.

    All values can be overridden via environment variables (see module docstring).

    Args:
        mini_qmt_path: miniQMT installation path (empty = default).
        account_id: Account ID (empty = auto-discover).
        account_type: Account type (``STOCK``, ``FUTURES``, ...).
        profile: ``paper`` / ``paper-trade`` / ``live-readonly`` / ``live-trade``.
        session_id: Session ID (0 = auto-generate).
        timeout: Connection timeout in seconds.
        readonly: Whether the connector is read-only.
    """

    mini_qmt_path: str = ""
    account_id: str = ""
    account_type: str = "STOCK"
    profile: str = "paper"
    session_id: int = 0
    timeout: float = 10.0
    readonly: bool = True

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None = None) -> "XtQuantConfig":
        """Construct config from a dict (convenience wrapper around build_config)."""
        return build_config(data)

    @property
    def is_live(self) -> bool:
        """True if this is a live (non-paper) profile."""
        return self.profile in ("live-readonly", "live-trade", "live")

    @property
    def environment(self) -> str:
        """Return ``"live"`` or ``"paper"`` based on profile."""
        return "live" if self.is_live else "paper"

    def with_overrides(self, **overrides: Any) -> "XtQuantConfig":
        """Return a new config with the given fields overridden."""
        return replace(self, **overrides)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _env_or_default(key: str, default: str) -> str:
    return os.getenv(f"XTQUANT_{key}", default)


def build_config(data: dict[str, Any] | None = None, overrides: dict[str, Any] | None = None) -> XtQuantConfig:
    """Build an ``XtQuantConfig`` from a JSON mapping + optional overrides.

    Args:
        data: Raw config dict (typically from ``~/.vibe-trading/xtquant.json``).
        overrides: Runtime overrides (e.g. CLI ``--profile live``).

    Returns:
        An ``XtQuantConfig`` instance.
    """
    payload = dict(data or {})
    if overrides:
        payload.update(overrides)

    profile = str(payload.get("profile") or _env_or_default("PROFILE", "paper")).strip().lower()
    _valid_profiles = {"paper", "paper-trade", "live-readonly", "live-trade", "live"}
    if profile not in _valid_profiles:
        raise XtQuantConfigError(
            f"Invalid profile {profile!r}. Must be one of: {', '.join(sorted(_valid_profiles))}"
        )

    return XtQuantConfig(
        mini_qmt_path=str(
            payload.get("mini_qmt_path") or _env_or_default("MINI_QMT_PATH", "")
        ).strip(),
        account_id=str(payload.get("account_id") or _env_or_default("ACCOUNT_ID", "")).strip(),
        account_type=str(payload.get("account_type") or _env_or_default("ACCOUNT_TYPE", "STOCK")).strip(),
        profile=profile,
        session_id=int(payload.get("session_id") or 0),
        timeout=float(payload.get("timeout") or _env_or_default("TIMEOUT", "10.0")),
        readonly=bool(payload.get("readonly", True)),
    )


def load_config() -> XtQuantConfig:
    """Load config from ``~/.vibe-trading/xtquant.json``, falling back to env vars."""
    path = get_runtime_root() / CONFIG_FILENAME
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return build_config(data)
        except OSError as exc:
            logger.warning("Failed to load %s: %s", path, exc)
        except json.JSONDecodeError as exc:
            raise XtQuantConfigError(
                f"Invalid JSON in {path}: {exc}"
            ) from exc
    return build_config()


# ---------------------------------------------------------------------------
# SDK lifecycle (singleton XtQuantTrader)
# ---------------------------------------------------------------------------

_trader_lock = threading.Lock()
_trader_instance: Any = None
_trader_config_hash: int = -1
_xtdata_instance: Any = None

# ── xtquant module import ──


def _import_xtquant() -> ModuleType:
    """Lazy-import the ``xtquant`` package with a friendly error on failure."""
    try:
        import xtquant  # type: ignore[import-untyped]
        return xtquant
    except ModuleNotFoundError as exc:
        raise XtQuantDependencyError(
            "xtquant is not installed. Install miniQMT on a Windows host first."
        ) from exc


def _check_platform() -> None:
    """Raise if the current platform is not Windows."""
    if platform.system() != "Windows":
        raise XtQuantPlatformError(
            "xtquant / miniQMT only runs on Windows. "
            f"Current platform: {platform.system()}."
        )


# ── Connection management ──


def _trader_config_key(cfg: XtQuantConfig) -> int:
    """Hash the connection-relevant config fields to detect changes."""
    return hash((cfg.mini_qmt_path, cfg.account_id, cfg.account_type, cfg.session_id))


def _ensure_connected(cfg: XtQuantConfig) -> tuple[Any, Any]:
    """Return ``(XtQuantTrader, xtdata_module)``, creating or reusing the singleton.

    Thread-safe: guarded by ``_trader_lock``.

    Raises:
        XtQuantDependencyError: If ``xtquant`` is not installed.
        XtQuantPlatformError: If not on Windows.
        XtQuantConnectionError: If connection to miniQMT fails.
    """
    global _trader_instance, _trader_config_hash, _xtdata_instance  # noqa: PLW0603

    _check_platform()
    _import_xtquant()

    with _trader_lock:
        key = _trader_config_key(cfg)
        if _trader_instance is not None and _trader_config_hash == key:
            return _trader_instance, _xtdata_instance

        # Teardown old connection if config changed
        if _trader_instance is not None:
            _disconnect()

        from xtquant.xttrader import XtQuantTrader  # type: ignore[import-untyped]
        import xtquant.xtdata as xtdata  # type: ignore[import-untyped]

        path = cfg.mini_qmt_path or ""
        session = cfg.session_id if cfg.session_id > 0 else int(uuid.uuid4().int % (2**31))

        trader = XtQuantTrader(path, session)
        trader.start()

        result = trader.connect()
        if result != 0:
            raise XtQuantConnectionError(
                f"miniQMT connection failed (code={result}). "
                f"mini_qmt_path={path!r}, session_id={session}. "
                "Ensure miniQMT is running and the path is correct."
            )

        _trader_instance = trader
        _trader_config_hash = key
        _xtdata_instance = xtdata

        logger.info(
            "miniQMT connected: path=%r session=%s account=%s",
            path, session, _mask_id(cfg.account_id),
        )

        return trader, xtdata


def _disconnect() -> None:
    """Tear down the XtQuantTrader singleton."""
    global _trader_instance, _trader_config_hash, _xtdata_instance  # noqa: PLW0603
    with _trader_lock:
        stop_heartbeat()
        if _trader_instance is not None:
            try:
                _trader_instance.stop()
            except Exception:
                pass
            _trader_instance = None
            _trader_config_hash = -1
            _xtdata_instance = None


def _with_reconnect(cfg: XtQuantConfig, fn, *args: Any, **kwargs: Any) -> Any:
    """Call *fn* with auto-reconnect on connection loss."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.debug("miniQMT call failed, attempting reconnect", exc_info=True)
        _disconnect()
        return fn(*args, **kwargs)


# ── Account helpers ──


def _to_stock_account(cfg: XtQuantConfig) -> Any:
    """Create a ``StockAccount`` from resolved account id and type."""
    from xtquant.xttype import StockAccount  # type: ignore[import-untyped]

    acc_id = _resolve_account_id(cfg)
    return StockAccount(acc_id, cfg.account_type)


def _resolve_account_id(cfg: XtQuantConfig) -> str:
    """Resolve account ID from config, falling back to auto-discover."""
    if cfg.account_id:
        return cfg.account_id
    discovered = _discover_account_ids(cfg)
    if discovered:
        return discovered[0]
    raise XtQuantConfigError(
        "No account_id configured and no accounts discovered. "
        "Set XTQUANT_ACCOUNT_ID or configure an account in xtquant.json."
    )


def _discover_account_ids(cfg: XtQuantConfig) -> list[str]:
    """Auto-discover available account IDs from miniQMT.

    xtquant does not expose a direct "list accounts" API; we query the connected
    asset snapshot and extract the account_id from the response.
    """
    try:
        trader, _ = _ensure_connected(cfg)
        acc = _to_stock_account(cfg)
        raw = trader.query_stock_asset(acc)
        acc_id = _attr(raw, "account_id", "") or _attr(raw, "accountID", "")
        return [str(acc_id)] if acc_id else []
    except Exception as exc:
        logger.debug("account discovery failed: %s", exc)
        return []


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Safely read an attribute from an xtquant data object."""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _mask_id(account_id: str) -> str:
    """Mask the middle digits of an account ID for safe logging."""
    if not account_id:
        return "***"
    s = str(account_id)
    if len(s) <= 4:
        return "***"
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


# Register disconnect on process exit
atexit.register(_disconnect)


# ---------------------------------------------------------------------------
# Public API — Read operations
# ---------------------------------------------------------------------------


def check_status(config: XtQuantConfig | None = None) -> dict[str, Any]:
    """Check miniQMT connectivity and SDK readiness.

    Returns a JSON-serializable health report.  Degrades cleanly on non-Windows
    platforms or when ``xtquant`` is not installed.
    """
    cfg = config or load_config()

    report: dict[str, Any] = {
        "status": "ok",
        "profile": cfg.profile,
        "platform": platform.system(),
        "connected": False,
    }

    try:
        _check_platform()
    except XtQuantPlatformError as exc:
        report["status"] = "error"
        report["error"] = str(exc)
        return report

    try:
        _import_xtquant()
        report["sdk"] = {"package": "xtquant", "installed": True}
    except XtQuantDependencyError as exc:
        report["status"] = "error"
        report["error"] = str(exc)
        report["sdk"] = {"package": "xtquant", "installed": False}
        return report

    # Paper profiles don't need a live miniQMT connection.
    if cfg.profile in ("paper", "paper-trade"):
        report["connected"] = False
        report["note"] = "paper profile — no miniQMT connection required"
        return report

    # Empty mini_qmt_path cannot connect.
    if not cfg.mini_qmt_path:
        report["status"] = "error"
        report["error"] = "mini_qmt_path is not configured"
        return report

    try:
        _ensure_connected(cfg)
        report["connected"] = True
        report["account_id"] = _mask_id(_resolve_account_id(cfg))
        report["account_id_raw"] = _resolve_account_id(cfg)
        report["account_type"] = cfg.account_type
        report["heartbeat"] = get_heartbeat_status()
    except XtQuantConnectionError as exc:
        report["status"] = "error"
        report["error"] = str(exc)

    return report


def get_account_snapshot(config: XtQuantConfig | None = None) -> dict[str, Any]:
    """Fetch account funds/assets.

    Paper profiles route to PaperEngine's simulated ledger (no SDK call).
    """
    cfg = config or load_config()
    if cfg.profile in ("paper", "paper-trade"):
        from src.trading.connectors.xtquant.paper_engine import get_paper_engine
        return get_paper_engine().get_account_snapshot()

    trader, _ = _ensure_connected(cfg)
    acc = _to_stock_account(cfg)
    raw = _with_reconnect(cfg, trader.query_stock_asset, acc)

    return {
        "status": "ok",
        "account_id": _mask_id(cfg.account_id or _resolve_account_id(cfg)),
        "account_type": cfg.account_type,
        "total_value": _attr(raw, "total_asset", 0.0),
        "cash": _attr(raw, "cash", 0.0),
        "market_value": _attr(raw, "market_value", 0.0),
        "buying_power": _attr(raw, "buying_power", _attr(raw, "cash", 0.0)),
        "frozen_cash": _attr(raw, "frozen_cash", 0.0),
    }


def get_positions(config: XtQuantConfig | None = None) -> dict[str, Any]:
    """Fetch current positions.

    Paper profiles route to PaperEngine (no SDK call).
    """
    cfg = config or load_config()
    if cfg.profile in ("paper", "paper-trade"):
        from src.trading.connectors.xtquant.paper_engine import get_paper_engine
        return {"status": "ok", "positions": get_paper_engine().get_positions()}

    trader, _ = _ensure_connected(cfg)
    acc = _to_stock_account(cfg)
    raw = _with_reconnect(cfg, trader.query_stock_positions, acc)

    positions: list[dict[str, Any]] = []
    if raw is not None and hasattr(raw, "__iter__") and not isinstance(raw, (str, bytes)):
        for pos in raw:
            positions.append({
                "symbol": _attr(pos, "stock_code", ""),
                "quantity": _attr(pos, "volume", 0),
                "avg_cost": _attr(pos, "avg_price", 0.0),
                "market_value": _attr(pos, "market_value", 0.0),
            })

    return {"status": "ok", "positions": positions}


def get_open_orders(
    config: XtQuantConfig | None = None,
    *,
    include_executions: bool = False,
) -> dict[str, Any]:
    """Fetch open orders.

    Paper profiles route to PaperEngine (no SDK call).
    """
    cfg = config or load_config()
    if cfg.profile in ("paper", "paper-trade"):
        from src.trading.connectors.xtquant.paper_engine import get_paper_engine
        return {"status": "ok", "orders": get_paper_engine().get_open_orders()}

    trader, _ = _ensure_connected(cfg)
    acc = _to_stock_account(cfg)
    raw = _with_reconnect(cfg, trader.query_stock_orders, acc)

    # xtquant constants for direction mapping
    _BUY = 1  # STOCK_BUY

    orders: list[dict[str, Any]] = []
    if raw is not None and hasattr(raw, "__iter__") and not isinstance(raw, (str, bytes)):
        for order in raw:
            orders.append({
                "order_id": str(_attr(order, "order_id", "")),
                "symbol": _attr(order, "stock_code", ""),
                "side": "buy" if _attr(order, "direction", 0) == _BUY else "sell",
                "quantity": _attr(order, "order_volume", 0),
                "filled_qty": _attr(order, "traded_volume", 0),
                "status": _attr(order, "order_status", "unknown"),
                "price": _attr(order, "price", 0.0),
                "type": "limit" if _attr(order, "price_type", 0) == 1 else "market",
            })

    result: dict[str, Any] = {"status": "ok", "orders": orders}
    if include_executions:
        # xtquant does not expose a direct "executions" endpoint; return empty
        result["executions"] = []
    return result


def get_quote(symbol: str, *, config: XtQuantConfig | None = None, **_: Any) -> dict[str, Any]:
    """Fetch a real-time quote via ``xtdata.get_full_tick()``."""
    cfg = config or load_config()
    if cfg.profile in ("paper", "paper-trade"):
        return {
            "status": "error",
            "symbol": symbol,
            "error": "get_quote requires a live miniQMT connection. "
                     "Use xtquant-live-readonly or xtquant-live-trade profile.",
        }
    _, xtdata = _ensure_connected(cfg)
    tick = _with_reconnect(cfg, xtdata.get_full_tick, [symbol])

    if not tick or symbol not in tick:
        return {"status": "error", "symbol": symbol, "error": "No data returned"}

    data = tick[symbol]
    bid_prices = _attr(data, "bidPrice", [])
    ask_prices = _attr(data, "askPrice", [])

    return {
        "status": "ok",
        "symbol": symbol,
        "quote": {
            "last": _attr(data, "lastPrice", 0.0),
            "bid": bid_prices[0] if bid_prices else 0.0,
            "ask": ask_prices[0] if ask_prices else 0.0,
            "volume": _attr(data, "volume", 0),
            "open": _attr(data, "open", 0.0),
            "high": _attr(data, "high", 0.0),
            "low": _attr(data, "low", 0.0),
            "prev_close": _attr(data, "lastClose", 0.0),
        },
    }


def get_historical_bars(
    symbol: str,
    config: XtQuantConfig | None = None,
    *,
    period: str = "1d",
    limit: int = 90,
) -> dict[str, Any]:
    """Fetch historical OHLCV bars via ``xtdata.download_history_data()`` +
    ``xtdata.get_local_data()``.
    """
    cfg = config or load_config()
    if cfg.profile in ("paper", "paper-trade"):
        return {
            "status": "error",
            "symbol": symbol,
            "error": "get_historical_bars requires a live miniQMT connection. "
                     "Use xtquant-live-readonly or xtquant-live-trade profile.",
        }
    _, xtdata = _ensure_connected(cfg)

    from datetime import datetime, timedelta

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=max(limit * 2, 90))).strftime("%Y%m%d")

    _with_reconnect(cfg, xtdata.download_history_data, symbol, period, start, end)
    data = _with_reconnect(
        cfg,
        xtdata.get_local_data,
        fields=["open", "high", "low", "close", "volume", "amount"],
        stock_codes=[symbol],
        period=period,
        start_time=start,
        end_time=end,
    )

    bars: list[dict[str, Any]] = []
    if data is not None and symbol in data and data[symbol] is not None:
        df = data[symbol]
        to_records = getattr(df, "to_dict", None)
        if callable(to_records) and hasattr(df, "columns"):
            try:
                records = to_records("records")
            except Exception:
                records = []
        elif hasattr(df, "__iter__") and not isinstance(df, (str, bytes)):
            records = list(df)
        else:
            records = []

        for row in records:
            if isinstance(row, dict):
                bars.append({
                    "time": str(row.get("time", row.get("date", ""))),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": int(row.get("volume", 0)),
                    "amount": float(row.get("amount", 0)),
                })

    return {"status": "ok", "symbol": symbol, "period": period, "bars": bars}


# ---------------------------------------------------------------------------
# Public API — Write operations
# ---------------------------------------------------------------------------


def place_order(
    config: XtQuantConfig,
    symbol: str,
    side: str,
    quantity: float | None = None,
    notional: float | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    time_in_force: str = "day",
    **kwargs: Any,
) -> dict[str, Any]:
    """Place an order.

    Paper profiles → PaperEngine local simulation (no SDK call).
    Live profiles → XtQuantTrader (caller ``service.py`` handles mandate gate +
    kill switch + audit).
    """
    cfg = config

    # ── Paper: local simulation ──
    if cfg.profile in ("paper", "paper-trade"):
        from src.trading.connectors.xtquant.paper_engine import get_paper_engine
        result = get_paper_engine().place_order(
            symbol=str(symbol or "").strip().upper(),
            side=str(side or "").strip().lower(),
            quantity=int(quantity) if quantity is not None else 0,
            order_type=str(order_type or "market").strip().lower(),
            limit_price=float(limit_price) if limit_price is not None else None,
            notional=notional,
        )
        logger.info("paper place_order: %s %s → %s", side, symbol, result.get("status"))
        return result

    # ── Live: XtQuantTrader ──
    from xtquant import xtconstant  # type: ignore[import-untyped]

    trader, _ = _ensure_connected(cfg)
    acc = _to_stock_account(cfg)

    code = str(symbol or "").strip().upper()
    side_key = str(side or "").strip().lower()
    direction = xtconstant.STOCK_BUY if side_key == "buy" else xtconstant.STOCK_SELL
    price_type = xtconstant.LATEST_PRICE if order_type == "market" else xtconstant.FIX_PRICE
    price = float(limit_price or 0)
    qty = int(quantity or 0)

    order_id = trader.order_stock(
        acc,
        code,
        xtconstant.STOCK,
        price_type,
        direction,
        xtconstant.ORDER_TYPE_NORMAL,
        qty,
        price,
        xtconstant.QUOTE_TYPE_LIMIT,
        order_remark="vibe-trading",
    )

    return {
        "status": "ok",
        "order_id": str(order_id),
        "symbol": code,
        "side": side_key,
        "profile": cfg.profile,
        "order_type": order_type,
        "quantity": qty,
        "limit_price": price if order_type == "limit" else None,
        "time_in_force": time_in_force,
    }


def cancel_order(
    config: XtQuantConfig,
    order_id: str,
    symbol: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Cancel an order.

    Paper profiles → PaperEngine local simulation (no SDK call).
    Live profiles → XtQuantTrader.
    """
    cfg = config

    if cfg.profile in ("paper", "paper-trade"):
        from src.trading.connectors.xtquant.paper_engine import get_paper_engine
        result = get_paper_engine().cancel_order(str(order_id))
        logger.info("paper cancel_order: %s → %s", order_id, result.get("status"))
        return result

    trader, _ = _ensure_connected(cfg)
    acc = _to_stock_account(cfg)

    try:
        oid = int(order_id)
    except (TypeError, ValueError):
        oid = 0

    trader.cancel_order(acc, oid, "")

    return {
        "status": "ok",
        "order_id": str(order_id),
        "profile": cfg.profile,
    }


# ---------------------------------------------------------------------------
# Today's trades (for T+1 guard)
# ---------------------------------------------------------------------------


def get_today_trades(config: XtQuantConfig | None = None) -> dict[str, Any]:
    """Fetch today's trade executions from xtquant.

    Uses ``XtQuantTrader.get_order_stock_trades()`` to retrieve the list of
    filled trades for the current trading day.  Paper profiles return an empty
    list (PaperEngine does not track intraday trades by default).

    This is used by the A-stock T+1 guard to determine whether a symbol was
    bought today.

    Returns:
        Dict with ``status``, ``trades`` (list of trade dicts with
        ``symbol``, ``side``, ``time`` fields).
    """
    cfg = config or load_config()
    if cfg.profile in ("paper", "paper-trade"):
        return {"status": "ok", "trades": []}

    try:
        trader, _ = _ensure_connected(cfg)
        acc = _to_stock_account(cfg)
        raw = _with_reconnect(cfg, trader.get_order_stock_trades, acc)
    except Exception as exc:
        logger.warning("get_today_trades failed: %s", exc)
        # Fail-closed: return empty list, caller will log a warning
        return {"status": "error", "error": str(exc), "trades": []}

    trades: list[dict[str, Any]] = []
    if raw is not None and hasattr(raw, "__iter__") and not isinstance(raw, (str, bytes)):
        for t in raw:
            trades.append({
                "symbol": _attr(t, "stock_code", ""),
                "side": "buy" if _attr(t, "direction", 0) == 1 else "sell",
                "time": str(_attr(t, "trade_time", _attr(t, "time", ""))),
            })

    return {"status": "ok", "trades": trades}


# ---------------------------------------------------------------------------
# Heartbeat / connection probing
# ---------------------------------------------------------------------------

import time as _time_module

_heartbeat_thread: threading.Thread | None = None
_heartbeat_stop = threading.Event()
_last_heartbeat_result: dict[str, Any] = {}
_last_heartbeat_time: float = 0.0


def probe_connection(config: XtQuantConfig | None = None) -> dict[str, Any]:
    """Lightweight connection probe for the heartbeat system.

    Returns a dict with ``status`` (``"ok"`` | ``"error"``) and optional
    ``error`` message.  Never raises.

    This is called by ``api_server.py``'s ``_sdk_heartbeat_probe()``.
    """
    cfg = config or load_config()
    # Paper profiles don't need a live miniQMT connection.
    if cfg.profile in ("paper", "paper-trade"):
        return {"status": "ok", "connected": False, "note": "paper profile — no probe needed"}
    try:
        _check_platform()
        _import_xtquant()
        trader, _ = _ensure_connected(cfg)
        # Lightweight operation to verify the connection is alive
        acc = _to_stock_account(cfg)
        result = trader.query_stock_asset(acc)
        if result is not None:
            return {"status": "ok", "connected": True}
        return {"status": "error", "error": "query_stock_asset returned None", "connected": False}
    except XtQuantPlatformError:
        return {"status": "ok", "connected": False, "note": "non-Windows platform"}
    except XtQuantDependencyError:
        return {"status": "ok", "connected": False, "note": "xtquant not installed"}
    except Exception as exc:
        logger.warning("xtquant probe_connection failed: %s", exc)
        return {"status": "error", "error": str(exc), "connected": False}


def start_heartbeat(
    config: XtQuantConfig,
    interval: float = 30.0,
    max_failures: int = 3,
) -> None:
    """Start a daemon heartbeat thread that periodically probes the connection.

    On *max_failures* consecutive failures the thread attempts an automatic
    reconnect (``_disconnect()`` + ``_ensure_connected()``).

    Safe to call multiple times (calls after the first are no-ops).
    """
    global _heartbeat_thread, _last_heartbeat_result, _last_heartbeat_time  # noqa: PLW0603

    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        return  # already running

    _heartbeat_stop.clear()
    failure_count = 0

    def _loop() -> None:
        nonlocal failure_count
        while not _heartbeat_stop.is_set():
            try:
                result = probe_connection(config)
                global _last_heartbeat_result, _last_heartbeat_time  # noqa: PLW0602
                _last_heartbeat_result = result
                _last_heartbeat_time = _time_module.time()

                if result.get("status") == "ok":
                    failure_count = 0
                else:
                    failure_count += 1
                    logger.debug("xtquant heartbeat failure %d/%d", failure_count, max_failures)
            except Exception as exc:
                failure_count += 1
                logger.debug("xtquant heartbeat exception %d/%d: %s", failure_count, max_failures, exc)

            if failure_count >= max_failures:
                logger.warning("xtquant heartbeat: %d consecutive failures, attempting reconnect", max_failures)
                try:
                    _disconnect()
                    _ensure_connected(config)
                    failure_count = 0
                except Exception as exc:
                    logger.error("xtquant heartbeat reconnect failed: %s", exc)

            _heartbeat_stop.wait(interval)

    _heartbeat_thread = threading.Thread(target=_loop, daemon=True, name="xtquant-heartbeat")
    _heartbeat_thread.start()
    logger.info("xtquant heartbeat started (interval=%.1fs, max_failures=%d)", interval, max_failures)


def stop_heartbeat() -> None:
    """Stop the heartbeat thread if running."""
    global _heartbeat_thread  # noqa: PLW0603
    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        _heartbeat_stop.set()
        _heartbeat_thread.join(timeout=5.0)
        _heartbeat_thread = None
        logger.info("xtquant heartbeat stopped")


def get_heartbeat_status() -> dict[str, Any]:
    """Return the last heartbeat result and time.

    Safe to call before ``start_heartbeat()`` — returns a default "not started"
    envelope.
    """
    if _last_heartbeat_time == 0.0:
        return {"status": "not_started", "connected": False}
    elapsed = _time_module.time() - _last_heartbeat_time
    return {
        **(_last_heartbeat_result or {}),
        "last_check_ago_sec": round(elapsed, 1),
    }
