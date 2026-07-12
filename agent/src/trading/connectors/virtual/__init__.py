"""Local virtual broker connector for paper-only simulated trading.

This connector provides a fully local, no-credentials, no-network simulated
trading environment. It is designed for LiveRunner dry-runs under a mandate
prompt, with zero real funds at risk. All state is persisted under
``~/.vibe-trading/live/virtual/<account_id>/``.

Key design properties:
- ``transport = "broker_sdk"`` — reuses the existing SDK connector path.
- ``environment = "paper"`` — all profiles are paper-only.
- Price data: best-effort yfinance (optional) → fallback deterministic drift.
- Matching: eager for market orders, lazy for limit orders (matched on next
  read/write). No background daemon threads.
- Thread-safe: ``threading.RLock`` guards all state read-modify-write.
"""

from src.trading.connectors.virtual.profiles import VIRTUAL_PROFILES

__all__ = ["VIRTUAL_PROFILES"]
