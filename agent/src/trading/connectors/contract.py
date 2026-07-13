"""Broker connector plugin contract — shared data classes for the plugin system.

External broker packages declare a ``register() -> BrokerConnectorSpec``
function and advertise it via the ``vibe_trading.connectors`` entry-point
group.  The registry (``registry.py``) discovers and validates these specs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.trading.types import TradingProfile


@dataclass(frozen=True)
class BrokerRuntimeDeclaration:
    """Per-broker runtime capability declaration.

    Plugins declare what their broker channel supports so that the API layer
    (``live_routes.py``) can answer capability queries without hard-coding
    broker-specific knowledge.
    """

    requires_oauth: bool = True
    """Whether the broker requires OAuth authorization before any operation."""

    direct_trading_supported: bool = False
    """Whether direct (SDK) trading is supported at all."""

    direct_trading_requires_mandate: bool = True
    """Whether direct trading requires a committed mandate."""

    runner_requires_mandate: bool = True
    """Whether the LiveRunner requires a committed mandate."""

    include_in_live_status: bool = True
    """Whether this broker appears in ``GET /live/status`` listings."""


@dataclass(frozen=True)
class LiveRunnerFactoryContext:
    """Context passed to a plugin's ``live_runner_factory`` callable.

    The factory receives everything it needs to construct a fully-wired
    ``LiveRunner`` without reaching into internal API module state.
    """

    broker: str
    """The broker key (e.g. ``"virtual"``)."""

    profile: TradingProfile
    """The resolved trading profile for this runner."""

    session_id: str
    """The session id assigned to the runner."""

    session_service: Any
    """The public ``SessionService`` instance."""

    agent_caller: Callable[..., Any]
    """Async callable ``(session_id, prompt) -> dict``."""

    write_audit_fn: Callable[..., Any]
    """Callable that writes a live-action audit record and emits a bus event."""

    reconcile_fn: Callable[..., Any]
    """The R4 reconciliation function."""


@dataclass(frozen=True)
class BrokerConnectorSpec:
    """Complete plugin specification for a broker connector.

    An external package registers one of these via::

        def register() -> BrokerConnectorSpec:
            return BrokerConnectorSpec(
                connector_key="acme",
                profiles=(TradingProfile(...),),
                sdk_module="vibe_trading_broker_acme.sdk",
                ...
            )
    """

    connector_key: str
    """Unique connector key (e.g. ``"virtual"``, ``"alpaca"``)."""

    profiles: tuple[TradingProfile, ...]
    """All trading profiles exposed by this connector."""

    sdk_module: str | None = None
    """Dotted import path to the SDK module (``broker_sdk`` transport)."""

    instrument: tuple[str, str | None] = ("equity", None)
    """Default ``(instrument_type, asset_class | None)`` for order classification."""

    supports_live_runner: bool | Callable[[TradingProfile], bool] = False
    """Whether this connector supports the persistent LiveRunner.

    When a callable, it receives a ``TradingProfile`` and returns a bool.
    """

    live_runner_factory: Callable[..., Any] | None = None
    """Optional factory: ``(LiveRunnerFactoryContext) -> LiveRunner``."""

    runtime: BrokerRuntimeDeclaration = field(default_factory=BrokerRuntimeDeclaration)
    """Per-broker runtime capability declaration."""
