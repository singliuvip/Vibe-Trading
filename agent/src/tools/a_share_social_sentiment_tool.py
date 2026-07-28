"""Read-only A-share social-sentiment tool: Eastmoney Guba post metadata.

A-share retail discussion is concentrated on Eastmoney's Guba (股吧) per-stock
forum. This tool wraps the public, no-auth Guba post-list HTML page and returns
raw post metadata (title / author / published / read_count / comment_count /
url) for one A-share symbol. Like every Eastmoney surface it rate-limits by
source IP, so the request routes through the frozen, IP-throttled
:mod:`backtest.loaders.eastmoney_client` rather than touching the host
directly.

The tool deliberately returns **raw post metadata only** — it does not score
sentiment. Sentiment quantification (LLM scoring in ``-1.0..1.0`` plus a rule
lexicon fallback) is the responsibility of the ``social_analyst`` agent, mir
roring the event-driven skill's "collect raw, score upstream" pattern. This
keeps the tool a thin protocol adapter with no business logic, per the
architecture layering rules.

Only A-share suffixes (SH / SZ / BJ) are supported; non-A-share codes return an
error envelope. A failure for the upstream is reported as an error envelope;
the tool never raises out of :meth:`AShareSocialSentimentTool.execute`.
"""

from __future__ import annotations

import json
import logging
import re
from html import unescape
from typing import Any

from backtest.loaders import eastmoney_client

from src.agent.tools import BaseTool

logger = logging.getLogger(__name__)

# Eastmoney Guba per-stock post-list HTML page. The bare 6-digit code selects
# the forum (e.g. 600519 -> list,600519.html). It is the most stable public
# entry to Guba post metadata; the JSON interface has shifted historically.
_GUBA_LIST_URL = "https://guba.eastmoney.com/list,{code}.html"

# A-share exchange suffixes that route to the Guba surface.
_A_SHARE_SUFFIXES = ("SH", "SZ", "BJ")

# Bounds so a noisy upstream can never return an unbounded payload.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100
# Per-post title trim so the envelope stays compact for the LLM.
_TITLE_CHARS = 120

# Guba list pages render each post row as a <span class="l3"> anchor block.
# The anchor carries the post title (text), the relative URL (href), and is
# followed by author / published / read / comment spans. The exact markup has
# minor variations across pages, so the parser is permissive: it scans for
# anchor tags whose href starts with the Guba path prefix and extracts the
# surrounding row's numeric spans.
_POST_HREF_RE = re.compile(
    r'<a[^>]+href="(/news,[^"]+,\d+\.html)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
# A row's numeric spans: read count and comment count appear as
# <span class="l3">123</span> style cells. We capture the first two integer
# spans after the title anchor as read_count / comment_count (Guba's column
# order). Non-digits are tolerated and skipped.
_SPAN_NUM_RE = re.compile(r'<span[^>]*>\s*([\d]+)\s*</span>')
# Author + published are emitted as plain text cells; we pull the author from
# the dedicated author anchor and the published timestamp from the date span.
_AUTHOR_HREF_RE = re.compile(
    r'<a[^>]+href="(https?://iguba\.eastmoney\.com/[^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_DATE_SPAN_RE = re.compile(
    r'<span[^>]*class="[^"]*\bdate\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
    re.DOTALL,
)
# Strip remaining HTML tags from a captured cell so titles/authors are plain text.
_TAG_RE = re.compile(r"<[^>]+>")


def _clamp_limit(raw: Any) -> int:
    """Coerce a caller-supplied ``limit`` into the supported ``1.._MAX_LIMIT`` range.

    Args:
        raw: The raw ``limit`` value from the tool arguments (any type).

    Returns:
        An integer in ``[1, _MAX_LIMIT]``, falling back to ``_DEFAULT_LIMIT``
        when ``raw`` is missing or non-numeric.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    if value < 1:
        return 1
    return min(value, _MAX_LIMIT)


def _suffix_of(code: str) -> str:
    """Return the upper-cased exchange suffix of a symbol, or ``""`` when none."""
    if "." not in code:
        return ""
    return code.rpartition(".")[2].strip().upper()


def _bare_code(code: str) -> str:
    """Strip any exchange suffix to the bare 6-digit A-share code."""
    return code.strip().split(".", 1)[0].strip()


def _clean_text(text: Any) -> str:
    """Strip HTML tags and collapse whitespace from a captured cell.

    Args:
        text: Raw HTML fragment (any type).

    Returns:
        A plain-text, whitespace-collapsed string capped at
        ``_TITLE_CHARS`` characters, or ``""`` when ``text`` is not usable.
    """
    if not isinstance(text, str):
        return ""
    stripped = _TAG_RE.sub("", text)
    collapsed = " ".join(unescape(stripped).split())
    if len(collapsed) <= _TITLE_CHARS:
        return collapsed
    return collapsed[:_TITLE_CHARS].rstrip() + "…"


def _parse_guba_posts(html: str, limit: int) -> list[dict[str, Any]]:
    """Parse Guba post-list HTML into compact post metadata records.

    The parser is intentionally permissive: Guba's markup varies across pages
    and over time, so it scans for post anchors and best-effort extracts the
    surrounding row's author / published / read_count / comment_count. A row
    missing a field still yields a record with that field set to ``None``.

    Args:
        html: Raw Guba list-page HTML body.
        limit: Maximum number of post records to return.

    Returns:
        A capped list of ``{title, author, published, read_count,
        comment_count, url}`` records; empty when no post anchors are found.
    """
    posts: list[dict[str, Any]] = []
    for match in _POST_HREF_RE.finditer(html):
        href, title_html = match.group(1), match.group(2)
        title = _clean_text(title_html)
        if not title:
            continue

        # Scan a window after the title anchor for the row's numeric spans
        # (read_count, comment_count) and the author/date cells.
        window = html[match.end(): match.end() + 1200]

        author = None
        author_match = _AUTHOR_HREF_RE.search(window)
        if author_match:
            author = _clean_text(author_match.group(2)) or None

        published = None
        date_match = _DATE_SPAN_RE.search(window)
        if date_match:
            published = " ".join(date_match.group(1).split()) or None

        numbers = _SPAN_NUM_RE.findall(window)
        read_count: int | None = None
        comment_count: int | None = None
        if len(numbers) >= 1:
            try:
                read_count = int(numbers[0])
            except (TypeError, ValueError):
                read_count = None
        if len(numbers) >= 2:
            try:
                comment_count = int(numbers[1])
            except (TypeError, ValueError):
                comment_count = None

        posts.append(
            {
                "title": title,
                "author": author,
                "published": published,
                "read_count": read_count,
                "comment_count": comment_count,
                "url": f"https://guba.eastmoney.com{href}",
            }
        )
        if len(posts) >= limit:
            break
    return posts


def _fetch_guba_posts(bare_code: str, limit: int) -> list[dict[str, Any]]:
    """Fetch Guba post metadata for one A-share code from Eastmoney.

    Args:
        bare_code: Bare 6-digit A-share code (e.g. ``"600519"``).
        limit: Maximum number of post records to return.

    Returns:
        A capped list of compact post records; empty when none.

    Raises:
        requests.RequestException: Network failure, propagated to the caller.
        requests.HTTPError: Non-2xx response status.
    """
    url = _GUBA_LIST_URL.format(code=bare_code)
    html = eastmoney_client.get_text(url)
    return _parse_guba_posts(html, limit)


class AShareSocialSentimentTool(BaseTool):
    """Read-only A-share social-sentiment post metadata from Eastmoney Guba."""

    name = "get_a_share_social_sentiment"
    description = (
        "Fetch A-share retail discussion post metadata from Eastmoney Guba "
        "(股吧), read-only and no auth. Returns raw post records (title / "
        "author / published / read_count / comment_count / url) for one A-share "
        "symbol (SH/SZ/BJ). Does NOT score sentiment — scoring is done by the "
        "social_analyst agent via LLM. Use 'code' with an A-share suffix. "
        'Example: {"code": "600519.SH", "limit": 50}.'
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "A-share symbol, e.g. '600519.SH', '000001.SZ', "
                    "'830799.BJ'. Only SH/SZ/BJ suffixes are supported."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Maximum number of posts to return (1-100). Default 50."
                ),
                "default": _DEFAULT_LIMIT,
            },
            "source": {
                "type": "string",
                "enum": ["guba"],
                "description": "Social source. Currently only 'guba' is supported.",
                "default": "guba",
            },
        },
        "required": ["code"],
    }

    def execute(self, **kwargs: Any) -> str:
        """Fetch Guba post metadata for one A-share symbol.

        Args:
            **kwargs: ``code`` (required, A-share symbol), optional ``limit``
                (1-100, default 50), optional ``source`` (default 'guba').

        Returns:
            A JSON string envelope. On success:
            ``{"ok": true, "market": "a_share", "source": "guba",
            "data": {"code", "secid", "posts": [...]}}``. On failure:
            ``{"ok": false, "error": "..."}``.
        """
        source = kwargs.get("source", "guba")
        if source != "guba":
            return self._error(
                f"unsupported source: {source!r}; currently only 'guba' is supported"
            )

        code_arg = kwargs.get("code")
        if not isinstance(code_arg, str) or not code_arg.strip():
            return self._error("missing required parameter: code (A-share symbol)")

        code = code_arg.strip()
        suffix = _suffix_of(code)
        if suffix not in _A_SHARE_SUFFIXES:
            return self._error(
                f"unsupported market for code {code!r}; only A-share suffixes "
                f"{_A_SHARE_SUFFIXES} are supported"
            )

        bare = _bare_code(code)
        if not bare:
            return self._error(f"invalid code: {code!r}")

        # Use the public resolve_secid (accepts the full symbol like "600519.SH")
        # rather than the private _resolve_a_share_secid, to preserve encapsulation.
        secid = eastmoney_client.resolve_secid(code)
        if not secid:
            return self._error(f"could not resolve secid for code {code!r}")

        limit = _clamp_limit(kwargs.get("limit"))
        try:
            posts = _fetch_guba_posts(bare, limit)
        except Exception as exc:  # noqa: BLE001 - surface any fetch failure as envelope
            logger.warning("guba fetch failed for %s: %s", code, exc)
            return self._error(f"guba fetch failed: {exc}")
        return self._ok(
            "a_share",
            "guba",
            {"code": code, "secid": secid, "posts": posts},
        )

    @staticmethod
    def _ok(market: str, source: str, data: dict[str, Any]) -> str:
        """Render a success envelope as a JSON string.

        Args:
            market: Market label (``"a_share"``).
            source: Upstream provider name (``"guba"``).
            data: The payload mapping.

        Returns:
            ``{"ok": true, "market": ..., "source": ..., "data": ...}`` as JSON.
        """
        return json.dumps(
            {"ok": True, "market": market, "source": source, "data": data},
            ensure_ascii=False,
        )

    @staticmethod
    def _error(message: str) -> str:
        """Render a failure envelope as a JSON string.

        Args:
            message: Human-readable error text.

        Returns:
            ``{"ok": false, "error": message}`` as a JSON string.
        """
        return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
