"""隔离数据库复现跨请求变化；不调用模型、不向Redis发送消息。"""
import json
import unittest
from unittest.mock import patch

import tests.fixtures.main as fixtures
import research_library.repositories.task_crud as task_crud
from research_library.db.database import Base
from research_library.db.model import ResearchTask, ReportJob, PaperPreparationJob, PaperChunk
from research_library.schemas import ResearchTaskReviewResponse, ReviewItemResponse


class TestProjectConsistency(fixtures.ComparisonDatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = fixtures.client
        self.session = fixtures.TestingSessionLocal

    def submit(self):
        with patch('research_library.api.reports.run_report_job.apply_async'):
            return self.client.post(f'/research-tasks/{self.task_id}/report-jobs')

    def set_job_state(self, state):
        with self.session() as db:
            job = db.query(ReportJob).one()
            job.status = state
            db.commit()

    def test_old_review_cannot_attach_to_new_analysis(self):
        new_analysis = self.analysis.model_copy(update={'summary': '并发保存的新分析'})
        def model(**kwargs):
            with self.session() as db:
                task_crud.save_task_analysis(db, db.get(ResearchTask, self.task_id), new_analysis)
            return ResearchTaskReviewResponse(task_id=self.task_id, items=[ReviewItemResponse(
                field='summary', claim=kwargs['analysis'].summary, verdict='supported', reason='测试', source_numbers=[1])])
        with patch('research_library.api.tasks.review_agent.review_research_task', side_effect=model):
            response = self.client.post(f'/research-tasks/{self.task_id}/review')
        self.assertEqual(response.status_code, 409, response.text)
        with self.session() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual(json.loads(task.result)['summary'], '并发保存的新分析')
            self.assertIsNone(task.review_result)

    def test_unchanged_analysis_can_save_review(self):
        result = ResearchTaskReviewResponse(task_id=self.task_id, items=[])
        with patch('research_library.api.tasks.review_agent.review_research_task', return_value=result):
            response = self.client.post(f'/research-tasks/{self.task_id}/review')
        self.assertEqual(response.status_code, 200, response.text)

    def test_analysis_save_rejects_changed_input_without_overwrite(self):
        with self.session() as db:
            task = db.get(ResearchTask, self.task_id)
            before = task_crud.task_input_snapshot(db, task)
            with self.session() as other:
                latest = other.get(ResearchTask, self.task_id)
                latest.result = self.analysis.model_copy(update={'summary': '新的采纳结果'}).model_dump_json()
                other.commit()
            with self.assertRaises(task_crud.TaskStateConflictError):
                task_crud.save_task_analysis(db, task, self.analysis, expected_input=before)
        with self.session() as db:
            self.assertEqual(json.loads(db.get(ResearchTask, self.task_id).result)['summary'], '新的采纳结果')

    def test_running_report_blocks_every_destructive_paper_route(self):
        self.assertEqual(self.submit().status_code, 202)
        for state in ('queued', 'running'):
            self.set_job_state(state)
            for route in ('document', 'document/extract', 'chunks', 'chunks/embeddings', 'prepare'):
                with self.subTest(state=state, route=route):
                    method = self.client.delete if route == 'document' else self.client.post
                    response = method(f'/papers/{self.paper_ids[0]}/{route}')
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn('报告作业', response.json()['detail'])
            with patch('research_library.api.papers.delete_pdf') as remove_file:
                response = self.client.delete(f'/papers/{self.paper_ids[0]}')
                self.assertEqual(response.status_code, 409, response.text)
                remove_file.assert_not_called()
        with self.session() as db:
            self.assertIsNotNone(db.get(PaperChunk, self.chunk_id))

    def test_report_blocks_preparation_job_submission(self):
        self.assertEqual(self.submit().status_code, 202)
        with patch('research_library.api.papers.run_preparation_job.apply_async') as publish:
            response = self.client.post(f'/papers/{self.paper_ids[0]}/preparation-jobs')
        self.assertEqual(response.status_code, 409, response.text)
        publish.assert_not_called()
        with self.session() as db:
            self.assertEqual(db.query(PaperPreparationJob).count(), 0)

    def test_report_blocks_link_changes(self):
        self.assertEqual(self.submit().status_code, 202)
        response = self.client.delete(f'/research-tasks/{self.task_id}/papers/{self.paper_ids[1]}')
        self.assertEqual(response.status_code, 409, response.text)
        response = self.client.post('/papers', json={'title':'补证论文','author':'测试','year':2026,'notes':''})
        paper_id = response.json()['id']
        response = self.client.post(f'/research-tasks/{self.task_id}/papers/{paper_id}', json={'note':''})
        self.assertEqual(response.status_code, 409, response.text)
        with self.session() as db:
            self.assertEqual(len(task_crud.get_task_paper_links(db, self.task_id)), 2)

    def test_terminal_report_releases_paper_protection(self):
        self.assertEqual(self.submit().status_code, 202)
        self.set_job_state('failed')
        with patch('research_library.api.papers.delete_pdf'):
            response = self.client.delete(f'/papers/{self.paper_ids[0]}/document')
        self.assertEqual(response.status_code, 200, response.text)

    def test_unlink_preserves_analysis_but_makes_it_stale(self):
        response = self.client.delete(f'/research-tasks/{self.task_id}/papers/{self.paper_ids[1]}')
        self.assertEqual(response.status_code, 200, response.text)
        with self.session() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual(task.status, 'stale')
            self.assertEqual(task.result, self.analysis.model_dump_json())
            self.assertIsNone(task.review_result)
        self.assertEqual(self.client.get(f'/research-tasks/{self.task_id}/analysis').status_code, 409)
        with patch('research_library.api.reports.run_report_job.apply_async') as publish:
            response = self.client.post(f'/research-tasks/{self.task_id}/report-jobs')
        self.assertEqual(response.status_code, 409, response.text)
        publish.assert_not_called()

    def test_legacy_analysis_of_unlinked_paper_cannot_submit_report(self):
        from research_library.db.model import ResearchTaskPaper
        with self.session() as db:
            db.query(ResearchTaskPaper).filter_by(task_id=self.task_id, paper_id=self.paper_ids[1]).delete()
            db.commit()
        self.assertEqual(self.submit().status_code, 409)

    def test_preparation_already_queued_blocks_report_submission(self):
        from research_library.db.model import PaperDocument
        with self.session() as db:
            document = db.query(PaperDocument).filter_by(paper_id=self.paper_ids[0]).one()
            db.add(PaperPreparationJob(paper_id=self.paper_ids[0], document_id=document.id,
                                      status='queued', stage='waiting'))
            db.commit()
        self.assertEqual(self.submit().status_code, 409)
        with self.session() as db:
            self.assertEqual(db.query(ReportJob).count(), 0)

    def test_failed_report_validation_releases_named_paper_locks(self):
        import research_library.repositories.report_job_crud as report_job_crud
        from research_library.services.paper_operation_guard import paper_operation_lock
        with self.session() as db:
            with patch('research_library.repositories.report_job_crud.build_comparison_context', side_effect=ValueError('测试失败')):
                with self.assertRaises(ValueError):
                    report_job_crud.create_report_job(db, self.task_id)
            for paper_id in self.paper_ids:
                with paper_operation_lock(db, paper_id):
                    pass

    def test_addition_marks_old_analysis_stale(self):
        paper_id = self.client.post('/papers', json={'title':'新论文','author':'测试','year':2026,'notes':''}).json()['id']
        response = self.client.post(f'/research-tasks/{self.task_id}/papers/{paper_id}', json={'note':''})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.submit().status_code, 409)

    def test_stale_analysis_cannot_be_reviewed(self):
        self.client.delete(f'/research-tasks/{self.task_id}/papers/{self.paper_ids[1]}')
        with patch('research_library.api.tasks.review_agent.review_research_task') as model:
            response = self.client.post(f'/research-tasks/{self.task_id}/review')
        self.assertEqual(response.status_code, 409)
        model.assert_not_called()

    def test_new_analysis_resolves_stale_state(self):
        self.client.delete(f'/research-tasks/{self.task_id}/papers/{self.paper_ids[1]}')
        with self.session() as db:
            task = db.get(ResearchTask, self.task_id)
            task_crud.save_task_analysis(db, task, self.analysis)
            self.assertEqual(task.status, 'completed')

    def test_report_protects_uncited_paper_available_for_supplement(self):
        from research_library.db.model import ResearchTaskPaper, Paper
        with self.session() as db:
            paper = Paper(title='未引用的补证论文', author='测试', year=2026, notes='')
            db.add(paper)
            db.flush()
            paper_id = paper.id
            db.add(ResearchTaskPaper(task_id=self.task_id, paper_id=paper_id, note=''))
            db.commit()
        self.assertEqual(self.submit().status_code, 202)
        response = self.client.delete(f'/papers/{paper_id}')
        self.assertEqual(response.status_code, 409, response.text)

    def test_analysis_save_rejects_membership_changes(self):
        with self.session() as db:
            task = db.get(ResearchTask, self.task_id)
            before = task_crud.task_input_snapshot(db, task)
            self.client.delete(f'/research-tasks/{self.task_id}/papers/{self.paper_ids[1]}')
            with self.assertRaises(task_crud.TaskStateConflictError):
                task_crud.save_task_analysis(db, task, self.analysis, expected_input=before)
        with self.session() as db:
            self.assertEqual(db.get(ResearchTask, self.task_id).status, 'stale')

    def test_stale_analysis_cannot_be_restored_from_history(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        with self.session() as db:
            version = ResearchTaskAnalysisVersion(task_id=self.task_id,
                previous_analysis=self.analysis.model_dump_json(),
                updated_analysis=self.analysis.model_dump_json(), reason='测试历史')
            db.add(version)
            db.commit()
            version_id = version.id
        self.client.delete(f'/research-tasks/{self.task_id}/papers/{self.paper_ids[1]}')
        response = self.client.post(f'/research-tasks/{self.task_id}/analysis/versions/{version_id}/restore',
                                    json={'base_analysis': self.analysis.model_dump(mode='json')})
        self.assertEqual(response.status_code, 409, response.text)



if __name__ == '__main__':
    unittest.main()
