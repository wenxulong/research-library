"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestCheckedComparisonWorkflow(ComparisonDatabaseFixture, unittest.TestCase):
    def _payload(self):
        with patch('research_library.agents.comparison_agent.generate_text', return_value=json.dumps(self.answer)):
            response = client.post(f"/research-tasks/{self.task_id}/comparison-report")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _reviews(self, first_verdict="supported"):
        return [
            json.dumps(dict(verdict=first_verdict, reason="检查方法原文", source_numbers=[1, 3])),
            json.dumps(dict(verdict="supported", reason="离线轨迹原文支持", source_numbers=[5])),
        ]

    def _post(self, payload, reviews, corrections=None):
        before = self._snapshot()
        with (
            patch('research_library.agents.review_agent.generate_text', side_effect=reviews) as reviewer,
            patch('research_library.services.comparison_workflow.generate_text', side_effect=corrections or []) as corrector,
            patch('research_library.agents.comparison_agent.generate_text', return_value=json.dumps(self.answer)) as generator,
        ):
            response = client.post(f"/research-tasks/{self.task_id}/comparison-report/checked", json=payload)
        self.assertEqual(self._snapshot(), before)
        return response, reviewer, corrector, generator

    def test_supported_report_passes_without_correction(self):
        response, reviewer, corrector, generator = self._post(self._payload(), self._reviews())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["passed"])
        self.assertEqual(response.json()["correction_rounds"], 0)
        self.assertEqual(reviewer.call_count, 2)
        corrector.assert_not_called(); generator.assert_not_called()

    def test_no_report_generates_then_reviews(self):
        response, _, corrector, generator = self._post(None, self._reviews())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["passed"])
        generator.assert_called_once(); corrector.assert_not_called()

    def test_bad_claim_is_corrected_and_rechecked_before_passing(self):
        payload = self._payload()
        payload["method_comparisons"][0]["text"] = "A使用语义匹配且Top-K为500，B使用完全匹配。[资料1][资料3]"
        responses = self._reviews("partially_supported") + self._reviews()
        response, reviewer, corrector, _ = self._post(payload, responses, [json.dumps(self.answer)])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["passed"])
        self.assertEqual(result["correction_rounds"], 1)
        self.assertEqual(result["initial_review"]["items"][0]["verdict"], "partially_supported")
        self.assertNotIn("Top-K为500", result["report"]["method_comparisons"][0]["text"])
        self.assertEqual(reviewer.call_count, 4)
        prompt = corrector.call_args.kwargs["user_prompt"]
        self.assertIn(self.raw_texts[0], prompt)
        self.assertIn(self.raw_texts[2], prompt)
        self.assertIn("partially_supported", prompt)

    def test_repeated_unsupported_results_are_not_marked_passed(self):
        response, reviewer, corrector, _ = self._post(
            self._payload(), self._reviews("partially_supported") * 3,
            [json.dumps(self.answer)] * 2,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["passed"])
        self.assertEqual(response.json()["correction_rounds"], 2)
        self.assertEqual(corrector.call_count, 2)
        self.assertEqual(reviewer.call_count, 6)

    def test_invalid_correction_json_is_retried_and_rechecked(self):
        response, _, corrector, _ = self._post(
            self._payload(), self._reviews("partially_supported") + self._reviews(),
            ["bad JSON", json.dumps(self.answer)],
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["passed"])
        self.assertEqual(corrector.call_count, 2)

    def test_invalid_correction_is_rejected_without_rechecking_or_saving(self):
        data = json.loads(json.dumps(self.answer))
        data["method_comparisons"][0]["text"] = "捏造引用。[资料99]"
        response, reviewer, corrector, _ = self._post(
            self._payload(), self._reviews("partially_supported"), [json.dumps(data)] * 2,
        )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(corrector.call_count, 2)
        self.assertEqual(reviewer.call_count, 2)

    def test_correction_cannot_expand_empty_comparison_dimensions(self):
        data = json.loads(json.dumps(self.answer))
        data["experiment_comparisons"] = [dict(text="新增加的实验比较。[资料1][资料3]")]
        response, _, corrector, _ = self._post(
            self._payload(), self._reviews("partially_supported"), [json.dumps(data)] * 2,
        )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("不得新增", response.json()["detail"])
        self.assertEqual(corrector.call_count, 2)

    def test_correction_cannot_remove_already_supported_note(self):
        data = json.loads(json.dumps(self.answer)); data["evidence_notes"] = []
        response, _, _, _ = self._post(
            self._payload(), self._reviews("partially_supported"), [json.dumps(data)] * 2,
        )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("不得改写或删除已通过", response.json()["detail"])

    def test_stale_sources_fail_before_model_calls(self):
        payload = self._payload(); payload["sources"][0]["chunk_id"] = 999
        response, reviewer, corrector, generator = self._post(payload, [])
        self.assertEqual(response.status_code, 409, response.text)
        reviewer.assert_not_called(); corrector.assert_not_called(); generator.assert_not_called()

    def test_generation_bad_json_retries_then_reviews(self):
        before = self._snapshot()
        with (
            patch('research_library.agents.comparison_agent.generate_text', side_effect=["invalid JSON", json.dumps(self.answer)]) as generate,
            patch('research_library.agents.review_agent.generate_text', side_effect=self._reviews()),
        ):
            response = client.post(f"/research-tasks/{self.task_id}/comparison-report/checked")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["passed"])
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(self._snapshot(), before)
