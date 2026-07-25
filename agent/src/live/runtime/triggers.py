"""Live-runtime triggers — when an autonomous tick is due (SPEC §7.5 component 4).

This layers market-session, interval, and event triggers over R1's wall-clock
scheduler. The reference scheduler is time-only; trading additionally needs market-session
awareness ("is the US equity market open right now?") and event-driven
predicates (price crossings, fill notifications). This module owns that layer.

Purity contract (CRITICAL): the testable core takes ``now_ms`` (epoch
milliseconds, UTC) and any market/event state as *arguments* — it never reads
the wall clock. This keeps :func:`due_now` and :func:`market_is_open`
deterministic under test. The only clock read lives in the thin
:func:`due_now_at` convenience wrapper, which delegates straight into the pure
core.

Frozen public contract (imported blind by R2):

* :class:`Trigger` — the immutable trigger descriptor.
* :func:`market_is_open` ``(market) -> bool`` — is the given market open *now*.
* :func:`due_now` ``(trigger, now_ms) -> bool`` — is the trigger due at ``now_ms``.

Imports are stdlib only (``datetime`` / ``zoneinfo``); no other parcel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------- #
# Market specifications (module-level config — no hardcoding inline).          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _MarketSpec:
    """Regular-trading-hours spec for one market.

    Attributes:
        tz: IANA timezone the session times are expressed in.
        open_time: Local session open (inclusive). Ignored when ``sessions``
            is non-empty.
        close_time: Local session close (exclusive — a tick exactly at the
            close bell counts as *closed*, matching exchange convention).
            Ignored when ``sessions`` is non-empty.
        weekdays: Permitted weekdays as ``date.weekday()`` values (Mon==0).
            Empty == every day (24/7 markets such as crypto).
        always_open: Short-circuit for 24/7 markets; when ``True`` the time /
            weekday / holiday checks are skipped entirely.
        holidays: Full-day market closures (no half-days modelled). This is a
            deliberately small, static set — see module docstring limitation.
        sessions: Multi-session support (e.g. A-shares with a lunch break).
            A tuple of ``(open, close)`` time pairs; open is inclusive, close
            is exclusive. When non-empty, ``open_time`` / ``close_time`` are
            ignored and these sessions are used instead. Defaults to an empty
            tuple so existing single-session markets are unaffected.
    """

    tz: str
    open_time: time
    close_time: time
    weekdays: frozenset[int] = frozenset()
    always_open: bool = False
    holidays: frozenset[date] = field(default_factory=frozenset)
    sessions: tuple[tuple[time, time], ...] = field(default_factory=tuple)


# US market holidays. LIMITATION: this is a hand-maintained static set (no
# half-day early closes, no rolling computation). It covers the current and
# next calendar year; extend as needed. A production deploy that needs decades
# of coverage should swap in `pandas_market_calendars` behind this same spec.
_US_EQUITY_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2026
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # MLK Jr. Day
        date(2026, 2, 16),  # Washington's Birthday
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day (observed)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
        # 2027
        date(2027, 1, 1),  # New Year's Day
        date(2027, 1, 18),  # MLK Jr. Day
        date(2027, 2, 15),  # Washington's Birthday
        date(2027, 3, 26),  # Good Friday
        date(2027, 5, 31),  # Memorial Day
        date(2027, 6, 18),  # Juneteenth (observed)
        date(2027, 7, 5),  # Independence Day (observed)
        date(2027, 9, 6),  # Labor Day
        date(2027, 11, 25),  # Thanksgiving
        date(2027, 12, 24),  # Christmas (observed)
    }
)

_WEEKDAYS_MON_FRI = frozenset({0, 1, 2, 3, 4})

# A-share (China) market holidays. LIMITATION: this is a hand-maintained static
# set (no half-day early closes, no rolling computation). It covers 2026-2027;
# extend as needed. A production deploy that needs decades of coverage should
# swap in a dedicated Chinese market calendar behind this same spec.
_CN_EQUITY_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2026
        date(2026, 1, 1),  # 元旦
        date(2026, 1, 2),  # 元旦
        date(2026, 2, 16),  # 春节
        date(2026, 2, 17),  # 春节
        date(2026, 2, 18),  # 春节
        date(2026, 2, 19),  # 春节
        date(2026, 2, 20),  # 春节
        date(2026, 4, 6),  # 清明
        date(2026, 5, 1),  # 劳动节
        date(2026, 5, 4),  # 劳动节
        date(2026, 5, 5),  # 劳动节
        date(2026, 6, 19),  # 端午
        date(2026, 9, 25),  # 中秋
        date(2026, 10, 1),  # 国庆
        date(2026, 10, 2),  # 国庆
        date(2026, 10, 5),  # 国庆
        date(2026, 10, 6),  # 国庆
        date(2026, 10, 7),  # 国庆
        date(2026, 10, 8),  # 国庆
        # 2027
        date(2027, 1, 1),  # 元旦
        date(2027, 2, 5),  # 春节
        date(2027, 2, 8),  # 春节
        date(2027, 2, 9),  # 春节
        date(2027, 2, 10),  # 春节
        date(2027, 2, 11),  # 春节
        date(2027, 4, 5),  # 清明
        date(2027, 5, 3),  # 劳动节
        date(2027, 5, 4),  # 劳动节
        date(2027, 5, 5),  # 劳动节
        date(2027, 6, 9),  # 端午
        date(2027, 9, 15),  # 中秋
        date(2027, 10, 1),  # 国庆
        date(2027, 10, 4),  # 国庆
        date(2027, 10, 5),  # 国庆
        date(2027, 10, 6),  # 国庆
        date(2027, 10, 7),  # 国庆
    }
)

# Market registry. Keys match the AssetClass-style identifiers the runner uses
# (e.g. "us_equity", "crypto"). Add markets here, never inline in functions.
MARKET_SPECS: Mapping[str, _MarketSpec] = {
    "us_equity": _MarketSpec(
        tz="America/New_York",
        open_time=time(9, 30),
        close_time=time(16, 0),
        weekdays=_WEEKDAYS_MON_FRI,
        holidays=_US_EQUITY_HOLIDAYS,
    ),
    "crypto": _MarketSpec(
        tz="UTC",
        open_time=time(0, 0),
        close_time=time(0, 0),
        always_open=True,
    ),
    "cn_equity": _MarketSpec(
        tz="Asia/Shanghai",
        open_time=time(9, 30),  # primary session (compat with single-session logic)
        close_time=time(15, 0),
        weekdays=_WEEKDAYS_MON_FRI,
        holidays=_CN_EQUITY_HOLIDAYS,
        sessions=(
            (time(9, 30), time(11, 30)),  # 上午盘
            (time(13, 0), time(15, 0)),  # 下午盘
        ),
    ),
}


# --------------------------------------------------------------------------- #
# Trigger model                                                                #
# --------------------------------------------------------------------------- #


class TriggerKind(str, Enum):
    """The trigger families layered over the wall-clock scheduler."""

    INTERVAL = "interval"
    MARKET = "market"
    EVENT = "event"
    CRON = "cron"


@dataclass(frozen=True)
class Trigger:
    """Immutable descriptor of when an autonomous tick should fire.

    A single dataclass covers all three families; the active fields depend on
    ``kind``. It is frozen so a mandate can pin it once and the runner can never
    mutate it mid-flight.

    Attributes:
        kind: Which trigger family this is.
        interval_ms: INTERVAL only — fire every this many milliseconds, counted
            from ``epoch_ms``. Must be > 0 for an interval trigger.
        epoch_ms: INTERVAL only — the phase anchor (epoch ms) the cadence is
            measured from. Defaults to 0 (UNIX epoch) so the schedule is stable
            across restarts.
        market: MARKET only — a key into :data:`MARKET_SPECS` (e.g.
            ``"us_equity"``). The trigger is "due" whenever that market is open.
        predicate: EVENT only — a pure callable evaluated against an
            event-state mapping supplied by the runner/event source. It returns
            ``True`` when the awaited condition (price crossing, fill, etc.) is
            met. The trigger carries the predicate; the runner carries the data.

    Construct via the classmethod helpers (:meth:`interval`, :meth:`market`,
    :meth:`event`) rather than the raw constructor where convenient.
    """

    kind: TriggerKind
    interval_ms: int = 0
    epoch_ms: int = 0
    market: str | None = None
    predicate: Callable[[Mapping[str, object]], bool] | None = None
    cron_spec: str | None = None  # CRON only — 5-field cron expression (UTC)

    @classmethod
    def interval(cls, interval_ms: int, *, epoch_ms: int = 0) -> "Trigger":
        """Build an interval trigger firing every ``interval_ms`` ms.

        Args:
            interval_ms: Cadence in milliseconds; must be strictly positive.
            epoch_ms: Phase anchor in epoch ms (default 0 == UNIX epoch).

        Returns:
            A frozen INTERVAL :class:`Trigger`.

        Raises:
            ValueError: If ``interval_ms`` is not strictly positive.
        """
        if interval_ms <= 0:
            raise ValueError("interval_ms must be > 0")
        return cls(kind=TriggerKind.INTERVAL, interval_ms=interval_ms, epoch_ms=epoch_ms)

    @classmethod
    def market(cls, market: str) -> "Trigger":
        """Build a market-session trigger for a known market key.

        Args:
            market: A key into :data:`MARKET_SPECS` (e.g. ``"us_equity"``).

        Returns:
            A frozen MARKET :class:`Trigger`.

        Raises:
            ValueError: If ``market`` is not a registered market.
        """
        if market not in MARKET_SPECS:
            raise ValueError(f"unknown market: {market!r}")
        return cls(kind=TriggerKind.MARKET, market=market)

    @classmethod
    def event(cls, predicate: Callable[[Mapping[str, object]], bool]) -> "Trigger":
        """Build an event trigger driven by a pure predicate.

        Args:
            predicate: A callable taking the runner-supplied event-state mapping
                and returning ``True`` when the awaited condition is met.

        Returns:
            A frozen EVENT :class:`Trigger`.
        """
        return cls(kind=TriggerKind.EVENT, predicate=predicate)

    @classmethod
    def cron(cls, cron_spec: str) -> "Trigger":
        """Build a cron trigger from a 5-field cron expression (UTC).

        Args:
            cron_spec: 5-field cron string, e.g. ``"25 9 * * 1-5"`` for 9:25
                UTC on weekdays.

        Returns:
            A frozen CRON :class:`Trigger`.

        Raises:
            ValueError: If ``cron_spec`` is not a valid 5-field cron expression.
        """
        from src.scheduled_research.models import validate_schedule

        validate_schedule(cron_spec)
        return cls(kind=TriggerKind.CRON, cron_spec=cron_spec)


# --------------------------------------------------------------------------- #
# Pure core                                                                    #
# --------------------------------------------------------------------------- #


def _ms_to_aware_dt(now_ms: int, tz: str) -> datetime:
    """Convert epoch milliseconds to a tz-aware datetime in ``tz``.

    Args:
        now_ms: Epoch milliseconds, UTC.
        tz: Target IANA timezone name.

    Returns:
        A timezone-aware :class:`datetime` localized to ``tz``.
    """
    utc_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    return utc_dt.astimezone(ZoneInfo(tz))


def market_is_open_at(market: str, now_ms: int) -> bool:
    """Pure core: is ``market`` open at the instant ``now_ms``.

    Deterministic — reads no clock. 24/7 markets (``always_open``) are always
    open. Session markets are open only on permitted weekdays, outside the
    holiday set, and within ``[open_time, close_time)`` local time.

    Args:
        market: A key into :data:`MARKET_SPECS`.
        now_ms: Epoch milliseconds, UTC.

    Returns:
        ``True`` if the market is open at ``now_ms``, else ``False``.

    Raises:
        ValueError: If ``market`` is not a registered market (fail loud — an
            unknown market must never be silently treated as open).
    """
    spec = MARKET_SPECS.get(market)
    if spec is None:
        raise ValueError(f"unknown market: {market!r}")
    if spec.always_open:
        return True

    local_dt = _ms_to_aware_dt(now_ms, spec.tz)
    if spec.weekdays and local_dt.weekday() not in spec.weekdays:
        return False
    if local_dt.date() in spec.holidays:
        return False
    # Multi-session support (e.g. A-shares with lunch break).
    if spec.sessions:
        return any(start <= local_dt.time() < end for start, end in spec.sessions)
    return spec.open_time <= local_dt.time() < spec.close_time


def due_now(trigger: Trigger, now_ms: int, *, event_state: Mapping[str, object] | None = None) -> bool:
    """Pure core: is ``trigger`` due at ``now_ms``.

    Deterministic — reads no clock; all inputs are arguments.

    * INTERVAL: due when ``(now_ms - epoch_ms)`` lands on an exact multiple of
      ``interval_ms``. The scheduler quantizes ticks, so callers test exact
      boundaries; this is the alignment the runner's quantized clock produces.
    * MARKET: due whenever the trigger's market is open at ``now_ms``.
    * EVENT: due when the trigger's predicate returns truthy against
      ``event_state`` (an empty mapping when the runner supplies none).
    * CRON: due when ``now_ms`` falls on a minute that matches the 5-field
      cron expression (1-minute resolution, UTC).

    Args:
        trigger: The trigger to evaluate.
        now_ms: Epoch milliseconds, UTC.
        event_state: Event-source state for EVENT triggers; ignored otherwise.

    Returns:
        ``True`` if the trigger is due, else ``False``.

    Raises:
        ValueError: If the trigger is malformed for its kind (e.g. an EVENT
            trigger with no predicate, or a MARKET trigger with no market).
    """
    if trigger.kind is TriggerKind.INTERVAL:
        if trigger.interval_ms <= 0:
            raise ValueError("interval trigger requires interval_ms > 0")
        elapsed = now_ms - trigger.epoch_ms
        return elapsed >= 0 and elapsed % trigger.interval_ms == 0

    if trigger.kind is TriggerKind.MARKET:
        if trigger.market is None:
            raise ValueError("market trigger requires a market")
        return market_is_open_at(trigger.market, now_ms)

    if trigger.kind is TriggerKind.EVENT:
        if trigger.predicate is None:
            raise ValueError("event trigger requires a predicate")
        return bool(trigger.predicate(event_state or {}))

    if trigger.kind is TriggerKind.CRON:
        if trigger.cron_spec is None:
            raise ValueError("cron trigger requires a cron_spec")
        from src.scheduled_research.executor import next_due

        # A cron trigger fires when now_ms falls on a matching minute.
        # Compute the next fire time from 1 minute ago; if it equals now_ms
        # (within the same minute), the trigger is due.
        prev_ms = now_ms - 60_000
        next_fire = next_due(trigger.cron_spec, prev_ms)
        return next_fire <= now_ms

    raise ValueError(f"unknown trigger kind: {trigger.kind!r}")


# --------------------------------------------------------------------------- #
# Clock-reading convenience wrappers (delegate to the pure core)               #
# --------------------------------------------------------------------------- #


def _now_ms() -> int:
    """Current wall-clock time in epoch milliseconds, UTC.

    Returns:
        Epoch milliseconds for the current instant.
    """
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def market_is_open(market: str) -> bool:
    """Is ``market`` open right now (wall clock).

    Thin convenience wrapper around the pure :func:`market_is_open_at`; the
    only clock read happens here and is delegated immediately.

    Args:
        market: A key into :data:`MARKET_SPECS`.

    Returns:
        ``True`` if the market is open now, else ``False``.

    Raises:
        ValueError: If ``market`` is not a registered market.
    """
    return market_is_open_at(market, _now_ms())


def due_now_at(trigger: Trigger, *, event_state: Mapping[str, object] | None = None) -> bool:
    """Is ``trigger`` due right now (wall clock).

    Thin convenience wrapper around the pure :func:`due_now`; the only clock
    read happens here and is delegated immediately.

    Args:
        trigger: The trigger to evaluate.
        event_state: Event-source state for EVENT triggers.

    Returns:
        ``True`` if the trigger is due now, else ``False``.
    """
    return due_now(trigger, _now_ms(), event_state=event_state)
