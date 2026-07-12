"""Order risk scope — the accountability boundary for a live order.

Every order that passes through the unified safety gate is associated with an
``OrderRiskScope`` that resolves the correct mandate, halt sentinel, daily
counter, and audit namespace for the executing entity.

Real broker: scope = (broker, "default") — one mandate per broker.
Virtual:     scope = (broker, account_id) — per-account isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.live.paths import broker_dir


@dataclass(frozen=True)
class OrderRiskScope:
    """The resolved risk scope for an order gate invocation.

    Attributes:
        broker: Normalized broker key, e.g. ``"virtual"``, ``"robinhood"``.
        account_id: Account identifier within the broker. For real brokers
            this is ``"default"`` (one mandate per broker); for virtual
            accounts it is the profile's ``account_id`` (e.g. ``"default"``
            for US, ``"cn-default"`` for A-share).
        mandate_dir: The directory that holds ``mandate.json`` for this scope.
        counter_path: The path to ``trade_counter.json`` for this scope.
        halt_path: The broker-level halt sentinel path (account-level halt
            is not separately supported; broker halt + global halt is enough).
    """
    broker: str
    account_id: str

    @property
    def mandate_dir(self) -> Path:
        return broker_dir(self.broker, self.account_id)

    @property
    def mandate_path(self) -> Path:
        return self.mandate_dir / "mandate.json"

    @property
    def counter_path(self) -> Path:
        return self.mandate_dir / "trade_counter.json"

    @property
    def broker_halt_path(self) -> Path:
        from src.live.halt import broker_halt_path
        return broker_halt_path(self.broker)


def resolve_scope(broker: str, account_id: str = "default") -> OrderRiskScope:
    """Resolve a broker + account_id to an ``OrderRiskScope``.

    Args:
        broker: Normalized broker key.
        account_id: Account id. For real brokers this should be ``"default"``;
            for virtual accounts use the profile's ``account_id``.

    Returns:
        An ``OrderRiskScope`` instance.

    Raises:
        ValueError: If broker is empty/invalid (delegated to
            :func:`~src.live.paths.broker_dir` for fail-fast validation).
    """
    normalized_broker = broker.strip().lower()
    normalized_account = account_id.strip().lower() if account_id else "default"

    # Early validation: let broker_dir raise on invalid broker/account_id
    # before constructing the scope (fail-fast).
    broker_dir(normalized_broker, normalized_account)

    return OrderRiskScope(
        broker=normalized_broker,
        account_id=normalized_account,
    )
