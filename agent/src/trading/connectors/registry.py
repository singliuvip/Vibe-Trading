"""Broker connector registry — discovers and validates connector plugins.

Entry-point plugins are discovered via the ``vibe_trading.connectors`` group.
Legacy built-in connectors are constructed from the same profile modules that
``profiles.py`` has always imported, guaranteeing zero behavioural change for
existing connectors.

All public query functions are safe to call at import time (the discovery is
lazy-cached on first use).
"""

from __future__ import annotations

import functools
import logging
import sys
from typing import Any, Callable

from src.trading.connectors.contract import (
    BrokerConnectorSpec,
    BrokerRuntimeDeclaration,
    LiveRunnerFactoryContext,
)
from src.trading.types import TradingProfile

#: Capability constant for managed live-runner support (mirrors ``service.RUNNER_CAPABILITY``).
_RUNNER_CAPABILITY = "runner.manage.requires_mandate"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Legacy built-in data (mirrors the imports in profiles.py + service.py)
# ---------------------------------------------------------------------------

# -- Profile modules (same imports as profiles.py) -------------------------
from src.trading.connectors.alpaca.profiles import ALPACA_PROFILES  # noqa: E402
from src.trading.connectors.binance.profiles import BINANCE_PROFILES  # noqa: E402
from src.trading.connectors.dhan.profiles import DHAN_PROFILES  # noqa: E402
from src.trading.connectors.futu.profiles import FUTU_PROFILES  # noqa: E402
from src.trading.connectors.ibkr.profiles import IBKR_PROFILES  # noqa: E402
from src.trading.connectors.longbridge.profiles import LONGBRIDGE_PROFILES  # noqa: E402
from src.trading.connectors.okx.profiles import OKX_PROFILES  # noqa: E402
from src.trading.connectors.robinhood.profiles import ROBINHOOD_PROFILES  # noqa: E402
from src.trading.connectors.shoonya.profiles import SHOONYA_PROFILES  # noqa: E402
from src.trading.connectors.tiger.profiles import TIGER_PROFILES  # noqa: E402
from src.trading.connectors.trading212.profiles import TRADING212_PROFILES  # noqa: E402

# Virtual is migrated to entry-point plugin — NOT in the legacy table.
# The entry-point plugin provides the same VIRTUAL_PROFILES via its spec.
# However, during development (PYTHONPATH without pip install), entry_points
# are not discoverable.  We include virtual in the legacy table as a fallback
# so that ``list_profiles()`` always returns the full set.  When the package
# is installed, the entry-point plugin is discovered and merged (the legacy
# entry takes priority for profiles/sdk/instrument, but the plugin's
# ``live_runner_factory`` and ``supports_live_runner`` are used via a
# post-merge augmentation step).

# -- SDK module paths (mirrors _SDK_CONNECTOR_MODULES in service.py) --------
_LEGACY_SDK_PATHS: dict[str, str] = {
    "tiger": "src.trading.connectors.tiger.sdk",
    "longbridge": "src.trading.connectors.longbridge.sdk",
    "alpaca": "src.trading.connectors.alpaca.sdk",
    "okx": "src.trading.connectors.okx.sdk",
    "binance": "src.trading.connectors.binance.sdk",
    "futu": "src.trading.connectors.futu.sdk",
    "dhan": "src.trading.connectors.dhan.sdk",
    "shoonya": "src.trading.connectors.shoonya.sdk",
    "trading212": "src.trading.connectors.trading212.sdk",
}

# -- Instrument classification (mirrors _CONNECTOR_INSTRUMENT in service.py) -
_LEGACY_INSTRUMENTS: dict[str, tuple[str, str | None]] = {
    "okx": ("crypto", "crypto"),
    "binance": ("crypto", "crypto"),
    "alpaca": ("equity", "us_equity"),
    "tiger": ("equity", None),
    "longbridge": ("equity", None),
    "futu": ("equity", None),
    "trading212": ("equity", None),
}

# -- Profile tuples keyed by connector -------------------------------------
_LEGACY_PROFILES: dict[str, tuple[TradingProfile, ...]] = {
    "ibkr": IBKR_PROFILES,
    "robinhood": ROBINHOOD_PROFILES,
    "tiger": TIGER_PROFILES,
    "longbridge": LONGBRIDGE_PROFILES,
    "alpaca": ALPACA_PROFILES,
    "okx": OKX_PROFILES,
    "binance": BINANCE_PROFILES,
    "futu": FUTU_PROFILES,
    "dhan": DHAN_PROFILES,
    "shoonya": SHOONYA_PROFILES,
    "trading212": TRADING212_PROFILES,
}


def _legacy_live_runner_support(profile: TradingProfile) -> bool:
    """Legacy live-runner support check (mirrors ``profile_supports_live_runner``).

    Paper brokers using broker_sdk (e.g. virtual) or live remote_mcp brokers
    can run the managed live runner if they carry the runner capability.
    """
    if profile.environment == "paper" and profile.transport == "broker_sdk":
        return _RUNNER_CAPABILITY in profile.capabilities and not profile.readonly
    return (
        profile.environment == "live"
        and profile.transport == "remote_mcp"
        and _RUNNER_CAPABILITY in profile.capabilities
    )


def _build_legacy_specs() -> dict[str, BrokerConnectorSpec]:
    """Build ``BrokerConnectorSpec`` entries for every legacy built-in connector.

    Virtual is included in the legacy table so that ``list_profiles()`` is
    complete even when the package has not been pip-installed (entry_points
    are not discoverable via ``PYTHONPATH`` alone).  Its ``live_runner_factory``
    is imported directly from the plugin module so the LiveRunner wiring works
    regardless of installation mode.
    """
    # Import virtual's live_runner_factory directly (avoiding entry_points)
    from src.trading.connectors.virtual.plugin import _build_virtual_live_runner as _virtual_factory

    # Virtual profiles + legacy profiles
    from src.trading.connectors.virtual.profiles import VIRTUAL_PROFILES

    _all_legacy: dict[str, tuple[TradingProfile, ...]] = dict(_LEGACY_PROFILES)
    _all_legacy["virtual"] = VIRTUAL_PROFILES

    _all_sdk: dict[str, str | None] = dict(_LEGACY_SDK_PATHS)
    _all_sdk["virtual"] = "src.trading.connectors.virtual.sdk"

    _all_instruments: dict[str, tuple[str, str | None]] = dict(_LEGACY_INSTRUMENTS)
    _all_instruments["virtual"] = ("equity", None)

    specs: dict[str, BrokerConnectorSpec] = {}
    for connector_key, profiles in _all_legacy.items():
        sdk_module = _all_sdk.get(connector_key)
        instrument = _all_instruments.get(connector_key, ("equity", None))

        # Virtual gets its live_runner_factory injected directly
        live_factory = None
        if connector_key == "virtual":
            live_factory = _virtual_factory

        specs[connector_key] = BrokerConnectorSpec(
            connector_key=connector_key,
            profiles=profiles,
            sdk_module=sdk_module,
            instrument=instrument,
            supports_live_runner=_legacy_live_runner_support,
            live_runner_factory=live_factory,
            runtime=BrokerRuntimeDeclaration(
                requires_oauth=(connector_key != "virtual"),
                direct_trading_supported=sdk_module is not None,
                direct_trading_requires_mandate=True,
                runner_requires_mandate=True,
                include_in_live_status=True,
            ),
        )
    return specs


# ---------------------------------------------------------------------------
# Entry-point discovery
# ---------------------------------------------------------------------------

_ENTRY_POINT_GROUP = "vibe_trading.connectors"


def _discover_entry_point_specs() -> dict[str, BrokerConnectorSpec]:
    """Discover connector plugins via ``importlib.metadata.entry_points``.

    Each entry point must point to a ``register() -> BrokerConnectorSpec``
    callable.  Plugins whose ``connector_key`` collides with a legacy key are
    rejected with a warning (legacy always wins).  A single failing plugin
    does not prevent other plugins from loading.
    """
    if sys.version_info < (3, 12):
        # Python 3.11: entry_points() does not support the ``group`` kwarg
        try:
            from importlib.metadata import entry_points as _eps  # type: ignore[attr-defined]
        except ImportError:
            return {}
        try:
            all_eps = _eps()
            group_eps = all_eps.get(_ENTRY_POINT_GROUP, ())
        except Exception:
            logger.debug("entry_points discovery failed", exc_info=True)
            return {}
    else:
        try:
            from importlib.metadata import entry_points as _eps
        except ImportError:
            return {}
        try:
            group_eps = _eps(group=_ENTRY_POINT_GROUP)
        except Exception:
            logger.debug("entry_points discovery failed", exc_info=True)
            return {}

    discovered: dict[str, BrokerConnectorSpec] = {}
    for ep in group_eps:
        try:
            register_fn = ep.load()
        except Exception:
            logger.warning(
                "Failed to load connector plugin entry point %r; skipping.", ep.name, exc_info=True
            )
            continue
        if not callable(register_fn):
            logger.warning(
                "Connector plugin entry point %r does not point to a callable; skipping.", ep.name
            )
            continue
        try:
            spec = register_fn()
        except Exception:
            logger.warning(
                "Connector plugin %r raised during register(); skipping.", ep.name, exc_info=True
            )
            continue
        if not isinstance(spec, BrokerConnectorSpec):
            logger.warning(
                "Connector plugin %r returned %r instead of BrokerConnectorSpec; skipping.",
                ep.name,
                type(spec).__name__,
            )
            continue
        discovered[spec.connector_key] = spec

    return discovered


# ---------------------------------------------------------------------------
# Merged spec cache
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _cached_all_specs() -> dict[str, BrokerConnectorSpec]:
    """Return the merged (legacy + entry-point) connector specs, cached."""
    legacy = _build_legacy_specs()
    entry = _discover_entry_point_specs()

    merged = dict(legacy)  # legacy wins
    for key, spec in entry.items():
        if key in legacy:
            logger.warning(
                "Connector plugin %r conflicts with built-in connector key; "
                "the built-in connector will be used. Remove the built-in "
                "connector or rename the plugin to resolve.",
                key,
            )
            continue
        merged[key] = spec
    return merged


def clear_connector_registry_cache() -> None:
    """Clear the connector spec cache (for test use)."""
    _cached_all_specs.cache_clear()


def discover_connector_specs() -> dict[str, BrokerConnectorSpec]:
    """Return all discovered connector specs (legacy + entry-point plugins)."""
    return dict(_cached_all_specs())


# ---------------------------------------------------------------------------
# Public query API
# ---------------------------------------------------------------------------


def list_connector_profiles() -> list[TradingProfile]:
    """Return every trading profile from every discovered connector.

    The order is deterministic: legacy built-in profiles first (in their
    traditional order), followed by entry-point plugin profiles (sorted by
    connector key).
    """
    specs = _cached_all_specs()
    # Legacy connectors in traditional order (mirrors BUILTIN_PROFILES order)
    legacy_order = [
        "ibkr", "robinhood", "tiger", "longbridge", "alpaca",
        "okx", "binance", "futu", "dhan", "shoonya", "trading212",
        "virtual",
    ]
    profiles: list[TradingProfile] = []
    seen_ids: set[str] = set()

    # Legacy first (preserving traditional order)
    for key in legacy_order:
        spec = specs.get(key)
        if spec is None:
            continue
        for p in spec.profiles:
            if p.id not in seen_ids:
                profiles.append(p)
                seen_ids.add(p.id)

    # Entry-point plugins (sorted by connector_key for determinism)
    plugin_keys = sorted(k for k in specs if k not in set(legacy_order))
    for key in plugin_keys:
        spec = specs[key]
        for p in spec.profiles:
            if p.id not in seen_ids:
                profiles.append(p)
                seen_ids.add(p.id)

    return profiles


def connector_spec(connector_key: str) -> BrokerConnectorSpec | None:
    """Return the ``BrokerConnectorSpec`` for *connector_key*, or ``None``."""
    return _cached_all_specs().get(connector_key)


def sdk_module_path(connector_key: str) -> str | None:
    """Return the dotted SDK module path for *connector_key*, or ``None``."""
    spec = _cached_all_specs().get(connector_key)
    if spec is not None:
        return spec.sdk_module
    return None


def connector_instrument(connector_key: str) -> tuple[str, str | None]:
    """Return the ``(instrument_type, asset_class | None)`` for *connector_key*."""
    spec = _cached_all_specs().get(connector_key)
    if spec is not None:
        return spec.instrument
    return ("equity", None)


def connector_supports_live_runner(profile: TradingProfile) -> bool:
    """Return whether *profile* supports the managed live runner.

    Delegates to the connector spec's ``supports_live_runner`` callable or bool.
    """
    spec = _cached_all_specs().get(profile.connector)
    if spec is None:
        return False
    support = spec.supports_live_runner
    if callable(support):
        return support(profile)
    return bool(support)


def connector_runtime_declaration(connector_key: str) -> BrokerRuntimeDeclaration | None:
    """Return the ``BrokerRuntimeDeclaration`` for *connector_key*, or ``None``."""
    spec = _cached_all_specs().get(connector_key)
    if spec is not None:
        return spec.runtime
    return None


def live_runner_factory(connector_key: str) -> Callable[..., Any] | None:
    """Return the ``live_runner_factory`` callable for *connector_key*, or ``None``."""
    spec = _cached_all_specs().get(connector_key)
    if spec is not None:
        return spec.live_runner_factory
    return None
