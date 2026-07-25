"""AutomationRunner — deterministic schedule → scan → execute → notify loop.

This is the closed-loop orchestrator for automated trading. It is DISTINCT from
the LLM-driven LiveRunner (src.live.runtime.runner): the AutomationRunner is
fully deterministic and never consults the agent model.

Per scheduled fire (cron/interval), one cycle runs in fixed fail-closed order:

    1. Load policy (fail-closed → DISABLED on any store error).
    2. If policy mode is DISABLED → skip the cycle entirely (no scan, no orders).
    3. Scan signals via the injected SignalProvider (fail-closed → [] on error).
    4. Read runtime state (positions/account/orders) for bounds checks.
    5. Delegate to AutomationService.run_cycle (mandate gate + decision gate +
       executor + bounds). The service owns all order placement.
    6. Best-effort notify the cycle result (never affects trading).

The runner NEVER calls a broker connector directly — all orders flow through
AutomationService → service.place_order → mandate gate.

All side-effecting dependencies (scheduler, signal provider, service, runtime
reader, notifier, clock) are injected so the runner is unit-testable with fakes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from src.live.automation.models import SignalCandidate
from src.live.automation.policy import AutomationMode, AutomationPolicy
from src.live.automation.runtime_state import AutomationRuntimeState
from src.live.automation.service import AutomationCycleResult, AutomationService
from src.live.automation.signal_provider import SignalProvider
from src.live.mandate.model import AssetClass, InstrumentType

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


#: Signature of the injected runtime-state reader (bound to read_runtime_state).
ReadRuntimeStateFn = Callable[..., AutomationRuntimeState]

#: Signature of the injected notifier (bound to notify_cycle_result). Returns an
#: awaitable bool (True if published). May be None to skip notification.
NotifyFn = Callable[[AutomationCycleResult], Awaitable[bool]]


@dataclass(frozen=True)
class AutomationRunnerConfig:
    """Static configuration for one AutomationRunner instance.

    Attributes:
        broker: Broker key this runner drives.
        account_id: Optional account scope.
        instrument_type: Instrument type passed to the service for each cycle.
        asset_class: Optional asset class (e.g. CN_EQUITY for A-shares).
        profile_id: Optional connector profile id for order placement.
        schedule: Schedule spec for the cycle job — a 5-field cron string
            (e.g. "25 9 * * 1-5") or "interval:<ms>".
        job_id: Stable id for the scheduled job.
        order_type: Default order type for cycles ("limit"/"market").
        time_in_force: Default time-in-force ("day"/"gtc").
        stop_loss: Optional default stop-loss passed to the service.
        take_profit: Optional default take-profit.
        max_holding_seconds: Optional default max holding period.
    """

    broker: str
    instrument_type: InstrumentType
    schedule: str
    job_id: str = "automation-cycle"
    account_id: Optional[str] = None
    asset_class: Optional[AssetClass] = None
    profile_id: Optional[str] = None
    order_type: str = "limit"
    time_in_force: str = "day"
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    max_holding_seconds: Optional[int] = None


@dataclass
class CycleOutcome:
    """Mutable per-cycle bookkeeping returned by run_once for observability."""

    ran: bool
    skipped_reason: str = ""
    result: Optional[AutomationCycleResult] = None
    notified: bool = False
    error: Optional[str] = None


class AutomationRunner:
    """Deterministic closed-loop automation runner for one broker scope.

    Dependencies are injected:
        config: Static runner configuration.
        signal_provider: Source of SignalCandidate (fail-closed).
        service: AutomationService that owns order placement.
        read_runtime_state_fn: Bound to read_runtime_state (positions/account).
        load_policy_fn: Bound to policy_store.load_policy (fail-closed).
        scheduler: runtime.Scheduler used by run_loop (optional for run_once).
        notify_fn: Optional async notifier (best-effort).
        now_fn: Injectable clock returning epoch ms.
    """

    def __init__(
        self,
        config: AutomationRunnerConfig,
        *,
        signal_provider: SignalProvider,
        service: AutomationService,
        read_runtime_state_fn: ReadRuntimeStateFn,
        load_policy_fn: Callable[..., AutomationPolicy],
        scheduler: Any = None,
        notify_fn: Optional[NotifyFn] = None,
        now_fn: Callable[[], int] = _now_ms,
    ) -> None:
        self._config = config
        self._signal_provider = signal_provider
        self._service = service
        self._read_runtime_state = read_runtime_state_fn
        self._load_policy = load_policy_fn
        self._scheduler = scheduler
        self._notify = notify_fn
        self._now_fn = now_fn
        #: Persistent cross-cycle dedup/cooldown state (survives within a run).
        self._seen_keys: set[str] = set()
        self._cooldown_deadlines: dict[str, int] = {}
        #: Last cycle outcome for observability / status endpoint.
        self.last_outcome: Optional[CycleOutcome] = None

    async def run_once(self) -> CycleOutcome:
        """Run ONE automation cycle in fixed fail-closed order.

        Returns:
            A CycleOutcome describing what happened (ran/skipped/error).
        """
        cfg = self._config

        # 1. Load policy (fail-closed → DISABLED on store error).
        try:
            policy = self._load_policy(cfg.broker, cfg.account_id)
        except Exception as exc:  # noqa: BLE001 — fail-closed
            logger.exception("automation policy load failed for %s", cfg.broker)
            outcome = CycleOutcome(ran=False, skipped_reason="policy load failed", error=str(exc))
            self.last_outcome = outcome
            return outcome

        # 2. Disabled → skip entirely (no scan, no orders).
        if policy.mode is AutomationMode.DISABLED:
            outcome = CycleOutcome(ran=False, skipped_reason="automation disabled")
            self.last_outcome = outcome
            return outcome

        # 3. Scan signals (fail-closed → [] on provider error).
        try:
            signals = list(self._signal_provider.scan())
        except Exception as exc:  # noqa: BLE001 — fail-closed, never trade on error
            logger.exception("automation signal scan failed for %s", cfg.broker)
            outcome = CycleOutcome(ran=False, skipped_reason="signal scan failed", error=str(exc))
            self.last_outcome = outcome
            return outcome

        # 4. Read runtime state for bounds checks (fail-closed inside reader).
        try:
            runtime_state = self._read_runtime_state()
        except Exception as exc:  # noqa: BLE001 — fail-closed
            logger.exception("automation runtime state read failed for %s", cfg.broker)
            runtime_state = AutomationRuntimeState(available=False, error=str(exc))

        # 5. Delegate to the service (mandate gate + decision gate + executor).
        try:
            result = self._service.run_cycle(
                cfg.broker,
                signals,
                policy,
                instrument_type=cfg.instrument_type,
                asset_class=cfg.asset_class,
                account_id=cfg.account_id,
                profile_id=cfg.profile_id,
                seen_keys=self._seen_keys,
                cooldown_deadlines=self._cooldown_deadlines,
                stop_loss=cfg.stop_loss,
                take_profit=cfg.take_profit,
                max_holding_seconds=cfg.max_holding_seconds,
                order_type=cfg.order_type,
                time_in_force=cfg.time_in_force,
                runtime_state=runtime_state,
            )
        except Exception as exc:  # noqa: BLE001 — service should not raise, but fail-closed
            logger.exception("automation cycle failed for %s", cfg.broker)
            outcome = CycleOutcome(ran=True, error=str(exc))
            self.last_outcome = outcome
            return outcome

        # 6. Best-effort notify (never affects trading).
        notified = False
        if self._notify is not None:
            try:
                notified = await self._notify(result)
            except Exception:  # noqa: BLE001 — notification must never break trading
                logger.warning("automation notification failed for %s", cfg.broker, exc_info=True)

        outcome = CycleOutcome(ran=True, result=result, notified=notified)
        self.last_outcome = outcome
        return outcome

    async def _on_fire(self, job: Any) -> None:
        """Scheduler fire callback — run one cycle."""
        logger.info(
            "automation cycle fired for %s (job=%s)",
            self._config.broker, getattr(job, "id", "?"),
        )
        await self.run_once()

    def run_loop(self) -> None:
        """Start the scheduler with the configured cycle job (resume-via-recompute).

        Raises:
            RuntimeError: If no scheduler was injected.
        """
        if self._scheduler is None:
            raise RuntimeError("run_loop requires an injected scheduler")
        from src.live.runtime.scheduler import Job

        now_ms = self._now_fn()
        # For cron/interval, the scheduler's advance_after_fire computes the next
        # fire; anchor the first fire at now so the scheduler decides due-ness.
        job = Job(
            id=self._config.job_id,
            next_run_at=now_ms,
            schedule=self._config.schedule,
            payload={"broker": self._config.broker, "account_id": self._config.account_id},
        )
        self._scheduler.add_job(job)
        self._scheduler.start()
        logger.info(
            "automation runner started for %s (schedule=%s)",
            self._config.broker, self._config.schedule,
        )

    async def stop_loop(self) -> None:
        """Stop the scheduler if injected (idempotent).

        ``Scheduler.stop()`` is a coroutine, so this method is async and awaits
        it — a synchronous call would return an un-awaited coroutine and leave
        the scheduler running.
        """
        if self._scheduler is not None:
            await self._scheduler.stop()
