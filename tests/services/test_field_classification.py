"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestFieldClassification(unittest.TestCase):
    """检查分类模型输出规则与提示词，不访问真实模型。"""

    def setUp(self):
        from research_library.schemas import AnalysisRevisionResponse
        self.revision = AnalysisRevisionResponse(field="limitations", original_claim="原陈述",
            revised_claim="算法切换次数最少，运行时间最快。[资料9]", reason="修改理由", source_numbers=[9])

    def test_accept_valid_classification_verdicts(self):
        for verdict, suggested in (("appropriate", None), ("mixed", None), ("uncertain", None), ("misplaced", "experiments")):
            with self.subTest(verdict=verdict):
                answer = json.dumps(dict(verdict=verdict, reason="分类理由", suggested_field=suggested))
                with patch('research_library.agents.revision_agent.generate_text', return_value="```json\n" + answer + "\n```"):
                    result = revision_agent.check_revision_field(self.revision)
                self.assertEqual(result.verdict, verdict)
                self.assertEqual(result.suggested_field, suggested)

    def test_reject_invalid_classification_payloads(self):
        cases = ["not JSON", "[]", "{}", json.dumps(dict(verdict="wrong", reason="原因")),
            json.dumps(dict(verdict="appropriate", reason=123)),
            json.dumps(dict(verdict="appropriate", reason=" ")),
            json.dumps(dict(verdict="misplaced", reason="原因", suggested_field="unknown"))]
        for answer in cases:
            with self.subTest(answer=answer), patch('research_library.agents.revision_agent.generate_text', return_value=answer):
                with self.assertRaises(RuntimeError):
                    revision_agent.check_revision_field(self.revision)

    def test_reject_contradictory_suggested_field_rules(self):
        cases = [("misplaced", None), ("misplaced", "limitations"),
                 ("appropriate", "experiments"), ("mixed", "methods"), ("uncertain", "summary")]
        for verdict, suggested in cases:
            answer = json.dumps(dict(verdict=verdict, reason="分类理由", suggested_field=suggested))
            with self.subTest(verdict=verdict, suggested=suggested), patch('research_library.agents.revision_agent.generate_text', return_value=answer):
                with self.assertRaises(RuntimeError):
                    revision_agent.check_revision_field(self.revision)

    def test_invalid_input_is_rejected_before_model_call(self):
        with patch('research_library.agents.revision_agent.generate_text') as generate:
            for update in ({"field": "unknown"}, {"revised_claim": " "}):
                with self.subTest(update=update):
                    with self.assertRaises(ValueError):
                        revision_agent.check_revision_field(self.revision.model_copy(update=update))
        generate.assert_not_called()

    def test_prompt_checks_revised_text_and_field_without_changing_revision(self):
        before = self.revision.model_dump()
        answer = json.dumps(dict(verdict="misplaced", reason="实验结果", suggested_field="experiments"))
        with patch('research_library.agents.revision_agent.generate_text', return_value=answer) as generate:
            revision_agent.check_revision_field(self.revision)
        generate.assert_called_once()
        arguments = generate.call_args.kwargs
        self.assertIn(self.revision.revised_claim, arguments["user_prompt"])
        self.assertIn("当前字段：limitations", arguments["user_prompt"])
        self.assertIn("不判断论文事实是否正确", arguments["system_prompt"])
        self.assertEqual(self.revision.model_dump(), before)
