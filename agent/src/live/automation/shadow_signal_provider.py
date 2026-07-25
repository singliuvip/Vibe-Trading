"""ShadowSignalProvider — adapt the shadow-account scanner to the automation pipeline.

This is the first concrete :class:`SignalProvider` implementation. It bridges
the deterministic shadow-account scanner (``scan_today_signals``) to the
automation pipeline by converting scan matches into :class:`SignalCandidate`
objects.

The adapter is a pure protocol translator: it carries NO trading business logic
(sizing, risk, order placement all live downstream in the AutomationService).
It is fail-closed — a missing profile or scan error yields an empty signal list
rather than fabricated signals, because fabricated signals are the single
biggest risk to automated trading.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional, Sequence

from src.live.automation.models import SignalCandidate, SignalDirection

logger = logging.getLogger(__name__)

#: Signature of the injected profile loader (bound to shadow_account.load_profile).
LoadProfileFn = Callable[[str], Any]

#: Signature of the injected scanner (bound to shadow_account.scan_today_signals).
ScanFn = Callable[..., list[dict[str, Any]]]


def _default_generated_at() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class ShadowSignalProvider:
    """Adapt the shadow-account scanner to the SignalProvider protocol.

    Dependencies are injected for testability:
        shadow_id: The shadow profile id to load.
        load_profile_fn: Bound to shadow_account.storage.load_profile.
        scan_fn: Bound to shadow_account.scanner.scan_today_signals.
        strategy_version: Version hash stamped onto every signal (identifies
            the shadow profile revision).
        per_market: Cap on matches per market passed to the scanner.
        target_date: Optional fixed scan date (ISO string or date). None means
            the scanner uses today; inject for deterministic tests.
        generated_at_fn: Injectable clock for the signal timestamp (testing).
    """

    def __init__(
        self,
        shadow_id: str,
        *,
        load_profile_fn: LoadProfileFn,
        scan_fn: ScanFn,
        strategy_version: str = "shadow-v1",
        per_market: int = 3,
        target_date: Optional[str | date] = None,
        generated_at_fn: Callable[[], str] = _default_generated_at,
    ) -> None:
        self._shadow_id = shadow_id
        self._load_profile = load_profile_fn
        self._scan = scan_fn
        self._strategy_version = strategy_version
        self._per_market = per_market
        self._target_date = target_date
        self._generated_at_fn = generated_at_fn

    def scan(self) -> Sequence[SignalCandidate]:
        """Scan the shadow profile and return signal candidates (fail-closed).

        Returns:
            A list of SignalCandidate (possibly empty). Never raises for
            routine no-signal / missing-profile / scan-error conditions —
            those all yield an empty list so the AutomationRunner skips the
            cycle rather than trading on bad data.
        """
        # 1. Load profile (fail-closed → [] on any error).
        try:
            profile = self._load_profile(self._shadow_id)
        except Exception as exc:  # noqa: BLE001 — fail-closed, never fabricate
            logger.warning("shadow profile %s load failed: %s", self._shadow_id, exc)
            return []

        # 2. Scan (fail-closed → [] on any error).
        try:
            matches = self._scan(
                profile,
                target_date=self._target_date,
                per_market=self._per_market,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            logger.warning("shadow scan failed for %s: %s", self._shadow_id, exc)
            return []

        # 3. Convert matches to SignalCandidate.
        generated_at = self._generated_at_fn()
        generated_epoch_ms = _to_epoch_ms(generated_at)
        strategy_id = f"shadow:{self._shadow_id}"

        signals: list[SignalCandidate] = []
        for match in matches:
            try:
                signal = self._match_to_signal(
                    match,
                    strategy_id=strategy_id,
                    strategy_version=self._strategy_version,
                    generated_at=generated_at,
                    generated_epoch_ms=generated_epoch_ms,
                )
            except Exception as exc:  # noqa: BLE001 — skip malformed match, keep others
                logger.warning("skipping malformed shadow match %r: %s", match, exc)
                continue
            if signal is not None:
                signals.append(signal)
        return signals

    def _match_to_signal(
        self,
        match: dict[str, Any],
        *,
        strategy_id: str,
        strategy_version: str,
        generated_at: str,
        generated_epoch_ms: int,
    ) -> Optional[SignalCandidate]:
        """Convert one scan match dict to a SignalCandidate (None if invalid)."""
        symbol = str(match.get("symbol") or "").strip().upper()
        if not symbol:
            return None
        rule_id = str(match.get("rule_id") or "")
        market = str(match.get("market") or "")
        reason = str(match.get("reason") or "")

        # Shadow entry signals are long entries (buy candidates).
        direction = SignalDirection.LONG
        # Score: a match is a binary deterministic signal, so we use a fixed
        # high confidence (the gate's min_signal_score still applies downstream).
        score = 1.0

        idempotency_key = f"{strategy_id}:{symbol}:{generated_epoch_ms}"

        return SignalCandidate(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            symbol=symbol,
            direction=direction,
            score=score,
            generated_at=generated_at,
            idempotency_key=idempotency_key,
            metadata={
                "rule_id": rule_id,
                "market": market,
                "reason": reason,
                "source": "shadow_account",
            },
        )


def _to_epoch_ms(iso_timestamp: str) -> int:
    """Parse an ISO-8601 timestamp to epoch ms (0 if unparseable, fail-safe)."""
    if not iso_timestamp:
        return 0
    normalized = iso_timestamp.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
