"""Read-only mandate loader.

This module exposes exactly ONE public function, :func:`load_mandate`. There is
deliberately NO ``save_mandate`` / ``set_mandate`` here or in any module
importable by the agent loop or registered as a tool — mandate writes happen
only in the consent UX commit path (``src.live.mandate.commit``, owned by a
separate parcel), which is not reachable from ``loop.py`` / ``worker.py`` /
any ``BaseTool.execute()``. This mirrors the #142 swarm-config trust template:
the protected file is resolved at boot from a fixed user-side path, never from
caller input, agent tool args, or session ``variables`` (see
the live-trading SPEC §3 trust invariant, Mandate §2).

Loading is fail-closed: a missing file, malformed JSON, or a structurally
invalid record yields ``None`` so the enforcement gate (P5) denies all orders
rather than guessing at an unrecognized contract. ``schema_version`` is parsed
and returned as-is; the gate compares it against
:data:`~src.live.mandate.model.MANDATE_SCHEMA_VERSION` and fail-closes on a
mismatch (so an unknown future version is surfaced at the gate, not silently
coerced here).
"""

from __future__ import annotations

import json
import logging

from src.live.mandate.model import (
    AssetClass,
    ConsentMeta,
    HardCaps,
    InstrumentType,
    Mandate,
    UniverseConstraint,
)
from src.live.paths import broker_dir

logger = logging.getLogger(__name__)

_MANDATE_FILENAME = "mandate.json"


def load_mandate(broker: str, account_id: str | None = None) -> Mandate | None:
    """Load the committed mandate for a scope from the protected store.

    Reads ``<runtime_root>/live/<broker>/mandate.json`` (or
    ``<runtime_root>/live/<broker>/<account_id>/mandate.json`` when
    ``account_id`` is provided). Written 0600 by the consent commit path.
    Parsing is strict and fail-closed: any absent file, unreadable file,
    malformed JSON, or structurally invalid record returns ``None`` so the
    gate denies. ``expires_at`` and ``schema_version`` are carried through
    verbatim for the gate to evaluate.

    When ``account_id`` is provided and no scoped mandate exists, automatic
    migration of a legacy broker-level mandate is attempted (idempotent).

    Args:
        broker: Broker key, e.g. ``"robinhood"``.
        account_id: Optional account id for per-account mandate isolation.
            When None, uses the broker-level path (backward-compatible).

    Returns:
        The committed :class:`~src.live.mandate.model.Mandate`, or ``None``
        when no valid mandate is on file.
    """
    if account_id is not None:
        path = broker_dir(broker, account_id) / _MANDATE_FILENAME
        if not path.is_file():
            # Auto-migrate legacy broker-level mandate to scoped path (idempotent).
            migrate_legacy_mandate(broker, account_id)
            path = broker_dir(broker, account_id) / _MANDATE_FILENAME
            if not path.is_file():
                return None
    else:
        path = broker_dir(broker) / _MANDATE_FILENAME
        if not path.is_file():
            return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("live mandate for %s is unreadable/invalid JSON: %s", broker, exc)
        return None
    try:
        return _parse_mandate(raw)
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("live mandate for %s failed structural validation: %s", broker, exc)
        return None


def _parse_mandate(raw: object) -> Mandate:
    """Build a :class:`Mandate` from a decoded JSON object (strict).

    Raises:
        KeyError: A required field is missing.
        TypeError: ``raw`` (or a nested section) is not an object.
        ValueError: An enum value or numeric field cannot be parsed.
    """
    if not isinstance(raw, dict):
        raise TypeError("mandate root must be a JSON object")

    caps = _require_dict(raw["hard_caps"], "hard_caps")
    universe = _require_dict(raw["universe"], "universe")
    consent = _require_dict(raw["consent"], "consent")

    hard_caps = HardCaps(
        account_funding_usd=float(caps["account_funding_usd"]),
        max_order_notional_usd=float(caps["max_order_notional_usd"]),
        max_total_exposure_usd=float(caps["max_total_exposure_usd"]),
        max_leverage=float(caps["max_leverage"]),
        allowed_instruments=tuple(
            InstrumentType(value) for value in caps["allowed_instruments"]
        ),
        max_trades_per_day=int(caps["max_trades_per_day"]),
    )
    universe_constraint = UniverseConstraint(
        asset_classes=tuple(AssetClass(value) for value in universe["asset_classes"]),
        min_market_cap_usd=_opt_float(universe["min_market_cap_usd"]),
        min_avg_daily_volume_usd=_opt_float(universe["min_avg_daily_volume_usd"]),
        exclude_symbols=tuple(str(value) for value in universe["exclude_symbols"]),
    )
    consent_meta = ConsentMeta(
        created_at=str(consent["created_at"]),
        consent_token_sha256=str(consent["consent_token_sha256"]),
        broker=str(consent["broker"]),
        account_ref=str(consent["account_ref"]),
        expires_at=str(consent["expires_at"]),
    )
    return Mandate(
        schema_version=int(raw["schema_version"]),
        hard_caps=hard_caps,
        universe=universe_constraint,
        consent=consent_meta,
        # Optional per-mandate halt-behavior flag (SPEC §7.5 #6). Absent on an
        # old mandate.json → False (cancel-only, the safe default), keeping the
        # read backward-compatible. Read on a halt trip by src.live.runtime.flatten.
        flatten_on_halt=bool(raw.get("flatten_on_halt", False)),
    )


def _require_dict(value: object, field: str) -> dict:
    """Return ``value`` as a dict or raise ``TypeError`` naming ``field``."""
    if not isinstance(value, dict):
        raise TypeError(f"mandate field {field!r} must be a JSON object")
    return value


def _opt_float(value: object) -> float | None:
    """Coerce an optional numeric field to ``float | None``."""
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# Legacy migration (idempotent, safe to call on every load)
# ---------------------------------------------------------------------------


def migrate_legacy_mandate(broker: str, target_account_id: str = "default") -> bool:
    """Migrate a broker-level mandate to an account-scoped path.

    For virtual broker: moves ``live/virtual/mandate.json`` →
    ``live/virtual/default/mandate.json`` if the broker-level file exists
    and the scoped path does not. Idempotent — if either condition is not
    met, returns ``False`` without modifying any files.

    Returns:
        ``True`` if migration occurred, ``False`` otherwise.
    """
    legacy = broker_dir(broker) / _MANDATE_FILENAME
    scoped = broker_dir(broker, target_account_id) / _MANDATE_FILENAME

    if not legacy.is_file():
        return False
    if scoped.is_file():
        return False  # scoped already exists; don't overwrite

    scoped.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    legacy.rename(scoped)
    logger.info("migrated mandate %s → %s", legacy, scoped)

    # Also migrate the trade counter if it exists.
    _migrate_legacy_counter(broker, target_account_id)

    return True


def _migrate_legacy_counter(broker: str, target_account_id: str = "default") -> bool:
    """Migrate a broker-level trade counter to an account-scoped path.

    Moves ``live/<broker>/trade_counter.json`` →
    ``live/<broker>/<account_id>/trade_counter.json``.

    Returns:
        ``True`` if migration occurred, ``False`` otherwise.
    """
    legacy = broker_dir(broker) / "trade_counter.json"
    scoped = broker_dir(broker, target_account_id) / "trade_counter.json"

    if not legacy.is_file():
        return False
    if scoped.is_file():
        return False

    scoped.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    legacy.rename(scoped)
    logger.info("migrated counter %s → %s", legacy, scoped)
    return True
