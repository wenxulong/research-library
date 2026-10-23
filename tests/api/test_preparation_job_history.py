"""隔离数据库验证准备历史；不操作真实PDF或消息队列。"""
import unittest
from unittest.mock import patch
from sqlalchemy import inspect
import tests.fixtures.main as fixtures
import research_library.repositories.paper_preparation_job_crud as paper_preparation_job_crud
from research_library.db.model import PaperPreparationJob, PaperDocument


class TestPreparationJobHistory(fixtures.ComparisonDatabaseFixture, unittest.TestCase):
    def add_jobs(self):
        with fixtures.TestingSessionLocal() as db:
            docs = [db.query(PaperDocument).filter_by(paper_id=pid).one() for pid in self.paper_ids]
            db.add_all([
                PaperPreparationJob(id=1, paper_id=self.paper_ids[0], document_id=docs[0].id,
                                    status='failed', stage='failed', error_message='提取失败'),
                PaperPreparationJob(id=2, paper_id=self.paper_ids[1], document_id=docs[1].id,
                                    status='running', stage='extracting'),
                PaperPreparationJob(id=3, paper_id=self.paper_ids[0], document_id=docs[0].id,
                                    status='succeeded', stage='finished', result={'private': 'not summary'}),
            ])
            db.commit()

    def get_history(self, query=''):
        return fixtures.client.get(f'/papers/{self.paper_ids[0]}/preparation-jobs{query}')

    def test_scoped_sorted_summary_without_dispatch(self):
        self.add_jobs()
        with patch('research_library.api.papers.run_preparation_job.apply_async') as dispatch:
            response = self.get_history()
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual([r['id'] for r in rows], [3, 1])
        self.assertEqual(rows[1]['error_message'], '提取失败')
        for row in rows:
            self.assertEqual(row['paper_id'], self.paper_ids[0])
            self.assertIn('document_id', row)
            self.assertNotIn('result', row)
        dispatch.assert_not_called()

    def test_pagination(self):
        self.add_jobs()
        self.assertEqual([r['id'] for r in self.get_history('?limit=1&offset=1').json()], [1])
        self.assertEqual(self.get_history('?limit=1&offset=2').json(), [])

    def test_empty_paper_vs_missing_paper(self):
        response = self.get_history()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(fixtures.client.get('/papers/999999/preparation-jobs').status_code, 404)

    def test_invalid_pagination(self):
        for query in ('?limit=0', '?limit=101', '?offset=-1', '?limit=abc'):
            with self.subTest(query=query):
                self.assertEqual(self.get_history(query).status_code, 422)

    def test_large_result_not_loaded(self):
        self.add_jobs()
        with fixtures.TestingSessionLocal() as db:
            for job in paper_preparation_job_crud.get_preparation_jobs_by_paper(db, self.paper_ids[0]):
                self.assertIn('result', inspect(job).unloaded)

    def test_existing_post_and_detail_routes_unchanged(self):
        with patch('research_library.api.papers.run_preparation_job.apply_async') as dispatch:
            response = fixtures.client.post(f'/papers/{self.paper_ids[0]}/preparation-jobs')
        self.assertEqual(response.status_code, 202, response.text)
        dispatch.assert_called_once()
        job_id = response.json()['id']
        detail = fixtures.client.get(f'/paper-preparation-jobs/{job_id}')
        self.assertEqual(detail.status_code, 200)
        self.assertIn('result', detail.json())
