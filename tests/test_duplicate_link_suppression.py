import os
import tempfile
import unittest

from core.storage.parse_record import ParseRecordManager


class DummyParser:
    name = "bilibili"


def _metadata(**overrides):
    data = {
        "source_url": "https://b23.tv/abc?share_source=qq",
        "url": "https://www.bilibili.com/video/BV123",
        "parser_name": "bilibili",
        "title": "demo",
        "_enable_text_metadata": True,
        "_enable_rich_media": True,
    }
    data.update(overrides)
    return data


class DuplicateLinkSuppressionTests(unittest.TestCase):
    def test_records_sent_link_and_filters_repeat_before_parse(self):
        with tempfile.TemporaryDirectory() as root:
            record_file = os.path.join(root, "records.json")
            manager = ParseRecordManager(record_file=record_file)

            self.assertEqual(
                manager.record_sent_link_metadata([_metadata()]),
                1,
            )

            reloaded = ParseRecordManager(record_file=record_file)
            links, duplicates = reloaded.filter_duplicate_links([
                (
                    "https://www.bilibili.com/video/BV123?from=share",
                    DummyParser(),
                )
            ])

            self.assertEqual(links, [])
            self.assertEqual(
                duplicates,
                [("https://www.bilibili.com/video/BV123?from=share", "bilibili")],
            )

    def test_filters_repeat_short_link_alias_after_parse(self):
        with tempfile.TemporaryDirectory() as root:
            manager = ParseRecordManager(
                record_file=os.path.join(root, "records.json")
            )
            manager.record_sent_link_metadata([_metadata()])

            filtered, duplicate_count = manager.filter_duplicate_metadata([
                _metadata(
                    source_url="https://another.short/link",
                    url="https://www.bilibili.com/video/BV123?utm_source=x",
                )
            ])

            self.assertEqual(filtered, [])
            self.assertEqual(duplicate_count, 1)

    def test_sent_link_expires_after_ttl_before_parse(self):
        with tempfile.TemporaryDirectory() as root:
            manager = ParseRecordManager(
                record_file=os.path.join(root, "records.json"),
                sent_link_ttl_seconds=10,
            )
            manager.record_sent_link_metadata([_metadata()], now=100)

            links, duplicates = manager.filter_duplicate_links(
                [("https://www.bilibili.com/video/BV123", DummyParser())],
                now=109,
            )
            self.assertEqual(links, [])
            self.assertEqual(len(duplicates), 1)

            links, duplicates = manager.filter_duplicate_links(
                [("https://www.bilibili.com/video/BV123", DummyParser())],
                now=110,
            )
            self.assertEqual(len(links), 1)
            self.assertEqual(duplicates, [])

    def test_sent_link_expires_after_ttl_for_final_alias(self):
        with tempfile.TemporaryDirectory() as root:
            manager = ParseRecordManager(
                record_file=os.path.join(root, "records.json"),
                sent_link_ttl_seconds=10,
            )
            manager.record_sent_link_metadata([_metadata()], now=100)

            filtered, duplicate_count = manager.filter_duplicate_metadata([
                _metadata(url="https://www.bilibili.com/video/BV123?from=share")
            ], now=110)

            self.assertEqual(len(filtered), 1)
            self.assertEqual(duplicate_count, 0)

    def test_filters_later_duplicate_in_same_batch(self):
        manager = ParseRecordManager()
        first = _metadata()
        second = _metadata(url="https://www.bilibili.com/video/BV123?utm_source=x")

        filtered, duplicate_count = manager.filter_duplicate_metadata([
            first,
            second,
        ])

        self.assertEqual(filtered, [first])
        self.assertEqual(duplicate_count, 1)

    def test_non_duplicate_metadata_is_kept_without_media(self):
        manager = ParseRecordManager()
        text_only = _metadata(video_urls=[], image_urls=[])

        filtered, duplicate_count = manager.filter_duplicate_metadata([
            text_only,
        ])

        self.assertEqual(filtered, [text_only])
        self.assertEqual(duplicate_count, 0)


if __name__ == "__main__":
    unittest.main()
