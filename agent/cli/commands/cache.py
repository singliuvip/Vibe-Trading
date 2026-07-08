"""/cache — inspect and manage data-source caches.

``/cache info``  prints the cache path and disk usage for each data source.
``/cache clear`` drops cached data for one or all sources.

Supported sources: ``xtdata``, ``tushare``, ``akshare``, ``yfinance``, ``all``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cli.theme import get_console

#: Known data sources whose caches live under ``~/.vibe-trading/cache/``.
#: Each entry maps source name → (cache subdirectory, label).
_SOURCES: dict[str, tuple[str, str]] = {
    "xtdata":   ("xtdata",   "miniQMT xtdata"),
    "tushare":  ("tushare",  "Tushare"),
    "akshare":  ("akshare",  "AKShare"),
    "yfinance": ("yfinance", "Yahoo Finance"),
}


def _resolve_console() -> Console:
    """Return the shared CLI console."""
    return get_console()


def _cache_dir() -> Path:
    """Return ``~/.vibe-trading/cache/``."""
    return Path.home() / ".vibe-trading" / "cache"


def _dir_size(path: Path) -> int:
    """Recursively compute total bytes under *path*.  Returns 0 on error."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except Exception:
        pass
    return total


def _format_size(size_bytes: int) -> str:
    """Human-readable size string (KB / MB / GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


# ---------------------------------------------------------------------------
# /cache info
# ---------------------------------------------------------------------------


def _xtdata_cache_info() -> dict[str, Any]:
    """Extra info for the xtdata cache — try to query xtquant's own cache API.

    xtdata maintains its own download cache (``.../userdata_mini/datadir/``).
    We attempt to surface that location and size alongside Vibe-Trading's
    loader cache.
    """
    info: dict[str, Any] = {"xtdata_api_available": False}
    try:
        import xtquant.xtdata as xtdata  # type: ignore[import-untyped]

        info["xtdata_api_available"] = True
        # xtdata does not expose a public cache-path API, but the data dir is
        # typically stored internally.  We try a common attribute.
        data_dir = getattr(xtdata, "_data_dir", None) or getattr(
            xtdata, "data_dir", None
        )
        if data_dir:
            p = Path(str(data_dir))
            if p.is_dir():
                info["xtdata_data_dir"] = str(p)
                info["xtdata_data_size"] = _format_size(_dir_size(p))
    except ImportError:
        pass
    except Exception:
        pass
    return info


def _collect_cache_info(source: str = "all") -> list[dict[str, Any]]:
    """Gather cache metadata for one or all sources.

    Returns:
        A list of dicts with keys ``source``, ``label``, ``path``, ``size_bytes``,
        ``size_human``, ``exists``.
    """
    base = _cache_dir()
    targets = dict(_SOURCES)
    if source != "all":
        if source not in targets:
            return []
        targets = {source: targets[source]}

    rows: list[dict[str, Any]] = []
    for src, (subdir, label) in targets.items():
        p = base / subdir
        exists = p.is_dir()
        size_bytes = _dir_size(p) if exists else 0
        rows.append({
            "source": src,
            "label": label,
            "path": str(p),
            "size_bytes": size_bytes,
            "size_human": _format_size(size_bytes),
            "exists": exists,
        })
    return rows


def cmd_cache_info(ctx: Any = None, *args: str) -> int:
    """``/cache info [--source <name>]`` — show cache path, size, and status."""
    console = _resolve_console()

    # Parse --source flag
    source = "all"
    extra: list[str] = []
    it = iter(args)
    for a in it:
        if a == "--source":
            try:
                source = next(it)
            except StopIteration:
                console.print(Text("Usage: /cache info [--source xtdata|tushare|akshare|yfinance|all]", style="bold red"))
                return 1
        else:
            extra.append(a)

    rows = _collect_cache_info(source)
    if not rows:
        console.print(Text(f"Unknown cache source: {source}", style="bold red"))
        console.print(Text(f"Known sources: {', '.join(_SOURCES.keys())}", style="dim"))
        return 1

    table = Table(title=f"📦  Vibe-Trading Cache  —  {_cache_dir()}", border_style="dim")
    table.add_column("Source", style="bold")
    table.add_column("Label")
    table.add_column("Path", style="dim")
    table.add_column("Size", justify="right")

    for r in rows:
        icon = "✅" if r["exists"] else "❌"
        table.add_row(
            f"{icon} {r['source']}",
            r["label"],
            r["path"],
            r["size_human"] if r["exists"] else "(not present)",
        )

    console.print(table)

    # Extra xtdata info
    if source in ("all", "xtdata"):
        xt_info = _xtdata_cache_info()
        if xt_info.get("xtdata_data_dir"):
            console.print()
            console.print(
                Text(
                    f"📁 xtdata internal data dir: {xt_info['xtdata_data_dir']} "
                    f"({xt_info.get('xtdata_data_size', 'unknown')})",
                    style="dim",
                )
            )

    return 0


# ---------------------------------------------------------------------------
# /cache clear
# ---------------------------------------------------------------------------


def _clear_xtdata_cache(force: bool = False) -> dict[str, Any]:
    """Attempt to clear xtdata's internal download cache via its Python API.

    xtdata provides ``clear_download_data()`` in newer versions.  If that is
    unavailable we fall back to clearing Vibe-Trading's loader cache only.
    """
    result: dict[str, Any] = {"xtdata_api_cleared": False, "loader_cache_cleared": False}

    # ── xtdata.clear_download_data() (newer xtquant versions) ──
    try:
        import xtquant.xtdata as xtdata  # type: ignore[import-untyped]

        clearer = getattr(xtdata, "clear_download_data", None)
        if callable(clearer):
            clearer()
            result["xtdata_api_cleared"] = True
    except ImportError:
        result["error"] = "xtquant not installed; clearing Vibe-Trading loader cache only"
    except Exception as exc:
        result["xtdata_api_error"] = str(exc)

    # ── Clear Vibe-Trading loader cache ──
    cache_path = _cache_dir() / "xtdata"
    if cache_path.is_dir():
        try:
            shutil.rmtree(cache_path)
            cache_path.mkdir(parents=True, exist_ok=True)
            result["loader_cache_cleared"] = True
        except OSError as exc:
            result["loader_cache_error"] = str(exc)

    return result


def cmd_cache_clear(ctx: Any = None, *args: str) -> int:
    """``/cache clear [--source <name>]`` — drop cached data for one or all sources."""
    console = _resolve_console()

    # Parse --source flag
    source = "all"
    it = iter(args)
    for a in it:
        if a == "--source":
            try:
                source = next(it)
            except StopIteration:
                console.print(Text("Usage: /cache clear [--source xtdata|tushare|akshare|yfinance|all]", style="bold red"))
                return 1

    if source != "all" and source not in _SOURCES:
        console.print(Text(f"Unknown cache source: {source}", style="bold red"))
        return 1

    base = _cache_dir()

    if source == "all":
        for src, (subdir, _label) in _SOURCES.items():
            p = base / subdir
            if p.is_dir():
                try:
                    shutil.rmtree(p)
                    p.mkdir(parents=True, exist_ok=True)
                    console.print(Text(f"  ✅ Cleared {src} cache", style="green"))
                except OSError as exc:
                    console.print(Text(f"  ❌ Failed to clear {src}: {exc}", style="red"))
        console.print(Text("\n📦 All caches cleared.", style="bold"))
    elif source == "xtdata":
        result = _clear_xtdata_cache()
        if result.get("xtdata_api_cleared"):
            console.print(Text("  ✅ xtdata.clear_download_data() called successfully", style="green"))
        elif result.get("xtdata_api_error"):
            console.print(Text(f"  ⚠️  xtdata API clear failed: {result['xtdata_api_error']}", style="yellow"))
        if result.get("loader_cache_cleared"):
            console.print(Text("  ✅ Vibe-Trading xtdata loader cache cleared", style="green"))
        if result.get("loader_cache_error"):
            console.print(Text(f"  ❌ Loader cache error: {result['loader_cache_error']}", style="red"))
        if result.get("error"):
            console.print(Text(f"  ℹ️  {result['error']}", style="dim"))
        console.print(Text("\n📦 xtdata cache cleared (see details above).", style="bold"))
    else:
        p = base / _SOURCES[source][0]
        if p.is_dir():
            try:
                shutil.rmtree(p)
                p.mkdir(parents=True, exist_ok=True)
                console.print(Text(f"  ✅ Cleared {source} cache", style="green"))
            except OSError as exc:
                console.print(Text(f"  ❌ Failed: {exc}", style="red"))
        else:
            console.print(Text(f"  ℹ️  No {source} cache found at {p}", style="dim"))
        console.print(Text(f"\n📦 {source} cache cleared.", style="bold"))

    return 0


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_DISPATCH = {
    "info":  cmd_cache_info,
    "clear": cmd_cache_clear,
}


def run(ctx: Any = None, command: str = "info", *args: str) -> int:
    """Dispatch ``run("info")`` / ``run("clear")``."""
    handler = _DISPATCH.get(command)
    if handler is None:
        console = _resolve_console()
        console.print(
            Text(
                f"Unknown cache subcommand: /cache {command}. "
                "Use /cache info or /cache clear.",
                style="bold red",
            )
        )
        return 1
    return handler(ctx, *args)


__all__ = ["run", "cmd_cache_info", "cmd_cache_clear"]
