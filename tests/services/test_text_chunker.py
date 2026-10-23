"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestTextChunker(unittest.TestCase):
    """测试逐页分块、重叠和参数校验。"""

    def test_chunk_pages_with_overlap(self):
        pages = [
            ExtractedPage(page_number=1, text="ABCDEFGHIJ"),
            ExtractedPage(page_number=2, text="   "),
            ExtractedPage(page_number=3, text="XYZ"),
        ]

        chunks = chunk_pages(pages, chunk_size=6, overlap=2)

        result = [
            (chunk.page_number, chunk.chunk_index, chunk.text)
            for chunk in chunks
        ]

        self.assertEqual(
            result,
            [
                (1, 1, "ABCDEF"),
                (1, 2, "EFGHIJ"),
                (3, 1, "XYZ"),
            ],
        )

    def test_reject_invalid_chunk_settings(self):
        pages = [ExtractedPage(page_number=1, text="ABCDE")]

        with self.assertRaises(ValueError):
            chunk_pages(pages, chunk_size=0)

        with self.assertRaises(ValueError):
            chunk_pages(pages, chunk_size=5, overlap=5)
