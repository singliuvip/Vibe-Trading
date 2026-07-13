"""Channel config loading and saving helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.config.loader import load_agent_config
from src.config.paths import get_config_path

logger = logging.getLogger(__name__)


def load_channels_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load the operator IM channel config from the structured agent config.

    Args:
        config_path: Optional explicit config path.

    Returns:
        A plain dictionary suitable for :class:`src.channels.manager.ChannelManager`.
    """
    config = load_agent_config(config_path)
    return config.channels.model_dump(mode="json", by_alias=False)


def save_channels_section(
    channel_name: str,
    section_config: dict[str, Any],
    config_path: Path | None = None,
) -> None:
    """Persist a channel's config section into the structured agent config file.

    Reads the existing config file (or starts with defaults), updates
    ``channels.<channel_name>`` with *section_config*, and writes back as JSON.
    Only JSON format is supported for writing.
    """
    path = get_config_path(config_path)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = {}
        path.parent.mkdir(parents=True, exist_ok=True)

    if not isinstance(raw, dict):
        raw = {}

    raw.setdefault("channels", {})
    if not isinstance(raw["channels"], dict):
        raw["channels"] = {}

    existing = raw["channels"].get(channel_name, {})
    if isinstance(existing, dict):
        existing.update(section_config)
    else:
        existing = section_config
    raw["channels"][channel_name] = existing

    path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Persisted %s channel config to %s", channel_name, path)
