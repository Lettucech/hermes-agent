"""Unit tests for the Slack Block Kit renderer (pure function, no adapter)."""

from plugins.platforms.slack.block_kit import (
    MAX_BLOCKS,
    MAX_SECTION_TEXT,
    render_blocks,
    sanitize_blocks,
)


def _types(blocks):
    return [b["type"] for b in blocks]


class TestRenderBlocksBasics:
    def test_empty_returns_none(self):
        assert render_blocks("") is None
        assert render_blocks("   \n  ") is None


    def test_header_becomes_header_block(self):
        blocks = render_blocks("# Title")
        assert blocks[0]["type"] == "header"
        assert blocks[0]["text"]["type"] == "plain_text"
        assert blocks[0]["text"]["text"] == "Title"


class TestNestedLists:
    def test_nested_bullets_produce_increasing_indent(self):
        md = "- a\n  - b\n    - c"
        blocks = render_blocks(md)
        rich = next(b for b in blocks if b["type"] == "rich_text")
        indents = [e["indent"] for e in rich["elements"] if e["type"] == "rich_text_list"]
        # true nesting: indent levels must strictly increase across the run
        assert indents == sorted(indents)
        assert max(indents) >= 2
        assert min(indents) == 0


class TestInlineFormatting:

    def test_slack_mrkdwn_link_in_bullet_becomes_link_element(self):
        """rich_text lists must parse Slack <url|label>, not emit it as text.

        rich_blocks turns markdown bullets into rich_text_list. rich_text does
        not interpret mrkdwn, so <url|text> has to become type=link.
        """
        blocks = render_blocks(
            "- <https://example.com/x|GitLab #1> — allow `in_progress`"
        )
        assert blocks is not None
        rich = next(b for b in blocks if b["type"] == "rich_text")
        els = rich["elements"][0]["elements"][0]["elements"]
        links = [e for e in els if e.get("type") == "link"]
        assert len(links) == 1
        assert links[0]["url"] == "https://example.com/x"
        assert links[0]["text"] == "GitLab #1"
        assert not any("<https://" in (e.get("text") or "") for e in els)
        assert any(e.get("style", {}).get("code") for e in els)

    def test_slack_mentions_in_bullet_are_not_links(self):
        blocks = render_blocks("- ping <@U123> in <#C456>")
        assert blocks is not None
        els = blocks[0]["elements"][0]["elements"][0]["elements"]
        assert all(e.get("type") != "link" for e in els)
        blob = "".join(e.get("text") or "" for e in els)
        assert "<@U123>" in blob
        assert "<#C456>" in blob


    def test_blank_line_separated_ordered_items_stay_in_one_list(self):
        """Regression: blank lines between ordered items must not reset numbering.

        Slack numbers each rich_text_list independently.  If blank lines break
        the list run, N items produce N separate lists each starting at 1.
        See: https://github.com/NousResearch/hermes-agent/issues/57076
        """
        md = "1. alpha\n\n1. beta\n\n1. gamma"
        blocks = render_blocks(md)
        rich = next(b for b in blocks if b["type"] == "rich_text")
        lists = [e for e in rich["elements"] if e["type"] == "rich_text_list"]
        # Must be ONE list with 3 items, not 3 separate single-item lists
        assert len(lists) == 1
        items = lists[0]["elements"]
        assert len(items) == 3


class TestTables:
    def test_pipe_table_renders_native_table_block(self):
        md = (
            "| Name | Status |\n"
            "|------|--------|\n"
            "| a | ok |\n"
            "| b | fail |"
        )
        blocks = render_blocks(md)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "table"
        rows = blocks[0]["rows"]
        # header + 2 body rows, 2 columns each
        assert len(rows) == 3
        assert all(len(r) == 2 for r in rows)
        # cells are rich_text carrying the values
        assert str(rows[0]).count("Name") == 1
        assert "fail" in str(rows[2])


    def test_oversized_table_falls_back_to_monospace(self):
        # 120 rows > MAX_TABLE_ROWS -> monospace rich_text fallback, not a table
        big = "| a | b |\n|---|---|\n" + "\n".join(f"| x{i} | y |" for i in range(120))
        blocks = render_blocks(big)
        assert blocks[0]["type"] == "rich_text"  # preformatted fallback
        assert blocks[0]["elements"][0]["type"] == "rich_text_preformatted"


    def test_escaped_pipe_not_a_column_separator(self):
        md = (
            "| Expr | Meaning |\n"
            "|------|--------|\n"
            "| a \\| b | or |"
        )
        blocks = render_blocks(md)
        assert blocks[0]["type"] == "table"
        # the escaped-pipe cell stays a single cell containing a literal pipe
        body = blocks[0]["rows"][1]
        assert len(body) == 2
        assert "|" in str(body[0])


class TestLimits:

    def test_too_many_blocks_returns_none(self):
        # 60 dividers => 60 blocks > MAX_BLOCKS => decline (caller uses text)
        md = "\n\n".join(["---"] * (MAX_BLOCKS + 10))
        assert render_blocks(md) is None


class TestEmptyContentGuards:
    """Empty content must never produce a Slack-rejected (invalid_blocks) payload.

    Slack rejects a rich_text_section / rich_text_preformatted /
    rich_text_quote whose ``elements`` is empty or contains a zero-length
    ``text`` element, and a ``header`` whose plain_text is empty. Each guard
    below corresponds to a real chat.postMessage rejection observed in
    production ("missing element" / "must be more than 0 characters").
    """

    @staticmethod
    def _assert_schema_valid(blocks):
        def walk(o):
            if isinstance(o, dict):
                if o.get("type") in (
                    "rich_text_section", "rich_text_preformatted", "rich_text_quote"
                ):
                    assert o.get("elements"), f"empty {o['type']} elements"
                if o.get("type") == "text":
                    assert len(o.get("text", "")) > 0, "zero-length text element"
                if o.get("type") == "header":
                    assert o["text"]["text"], "empty plain_text header"
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(blocks)

    def test_ragged_and_empty_table_cells_are_schema_valid(self):
        # Blank middle cell + ragged short row (padded with "") must not emit
        # an empty section or a 0-char text element.
        md = (
            "| x | y | z |\n"
            "| --- | --- | --- |\n"
            "| 1 |  | 3 |\n"   # blank middle cell
            "| 4 |"           # ragged row -> padded with empty cells
        )
        blocks = render_blocks(md)
        assert blocks[0]["type"] == "table"
        self._assert_schema_valid(blocks)

    def test_empty_code_fence_quote_and_list_item_are_schema_valid(self):
        # Empty fenced code block (common around empty tool output), blank
        # quote line, and empty list item must all stay schema-valid.
        md = "```\n```\n\n> \n\n- \n- real item"
        blocks = render_blocks(md)
        assert blocks is not None
        self._assert_schema_valid(blocks)


class TestSanitizeBlocks:
    """Outbound boundary clamp: one bad block must never fail the whole call.

    Regression coverage for the invalid_blocks / msg_too_long bug class
    (#56615 null column_settings, #62054 / #53693 >3000-char sections on
    approval chat.update after HTML-escaping inflation).
    """


    def test_oversized_section_text_is_clamped(self):
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": "x" * 3500}},
        ]
        out = sanitize_blocks(blocks)
        assert len(out[0]["text"]["text"]) <= MAX_SECTION_TEXT
        assert out[0]["text"]["text"].endswith("…")

    def test_html_escape_inflated_approval_update_is_clamped(self):
        # #53693 / #62054: send path budgeted the RAW text to <=3000, but the
        # interaction payload echoes it back HTML-escaped (& -> &amp;) so the
        # chat.update section exceeds the cap.
        inflated = "a" * 2990 + "&amp;" * 10  # 3040 chars
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": inflated}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "✅ ok"}]},
        ]
        out = sanitize_blocks(blocks)
        assert len(out[0]["text"]["text"]) <= MAX_SECTION_TEXT
        # context block untouched
        assert out[1] == blocks[1]

    def test_null_column_settings_entries_are_fixed(self):
        # #56615: Slack rejects null entries in table column_settings.
        table = {
            "type": "table",
            "rows": [[{"type": "rich_text", "elements": []}]],
            "column_settings": [None, {"align": "center"}, None],
        }
        out = sanitize_blocks([table])
        cs = out[0]["column_settings"]
        assert cs == [{}, {"align": "center"}]
        assert all(isinstance(c, dict) for c in cs)

    def test_all_null_column_settings_are_dropped(self):
        table = {
            "type": "table",
            "rows": [[{"type": "rich_text", "elements": []}]],
            "column_settings": [None, None],
        }
        out = sanitize_blocks([table])
        assert "column_settings" not in out[0]


class TestSplitTextFenceBalanced:
    """_split_text closes/reopens ``` fences at section chunk boundaries."""

    def test_fenced_split_every_chunk_balanced(self):
        from plugins.platforms.slack.block_kit import _split_text

        text = "```\n" + "\n".join("y" * 20 for _ in range(30)) + "\n```"
        chunks = _split_text(text, 100)
        assert len(chunks) >= 2
        for i, chunk in enumerate(chunks):
            assert chunk.count("```") % 2 == 0, (
                f"chunk {i} has unbalanced fences: {chunk[:60]!r}"
            )




class TestContainerFamily:
    def test_details_renders_collapsible_container(self):
        md = ":::details Steps\n- one\n- two\n:::"
        blocks = render_blocks(md)
        assert len(blocks) == 1
        c = blocks[0]
        assert c["type"] == "container"
        assert c["is_collapsible"] is True
        assert c["default_collapsed"] is True
        assert c["title"]["text"] == "Steps"
        assert c["child_blocks"]

    def test_details_open_defaults_expanded(self):
        blocks = render_blocks(":::details-open Notes\nhello\n:::")
        assert blocks[0]["type"] == "container"
        assert blocks[0]["default_collapsed"] is False

    def test_nested_container_flattens(self):
        md = ":::details outer\n:::details inner\nx\n:::\n:::"
        blocks = render_blocks(md)
        c = blocks[0]
        assert c["type"] == "container"
        assert all(b["type"] != "container" for b in c["child_blocks"])

    def test_over_ten_children_degrades_to_flat(self):
        body = "\n\n".join(f"para {k}" for k in range(12))
        blocks = render_blocks(f":::details many\n{body}\n:::")
        assert blocks[0]["type"] == "header"
        assert blocks[0]["text"]["text"] == "many"
        assert all(b["type"] != "container" for b in blocks)

    def test_unclosed_details_still_renders_body(self):
        blocks = render_blocks(":::details t\nhello")
        assert blocks[0]["type"] == "container"
        assert blocks[0]["child_blocks"]

    def test_empty_details_body_dropped(self):
        assert render_blocks(":::details t\n:::") is None

    def test_details_inside_fence_not_treated_as_container(self):
        md = "```\n:::details not-a-container\n```"
        blocks = render_blocks(md)
        assert _types(blocks) == ["rich_text"]


class TestContextAndImage:
    def test_context_block(self):
        blocks = render_blocks(":::context\nsmall print\nsecond line\n:::")
        assert _types(blocks) == ["context"]
        assert blocks[0]["elements"][0]["text"] == "small print"
        assert blocks[0]["elements"][1]["text"] == "second line"

    def test_context_overflow_lines_merge_into_last_element(self):
        body = "\n".join(f"line {k}" for k in range(14))
        blocks = render_blocks(f":::context\n{body}\n:::")
        assert len(blocks[0]["elements"]) == 10

    def test_standalone_image_becomes_image_block(self):
        blocks = render_blocks("![alt text](https://example.com/a.png)")
        assert _types(blocks) == ["image"]
        assert blocks[0]["image_url"] == "https://example.com/a.png"
        assert blocks[0]["alt_text"] == "alt text"

    def test_image_with_empty_alt_defaults(self):
        blocks = render_blocks("![](https://example.com/a.png)")
        assert blocks[0]["alt_text"] == "image"

    def test_mixed_text_and_image_stays_section(self):
        blocks = render_blocks("look ![x](https://e.com/i.png) here")
        assert _types(blocks) == ["section"]


class TestActionsBlock:
    def test_actions_url_buttons(self):
        md = ":::actions\n[Approve](https://x.co/a)\n[Reject](https://x.co/r)\n:::"
        blocks = render_blocks(md)
        assert _types(blocks) == ["actions"]
        btns = blocks[0]["elements"]
        assert [b["url"] for b in btns] == ["https://x.co/a", "https://x.co/r"]
        assert btns[0]["text"]["text"] == "Approve"

    def test_actions_non_button_lines_kept(self):
        md = ":::actions\nnote line\n[Go](https://x.co)\n:::"
        blocks = render_blocks(md)
        assert _types(blocks) == ["section", "actions"]

    def test_actions_without_buttons_no_actions_block(self):
        md = ":::actions\njust words\n:::"
        blocks = render_blocks(md)
        assert _types(blocks) == ["section"]


class TestSanitizeContainers:
    def test_sanitize_flattens_nested_containers(self):
        inner = {"type": "container", "title": {"type": "plain_text", "text": "in"},
                 "is_collapsible": True, "child_blocks": [{"type": "divider"}]}
        outer = {"type": "container", "title": {"type": "plain_text", "text": "out"},
                 "is_collapsible": True, "child_blocks": [inner]}
        out = sanitize_blocks([outer])
        kids = out[0]["child_blocks"]
        assert all(k["type"] != "container" for k in kids)

    def test_sanitize_drops_image_without_url(self):
        assert sanitize_blocks([{"type": "image", "alt_text": "x"}]) is None

    def test_sanitize_fills_missing_alt_text(self):
        out = sanitize_blocks([{"type": "image", "image_url": "https://e.com/i.png"}])
        assert out[0]["alt_text"] == "image"

    def test_sanitize_truncates_over_ten_children(self):
        kids = [{"type": "divider"} for _ in range(14)]
        c = {"type": "container", "title": {"type": "plain_text", "text": "t"},
             "is_collapsible": True, "child_blocks": kids}
        out = sanitize_blocks([c])
        assert len(out[0]["child_blocks"]) == 10

    def test_sanitize_drops_container_without_children(self):
        c = {"type": "container", "title": {"type": "plain_text", "text": "t"},
             "is_collapsible": True, "child_blocks": []}
        assert sanitize_blocks([c]) is None
