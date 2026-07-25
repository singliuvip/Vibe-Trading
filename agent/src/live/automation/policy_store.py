"""Durable automation policy store (read/write, fail-closed).

Persists the user-authorized AutomationPolicy to
``~/.vibe-trading/live/<broker>/<account_id>/automation_policy.json``.
Mirrors the mandate store's safety conventions: atomic write (temp file →
os.replace), 0600 permissions, and fail-closed loading (any read/parse error
yields a DISABLED policy rather than guessing).

The policy mode gate is the ONLY thing this store controls. Enabling automation
still requires a valid mandate WITH automation_bounds — the store never widens
trading authority on its own.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from src.live.automation.policy import AutomationMode, AutomationPolicy
from src.live.paths import broker_dir

logger = logging.getLogger(__name__)

_POLICY_FILENAME = "automation_policy.json"


def policy_path(broker: str, account_id: Optional[str] = None) -> Path:
    """Return the automation policy file path for a scope."""
    return broker_dir(broker, account_id) / _POLICY_FILENAME


def load_policy(broker: str, account_id: Optional[str] = None) -> AutomationPolicy:
    """Load the automation policy for a scope (fail-closed → DISABLED on any error).

    A missing file, unreadable file, or malformed record yields a DISABLED
    policy so automation can never be accidentally enabled by a corrupt store.
    """
    path = policy_path(broker, account_id)
    if not path.is_file():
        return AutomationPolicy()  # default DISABLED
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("automation policy for %s unreadable/invalid: %s", broker, exc)
        return AutomationPolicy()
    try:
        return _parse_policy(raw)
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("automation policy for %s failed validation: %s", broker, exc)
        return AutomationPolicy()


def save_policy(policy: AutomationPolicy, broker: str, account_id: Optional[str] = None) -> Path:
    """Atomically persist an automation policy (0600). Returns the written path."""
    path = policy_path(broker, account_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "mode": policy.mode.value,
        "min_signal_score": policy.min_signal_score,
        "max_signal_age_seconds": policy.max_signal_age_seconds,
        "require_exit_protection": policy.require_exit_protection,
        "allowed_strategies": list(policy.allowed_strategies),
        "max_orders_per_cycle": policy.max_orders_per_cycle,
        "symbol_cooldown_seconds": policy.symbol_cooldown_seconds,
    }
    # Atomic write: temp file in same dir → fsync → os.replace.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".automation_policy.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def _parse_policy(raw: object) -> AutomationPolicy:
    """Build an AutomationPolicy from decoded JSON (strict)."""
    if not isinstance(raw, dict):
        raise TypeError("automation policy root must be a JSON object")
    mode = AutomationMode(str(raw.get("mode", "disabled")))
    allowed = raw.get("allowed_strategies")
    return AutomationPolicy(
        mode=mode,
        min_signal_score=float(raw.get("min_signal_score", 0.7)),
        max_signal_age_seconds=int(raw.get("max_signal_age_seconds", 300)),
        require_exit_protection=bool(raw.get("require_exit_protection", True)),
        allowed_strategies=tuple(str(s) for s in allowed) if allowed else (),
        max_orders_per_cycle=int(raw.get("max_orders_per_cycle", 5)),
        symbol_cooldown_seconds=int(raw.get("symbol_cooldown_seconds", 3600)),
    )
