"""HTTP-based xtquant connector configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class XtQuantHttpConfig:
    bridge_url: str = "http://host.docker.internal:8888"
    bearer_token: str = "qmt-ql-8f3a2d1e9c"
    mini_qmt_path: str = ""
    account_id: str = ""
    account_type: str = "STOCK"
    profile: str = "paper"
    timeout: float = 15.0
    max_retries: int = 3
    readonly: bool = True

    @classmethod
    def from_env(cls, **overrides) -> "XtQuantHttpConfig":
        return cls(
            bridge_url=overrides.pop("bridge_url", os.getenv("XTQUANT_BRIDGE_URL", "http://host.docker.internal:8888")),
            bearer_token=overrides.pop("bearer_token", os.getenv("XTQUANT_BEARER_TOKEN", "qmt-ql-8f3a2d1e9c")),
            mini_qmt_path=overrides.pop("mini_qmt_path", os.getenv("XTQUANT_MINI_QMT_PATH", "")),
            account_id=overrides.pop("account_id", os.getenv("XTQUANT_ACCOUNT_ID", "")),
            account_type=overrides.pop("account_type", os.getenv("XTQUANT_ACCOUNT_TYPE", "STOCK")),
            profile=overrides.pop("profile", os.getenv("XTQUANT_PROFILE", "paper")),
            timeout=float(overrides.pop("timeout", os.getenv("XTQUANT_HTTP_TIMEOUT", "15.0"))),
            max_retries=int(overrides.pop("max_retries", 3)),
            readonly=bool(overrides.pop("readonly", True)),
            **overrides,
        )

    @property
    def is_live(self) -> bool:
        return self.profile in ("live", "live-readonly")

    @property
    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"}

    def to_dict(self) -> dict[str, str | int | float | bool]:
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
