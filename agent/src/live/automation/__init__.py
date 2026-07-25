"""Live-trading automation — structured signal-to-order contracts.

This package defines the deterministic data contracts for the automated
trading pipeline: SignalCandidate → DecisionGate → TradePlan → OrderIntent.
All models are frozen dataclasses (no Pydantic) following the project's
immutability convention for safety-critical data.
"""

from src.live.automation.bounds_check import (
    BoundsVerdict,
    check_automation_bounds,
    check_slippage,
)
from src.live.automation.decision_gate import evaluate_signal
from src.live.automation.executor import (
    AutomationExecutor,
    ExecutionResult,
    build_trade_plan,
)
from src.live.automation.models import (
    DecisionOutcome,
    ExecutionDecision,
    SignalCandidate,
    SignalDirection,
    TradePlan,
)
from src.live.automation.notifications import (
    format_cycle_notification,
    notify_cycle_result,
)
from src.live.automation.policy import AutomationMode, AutomationPolicy
from src.live.automation.runner import (
    AutomationRunner,
    AutomationRunnerConfig,
    CycleOutcome,
)
from src.live.automation.runtime_state import (
    AutomationRuntimeState,
    read_runtime_state,
)
from src.live.automation.service import AutomationCycleResult, AutomationService
from src.live.automation.signal_provider import (
    CallableSignalProvider,
    SignalProvider,
    StaticSignalProvider,
)
from src.live.automation.shadow_signal_provider import ShadowSignalProvider
from src.live.mandate.model import AutomationBounds

__all__ = [
    "AutomationBounds",
    "AutomationCycleResult",
    "AutomationExecutor",
    "AutomationMode",
    "AutomationPolicy",
    "AutomationRunner",
    "AutomationRunnerConfig",
    "AutomationRuntimeState",
    "AutomationService",
    "BoundsVerdict",
    "CallableSignalProvider",
    "CycleOutcome",
    "DecisionOutcome",
    "ExecutionDecision",
    "ExecutionResult",
    "SignalCandidate",
    "SignalDirection",
    "SignalProvider",
    "ShadowSignalProvider",
    "StaticSignalProvider",
    "TradePlan",
    "build_trade_plan",
    "check_automation_bounds",
    "check_slippage",
    "evaluate_signal",
    "format_cycle_notification",
    "notify_cycle_result",
    "read_runtime_state",
]
