"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestQueryRewriter(unittest.TestCase):
    def test_chinese_question_is_rewritten(self):
        with patch(
            'research_library.retrieval.query_rewriter.generate_text',
            return_value=(
                "How does semantic caching reduce "
                "LLM serving costs?"
            ),
        ) as mock_generate:
            result = (
                query_rewriter
                .rewrite_query_for_search(
                    "语义缓存如何降低大模型服务成本？"
                )
            )

        self.assertIn(
            "语义缓存如何降低大模型服务成本？",
            result,
        )

        self.assertIn(
            "How does semantic caching reduce "
            "LLM serving costs?",
            result,
        )

        mock_generate.assert_called_once()

    def test_english_question_is_not_rewritten(self):
        question = (
            "How does semantic caching reduce "
            "LLM serving costs?"
        )

        with patch(
            'research_library.retrieval.query_rewriter.generate_text',
        ) as mock_generate:
            result = (
                query_rewriter
                .rewrite_query_for_search(
                    question
                )
            )

        self.assertEqual(
            result,
            question,
        )

        mock_generate.assert_not_called()
