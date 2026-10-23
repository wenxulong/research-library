"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestSplitVerification(unittest.TestCase):
    def setUp(self):
        from research_library.schemas import AnalysisRevisionResponse, FieldCheckResponse, AnalysisSplitResponse
        self.claim=ReviewClaim("limitations","原陈述[资料3]",[3],[ReviewEvidence(3,"论文",14,5,"方法原文")])
        self.revision=AnalysisRevisionResponse(field="limitations",original_claim=self.claim.claim,
            revised_claim="方法与边界[资料3]",reason="修改",source_numbers=[3])
        self.category=FieldCheckResponse(verdict="mixed",reason="混合",suggested_field=None)
        self.split=AnalysisSplitResponse(source_field="limitations",source_claim=self.revision.revised_claim,reason="拆分",items=[
            dict(field="methods",claim="方法[资料3]",source_numbers=[3]),
            dict(field="limitations",claim="当前证据不足",source_numbers=[]),
        ])

    def test_missing_evidence_is_rejected_before_recheck(self):
        claim=ReviewClaim(self.claim.field,self.claim.claim,[3],[])
        with patch('research_library.services.field_splitter.split_mixed_revision',return_value=self.split),patch('research_library.agents.revision_agent.review_claim') as check:
            with self.assertRaisesRegex(ValueError,"原文证据不存在"):
                revision_agent.split_and_check_revision(claim,self.revision,self.category,3)
        check.assert_not_called()

    def test_no_citation_part_is_not_marked_ready(self):
        appropriate=json.dumps(dict(verdict="appropriate",reason="分类正确",suggested_field=None))
        supported=json.dumps(dict(verdict="supported",reason="原文支持",source_numbers=[3]))
        with (
            patch('research_library.services.field_splitter.split_mixed_revision',return_value=self.split),
            patch('research_library.agents.review_agent.generate_text',return_value=supported) as generate,
            patch('research_library.agents.revision_agent.generate_text',return_value=appropriate),
        ):
            result,checks=revision_agent.split_and_check_revision(self.claim,self.revision,self.category,3)
        generate.assert_called_once()
        self.assertEqual([i.ready for i in checks],[True,False])
        self.assertEqual(checks[1].review.verdict,"insufficient_evidence")
