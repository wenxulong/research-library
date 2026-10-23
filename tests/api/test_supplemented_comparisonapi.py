"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestSupplementedComparisonAPI(ComparisonDatabaseFixture, unittest.TestCase):
    """HTTP入口测试：使用隔离数据库，模型输出模拟，真实执行内部流程。"""

    def setUp(self):
        super().setUp()
        self.url = f"/research-tasks/{self.task_id}/comparison-report/supplemented"

    def _review_json(self, verdict="supported"):
        return json.dumps(dict(verdict=verdict, reason="测试原文审校", source_numbers=[1, 3, 5]))

    def test_no_body_generates_report_and_returns_serializable_result(self):
        before = self._snapshot()
        # 每条回复只能引用当次提供的证据。
        responses = [dict(verdict="supported", reason="有原文支持", source_numbers=[1, 3]),
                     dict(verdict="supported", reason="有原文支持", source_numbers=[5])]
        with patch('research_library.agents.comparison_agent.generate_text', return_value=json.dumps(self.answer)) as generate, \
             patch('research_library.agents.review_agent.generate_text', side_effect=[json.dumps(r) for r in responses]), \
             patch('research_library.agents.revision_agent.generate_text', side_effect=AssertionError("通过的陈述不应修正")):
            response = client.post(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["passed"])
        self.assertEqual(body["correction_rounds"], 0)
        self.assertEqual(len(body["review"]["items"]), 2)
        self.assertEqual(len(body["report"]["sources"]), 5)
        generate.assert_called_once()
        self.assertEqual(self._snapshot(), before)

    def test_submitted_report_skips_generation_and_false_verdict_survives_http(self):
        import research_library.agents.comparison_agent as comparison_agent
        from research_library.schemas import ComparisonSourceResponse
        sources = [ComparisonSourceResponse(source_number=n, **self.analysis.sources[n-1].model_dump())
                   for n in (1, 3, 5)]
        report = comparison_agent.parse_comparison_response(json.dumps(self.answer), self.task_id,
                                                            self.analysis.question, sources)
        from research_library.services.comparison_report_builder import assemble_comparison_report
        from research_library.services.comparison_evidence_workflow import ComparisonEvidenceResult, ComparisonClaimResult
        from research_library.services.evidence_review_workflow import EvidenceReviewResult
        from research_library.services.evidence_supplement import build_evidence_registry
        from research_library.services.comparison_review import prepare_comparison_claims
        with TestingSessionLocal() as db:
            claims = prepare_comparison_claims(db, self.analysis, report)
            registry = build_evidence_registry(db, self.analysis)
        items = []
        for claim in claims:
            review = ReviewItemResponse(field=claim.field, claim=claim.claim, verdict="insufficient_evidence",
                                        reason="仍未确认", source_numbers=[])
            items.append(ComparisonClaimResult(EvidenceReviewResult(claim, review, review, []), None))
        processed = ComparisonEvidenceResult(self.task_id, items, registry)
        with patch('research_library.agents.comparison_agent.generate_text', side_effect=AssertionError("不应重新生成")), \
             patch('research_library.services.comparison_evidence_workflow.process_comparison_evidence', return_value=processed):
            response = client.post(self.url, json=report.model_dump())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["passed"])

    def test_missing_task_and_bad_body_do_not_call_model(self):
        with patch('research_library.agents.comparison_agent.generate_text') as generate:
            self.assertEqual(client.post('/research-tasks/99999/comparison-report/supplemented').status_code, 404)
            self.assertEqual(client.post(self.url, json={"bad": True}).status_code, 422)
            generate.assert_not_called()

    def test_value_and_runtime_errors_become_http_errors(self):
        for error, status in ((ValueError("来源不一致"), 409), (RuntimeError("模型结果不符合规则"), 502)):
            with self.subTest(status=status), patch('research_library.agents.comparison_agent.generate_comparison_report', side_effect=error):
                response = client.post(self.url)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["detail"], str(error))
