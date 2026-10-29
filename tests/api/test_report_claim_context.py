"""陈述定位测试：隔离SQLite，不访问模型、队列和实际论文文件。"""
import unittest
from unittest.mock import patch

import tests.fixtures.main as fixtures
from research_library.services.comparison_evidence_workflow import ComparisonClaimResult, ComparisonEvidenceResult
from research_library.services.comparison_report_builder import assemble_comparison_report
from research_library.services.comparison_review import prepare_comparison_claims
from research_library.services.evidence_review_workflow import EvidenceReviewResult, EvidenceRevisionResult
from research_library.services.evidence_supplement import build_evidence_registry
from research_library.db.model import PaperChunk, ReportJob
from research_library.services.review_service import ReviewClaim
from research_library.schemas import AnalysisRevisionResponse, CheckedComparisonReportResponse, ReviewItemResponse


class TestReportClaimContext(fixtures.ComparisonDatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with fixtures.TestingSessionLocal() as db:
            from research_library.agents.comparison_agent import parse_comparison_response
            import json
            from research_library.services.comparison_service import build_comparison_context
            from research_library.schemas import ComparisonSourceResponse
            context = build_comparison_context(db, self.analysis)
            sources = [ComparisonSourceResponse(
                source_number=n, **self.analysis.sources[n - 1].model_dump()
            ) for n in context.source_numbers]
            report = parse_comparison_response(
                json.dumps(self.answer), self.task_id, self.analysis.question, sources)
            claims = prepare_comparison_claims(db, self.analysis, report)
            self.items = []
            for claim in claims:
                review = ReviewItemResponse(
                    field=claim.field, claim=claim.claim, verdict="partially_supported",
                    reason="需要更多实验条件", source_numbers=claim.source_numbers)
                self.items.append(ComparisonClaimResult(
                    EvidenceReviewResult(claim, review, review, []), None))
            self.processing = ComparisonEvidenceResult(
                self.task_id, self.items, build_evidence_registry(db, self.analysis))
            self.checked = assemble_comparison_report(self.analysis, self.processing)
            job = ReportJob(task_id=self.task_id, status="succeeded", stage="finished",
                            analysis_snapshot=self.analysis.model_dump(mode="json"),
                            result=self.checked.model_dump(mode="json"))
            db.add(job)
            db.commit()
            self.job_id = job.id
        self.claim_id = self.checked.report.method_comparisons[0].claim_id
        self.url = f"/report-jobs/{self.job_id}/claims/{self.claim_id}"

    def test_final_statements_and_reviews_share_persisted_unique_ids(self):
        statements = self.checked.report.method_comparisons + self.checked.report.evidence_notes
        ids = [s.claim_id for s in statements]
        self.assertTrue(all(ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [r.claim_id for r in self.checked.review.items])
        self.assertEqual(self.items[0].review_result.final_review.claim_id, "")
        restored = CheckedComparisonReportResponse.model_validate_json(self.checked.model_dump_json())
        self.assertEqual(restored.report.method_comparisons[0].claim_id, self.claim_id)

    def test_api_returns_current_claim_final_review_and_real_evidence_without_writes(self):
        with patch('research_library.core.llm_service.OpenAI') as llm:
            first = fixtures.client.get(self.url)
            second = fixtures.client.get(self.url)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        data = first.json()
        self.assertEqual(data["review"]["claim_id"], self.claim_id)
        self.assertEqual(data["text"], self.checked.report.method_comparisons[0].text)
        self.assertEqual(data["paper_ids"], self.paper_ids)
        self.assertEqual([e["source_number"] for e in data["evidence"]], [1, 3])
        self.assertEqual([e["text"] for e in data["evidence"]],
                         [self.raw_texts[0], self.raw_texts[2]])
        llm.assert_not_called()
        with fixtures.TestingSessionLocal() as db:
            self.assertEqual(db.get(ReportJob, self.job_id).result,
                             self.checked.model_dump(mode="json"))

    def test_missing_job_and_claim_return_404(self):
        self.assertEqual(fixtures.client.get("/report-jobs/99999/claims/missing").status_code, 404)
        self.assertEqual(fixtures.client.get(f"/report-jobs/{self.job_id}/claims/missing").status_code, 404)

    def test_unfinished_job_returns_409(self):
        with fixtures.TestingSessionLocal() as db:
            db.get(ReportJob, self.job_id).status = "running"
            db.commit()
        self.assertEqual(fixtures.client.get(self.url).status_code, 409)

    def test_mismatched_final_review_returns_409(self):
        changed = self.checked.model_dump(mode="json")
        changed["review"]["items"][0]["claim"] = "另一条陈述"
        with fixtures.TestingSessionLocal() as db:
            db.get(ReportJob, self.job_id).result = changed
            db.commit()
        self.assertEqual(fixtures.client.get(self.url).status_code, 409)

    def test_missing_original_chunk_returns_409(self):
        with fixtures.TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id))
            db.commit()
        self.assertEqual(fixtures.client.get(self.url).status_code, 409)

    def test_legacy_report_still_reads_without_inventing_ids(self):
        old = self.checked.model_dump(mode="json")
        for field in ("method_comparisons", "experiment_comparisons",
                      "conclusion_comparisons", "evidence_notes"):
            for item in old["report"][field]:
                item.pop("claim_id")
        for field in ("review", "initial_review"):
            for item in old[field]["items"]:
                item.pop("claim_id", None)
        with fixtures.TestingSessionLocal() as db:
            db.get(ReportJob, self.job_id).result = old
            db.commit()
        response = fixtures.client.get(f"/report-jobs/{self.job_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"]["report"]["method_comparisons"][0]["claim_id"], "")
        self.assertEqual(fixtures.client.get(self.url).status_code, 404)

    def test_accepted_revision_gets_matching_id_for_final_text(self):
        original = self.items[0].review_result
        revised = ReviewClaim(original.claim.field,
                              "修订后的对比结论。[资料1][资料3]", [1, 3], original.claim.evidence)
        review = ReviewItemResponse(field=revised.field, claim=revised.claim, verdict="supported",
                                    reason="已核对", source_numbers=[1, 3])
        revision = AnalysisRevisionResponse(field=revised.field, original_claim=original.claim.claim,
                                             revised_claim=revised.claim, reason="缩小范围", source_numbers=[1, 3])
        self.items[0] = ComparisonClaimResult(original, EvidenceRevisionResult(revision, revised, review))
        checked = assemble_comparison_report(self.analysis, self.processing)
        statement = checked.report.method_comparisons[0]
        self.assertEqual(statement.text, revised.claim)
        self.assertEqual(checked.review.items[0].claim_id, statement.claim_id)
        self.assertEqual(checked.review.items[0].claim, statement.text)

    def test_review_only_sources_are_loaded_without_changing_statement_citations(self):
        changed = self.checked.model_dump(mode='json')
        changed['review']['items'][0].update(
            verdict='contradicted', source_numbers=[3, 5, 1, 5])
        with fixtures.TestingSessionLocal() as db:
            db.get(ReportJob, self.job_id).result = changed
            db.commit()
        response = fixtures.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['source_numbers'], [1, 3])
        self.assertEqual([e['source_number'] for e in data['evidence']], [1, 3, 5])
        self.assertEqual(data['evidence'][-1]['text'], self.raw_texts[4])
        self.assertEqual(data['review']['verdict'], 'contradicted')
        with fixtures.TestingSessionLocal() as db:
            self.assertEqual(db.get(ReportJob, self.job_id).result, changed)

    def test_missing_review_only_original_is_reported_instead_of_silently_skipped(self):
        changed = self.checked.model_dump(mode='json')
        changed['review']['items'][0]['source_numbers'] = [1, 3, 5]
        with fixtures.TestingSessionLocal() as db:
            db.get(ReportJob, self.job_id).result = changed
            db.delete(db.get(PaperChunk, self.analysis.sources[4].chunk_id))
            db.commit()
        response = fixtures.client.get(self.url)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('资料5', response.json()['detail'])
