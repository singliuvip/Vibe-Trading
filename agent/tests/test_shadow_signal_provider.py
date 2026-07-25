"""Tests for ShadowSignalProvider — the shadow-account scanner adapter.

Uses fakes (no real shadow_account filesystem) to cover:
- Normal match → SignalCandidate conversion (fields, uppercasing, metadata).
- idempotency_key format.
- Fail-closed: profile load error → [] (scan not called); scan error → [].
- Empty match list → [].
- Malformed match (missing symbol) → skipped, valid ones kept.
- target_date / per_market passthrough to the scanner.
- generated_at injection → consistent timestamp + epoch_ms in idempotency_key.
- Protocol conformance (isinstance SignalProvider).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.live.automation.models import SignalCandidate, SignalDirection
from src.live.automation.shadow_signal_provider import (
    ShadowSignalProvider,
    _to_epoch_ms,
)
from src.live.automation.signal_provider import SignalProvider

# --------------------------------------------------------------------------- #
# Fixed clock                                                                 #
# --------------------------------------------------------------------------- #

FIXED_TIME = "2026-07-24T09:25:00+00:00"
FIXED_EPOCH_MS = int(datetime.fromisoformat(FIXED_TIME).timestamp() * 1000)


def _fixed_generated_at() -> str:
    return FIXED_TIME


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #


class _FakeProfile:
    """Stand-in for ShadowProfile (the adapter treats it opaquely)."""

    def __init__(self, shadow_id: str = "sh-1") -> None:
        self.shadow_id = shadow_id


def _make_load_profile(
    profile: Any | None = None, error: Exception | None = None
):
    """Build a fake load_profile_fn that returns a profile or raises."""
    calls: list[str] = []

    def fn(shadow_id: str):
        calls.append(shadow_id)
        if error is not None:
            raise error
        return profile if profile is not None else _FakeProfile(shadow_id)

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


class _FakeScanner:
    """Fake scan_today_signals that records call kwargs and returns matches."""

    def __init__(
        self, matches: list[dict[str, Any]] | None = None, error: Exception | None = None
    ) -> None:
        self.matches = matches or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, profile, *, target_date=None, per_market=3):
        self.calls.append(
            {"profile": profile, "target_date": target_date, "per_market": per_market}
        )
        if self.error is not None:
            raise self.error
        return self.matches


def _make_provider(
    *,
    shadow_id: str = "sh-1",
    matches: list[dict[str, Any]] | None = None,
    load_error: Exception | None = None,
    scan_error: Exception | None = None,
    per_market: int = 3,
    target_date=None,
    strategy_version: str = "shadow-v1",
):
    """Construct a ShadowSignalProvider wired to fakes with a fixed clock."""
    load_fn = _make_load_profile(error=load_error)
    scanner = _FakeScanner(matches=matches, error=scan_error)
    provider = ShadowSignalProvider(
        shadow_id,
        load_profile_fn=load_fn,
        scan_fn=scanner,
        strategy_version=strategy_version,
        per_market=per_market,
        target_date=target_date,
        generated_at_fn=_fixed_generated_at,
    )
    return provider, load_fn, scanner


# --------------------------------------------------------------------------- #
# Normal conversion                                                           #
# --------------------------------------------------------------------------- #


def test_normal_conversion_two_matches() -> None:
    matches = [
        {"symbol": "600519.sh", "market": "china_a", "rule_id": "r1", "reason": "breakout"},
        {"symbol": "AAPL", "market": "us", "rule_id": "r2", "reason": "mean-revert"},
    ]
    provider, _, _ = _make_provider(matches=matches)

    signals = provider.scan()

    assert len(signals) == 2
    assert all(isinstance(s, SignalCandidate) for s in signals)

    s0 = signals[0]
    assert s0.symbol == "600519.SH"  # uppercased
    assert s0.direction is SignalDirection.LONG
    assert s0.score == 1.0
    assert s0.strategy_id == "shadow:sh-1"
    assert s0.strategy_version == "shadow-v1"
    assert s0.generated_at == FIXED_TIME
    assert s0.metadata == {
        "rule_id": "r1",
        "market": "china_a",
        "reason": "breakout",
        "source": "shadow_account",
    }

    s1 = signals[1]
    assert s1.symbol == "AAPL"
    assert s1.metadata["rule_id"] == "r2"
    assert s1.metadata["market"] == "us"


def test_symbol_uppercasing() -> None:
    provider, _, _ = _make_provider(
        matches=[{"symbol": "600519.sh", "market": "china_a", "rule_id": "r", "reason": "x"}]
    )
    signals = provider.scan()
    assert signals[0].symbol == "600519.SH"


# --------------------------------------------------------------------------- #
# idempotency_key                                                             #
# --------------------------------------------------------------------------- #


def test_idempotency_key_format() -> None:
    provider, _, _ = _make_provider(
        matches=[{"symbol": "aapl", "market": "us", "rule_id": "r", "reason": "x"}]
    )
    signals = provider.scan()
    assert signals[0].idempotency_key == f"shadow:sh-1:AAPL:{FIXED_EPOCH_MS}"


def test_generated_at_injection_consistent_epoch() -> None:
    """Fixed clock → generated_at and idempotency_key epoch_ms agree."""
    provider, _, _ = _make_provider(
        matches=[{"symbol": "AAPL", "market": "us", "rule_id": "r", "reason": "x"}]
    )
    signals = provider.scan()
    sig = signals[0]
    assert sig.generated_at == FIXED_TIME
    # epoch_ms embedded in the key equals the independently computed value.
    assert sig.idempotency_key.endswith(f":{FIXED_EPOCH_MS}")
    assert _to_epoch_ms(sig.generated_at) == FIXED_EPOCH_MS


# --------------------------------------------------------------------------- #
# Fail-closed                                                                 #
# --------------------------------------------------------------------------- #


def test_profile_load_failure_returns_empty_and_skips_scan() -> None:
    provider, load_fn, scanner = _make_provider(load_error=FileNotFoundError("absent"))

    signals = provider.scan()

    assert list(signals) == []
    assert load_fn.calls == ["sh-1"]  # load attempted
    assert scanner.calls == []  # scan NOT called


def test_scan_error_returns_empty() -> None:
    provider, _, scanner = _make_provider(scan_error=RuntimeError("boom"))

    signals = provider.scan()

    assert list(signals) == []
    assert len(scanner.calls) == 1  # scan was attempted


# --------------------------------------------------------------------------- #
# Empty / malformed                                                           #
# --------------------------------------------------------------------------- #


def test_empty_match_list_returns_empty() -> None:
    provider, _, _ = _make_provider(matches=[])
    assert list(provider.scan()) == []


def test_malformed_match_missing_symbol_skipped() -> None:
    matches = [
        {"symbol": "", "market": "us", "rule_id": "r1", "reason": "no-symbol"},
        {"market": "us", "rule_id": "r2", "reason": "also-no-symbol"},
        {"symbol": "AAPL", "market": "us", "rule_id": "r3", "reason": "valid"},
    ]
    provider, _, _ = _make_provider(matches=matches)

    signals = provider.scan()

    assert len(signals) == 1
    assert signals[0].symbol == "AAPL"
    assert signals[0].metadata["rule_id"] == "r3"


def test_whitespace_only_symbol_skipped() -> None:
    provider, _, _ = _make_provider(
        matches=[{"symbol": "   ", "market": "us", "rule_id": "r", "reason": "x"}]
    )
    assert list(provider.scan()) == []


def test_none_symbol_skipped_not_stringified() -> None:
    """A match whose symbol key is present but None must be skipped, not turned into 'NONE'."""
    matches = [
        {"symbol": None, "market": "us", "rule_id": "r1", "reason": "none-symbol"},
        {"symbol": "AAPL", "market": "us", "rule_id": "r2", "reason": "valid"},
    ]
    provider, _, _ = _make_provider(matches=matches)

    signals = provider.scan()

    assert len(signals) == 1
    assert signals[0].symbol == "AAPL"
    # Ensure no signal with symbol "NONE" was produced.
    assert all(s.symbol != "NONE" for s in signals)


def test_none_metadata_values_become_empty_strings() -> None:
    """None rule_id/market/reason values become '' in metadata, not 'None'."""
    matches = [
        {"symbol": "AAPL", "market": None, "rule_id": None, "reason": None},
    ]
    provider, _, _ = _make_provider(matches=matches)

    signals = provider.scan()

    assert len(signals) == 1
    assert signals[0].metadata["rule_id"] == ""
    assert signals[0].metadata["market"] == ""
    assert signals[0].metadata["reason"] == ""


# --------------------------------------------------------------------------- #
# Passthrough args                                                            #
# --------------------------------------------------------------------------- #


def test_target_date_and_per_market_passed_to_scanner() -> None:
    provider, _, scanner = _make_provider(
        matches=[], target_date="2026-07-01", per_market=5
    )
    provider.scan()

    assert len(scanner.calls) == 1
    call = scanner.calls[0]
    assert call["target_date"] == "2026-07-01"
    assert call["per_market"] == 5


def test_profile_object_passed_to_scanner() -> None:
    profile = _FakeProfile("custom")
    load_fn = _make_load_profile(profile=profile)
    scanner = _FakeScanner(matches=[])
    provider = ShadowSignalProvider(
        "custom",
        load_profile_fn=load_fn,
        scan_fn=scanner,
        generated_at_fn=_fixed_generated_at,
    )
    provider.scan()
    assert scanner.calls[0]["profile"] is profile


# --------------------------------------------------------------------------- #
# Protocol conformance                                                        #
# --------------------------------------------------------------------------- #


def test_is_signal_provider() -> None:
    provider, _, _ = _make_provider()
    assert isinstance(provider, SignalProvider)


# --------------------------------------------------------------------------- #
# _to_epoch_ms helper                                                         #
# --------------------------------------------------------------------------- #


def test_to_epoch_ms_z_suffix() -> None:
    assert _to_epoch_ms("2026-07-24T09:25:00Z") == _to_epoch_ms(FIXED_TIME)


def test_to_epoch_ms_invalid_returns_zero() -> None:
    assert _to_epoch_ms("not-a-date") == 0
    assert _to_epoch_ms("") == 0
