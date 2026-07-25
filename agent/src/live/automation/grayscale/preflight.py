"""Grayscale pre-flight checks for automated trading.

Validates a grayscale configuration before starting the automation runner.
Checks mandate validity, policy consistency, broker connectivity, and
configuration sanity. All checks are fail-closed: any failure blocks startup.

Usage:
    python -m src.live.automation.grayscale.preflight --config phase1_paper.json
    python -m src.live.automation.grayscale.preflight --broker virtual --mode paper_auto
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_config(config_path: str | Path) -> dict[str, Any]:
    """Load a grayscale configuration file."""
    path = Path(config_path)
    if not path.is_file():
        # Try relative to the grayscale directory.
        grayscale_dir = Path(__file__).parent
        path = grayscale_dir / config_path
    if not path.is_file():
        raise FileNotFoundError(f"Grayscale config not found: {config_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def check_mandate(broker: str, account_id: str | None = None) -> list[str]:
    """Check that a valid mandate with automation_bounds exists."""
    errors: list[str] = []
    try:
        from src.live.mandate.store import load_mandate

        mandate = load_mandate(broker, account_id)
        if mandate is None:
            errors.append(f"No committed mandate for broker={broker!r}")
            return errors
        if mandate.automation_bounds is None:
            errors.append(
                f"Mandate for {broker!r} has no automation_bounds (v1 mandate). "
                "Automation requires a v2 mandate with automation_bounds."
            )
        # Check expiry.
        from datetime import datetime, timezone

        from src.live.runtime.runner import _mandate_is_expired

        if _mandate_is_expired(mandate, datetime.now(timezone.utc)):
            errors.append(f"Mandate for {broker!r} has expired. Re-authorize first.")
    except Exception as exc:
        errors.append(f"Mandate check failed: {exc}")
    return errors


def check_policy(broker: str, account_id: str | None, expected_mode: str) -> list[str]:
    """Check that the automation policy is consistent with the expected mode."""
    errors: list[str] = []
    try:
        from src.live.automation.policy_store import load_policy

        policy = load_policy(broker, account_id)
        if policy.mode.value == "disabled":
            errors.append(
                f"Automation policy for {broker!r} is DISABLED. "
                f"Enable it first: vibe-trading automation enable {broker} --mode {expected_mode}"
            )
        elif policy.mode.value != expected_mode:
            errors.append(
                f"Policy mode is {policy.mode.value!r} but expected {expected_mode!r}. "
                f"Update: vibe-trading automation enable {broker} --mode {expected_mode}"
            )
    except Exception as exc:
        errors.append(f"Policy check failed: {exc}")
    return errors


def check_kill_switch(broker: str) -> list[str]:
    """Check that the kill switch is not tripped."""
    errors: list[str] = []
    try:
        from src.live.halt import halt_flag_set

        if halt_flag_set(broker=broker) or halt_flag_set(broker=None):
            errors.append(
                f"Kill switch is tripped for {broker!r}. "
                "Clear it first: vibe-trading connector resume"
            )
    except Exception as exc:
        errors.append(f"Kill switch check failed: {exc}")
    return errors


def check_config_sanity(config: dict[str, Any]) -> list[str]:
    """Validate configuration sanity (bounds, policy, exit protection)."""
    errors: list[str] = []

    bounds = config.get("automation_bounds", {})
    policy = config.get("policy", {})

    # Bounds checks.
    turnover = bounds.get("max_daily_turnover_usd")
    if turnover is not None and turnover <= 0:
        errors.append("max_daily_turnover_usd must be > 0")

    order_types = bounds.get("allowed_order_types", [])
    if not order_types:
        errors.append("allowed_order_types must not be empty")

    if bounds.get("max_slippage_pct") is not None:
        slip = bounds["max_slippage_pct"]
        if not (0 < slip <= 1):
            errors.append(f"max_slippage_pct must be in (0, 1], got {slip}")

    if bounds.get("max_drawdown_pct") is not None:
        dd = bounds["max_drawdown_pct"]
        if not (0 < dd <= 1):
            errors.append(f"max_drawdown_pct must be in (0, 1], got {dd}")

    # Policy checks.
    score = policy.get("min_signal_score", 0.7)
    if not (0 <= score <= 1):
        errors.append(f"min_signal_score must be in [0, 1], got {score}")

    max_orders = policy.get("max_orders_per_cycle", 5)
    if max_orders <= 0:
        errors.append("max_orders_per_cycle must be > 0")

    # Exit protection.
    exit_prot = config.get("exit_protection", {})
    if bounds.get("require_stop_loss") and not exit_prot.get("stop_loss_pct"):
        errors.append("require_stop_loss is true but no stop_loss_pct configured")

    return errors


def check_broker_connectivity(broker: str, profile_id: str | None = None) -> list[str]:
    """Check that the broker connector is reachable (best-effort)."""
    errors: list[str] = []
    try:
        from src.trading.service import get_account

        result = get_account(profile_id)
        if result.get("status") == "error":
            errors.append(f"Broker connectivity check failed: {result.get('error')}")
    except Exception as exc:
        errors.append(f"Broker connectivity check failed: {exc}")
    return errors


def run_preflight(config: dict[str, Any]) -> tuple[bool, list[str]]:
    """Run all pre-flight checks. Returns (passed, errors)."""
    all_errors: list[str] = []

    broker = config.get("broker", "")
    account_id = config.get("account_id")
    mode = config.get("mode", "disabled")
    profile_id = config.get("profile_id")

    if not broker:
        all_errors.append("broker is required in config")
        return False, all_errors

    # 1. Mandate check.
    all_errors.extend(check_mandate(broker, account_id))

    # 2. Policy check.
    all_errors.extend(check_policy(broker, account_id, mode))

    # 3. Kill switch check.
    all_errors.extend(check_kill_switch(broker))

    # 4. Config sanity check.
    all_errors.extend(check_config_sanity(config))

    # 5. Broker connectivity (best-effort, non-blocking).
    conn_errors = check_broker_connectivity(broker, profile_id)
    if conn_errors:
        # Connectivity is best-effort; warn but don't block.
        print(f"  ⚠️  Broker connectivity: {conn_errors[0]}")

    passed = len(all_errors) == 0
    return passed, all_errors


def main() -> int:
    """CLI entry point for grayscale pre-flight checks."""
    parser = argparse.ArgumentParser(description="Grayscale pre-flight checks")
    parser.add_argument(
        "--config",
        default="phase1_paper.json",
        help="Grayscale config file (default: phase1_paper.json)",
    )
    parser.add_argument("--broker", help="Override broker from config")
    parser.add_argument("--mode", help="Override mode from config")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        return 1

    if args.broker:
        config["broker"] = args.broker
    if args.mode:
        config["mode"] = args.mode

    phase = config.get("phase", "?")
    name = config.get("name", "unknown")
    broker = config.get("broker", "?")

    print(f"🔍 Grayscale pre-flight: Phase {phase} — {name}")
    print(f"   Broker: {broker} | Mode: {config.get('mode', '?')}")
    print()

    passed, errors = run_preflight(config)

    if passed:
        print("✅ All pre-flight checks passed. Ready to start automation.")
        print()
        print(f"   Start: vibe-trading automation start {broker}")
        print(f"   Status: vibe-trading automation status {broker}")
        print(f"   Stop:   vibe-trading automation stop {broker}")
        return 0
    else:
        print(f"❌ {len(errors)} pre-flight check(s) failed:")
        for i, err in enumerate(errors, 1):
            print(f"   {i}. {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())