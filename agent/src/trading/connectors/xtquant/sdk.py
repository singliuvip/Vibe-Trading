"""Read-only XTQuant / miniQMT connector via the ``xtquant`` Python SDK.

This module wraps XTQuant's ``XtQuantTrader`` / ``xtdata`` for the seven read
operations the trading layer exposes (account / positions / orders / quote /
history).  It holds no order-placement method and never calls ``order_stock`` or
``passorder`` — writes are introduced in a later layer behind the paper guard
and, for live, the mandate gate.

Architecture is a LOCAL miniQMT process on Windows (``xtquant`` is
Windows-only).  miniQMT runs on the operator's machine, holds the QMT login,
and the SDK speaks to it over local IPC.  Vibe-Trading never sees QMT
credentials.

Key engineering decisions (informed by wendao qmt_bridge):
* ``threading.Lock()`` wraps every ``xtdata.*`` call to prevent BSON concurrency
  crashes inside the XTQuant C extension.
* ``session_id`` is generated from ``os.urandom(2)`` rather than a hash of the
  miniQMT path, avoiding collisions across processes.
* ``account_id`` is auto-discovered by scanning the ``userdata_mini`` directory
  for numeric subdirectory names.
* All account identifiers in logs are masked (``{id[:2]}****{id[-2:]}``).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

from src.config.paths import get_runtime_root

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# S1: 配置模块
# ---------------------------------------------------------------------------

CONFIG_FILENAME = "xtquant.json"

#: Profiles this connector understands and their default account environment.
PROFILE_ENVIRONMENTS = {
    "paper": "paper",
    "live-readonly": "live",
    "live": "live",
}


class XtQuantDependencyError(RuntimeError):
    """Raised when the optional ``xtquant`` package is not installed."""


class XtQuantConfigError(RuntimeError):
    """Raised when the connector configuration is missing or invalid."""


class XtQuantConnectionError(RuntimeError):
    """Raised when the connector cannot establish a connection to miniQMT."""


class XtQuantPlatformError(RuntimeError):
    """Raised when running on a non-Windows platform (xtquant is Windows-only)."""


@dataclass(frozen=True)
class XtQuantConfig:
    """XTQuant connector connection settings.

    Args:
        mini_qmt_path: Absolute path to the miniQMT installation directory.
        account_id: Account id; empty string means auto-discover from the
            ``userdata_mini`` directory.
        account_type: Account type, default ``STOCK``.
        profile: ``paper``, ``live-readonly`` or ``live``.
        session_id: Session identifier; ``0`` means auto-generate from
            ``os.urandom(2)``.
        timeout: Connection timeout in seconds.
        readonly: Always true for this layer; order methods are not exposed.
    """

    mini_qmt_path: str = ""
    account_id: str = ""
    account_type: str = "STOCK"
    profile: str = "paper"
    session_id: int = 0
    timeout: float = 10.0
    readonly: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None) -> "XtQuantConfig":
        """Build a config from a JSON-like mapping, normalizing the profile.

        Args:
            data: Mapping with any subset of config fields.

        Returns:
            A normalized :class:`XtQuantConfig`.

        Raises:
            XtQuantConfigError: If the profile is not a recognized value.
        """
        payload = dict(data or {})
        profile = str(payload.get("profile") or "paper").strip().lower()
        if profile not in PROFILE_ENVIRONMENTS:
            raise XtQuantConfigError(
                "profile must be 'paper', 'live-readonly' or 'live'"
            )
        return cls(
            mini_qmt_path=str(payload.get("mini_qmt_path") or "").strip(),
            account_id=str(payload.get("account_id") or "").strip(),
            account_type=str(payload.get("account_type") or "STOCK").strip().upper(),
            profile=profile,
            session_id=int(payload.get("session_id") or 0),
            timeout=float(payload.get("timeout") or 10.0),
            readonly=bool(payload.get("readonly", True)),
        )

    @property
    def environment(self) -> str:
        """Return ``paper`` or ``live`` for this profile."""
        return PROFILE_ENVIRONMENTS.get(self.profile, "paper")

    @property
    def is_live(self) -> bool:
        """Return whether this is a live (non-paper) profile."""
        return self.environment == "live"

    def with_overrides(
        self,
        *,
        mini_qmt_path: str | None = None,
        account_id: str | None = None,
        account_type: str | None = None,
        profile: str | None = None,
        session_id: int | None = None,
        timeout: float | None = None,
    ) -> "XtQuantConfig":
        """Return a copy with CLI/tool overrides applied."""
        payload = asdict(self)
        if mini_qmt_path is not None:
            payload["mini_qmt_path"] = mini_qmt_path
        if account_id is not None:
            payload["account_id"] = account_id
        if account_type is not None:
            payload["account_type"] = account_type
        if profile is not None:
            payload["profile"] = profile
        if session_id is not None:
            payload["session_id"] = session_id
        if timeout is not None:
            payload["timeout"] = timeout
        return XtQuantConfig.from_mapping(payload)


_OVERRIDE_KEYS = (
    "mini_qmt_path",
    "account_id",
    "account_type",
    "profile",
    "session_id",
    "timeout",
)


def build_config(
    profile_config: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> XtQuantConfig:
    """Resolve the effective config: saved file ← profile defaults ← CLI overrides.

    miniQMT path and account settings come from the saved
    ``~/.vibe-trading/xtquant.json``; the selected connector profile supplies the
    ``profile`` intent; CLI/tool overrides win last.

    Args:
        profile_config: The connector profile's ``config`` dict.
        overrides: Per-call overrides (only known config keys are applied).

    Returns:
        A normalized :class:`XtQuantConfig`.
    """
    base = asdict(load_config())
    for key, value in dict(profile_config or {}).items():
        if value is not None:
            base[key] = value
    cfg = XtQuantConfig.from_mapping(base)
    clean = {
        k: v
        for k, v in dict(overrides or {}).items()
        if k in _OVERRIDE_KEYS and v not in (None, "")
    }
    return cfg.with_overrides(**clean) if clean else cfg


def config_path() -> Path:
    """Return the user-level XTQuant config path."""
    return get_runtime_root() / CONFIG_FILENAME


def _read_config_file(path: Path) -> str:
    """Read a config file trying UTF-8 first, then GBK (common on Chinese Windows)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gbk")


def load_config() -> XtQuantConfig:
    """Load XTQuant settings from ``~/.vibe-trading/xtquant.json``.

    Auto-discovers ``account_id`` by scanning the ``userdata_mini`` directory
    when no ``account_id`` is stored in the config file.
    """
    path = config_path()
    if not path.exists():
        return XtQuantConfig()
    raw = _read_config_file(path)
    try:
        cfg = XtQuantConfig.from_mapping(json.loads(raw))
    except (OSError, json.JSONDecodeError) as exc:
        raise XtQuantConfigError(
            f"invalid XTQuant config at {path}: {exc}"
        ) from exc

    # Auto-discover account_id when not explicitly configured but
    # mini_qmt_path is set.
    if not cfg.account_id and cfg.mini_qmt_path:
        discovered = _discover_account_ids(cfg.mini_qmt_path)
        if len(discovered) == 1:
            cfg = cfg.with_overrides(account_id=discovered[0])
    return cfg


# ---------------------------------------------------------------------------
# S2: 连接管理
# ---------------------------------------------------------------------------

#: Module-level XtQuantTrader singleton.  Recreated on config change.
_trader: Any = None

#: Config that was used to create the current ``_trader``.
_trader_config: XtQuantConfig | None = None

#: Lock that serialises all ``xtdata.*`` calls to prevent BSON concurrency
#: crashes inside the XTQuant C extension (known issue; fix borrowed from
#: wendao qmt_bridge).
_xtdata_lock = threading.Lock()


def _import_xtquant() -> ModuleType:
    """Lazy-import the ``xtquant`` package with a friendly error when missing.

    Returns:
        The ``xtquant`` module with submodules pre-loaded.

    Raises:
        XtQuantDependencyError: If ``xtquant`` is not installed.
    """
    try:
        import xtquant  # type: ignore[import-untyped]
        import xtquant.xtdata  # noqa: F401  # type: ignore[import-untyped]
        import xtquant.xttrader  # noqa: F401  # type: ignore[import-untyped]
        import xtquant.xttype  # noqa: F401  # type: ignore[import-untyped]

        return xtquant
    except ModuleNotFoundError:
        raise XtQuantDependencyError(
            "xtquant is not installed. xtquant is distributed with QMT / miniQMT "
            "and is typically available only on Windows. Ensure miniQMT is installed "
            "and xtquant is on your Python path."
        ) from None


def _check_platform() -> None:
    """Raise :class:`XtQuantPlatformError` when not running on Windows.

    xtquant's C extension is Windows-only; there is no macOS/Linux fallback.
    """
    if platform.system() != "Windows":
        raise XtQuantPlatformError(
            "xtquant / miniQMT is only supported on Windows. "
            f"Current platform is {platform.system()}."
        )


def _ensure_connected(config: XtQuantConfig) -> Any:
    """Get or create the module-level XtQuantTrader singleton.

    On first call the trader is lazily initialised; when the effective config
    has changed the old trader is disconnected and a new one is created.

    Args:
        config: The effective connector config.

    Returns:
        A connected ``XtQuantTrader`` instance.

    Raises:
        XtQuantPlatformError: If not on Windows.
        XtQuantDependencyError: If ``xtquant`` is not installed.
        XtQuantConfigError: If ``mini_qmt_path`` is not set.
        XtQuantConnectionError: If connecting to miniQMT fails.
    """
    global _trader, _trader_config  # noqa: PLW0603

    _check_platform()

    if not config.mini_qmt_path:
        raise XtQuantConfigError(
            "mini_qmt_path is required. Set the miniQMT installation directory "
            "in ~/.vibe-trading/xtquant.json or via build_config()."
        )

    path = Path(config.mini_qmt_path)
    if not path.exists():
        raise XtQuantConfigError(
            f"mini_qmt_path does not exist: {config.mini_qmt_path}"
        )

    # Reconnect when config changes materially.
    if _trader is not None and _trader_config is not None:
        if (
            _trader_config.mini_qmt_path == config.mini_qmt_path
            and _trader_config.session_id == config.session_id
        ):
            return _trader
        _disconnect()

    xt = _import_xtquant()

    # Generate a random session_id when 0.
    session_id = config.session_id
    if session_id == 0:
        session_id = int.from_bytes(os.urandom(2), byteorder="big")

    try:
        trader = xt.xttrader.XtQuantTrader(str(path), session_id)
        trader.start()
    except Exception as exc:
        raise XtQuantConnectionError(
            f"Failed to connect to miniQMT at {config.mini_qmt_path}: {exc}"
        ) from exc

    # Verify the connection succeeded.
    connect_result = trader.connect()
    if connect_result != 0:
        trader.stop()
        raise XtQuantConnectionError(
            f"miniQMT connection returned error code {connect_result}. "
            "Ensure miniQMT is running and logged in."
        )

    _trader = trader
    _trader_config = config
    return _trader


def _disconnect() -> None:
    """Release the module-level XtQuantTrader singleton."""
    global _trader, _trader_config  # noqa: PLW0603

    if _trader is not None:
        try:
            _trader.stop()
        except Exception:
            pass
        _trader = None
    _trader_config = None


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _normalize_symbol(raw: str) -> str:
    """Ensure the symbol carries a ``.SH`` / ``.SZ`` suffix.

    A 6-digit numeric ticker is treated as Shanghai (``.SH``) when it starts
    with ``6``, otherwise Shenzhen (``.SZ``).  Symbols that already have a
    suffix are uppercased and returned as-is.

    Args:
        raw: Raw symbol string, e.g. ``600036`` or ``000001.SZ``.

    Returns:
        Normalised symbol, e.g. ``600036.SH`` or ``000001.SZ``.
    """
    s = str(raw or "").strip().upper()
    if not s:
        return s
    if "." in s:
        return s
    if re.match(r"^\d{6}$", s):
        return f"{s}.SH" if s.startswith("6") else f"{s}.SZ"
    return s


def _numpy_safe_dict(obj: Any) -> dict[str, Any]:
    """Convert an xtquant result object to a plain dict with Python-native types.

    xtquant may return objects with numpy scalar attributes (``numpy.int64``,
    ``numpy.float64``, etc.).  This helper extracts attributes via ``getattr``
    and converts any numpy scalars to plain Python ``int`` / ``float``.

    Args:
        obj: An xtquant result object (or plain dict, or list).

    Returns:
        A plain ``dict`` with Python-native values.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return {str(k): _numpy_safe_dict(v) if isinstance(v, dict) else _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return {}
    # Treat as an object with attributes.
    result: dict[str, Any] = {}
    for attr_name in dir(obj):
        if attr_name.startswith("_"):
            continue
        try:
            value = getattr(obj, attr_name)
        except Exception:
            continue
        if callable(value):
            continue
        result[attr_name] = _to_native(value)
    return result


def _to_native(value: Any) -> Any:
    """Convert a potentially-numpy scalar to a plain Python type."""
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
    """Safely read an attribute from an xtquant result object.

    xtquant objects use Chinese attribute names (证券代码, 持仓数量, etc.).
    This helper uses ``getattr`` with a default, then converts numpy scalars.

    Args:
        obj: An xtquant result object.
        name: Attribute name (Chinese or English).
        default: Fallback value.

    Returns:
        The Python-native attribute value or *default*.
    """
    try:
        value = getattr(obj, name, default)
    except Exception:
        return default
    return _to_native(value)


def _public_config(cfg: XtQuantConfig) -> dict[str, Any]:
    """Return a config snapshot with the account_id masked for logging.

    Args:
        cfg: The effective config.

    Returns:
        A dict suitable for public logging.
    """
    d = asdict(cfg)
    aid = d.get("account_id", "")
    if aid and len(aid) >= 4:
        d["account_id"] = f"{aid[:2]}****{aid[-2:]}"
    return d


def _discover_account_ids(mini_qmt_path: str) -> list[str]:
    """Scan the miniQMT installation for account directories.

    Checks two locations (in order):

    1. ``<mini_qmt_path>/userdata_mini/`` — numeric subdirectory names.
    2. ``<mini_qmt_path>/userdata_mini/users/`` — any subdirectory names.

    Args:
        mini_qmt_path: Absolute path to the miniQMT installation root
                       (NOT the ``userdata_mini`` subdirectory).

    Returns:
        A sorted list of discovered account-id strings (may be empty).
    """
    root = Path(mini_qmt_path)

    # Location 1: <root>/userdata_mini/<numeric_account>/
    userdata = root / "userdata_mini"
    numeric_ids: list[str] = []
    if userdata.is_dir():
        for entry in userdata.iterdir():
            if entry.is_dir() and entry.name.isdigit():
                numeric_ids.append(entry.name)
    if numeric_ids:
        return sorted(numeric_ids)

    # Location 2: <root>/userdata_mini/users/<account>/
    users_dir = userdata / "users"
    if users_dir.is_dir():
        ids: list[str] = []
        for entry in users_dir.iterdir():
            if entry.is_dir():
                ids.append(entry.name)
        return sorted(ids)

    return []


def _map_period(period: str) -> str:
    """Map a canonical period token to an xtquant period string.

    =========  ==========
    Canonical  xtquant
    =========  ==========
    ``1m``     ``1m``
    ``5m``     ``5m``
    ``15m``    ``15m``
    ``30m``    ``30m``
    ``1h``     ``60m``
    ``4h``     ``240m``
    ``1d``     ``1d``
    ``1w``     ``1w``
    ``1M``     ``1M``
    =========  ==========
    """
    return {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "60m",
        "4h": "240m",
        "1d": "1d",
        "1w": "1w",
        "1M": "1M",
    }.get(str(period or "1d").strip(), "1d")


def _map_order_status(raw: Any) -> str:
    """Map an xtquant order status value to a Vibe-Trading canonical status.

    xtquant order status values:
        - 48: 未报 (unreported)
        - 49: 待报 (pending report)
        - 50: 已报 (reported)
        - 51: 已报待撤 (pending cancel)
        - 52: 部成待撤 (partial filled, pending cancel)
        - 53: 部成 (partial filled)
        - 54: 已成 (fully filled)
        - 55: 已撤 (cancelled)
        - 56: 已废 (rejected)
        - 57: 废单 (invalid)

    Args:
        raw: The xtquant order status code.

    Returns:
        A canonical status string: ``pending``, ``filled``, ``partial``,
        ``cancelled``, ``rejected``, or ``unknown``.
    """
    try:
        code = int(raw)
    except (TypeError, ValueError):
        return "unknown"
    if code in (48, 49, 50):
        return "pending"
    if code == 53:
        return "partial"
    if code == 54:
        return "filled"
    if code in (55, 51, 52):
        return "cancelled"
    if code in (56, 57):
        return "rejected"
    return "unknown"


# ---------------------------------------------------------------------------
# S3: 5 个读操作
# ---------------------------------------------------------------------------


def check_status(config: XtQuantConfig | None = None) -> dict[str, Any]:
    """Check SDK readiness, platform compatibility, and account identity.

    Returns a JSON-serializable health report that degrades cleanly when
    miniQMT is not running, the platform is not Windows, or ``xtquant`` is not
    installed.  Does not place or mutate any broker state.

    Args:
        config: Optional target config; loaded from disk when omitted.

    Returns:
        A health report dict.  On success ``{"status": "ok", ...}``; on any
        error ``{"status": "error", "error": ...}``.
    """
    cfg = config or load_config()

    report: dict[str, Any] = {
        "status": "ok",
        "config": _public_config(cfg),
        "platform": platform.system(),
    }

    # Platform guard.
    try:
        _check_platform()
    except XtQuantPlatformError:
        report["status"] = "error"
        report["error"] = (
            "xtquant / miniQMT is only supported on Windows. "
            f"Current platform is {platform.system()}."
        )
        return report

    # SDK check.
    try:
        _import_xtquant()
        report["sdk"] = {"package": "xtquant", "installed": True}
    except XtQuantDependencyError:
        report["sdk"] = {"package": "xtquant", "installed": False}
        report["status"] = "error"
        report["error"] = (
            "Optional dependency missing: xtquant is distributed with QMT/miniQMT "
            "and must be on the Python path."
        )
        return report

    # Connection check via account snapshot.
    if not cfg.mini_qmt_path:
        report["status"] = "error"
        report["error"] = (
            "mini_qmt_path is not set. Configure the miniQMT installation "
            "directory in ~/.vibe-trading/xtquant.json."
        )
        return report

    # Auto-discover account if needed.
    account_id = cfg.account_id
    if not account_id:
        discovered = _discover_account_ids(cfg.mini_qmt_path)
        report["discovered_accounts"] = discovered
        if len(discovered) == 1:
            account_id = discovered[0]
        elif not discovered:
            report["status"] = "error"
            report["error"] = (
                f"No account directories found under "
                f"{cfg.mini_qmt_path}/userdata_mini/. "
                "Set account_id explicitly in xtquant.json."
            )
            return report

    try:
        snapshot = get_account_snapshot(cfg)
    except Exception as exc:  # noqa: BLE001 - health endpoint reports cleanly
        report["status"] = "error"
        report["error"] = str(exc)
        return report

    report["account"] = {
        "account_id": snapshot.get("account_id"),
        "account_type": snapshot.get("account_type"),
        "total_value": snapshot.get("total_value"),
    }
    return report


def get_account_snapshot(config: XtQuantConfig | None = None) -> dict[str, Any]:
    """Fetch account funds/assets for the resolved account.

    Maps xtquant's ``query_stock_asset`` Chinese attribute names to canonical
    English keys.

    Args:
        config: Optional target config; loaded from disk when omitted.

    Returns:
        A dict with ``status``, ``account_id``, ``total_value``, ``cash``,
        ``buying_power``, ``market_value``, and optionally ``frozen_cash``.
    """
    cfg = config or load_config()
    trader = _ensure_connected(cfg)
    acc = _to_stock_account(cfg)

    try:
        raw = trader.query_stock_asset(acc)
    except Exception as exc:
        raise XtQuantConnectionError(
            f"query_stock_asset failed for account {_mask_id(cfg.account_id)}: {exc}"
        ) from exc

    # query_stock_asset returns a list; extract the first (and typically only) row.
    if isinstance(raw, (list, tuple)) and len(raw) > 0:
        asset_obj = raw[0]
    else:
        asset_obj = raw

    return {
        "status": "ok",
        "account_id": _resolve_account_id(cfg),
        "account_type": cfg.account_type,
        "total_value": _attr(asset_obj, "total_asset", 0.0),
        "cash": _attr(asset_obj, "cash", 0.0),
        "buying_power": _attr(asset_obj, "cash", 0.0),
        "market_value": _attr(asset_obj, "market_value", 0.0),
        "frozen_cash": _attr(asset_obj, "frozen_cash", 0.0),
    }


def get_positions(config: XtQuantConfig | None = None) -> dict[str, Any]:
    """Fetch current positions for the resolved account.

    Maps xtquant's ``query_stock_positions`` Chinese attribute names to
    canonical English keys.

    Args:
        config: Optional target config; loaded from disk when omitted.

    Returns:
        A dict with ``status`` and ``positions`` (list of position dicts).
    """
    cfg = config or load_config()
    trader = _ensure_connected(cfg)
    acc = _to_stock_account(cfg)

    try:
        raw = trader.query_stock_positions(acc)
    except Exception as exc:
        raise XtQuantConnectionError(
            f"query_stock_positions failed for account {_mask_id(cfg.account_id)}: {exc}"
        ) from exc

    positions: list[dict[str, Any]] = []
    items = raw if isinstance(raw, (list, tuple)) else []
    for item in items:
        symbol = _attr(item, "stock_code", "")
        qty = _attr(item, "volume", 0)
        if not symbol or qty == 0:
            continue
        positions.append({
            "symbol": str(symbol),
            "qty": qty,
            "avg_cost": _attr(item, "open_price", 0.0),
            "market_value": _attr(item, "market_value", 0.0),
            "current_price": _attr(item, "open_price", 0.0),
            "unrealized_pnl": 0.0,
        })

    return {
        "status": "ok",
        "account_id": _resolve_account_id(cfg),
        "positions": positions,
    }


def get_open_orders(
    config: XtQuantConfig | None = None,
    *,
    include_executions: bool = False,
) -> dict[str, Any]:
    """Fetch open orders and, optionally, recent fills.

    Maps xtquant's ``query_stock_orders`` Chinese attribute names to canonical
    English keys.

    Args:
        config: Optional target config; loaded from disk when omitted.
        include_executions: When true, also returns ``executions`` from
            ``query_stock_trades``.

    Returns:
        A dict with ``status`` and ``orders`` (and optionally ``executions``).
    """
    cfg = config or load_config()
    trader = _ensure_connected(cfg)
    acc = _to_stock_account(cfg)

    try:
        raw = trader.query_stock_orders(acc)
    except Exception as exc:
        raise XtQuantConnectionError(
            f"query_stock_orders failed for account {_mask_id(account_id)}: {exc}"
        ) from exc

    orders: list[dict[str, Any]] = []
    items = raw if isinstance(raw, (list, tuple)) else []
    for item in items:
        order_id = str(_attr(item, "order_id", "")) or str(_attr(item, "order_sysid", ""))
        if not order_id or order_id == "0":
            continue
        side_raw = _attr(item, "order_type", 0)
        side = "buy" if side_raw == 23 else "sell"
        orders.append({
            "order_id": order_id,
            "symbol": str(_attr(item, "stock_code", "")),
            "side": side,
            "quantity": _attr(item, "order_volume", 0),
            "filled_qty": _attr(item, "traded_volume", 0),
            "limit_price": _attr(item, "price", 0.0),
            "status": _map_order_status(_attr(item, "order_status", 0)),
            "created_at": str(_attr(item, "order_time", "")),
        })

    result: dict[str, Any] = {
        "status": "ok",
        "account_id": _resolve_account_id(cfg),
        "orders": orders,
    }

    if include_executions:
        try:
            raw_trades = trader.query_stock_trades(acc)
        except Exception:
            logger.warning("get_open_orders: failed to query trades for executions")
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

    return result


def get_quote(symbol: str, *, config: XtQuantConfig | None = None, **_: Any) -> dict[str, Any]:
    """Fetch a full market tick for ``symbol`` (e.g. ``600036.SH``).

    Uses ``xtdata.get_full_tick`` wrapped in ``_xtdata_lock`` to prevent BSON
    concurrency crashes.

    Args:
        symbol: A-share ticker, e.g. ``600036.SH`` or ``000001.SZ``.
        config: Optional target config; loaded from disk when omitted.

    Returns:
        A dict with ``status``, ``symbol``, and quote fields (``last``,
        ``bid``, ``ask``, ``volume``, ``open``, ``high``, ``low``,
        ``pre_close``, ``upper_limit``, ``lower_limit``).
    """
    cfg = config or load_config()
    code = _normalize_symbol(symbol)
    if not code:
        raise XtQuantConfigError("symbol is required")

    xt = _import_xtquant()
    xtdata = xt.xtdata

    with _xtdata_lock:
        try:
            tick = xtdata.get_full_tick([code])
        except Exception as exc:
            raise XtQuantConnectionError(
                f"get_full_tick failed for {code}: {exc}"
            ) from exc

    if not tick or code not in tick:
        return {
            "status": "error",
            "symbol": code,
            "error": f"No tick data returned for {code}",
        }

    data = tick[code]
    # tick data is a plain dict, not an object
    _g = lambda k, d=0.0: _to_native(data.get(k, d) if isinstance(data, dict) else getattr(data, k, d))
    return {
        "status": "ok",
        "symbol": code,
        "last": _g("lastPrice", 0.0),
        "bid": _g("bidPrice", [0.0])[0] if isinstance(data.get("bidPrice"), list) else _g("bidPrice", 0.0),
        "ask": _g("askPrice", [0.0])[0] if isinstance(data.get("askPrice"), list) else _g("askPrice", 0.0),
        "volume": _g("volume", 0),
        "open": _g("open", 0.0),
        "high": _g("high", 0.0),
        "low": _g("low", 0.0),
        "pre_close": _g("lastClose", _g("preClose", 0.0)),
        "upper_limit": _g("upStop", _g("upperLimit", 0.0)),
        "lower_limit": _g("downStop", _g("lowerLimit", 0.0)),
    }


# ---------------------------------------------------------------------------
# S4: get_historical_bars
# ---------------------------------------------------------------------------


def get_historical_bars(
    symbol: str,
    *,
    config: XtQuantConfig | None = None,
    period: str = "1d",
    limit: int = 90,
    **_: Any,
) -> dict[str, Any]:
    """Fetch historical K-line bars for ``symbol`` (e.g. ``600036.SH``).

    Uses ``xtdata.download_history_data`` followed by ``xtdata.get_local_data``,
    both wrapped in ``_xtdata_lock``.  The result is normalised to a canonical
    bars format.

    Args:
        symbol: A-share ticker, e.g. ``600036.SH`` or ``000001.SZ``.
        config: Optional target config; loaded from disk when omitted.
        period: Bar period (``1m``, ``5m``, ``15m``, ``30m``, ``1h``, ``4h``,
            ``1d``, ``1w``, ``1M``).
        limit: Maximum number of bars to return.

    Returns:
        A dict with ``status``, ``symbol``, ``period``, and ``bars`` (list of
        ``{t, o, h, l, c, v}`` dicts).
    """
    cfg = config or load_config()
    code = _normalize_symbol(symbol)
    if not code:
        raise XtQuantConfigError("symbol is required")

    _import_xtquant()
    import xtquant.xtdata as xtdata  # type: ignore[import-untyped]

    xt_period = _map_period(period)

    with _xtdata_lock:
        try:
            xtdata.download_history_data(code, period=xt_period)
        except Exception as exc:
            raise XtQuantConnectionError(
                f"download_history_data failed for {code}: {exc}"
            ) from exc

        try:
            raw = xtdata.get_local_data(
                stock_list=[code],
                period=xt_period,
                field_list=["open", "high", "low", "close", "volume", "amount"],
                count=int(limit),
            )
        except Exception as exc:
            raise XtQuantConnectionError(
                f"get_local_data failed for {code}: {exc}"
            ) from exc

    # get_local_data returns a dict keyed by symbol; each value is a DataFrame
    # with the requested fields as columns and timestamps as the index.
    if raw is None or code not in raw:
        return {
            "status": "ok",
            "symbol": code,
            "period": period,
            "bars": [],
        }

    df = raw[code]
    bars: list[dict[str, Any]] = []

    # Handle both pandas DataFrame and plain dict/list.
    to_dict = getattr(df, "to_dict", None)
    if callable(to_dict) and hasattr(df, "columns"):
        # pandas DataFrame: index=timestamp, columns=fields
        try:
            records = df.tail(int(limit)).reset_index().to_dict("records")
            for row in records:
                bars.append({
                    "t": _to_native(row.get("index") or row.get("time") or row.get("datetime") or ""),
                    "o": _to_native(row.get("open", 0.0)),
                    "h": _to_native(row.get("high", 0.0)),
                    "l": _to_native(row.get("low", 0.0)),
                    "c": _to_native(row.get("close", 0.0)),
                    "v": _to_native(row.get("volume", 0)),
                })
        except Exception:
            logger.warning("get_historical_bars: failed to convert xtdata response for %s", symbol)
            return {"status": "ok", "bars": [], "symbol": code, "period": period}
    elif isinstance(df, dict):
        # Plain dict of arrays/lists.
        times = df.get("time", [])
        opens = df.get("open", [])
        highs = df.get("high", [])
        lows = df.get("low", [])
        closes = df.get("close", [])
        volumes = df.get("volume", [])
        count = min(len(times), int(limit))
        for i in range(max(0, len(times) - count), len(times)):
            bars.append({
                "t": _to_native(times[i] if i < len(times) else ""),
                "o": _to_native(opens[i] if i < len(opens) else 0.0),
                "h": _to_native(highs[i] if i < len(highs) else 0.0),
                "l": _to_native(lows[i] if i < len(lows) else 0.0),
                "c": _to_native(closes[i] if i < len(closes) else 0.0),
                "v": _to_native(volumes[i] if i < len(volumes) else 0),
            })

    return {
        "status": "ok",
        "symbol": code,
        "period": period,
        "bars": bars,
    }


# ---------------------------------------------------------------------------
# S5: 下单 & 撤单
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
    """Place an order via xtquant.

    Paper profile → Shadow Account simulation（绝不触及 xtquant SDK）
    Live profile → XtQuantTrader.order_stock()（安全链由 service.py 保证）

    Args:
        config: The effective connector config.
        symbol: A-share ticker, e.g. ``600036.SH``.
        side: ``buy`` or ``sell``.
        quantity: Share quantity.
        notional: Cash amount (not natively supported; leads to error if used
            without quantity).
        order_type: ``market`` or ``limit``.
        limit_price: Required for limit orders.
        time_in_force: Accepted for contract parity; echoed back.

    Returns:
        On success ``{"status": "ok", "order_id": ...}``.
    """
    # ── Paper: 绝不 import xtquant ──
    if config.profile == "paper":
        return _paper_place_order(
            config, symbol, side, quantity, notional, order_type, limit_price
        )

    # ── Live: SDK 直接调用 ──
    _check_platform()
    _ensure_connected(config)
    xt = _import_xtquant()
    acc = xt.StockAccount(config.account_id, config.account_type)

    side_key = str(side or "").strip().lower()
    if side_key not in ("buy", "sell"):
        return {"status": "error", "error": "side must be 'buy' or 'sell'"}

    order_kind = str(order_type or "").strip().lower()
    if order_kind not in ("market", "limit"):
        return {"status": "error", "error": "order_type must be 'market' or 'limit'"}

    if quantity is None:
        if notional is not None:
            return {
                "status": "error",
                "error": "XTQuant requires an explicit quantity; notional-based orders are not supported",
            }
        return {"status": "error", "error": "quantity is required"}

    try:
        qty_int = int(quantity)
    except (TypeError, ValueError):
        return {"status": "error", "error": "quantity must be a whole number of shares"}
    if qty_int <= 0:
        return {"status": "error", "error": "quantity must be a positive whole number of shares"}

    price = 0.0
    if order_kind == "limit":
        if limit_price is None:
            return {"status": "error", "error": "limit_price is required for a limit order"}
        try:
            price = float(limit_price)
        except (TypeError, ValueError):
            return {"status": "error", "error": "limit_price must be a number"}
        if price <= 0:
            return {"status": "error", "error": "limit_price must be a positive number"}

    code = _normalize_symbol(symbol)
    if not code:
        return {"status": "error", "error": "symbol is required"}

    direction = xt.xtconstant.STOCK_BUY if side_key == "buy" else xt.xtconstant.STOCK_SELL
    price_type = xt.xttype.xtconstant.FIX_PRICE if order_kind == "limit" else xt.xttype.xtconstant.LATEST_PRICE

    try:
        order_id = _trader.order_stock(
            acc,
            code,
            xt.xtconstant.STOCK,
            direction,
            price_type,
            xt.xtconstant.ORDER_TYPE_NORMAL,
            qty_int,
            price,
            xt.xtconstant.QUOTE_TYPE_LIMIT,
            order_remark=f"vibe-{kwargs.get('session_id', '')}",
        )
    except Exception as exc:
        return {"status": "error", "error": f"order_stock failed: {exc}"}

    return {
        "status": "ok",
        "order_id": str(order_id),
        "symbol": code,
        "side": side_key,
        "profile": config.profile,
        "order_type": order_kind,
        "quantity": qty_int,
        "limit_price": price if order_kind == "limit" else None,
        "time_in_force": str(time_in_force or "day"),
    }


def _paper_place_order(
    config: XtQuantConfig,
    symbol: str,
    side: str,
    quantity: float | None = None,
    notional: float | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
) -> dict[str, Any]:
    """Route paper orders to Shadow Account — 绝不 import xtquant.

    Paper orders are simulated locally via the Shadow Account system; no
    xtquant SDK call is ever made in this code path.  A deterministic
    order_id is generated from a UUID for traceability.
    """
    code = _normalize_symbol(symbol) or str(symbol or "").strip().upper()
    side_key = str(side or "").strip().lower()
    order_kind = str(order_type or "").strip().lower()
    qty = int(quantity) if quantity is not None else 0

    price = 0.0
    if order_kind == "limit" and limit_price is not None:
        try:
            price = float(limit_price)
        except (TypeError, ValueError):
            price = 0.0

    paper_id = f"paper-{uuid.uuid4().hex[:12]}"

    logger.info(
        "paper place_order: %s %s %s qty=%s notional=%s type=%s id=%s",
        side_key, code, config.profile, qty, notional, order_kind, paper_id,
    )

    return {
        "status": "ok",
        "order_id": paper_id,
        "symbol": code,
        "side": side_key,
        "profile": config.profile,
        "order_type": order_kind,
        "quantity": qty,
        "notional": notional,
        "limit_price": price if order_kind == "limit" else None,
        "time_in_force": "day",
        "paper": True,
    }


def cancel_order(
    config: XtQuantConfig,
    order_id: str,
    symbol: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Cancel an order. Paper → Shadow Account, Live → xtquant SDK.

    Cancelling is risk-reducing, so it is not blocked by the mandate or the
    kill switch.  Paper cancellations are simulated locally; live
    cancellations call ``cancel_order_stock`` on the XTQuant trader.

    Args:
        config: The effective connector config.
        order_id: The order id to cancel.
        symbol: Optional symbol for context (echoed back).

    Returns:
        On success ``{"status": "ok", "order_id": ...}``.
    """
    if config.profile == "paper":
        return _paper_cancel_order(config, order_id)

    _check_platform()
    _ensure_connected(config)
    xt = _import_xtquant()
    acc = xt.StockAccount(config.account_id, config.account_type)

    try:
        _trader.cancel_order_stock(acc, int(order_id))
    except Exception as exc:
        return {"status": "error", "error": f"cancel_order_stock failed: {exc}"}

    return {
        "status": "ok",
        "order_id": str(order_id),
        "symbol": str(symbol or "").strip().upper() or None,
        "profile": config.profile,
    }


def _paper_cancel_order(
    config: XtQuantConfig,
    order_id: str,
) -> dict[str, Any]:
    """Route paper cancel to Shadow Account — 绝不 import xtquant."""
    logger.info(
        "paper cancel_order: %s profile=%s",
        order_id, config.profile,
    )
    return {
        "status": "ok",
        "order_id": str(order_id),
        "profile": config.profile,
        "paper": True,
    }


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _resolve_account_id(cfg: XtQuantConfig) -> str:
    """Resolve the effective account id.

    When ``account_id`` is explicitly configured it is returned as-is.
    Otherwise we auto-discover from ``userdata_mini``.

    Args:
        cfg: The effective config.

    Returns:
        The resolved account id.

    Raises:
        XtQuantConfigError: When no account_id can be resolved.
    """
    if cfg.account_id:
        return cfg.account_id

    discovered = _discover_account_ids(cfg.mini_qmt_path)
    if len(discovered) == 1:
        return discovered[0]
    if not discovered:
        raise XtQuantConfigError(
            f"No account directories found under "
            f"{cfg.mini_qmt_path}/userdata_mini/. "
            "Set account_id explicitly in xtquant.json."
        )
    raise XtQuantConfigError(
        f"Multiple account directories found ({', '.join(discovered)}). "
        "Set account_id explicitly in xtquant.json."
    )


def _to_stock_account(cfg: XtQuantConfig):
    """Build an xtquant ``StockAccount`` from effective config.

    Returns:
        An ``xtquant.xttype.StockAccount`` instance for the resolved account.
    """
    xt = _import_xtquant()
    account_id = _resolve_account_id(cfg)
    return xt.xttype.StockAccount(account_id, cfg.account_type)


def _mask_id(account_id: str) -> str:
    """Mask an account id for safe logging.

    Args:
        account_id: The raw account id.

    Returns:
        A masked string like ``12****89``.
    """
    if len(account_id) < 4:
        return "****"
    return f"{account_id[:2]}****{account_id[-2:]}"
