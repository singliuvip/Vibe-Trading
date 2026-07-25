"""Live-trading automation control-plane HTTP routes.

Mounted by ``agent/api_server.py`` via ``register_automation_routes(app)``.

These are privileged SURFACE actions for the automation pipeline. None is an
agent tool:

- ``GET  /live/automation/status``  — policy mode + mandate automation_bounds + readiness
- ``POST /live/automation/enable``  — set policy mode to paper_auto/live_bounded (requires ack)
- ``POST /live/automation/disable`` — set policy mode to disabled
- ``POST /live/automation/start``   — start the persistent AutomationRunner (privileged)
- ``POST /live/automation/stop``    — stop the persistent AutomationRunner (safe)

Enabling automation is gated on a valid mandate WITH automation_bounds (a v1
mandate cannot enable automation). All paths are fail-closed.
"""

from __future__ import annotations

import asyncio
import logging
import sys as _sys
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ============================================================================
# Module-level runner task registry (independent from LiveRunner's _runner_tasks)
# ============================================================================

#: broker key -> asyncio.Task driving an AutomationRunner.run_loop().
#: Kept separate from LiveRunner's ``_runner_tasks`` so the two loops never
#: collide on the same key and a stop of one never touches the other.
_automation_runner_tasks: Dict[str, "asyncio.Task[Any]"] = {}

#: broker key -> AutomationRunner instance (parallel to _automation_runner_tasks).
#: Holds the runner reference so the stop endpoint can await ``runner.stop_loop()``
#: to release the scheduler cleanly (an asyncio.Task does not expose its target's
#: receiver).
_automation_runner_holder: Dict[str, Any] = {}


# ============================================================================ 
# Pydantic request models
# ============================================================================


class AutomationEnableRequest(BaseModel):
    """Enable automation for a broker scope (privileged, requires ack).

    ``mode`` must be ``paper_auto`` or ``live_bounded``. ``ack`` MUST be true —
    an explicit affirmative like the mandate commit's consent_ack.
    """

    broker: str = Field(..., min_length=1, max_length=64)
    account_id: Optional[str] = Field(None, max_length=64)
    mode: str = Field(..., pattern=r"^(paper_auto|live_bounded)$")
    ack: bool = Field(..., description="Explicit affirmative; must be true")
    session_id: Optional[str] = None


class AutomationDisableRequest(BaseModel):
    """Disable automation for a broker scope (privileged)."""

    broker: str = Field(..., min_length=1, max_length=64)
    account_id: Optional[str] = Field(None, max_length=64)
    session_id: Optional[str] = None


class AutomationRunnerControlRequest(BaseModel):
    """Start or stop the automation runner for one broker scope.

    Starting the runner is a privileged surface action. A committed, unexpired
    mandate WITH automation_bounds must already exist, and the policy mode must
    not be DISABLED.
    """

    broker: str = Field(..., min_length=1, max_length=64)
    account_id: Optional[str] = Field(None, max_length=64)
    session_id: Optional[str] = None
    schedule: Optional[str] = Field(
        None,
        max_length=64,
        description=(
            "Cron expression (5-field, UTC) or 'interval:<ms>' for the cycle cadence. "
            "E.g. '25 9 * * 1-5' for weekdays 09:25 UTC, '30 1 * * 1-5' for A-share "
            "09:30 CST. Defaults to '0 9 * * 1-5' when omitted."
        ),
    )
    shadow_id: Optional[str] = Field(
        None,
        max_length=64,
        description=(
            "Shadow profile id for the signal source. "
            "Defaults to 'shadow_{broker}' when omitted."
        ),
    )


# ============================================================================
# Host resolution (monkeypatch-safe, mirrors live_routes)
# ============================================================================


def _host() -> Any:
    return _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")


# ============================================================================
# AutomationRunner wiring (factory + driver)
# ============================================================================


def _load_shadow_profile(shadow_id: str) -> Any:
    """Load a shadow profile by id (fail-closed wrapper)."""
    from src.shadow_account.storage import load_profile

    return load_profile(shadow_id)


def _scan_shadow_signals(profile: Any, **kwargs: Any) -> list:
    """Scan shadow signals (fail-closed wrapper)."""
    from src.shadow_account.scanner import scan_today_signals

    return scan_today_signals(profile, **kwargs)


def _build_automation_runner(
    broker: str,
    account_id: Optional[str] = None,
    schedule: Optional[str] = None,
    shadow_id: Optional[str] = None,
) -> Any:
    """Construct a fully-wired AutomationRunner for a broker scope.

    Wires the runner to the real surfaces:
    - ShadowSignalProvider (bound to shadow_account scanner + storage)
    - AutomationService (bound to trading.service.place_order + mandate store)
    - read_runtime_state (bound to trading.service get_positions/account/orders)
    - policy_store.load_policy (fail-closed)
    - runtime.Scheduler (cron/interval)
    - notify_cycle_result (bound to MessageBus, best-effort)

    Raises:
        HTTPException: When the broker has no mandate with automation_bounds,
            the mandate is expired, or the policy mode is DISABLED.
    """
    from datetime import datetime, timezone

    from src.live.automation.policy_store import load_policy
    from src.live.automation.runner import AutomationRunner, AutomationRunnerConfig
    from src.live.automation.runtime_state import read_runtime_state
    from src.live.automation.service import AutomationService
    from src.live.automation.shadow_signal_provider import ShadowSignalProvider
    from src.live.mandate.model import AssetClass, InstrumentType
    from src.live.mandate.store import load_mandate
    from src.live.runtime.runner import _mandate_is_expired
    from src.live.runtime.scheduler import Scheduler
    from src.trading.service import (
        get_account,
        get_open_orders,
        get_positions,
        place_order,
    )

    # 1. Validate mandate has automation_bounds (fail-closed).
    mandate = load_mandate(broker, account_id)
    if mandate is None:
        raise HTTPException(
            status_code=409,
            detail=f"no committed mandate for {broker}; commit a mandate first",
        )
    if mandate.automation_bounds is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"mandate for {broker} has no automation_bounds (v1 mandate); "
                "automation requires a v2 mandate with automation_bounds"
            ),
        )
    if _mandate_is_expired(mandate, datetime.now(timezone.utc)):
        raise HTTPException(
            status_code=409,
            detail=f"mandate for {broker} has expired; re-authorize first",
        )

    # 2. Load policy (fail-closed → DISABLED).
    policy = load_policy(broker, account_id)
    if policy.mode.value == "disabled":
        raise HTTPException(
            status_code=409,
            detail="automation policy is disabled; enable first",
        )

    # 3. Determine instrument_type / asset_class from broker key.
    #    Default to EQUITY / CN_EQUITY for A-share brokers, US_EQUITY otherwise.
    #    A future step can make this configurable via the mandate.
    instrument_type = InstrumentType.EQUITY
    # Derive asset_class from the mandate's universe when available (more
    # accurate than broker-name string matching); fall back to a heuristic.
    if mandate.universe.asset_classes:
        asset_class = mandate.universe.asset_classes[0]
    else:
        asset_class = (
            AssetClass.CN_EQUITY
            if ("cn" in broker or "virtual" in broker)
            else AssetClass.US_EQUITY
        )

    # 4. Build the signal provider (ShadowSignalProvider bound to shadow_account).
    effective_shadow_id = shadow_id or f"shadow_{broker}"
    signal_provider = ShadowSignalProvider(
        shadow_id=effective_shadow_id,
        load_profile_fn=_load_shadow_profile,
        scan_fn=_scan_shadow_signals,
    )

    # 5. Build the service (bound to trading.service.place_order + mandate store).
    service = AutomationService(
        place_order_fn=place_order,
        load_mandate_fn=load_mandate,
    )

    # 6. Build the runtime-state reader (bound to trading.service reads).
    #    Resolve the connector profile_id the same way LiveRunner does
    #    (connector_profile_id_for_broker), so reads route to the right account.
    from src.trading.service import connector_profile_id_for_broker

    try:
        profile_id = connector_profile_id_for_broker(broker)
    except Exception:  # noqa: BLE001 — fail-closed: no profile → None
        profile_id = None

    def _read_runtime_state() -> Any:
        return read_runtime_state(
            read_positions=lambda: get_positions(profile_id),
            read_account=lambda: get_account(profile_id),
            read_open_orders=lambda: get_open_orders(profile_id),
        )

    # 7. Build the scheduler with on_fire bound to the runner (wired below).
    runner_holder: Dict[str, Any] = {}

    async def _on_fire(_job: Any) -> None:
        runner = runner_holder.get("runner")
        if runner is not None:
            await runner.run_once()

    scheduler = Scheduler(_on_fire)

    # 8. Build the notify function (best-effort, bound to MessageBus).
    h = _host()
    notify_fn: Optional[Any] = None
    if h is not None:
        bus = getattr(h, "_channel_bus", None)
        if bus is not None:
            async def _notify(result: Any) -> bool:
                from src.live.automation.notifications import notify_cycle_result

                return await notify_cycle_result(
                    result, publish_outbound=bus.publish_outbound
                )

            notify_fn = _notify

    # 9. Build the config + runner.
    config = AutomationRunnerConfig(
        broker=broker,
        instrument_type=instrument_type,
        schedule=schedule or "0 9 * * 1-5",  # default: every weekday 09:00 UTC
        account_id=account_id,
        asset_class=asset_class,
        profile_id=profile_id,
    )
    runner = AutomationRunner(
        config,
        signal_provider=signal_provider,
        service=service,
        read_runtime_state_fn=_read_runtime_state,
        load_policy_fn=load_policy,
        scheduler=scheduler,
        notify_fn=notify_fn,
    )
    runner_holder["runner"] = runner
    return runner


async def _drive_automation_runner(runner: Any) -> None:
    """Run an AutomationRunner's run_loop to completion.

    ``run_loop`` is synchronous (it calls ``scheduler.start()`` which spawns a
    task on the running event loop). We still tolerate a coroutine return for
    forward compatibility, mirroring the LiveRunner driver shape.
    """
    result = runner.run_loop()
    if asyncio.iscoroutine(result):
        await result
    else:
        await asyncio.get_running_loop().run_in_executor(None, lambda: result)


# ============================================================================
# Route registration
# ============================================================================


def register_automation_routes(
    app: FastAPI,
    require_auth: Any = None,
) -> None:
    """Mount the automation control-plane routes onto ``app``."""
    h = _host()
    if h is None:
        raise RuntimeError(
            "register_automation_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )
    if require_auth is None:
        require_auth = h.require_auth

    @app.get("/live/automation/status", dependencies=[Depends(require_auth)])
    async def automation_status_endpoint(broker: str, account_id: Optional[str] = None):
        """Return automation readiness: policy mode, mandate bounds, enablement."""
        from src.live.automation.policy_store import load_policy
        from src.live.mandate.store import load_mandate

        key = broker.strip().lower()
        if not key:
            raise HTTPException(status_code=400, detail="broker must not be blank")

        policy = load_policy(key, account_id)
        mandate = load_mandate(key, account_id)

        has_mandate = mandate is not None
        has_automation_bounds = bool(mandate is not None and mandate.automation_bounds is not None)
        mandate_expired = False
        if mandate is not None:
            from datetime import datetime, timezone

            from src.live.runtime.runner import _mandate_is_expired

            mandate_expired = _mandate_is_expired(mandate, datetime.now(timezone.utc))

        # Automation is effectively active only when: mode != disabled AND
        # mandate has automation_bounds AND mandate not expired.
        effectively_active = (
            policy.mode.value != "disabled"
            and has_automation_bounds
            and not mandate_expired
        )

        return {
            "broker": key,
            "account_id": account_id,
            "policy_mode": policy.mode.value,
            "min_signal_score": policy.min_signal_score,
            "max_orders_per_cycle": policy.max_orders_per_cycle,
            "symbol_cooldown_seconds": policy.symbol_cooldown_seconds,
            "require_exit_protection": policy.require_exit_protection,
            "allowed_strategies": list(policy.allowed_strategies),
            "has_mandate": has_mandate,
            "has_automation_bounds": has_automation_bounds,
            "mandate_expired": mandate_expired,
            "effectively_active": effectively_active,
        }

    @app.post("/live/automation/enable", dependencies=[Depends(require_auth)])
    async def automation_enable_endpoint(payload: AutomationEnableRequest):
        """Enable automation (paper_auto/live_bounded). Requires ack + valid mandate bounds."""
        from datetime import datetime, timezone

        from src.live.automation.policy import AutomationMode, AutomationPolicy
        from src.live.automation.policy_store import load_policy, save_policy
        from src.live.halt import halt_flag_set
        from src.live.mandate.store import load_mandate
        from src.live.runtime.runner import _mandate_is_expired

        if payload.ack is not True:
            raise HTTPException(status_code=400, detail="ack must be true to enable automation")

        key = payload.broker.strip().lower()
        if not key:
            raise HTTPException(status_code=400, detail="broker must not be blank")

        # Kill switch check — never enable while halted.
        if halt_flag_set(broker=key) or halt_flag_set(broker=None):
            raise HTTPException(
                status_code=409,
                detail="kill switch is tripped; resume before enabling automation",
            )

        # Mandate gate — automation requires a mandate WITH automation_bounds.
        mandate = load_mandate(key, payload.account_id)
        if mandate is None:
            raise HTTPException(
                status_code=409,
                detail=f"no committed mandate for {key}; commit a mandate first",
            )
        if mandate.automation_bounds is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"mandate for {key} has no automation_bounds (v1 mandate); "
                    "automation requires a v2 mandate with automation_bounds"
                ),
            )

        # Expiry check.
        if _mandate_is_expired(mandate, datetime.now(timezone.utc)):
            raise HTTPException(
                status_code=409,
                detail=f"mandate for {key} has expired; re-authorize first",
            )

        # Load current policy, swap mode, persist.
        current = load_policy(key, payload.account_id)
        new_mode = AutomationMode(payload.mode)
        updated = AutomationPolicy(
            mode=new_mode,
            min_signal_score=current.min_signal_score,
            max_signal_age_seconds=current.max_signal_age_seconds,
            require_exit_protection=current.require_exit_protection,
            allowed_strategies=current.allowed_strategies,
            max_orders_per_cycle=current.max_orders_per_cycle,
            symbol_cooldown_seconds=current.symbol_cooldown_seconds,
        )
        path = save_policy(updated, key, payload.account_id)

        h._emit_live_event(
            payload.session_id,
            "live.action",
            {"kind": "automation_enabled", "broker": key, "mode": new_mode.value},
        )
        return {
            "broker": key,
            "account_id": payload.account_id,
            "enabled": True,
            "mode": new_mode.value,
            "policy_path": str(path),
        }

    @app.post("/live/automation/disable", dependencies=[Depends(require_auth)])
    async def automation_disable_endpoint(payload: AutomationDisableRequest):
        """Disable automation (set mode to disabled)."""
        from src.live.automation.policy import AutomationMode, AutomationPolicy
        from src.live.automation.policy_store import load_policy, save_policy

        key = payload.broker.strip().lower()
        if not key:
            raise HTTPException(status_code=400, detail="broker must not be blank")

        current = load_policy(key, payload.account_id)
        updated = AutomationPolicy(
            mode=AutomationMode.DISABLED,
            min_signal_score=current.min_signal_score,
            max_signal_age_seconds=current.max_signal_age_seconds,
            require_exit_protection=current.require_exit_protection,
            allowed_strategies=current.allowed_strategies,
            max_orders_per_cycle=current.max_orders_per_cycle,
            symbol_cooldown_seconds=current.symbol_cooldown_seconds,
        )
        path = save_policy(updated, key, payload.account_id)

        h._emit_live_event(
            payload.session_id,
            "live.action",
            {"kind": "automation_disabled", "broker": key},
        )
        return {
            "broker": key,
            "account_id": payload.account_id,
            "enabled": False,
            "mode": "disabled",
            "policy_path": str(path),
        }

    @app.post("/live/automation/start", dependencies=[Depends(require_auth)])
    async def automation_start_endpoint(payload: AutomationRunnerControlRequest):
        """Start the persistent automation runner for a broker scope.

        Privileged surface action. Pre-flight checks (fail-closed):
        1. Kill switch not tripped (broker-scoped + global).
        2. A committed, unexpired mandate WITH automation_bounds exists.
        3. Policy mode is not DISABLED.

        These mirror the enable endpoint's gate so a runner cannot be started
        in a state the enable endpoint would refuse.
        """
        from src.live.halt import halt_flag_set

        key = payload.broker.strip().lower()
        if not key:
            raise HTTPException(status_code=400, detail="broker must not be blank")

        # 1. Kill switch check — never start while halted.
        if halt_flag_set(broker=key) or halt_flag_set(broker=None):
            raise HTTPException(
                status_code=409,
                detail="kill switch is tripped; resume before starting automation",
            )

        # Idempotency: a still-running task for this broker is a no-op.
        existing = _automation_runner_tasks.get(key)
        if existing is not None and not existing.done():
            return {"broker": key, "started": False, "already_running": True}

        # 2 + 3. Mandate + automation_bounds + expiry + policy gate (inside the
        #    factory, which raises HTTPException on any failure).
        try:
            runner = _build_automation_runner(
                key, payload.account_id,
                schedule=payload.schedule,
                shadow_id=payload.shadow_id,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — surface a clean 500
            raise HTTPException(
                status_code=500,
                detail=f"could not construct automation runner: {exc}",
            ) from exc

        # Hold the runner reference so the stop endpoint can await stop_loop().
        _automation_runner_holder[key] = runner
        task = asyncio.ensure_future(_drive_automation_runner(runner))
        _automation_runner_tasks[key] = task
        task.add_done_callback(
            lambda t, b=key: (
                _automation_runner_tasks.pop(b, None)
                if _automation_runner_tasks.get(b) is t
                else None,
                _automation_runner_holder.pop(b, None),
            )
        )

        h._emit_live_event(
            payload.session_id,
            "live.action",
            {"kind": "automation_runner_started", "broker": key},
        )
        return {"broker": key, "started": True, "already_running": False}

    @app.post("/live/automation/stop", dependencies=[Depends(require_auth)])
    async def automation_stop_endpoint(payload: AutomationRunnerControlRequest):
        """Stop the persistent automation runner for a broker scope.

        Unconditionally safe: cancelling a non-running task is a no-op. The
        runner's ``stop_loop`` is awaited best-effort to release the scheduler;
        any failure is swallowed so the endpoint never fails to return.
        """
        key = payload.broker.strip().lower()
        if not key:
            raise HTTPException(status_code=400, detail="broker must not be blank")

        task = _automation_runner_tasks.pop(key, None)
        runner = _automation_runner_holder.pop(key, None)
        if task is None or task.done():
            return {"broker": key, "stopped": False, "was_running": False}

        task.cancel()
        # Best-effort: await the runner's stop_loop to release the scheduler.
        if runner is not None:
            try:
                await runner.stop_loop()
            except Exception:  # noqa: BLE001 — stop must never fail the endpoint
                logger.warning("automation runner stop_loop failed for %s", key, exc_info=True)

        h._emit_live_event(
            payload.session_id,
            "live.action",
            {"kind": "automation_runner_stopped", "broker": key},
        )
        return {"broker": key, "stopped": True, "was_running": True}
