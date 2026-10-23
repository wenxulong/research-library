"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestPaperIdentityPrompts(unittest.TestCase):
    """相同标题、乱序资料也必须保留真实论文编号，禁止真实模型请求。"""

    def setUp(self):
        self.evidence = [
            ReviewEvidence(23, "相同标题", 9001, 4, "低切换实验结果", 9),
            ReviewEvidence(3, "相同标题", 9002, 5, "法律医疗实验结果", 10),
        ]
        self.claim = ReviewClaim("evidence_notes", "论文归属待核对。[资料23][资料3]", [23, 3], self.evidence)
        self.review = ReviewItemResponse(field=self.claim.field, claim=self.claim.claim,
                                        verdict="contradicted", reason="论文归属错误", source_numbers=[23, 3])

    def _check_prompt(self, prompt):
        self.assertIn("[资料23]\n论文编号：9\n论文标题：相同标题\n页码：4\n原文：低切换实验结果", prompt)
        self.assertIn("[资料3]\n论文编号：10\n论文标题：相同标题\n页码：5\n原文：法律医疗实验结果", prompt)
        self.assertNotIn("论文编号：23", prompt)

    def test_review_prompt_preserves_real_paper_ids(self):
        from research_library.agents.review_agent import review_claim
        with patch('research_library.agents.review_agent.generate_text', return_value=json.dumps(dict(
            verdict="contradicted", reason="论文归属错误", source_numbers=[23, 3]))) as generate:
            review_claim(self.claim)
        self._check_prompt(generate.call_args.kwargs["user_prompt"])

    def test_revision_prompt_preserves_ids_and_forbids_swapping(self):
        from research_library.agents.revision_agent import revise_claim
        with patch('research_library.agents.revision_agent.generate_text', return_value=json.dumps(dict(
            revised_claim="论文9报告低切换，论文10报告法律医疗实验。[资料23][资料3]", reason="核对来源"))) as generate:
            revise_claim(self.claim, self.review, 23)
        self._check_prompt(generate.call_args.kwargs["user_prompt"])
        self.assertIn("不得交换不同论文的结果", generate.call_args.kwargs["system_prompt"])

    def test_legacy_evidence_does_not_invent_paper_id(self):
        from dataclasses import replace
        from research_library.services.review_service import format_review_evidence
        text = format_review_evidence([replace(self.evidence[0], paper_id=None)])
        self.assertIn("[资料23]", text)
        self.assertNotIn("论文编号：", text)
        self.assertIn("低切换实验结果", text)
