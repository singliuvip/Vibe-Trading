"""Tests for the A-share social-sentiment (Guba) tool.

No request leaves the process: the Eastmoney HTTP boundary
(:func:`backtest.loaders.eastmoney_client.throttled_get_text`) is mocked so the
real client + tool parsing run fully offline.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from backtest.loaders import eastmoney_client
from src.tools.a_share_social_sentiment_tool import (
    AShareSocialSentimentTool,
    _bare_code,
    _clamp_limit,
    _clean_text,
    _parse_guba_posts,
    _suffix_of,
)


def _guba_html(posts: list[dict[str, Any]]) -> str:
    """Render a minimal Guba list-page HTML carrying the given post rows."""
    rows = []
    for p in posts:
        rows.append(
            f"""
            <span class="l3">
              <a href="{p['href']}" title="{p['title']}">{p['title']}</a>
            </span>
            <span class="l4">
              <a href="https://iguba.eastmoney.com/u,{p['author']}">{p['author']}</a>
            </span>
            <span class="l5 date">{p['published']}</span>
            <span class="l6">{p['read']}</span>
            <span class="l7">{p['comment']}</span>
            """
        )
    return "<html><body>" + "".join(rows) + "</body></html>"


def _sample_posts() -> list[dict[str, Any]]:
    """Two Guba post rows for 600519."""
    return [
        {
            "href": "/news,600519,123456.html",
            "title": "茅台一季度业绩超预期",
            "author": "股民老王",
            "published": "2024-04-30 08:30:00",
            "read": "12345",
            "comment": "678",
        },
        {
            "href": "/news,600519,789012.html",
            "title": "白酒板块全线走强",
            "author": "价值投资者",
            "published": "2024-04-29 18:00:00",
            "read": "5432",
            "comment": "90",
        },
    ]


class TestHelpers:
    def test_suffix_of(self) -> None:
        assert _suffix_of("600519.SH") == "SH"
        assert _suffix_of("000001.SZ") == "SZ"
        assert _suffix_of("830799.BJ") == "BJ"
        assert _suffix_of("AAPL.US") == "US"
        assert _suffix_of("NOSUFFIX") == ""

    def test_bare_code(self) -> None:
        assert _bare_code("600519.SH") == "600519"
        assert _bare_code(" 000001.SZ ") == "000001"

    def test_clamp_limit(self) -> None:
        assert _clamp_limit(None) == 50
        assert _clamp_limit("garbage") == 50
        assert _clamp_limit(0) == 1
        assert _clamp_limit(999) == 100
        assert _clamp_limit(10) == 10

    def test_clean_text_strips_tags(self) -> None:
        assert _clean_text(None) == ""
        assert _clean_text("<b>hello</b>") == "hello"
        assert _clean_text("  a   b  ") == "a b"

    def test_clean_text_trims_long(self) -> None:
        long = "x" * 200
        out = _clean_text(long)
        assert len(out) <= 121
        assert out.endswith("…")

    def test_parse_guba_posts_basic(self) -> None:
        html = _guba_html(_sample_posts())
        posts = _parse_guba_posts(html, 10)
        assert len(posts) == 2
        first = posts[0]
        assert first["title"] == "茅台一季度业绩超预期"
        assert first["author"] == "股民老王"
        assert first["published"] == "2024-04-30 08:30:00"
        assert first["read_count"] == 12345
        assert first["comment_count"] == 678
        assert first["url"] == "https://guba.eastmoney.com/news,600519,123456.html"

    def test_parse_guba_posts_respects_limit(self) -> None:
        html = _guba_html(_sample_posts())
        posts = _parse_guba_posts(html, 1)
        assert len(posts) == 1

    def test_parse_guba_posts_empty_html(self) -> None:
        assert _parse_guba_posts("<html></html>", 10) == []

    def test_parse_guba_posts_missing_fields(self) -> None:
        # A post anchor with no surrounding numeric spans still yields a record.
        html = '<a href="/news,600519,1.html">孤帖</a>'
        posts = _parse_guba_posts(html, 10)
        assert len(posts) == 1
        assert posts[0]["title"] == "孤帖"
        assert posts[0]["author"] is None
        assert posts[0]["read_count"] is None
        assert posts[0]["comment_count"] is None


class TestToolContract:
    def test_name_and_schema(self) -> None:
        tool = AShareSocialSentimentTool()
        assert tool.name == "get_a_share_social_sentiment"
        assert tool.is_readonly is True
        assert tool.parameters["required"] == ["code"]
        assert tool.parameters["properties"]["source"]["enum"] == ["guba"]
        desc = tool.description.lower()
        assert "guba" in desc
        assert "a-share" in desc or "a share" in desc


class TestExecuteSuccess:
    def test_a_share_guba_posts(self) -> None:
        tool = AShareSocialSentimentTool()
        with patch.object(
            eastmoney_client, "throttled_get_text", return_value=_guba_html(_sample_posts())
        ) as http:
            out = json.loads(tool.execute(code="600519.SH", limit=10))

        http.assert_called_once()
        _, kwargs = http.call_args
        assert kwargs["host_key"] == "eastmoney"

        assert out["ok"] is True
        assert out["market"] == "a_share"
        assert out["source"] == "guba"
        assert out["data"]["code"] == "600519.SH"
        assert out["data"]["secid"] == "1.600519"
        assert len(out["data"]["posts"]) == 2
        first = out["data"]["posts"][0]
        assert first["title"] == "茅台一季度业绩超预期"
        assert first["read_count"] == 12345
        assert first["url"].startswith("https://guba.eastmoney.com/")

    def test_sz_suffix_resolves_secid(self) -> None:
        tool = AShareSocialSentimentTool()
        with patch.object(
            eastmoney_client, "throttled_get_text", return_value=_guba_html(_sample_posts())
        ):
            out = json.loads(tool.execute(code="000001.SZ"))

        assert out["ok"] is True
        assert out["data"]["secid"] == "0.000001"

    def test_bj_suffix_resolves_secid(self) -> None:
        tool = AShareSocialSentimentTool()
        with patch.object(
            eastmoney_client, "throttled_get_text", return_value=_guba_html(_sample_posts())
        ):
            out = json.loads(tool.execute(code="830799.BJ"))

        assert out["ok"] is True
        assert out["data"]["secid"] == "0.830799"

    def test_limit_is_clamped_before_request(self) -> None:
        tool = AShareSocialSentimentTool()
        with patch.object(
            eastmoney_client, "throttled_get_text", return_value=_guba_html(_sample_posts())
        ) as http:
            out = json.loads(tool.execute(code="600519.SH", limit=999))

        assert out["ok"] is True
        # The parser is capped at the clamped limit (100), not the raw 999.
        assert len(out["data"]["posts"]) <= 100
        # URL built from the bare code, not the suffix.
        assert "list,600519.html" in http.call_args.args[0]

    def test_empty_html_returns_empty_posts(self) -> None:
        tool = AShareSocialSentimentTool()
        with patch.object(eastmoney_client, "throttled_get_text", return_value="<html></html>"):
            out = json.loads(tool.execute(code="600519.SH"))

        assert out["ok"] is True
        assert out["data"]["posts"] == []


class TestExecuteError:
    def test_missing_code(self) -> None:
        out = json.loads(AShareSocialSentimentTool().execute())
        assert out["ok"] is False
        assert "code" in out["error"]

    def test_non_a_share_code(self) -> None:
        out = json.loads(AShareSocialSentimentTool().execute(code="AAPL.US"))
        assert out["ok"] is False
        assert "a-share" in out["error"].lower() or "a share" in out["error"].lower()

    def test_hk_code_rejected(self) -> None:
        out = json.loads(AShareSocialSentimentTool().execute(code="00700.HK"))
        assert out["ok"] is False

    def test_unsupported_source(self) -> None:
        out = json.loads(
            AShareSocialSentimentTool().execute(code="600519.SH", source="weibo")
        )
        assert out["ok"] is False
        assert "source" in out["error"]

    def test_http_failure_envelope(self) -> None:
        tool = AShareSocialSentimentTool()
        with patch.object(
            eastmoney_client,
            "throttled_get_text",
            side_effect=RuntimeError("eastmoney banned"),
        ):
            out = json.loads(tool.execute(code="600519.SH"))

        assert out["ok"] is False
        assert "eastmoney banned" in out["error"]
        assert "guba fetch failed" in out["error"]
