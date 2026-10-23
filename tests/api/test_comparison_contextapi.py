"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestComparisonContextAPI(ComparisonDatabaseFixture, unittest.TestCase):
    """原文分组不调用模型，按论文去重，并保留不连续的原资料编号。"""

    def test_grouping_deduplicates_raw_evidence_and_preserves_original_numbers(self):
        response, generate = self._request("comparison-context", 200)
        generate.assert_not_called()
        data = response.json()
        self.assertEqual(data["task_id"], self.task_id)
        self.assertEqual(data["question"], self.analysis.question)
        self.assertEqual(data["paper_ids"], self.paper_ids)
        self.assertEqual(data["source_numbers"], [1, 3, 5])
        context = data["context"]
        for number, position in ((1, 0), (3, 2), (5, 4)):
            self.assertEqual(context.count(f"[资料{number}]"), 1)
            self.assertIn(self.raw_texts[position], context)
        self.assertNotIn("[资料2]", context)
        self.assertNotIn("[资料4]", context)
        self.assertNotIn(self.raw_texts[1], context)
        self.assertNotIn(self.raw_texts[3], context)
        self.assertNotIn("模型整理的概述", context)
        first_section, second_section = context.split(f"=== 论文 {self.paper_ids[1]} ===")
        self.assertIn("[资料1]", first_section)
        self.assertIn("[资料5]", first_section)
        self.assertNotIn("[资料3]", first_section)
        self.assertIn("[资料3]", second_section)
        # 分组展示顺序可以是1、5、3，但原编号不改变。
        self.assertLess(context.index("[资料5]"), context.index("[资料3]"))

    def test_two_linked_papers_but_only_one_cited_paper_cannot_generate_report(self):
        analysis = self.analysis.model_dump()
        analysis["conclusions"] = []
        self._save_analysis(analysis)
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint):
                _, generate = self._request(endpoint, 409)
                generate.assert_not_called()

    def test_sources_without_citations_are_not_treated_as_comparison_evidence(self):
        analysis = self.analysis.model_dump()
        analysis.update(summary="没有引用的概述", methods=[], experiments=[], conclusions=[])
        self._save_analysis(analysis)
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint):
                _, generate = self._request(endpoint, 409)
                generate.assert_not_called()

    def test_deleted_chunk_prevents_model_call(self):
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id))
            db.commit()
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint):
                _, generate = self._request(endpoint, 409)
                generate.assert_not_called()

    def test_wrong_source_paper_is_rejected_before_model_call(self):
        analysis = self.analysis.model_dump()
        analysis["sources"][0]["paper_id"] = self.paper_ids[1]
        self._save_analysis(analysis)
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint):
                _, generate = self._request(endpoint, 409)
                generate.assert_not_called()

    def test_missing_task_or_saved_analysis_returns_404_without_model_call(self):
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint, missing="task"):
                _, generate = self._request(endpoint, 404, task_id=999999)
                generate.assert_not_called()
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).result = None
            db.commit()
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint, missing="analysis"):
                _, generate = self._request(endpoint, 404)
                generate.assert_not_called()

    def test_invalid_saved_analysis_is_rejected_before_model_call(self):
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).result = "这不是正确的JSON"
            db.commit()
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint):
                _, generate = self._request(endpoint, 409)
                generate.assert_not_called()
