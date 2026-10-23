"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestComparisonEvidenceAssembly(ComparisonDatabaseFixture, unittest.TestCase):
    """验证补证结果组装与完整内部链路；模型网络调用全部模拟。"""

    def setUp(self):
        super().setUp()
        import research_library.services.comparison_report_builder as builder
        from research_library.services.evidence_supplement import build_evidence_registry
        from research_library.services.comparison_evidence_workflow import ComparisonEvidenceResult
        self.builder = builder
        with TestingSessionLocal() as db:
            registry = build_evidence_registry(db, self.analysis)
        # 故意使用不连续编号23：不能用证据数量代替编号上限。
        self.new_evidence = ReviewEvidence(23, "论文A", 9001, 4,
                                          "候选召回数Top-K默认设置为10。", self.paper_ids[0])
        registry[9001] = self.new_evidence
        self.result = ComparisonEvidenceResult(self.task_id, [], dict(reversed(list(registry.items()))))
        self.original_text = "A采用语义匹配，B采用完全匹配。[资料1][资料3]"
        self.result.items.append(self._item(self.original_text, [1, 3]))
        # 所有测试必须显式模拟模型；意外外部请求直接失败。
        for target in ('research_library.agents.review_agent.generate_text', 'research_library.agents.revision_agent.generate_text'):
            guard = patch(target, side_effect=AssertionError("测试禁止真实模型请求"))
            guard.start()
            self.addCleanup(guard.stop)

    def _claim(self, text, numbers, field="method_comparisons"):
        by_number = {e.source_number: e for e in self.result.evidence_by_chunk_id.values()}
        return ReviewClaim(field, text, numbers, [by_number[n] for n in numbers])

    def _review(self, claim, verdict="supported"):
        return ReviewItemResponse(field=claim.field, claim=claim.claim, verdict=verdict,
                                  reason="测试审校意见", source_numbers=claim.source_numbers)

    def _item(self, text, numbers, verdict="supported", field="method_comparisons"):
        from research_library.services.comparison_evidence_workflow import ComparisonClaimResult
        from research_library.services.evidence_review_workflow import EvidenceReviewResult
        claim = self._claim(text, numbers, field)
        review = self._review(claim, verdict)
        return ComparisonClaimResult(EvidenceReviewResult(claim, review, review, []), None)

    def _add_revision(self, verdict="supported", text=None, numbers=None):
        from research_library.services.comparison_evidence_workflow import ComparisonClaimResult
        from research_library.services.evidence_review_workflow import EvidenceRevisionResult
        from research_library.schemas import AnalysisRevisionResponse
        original = self.result.items[0]
        claim = self._claim(text or "A的Top-K为10，B使用完全匹配。[资料23][资料3]",
                            [23, 3] if numbers is None else numbers)
        revision = AnalysisRevisionResponse(field=claim.field, original_claim=self.original_text,
                                            revised_claim=claim.claim, reason="按新增原文修正",
                                            source_numbers=claim.source_numbers)
        self.result.items[0] = ComparisonClaimResult(
            original.review_result,
            EvidenceRevisionResult(revision, claim, self._review(claim, verdict)))

    def test_failed_revision_preserves_original_and_records_rejection(self):
        from dataclasses import asdict
        for verdict in ("contradicted", "partially_supported", "insufficient_evidence"):
            with self.subTest(verdict=verdict):
                self.result.items[0] = self._item(self.original_text, [1, 3], "partially_supported")
                self._add_revision(verdict=verdict)
                before = asdict(self.result)
                report = self.builder.assemble_comparison_report(self.analysis, self.result)
                self.assertEqual(report.report.method_comparisons[0].text, self.original_text)
                self.assertEqual(report.report.method_comparisons[0].source_numbers, [1, 3])
                self.assertEqual(report.review.items[0].verdict, "partially_supported")
                self.assertEqual(report.review.items[0].claim, self.original_text)
                self.assertFalse(report.passed)
                self.assertEqual(report.correction_rounds, 1)
                rejected = report.rejected_revisions[0]
                self.assertEqual(rejected.review.verdict, verdict)
                self.assertEqual(rejected.revision.revised_claim, self.result.items[0].revision_result.revised_claim.claim)
                self.assertEqual(asdict(self.result), before)
                # 经过JSON序列化，查询方仍能读取未采用的修订及原因。
                from research_library.schemas import CheckedComparisonReportResponse
                restored = CheckedComparisonReportResponse.model_validate_json(report.model_dump_json())
                self.assertEqual(restored.rejected_revisions, report.rejected_revisions)

    def test_accepted_revision_has_no_rejections(self):
        self._add_revision()
        report = self.builder.assemble_comparison_report(self.analysis, self.result)
        self.assertTrue(report.passed)
        self.assertEqual(report.rejected_revisions, [])

    def test_legacy_report_without_rejections_is_readable(self):
        from research_library.schemas import CheckedComparisonReportResponse
        report = self.builder.assemble_comparison_report(self.analysis, self.result)
        payload = report.model_dump()
        del payload["rejected_revisions"]
        self.assertEqual(CheckedComparisonReportResponse.model_validate(payload).rejected_revisions, [])

    def test_mismatched_rejected_revision_is_not_silently_accepted(self):
        from dataclasses import replace
        self._add_revision(verdict="contradicted")
        item = self.result.items[0]
        wrong = item.revision_result.review.model_copy(update={"claim": "不对应的陈述"})
        self.result.items[0] = replace(item, revision_result=replace(item.revision_result, review=wrong))
        with self.assertRaisesRegex(ValueError, "修订陈述与审校结果不一致"):
            self.builder.assemble_comparison_report(self.analysis, self.result)

    def test_sources_include_every_entry_sorted_without_renumbering(self):
        sources = self.builder.build_report_sources(self.analysis, self.result)
        self.assertEqual([s.source_number for s in sources], [1, 2, 3, 4, 5, 23])
        self.assertEqual(sources[-1].chunk_id, 9001)
        self.assertEqual([s.score for s in sources[:-1]], [0.9] * 5)
        self.assertIsNone(sources[-1].score)
        self.assertEqual(json.loads(sources[-1].model_dump_json())["score"], None)

    def test_empty_registry_returns_empty_list(self):
        from dataclasses import replace
        result = replace(self.result, evidence_by_chunk_id={})
        self.assertEqual(self.builder.build_report_sources(self.analysis, result), [])

    def test_source_without_paper_id_is_rejected_even_after_first_entry(self):
        from dataclasses import replace
        self.result.evidence_by_chunk_id[9001] = replace(self.new_evidence, paper_id=None)
        with self.assertRaisesRegex(ValueError, "缺少论文编号"):
            self.builder.build_report_sources(self.analysis, self.result)

    def test_supported_original_is_preserved_and_input_is_unchanged(self):
        from dataclasses import asdict
        before = asdict(self.result)
        analysis_before = self.analysis.model_dump()
        report = self.builder.assemble_comparison_report(self.analysis, self.result)
        self.assertEqual(report.report.method_comparisons[0].text, self.original_text)
        self.assertEqual(report.report.method_comparisons[0].source_numbers, [1, 3])
        self.assertTrue(report.passed)
        self.assertEqual(report.correction_rounds, 0)
        self.assertEqual(asdict(self.result), before)
        self.assertEqual(self.analysis.model_dump(), analysis_before)

    def test_revision_uses_new_text_and_matching_final_review(self):
        self._add_revision()
        report = self.builder.assemble_comparison_report(self.analysis, self.result)
        self.assertIn("Top-K为10", report.report.method_comparisons[0].text)
        self.assertEqual(report.report.method_comparisons[0].source_numbers, [23, 3])
        self.assertEqual(report.initial_review.items[0].claim, self.original_text)
        self.assertEqual(report.review.items[0].claim, report.report.method_comparisons[0].text)
        self.assertTrue(report.passed)
        self.assertEqual(report.correction_rounds, 1)

    def test_unresolved_original_and_revision_never_pass(self):
        for revised in (False, True):
            with self.subTest(revised=revised):
                self.result.items[0] = self._item(self.original_text, [1, 3], "insufficient_evidence")
                if revised:
                    self._add_revision(verdict="contradicted")
                report = self.builder.assemble_comparison_report(self.analysis, self.result)
                self.assertFalse(report.passed)
                self.assertEqual(report.correction_rounds, int(revised))

    def test_single_paper_comparison_is_rejected(self):
        self._add_revision(text="A的Top-K为10。[资料23]", numbers=[23])
        with self.assertRaisesRegex(RuntimeError, "至少两篇"):
            self.builder.assemble_comparison_report(self.analysis, self.result)

    def test_one_paper_evidence_note_is_allowed(self):
        self.result.items[:] = [self._item("原文给出的Top-K是10。[资料23]", [23], field="evidence_notes")]
        report = self.builder.assemble_comparison_report(self.analysis, self.result)
        self.assertTrue(report.passed)
        self.assertEqual(report.report.evidence_notes[0].source_numbers, [23])

    def test_unknown_citation_and_empty_report_are_rejected(self):
        self.result.items[:] = [self._item("错误引用。[资料99]", [1, 3])]
        with self.assertRaisesRegex(RuntimeError, "引用"):
            self.builder.assemble_comparison_report(self.analysis, self.result)
        self.result.items.clear()
        with self.assertRaisesRegex(RuntimeError, "空报告"):
            self.builder.assemble_comparison_report(self.analysis, self.result)

    def test_task_field_and_review_mismatches_are_rejected(self):
        from dataclasses import replace
        with self.assertRaisesRegex(ValueError, "研究任务不一致"):
            self.builder.assemble_comparison_report(self.analysis, replace(self.result, task_id=999))
        self.result.items[:] = [self._item(self.original_text, [1, 3], field="unknown")]
        with self.assertRaisesRegex(ValueError, "未知的报告字段"):
            self.builder.assemble_comparison_report(self.analysis, self.result)
        item = self._item(self.original_text, [1, 3])
        wrong = item.review_result.final_review.model_copy(update={"claim": "另一句话"})
        self.result.items[:] = [replace(item, review_result=replace(item.review_result, final_review=wrong))]
        with self.assertRaisesRegex(ValueError, "审校结果不一致"):
            self.builder.assemble_comparison_report(self.analysis, self.result)

    def test_no_new_neighbors_does_not_repeat_review(self):
        from research_library.services.evidence_review_workflow import review_claim_with_supplement
        claim = self._claim(self.original_text, [1, 3])
        for verdict in ("supported", "contradicted", "insufficient_evidence", "partially_supported"):
            with self.subTest(verdict=verdict), \
                 patch('research_library.services.evidence_review_workflow.review_claim', return_value=self._review(claim, verdict)) as review, \
                 patch('research_library.services.evidence_review_workflow.supplement_claim_evidence', return_value=claim) as supplement:
                result = review_claim_with_supplement(Mock(), claim, self.result.evidence_by_chunk_id)
                self.assertEqual(review.call_count, 1)
                self.assertEqual(result.added_source_numbers, [])
                self.assertEqual(supplement.call_count, int(verdict in ("insufficient_evidence", "partially_supported")))

    def test_revision_accepts_sparse_new_number_and_rechecks_only_cited_evidence(self):
        from research_library.services.evidence_review_workflow import revise_supplemented_claim, EvidenceReviewResult
        claim = self._claim("Top-K是500。[资料1]", [1, 3, 23])
        old_review = self._review(claim, "contradicted")
        output = json.dumps({"revised_claim": "Top-K默认是10。[资料23]", "reason": "根据新增原文修正"})
        review_output = json.dumps({"verdict": "supported", "reason": "原文是10", "source_numbers": [23]})
        with patch('research_library.agents.revision_agent.generate_text', return_value=output) as revise, \
             patch('research_library.agents.review_agent.generate_text', return_value=review_output) as review:
            result = revise_supplemented_claim(EvidenceReviewResult(claim, old_review, old_review, [23]))
        self.assertEqual(result.revised_claim.source_numbers, [23])
        self.assertEqual(result.revised_claim.evidence, [self.new_evidence])
        self.assertIn("[资料23]", revise.call_args.kwargs["user_prompt"])
        self.assertIn(self.new_evidence.text, review.call_args.kwargs["user_prompt"])
        self.assertNotIn("[资料1]", review.call_args.kwargs["user_prompt"])

    def test_full_internal_workflow_reads_db_supplements_revises_and_assembles(self):
        import research_library.agents.comparison_agent as comparison_agent
        from research_library.services.comparison_evidence_workflow import process_comparison_evidence
        from research_library.schemas import ComparisonSourceResponse
        with TestingSessionLocal() as db:
            anchor = db.get(PaperChunk, self.analysis.sources[4].chunk_id)
            extra = PaperChunk(document_id=anchor.document_id, page_number=4, chunk_index=0,
                               text="候选召回数Top-K默认设置为10。", char_count=21)
            db.add(extra); db.commit()
            extra_id = extra.id
        # 先查5的邻居，可获得旧资料2和新资料6；新编号不从报告中只出现的编号数开始。
        data = dict(method_comparisons=[], experiment_comparisons=[
            {"text": "A的Top-K是500，B使用完全匹配。[资料5][资料3]"}],
            conclusion_comparisons=[], evidence_notes=[])
        sources = [ComparisonSourceResponse(source_number=n, **self.analysis.sources[n-1].model_dump())
                   for n in (1, 3, 5)]
        report = comparison_agent.parse_comparison_response(json.dumps(data), self.task_id,
                                                            self.analysis.question, sources)
        before = self._snapshot()
        original_report = report.model_dump()
        responses = [
            dict(verdict="insufficient_evidence", reason="缺少默认值", source_numbers=[]),
            dict(verdict="contradicted", reason="新增原文是10", source_numbers=[6]),
            dict(verdict="supported", reason="修正后有依据", source_numbers=[6, 3]),
        ]
        revision = dict(revised_claim="A的Top-K是10，B使用完全匹配。[资料6][资料3]", reason="依据新原文")
        with TestingSessionLocal() as db, \
             patch('research_library.agents.review_agent.generate_text', side_effect=[json.dumps(r) for r in responses]) as review, \
             patch('research_library.agents.revision_agent.generate_text', return_value=json.dumps(revision)) as revise:
            processed = process_comparison_evidence(db, self.analysis, report)
            assembled = self.builder.assemble_comparison_report(self.analysis, processed)
        self.assertEqual(review.call_count, 3)
        revise.assert_called_once()
        self.assertEqual(processed.items[0].review_result.added_source_numbers, [2, 6])
        self.assertEqual(processed.evidence_by_chunk_id[extra_id].source_number, 6)
        self.assertTrue(assembled.passed)
        self.assertEqual(assembled.report.experiment_comparisons[0].source_numbers, [6, 3])
        self.assertIsNone(assembled.report.sources[-1].score)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(report.model_dump(), original_report)
