"""Virtual broker connector plugin — entry-point registration.

This module is loaded via the ``vibe_trading.connectors`` entry-point group.
It declares the virtual broker's capabilities so the registry and API layer
can query them without hard-coding ``"virtual"`` special cases.

When the virtual LiveRunner needs to be constructed, the factory defined here
builds it using the same wiring that was previously inline in
``live_routes._build_live_runner``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from src.trading.connectors.contract import (
    BrokerConnectorSpec,
    BrokerRuntimeDeclaration,
    LiveRunnerFactoryContext,
)
from src.trading.connectors.virtual.profiles import VIRTUAL_PROFILES

logger = logging.getLogger(__name__)


def register() -> BrokerConnectorSpec:
    """Return the virtual broker connector spec.

    The virtual broker is a fully local paper-trading simulator.  It requires
    no OAuth, supports direct SDK trading, and provides a LiveRunner factory
    for autonomous dry-run trading under a committed mandate.
    """
    return BrokerConnectorSpec(
        connector_key="virtual",
        profiles=VIRTUAL_PROFILES,
        sdk_module="src.trading.connectors.virtual.sdk",
        instrument=("equity", None),
        supports_live_runner=lambda p: (
            p.environment == "paper"
            and p.transport == "broker_sdk"
            and "runner.manage.requires_mandate" in p.capabilities
            and not p.readonly
        ),
        live_runner_factory=_build_virtual_live_runner,
        runtime=BrokerRuntimeDeclaration(
            requires_oauth=False,
            direct_trading_supported=True,
            direct_trading_requires_mandate=True,
            runner_requires_mandate=True,
            include_in_live_status=True,
        ),
    )


def _build_virtual_live_runner(context: LiveRunnerFactoryContext) -> Any:
    """Construct a fully-wired ``LiveRunner`` for the virtual broker.

    Migrated from the ``if broker == "virtual":`` branch in
    ``live_routes._build_live_runner()``.  Wires the runner to the real
    trading surfaces — the session service agent caller, the virtual SDK
    read/write operations, the R4 reconciler, the R1 scheduler, and R3
    market-hours triggers — and injects an audit ``event_callback`` so every
    autonomous live action is broadcast as a ``live.action`` SSE event.
    """
    from src.live.runtime.runner import LiveRunner
    from src.live.runtime.scheduler import Scheduler
    from src.live.runtime.triggers import Trigger
    from src.trading import service as trading_service

    profile = context.profile
    profile_id = profile.id

    def _virtual_read_positions() -> Dict[str, Any]:
        return trading_service.get_positions(profile_id)

    def _virtual_read_balance() -> Dict[str, Any]:
        return trading_service.get_account(profile_id)

    def _virtual_read_open_orders() -> Dict[str, Any]:
        return trading_service.get_open_orders(profile_id, include_executions=True)

    def _virtual_submit(order: Dict[str, Any]) -> Dict[str, Any]:
        if order.get("action") == "cancel":
            return trading_service.cancel_order(
                order_id=order.get("order_id", ""),
                profile_id=profile_id,
                symbol=order.get("symbol"),
                session_id=context.session_id,
            )
        return trading_service.place_order(
            symbol=order.get("symbol", ""),
            profile_id=profile_id,
            side=order.get("side", ""),
            quantity=order.get("quantity"),
            notional=order.get("notional"),
            order_type=order.get("order_type", "market"),
            limit_price=order.get("limit_price"),
            time_in_force=order.get("time_in_force", "day"),
            session_id=context.session_id,
        )

    runner_holder: Dict[str, Any] = {}

    async def _virtual_on_fire(_job: Any) -> None:
        runner = runner_holder.get("runner")
        if runner is not None:
            await runner.run_once()

    scheduler = Scheduler(_virtual_on_fire)

    runner = LiveRunner(
        context.broker,
        agent_caller=context.agent_caller,
        reconcile_fn=context.reconcile_fn,
        read_positions=_virtual_read_positions,
        read_balance=_virtual_read_balance,
        read_open_orders=_virtual_read_open_orders,
        submit_fn=_virtual_submit,
        write_audit_fn=context.write_audit_fn,
        scheduler=scheduler,
        triggers=[Trigger.interval(interval_ms=60000)],
        session_id=context.session_id,
    )
    runner_holder["runner"] = runner
    return runner
