"""Activate a mandate for virtual-paper-trade (US default account)."""
from __future__ import annotations

import sys
import uuid

sys.path.insert(0, "/app/agent")

from src.live.mandate.commit import commit_mandate, save_proposal
from src.live.mandate.store import load_mandate
from src.live.risk_scope import resolve_scope
from src.live.halt import clear_halt

# Clear any lingering halt
clear_halt("virtual")
clear_halt()
print("Halts cleared.")

# Create proposal payload
proposal_id = f"mp_{uuid.uuid4().hex}"
proposal = {
    "type": "mandate.proposal",
    "proposal_id": proposal_id,
    "session_id": "dispatcher-activation",
    "intent_normalized": "virtual paper trading with risk controls",
    "account": {
        "broker": "virtual",
        "account_id": "default",
        "type": "cash",
        "funded_by": "user",
    },
    "ceilings_ref": f"caps_{uuid.uuid4().hex}",
    "ceilings": {
        "account_funding_usd": 1_000_000.0,
        "max_order_notional_usd": 1_000_000.0,
        "max_total_exposure_usd": 1_000_000.0,
    },
    "profiles": [
        {
            "ordinal": 1, "label": "稳健",
            "universe": ["AAPL", "MSFT", "NVDA", "GOOGL"],
            "max_order_usd": 50000.0,
            "max_total_exposure_usd": 200000.0,
            "daily_trade_cap": 5,
            "leverage": "none",
            "instruments": ["equity"],
            "flatten_on_halt": False,
            "notes": "Conservative: 5% of funding per order, cash-only.",
        },
        {
            "ordinal": 2, "label": "均衡",
            "universe": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA"],
            "max_order_usd": 150000.0,
            "max_total_exposure_usd": 500000.0,
            "daily_trade_cap": 10,
            "leverage": "none",
            "instruments": ["equity"],
            "flatten_on_halt": False,
            "notes": "Balanced: moderate sizing, cash-only.",
        },
        {
            "ordinal": 3, "label": "激进",
            "universe": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "SPY", "QQQ"],
            "max_order_usd": 300000.0,
            "max_total_exposure_usd": 1_000_000.0,
            "daily_trade_cap": 20,
            "leverage": 2.0,
            "instruments": ["equity"],
            "flatten_on_halt": True,
            "notes": "Aggressive: up to 2x leverage, largest clips.",
        },
    ],
    "funding_note": "Funding is set by YOU inside the broker account.",
    "halt_note": "A single command halts everything instantly.",
}

# Save proposal to scoped path
save_proposal(proposal)
print(f"Proposal saved: {proposal_id}")

# Commit the mandate (profile 2 = 均衡)
result = commit_mandate(
    proposal_id=proposal_id,
    ordinal=2,
    adjustments=None,
    consent_ack=True,
    broker="virtual",
    account_id="default",
    account_ref="virtual-default",
    session_id="dispatcher-activation",
    lifetime_days=90,
    flatten_on_halt=False,
)
print(f"Mandate committed: {result['mandate_id']}")
print(f"Expires at: {result['expires_at']}")

# Verify
mandate = load_mandate("virtual", "default")
if mandate:
    print(f"Mandate verified OK!")
    print(f"  Leverage: {mandate.hard_caps.max_leverage}")
    print(f"  Max order: ${mandate.hard_caps.max_order_notional_usd:,.0f}")
    print(f"  Max exposure: ${mandate.hard_caps.max_total_exposure_usd:,.0f}")
    print(f"  Max trades/day: {mandate.hard_caps.max_trades_per_day}")
    print(f"  Instruments: {[str(i.value) for i in mandate.hard_caps.allowed_instruments]}")
    print(f"  Flatten on halt: {mandate.flatten_on_halt}")
    print(f"  Expires: {mandate.consent.expires_at}")
else:
    print("ERROR: Mandate not found after commit!")
    sys.exit(1)
