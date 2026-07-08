"""QMT Bridge — FastAPI server that exposes xtquant/miniQMT as HTTP endpoints.

Runs on the Windows host machine. The Docker container accesses xtquant through
this bridge via ``host.docker.internal:8888``.

Usage::

    python qmt_bridge/server.py --host 127.0.0.1 --port 8888
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_xtdata_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Kill Switch
# ---------------------------------------------------------------------------

KILL_SWITCH_DIR = Path(os.path.expanduser("~/.vibe-trading/kill_switch"))
GLOBAL_HALT = Path(os.path.expanduser("~/.vibe-trading/live/HALT"))


def _is_halted(broker: str = "xtquant") -> bool:
    """Check whether trading is halted for *broker*."""
    if GLOBAL_HALT.exists():
        return True
    halt_file = KILL_SWITCH_DIR / f"{broker}.halt"
    return halt_file.exists()


# ---------------------------------------------------------------------------
# xtquant import guard
# ---------------------------------------------------------------------------


def _import_xtquant():
    """Lazy-import xtquant with a friendly error."""
    try:
        import xtquant  # type: ignore[import-untyped]
        import xtquant.xtdata  # noqa: F401  # type: ignore[import-untyped]
        import xtquant.xttrader  # noqa: F401  # type: ignore[import-untyped]

        return xtquant
    except ModuleNotFoundError:
        raise RuntimeError(
            "xtquant is not installed. It is distributed with QMT/miniQMT "
            "and typically available only on Windows."
        ) from None


def _to_native(value: Any) -> Any:
    """Convert numpy scalar to plain Python type."""
    if value is None:
        return None
    t = type(value).__module__
    if t == "numpy":
        try:
            return value.item()
        except Exception:
            return float(value)
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Safely read an attribute with numpy-safe conversion."""
    try:
        value = getattr(obj, name, default)
    except Exception:
        return default
    return _to_native(value)


def _normalize_symbol(raw: str) -> str:
    """Normalize symbol to SH/SZ suffix format."""
    import re

    s = str(raw or "").strip().upper()
    if not s:
        return s
    if "." in s:
        return s
    if re.match(r"^\d{6}$", s):
        return f"{s}.SH" if s.startswith("6") else f"{s}.SZ"
    return s


def _mask_id(aid: str) -> str:
    """Mask account id for logging."""
    if aid and len(aid) >= 4:
        return f"{aid[:2]}****{aid[-2:]}"
    return str(aid)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def create_app():
    """Create and return the FastAPI app."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(
        title="QMT Bridge",
        description="HTTP bridge for xtquant / miniQMT",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Auth middleware ──
    BEARER_TOKEN = os.getenv("QMT_BRIDGE_TOKEN", "qmt-ql-8f3a2d1e9c")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path == "/health" and request.method == "GET":
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {BEARER_TOKEN}"
        if auth != expected:
            raise HTTPException(status_code=401, detail="Invalid or missing Bearer token")
        return await call_next(request)

    # ── Kill Switch middleware ──
    @app.middleware("http")
    async def kill_switch_middleware(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if request.url.path.startswith("/api/v1/order/"):
            if _is_halted():
                raise HTTPException(
                    status_code=503,
                    detail="Trading halted: kill switch is active",
                )
        return await call_next(request)

    # ── Health ──
    @app.get("/health")
    async def health():
        xt_ok = False
        try:
            _import_xtquant()
            xt_ok = True
        except RuntimeError:
            pass

        return {
            "status": "ok",
            "platform": sys.platform,
            "xtquant_installed": xt_ok,
            "accounts": [],
            "connected": xt_ok,
            "halted": _is_halted(),
        }

    # ── Account snapshot ──
    @app.post("/api/v1/account/snapshot")
    async def account_snapshot(body: dict[str, Any]):
        account_id = str(body.get("account_id", ""))
        account_type = str(body.get("account_type", "STOCK")).upper()
        mini_qmt_path = str(body.get("mini_qmt_path", ""))

        if _is_halted():
            return {"status": "blocked", "decision": "halt", "reason": "kill switch tripped"}

        xt = _import_xtquant()
        session_id = int.from_bytes(os.urandom(2), byteorder="big")
        try:
            trader = xt.xttrader.XtQuantTrader(mini_qmt_path, session_id)
            trader.start()
            connect_result = trader.connect()
            if connect_result != 0:
                return {"status": "error", "error": f"miniQMT connection returned error code {connect_result}", "error_code": "MINIQMT_DISCONNECTED"}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "error_code": "MINIQMT_DISCONNECTED"}

        try:
            acc = xt.StockAccount(account_id, account_type)
            raw = trader.query_stock_asset(acc)
        except Exception as exc:
            return {"status": "error", "error": str(exc), "error_code": "XTQUANT_CRASH"}
        finally:
            try:
                trader.stop()
            except Exception:
                pass

        if isinstance(raw, (list, tuple)) and len(raw) > 0:
            asset_obj = raw[0]
        else:
            asset_obj = raw

        return {
            "status": "ok", "account_id": account_id, "account_type": account_type,
            "total_value": _attr(asset_obj, "total_asset", 0.0),
            "cash": _attr(asset_obj, "cash", 0.0),
            "buying_power": _attr(asset_obj, "cash", 0.0),
            "market_value": _attr(asset_obj, "market_value", 0.0),
            "frozen_cash": _attr(asset_obj, "frozen_cash", 0.0),
        }

    # ── Positions ──
    @app.post("/api/v1/account/positions")
    async def positions(body: dict[str, Any]):
        account_id = str(body.get("account_id", ""))
        account_type = str(body.get("account_type", "STOCK")).upper()
        mini_qmt_path = str(body.get("mini_qmt_path", ""))

        if _is_halted():
            return {"status": "blocked", "decision": "halt", "reason": "kill switch tripped"}

        xt = _import_xtquant()
        session_id = int.from_bytes(os.urandom(2), byteorder="big")
        try:
            trader = xt.xttrader.XtQuantTrader(mini_qmt_path, session_id)
            trader.start()
            if trader.connect() != 0:
                return {"status": "error", "error": "miniQMT connection failed", "error_code": "MINIQMT_DISCONNECTED"}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "error_code": "MINIQMT_DISCONNECTED"}

        try:
            acc = xt.StockAccount(account_id, account_type)
            raw = trader.query_stock_positions(acc)
        except Exception as exc:
            return {"status": "error", "error": str(exc), "error_code": "XTQUANT_CRASH"}
        finally:
            try:
                trader.stop()
            except Exception:
                pass

        positions_list: list[dict[str, Any]] = []
        items = raw if isinstance(raw, (list, tuple)) else []
        for item in items:
            symbol = _attr(item, "stock_code", "")
            qty = _attr(item, "volume", 0)
            if not symbol or qty == 0:
                continue
            positions_list.append({
                "symbol": str(symbol), "qty": qty,
                "avg_cost": _attr(item, "open_price", 0.0),
                "market_value": _attr(item, "market_value", 0.0),
                "current_price": _attr(item, "open_price", 0.0),
                "unrealized_pnl": 0.0,
            })

        return {"status": "ok", "account_id": account_id, "positions": positions_list}

    # ── Orders ──
    @app.post("/api/v1/account/orders")
    async def orders(body: dict[str, Any]):
        account_id = str(body.get("account_id", ""))
        account_type = str(body.get("account_type", "STOCK")).upper()
        mini_qmt_path = str(body.get("mini_qmt_path", ""))
        include_executions = bool(body.get("include_executions", False))

        if _is_halted():
            return {"status": "blocked", "decision": "halt", "reason": "kill switch tripped"}

        xt = _import_xtquant()
        session_id = int.from_bytes(os.urandom(2), byteorder="big")
        try:
            trader = xt.xttrader.XtQuantTrader(mini_qmt_path, session_id)
            trader.start()
            if trader.connect() != 0:
                return {"status": "error", "error": "miniQMT connection failed", "error_code": "MINIQMT_DISCONNECTED"}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "error_code": "MINIQMT_DISCONNECTED"}

        try:
            acc = xt.StockAccount(account_id, account_type)
            raw = trader.query_stock_orders(acc)
        except Exception as exc:
            try:
                trader.stop()
            except Exception:
                pass
            return {"status": "error", "error": str(exc), "error_code": "XTQUANT_CRASH"}

        order_status_map = {48: "pending", 49: "pending", 50: "pending", 53: "partial", 54: "filled", 51: "cancelled", 52: "cancelled", 55: "cancelled", 56: "rejected", 57: "rejected"}

        orders_list: list[dict[str, Any]] = []
        items = raw if isinstance(raw, (list, tuple)) else []
        for item in items:
            order_id = str(_attr(item, "order_id", "")) or str(_attr(item, "order_sysid", ""))
            if not order_id or order_id == "0":
                continue
            side_raw = _attr(item, "order_type", 0)
            side = "buy" if side_raw == 23 else "sell"
            orders_list.append({
                "order_id": order_id, "symbol": str(_attr(item, "stock_code", "")),
                "side": side, "quantity": _attr(item, "order_volume", 0),
                "filled_qty": _attr(item, "traded_volume", 0),
                "limit_price": _attr(item, "price", 0.0),
                "status": order_status_map.get(int(_attr(item, "order_status", 0)), "unknown"),
                "created_at": str(_attr(item, "order_time", "")),
            })

        result: dict[str, Any] = {"status": "ok", "account_id": account_id, "orders": orders_list}

        if include_executions:
            try:
                raw_trades = trader.query_stock_trades(acc)
            except Exception:
                raw_trades = []
            trades = raw_trades if isinstance(raw_trades, (list, tuple)) else []
            executions = []
            for item in trades:
                executions.append({
                    "order_id": str(_attr(item, "order_id", "")),
                    "symbol": str(_attr(item, "stock_code", "")),
                    "side": "buy" if _attr(item, "order_type", 0) == 23 else "sell",
                    "filled_qty": _attr(item, "traded_volume", 0),
                    "price": _attr(item, "traded_price", 0.0),
                    "time": str(_attr(item, "order_time", "")),
                })
            result["executions"] = executions

        try:
            trader.stop()
        except Exception:
            pass

        return result

    # ── Quote ──
    @app.post("/api/v1/market/quote")
    async def quote(body: dict[str, Any]):
        symbol = _normalize_symbol(str(body.get("symbol", "")))
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol is required")

        xt = _import_xtquant()
        xtdata = xt.xtdata

        with _xtdata_lock:
            try:
                tick = xtdata.get_full_tick([symbol])
            except Exception as exc:
                return {"status": "error", "symbol": symbol, "error": str(exc), "error_code": "XTQUANT_CRASH"}

        if not tick or symbol not in tick:
            return {"status": "error", "symbol": symbol, "error": f"No tick data returned for {symbol}", "error_code": "INVALID_SYMBOL"}

        data = tick[symbol]
        def _g(k, d=0.0):
            if isinstance(data, dict):
                return _to_native(data.get(k, d))
            return _to_native(getattr(data, k, d))

        return {
            "status": "ok", "symbol": symbol,
            "last": _g("lastPrice", 0.0),
            "bid": _g("bidPrice", [0.0])[0] if isinstance(data.get("bidPrice", None), list) else _g("bidPrice", 0.0),
            "ask": _g("askPrice", [0.0])[0] if isinstance(data.get("askPrice", None), list) else _g("askPrice", 0.0),
            "volume": _g("volume", 0), "open": _g("open", 0.0), "high": _g("high", 0.0),
            "low": _g("low", 0.0), "pre_close": _g("lastClose", _g("preClose", 0.0)),
            "upper_limit": _g("upStop", _g("upperLimit", 0.0)),
            "lower_limit": _g("downStop", _g("lowerLimit", 0.0)),
        }

    # ── Historical bars ──
    @app.post("/api/v1/market/bars")
    async def bars(body: dict[str, Any]):
        symbol = _normalize_symbol(str(body.get("symbol", "")))
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol is required")

        period = str(body.get("period", "1d")).strip()
        limit = int(body.get("limit", 90))

        period_map = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m", "4h": "240m", "1d": "1d", "1w": "1w", "1M": "1M"}
        xt_period = period_map.get(period, "1d")

        xt = _import_xtquant()
        xtdata = xt.xtdata

        with _xtdata_lock:
            try:
                xtdata.download_history_data(symbol, period=xt_period)
            except Exception as exc:
                return {"status": "error", "symbol": symbol, "error": str(exc), "error_code": "XTQUANT_CRASH"}

            try:
                raw = xtdata.get_local_data(stock_list=[symbol], period=xt_period, field_list=["open", "high", "low", "close", "volume", "amount"], count=int(limit))
            except Exception as exc:
                return {"status": "error", "symbol": symbol, "error": str(exc), "error_code": "XTQUANT_CRASH"}

        if raw is None or symbol not in raw:
            return {"status": "ok", "symbol": symbol, "period": period, "bars": []}

        df = raw[symbol]
        bars_list: list[dict[str, Any]] = []

        to_dict = getattr(df, "to_dict", None)
        if callable(to_dict) and hasattr(df, "columns"):
            try:
                records = df.tail(int(limit)).reset_index().to_dict("records")
                for row in records:
                    bars_list.append({"t": str(_to_native(row.get("index") or row.get("time") or row.get("datetime") or "")), "o": _to_native(row.get("open", 0.0)), "h": _to_native(row.get("high", 0.0)), "l": _to_native(row.get("low", 0.0)), "c": _to_native(row.get("close", 0.0)), "v": _to_native(row.get("volume", 0))})
            except Exception:
                pass
        elif isinstance(df, dict):
            times = df.get("time", [])
            opens = df.get("open", [])
            highs = df.get("high", [])
            lows = df.get("low", [])
            closes = df.get("close", [])
            volumes = df.get("volume", [])
            count = min(len(times), int(limit))
            for i in range(max(0, len(times) - count), len(times)):
                bars_list.append({"t": str(_to_native(times[i] if i < len(times) else "")), "o": _to_native(opens[i] if i < len(opens) else 0.0), "h": _to_native(highs[i] if i < len(highs) else 0.0), "l": _to_native(lows[i] if i < len(lows) else 0.0), "c": _to_native(closes[i] if i < len(closes) else 0.0), "v": _to_native(volumes[i] if i < len(volumes) else 0)})

        return {"status": "ok", "symbol": symbol, "period": period, "bars": bars_list}

    # ── Place order (future) ──
    @app.post("/api/v1/order/place")
    async def place_order(body: dict[str, Any]):
        if _is_halted():
            return {"status": "blocked", "decision": "halt", "reason": "kill switch tripped"}
        return {"status": "error", "error": "Order placement through QMT Bridge is not yet implemented.", "error_code": "NOT_IMPLEMENTED"}

    # ── Cancel order (future) ──
    @app.post("/api/v1/order/cancel")
    async def cancel_order(body: dict[str, Any]):
        if _is_halted():
            return {"status": "blocked", "decision": "halt", "reason": "kill switch tripped"}
        return {"status": "error", "error": "Order cancellation through QMT Bridge is not yet implemented.", "error_code": "NOT_IMPLEMENTED"}

    return app


def main():
    """Run the QMT Bridge server."""
    import argparse

    parser = argparse.ArgumentParser(description="QMT Bridge — xtquant HTTP bridge")
    parser.add_argument("--host", default="127.0.0.1", help="Listen address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8888, help="Listen port (default: 8888)")
    args = parser.parse_args()

    import uvicorn

    app = create_app()
    logger.info("Starting QMT Bridge on %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
