"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestRevisionAgent(unittest.TestCase):
    """验证修订解析与批量处理，包括全部校验后才调用模型。"""

    def setUp(self):
        self.claim = ReviewClaim("conclusions", "所有数据集上成本下降。[资料3]", [3], [
            ReviewEvidence(3, "测试论文", 14, 5, "在数据集A上成本下降20%。"),
        ])
        self.item = ReviewItemResponse(field=self.claim.field, claim=self.claim.claim,
                                      verdict="partially_supported", reason="仅支持数据集A。", source_numbers=[3])
        self.answer = json.dumps(dict(revised_claim="数据集A上成本下降20%。[资料3]", reason="缩小结论范围。"))

    def test_valid_response_preserves_original_and_extracts_citations(self):
        result = revision_agent.parse_revision_response("```json\n" + self.answer + "\n```", self.claim, 3)
        self.assertEqual(result.original_claim, self.claim.claim)
        self.assertEqual(result.field, "conclusions")
        self.assertEqual(result.source_numbers, [3])

    def test_reject_invalid_model_payloads(self):
        cases = ["not JSON", "[]", "{}", json.dumps(dict(revised_claim=123, reason="原因")),
                 json.dumps(dict(revised_claim=" ", reason="原因")),
                 json.dumps(dict(revised_claim="修改后", reason=" "))]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    revision_agent.parse_revision_response(value, self.claim, 3)

    def test_reject_malformed_out_of_range_and_unprovided_citations(self):
        for citation in ("[资料3", "[资料四]", "[资料4]", "[资料1]"):
            with self.subTest(citation=citation):
                with self.assertRaises(RuntimeError):
                    revision_agent.parse_revision_response(
                        json.dumps(dict(revised_claim="修改后" + citation, reason="原因")), self.claim, 3,
                    )

    def test_evidence_insufficient_text_can_omit_citations(self):
        result = revision_agent.parse_revision_response(json.dumps(dict(
            revised_claim="当前引用原文不足以确认原陈述。", reason="原文不足。")), self.claim, 3)
        self.assertEqual(result.source_numbers, [])

    def test_single_revision_sends_claim_review_and_evidence(self):
        with patch('research_library.agents.revision_agent.generate_text', return_value=self.answer) as generate:
            result = revision_agent.revise_claim(self.claim, self.item, 3)
        generate.assert_called_once()
        prompt = generate.call_args.kwargs["user_prompt"]
        for fragment in (self.claim.claim, self.item.reason, self.claim.evidence[0].text, "[资料3]"):
            self.assertIn(fragment, prompt)
        self.assertEqual(result.source_numbers, [3])

    def test_single_revision_rejects_ineligible_input_before_model_call(self):
        cases = [
            (self.claim, self.item.model_copy(update={"claim": "不同陈述"})),
            (self.claim, self.item.model_copy(update={"field": "methods"})),
            (self.claim, self.item.model_copy(update={"verdict": "supported"})),
            (ReviewClaim(self.claim.field, self.claim.claim, [3], []), self.item),
        ]
        with patch('research_library.agents.revision_agent.generate_text') as generate:
            for claim, item in cases:
                with self.subTest(item=item):
                    with self.assertRaises(ValueError):
                        revision_agent.revise_claim(claim, item, 3)
        generate.assert_not_called()

    def _batch_fixture(self):
        supported = ReviewClaim("summary", "概述。[资料3]", [3], self.claim.evidence)
        missing = ReviewClaim("limitations", "当前资料不足。", [], [])
        claims = [supported, self.claim, missing]
        items = [
            ReviewItemResponse(field=supported.field, claim=supported.claim, verdict="supported", reason="支持", source_numbers=[3]),
            self.item,
            ReviewItemResponse(field=missing.field, claim=missing.claim, verdict="insufficient_evidence", reason="无证据", source_numbers=[]),
        ]
        return claims, ResearchTaskReviewResponse(task_id=1, items=items)

    def test_batch_skips_supported_and_reports_missing_evidence(self):
        claims, review = self._batch_fixture()
        with (
            patch('research_library.agents.revision_agent.prepare_review_claims', return_value=claims),
            patch('research_library.agents.revision_agent.generate_text', return_value=self.answer) as generate,
        ):
            result = revision_agent.revise_research_task(Mock(), SimpleNamespace(task_id=1, sources=[None]*3), review)
        generate.assert_called_once()
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].original_claim, self.claim.claim)
        self.assertEqual(result.skipped_items, [review.items[2]])

    def test_batch_checks_every_item_before_calling_model(self):
        for change in ("claim", "source_numbers"):
            claims, review = self._batch_fixture()
            if change == "claim":
                review.items[2] = review.items[2].model_copy(update={"claim": "已过期的陈述"})
            else:
                review.items[1] = review.items[1].model_copy(update={"source_numbers": [1]})
            with (
                self.subTest(change=change),
                patch('research_library.agents.revision_agent.prepare_review_claims', return_value=claims),
                patch('research_library.agents.revision_agent.generate_text', return_value=self.answer) as generate,
            ):
                with self.assertRaises(ValueError):
                    revision_agent.revise_research_task(Mock(), SimpleNamespace(task_id=1, sources=[None]*3), review)
                generate.assert_not_called()

    def test_batch_rejects_task_or_item_count_mismatch(self):
        claims, review = self._batch_fixture()
        for task_id, items in ((2, review.items), (1, review.items[:-1])):
            with (
                self.subTest(task_id=task_id),
                patch('research_library.agents.revision_agent.prepare_review_claims', return_value=claims),
                patch('research_library.agents.revision_agent.generate_text') as generate,
            ):
                with self.assertRaises(ValueError):
                    revision_agent.revise_research_task(Mock(), SimpleNamespace(task_id=task_id, sources=[None]*3),
                        ResearchTaskReviewResponse(task_id=1, items=items))
                generate.assert_not_called()

    def test_empty_batch_returns_empty_response(self):
        with patch('research_library.agents.revision_agent.prepare_review_claims', return_value=[]):
            result = revision_agent.revise_research_task(Mock(), SimpleNamespace(task_id=1, sources=[]),
                                                        ResearchTaskReviewResponse(task_id=1, items=[]))
        self.assertIsNotNone(result)
        self.assertEqual(result.items, [])
        self.assertEqual(result.skipped_items, [])
