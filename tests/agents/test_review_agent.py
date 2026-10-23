"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestReviewAgent(unittest.TestCase):
    """检查审校解析、真实证据提示词和逐条处理逻辑；模拟模型请求。"""

    def setUp(self):
        self.claim = ReviewClaim(
            field="conclusions", claim="所有数据集上成本都下降。[资料3]",
            source_numbers=[3], evidence=[ReviewEvidence(
                source_number=3, paper_title="测试论文", chunk_id=14,
                page_number=5, text="在数据集A上，服务成本降低了20%。",
            )],
        )
        self.data = dict(verdict="partially_supported", reason="只支持数据集A。", source_numbers=[3])

    def test_parse_valid_code_block_preserves_original_claim(self):
        data = dict(self.data, field="methods", claim="模型试图改写的陈述")
        result = review_agent.parse_review_response(
            "```json\n" + json.dumps(data) + "\n```", self.claim,
        )
        self.assertEqual(result.field, self.claim.field)
        self.assertEqual(result.claim, self.claim.claim)
        self.assertEqual(result.verdict, "partially_supported")
        self.assertEqual(result.source_numbers, [3])

    def test_reject_invalid_model_payloads(self):
        cases = [
            ("not json", "正确的JSON"),
            ("[]", "不是JSON对象"),
            (json.dumps(dict(self.data, verdict="maybe")), "字段或类型"),
            (json.dumps(dict(self.data, source_numbers=["3"])), "字段或类型"),
            (json.dumps(dict(self.data, source_numbers=[True])), "字段或类型"),
            (json.dumps(dict(self.data, reason="  ")), "审校理由"),
            (json.dumps(dict(self.data, source_numbers=[])), "没有提供资料编号"),
            (json.dumps({"verdict": "supported"}), "字段或类型"),
        ]
        for text, message in cases:
            with self.subTest(text=text):
                with self.assertRaisesRegex(RuntimeError, message):
                    review_agent.parse_review_response(text, self.claim)

    def test_reject_source_not_provided(self):
        with self.assertRaisesRegex(RuntimeError, "未提供的资料"):
            review_agent.parse_review_response(
                json.dumps(dict(self.data, source_numbers=[1])), self.claim,
            )

    def test_no_evidence_does_not_call_model(self):
        claim = ReviewClaim("limitations", "当前资料不足。", [], [])
        with patch('research_library.agents.review_agent.generate_text') as generate:
            result = review_agent.review_claim(claim)
        self.assertEqual(result.verdict, "insufficient_evidence")
        self.assertEqual(result.source_numbers, [])
        generate.assert_not_called()

    def test_model_receives_original_evidence_and_number(self):
        with patch('research_library.agents.review_agent.generate_text', return_value=json.dumps(self.data)) as generate:
            result = review_agent.review_claim(self.claim)
        generate.assert_called_once()
        prompt = generate.call_args.kwargs["user_prompt"]
        self.assertIn(self.claim.claim, prompt)
        self.assertIn("[资料3]", prompt)
        self.assertIn("页码：5", prompt)
        self.assertIn(self.claim.evidence[0].text, prompt)
        self.assertEqual(result.verdict, "partially_supported")

    def test_task_reviews_all_claims_in_order(self):
        second = ReviewClaim("limitations", "当前资料不足。", [], [])
        analysis = SimpleNamespace(task_id=7)
        db = Mock()
        with (
            patch('research_library.agents.review_agent.prepare_review_claims', return_value=[self.claim, second]) as prepare,
            patch('research_library.agents.review_agent.generate_text', return_value=json.dumps(self.data)) as generate,
        ):
            result = review_agent.review_research_task(db, analysis)
        prepare.assert_called_once_with(db=db, analysis=analysis)
        generate.assert_called_once()
        self.assertEqual(result.task_id, 7)
        self.assertEqual([i.field for i in result.items], ["conclusions", "limitations"])
        self.assertEqual(result.items[1].verdict, "insufficient_evidence")

    def test_model_failure_is_propagated(self):
        with patch('research_library.agents.review_agent.generate_text', side_effect=RuntimeError("模型连接失败")):
            with self.assertRaisesRegex(RuntimeError, "模型连接失败"):
                review_agent.review_claim(self.claim)
