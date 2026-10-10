import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape, quoteattr

from tests import test_auto_reply_cli_runtime as runtime_helpers


class GeekNewsDigestTests(unittest.TestCase):
    _load_auto_reply_module = staticmethod(
        runtime_helpers.AutoReplyCliRuntimeTests._load_auto_reply_module
    )

    def setUp(self):
        self.module = self._load_auto_reply_module("auto_reply_geeknews_format_test")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.module.QUEUE = Path(temporary.name) / "reply-queue.sqlite3"
        self.now = datetime(2026, 1, 2, 0, 5, tzinfo=timezone.utc).timestamp()
        for target, name in (
            (self.module, "_open_pinned_link"),
            (self.module, "generate_reply"),
            (self.module.subprocess, "run"),
            (self.module.subprocess, "Popen"),
        ):
            patcher = mock.patch.object(
                target, name, side_effect=AssertionError("digest must stay offline")
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def item(topic_id, *, title="Release (preview)", summary="", url=None):
        return {
            "id": topic_id,
            "title": title,
            "summary": summary,
            "url": url if url is not None else f"https://news.hada.io/topic?id={topic_id}",
        }

    @staticmethod
    def feed(items):
        return '<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(
            f"<entry><title>{escape(item['title'])}</title>"
            f"<link href={quoteattr(item['url'])}/>"
            f"<summary>{escape(item['summary'])}</summary></entry>"
            for item in items
        ) + "</feed>"

    def format(self, items):
        return self.module._format_geeknews_top3_line(items, now=self.now)

    def test_top5_layout_has_distinct_title_summary_and_canonical_link_lines(self):
        items = [
            self.item(i, title=f"Release {i} (preview)", summary=f"Source detail {i}.")
            for i in range(1, 7)
        ]
        digest = self.module._next_geeknews_digest(
            fetcher=lambda: self.feed(items), now=self.now, persist_seen=False
        )
        self.assertEqual(digest["ids"], [1, 2, 3, 4, 5])
        self.assertEqual(digest["message"], "\n\n".join([
            "GeekNews TOP5 · 2026-01-02 09:05 KST",
            *[
                f"{i}. Release {i} (preview)\nSource detail {i}.\n"
                f"https://news.hada.io/topic?id={i}"
                for i in range(1, 6)
            ],
        ]))
        self.assertEqual(self.module.GEEKNEWS_FEED_URL, "https://news.hada.io/rss/news")
        self.assertEqual(len(self.module.GEEKNEWS_DAILY_SLOTS), 3)

    def test_atom_cdata_content_supplies_actual_summary(self):
        feed = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
            <title><![CDATA[엔진 릴리스 (실험적)]]></title>
            <link rel="self" href="https://example.invalid/feed/10"/>
            <link rel="alternate" href="https://news.hada.io/topic?id=10"/>
            <content type="html"><![CDATA[<p>메모리 사용량을 <b>측정하는</b></p>
            <p>도구를 추가했다.</p><script>hidden()</script>]]></content>
            </entry></feed>"""
        items = self.module._parse_geeknews_entries(feed)
        self.assertEqual(items[0]["summary"], "메모리 사용량을 측정하는 도구를 추가했다.")
        self.assertIn(
            "1. 엔진 릴리스 (실험적)\n메모리 사용량을 측정하는 도구를 추가했다.\n"
            "https://news.hada.io/topic?id=10", self.format(items)
        )

    def test_atom_summary_handles_escaped_html_and_takes_precedence_over_content(self):
        feed = """<a:feed xmlns:a="http://www.w3.org/2005/Atom"><a:entry>
            <a:title type="html">API &amp;amp; tools (beta)</a:title>
            <a:link href="https://news.hada.io/topic?id=12"/>
            <a:summary type="html">&lt;p&gt;Source &amp;amp; detail.&lt;/p&gt;</a:summary>
            <a:content type="html">Longer content.</a:content>
            </a:entry></a:feed>"""
        items = self.module._parse_geeknews_entries(feed)
        self.assertEqual(items[0]["title"], "API & tools (beta)")
        self.assertEqual(items[0]["summary"], "Source & detail.")

    def test_xhtml_content_preserves_block_spacing(self):
        feed = """<feed><entry><title>Release</title>
            <link href="https://news.hada.io/topic?id=13"/>
            <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">
            <p>First detail.</p><p>Second detail.</p></div></content>
            </entry></feed>"""
        items = self.module._parse_geeknews_entries(feed)
        self.assertEqual(items[0]["summary"], "First detail. Second detail.")

    def test_plain_text_comparisons_and_qualifiers_are_preserved(self):
        title = "Speed < 10 (forecast, not measured) and memory > 2"
        items = self.module._parse_geeknews_entries(self.feed([self.item(14, title=title)]))
        self.assertEqual(items[0]["title"], title)
        self.assertIn(f"1. {title}\nhttps://news.hada.io/topic?id=14", self.format(items))

    def test_missing_or_repeated_summary_is_omitted_without_filler(self):
        for summary in ("", None, "  release\n(preview).  "):
            with self.subTest(summary=summary):
                self.assertEqual(
                    self.format([self.item(15, summary=summary)]).split("\n\n")[1],
                    "1. Release (preview)\nhttps://news.hada.io/topic?id=15",
                )

    def test_whitespace_is_collapsed_without_extra_blocks(self):
        message = self.format([
            self.item(16, title="  릴리스\n\n (예정)\t", summary=" 첫째\n\n둘째\t 셋째\u00a0 ")
        ])
        self.assertEqual(message.split("\n\n")[1],
                         "1. 릴리스 (예정)\n첫째 둘째 셋째\nhttps://news.hada.io/topic?id=16")

    def test_title_summary_and_total_are_bounded_with_visible_truncation(self):
        items = [
            self.item(9_999_999_999 - i, title="제목" * 200 + " (예정)", summary="요약 " * 200)
            for i in range(5)
        ]
        for source in (items, self.module._parse_geeknews_entries(self.feed(items))):
            message = self.format(source)
            blocks = message.split("\n\n")
            self.assertEqual(len(blocks), 6)
            self.assertLessEqual(len(message), 1500)
            self.assertLessEqual(len(message.encode("utf-8")), self.module.MAX_MESSAGE_BYTES)
            for block in blocks[1:]:
                title, summary, url = block.splitlines()
                self.assertLessEqual(len(title[3:]), 140)
                self.assertTrue(title.endswith("…"))
                self.assertLessEqual(len(summary), 90)
                self.assertTrue(summary.endswith("…"))
                self.assertRegex(url, r"^https://news\.hada\.io/topic\?id=[1-9][0-9]{9}$")

    def test_exact_field_limits_do_not_add_ellipsis(self):
        title, summary = "제" * 140, "요" * 90
        block = self.format([self.item(17, title=title, summary=summary)]).split("\n\n")[1]
        self.assertEqual(block, f"1. {title}\n{summary}\nhttps://news.hada.io/topic?id=17")

    def test_long_identical_summary_is_omitted_before_truncation(self):
        title = "소스 제목 " * 100
        items = self.module._parse_geeknews_entries(self.feed([
            self.item(18, title=title, summary=title)
        ]))
        self.assertEqual(items[0]["summary"], "")
        self.assertEqual(len(self.format(items).split("\n\n")[1].splitlines()), 2)

    def test_invalid_urls_are_rejected_by_parser_and_formatter(self):
        urls = (
            "https://example.invalid/topic?id=20",
            "https://news.hada.io.evil.invalid/topic?id=20",
            "https://news.hada.io@evil.invalid/topic?id=20",
            "https://user@news.hada.io/topic?id=20",
            "http://news.hada.io/topic?id=20",
            "javascript:alert(1)",
            "//news.hada.io/topic?id=20",
            "https://news.hada.io:443/topic?id=20",
            "https://news.hada.io/other?id=20",
            "https://news.hada.io/topic?id=20&next=https://example.invalid",
            "https://news.hada.io/topic?id=20&id=21",
            "https://news.hada.io/topic?id=20#fragment",
            "https://news.hada.io/topic?id=0",
            "https://news.hada.io/topic?id=-20",
            "https://news.hada.io/topic?id=020",
            "https://news.hada.io/topic?id=10000000000",
            "https://news.hada.io/topic?id=%32%30",
            "https://news.hada.io/topic?id=２０",
            "https://news.hada.io/topic?id=20\nhttps://example.invalid",
            " https://news.hada.io/topic?id=20",
            "",
        )
        for url in urls:
            with self.subTest(url=url):
                item = self.item(20, url=url)
                self.assertEqual(self.module._parse_geeknews_entries(self.feed([item])), [])
                self.assertEqual(self.format([item]), "")

    def test_invalid_and_duplicate_entries_do_not_consume_top5_places(self):
        first = self.item(1, title="First occurrence")
        duplicate = self.item(999, title="Duplicate", url=first["url"])
        items = [
            self.item(20, url="https://example.invalid/topic?id=20"),
            self.item(21, title="  \n "), first, duplicate,
            *[self.item(i, title=f"Release {i}") for i in range(2, 7)],
        ]
        parsed = self.module._parse_geeknews_entries(self.feed(items))
        self.assertEqual([item["id"] for item in parsed], [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.format([None, {}, *items]), self.format(parsed))
        digest = self.module._next_geeknews_digest(
            fetcher=lambda: self.feed(items), now=self.now, persist_seen=False
        )
        self.assertEqual(digest["ids"], [1, 2, 3, 4, 5])
        self.assertNotIn("Duplicate", digest["message"])
        self.assertNotIn("Release 6", digest["message"])

    def test_malformed_xml_produces_no_digest(self):
        self.assertEqual(self.module._parse_geeknews_entries("<feed><entry>"), [])
        self.assertIsNone(self.module._next_geeknews_digest(
            fetcher=lambda: "<feed><entry>", now=self.now, persist_seen=False
        ))

    def test_preview_preserves_existing_seen_and_slot_bytes(self):
        path = self.module._geeknews_cursor_path()
        before = json.dumps({
            "seen_ids": [1], "newest_id": 1,
            "posted_slots": ["2026-01-01:morning"], "updated_at": 123,
        }).encode("utf-8")
        path.write_bytes(before)
        path.chmod(0o600)
        feed = self.feed([self.item(1), self.item(2, summary="Source detail.")])
        with mock.patch.object(self.module, "_store_geeknews_seen_ids") as store:
            first = self.module._next_geeknews_digest(
                fetcher=lambda: feed, now=self.now, persist_seen=False
            )
            second = self.module._next_geeknews_digest(
                fetcher=lambda: feed, now=self.now, persist_seen=False
            )
        store.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(first["ids"], [2])
        self.assertEqual(path.read_bytes(), before)

    def test_preview_does_not_create_a_cursor(self):
        feed = self.feed([self.item(1)])
        self.assertIsNotNone(self.module._next_geeknews_digest(
            fetcher=lambda: feed, now=self.now, persist_seen=False
        ))
        self.assertFalse(self.module._geeknews_cursor_path().exists())


if __name__ == "__main__":
    unittest.main()
