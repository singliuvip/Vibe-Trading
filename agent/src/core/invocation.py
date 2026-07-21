"""Invocation context — cross-cutting DTO that tells the agent about its trigger source."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Literal, Optional


InvocationSource = Literal["interactive", "scheduled_research", "live_runner", "channel", "goal"]


@dataclass(frozen=True)
class InvocationContext:
    """Trusted execution context injected by the service layer.

    Defaults (all None) represent a standard interactive invocation — CLI,
    Web UI, or channel — where the agent should behave normally.
    """

    source: InvocationSource = "interactive"
    trigger_id: Optional[str] = None
    scheduled_for: Optional[int] = None  # epoch-ms
    triggered_at: Optional[int] = None   # epoch-ms
    schedule: Optional[str] = None
    unattended: bool = False
    research_only: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InvocationContext":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})