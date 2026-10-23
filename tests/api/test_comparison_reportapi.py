"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestComparisonReportAPI(ComparisonDatabaseFixture, unittest.TestCase):
    """真实取证、提示词、解析和接口串联，仅替换DeepSeek网络请求。"""

    def test_success_reads_real_evidence_preserves_sources_and_does_not_save(self):
        response, generate = self._request("comparison-report", 200)
        generate.assert_called_once()
        data = response.json()
        self.assertEqual(data["task_id"], self.task_id)
        self.assertEqual(data["question"], self.analysis.question)
        self.assertEqual(data["paper_ids"], self.paper_ids)
        self.assertEqual(data["method_comparisons"][0]["source_numbers"], [1, 3])
        self.assertEqual(data["evidence_notes"][0]["source_numbers"], [5])
        self.assertEqual([source["source_number"] for source in data["sources"]], [1, 3, 5])
        for number, source in zip((1, 3, 5), data["sources"]):
            expected = self.analysis.sources[number - 1].model_dump()
            expected["source_number"] = number
            self.assertEqual(source, expected)
        prompt = generate.call_args.kwargs["user_prompt"]
        self.assertIn(self.analysis.question, prompt)
        for position in (0, 2, 4):
            self.assertIn(self.raw_texts[position], prompt)
        for position in (1, 3):
            self.assertNotIn(self.raw_texts[position], prompt)
        self.assertNotIn("模型整理的概述", prompt)
        self.assertEqual(prompt.count("[资料1]"), 1)
        rules = generate.call_args.kwargs["system_prompt"]
        self.assertIn("条件不同，不能直接判断某方法更好", rules)
        self.assertIn("保留原编号", rules)

    def test_model_error_returns_502_without_changing_analysis_or_history(self):
        before = self._snapshot()
        with patch('research_library.agents.comparison_agent.generate_text', side_effect=RuntimeError("模拟模型连接失败")):
            response = client.post(f"/research-tasks/{self.task_id}/comparison-report")
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"], "模拟模型连接失败")
        self.assertEqual(self._snapshot(), before)

    def test_invalid_model_reports_return_502_without_saving(self):
        bad_number = json.loads(json.dumps(self.answer))
        bad_number["method_comparisons"][0]["text"] = "未提供的引用。[资料1][资料2]"
        same_paper = json.loads(json.dumps(self.answer))
        same_paper["method_comparisons"][0]["text"] = "只有一篇论文。[资料1][资料5]"
        for answer in ("not JSON", json.dumps(bad_number), json.dumps(same_paper)):
            with self.subTest(answer=answer):
                _, generate = self._request("comparison-report", 502, answer=answer)
                self.assertEqual(generate.call_count, 2)

    def test_empty_model_report_returns_502(self):
        answer = json.dumps({name: [] for name in self.answer})
        self._request("comparison-report", 502, answer=answer)

    def test_notes_only_report_is_returned_as_insufficient_comparison_evidence(self):
        data = {name: [] for name in self.answer}
        data["evidence_notes"] = [dict(text="当前资料不足以比较在线实验。[资料1][资料3]")]
        response, generate = self._request("comparison-report", 200, answer=json.dumps(data))
        generate.assert_called_once()
        self.assertEqual(response.json()["method_comparisons"], [])
        self.assertEqual(response.json()["evidence_notes"][0]["source_numbers"], [1, 3])

    def test_each_request_generates_a_new_preview_without_modifying_saved_analysis(self):
        second = json.loads(json.dumps(self.answer))
        second["method_comparisons"][0]["text"] = "第二次生成的机制对比。[资料1][资料3]"
        before = self._snapshot()
        with patch('research_library.agents.comparison_agent.generate_text', side_effect=[json.dumps(self.answer), json.dumps(second)]) as generate:
            first = client.post(f"/research-tasks/{self.task_id}/comparison-report")
            another = client.post(f"/research-tasks/{self.task_id}/comparison-report")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(another.status_code, 200, another.text)
        self.assertEqual(generate.call_count, 2)
        self.assertNotEqual(first.json()["method_comparisons"], another.json()["method_comparisons"])
        self.assertEqual(self._snapshot(), before)
