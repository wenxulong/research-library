"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestComparisonReviewAPI(ComparisonDatabaseFixture, unittest.TestCase):
    """真实数据库和引用映射，只模拟审校模型网络请求。"""

    def _report_payload(self):
        with patch('research_library.agents.comparison_agent.generate_text', return_value=json.dumps(self.answer)):
            response = client.post(f"/research-tasks/{self.task_id}/comparison-report")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _review(self, payload, model=None, task_id=None):
        # 两条陈述分别引用 1/3 和 5，编号有空缺，不可按报告列表位置取证。
        responses = [
            json.dumps(dict(verdict="supported", reason="原文分别支持两种匹配机制。", source_numbers=[1, 3])),
            json.dumps(dict(verdict="supported", reason="原文说明使用离线请求轨迹。", source_numbers=[5])),
        ]
        before = self._snapshot()
        with patch('research_library.agents.review_agent.generate_text', side_effect=responses if model is None else model) as generate:
            response = client.post(
                f"/research-tasks/{self.task_id if task_id is None else task_id}/comparison-report/review",
                json=payload,
            )
        self.assertEqual(self._snapshot(), before)
        return response, generate

    def test_review_uses_actual_raw_text_and_original_sparse_numbers(self):
        response, generate = self._review(self._report_payload())
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertEqual([item["field"] for item in items], ["method_comparisons", "evidence_notes"])
        self.assertEqual([item["source_numbers"] for item in items], [[1, 3], [5]])
        self.assertEqual(generate.call_count, 2)
        first_prompt = generate.call_args_list[0].kwargs["user_prompt"]
        second_prompt = generate.call_args_list[1].kwargs["user_prompt"]
        self.assertIn(self.raw_texts[0], first_prompt)
        self.assertIn(self.raw_texts[2], first_prompt)
        self.assertIn(f"论文编号：{self.paper_ids[0]}", first_prompt)
        self.assertIn(f"论文编号：{self.paper_ids[1]}", first_prompt)
        self.assertNotIn(self.raw_texts[1], first_prompt)
        self.assertNotIn("模型整理的概述", first_prompt)
        self.assertIn(self.raw_texts[4], second_prompt)
        self.assertIn("[资料5]", second_prompt)
        self.assertNotIn(self.raw_texts[0], second_prompt)
        rules = generate.call_args_list[0].kwargs["system_prompt"]
        self.assertIn("数字、单位、参数名称", rules)
        self.assertIn("缓存容量和候选召回数Top-K是不同参数", rules)
        self.assertIn("不能用其他片段中的数字补全", rules)

    def test_source_numbers_are_derived_from_text_instead_of_trusting_request(self):
        payload = self._report_payload()
        payload["method_comparisons"][0]["source_numbers"] = [999]
        response, generate = self._review(payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["source_numbers"], [1, 3])
        self.assertNotIn("资料999", generate.call_args_list[0].kwargs["user_prompt"])

    def test_mismatched_identity_and_source_metadata_fail_before_model(self):
        base = self._report_payload()
        variants = []
        for key, value in (("task_id", 999), ("question", "其他问题"), ("paper_ids", [999])):
            payload = json.loads(json.dumps(base)); payload[key] = value; variants.append(payload)
        for key in ("chunk_id", "paper_id", "page_number", "source_number"):
            payload = json.loads(json.dumps(base)); payload["sources"][0][key] = 999; variants.append(payload)
        missing = json.loads(json.dumps(base)); missing["sources"].pop(); variants.append(missing)
        duplicate = json.loads(json.dumps(base)); duplicate["sources"].append(duplicate["sources"][0]); variants.append(duplicate)
        for payload in variants:
            with self.subTest(payload=payload):
                response, generate = self._review(payload)
                self.assertEqual(response.status_code, 409, response.text)
                generate.assert_not_called()

    def test_invalid_report_citations_and_empty_report_fail_before_model(self):
        base = self._report_payload()
        for text in ("对比。[资料2][资料3]", "对比。[资料1]", "对比。[资料99]", "对比。[资料", "没有引用"):
            payload = json.loads(json.dumps(base)); payload["method_comparisons"][0]["text"] = text
            response, generate = self._review(payload)
            self.assertEqual(response.status_code, 409, response.text)
            generate.assert_not_called()
        payload = json.loads(json.dumps(base))
        payload["method_comparisons"] = []; payload["evidence_notes"] = []
        response, generate = self._review(payload)
        self.assertEqual(response.status_code, 409, response.text)
        generate.assert_not_called()

    def test_deleted_chunk_fails_before_model(self):
        payload = self._report_payload()
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id)); db.commit()
        response, generate = self._review(payload)
        self.assertEqual(response.status_code, 409, response.text)
        generate.assert_not_called()

    def test_missing_task_or_analysis_returns_404_without_model(self):
        payload = self._report_payload()
        response, generate = self._review(payload, task_id=999)
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).result = None; db.commit()
        response, generate = self._review(payload)
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()

    def test_model_failures_and_invalid_review_return_502(self):
        payload = self._report_payload()
        responses = [
            RuntimeError("模型服务不可用"),
            ["不是JSON", "仍不是JSON"],
            [json.dumps(dict(verdict="supported", reason="支持", source_numbers=[999]))] * 2,
        ]
        for model in responses:
            with self.subTest(model=model):
                response, _ = self._review(payload, model=model)
                self.assertEqual(response.status_code, 502, response.text)

    def test_partially_supported_verdict_and_reason_are_returned_without_rewriting(self):
        payload = self._report_payload()
        payload["method_comparisons"][0]["text"] = "A通过语义匹配且Top-K为500，B通过完全匹配。[资料1][资料3]"
        reason = "匹配机制有依据，但引用原文没有Top-K的数值。"
        model = [
            json.dumps(dict(verdict="partially_supported", reason=reason, source_numbers=[1, 3])),
            json.dumps(dict(verdict="supported", reason="离线轨迹有依据", source_numbers=[5])),
        ]
        response, _ = self._review(payload, model=model)
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["verdict"], "partially_supported")
        self.assertEqual(item["reason"], reason)
        self.assertEqual(item["claim"], payload["method_comparisons"][0]["text"])
