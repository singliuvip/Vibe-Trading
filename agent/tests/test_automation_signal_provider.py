"""Tests for SignalProvider protocol and built-in implementations.

Covers:
- StaticSignalProvider.scan() returns a fixed list (and a copy, so mutating the
  returned list does not affect internal state).
- CallableSignalProvider.scan() delegates to the underlying callable.
- SignalProvider is a runtime_checkable Protocol (isinstance works).
"""

from __future__ import annotations

from src.live.automation.models import SignalCandidate, SignalDirection
from src.live.automation.signal_provider import (
    CallableSignalProvider,
    SignalProvider,
    StaticSignalProvider,
)


def _signal(**overrides) -> SignalCandidate:
    defaults = dict(
        strategy_id="v17.13",
        strategy_version="abc123",
        symbol="688598.SH",
        direction=SignalDirection.LONG,
        score=0.85,
        generated_at="2026-07-24T09:25:00Z",
        idempotency_key="v17.13:688598.SH:1784885100000",
        price_ref=27.55,
        target_quantity=1000.0,
    )
    defaults.update(overrides)
    return SignalCandidate(**defaults)


# --------------------------------------------------------------------------- #
# StaticSignalProvider                                                        #
# --------------------------------------------------------------------------- #


def test_static_provider_returns_fixed_signals() -> None:
    sig = _signal()
    provider = StaticSignalProvider([sig])
    result = provider.scan()
    assert list(result) == [sig]


def test_static_provider_empty() -> None:
    provider = StaticSignalProvider([])
    assert list(provider.scan()) == []


def test_static_provider_returns_copy_not_internal_reference() -> None:
    """Mutating the returned list must not affect the provider's internal state."""
    sig = _signal()
    provider = StaticSignalProvider([sig])

    first = provider.scan()
    assert list(first) == [sig]

    # Mutate the returned list (it is a fresh list copy).
    returned = list(provider.scan())
    returned.clear()

    # Internal state is unchanged.
    assert list(provider.scan()) == [sig]


def test_static_provider_constructor_copies_input() -> None:
    """Mutating the input sequence after construction must not affect the provider."""
    sig = _signal()
    source = [sig]
    provider = StaticSignalProvider(source)

    source.clear()

    assert list(provider.scan()) == [sig]


# --------------------------------------------------------------------------- #
# CallableSignalProvider                                                      #
# --------------------------------------------------------------------------- #


def test_callable_provider_delegates_to_callable() -> None:
    sig = _signal()
    calls: list[int] = []

    def fn():
        calls.append(1)
        return [sig]

    provider = CallableSignalProvider(fn)
    result = provider.scan()

    assert list(result) == [sig]
    assert calls == [1]


def test_callable_provider_called_each_scan() -> None:
    counter = {"n": 0}

    def fn():
        counter["n"] += 1
        return [_signal(score=0.5 + counter["n"] * 0.1)]

    provider = CallableSignalProvider(fn)
    provider.scan()
    provider.scan()
    assert counter["n"] == 2


# --------------------------------------------------------------------------- #
# Protocol conformance                                                        #
# --------------------------------------------------------------------------- #


def test_static_provider_is_signal_provider() -> None:
    assert isinstance(StaticSignalProvider([]), SignalProvider)


def test_callable_provider_is_signal_provider() -> None:
    assert isinstance(CallableSignalProvider(lambda: []), SignalProvider)


def test_plain_object_is_not_signal_provider() -> None:
    class NotAProvider:
        pass

    assert not isinstance(NotAProvider(), SignalProvider)
