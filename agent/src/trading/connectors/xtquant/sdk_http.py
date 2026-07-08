"""HTTP-bridge xtquant connector — Docker / Linux compatible.

Mirrors the function signatures of ``sdk.py`` but replaces every ``import
xtquant`` + direct SDK call with an HTTP POST to a QMT Bridge server running on
the Windows host.  The caller (``service.py``) sees the same interface and does
not need to know which transport is in use.

Architecture::

    service.py ──► sdk_http.py ──► httpx ──► QMT Bridge :8888 ──► xtquant ──► miniQMT
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig

logger = logging.getLogger(__name__)

# Re-export the config class so callers can use it as they do XtQuantConfig.
__all__ = [
    "XtQuantHttpConfig",
    "build_config",
    "check_status",
    "get_account_snapshot",
    "get_positions",
    "get_open_orders",
    "get_quote",
    "get_historical_bars",
    "load_config",
]

# ---------------------------------------------------------------------------
# Error classes (mirror sdk.py)
# ---------------------------------------------------------------------------


class XtQuantDependencyError(RuntimeError):
    """Raised when httpx (the HTTP transport dependency) is not available."""


class XtQuantConfigError(RuntimeError):
    """Raised when the connector configuration is missing or invalid."""


class XtQuantConnectionError(RuntimeError):
    """Raised when the connector cannot reach the QMT Bridge."""


class XtQuantPlatformError(RuntimeError):
    """Raised when the platform check fails (not relevant for HTTP mode)."""


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def _http_get(endpoint: str, config: XtQuantHttpConfig) -> dict[str, Any]:
    """GET from the QMT Bridge.

    Args:
        endpoint: API path, e.g. ``/health``.
        config: HTTP connection config.

    Returns:
        Parsed JSON response dict.
    """
    url = f"{config.bridge_url.rstrip('/')}{endpoint}"
    headers = config.auth_header

    last_exc: Exception | None = None
    for attempt in range(config.max_retries):
        try:
            resp = httpx.get(url, headers=headers, timeout=config.timeout)
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < config.max_retries - 1:
                time.sleep(min(2 ** attempt, 8))
            continue
        except httpx.ConnectError as exc:
            raise XtQuantConnectionError(
                f"Cannot connect to QMT Bridge at {config.bridge_url}. "
                "Is the bridge running on the host?"
            ) from exc
        except Exception as exc:
            last_exc = exc
            if attempt < config.max_retries - 1:
                time.sleep(min(2 ** attempt, 8))
            continue

        if resp.status_code >= 400:
            raise XtQuantConnectionError(
                f"QMT Bridge returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    raise XtQuantConnectionError(
        f"QMT Bridge request failed after {config.max_retries} retries: {last_exc}"
    )


def _http_post(endpoint: str, body: dict[str, Any], config: XtQuantHttpConfig) -> dict[str, Any]:
    """POST to the QMT Bridge with retry and error mapping.

    Args:
        endpoint: API path, e.g. ``/api/v1/account/snapshot``.
        body: JSON-serializable request body.
        config: HTTP connection config.

    Returns:
        Parsed JSON response dict.

    Raises:
        XtQuantConnectionError: On connection failure, timeout, or bridge error.
        XtQuantConfigError: On 401 (bad bearer token).
    """
    url = f"{config.bridge_url.rstrip('/')}{endpoint}"
    headers = {
        "Content-Type": "application/json",
        **config.auth_header,
    }

    last_exc: Exception | None = None
    for attempt in range(config.max_retries):
        try:
            resp = httpx.post(
                url,
                json=body,
                headers=headers,
                timeout=config.timeout,
            )
        except httpx.TimeoutException as exc:
            last_exc = exc
            logger.warning(
                "xtquant HTTP timeout on %s (attempt %d/%d)",
                endpoint, attempt + 1, config.max_retries,
            )
            if attempt < config.max_retries - 1:
                time.sleep(min(2 ** attempt, 8))
            continue
        except httpx.ConnectError as exc:
            raise XtQuantConnectionError(
                f"Cannot connect to QMT Bridge at {config.bridge_url}. "
                "Is the bridge running on the host?"
            ) from exc
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "xtquant HTTP error on %s (attempt %d/%d): %s",
                endpoint, attempt + 1, config.max_retries, exc,
            )
            if attempt < config.max_retries - 1:
                time.sleep(min(2 ** attempt, 8))
            continue

        # Check HTTP status
        if resp.status_code == 401:
            raise XtQuantConfigError(
                "QMT Bridge returned 401 — check XTQUANT_BEARER_TOKEN"
            )
        if resp.status_code == 503:
            body_data = resp.json()
            if body_data.get("decision") == "halt":
                raise XtQuantConnectionError(
                    "QMT Bridge reports kill switch is active — trading halted"
                )
            raise XtQuantConnectionError(
                f"QMT Bridge returned 503: {body_data.get('detail', 'Service unavailable')}"
            )
        if resp.status_code >= 400:
            raise XtQuantConnectionError(
                f"QMT Bridge returned {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        if data.get("status") == "blocked":
            raise XtQuantConnectionError(
                f"QMT Bridge blocked request: {data.get('reason', 'unknown')}"
            )
        return data

    raise XtQuantConnectionError(
        f"QMT Bridge request failed after {config.max_retries} retries: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Config loading (mirrors sdk.py)
# ---------------------------------------------------------------------------


def load_config() -> XtQuantHttpConfig:
    """Load HTTP config from environment variables.

    In HTTP mode, configuration comes from environment variables rather than
    ``~/.vibe-trading/xtquant.json`` because the container cannot access the
    host filesystem directly.

    Returns:
        A :class:`XtQuantHttpConfig` from environment.
    """
    return XtQuantHttpConfig.from_env()


def build_config(
    profile_config: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> XtQuantHttpConfig:
    """Resolve the effective config: env defaults ← profile config ← overrides.

    Args:
        profile_config: The connector profile's ``config`` dict (from profiles.py).
        overrides: Per-call overrides.

    Returns:
        A resolved :class:`XtQuantHttpConfig`.
    """
    cfg = load_config()
    # Apply profile-level config
    if profile_config:
        profile = profile_config.get("profile", cfg.profile)
        mini_qmt_path = profile_config.get("mini_qmt_path", cfg.mini_qmt_path)
        account_id = profile_config.get("account_id", cfg.account_id)
        account_type = profile_config.get("account_type", cfg.account_type)
        cfg = XtQuantHttpConfig(
            bridge_url=cfg.bridge_url,
            bearer_token=cfg.bearer_token,
            mini_qmt_path=mini_qmt_path,
            account_id=account_id,
            account_type=account_type,
            profile=profile,
            timeout=cfg.timeout,
            max_retries=cfg.max_retries,
            readonly=cfg.readonly,
        )
    # Apply CLI/tool overrides
    if overrides:
        cfg = XtQuantHttpConfig(
            bridge_url=overrides.get("bridge_url", cfg.bridge_url),
            bearer_token=overrides.get("bearer_token", cfg.bearer_token),
            mini_qmt_path=overrides.get("mini_qmt_path", cfg.mini_qmt_path),
            account_id=overrides.get("account_id", cfg.account_id),
            account_type=overrides.get("account_type", cfg.account_type),
            profile=overrides.get("profile", cfg.profile),
            timeout=float(overrides.get("timeout", cfg.timeout)),
            max_retries=int(overrides.get("max_retries", cfg.max_retries)),
            readonly=bool(overrides.get("readonly", cfg.readonly)),
        )
    return cfg


# ---------------------------------------------------------------------------
# Body helper
# ---------------------------------------------------------------------------


def _body(config: XtQuantHttpConfig, **extra: Any) -> dict[str, Any]:
    """Build the standard POST body including mini_qmt_path and account info."""
    body: dict[str, Any] = {
        "account_id": config.account_id,
        "account_type": config.account_type,
        "mini_qmt_path": config.mini_qmt_path,
    }
    body.update({k: v for k, v in extra.items() if v not in (None, "", 0)})
    return body


# ---------------------------------------------------------------------------
# 7 read operations (identical signatures to sdk.py)
# ---------------------------------------------------------------------------


def check_status(config: XtQuantHttpConfig | None = None) -> dict[str, Any]:
    """Check bridge health and xtquant readiness.

    Args:
        config: Optional target config; loaded from env when omitted.

    Returns:
        A health report dict.
    """
    cfg = config or load_config()
    try:
        data = _http_get("/health", cfg)
    except XtQuantConnectionError as exc:
        return {
            "status": "error",
            "error": str(exc),
            "config": cfg.to_dict(),
        }

    return {
        "status": "ok" if data.get("xtquant_installed") else "error",
        "config": cfg.to_dict(),
        "platform": data.get("platform", "unknown"),
        "sdk": {"package": "xtquant (via HTTP bridge)", "installed": data.get("xtquant_installed", False)},
        "bridge": {
            "url": cfg.bridge_url,
            "connected": data.get("connected", False),
        },
    }


def get_account_snapshot(config: XtQuantHttpConfig | None = None) -> dict[str, Any]:
    """Fetch account funds/assets via HTTP bridge.

    Args:
        config: Optional target config; loaded from env when omitted.

    Returns:
        A dict with ``status``, ``account_id``, ``total_value``, ``cash``,
        ``buying_power``, ``market_value``, ``frozen_cash``.
    """
    cfg = config or load_config()
    data = _http_post("/api/v1/account/snapshot", _body(cfg), cfg)
    if data.get("status") != "ok":
        raise XtQuantConnectionError(
            f"account/snapshot failed: {data.get('error', 'unknown')}"
        )
    return data


def get_positions(config: XtQuantHttpConfig | None = None) -> dict[str, Any]:
    """Fetch current positions via HTTP bridge.

    Args:
        config: Optional target config; loaded from env when omitted.

    Returns:
        A dict with ``status`` and ``positions``.
    """
    cfg = config or load_config()
    data = _http_post("/api/v1/account/positions", _body(cfg), cfg)
    if data.get("status") != "ok":
        raise XtQuantConnectionError(
            f"account/positions failed: {data.get('error', 'unknown')}"
        )
    return data


def get_open_orders(
    config: XtQuantHttpConfig | None = None,
    *,
    include_executions: bool = False,
) -> dict[str, Any]:
    """Fetch open orders via HTTP bridge.

    Args:
        config: Optional target config; loaded from env when omitted.
        include_executions: When true, also returns ``executions``.

    Returns:
        A dict with ``status``, ``orders`` (and optionally ``executions``).
    """
    cfg = config or load_config()
    data = _http_post(
        "/api/v1/account/orders",
        _body(cfg, include_executions=include_executions),
        cfg,
    )
    if data.get("status") != "ok":
        raise XtQuantConnectionError(
            f"account/orders failed: {data.get('error', 'unknown')}"
        )
    return data


def get_quote(symbol: str, *, config: XtQuantHttpConfig | None = None, **_: Any) -> dict[str, Any]:
    """Fetch a real-time quote via HTTP bridge.

    Args:
        symbol: A-share ticker, e.g. ``600036.SH``.
        config: Optional target config; loaded from env when omitted.

    Returns:
        A dict with ``status``, ``symbol``, and quote fields.
    """
    cfg = config or load_config()
    data = _http_post("/api/v1/market/quote", {"symbol": symbol}, cfg)
    if data.get("status") != "ok":
        raise XtQuantConnectionError(
            f"market/quote failed for {symbol}: {data.get('error', 'unknown')}"
        )
    return data


def get_historical_bars(
    symbol: str,
    *,
    config: XtQuantHttpConfig | None = None,
    period: str = "1d",
    limit: int = 90,
    **_: Any,
) -> dict[str, Any]:
    """Fetch historical K-line bars via HTTP bridge.

    Args:
        symbol: A-share ticker, e.g. ``600036.SH``.
        config: Optional target config; loaded from env when omitted.
        period: Bar period (``1m``, ``5m``, ``1h``, ``1d``, etc.).
        limit: Max number of bars.

    Returns:
        A dict with ``status``, ``symbol``, ``period``, and ``bars``.
    """
    cfg = config or load_config()
    data = _http_post(
        "/api/v1/market/bars",
        {"symbol": symbol, "period": period, "limit": limit},
        cfg,
    )
    if data.get("status") != "ok":
        raise XtQuantConnectionError(
            f"market/bars failed for {symbol}: {data.get('error', 'unknown')}"
        )
    return data
