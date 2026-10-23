"""报告历史仅查询隔离数据库，不调用模型或实际消息队列。"""
import unittest
from unittest.mock import patch
from sqlalchemy import inspect
import tests.fixtures.main as fixtures
import research_library.repositories.report_job_crud as report_job_crud
from research_library.db.model import ReportJob, ResearchTask


class TestReportJobHistory(fixtures.ComparisonDatabaseFixture, unittest.TestCase):
    def add_jobs(self):
        with fixtures.TestingSessionLocal() as db:
            other = db.query(ResearchTask).filter(ResearchTask.id != self.task_id).one()
            db.add_all([
                ReportJob(id=1, task_id=self.task_id, status='failed', stage='failed',
                          analysis_snapshot={'private': 'input'}, error_message='模型超时'),
                ReportJob(id=2, task_id=other.id, status='succeeded', stage='finished',
                          analysis_snapshot={}, result={'private': 'other task'}),
                ReportJob(id=3, task_id=self.task_id, status='succeeded', stage='finished',
                          analysis_snapshot={}, result={'private': 'large report'}),
            ])
            db.commit()

    def get_history(self, query=''):
        return fixtures.client.get(f'/research-tasks/{self.task_id}/report-jobs{query}')

    def test_history_is_scoped_sorted_and_does_not_dispatch(self):
        self.add_jobs()
        with patch('research_library.api.reports.run_report_job.apply_async') as dispatch:
            response = self.get_history()
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual([row['id'] for row in rows], [3, 1])
        self.assertEqual(rows[1]['error_message'], '模型超时')
        for row in rows:
            self.assertEqual(row['task_id'], self.task_id)
            self.assertNotIn('result', row)
            self.assertNotIn('analysis_snapshot', row)
        dispatch.assert_not_called()

    def test_pagination_and_empty_final_page(self):
        self.add_jobs()
        self.assertEqual([x['id'] for x in self.get_history('?limit=1&offset=1').json()], [1])
        self.assertEqual(self.get_history('?limit=1&offset=2').json(), [])

    def test_empty_task_and_missing_task_are_distinct(self):
        self.assertEqual(self.get_history().json(), [])
        response = fixtures.client.get('/research-tasks/999999/report-jobs')
        self.assertEqual(response.status_code, 404)

    def test_invalid_pagination_returns_422(self):
        for query in ('?limit=0', '?limit=101', '?offset=-1', '?limit=abc'):
            with self.subTest(query=query):
                self.assertEqual(self.get_history(query).status_code, 422)

    def test_summary_query_does_not_load_large_json_columns(self):
        self.add_jobs()
        with fixtures.TestingSessionLocal() as db:
            rows = report_job_crud.get_report_jobs_by_task(db, self.task_id)
            for row in rows:
                self.assertTrue({'result', 'analysis_snapshot'} <= inspect(row).unloaded)

    def test_post_still_submits_instead_of_returning_history(self):
        with patch('research_library.api.reports.run_report_job.apply_async') as dispatch:
            response = fixtures.client.post(f'/research-tasks/{self.task_id}/report-jobs')
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()['status'], 'queued')
        dispatch.assert_called_once()
