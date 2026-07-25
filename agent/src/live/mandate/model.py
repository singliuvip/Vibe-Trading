"""Mandate data model — the immutable bounded-autonomy contract.

Frozen dataclasses (no Pydantic): the mandate is read once at boot and never
mutated, so plain frozen dataclasses give the strongest immutability guarantee
with zero validation surface the agent could exploit. See
the live-trading SPEC, Mandate §1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

MANDATE_SCHEMA_VERSION = 1


class InstrumentType(str, Enum):
    """Instrument classes the broker may report or accept.

    ``CFD`` covers margin contracts-for-difference that are not spot forex
    pairs (MT5 metals like XAUUSD, index/energy/crypto CFDs). Like ``OPTION``
    it has no universe asset-class bucket and is admitted only when the user's
    mandate explicitly lists ``"cfd"`` in ``allowed_instruments``.
    """

    EQUITY = "equity"
    ETF = "etf"
    OPTION = "option"
    CRYPTO = "crypto"
    FOREX = "forex"
    CFD = "cfd"


class AssetClass(str, Enum):
    """Universe-level asset class buckets the user may permit."""

    US_EQUITY = "us_equity"
    US_ETF = "us_etf"
    HK_EQUITY = "hk_equity"
    CN_EQUITY = "cn_equity"
    IN_EQUITY = "in_equity"
    CRYPTO = "crypto"
    FOREX = "forex"


@dataclass(frozen=True)
class HardCaps:
    """Layer (a): user-set quantitative ceilings.

    Funding is enforced BROKER-SIDE (Robinhood dedicated agentic account
    balance) and is the absolute ceiling the agent physically cannot exceed;
    it is mirrored here only for defense-in-depth pre-trade math, never as the
    primary guarantee. Every other field is enforced VIBE-SIDE in the gate.

    Attributes:
        account_funding_usd: Ring-fenced balance in the dedicated agentic
            account, USD. BROKER-ENFORCED ceiling; mirrored for local math.
        max_order_notional_usd: Vibe-enforced max single-order notional, USD.
        max_total_exposure_usd: Vibe-enforced cap on aggregate post-trade
            market value of all open positions, USD.
        max_leverage: Vibe-enforced gross leverage multiple. 1.0 == cash-only.
        allowed_instruments: Vibe-enforced whitelist of tradable instrument
            types. Empty == deny all (fail-closed).
        max_trades_per_day: Vibe-enforced count of order placements allowed
            per UTC calendar day. Counter persisted alongside the mandate.
    """

    account_funding_usd: float
    max_order_notional_usd: float
    max_total_exposure_usd: float
    max_leverage: float
    allowed_instruments: tuple[InstrumentType, ...]
    max_trades_per_day: int


@dataclass(frozen=True)
class UniverseConstraint:
    """Layer (b): user-set universe the agent picks symbols WITHIN.

    Not a ticker whitelist — that would kill agent discovery. The agent selects
    individual symbols freely so long as each clears these structural filters.

    Attributes:
        asset_classes: Permitted asset-class buckets. Empty == deny all.
        min_market_cap_usd: Market-cap floor, USD. ``None`` == no floor.
        min_avg_daily_volume_usd: Liquidity floor as trailing avg daily dollar
            volume, USD. ``None`` == no floor.
        exclude_symbols: Hard per-symbol denylist (normalized upper-case,
            e.g. ``BTC-USDT`` style for crypto). Takes precedence over every
            other universe rule.
    """

    asset_classes: tuple[AssetClass, ...]
    min_market_cap_usd: float | None
    min_avg_daily_volume_usd: float | None
    exclude_symbols: tuple[str, ...]


@dataclass(frozen=True)
class ConsentMeta:
    """Provenance proving the user (not the agent) authored this mandate.

    Attributes:
        created_at: ISO-8601 UTC timestamp the user committed the mandate.
        consent_token_sha256: Hash of the consent artifact emitted by the
            consent UX section, binding this file to an explicit human approval.
        broker: Broker key, e.g. ``"robinhood"``.
        account_ref: Opaque broker account identifier (NOT credentials).
        expires_at: ISO-8601 UTC timestamp after which the mandate is dead and
            the gate fail-closes until the user re-authorizes. Default lifetime
            30 days from ``created_at`` (configurable per commit). A live
            mandate must not live forever — see §9 decision 2.
    """

    created_at: str
    consent_token_sha256: str
    broker: str
    account_ref: str
    expires_at: str


@dataclass(frozen=True)
class AutomationBounds:
    """Layer (c): user-set ceilings that ONLY the automated execution path enforces.

    These bounds are additive and optional. A mandate without automation_bounds
    (the v1 shape) behaves exactly as before for manual/agent-driven trading —
    the shared gate (check_mandate) never reads this object. Only the automated
    execution pipeline (src.live.automation) consults it, and a mandate lacking
    it cannot enable live automation (fail-closed).

    All fields are Optional: None means "no bound on this dimension" (the
    automation layer still applies its own policy + the shared mandate gate).
    When set, each is a hard ceiling enforced fail-closed by the automation
    executor on top of check_mandate.

    Attributes:
        max_daily_turnover_usd: Max cumulative traded notional (buys + sells)
            per UTC calendar day. None == no daily turnover cap.
        max_symbol_exposure_usd: Max post-trade market value held in any single
            symbol. None == no per-symbol cap (total exposure still capped by
            HardCaps.max_total_exposure_usd).
        max_positions: Max number of distinct open positions. None == no cap.
        max_open_orders: Max number of resting (unfilled) orders. None == no cap.
        max_daily_realized_loss_usd: Max realized loss (positive number) allowed
            per UTC day before automation halts. None == no cap.
        max_drawdown_pct: Max portfolio drawdown fraction (0,1] from peak equity
            before automation halts. None == no cap. E.g. 0.10 == 10%.
        allowed_order_types: Whitelist of order types automation may use
            (subset of {"market","limit"}). Empty == deny all automation orders
            (fail-closed). Manual trading is unaffected.
        max_slippage_pct: Max allowed slippage fraction (0,1] vs signal price_ref.
            None == no slippage check.
        require_stop_loss: Whether every automated order must carry a stop-loss.
        allow_short: Whether automated sell-to-open (shorting) is permitted.
            False (default) restricts sells to closing existing longs.
    """

    max_daily_turnover_usd: float | None = None
    max_symbol_exposure_usd: float | None = None
    max_positions: int | None = None
    max_open_orders: int | None = None
    max_daily_realized_loss_usd: float | None = None
    max_drawdown_pct: float | None = None
    allowed_order_types: tuple[str, ...] = field(default_factory=tuple)
    max_slippage_pct: float | None = None
    require_stop_loss: bool = False
    allow_short: bool = False

    def __post_init__(self) -> None:
        """Validate bounds are sane (fail-closed on construction)."""
        for name in ("max_daily_turnover_usd", "max_symbol_exposure_usd",
                     "max_daily_realized_loss_usd"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be > 0 when set, got {value}")
        for name in ("max_positions", "max_open_orders"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be > 0 when set, got {value}")
        if self.max_drawdown_pct is not None and not (0.0 < self.max_drawdown_pct <= 1.0):
            raise ValueError(f"max_drawdown_pct must be in (0,1], got {self.max_drawdown_pct}")
        if self.max_slippage_pct is not None and not (0.0 < self.max_slippage_pct <= 1.0):
            raise ValueError(f"max_slippage_pct must be in (0,1], got {self.max_slippage_pct}")
        for ot in self.allowed_order_types:
            if ot not in ("market", "limit"):
                raise ValueError(f"allowed_order_types entries must be 'market'/'limit', got {ot!r}")


@dataclass(frozen=True)
class Mandate:
    """Immutable bounded-autonomy mandate for one live broker channel.

    Loaded read-only at session boot from the user-side protected store. The
    agent loop has no constructor or write path to this object (see §2).

    Attributes:
        schema_version: ``MANDATE_SCHEMA_VERSION`` at write time; gate refuses
            to operate on an unknown future version (fail-closed).
        hard_caps: Layer (a) quantitative ceilings.
        universe: Layer (b) discovery universe.
        consent: Provenance/consent metadata.
        flatten_on_halt: Whether a kill-switch trip should flatten open
            positions (submit closing orders) in addition to cancelling resting
            orders. ``False`` (the default, and the value an old ``mandate.json``
            lacking this field loads as) means cancel-only — the safe default.
            ``True`` is an explicit per-mandate opt-in the user makes at commit.
            Read by ``src.live.runtime.flatten`` on a halt trip (SPEC §7.5 #6
            "optionally, per mandate").
        automation_bounds: Layer (c) optional ceilings enforced ONLY by the
            automated execution pipeline (``src.live.automation``). ``None``
            (the default, and the value an old ``mandate.json`` lacking this
            section loads as) means the mandate predates automation bounds and
            cannot enable live automation (fail-closed). The shared gate
            (``check_mandate``) never reads this object, so manual/agent-driven
            trading is unaffected by its presence or absence.
    """

    schema_version: int
    hard_caps: HardCaps
    universe: UniverseConstraint
    consent: ConsentMeta
    flatten_on_halt: bool = False
    automation_bounds: AutomationBounds | None = None
