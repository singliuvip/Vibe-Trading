"""HTTP-based xtquant connector configuration.

When Vibe-Trading runs inside a Docker container (Linux), it cannot import the
Windows-only xtquant package directly.  Instead it talks to a QMT Bridge HTTP
server running on the Windows host via ``host.docker.internal``.

Usage::

    from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig

    cfg = XtQuantHttpConfig.from_env()
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class XtQuantHttpConfig:
    """HTTP bridge connection settings for xtquant/miniQMT.

    All values can be overridden via environment variables.
    """

    #: QMT Bridge base URL (default: host.docker.internal for Docker).
    bridge_url: str = "http://host.docker.internal:8888"

    #: Bearer token for authenticating with QMT Bridge.
    bearer_token: str = "qmt-ql-8f3a2d1e9c"

    #: miniQMT installation path on the Windows host (passed to Bridge).
    mini_qmt_path: str = ""

    #: Account ID for queries (empty = auto-discover on Bridge side).
    account_id: str = ""

    #: Account type (STOCK, FUTURES, etc.).
    account_type: str = "STOCK"

    #: Profile environment: paper, live-readonly, or live.
    profile: str = "paper"

    #: HTTP request timeout in seconds.
    timeout: float = 15.0

    #: Maximum retries on transient failures.
    max_retries: int = 3

    #: Read-only flag (always True for this layer).
    readonly: bool = True

    @classmethod
    def from_env(cls, **overrides) -> "XtQuantHttpConfig":
        """Build config from environment variables with optional overrides.

        Environment variables:
            XTQUANT_BRIDGE_URL: QMT Bridge URL.
            XTQUANT_BEARER_TOKEN: Bearer token.
            XTQUANT_MINI_QMT_PATH: miniQMT path on host.
            XTQUANT_ACCOUNT_ID: Account ID.
            XTQUANT_ACCOUNT_TYPE: Account type.
            XTQUANT_PROFILE: Profile (paper/live-readonly/live).
            XTQUANT_HTTP_TIMEOUT: Request timeout (seconds).
        """
        return cls(
            bridge_url=overrides.pop(
                "bridge_url",
                os.getenv("XTQUANT_BRIDGE_URL", "http://host.docker.internal:8888"),
            ),
            bearer_token=overrides.pop(
                "bearer_token",
                os.getenv("XTQUANT_BEARER_TOKEN", "qmt-ql-8f3a2d1e9c"),
            ),
            mini_qmt_path=overrides.pop(
                "mini_qmt_path",
                os.getenv("XTQUANT_MINI_QMT_PATH", ""),
            ),
            account_id=overrides.pop(
                "account_id",
                os.getenv("XTQUANT_ACCOUNT_ID", ""),
            ),
            account_type=overrides.pop(
                "account_type",
                os.getenv("XTQUANT_ACCOUNT_TYPE", "STOCK"),
            ),
            profile=overrides.pop(
                "profile",
                os.getenv("XTQUANT_PROFILE", "paper"),
            ),
            timeout=float(overrides.pop(
                "timeout",
                os.getenv("XTQUANT_HTTP_TIMEOUT", "15.0"),
            )),
            max_retries=int(overrides.pop("max_retries", 3)),
            readonly=bool(overrides.pop("readonly", True)),
            **overrides,
        )

    @property
    def is_live(self) -> bool:
        """Return whether this is a live (non-paper) profile."""
        return self.profile in ("live", "live-readonly")

    @property
    def auth_header(self) -> dict[str, str]:
        """Return the Authorization header dict."""
        return {"Authorization": f"Bearer {self.bearer_token}"}

    def to_dict(self) -> dict[str, str | int | float | bool]:
        """Return config as a plain dict (for logging)."""
        return {
            "bridge_url": self.bridge_url,
            "bearer_token": f"{self.bearer_token[:4]}****",
            "mini_qmt_path": self.mini_qmt_path,
            "account_id": self.account_id,
            "account_type": self.account_type,
            "profile": self.profile,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "readonly": self.readonly,
        }
