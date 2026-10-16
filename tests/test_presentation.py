import copy
import html
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

import app


class PageText(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.parts = []
        self.feed(markup)

    def handle_data(self, data):
        self.parts.append(data)


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.editions = []
        for hour in (12, 6, 0):
            self.editions.append({
                "slug": f"2026-10-16-{hour:02d}00",
                "title": f"🇮🇪 爱尔兰新闻简报｜2026-10-16 {hour:02d}:00｜住房 &amp; IT 优先",
                "scheduled_at": f"2026-10-16T{hour:02d}:00:00+01:00",
                "published_at": f"2026-10-16T{hour:02d}:05:00+01:00",
                "item_count": 1,
                "items": [{
                    "category": "other", "region": "West Dublin", "is_update": True,
                    "title": "标题 &amp; 后续", "summary": "摘要 &quot;内容&quot;",
                    "source_name": "RTÉ", "source_url": "https://example.com/news?a=1&b=2",
                    "published_at": "2026-10-16T07:43:39Z",
                }],
            })
        self.by_slug = {ed["slug"]: ed for ed in self.editions}
        fake = patch.object(app, "store")
        self.store = fake.start()
        self.addCleanup(fake.stop)
        self.store.latest.return_value = self.editions[0]
        self.store.list_editions.return_value = self.editions
        self.store.get.side_effect = self.by_slug.get

    def test_both_navigation_bars_point_in_chronological_order(self):
        page = app.edition(self.editions[1]["slug"])
        self.assertEqual(page.count('href="/edition/2026-10-16-0000"'), 2)
        self.assertEqual(page.count('href="/edition/2026-10-16-1200"'), 2)
        self.assertEqual(page.count('← 更早一期'), 4)  # Visible label plus accessible name.
        self.assertIn('2026-10-16 00:00</time>', page)
        self.assertIn('2026-10-16 12:00</time>', page)
        self.assertLess(page.index('pager-top'), page.index('<article'))
        self.assertGreater(page.rindex('<nav class="pager'), page.index('</article>'))

    def test_latest_and_oldest_boundaries(self):
        latest = app.home()
        self.assertEqual(latest.count('href="/edition/2026-10-16-0600"'), 2)
        self.assertEqual(latest.count('aria-disabled="true">已是最新一期'), 2)
        oldest = app.edition(self.editions[-1]["slug"])
        self.assertEqual(oldest.count('aria-disabled="true">已是最早一期'), 2)

    def test_titles_are_decoded_once_for_display_and_ampersand_becomes_space(self):
        self.editions[1]["title"] = self.editions[1]["title"].replace("&amp;", "&amp;amp;")
        for markup in (app.home(), app.edition(self.editions[1]["slug"]), app.archive()):
            text = "".join(PageText(markup).parts)
            self.assertIn("住房 IT 优先", text)
            self.assertNotIn("&amp;", text)
            self.assertNotIn("住房 & IT", text)

    def test_card_time_is_short_and_uses_dublin_timezone(self):
        markup = app.cards(self.editions[0]["items"])
        self.assertIn('>10-16 08:43</time>', markup)
        self.assertIn('title="2026-10-16 08:43（都柏林时间）"', markup)
        self.assertIn('datetime="2026-10-16T08:43:39+01:00"', markup)
        self.assertIn('href="https://example.com/news?a=1&amp;b=2"', markup)
        self.assertIn('标题 &amp; 后续', markup)
        row = copy.deepcopy(self.editions[0]["items"][0])
        row["published_at"] = None
        self.assertNotIn('<time', app.cards([row]))

    def test_decoded_content_is_still_escaped(self):
        item = copy.deepcopy(self.editions[0]["items"][0])
        item["title"] = '&lt;img src=x onerror="alert(1)"&gt;'
        markup = app.cards([item])
        self.assertNotIn('<img', markup)
        self.assertIn(html.escape('<img src=x onerror="alert(1)">'), markup)

    def test_category_filter_does_not_auto_complete_whole_edition(self):
        self.assertIn('data-reading-end="2026-10-16-1200"', app.home())
        filtered = app.home(category="other")
        self.assertNotIn('data-reading-end=', filtered)
        self.assertEqual(filtered.count('data-auto-read="false"'), 2)
        self.assertIn('可手动标记本期阅读状态', filtered)


if __name__ == "__main__":
    unittest.main()
