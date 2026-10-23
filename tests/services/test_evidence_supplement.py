"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestEvidenceSupplement(ComparisonDatabaseFixture, unittest.TestCase):
    """真实 SQLite 证据读取、相邻块补充及编号管理；模型调用单独模拟。"""

    def setUp(self):
        super().setUp()
        with TestingSessionLocal() as db:
            anchor = db.get(PaperChunk, self.analysis.sources[4].chunk_id)
            extra = PaperChunk(document_id=anchor.document_id, page_number=4, chunk_index=0,
                               text="候选召回数Top-K默认设置为10。", char_count=21)
            db.add(extra); db.flush()
            self.extra_id = extra.id
            db.commit()

    def _registry(self, db):
        from research_library.services.evidence_supplement import build_evidence_registry
        return build_evidence_registry(db, self.analysis)

    def _claim(self, registry, numbers=(5,)):
        evidence = [registry[self.analysis.sources[n - 1].chunk_id] for n in numbers]
        text = "候选召回数Top-K默认设置为500。" + "".join(f"[资料{n}]" for n in numbers)
        return ReviewClaim("experiments", text, list(numbers), evidence)

    def test_registry_preserves_all_original_numbers_and_real_text(self):
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            self.assertEqual(len(registry), 5)
            for number, source in enumerate(self.analysis.sources, 1):
                evidence = registry[source.chunk_id]
                self.assertEqual(evidence.source_number, number)
                self.assertEqual(evidence.text, self.raw_texts[number - 1])
                self.assertEqual(evidence.paper_id, source.paper_id)
                self.assertEqual(evidence.page_number, source.page_number)

    def test_registry_rejects_duplicate_and_missing_chunks(self):
        from research_library.services.evidence_supplement import build_evidence_registry
        duplicate = self.analysis.model_copy(deep=True)
        duplicate.sources.append(duplicate.sources[0])
        with TestingSessionLocal() as db:
            with self.assertRaisesRegex(ValueError, "重复文本块"):
                build_evidence_registry(db, duplicate)
            db.delete(db.get(PaperChunk, self.chunk_id)); db.commit()
            with self.assertRaisesRegex(ValueError, "不存在"):
                self._registry(db)

    def test_expansion_reuses_old_number_and_assigns_new_number_after_all_sources(self):
        from research_library.services.evidence_supplement import supplement_claim_evidence
        before = self._snapshot()
        original_analysis = self.analysis.model_dump()
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            claim = self._claim(registry)
            expanded = supplement_claim_evidence(db, claim, registry, max_new_chunks=2)
            self.assertEqual(expanded.source_numbers, [5, 2, 6])
            self.assertEqual(expanded.evidence[-1].chunk_id, self.extra_id)
            self.assertEqual(expanded.evidence[-1].text, "候选召回数Top-K默认设置为10。")
            self.assertEqual(expanded.evidence[-1].page_number, 4)
            self.assertTrue(all(e.paper_id == self.paper_ids[0] for e in expanded.evidence))
            self.assertEqual(claim.source_numbers, [5])
            self.assertEqual(len(claim.evidence), 1)
            self.assertEqual(expanded.claim, claim.claim)
            self.assertIsNot(expanded.evidence, claim.evidence)
        self.assertEqual(self.analysis.model_dump(), original_analysis)
        self.assertEqual(self._snapshot(), before)

    def test_shared_registry_reuses_new_number_across_claims(self):
        from research_library.services.evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            first = supplement_claim_evidence(db, self._claim(registry), registry)
            second = supplement_claim_evidence(db, self._claim(registry), registry)
            self.assertEqual(first.source_numbers, second.source_numbers)
            self.assertEqual(registry[self.extra_id].source_number, 6)
            self.assertEqual(len(registry), 6)

    def test_overlapping_neighbors_are_deduplicated(self):
        from research_library.services.evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            expanded = supplement_claim_evidence(db, self._claim(registry, (1, 5)), registry)
            self.assertEqual(expanded.source_numbers, [1, 5, 2, 6])
            ids = [e.chunk_id for e in expanded.evidence]
            self.assertEqual(len(ids), len(set(ids)))

    def test_budget_counts_existing_registry_neighbors_as_added_to_claim(self):
        from research_library.services.evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            expanded = supplement_claim_evidence(db, self._claim(registry), registry, max_new_chunks=1)
            self.assertEqual(expanded.source_numbers, [5, 2])
            self.assertNotIn(self.extra_id, registry)

    def test_zero_budget_and_empty_claim_do_not_query_neighbors(self):
        from research_library.services.evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db, patch('research_library.services.evidence_supplement.get_neighbor_chunks') as neighbors:
            registry = self._registry(db)
            claim = self._claim(registry)
            expanded = supplement_claim_evidence(db, claim, registry, max_new_chunks=0)
            self.assertEqual(expanded, claim)
            empty = ReviewClaim("experiments", "没有引用", [], [])
            self.assertEqual(supplement_claim_evidence(db, empty, {}), empty)
            neighbors.assert_not_called()

    def test_invalid_budget_fails_before_neighbor_query(self):
        from research_library.services.evidence_supplement import supplement_claim_evidence
        for count in (-1, 5, True, False, 1.5, "2", None):
            with self.subTest(count=count), patch('research_library.services.evidence_supplement.get_neighbor_chunks') as neighbors:
                with self.assertRaises(ValueError):
                    supplement_claim_evidence(Mock(), ReviewClaim("experiments", "", [], []), {}, count)
                neighbors.assert_not_called()

    def test_mismatched_evidence_rejected_before_registry_mutation(self):
        from dataclasses import replace
        from research_library.services.evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            snapshot = dict(registry)
            claim = self._claim(registry)
            forged = replace(claim.evidence[0], text="被修改的原文")
            bad_claim = replace(claim, evidence=[forged])
            with patch('research_library.services.evidence_supplement.get_neighbor_chunks') as neighbors:
                with self.assertRaisesRegex(ValueError, "不一致"):
                    supplement_claim_evidence(db, bad_claim, registry)
                neighbors.assert_not_called()
            self.assertEqual(registry, snapshot)

    def test_reviewer_receives_new_raw_text_and_accepts_new_source_number(self):
        from research_library.services.evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            expanded = supplement_claim_evidence(db, self._claim(registry), registry)
            answer = json.dumps(dict(verdict="contradicted", reason="新原文默认值是10，不是500。", source_numbers=[6]))
            with patch('research_library.agents.review_agent.generate_text', return_value=answer) as generate:
                result = review_agent.review_claim(expanded)
        self.assertEqual(result.verdict, "contradicted")
        self.assertEqual(result.source_numbers, [6])
        prompt = generate.call_args.kwargs["user_prompt"]
        self.assertIn("[资料6]", prompt)
        self.assertIn("候选召回数Top-K默认设置为10。", prompt)
        self.assertIn("页码：4", prompt)
