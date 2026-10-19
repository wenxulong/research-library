"""候选问题模块的回归测试：完整读取原文，接口返回正确结果。"""
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from api import app
from database import get_db
import research_question_agent
from schemas import (
    CheckedComparisonReportResponse,
    ComparisonReportResponse,
    ResearchTaskReviewResponse,
    ReviewItemResponse,
)


class TestResearchQuestions(unittest.TestCase):
    def setUp(self):
        self.report = ComparisonReportResponse(
            task_id=21, question="两种方法如何处理变化？", paper_ids=[16, 17],
            method_comparisons=[], experiment_comparisons=[],
            conclusion_comparisons=[], evidence_notes=[],
            sources=[dict(
                source_number=number, paper_id=paper_id, paper_title=f"论文{paper_id}",
                chunk_id=300 + number, page_number=number, score=0.9,
            ) for number, paper_id in ((1, 16), (3, 17))],
        )
        chunks = {
            source.chunk_id: SimpleNamespace(
                id=source.chunk_id, page_number=source.page_number,
                text=f"资料{source.source_number}的真实测试原文",
                document=SimpleNamespace(paper_id=source.paper_id),
            ) for source in self.report.sources
        }
        self.db = Mock()
        self.db.get.side_effect = lambda model, chunk_id: chunks.get(chunk_id)

    def test_all_evidence_is_loaded_and_original_numbers_are_preserved(self):
        evidence = research_question_agent.load_report_evidence(self.db, self.report)
        self.assertEqual(set(evidence), {1, 3})
        self.assertEqual(evidence[3].text, "资料3的真实测试原文")
        self.assertEqual(self.db.get.call_count, 2)

    def test_http_endpoint_generates_and_reviews_candidate_using_later_source(self):
        checked = CheckedComparisonReportResponse(
            task_id=21, report=self.report, passed=True, correction_rounds=0,
            initial_review=ResearchTaskReviewResponse(task_id=21, items=[]),
            review=ResearchTaskReviewResponse(task_id=21, items=[]),
        )
        job = SimpleNamespace(id=17, status="succeeded", result=checked.model_dump(mode="json"))
        candidate = dict(
            observation="当前资料只展示了固定分布实验。",
            research_question="分布变化时方法的表现如何？",
            research_value="明确方法的适用条件。", source_numbers=[3],
            verification_plan="构造分布变化的请求序列。", uncertainties=["需要补充文献检索"],
        )
        review = ReviewItemResponse(
            field="observation", claim=candidate["observation"], verdict="supported",
            reason="测试原文支持该观察。", source_numbers=[3],
        )

        def test_db():
            yield self.db

        previous = app.dependency_overrides.get(get_db)
        app.dependency_overrides[get_db] = test_db
        try:
            def saved_result(db, result):
                return SimpleNamespace(
                    **result.model_dump(mode="json"), id=7,
                    selected_candidate_number=None, selected_question=None,
                    created_at=datetime(2026, 10, 16),
                )

            with patch("api.report_job_crud.get_report_job", return_value=job), \
                 patch("api.research_question_crud.save_research_questions", side_effect=saved_result) as save, \
                 patch("research_question_agent.generate_text", return_value=json.dumps({"candidates": [candidate]})) as generate, \
                 patch("research_question_agent.review_agent.review_claim", return_value=review) as reviewer:
                response = TestClient(app).post("/report-jobs/17/research-questions")
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual(data["report_job_id"], 17)
                self.assertEqual(data["task_id"], 21)
                self.assertEqual(data["candidates"][0]["source_numbers"], [3])
                self.assertEqual(data["candidates"][0]["evidence_review"]["verdict"], "supported")
                self.assertEqual([s["source_number"] for s in data["sources"]], [1, 3])
                generate.assert_called_once()
                save.assert_called_once()
                reviewer.assert_called_once()
                self.assertEqual(reviewer.call_args.args[0].evidence[0].source_number, 3)
                self.assertIn("资料3的真实测试原文", generate.call_args.kwargs["user_prompt"])
        finally:
            if previous is None:
                app.dependency_overrides.pop(get_db, None)
            else:
                app.dependency_overrides[get_db] = previous


if __name__ == "__main__":
    unittest.main()
