"""Unit tests for Feishu/Lark channel module.

Covers: FeishuConfig, _extract_post_content, _detect_msg_format,
_markdown_to_post, _resolve_mentions, _parse_md_table.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.channels.feishu import (
    FeishuConfig,
    _extract_post_content,
)


# ---------------------------------------------------------------------------
# FeishuConfig
# ---------------------------------------------------------------------------

class TestFeishuConfig:
    """Tests for the FeishuConfig Pydantic model defaults and parsing."""

    def test_defaults(self) -> None:
        cfg = FeishuConfig()
        assert cfg.enabled is False
        assert cfg.app_id == ""
        assert cfg.app_secret == ""
        assert cfg.domain == "feishu"
        assert cfg.group_policy == "mention"
        assert cfg.streaming is True
        assert cfg.topic_isolation is True
        assert cfg.reply_to_message is False
        assert cfg.react_emoji == "THUMBSUP"
        assert cfg.done_emoji is None
        assert cfg.tool_hint_prefix == "\U0001f527"
        assert cfg.allow_from == []
        assert cfg.encrypt_key == ""
        assert cfg.verification_token == ""

    def test_from_dict_parsing(self) -> None:
        data = {
            "app_id": "cli_test123",
            "app_secret": "secret_abc",
            "domain": "lark",
            "enabled": True,
            "group_policy": "open",
            "streaming": False,
            "react_emoji": "OK",
            "done_emoji": "DONE",
        }
        cfg = FeishuConfig.model_validate(data)
        assert cfg.app_id == "cli_test123"
        assert cfg.app_secret == "secret_abc"
        assert cfg.domain == "lark"
        assert cfg.enabled is True
        assert cfg.group_policy == "open"
        assert cfg.streaming is False
        assert cfg.react_emoji == "OK"
        assert cfg.done_emoji == "DONE"

    def test_from_dict_partial(self) -> None:
        """Only supplied fields override; others stay at defaults."""
        cfg = FeishuConfig.model_validate({"app_id": "cli_abc"})
        assert cfg.app_id == "cli_abc"
        assert cfg.app_secret == ""
        assert cfg.enabled is False
        assert cfg.domain == "feishu"


# ---------------------------------------------------------------------------
# _extract_post_content
# ---------------------------------------------------------------------------

class TestExtractPostContent:
    """Tests for _extract_post_content with three payload formats."""

    def test_direct_format_simple(self) -> None:
        payload = {
            "title": "Hello",
            "content": [
                [
                    {"tag": "text", "text": "Hello world"},
                ],
            ],
        }
        text, imgs = _extract_post_content(payload)
        assert text == "Hello Hello world"
        assert imgs == []

    def test_direct_format_with_at_and_image(self) -> None:
        payload = {
            "title": "Chat",
            "content": [
                [
                    {"tag": "text", "text": "Hi "},
                    {"tag": "at", "user_name": "Alice"},
                    {"tag": "text", "text": " check "},
                    {"tag": "img", "image_key": "img_abc123"},
                ],
            ],
        }
        text, imgs = _extract_post_content(payload)
        assert "Hi" in text
        assert "@Alice" in text
        assert imgs == ["img_abc123"]

    def test_direct_format_with_code_block(self) -> None:
        payload = {
            "title": "Code",
            "content": [
                [
                    {"tag": "code_block", "language": "python", "text": "print(1)"},
                ],
            ],
        }
        text, imgs = _extract_post_content(payload)
        assert "python" in text
        assert "print(1)" in text
        assert imgs == []

    def test_localized_format_zh_cn(self) -> None:
        payload = {
            "zh_cn": {
                "title": "标题",
                "content": [
                    [
                        {"tag": "text", "text": "你好"},
                    ],
                ],
            },
        }
        text, imgs = _extract_post_content(payload)
        assert text == "标题 你好"
        assert imgs == []

    def test_localized_format_en_us(self) -> None:
        payload = {
            "en_us": {
                "title": "Title",
                "content": [
                    [
                        {"tag": "text", "text": "Hello"},
                    ],
                ],
            },
        }
        text, imgs = _extract_post_content(payload)
        assert text == "Title Hello"

    def test_wrapped_post_format(self) -> None:
        payload = {
            "post": {
                "zh_cn": {
                    "title": "Post Title",
                    "content": [
                        [
                            {"tag": "text", "text": "Body text"},
                            {"tag": "a", "text": "Link", "href": "https://example.com"},
                        ],
                    ],
                },
            },
        }
        text, imgs = _extract_post_content(payload)
        assert "Post Title" in text
        assert "Body text" in text
        assert imgs == []

    def test_empty_dict(self) -> None:
        text, imgs = _extract_post_content({})
        assert text == ""
        assert imgs == []

    def test_non_dict_input(self) -> None:
        text, imgs = _extract_post_content("not a dict")  # type: ignore[arg-type]
        assert text == ""
        assert imgs == []


# ---------------------------------------------------------------------------
# _detect_msg_format
# ---------------------------------------------------------------------------

class TestDetectMsgFormat:
    """Tests for FeishuChannel._detect_msg_format classmethod."""

    @staticmethod
    def _get_fn():
        from src.channels.feishu import FeishuChannel
        return FeishuChannel._detect_msg_format

    def test_short_plain_text(self) -> None:
        fn = self._get_fn()
        assert fn("Hello") == "text"

    def test_text_with_link(self) -> None:
        fn = self._get_fn()
        assert fn("Check [this](https://example.com) out") == "post"

    def test_code_block_is_interactive(self) -> None:
        fn = self._get_fn()
        assert fn("Here is code:\n```python\nprint(1)\n```") == "interactive"

    def test_table_is_interactive(self) -> None:
        fn = self._get_fn()
        content = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        assert fn(content) == "interactive"

    def test_heading_is_interactive(self) -> None:
        fn = self._get_fn()
        assert fn("# Heading\nSome text") == "interactive"

    def test_bold_is_interactive(self) -> None:
        fn = self._get_fn()
        assert fn("This is **bold** text") == "interactive"

    def test_long_text_is_interactive(self) -> None:
        fn = self._get_fn()
        long_text = "A" * 2500  # exceeds _POST_MAX_LEN=2000
        assert fn(long_text) == "interactive"

    def test_medium_plain_text_is_post(self) -> None:
        fn = self._get_fn()
        medium = "B" * 300  # between _TEXT_MAX_LEN=200 and _POST_MAX_LEN=2000
        assert fn(medium) == "post"

    def test_unordered_list_is_interactive(self) -> None:
        fn = self._get_fn()
        assert fn("- item 1\n- item 2") == "interactive"

    def test_ordered_list_is_interactive(self) -> None:
        fn = self._get_fn()
        assert fn("1. first\n2. second") == "interactive"


# ---------------------------------------------------------------------------
# _markdown_to_post
# ---------------------------------------------------------------------------

class TestMarkdownToPost:
    """Tests for FeishuChannel._markdown_to_post classmethod."""

    @staticmethod
    def _get_fn():
        from src.channels.feishu import FeishuChannel
        return FeishuChannel._markdown_to_post

    def test_plain_text(self) -> None:
        fn = self._get_fn()
        result = fn("Hello world")
        parsed = json.loads(result)
        content = parsed["zh_cn"]["content"]
        assert len(content) == 1
        assert content[0][0]["tag"] == "text"
        assert content[0][0]["text"] == "Hello world"

    def test_with_link(self) -> None:
        fn = self._get_fn()
        result = fn("Check [Google](https://google.com)")
        parsed = json.loads(result)
        elements = parsed["zh_cn"]["content"][0]
        assert elements[0]["tag"] == "text"
        assert elements[0]["text"] == "Check "
        assert elements[1]["tag"] == "a"
        assert elements[1]["text"] == "Google"
        assert elements[1]["href"] == "https://google.com"

    def test_multiple_links(self) -> None:
        fn = self._get_fn()
        result = fn("[A](https://a.com) and [B](https://b.com)")
        parsed = json.loads(result)
        elements = parsed["zh_cn"]["content"][0]
        tags = [e["tag"] for e in elements]
        assert tags == ["a", "text", "a"]
        assert elements[0]["href"] == "https://a.com"
        assert elements[2]["href"] == "https://b.com"

    def test_multiline(self) -> None:
        fn = self._get_fn()
        result = fn("Line 1\nLine 2")
        parsed = json.loads(result)
        content = parsed["zh_cn"]["content"]
        assert len(content) == 2
        assert content[0][0]["text"] == "Line 1"
        assert content[1][0]["text"] == "Line 2"

    def test_empty_line_preserved(self) -> None:
        fn = self._get_fn()
        result = fn("Line 1\n\nLine 3")
        parsed = json.loads(result)
        content = parsed["zh_cn"]["content"]
        assert len(content) == 3
        # Middle paragraph should be empty text tag
        assert content[1][0]["tag"] == "text"
        assert content[1][0]["text"] == ""


# ---------------------------------------------------------------------------
# _resolve_mentions
# ---------------------------------------------------------------------------

class TestResolveMentions:
    """Tests for FeishuChannel._resolve_mentions static method."""

    @staticmethod
    def _get_fn():
        from src.channels.feishu import FeishuChannel
        return FeishuChannel._resolve_mentions

    def _make_mention(self, key: str, name: str, open_id: str = "", user_id: str = ""):
        mention = MagicMock()
        mention.key = key
        mention.name = name
        id_obj = MagicMock()
        id_obj.open_id = open_id
        id_obj.user_id = user_id
        mention.id = id_obj
        return mention

    def test_no_mentions(self) -> None:
        fn = self._get_fn()
        assert fn("hello world", None) == "hello world"
        assert fn("hello world", []) == "hello world"

    def test_empty_text(self) -> None:
        fn = self._get_fn()
        m = self._make_mention("@_user_1", "Alice")
        assert fn("", [m]) == ""

    def test_replace_single_mention(self) -> None:
        fn = self._get_fn()
        m = self._make_mention("@_user_1", "Alice", open_id="ou_abc", user_id="u123")
        result = fn("Hi @_user_1", [m])
        assert result == "Hi @Alice (ou_abc, user id: u123)"

    def test_replace_mention_open_id_only(self) -> None:
        fn = self._get_fn()
        m = self._make_mention("@_user_1", "Bob", open_id="ou_xyz")
        result = fn("@_user_1 says hi", [m])
        assert result == "@Bob (ou_xyz) says hi"

    def test_replace_mention_no_ids(self) -> None:
        fn = self._get_fn()
        m = self._make_mention("@_user_1", "Carol")
        result = fn("@_user_1", [m])
        assert result == "@Carol"

    def test_mention_not_in_text(self) -> None:
        fn = self._get_fn()
        m = self._make_mention("@_user_2", "Dave")
        result = fn("Hello @_user_1", [m])
        assert result == "Hello @_user_1"  # unchanged

    def test_mention_boundary_not_partial_match(self) -> None:
        """@_user_1 should not match @_user_10 due to word boundary."""
        fn = self._get_fn()
        m = self._make_mention("@_user_1", "Alice")
        result = fn("@_user_10 is here", [m])
        assert result == "@_user_10 is here"  # not matched

    def test_multiple_mentions(self) -> None:
        fn = self._get_fn()
        m1 = self._make_mention("@_user_1", "Alice", open_id="ou_a")
        m2 = self._make_mention("@_user_2", "Bob", open_id="ou_b", user_id="u2")
        result = fn("@_user_1 and @_user_2", [m1, m2])
        assert result == "@Alice (ou_a) and @Bob (ou_b, user id: u2)"

    def test_mention_with_no_key(self) -> None:
        fn = self._get_fn()
        m = self._make_mention("", "Alice")  # key is empty
        m.key = None
        result = fn("@_user_1", [m])
        assert result == "@_user_1"  # skipped


# ---------------------------------------------------------------------------
# _parse_md_table
# ---------------------------------------------------------------------------

class TestParseMdTable:
    """Tests for FeishuChannel._parse_md_table classmethod."""

    @staticmethod
    def _get_fn():
        from src.channels.feishu import FeishuChannel
        return FeishuChannel._parse_md_table

    def test_valid_table(self) -> None:
        fn = self._get_fn()
        table = "| Name | Age |\n| --- | --- |\n| Alice | 30 |\n| Bob | 25 |"
        result = fn(table)
        assert result is not None
        assert result["tag"] == "table"
        assert len(result["columns"]) == 2
        assert result["columns"][0]["display_name"] == "Name"
        assert result["columns"][1]["display_name"] == "Age"
        assert len(result["rows"]) == 2
        assert result["rows"][0]["c0"] == "Alice"
        assert result["rows"][1]["c1"] == "25"

    def test_table_with_markdown_formatting(self) -> None:
        fn = self._get_fn()
        table = "| **Name** | Age |\n| --- | --- |\n| *Alice* | 30 |"
        result = fn(table)
        assert result is not None
        # Bold/italic markers should be stripped
        assert result["columns"][0]["display_name"] == "Name"
        assert result["rows"][0]["c0"] == "Alice"

    def test_too_few_lines_returns_none(self) -> None:
        fn = self._get_fn()
        assert fn("| A | B |") is None  # only header, no separator + data

    def test_header_only_with_separator(self) -> None:
        fn = self._get_fn()
        # header + separator but no data rows → < 3 lines after strip
        table = "| A | B |\n| --- | --- |"
        assert fn(table) is None

    def test_empty_input(self) -> None:
        fn = self._get_fn()
        assert fn("") is None

    def test_single_column_table(self) -> None:
        fn = self._get_fn()
        table = "| Value |\n| --- |\n| 1 |\n| 2 |"
        result = fn(table)
        assert result is not None
        assert len(result["columns"]) == 1
        assert len(result["rows"]) == 2

    def test_table_with_uneven_columns(self) -> None:
        fn = self._get_fn()
        table = "| A | B |\n| --- | --- |\n| 1 |"
        result = fn(table)
        assert result is not None
        # Second column missing → empty string
        assert result["rows"][0]["c1"] == ""
