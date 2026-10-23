"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestEvidenceNotes(unittest.TestCase):
    """区分论文局限性和检索证据不足，并兼容旧分析数据。"""

    def _legacy_analysis(self):
        return dict(task_id=1,question="研究问题",summary="概述",methods=[],experiments=[],
                    conclusions=[],limitations=[],sources=[])

    def test_old_analysis_json_defaults_notes_to_empty_list(self):
        data=self._legacy_analysis()
        analysis=ResearchTaskAnalysisResponse.model_validate_json(json.dumps(data))
        self.assertEqual(analysis.evidence_notes,[])
        parsed=paper_analysis_agent.parse_analysis_response(json.dumps(data))
        self.assertEqual(parsed["evidence_notes"],[])

    def test_notes_type_and_citations_are_validated(self):
        data=self._legacy_analysis()
        data["evidence_notes"]="不是列表"
        with self.assertRaises(RuntimeError):
            paper_analysis_agent.parse_analysis_response(json.dumps(data))
        data["evidence_notes"]=["当前资料未提供。[资料2]"]
        with self.assertRaisesRegex(RuntimeError,"evidence_notes.*引用不存在"):
            paper_analysis_agent.validate_analysis_citations(data,1)

    def test_notes_are_prepared_for_review_with_original_evidence(self):
        analysis=ResearchTaskAnalysisResponse.model_validate(dict(self._legacy_analysis(),
            evidence_notes=["当前引用原文未明确说明。[资料1]"],
            sources=[dict(paper_id=3,paper_title="论文",chunk_id=14,page_number=5,score=0.9)]))
        db=Mock()
        db.get.side_effect=[SimpleNamespace(id=14,document_id=2,page_number=5,text="原文"),SimpleNamespace(paper_id=3)]
        claims=review_service.prepare_review_claims(db,analysis)
        self.assertEqual(claims[-1].field,"evidence_notes")
        self.assertEqual(claims[-1].evidence[0].text,"原文")

    def test_classifier_accepts_notes_and_suggests_them_for_meta_claims(self):
        from research_library.schemas import AnalysisRevisionResponse
        revision=AnalysisRevisionResponse(field="limitations",original_claim="原陈述",
            revised_claim="当前原文未提及生产环境验证。[资料1]",reason="备注",source_numbers=[1])
        misplaced=json.dumps(dict(verdict="misplaced",reason="这是检索备注",suggested_field="evidence_notes"))
        with patch('research_library.agents.revision_agent.generate_text',return_value=misplaced):
            result=revision_agent.check_revision_field(revision)
        self.assertEqual(result.suggested_field,"evidence_notes")
        with patch('research_library.agents.revision_agent.generate_text',return_value=json.dumps(dict(verdict="appropriate",reason="证据备注",suggested_field=None))) as generate:
            revision_agent.check_revision_field(revision.model_copy(update={"field":"evidence_notes"}))
        prompt=generate.call_args.kwargs["system_prompt"]
        self.assertIn("evidence_notes用于当前引用原文不足",prompt)
        self.assertIn("limitations只用于论文自身",prompt)

    def test_splitter_can_separate_methods_and_evidence_notes(self):
        from research_library.schemas import AnalysisRevisionResponse, FieldCheckResponse
        revision=AnalysisRevisionResponse(field="limitations",original_claim="原陈述",
            revised_claim="使用缓存。[资料1]当前引用原文未说明生产验证。[资料1]",reason="拆分",source_numbers=[1])
        category=FieldCheckResponse(verdict="mixed",reason="方法和证据备注混合")
        answer=json.dumps(dict(reason="分开方法和备注",items=[
            dict(field="methods",claim="使用缓存。[资料1]"),
            dict(field="evidence_notes",claim="当前引用原文未说明生产验证。[资料1]"),
        ]))
        with patch('research_library.services.field_splitter.generate_text',return_value=answer):
            result=field_splitter.split_mixed_revision(revision,category,1)
        self.assertEqual(result.items[1].field,"evidence_notes")
        self.assertEqual(result.items[1].source_numbers,[1])

    def test_move_preserves_text_citations_and_original_field(self):
        from research_library.schemas import AnalysisRevisionResponse
        revision=AnalysisRevisionResponse(field="limitations",original_claim="原陈述[资料1]",
            revised_claim="当前原文未说明。[资料1]",reason="修改",source_numbers=[1])
        claim=ReviewClaim("limitations",revision.original_claim,[1],[ReviewEvidence(1,"论文",14,5,"原文")])
        before=revision.model_dump()
        with (
            patch('research_library.agents.review_agent.generate_text',return_value=json.dumps(dict(verdict="supported",reason="引用片段确实未说明",source_numbers=[1]))),
            patch('research_library.agents.revision_agent.generate_text',return_value=json.dumps(dict(verdict="appropriate",reason="证据备注",suggested_field=None))),
        ):
            result=revision_agent.move_revision_to_evidence_notes(claim,revision,1)
        self.assertEqual(result.source_field,"limitations")
        self.assertEqual(result.item.field,"evidence_notes")
        self.assertEqual(result.item.claim,revision.revised_claim)
        self.assertEqual(result.review.field,"evidence_notes")
        self.assertTrue(result.ready)
        self.assertEqual(revision.model_dump(),before)

    def test_move_does_not_accept_unverified_note(self):
        from research_library.schemas import AnalysisRevisionResponse
        revision=AnalysisRevisionResponse(field="limitations",original_claim="原陈述[资料1]",
            revised_claim="当前原文未说明。[资料1]",reason="修改",source_numbers=[1])
        claim=ReviewClaim("limitations",revision.original_claim,[1],[ReviewEvidence(1,"论文",14,5,"原文")])
        with (
            patch('research_library.agents.review_agent.generate_text',return_value=json.dumps(dict(verdict="insufficient_evidence",reason="证据不足",source_numbers=[1]))),
            patch('research_library.agents.revision_agent.generate_text',return_value=json.dumps(dict(verdict="appropriate",reason="备注",suggested_field=None))),
        ):
            result=revision_agent.move_revision_to_evidence_notes(claim,revision,1)
        self.assertFalse(result.ready)

    def test_analysis_agent_returns_new_evidence_notes(self):
        data=dict(self._legacy_analysis(),summary="概述[资料1]",evidence_notes=["当前原文不足。[资料1]"])
        with (
            patch('research_library.agents.paper_analysis_agent.collect_analysis_matches',return_value=[(SimpleNamespace(id=14,page_number=5,text="原文"),SimpleNamespace(id=3,title="论文"),0.9)]),
            patch('research_library.agents.paper_analysis_agent.generate_text',return_value=json.dumps(data)),
        ):
            result=paper_analysis_agent.analyze_research_task(Mock(),1,"研究问题")
        self.assertEqual(result.evidence_notes,data["evidence_notes"])
