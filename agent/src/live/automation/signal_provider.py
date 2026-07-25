"""SignalProvider protocol — the pluggable strategy-scan source.

The automation pipeline is deterministic and strategy-agnostic: it consumes
structured :class:`SignalCandidate` objects produced by a SignalProvider. The
actual strategy adapter (backtest SignalEngine, shadow-account scanner, factor
zoo, etc.) is injected by the caller; this module only defines the contract.

A SignalProvider MUST be deterministic and fail-closed: when it cannot produce
reliable signals (missing data, stale bars, errors) it returns an empty list
rather than fabricated signals. Fabricated signals are the single biggest risk
to automated trading, so providers must never guess.
"""

from __future__ import annotations

from typing import Callable, Protocol, Sequence, runtime_checkable

from src.live.automation.models import SignalCandidate


@runtime_checkable
class SignalProvider(Protocol):
    """A deterministic source of trading signals.

    Implementations adapt a concrete strategy engine to the automation
    pipeline. They must be pure with respect to broker state (never place
    orders themselves) and fail-closed (return [] on any uncertainty).
    """

    def scan(self) -> Sequence[SignalCandidate]:
        """Scan and return current signal candidates (possibly empty).

        Returns:
            A sequence of SignalCandidate. Empty when no reliable signal is
            available. Must never raise for routine no-signal conditions;
            raise only for hard misconfiguration the caller should surface.
        """
        ...


class StaticSignalProvider:
    """A SignalProvider that returns a fixed list of signals.

    Useful for tests and for callers that compute signals out-of-band and want
    to feed them into the automation pipeline.
    """

    def __init__(self, signals: Sequence[SignalCandidate]) -> None:
        self._signals = list(signals)

    def scan(self) -> Sequence[SignalCandidate]:
        return list(self._signals)


class CallableSignalProvider:
    """Adapt a plain callable ``() -> Sequence[SignalCandidate]`` to the protocol."""

    def __init__(self, fn: Callable[[], Sequence[SignalCandidate]]) -> None:
        self._fn = fn

    def scan(self) -> Sequence[SignalCandidate]:
        return self._fn()
