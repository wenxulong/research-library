"""查证接口使用隔离数据库，模型与工具返回全部模拟。"""
import unittest
from unittest.mock import patch

import tests.fixtures.main as fixtures
import research_library.services.report_claim_investigation as service
from research_library.db.model import ReportJob, ReportClaimInvestigation
from tests.agents.test_evidence_investigation import InvestigationFixture


class TestClaimInvestigationAPI(InvestigationFixture, unittest.TestCase):
    @property
    def url(self):
        return f'/report-jobs/{self.job_id}/claims/{self.context.claim_id}/investigations'

    def create(self):
        with patch.object(service, 'run_evidence_react', return_value=self.react_result()), \
             patch.object(service, 'review_claim', return_value=self.context.review):
            response = fixtures.client.post(self.url, json={'instruction':'核对实验条件'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_create_persists_candidate_and_get_history_never_regenerates(self):
        saved = self.create()
        with patch.object(service, 'run_evidence_react') as generate:
            read = fixtures.client.get(f'/report-claim-investigations/{saved["id"]}')
            history = fixtures.client.get(self.url)
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json(), saved)
        self.assertEqual(history.json(), [saved])
        generate.assert_not_called()
        with fixtures.TestingSessionLocal() as db:
            self.assertEqual(db.get(ReportJob, self.job_id).result, self.base_report)

    def test_history_is_sorted_scoped_and_limited(self):
        first = self.create()
        second = self.create()
        self.assertEqual([x['id'] for x in fixtures.client.get(self.url).json()], [second['id'], first['id']])
        self.assertEqual(len(fixtures.client.get(self.url+'?limit=1').json()), 1)
        other = self.url.replace(self.context.claim_id, 'another-claim')
        self.assertEqual(fixtures.client.get(other).json(), [])
        self.assertEqual(fixtures.client.get(self.url+'?limit=0').status_code, 422)

    def test_bad_input_and_missing_records(self):
        with patch.object(service, 'run_evidence_react') as generate:
            for instruction in ('', '   ', 'x'*2001):
                self.assertEqual(fixtures.client.post(self.url, json={'instruction':instruction}).status_code, 422)
            self.assertEqual(fixtures.client.post(self.url, json={}).status_code, 422)
            self.assertEqual(fixtures.client.post(self.url.replace(self.context.claim_id,'missing'), json={'instruction':'查证'}).status_code, 404)
            self.assertEqual(fixtures.client.get('/report-claim-investigations/99999').status_code, 404)
            self.assertEqual(fixtures.client.get('/report-jobs/99999/claims/a/investigations').status_code, 404)
            generate.assert_not_called()

    def test_model_failure_returns_502_without_saved_record(self):
        with patch.object(service, 'run_evidence_react', side_effect=RuntimeError('模拟连接失败')):
            response = fixtures.client.post(self.url, json={'instruction':'查证'})
        self.assertEqual(response.status_code, 502)
        with fixtures.TestingSessionLocal() as db:
            self.assertEqual(db.query(ReportClaimInvestigation).count(), 0)

    def test_unfinished_job_returns_409(self):
        with fixtures.TestingSessionLocal() as db:
            db.get(ReportJob, self.job_id).status = 'running'
            db.commit()
        self.assertEqual(fixtures.client.post(self.url, json={'instruction':'查证'}).status_code, 409)
