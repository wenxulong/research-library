"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestRevisionRecheck(unittest.TestCase):
    """确保再次审校使用修订后的文本及其实际引用的原文。"""

    def setUp(self):
        from research_library.schemas import AnalysisRevisionResponse
        self.claim = ReviewClaim("conclusions", "所有数据集成本下降。[资料3][资料5]", [3, 5], [
            ReviewEvidence(3, "论文", 14, 5, "数据集A成本下降20%。"),
            ReviewEvidence(5, "论文", 20, 6, "不应提供给本次审校的未引用段落。"),
        ])
        self.revision = AnalysisRevisionResponse(field="conclusions", original_claim=self.claim.claim,
            revised_claim="数据集A成本下降20%。[资料3]", reason="限定范围", source_numbers=[3])

    def test_recheck_uses_revised_text_and_only_its_cited_evidence(self):
        answer = json.dumps(dict(verdict="supported", reason="原文支持", source_numbers=[3]))
        with patch('research_library.agents.review_agent.generate_text', return_value=answer) as generate:
            result = revision_agent.review_revision(self.claim, self.revision, 5)
        generate.assert_called_once()
        prompt = generate.call_args.kwargs["user_prompt"]
        self.assertIn(self.revision.revised_claim, prompt)
        self.assertIn("数据集A成本下降20%。", prompt)
        self.assertNotIn("不应提供给本次审校", prompt)
        self.assertNotIn("[资料5]", prompt)
        self.assertEqual(result.claim, self.revision.revised_claim)
        self.assertEqual(result.source_numbers, [3])

    def test_recheck_rejects_invalid_revision_before_model_call(self):
        cases = [
            {"field": "methods"}, {"original_claim": "另一条陈述"}, {"revised_claim": " "},
            {"source_numbers": [5]},
            {"revised_claim": "修改后[资料3", "source_numbers": [3]},
            {"revised_claim": "修改后[资料6]", "source_numbers": [6]},
            {"revised_claim": "修改后[资料1]", "source_numbers": [1]},
        ]
        with patch('research_library.agents.review_agent.generate_text') as generate:
            for update in cases:
                with self.subTest(update=update):
                    with self.assertRaises(ValueError):
                        revision_agent.review_revision(self.claim, self.revision.model_copy(update=update), 5)
        generate.assert_not_called()

    def test_recheck_requires_actual_evidence_for_cited_number(self):
        claim = ReviewClaim(self.claim.field, self.claim.claim, [3, 5], [])
        with patch('research_library.agents.review_agent.generate_text') as generate:
            with self.assertRaisesRegex(ValueError, "没有找到资料3"):
                revision_agent.review_revision(claim, self.revision, 5)
        generate.assert_not_called()

    def test_revision_without_citations_returns_insufficient_evidence_without_model_call(self):
        revision = self.revision.model_copy(update={"revised_claim": "当前原文不足以确认。", "source_numbers": []})
        with patch('research_library.agents.review_agent.generate_text') as generate:
            result = revision_agent.review_revision(self.claim, revision, 5)
        self.assertEqual(result.verdict, "insufficient_evidence")
        self.assertEqual(result.claim, revision.revised_claim)
        generate.assert_not_called()

    def test_combined_returns_empty_response_when_no_drafts(self):
        from research_library.schemas import ResearchTaskRevisionResponse
        draft = ResearchTaskRevisionResponse(task_id=1, items=[], skipped_items=[])
        with (
            patch('research_library.agents.revision_agent.revise_research_task', return_value=draft),
            patch('research_library.agents.revision_agent.prepare_review_claims', return_value=[]),
            patch('research_library.agents.review_agent.generate_text') as generate,
        ):
            result = revision_agent.revise_and_review_research_task(Mock(), SimpleNamespace(task_id=1, sources=[]), Mock())
        self.assertEqual(result.task_id, 1)
        self.assertEqual(result.items, [])
        generate.assert_not_called()

    def test_combined_rejects_draft_with_unknown_original_claim(self):
        from research_library.schemas import ResearchTaskRevisionResponse
        draft = ResearchTaskRevisionResponse(task_id=1, items=[self.revision], skipped_items=[])
        with (
            patch('research_library.agents.revision_agent.revise_research_task', return_value=draft),
            patch('research_library.agents.revision_agent.prepare_review_claims', return_value=[]),
            patch('research_library.agents.review_agent.generate_text') as generate,
        ):
            with self.assertRaisesRegex(ValueError, "找不到对应的原陈述"):
                revision_agent.revise_and_review_research_task(Mock(), SimpleNamespace(task_id=1, sources=[None]*5), Mock())
        generate.assert_not_called()
