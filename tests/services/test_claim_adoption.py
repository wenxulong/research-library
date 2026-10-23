"""采用候选、编号一致性和报告导出；不访问外部模型。"""
import unittest
from unittest.mock import patch

import tests.fixtures.main as fixtures
import research_library.services.report_claim_investigation as service
from research_library.db.model import PaperChunk, ReportJob, ReportClaimInvestigation
from tests.agents.test_evidence_investigation import InvestigationFixture


class TestClaimAdoption(InvestigationFixture, unittest.TestCase):
    def candidate(self, verdict='supported', status='finished'):
        with fixtures.TestingSessionLocal() as db:
            chunk = db.get(PaperChunk, self.chunk_id)
            extra = PaperChunk(document_id=chunk.document_id, page_number=99, chunk_index=0,
                               text='补充实验原文', char_count=6)
            db.add(extra); db.commit()
            raw = self.react_result(status)
            raw['evidence'].append({'chunk_id':extra.id})
            review = self.context.review.model_copy(update={
                'verdict':verdict, 'source_numbers':[1, 3, 6], 'reason':'结合新增实验原文复审'})
            with patch.object(service,'run_evidence_react',return_value=raw), \
                 patch.object(service,'review_claim',return_value=review):
                return self.run_saved(db)

    def adopt(self, candidate_id):
        return fixtures.client.post(f'/report-claim-investigations/{candidate_id}/adopt')

    def test_adopt_persists_ids_citations_review_and_export_then_is_idempotent(self):
        saved = self.candidate()
        with patch('research_library.core.llm_service.OpenAI') as llm:
            response = self.adopt(saved.id)
            again = self.adopt(saved.id)
        self.assertEqual(response.status_code,200,response.text)
        first=response.json(); second=again.json()
        self.assertFalse(first['already_adopted']); self.assertTrue(second['already_adopted'])
        self.assertEqual(first['checked_report'],second['checked_report'])
        checked=first['checked_report']; statement=checked['report']['method_comparisons'][0]
        self.assertEqual(statement['claim_id'],self.context.claim_id)
        self.assertEqual(statement['text'],self.context.text+'[资料6]')
        self.assertEqual(statement['source_numbers'],[1,3,6])
        self.assertEqual(checked['review']['items'][0]['claim'],statement['text'])
        self.assertEqual(checked['review']['items'][0]['source_numbers'],[1,3,6])
        self.assertFalse(checked['passed'])  # 其他陈述仍为部分支持。
        with fixtures.TestingSessionLocal() as db:
            self.assertIsNotNone(db.get(ReportClaimInvestigation,saved.id).adopted_at)
            self.assertEqual(db.get(ReportJob,self.job_id).result,checked)
        exported=fixtures.client.get(f'/report-jobs/{self.job_id}/export')
        self.assertEqual(exported.status_code,200,exported.text)
        self.assertIn('[资料6]',exported.text)
        self.assertIn('页码：99',exported.text.replace(' ',''))
        llm.assert_not_called()

    def test_changed_report_rejects_stale_candidate_without_adoption(self):
        saved=self.candidate()
        with fixtures.TestingSessionLocal() as db:
            job=db.get(ReportJob,self.job_id)
            changed=dict(job.result); changed['passed']=True; job.result=changed; db.commit()
        response=self.adopt(saved.id)
        self.assertEqual(response.status_code,409,response.text)
        with fixtures.TestingSessionLocal() as db:
            self.assertIsNone(db.get(ReportClaimInvestigation,saved.id).adopted_at)
            self.assertTrue(db.get(ReportJob,self.job_id).result['passed'])

    def test_contradicted_and_limit_results_are_not_labeled_supported(self):
        saved=self.candidate(verdict='contradicted',status='limit_reached')
        response=self.adopt(saved.id)
        self.assertEqual(response.status_code,200,response.text)
        checked=response.json()['checked_report']
        self.assertEqual(checked['review']['items'][0]['verdict'],'contradicted')
        self.assertFalse(checked['passed'])
        record=fixtures.client.get(f'/report-claim-investigations/{saved.id}').json()
        self.assertEqual(record['result']['status'],'limit_reached')

    def test_source_collision_rolls_back_report_and_adoption_flag(self):
        saved=self.candidate()
        with fixtures.TestingSessionLocal() as db:
            record=db.get(ReportClaimInvestigation,saved.id)
            data=dict(record.result)
            data['evidence']=[dict(x) for x in data['evidence']]
            data['evidence'][-1]['source_number']=1
            record.result=data; db.commit()
        self.assertEqual(self.adopt(saved.id).status_code,409)
        with fixtures.TestingSessionLocal() as db:
            self.assertEqual(db.get(ReportJob,self.job_id).result,self.base_report)
            self.assertIsNone(db.get(ReportClaimInvestigation,saved.id).adopted_at)

    def test_missing_and_repeated_requests_do_not_call_model(self):
        self.assertEqual(self.adopt(99999).status_code,404)

    def test_passed_recomputed_when_all_final_reviews_supported(self):
        with fixtures.TestingSessionLocal() as db:
            job=db.get(ReportJob,self.job_id)
            data=dict(job.result)
            data['review']={'task_id':self.task_id,'items':[dict(x) for x in data['review']['items']]}
            data['review']['items'][1]['verdict']='supported'
            job.result=data; db.commit()
        saved=self.candidate()
        response=self.adopt(saved.id)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['checked_report']['passed'])

    def assert_separate_review(self, verdict):
        saved = self.candidate(verdict=verdict)
        response = self.adopt(saved.id)
        self.assertEqual(response.status_code, 200, response.text)
        checked = response.json()['checked_report']
        statement = checked['report']['method_comparisons'][0]
        self.assertEqual(statement['text'], self.context.text)
        self.assertEqual(statement['source_numbers'], self.context.review.source_numbers)
        self.assertEqual(checked['review']['items'][0]['source_numbers'], [1, 3, 6])
        self.assertEqual(checked['review']['items'][0]['verdict'], verdict)
        self.assertFalse(checked['passed'])
        exported = fixtures.client.get(f'/report-jobs/{self.job_id}/export').text
        self.assertIn('[资料6]', exported)
        labels = {'contradicted': '反对证据／矛盾核查依据',
                  'partially_supported': '适用条件与限制的核查依据',
                  'insufficient_evidence': '证据缺口的核查资料'}
        self.assertIn(labels[verdict], exported)
        self.assertNotIn(self.context.text + '[资料6]', exported)

    def test_contradicted_evidence_is_separate_from_statement(self):
        self.assert_separate_review('contradicted')

    def test_partial_support_evidence_is_separate_from_statement(self):
        self.assert_separate_review('partially_supported')

    def test_insufficient_evidence_is_separate_from_statement(self):
        self.assert_separate_review('insufficient_evidence')
