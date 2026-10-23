"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestReferenceDetection(unittest.TestCase):
    """确保文内引用不会触发参考文献降权。"""

    def test_body_with_citations_keeps_its_rerank_score(self):
        body = "Our method reduces serving cost. We compare [1], [2], and [3]."
        self.assertFalse(semantic_search.looks_like_reference_chunk(body))
        self.assertAlmostEqual(
            semantic_search.calculate_rerank_score("serving cost", body, 0.8),
            0.8 * semantic_search.SEMANTIC_WEIGHT + semantic_search.KEYWORD_WEIGHT,
        )

    def test_body_mentions_do_not_mark_whole_page_as_references(self):
        body = "Our method references prior work by Smith et al. and Lee et al."
        self.assertFalse(semantic_search.looks_like_reference_chunk(body))
        self.assertEqual(semantic_search.find_reference_pages([
            (SimpleNamespace(text=body, page_number=2), SimpleNamespace(id=3), None),
        ]), set())

    def test_reference_headings_and_numbered_entries_are_detected(self):
        for text in (
            "REFERENCES\n[1] First paper",
            "7 References\n[1] First paper",
            "参考文献\n[1] 第一篇论文",
            "[1] Smith. First paper. 2022.\n[2] Lee. Second paper. 2023.\n[3] Li. Third paper. 2024.",
        ):
            with self.subTest(text=text):
                self.assertTrue(semantic_search.looks_like_reference_chunk(text))
