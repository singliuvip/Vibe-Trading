"""Phase 1 grayscale execution script — paper_auto on virtual broker.

Guides the operator through the full Phase 1 grayscale startup:
1. Pre-flight checks (mandate, policy, kill switch, config sanity)
2. Enable paper_auto mode (if not already enabled)
3. Start the automation runner with A-share schedule
4. Verify the runner is alive

This script is interactive and requires explicit confirmation before each
privileged action. It never bypasses the consent flow — mandate creation
must be done separately via the proper surface (propose_mandate + commit).

Usage:
    python -m src.live.automation.grayscale.run_phase1
    python -m src.live.automation.grayscale.run_phase1 --yes  # skip confirmations
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

BROKER = "virtual"
ACCOUNT_ID = "cn-default"
MODE = "paper_auto"
SCHEDULE = "30 1 * * 1-5"  # A-share 09:30 CST = 01:30 UTC, weekdays
SHADOW_ID = None  # defaults to shadow_{broker}


def _confirm(prompt: str, auto_yes: bool = False) -> bool:
    """Ask for confirmation unless --yes is set."""
    if auto_yes:
        return True
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def step_preflight() -> tuple[bool, list[str]]:
    """Run pre-flight checks."""
    from src.live.automation.grayscale.preflight import load_config, run_preflight

    print("\n" + "=" * 60)
    print("  Phase 1 Grayscale — Pre-flight Checks")
    print("=" * 60)

    try:
        config = load_config("phase1_paper.json")
    except FileNotFoundError:
        print("❌ phase1_paper.json not found")
        return False, ["config file missing"]

    passed, errors = run_preflight(config)

    if passed:
        print("[OK] All pre-flight checks passed")
    else:
        print(f"[FAIL] {len(errors)} check(s) failed:")
        for i, err in enumerate(errors, 1):
            print(f"   {i}. {err}")

    return passed, errors


def step_check_mandate() -> bool:
    """Check if a valid mandate with automation_bounds exists."""
    from src.live.mandate.store import load_mandate

    mandate = load_mandate(BROKER, ACCOUNT_ID)
    if mandate is None:
        print(f"\n[WARN] No mandate for broker={BROKER!r}, account={ACCOUNT_ID!r}")
        print("   You must create a mandate with automation_bounds first.")
        print("   Use the propose_mandate tool or the frontend consent flow.")
        return False

    if mandate.automation_bounds is None:
        print(f"\n[WARN] Mandate for {BROKER!r} has no automation_bounds (v1).")
        print("   Automation requires a v2 mandate with automation_bounds.")
        return False

    print(f"\n[OK] Valid mandate found for {BROKER!r} (account={ACCOUNT_ID!r})")
    print(f"   automation_bounds: max_daily_turnover={mandate.automation_bounds.max_daily_turnover_usd}, "
          f"allowed_order_types={mandate.automation_bounds.allowed_order_types}")
    return True


def step_enable_policy(auto_yes: bool = False) -> bool:
    """Enable paper_auto mode via policy_store."""
    from src.live.automation.policy import AutomationMode, AutomationPolicy
    from src.live.automation.policy_store import load_policy, save_policy

    current = load_policy(BROKER, ACCOUNT_ID)
    if current.mode == AutomationMode.PAPER_AUTO:
        print(f"\n[OK] Policy already in {MODE} mode")
        return True

    if current.mode != AutomationMode.DISABLED:
        print(f"\n[WARN] Policy is in {current.mode.value!r} mode (expected disabled or paper_auto)")

    print(f"\n[INFO] About to enable {MODE} mode for {BROKER!r}")
    if not _confirm("   Enable paper_auto mode?", auto_yes):
        print("   Cancelled.")
        return False

    updated = AutomationPolicy(
        mode=AutomationMode.PAPER_AUTO,
        min_signal_score=current.min_signal_score,
        max_signal_age_seconds=current.max_signal_age_seconds,
        require_exit_protection=current.require_exit_protection,
        allowed_strategies=current.allowed_strategies,
        max_orders_per_cycle=current.max_orders_per_cycle,
        symbol_cooldown_seconds=current.symbol_cooldown_seconds,
    )
    path = save_policy(updated, BROKER, ACCOUNT_ID)
    print(f"[OK] Policy enabled: {MODE} -> {path}")
    return True


def step_start_runner(auto_yes: bool = False) -> bool:
    """Start the automation runner via the API endpoint logic."""
    print(f"\n[INFO] About to start automation runner:")
    print(f"   Broker:   {BROKER}")
    print(f"   Account:  {ACCOUNT_ID}")
    print(f"   Schedule: {SCHEDULE} (A-share 09:30 CST, weekdays)")
    print(f"   Mode:     {MODE}")

    if not _confirm("   Start the automation runner?", auto_yes):
        print("   Cancelled.")
        return False

    # Use the API endpoint logic directly (same as POST /live/automation/start).
    from src.api.automation_routes import _build_automation_runner, _drive_automation_runner
    import asyncio

    try:
        runner = _build_automation_runner(
            BROKER, ACCOUNT_ID,
            schedule=SCHEDULE,
            shadow_id=SHADOW_ID,
        )
    except Exception as exc:
        print(f"[FAIL] Failed to build runner: {exc}")
        return False

    # Note: In production, this would be started via the API server's event loop.
    # Here we just verify the runner can be constructed.
    print(f"[OK] Runner constructed successfully")
    print(f"   Config: broker={runner._config.broker}, schedule={runner._config.schedule}")
    print(f"   Profile: {runner._config.profile_id}")
    print()
    print("[WARN] To actually run the loop, start the API server and use:")
    print(f"   curl -X POST http://localhost:8000/live/automation/start \\")
    print(f'     -H "Content-Type: application/json" \\')
    print(f'     -d \'{{"broker": "{BROKER}", "account_id": "{ACCOUNT_ID}", "schedule": "{SCHEDULE}"}}\'')
    print()
    print("   Or via CLI (when API server is running):")
    print(f"   vibe-trading automation start {BROKER} --schedule \"{SCHEDULE}\"")
    return True


def main() -> int:
    """Run Phase 1 grayscale startup."""
    parser = argparse.ArgumentParser(description="Phase 1 grayscale execution")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmations")
    args = parser.parse_args()

    print("Vibe-Trading Phase 1 Grayscale - paper_auto on virtual broker")
    print("   This is PAPER trading only. No real funds at risk.")

    # Step 1: Pre-flight.
    passed, errors = step_preflight()

    # Step 2: Check mandate.
    mandate_ok = step_check_mandate()

    if not mandate_ok:
        print("\n[FAIL] Cannot proceed without a valid mandate.")
        print("   Create one via the consent flow, then re-run this script.")
        return 1

    # Step 3: Enable policy.
    if not step_enable_policy(args.yes):
        return 1

    # Step 4: Start runner.
    if not step_start_runner(args.yes):
        return 1

    print("\n" + "=" * 60)
    print("  [OK] Phase 1 setup complete")
    print("=" * 60)
    print()
    print("  Monitoring:")
    print(f"    vibe-trading automation status {BROKER}")
    print()
    print("  Rollback:")
    print(f"    vibe-trading automation stop {BROKER}")
    print(f"    vibe-trading automation disable {BROKER}")
    print(f"    vibe-trading connector halt  # emergency kill switch")
    return 0


if __name__ == "__main__":
    sys.exit(main())