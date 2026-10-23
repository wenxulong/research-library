"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestReportJobSubmission(ComparisonDatabaseFixture, unittest.TestCase):
    """作业提交/查询和重复控制；使用隔离数据库并禁止真实队列投递。"""

    def setUp(self):
        super().setUp()
        from research_library.db.model import ReportJob
        self.Job = ReportJob
        self.url = f"/research-tasks/{self.task_id}/report-jobs"
        self.publish = patch('research_library.api.reports.run_report_job.apply_async').start()
        self.addCleanup(patch.stopall)

    def _jobs(self):
        from sqlalchemy import select
        with TestingSessionLocal() as db:
            return list(db.scalars(select(self.Job).order_by(self.Job.id)))

    def _seed_job(self, status, task_id=None):
        with TestingSessionLocal() as db:
            job = self.Job(task_id=self.task_id if task_id is None else task_id,
                           status=status, stage="waiting", analysis_snapshot=self.analysis.model_dump(mode="json"))
            db.add(job); db.commit(); db.refresh(job)
            return job.id

    def test_first_submission_returns_202_and_publishes_only_id(self):
        before = self._snapshot()
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["task_id"], self.task_id)
        self.assertIsNone(body["result"])
        self.assertNotIn("analysis_snapshot", body)
        self.publish.assert_called_once_with(args=[body["id"]], retry=False)
        jobs = self._jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].analysis_snapshot, self.analysis.model_dump(mode="json"))
        self.assertEqual(self._snapshot(), before)

    def test_repeated_submission_does_not_create_or_publish_second_job(self):
        first = client.post(self.url)
        second = client.post(self.url)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(second.status_code, 409, second.text)
        self.assertIn(str(first.json()["id"]), second.json()["detail"])
        self.assertEqual(len(self._jobs()), 1)
        self.assertEqual(self.publish.call_count, 1)

    def test_queued_and_running_jobs_both_block_submission(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                with TestingSessionLocal() as db:
                    db.query(self.Job).delete(); db.commit()
                existing = self._seed_job(status)
                self.publish.reset_mock()
                response = client.post(self.url)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn(str(existing), response.json()["detail"])
                self.assertEqual(len(self._jobs()), 1)
                self.publish.assert_not_called()

    def test_terminal_jobs_allow_new_submission_and_preserve_history(self):
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                with TestingSessionLocal() as db:
                    db.query(self.Job).delete(); db.commit()
                existing = self._seed_job(status)
                self.publish.reset_mock()
                response = client.post(self.url)
                self.assertEqual(response.status_code, 202, response.text)
                jobs = self._jobs()
                self.assertEqual(len(jobs), 2)
                self.assertEqual(jobs[0].id, existing)
                self.assertEqual(jobs[0].status, status)
                self.assertNotEqual(response.json()["id"], existing)
                self.publish.assert_called_once()

    def test_other_task_active_job_does_not_block_this_task(self):
        with TestingSessionLocal() as db:
            other = db.query(ResearchTask).filter(ResearchTask.id != self.task_id).one()
            other_id = other.id
        self._seed_job("running", other_id)
        self.assertEqual(client.post(self.url).status_code, 202)
        self.assertEqual(len(self._jobs()), 2)

    def test_missing_or_invalid_analysis_never_publishes(self):
        self.assertEqual(client.post('/research-tasks/999999/report-jobs').status_code, 404)
        for saved in (None, "不是JSON", self.analysis.model_copy(update={"question": "旧问题"}).model_dump_json()):
            with self.subTest(saved=saved):
                with TestingSessionLocal() as db:
                    db.get(ResearchTask, self.task_id).result = saved
                    db.commit()
                response = client.post(self.url)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self._jobs(), [])
        self.publish.assert_not_called()

    def test_invalid_raw_evidence_rejected_before_job_creation(self):
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id)); db.commit()
        response = client.post(self.url)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self._jobs(), [])
        self.publish.assert_not_called()

    def test_query_returns_current_state_without_publishing(self):
        job_id = self._seed_job("queued")
        for status, stage in (("queued", "waiting"), ("running", "generating"), ("failed", "failed")):
            with TestingSessionLocal() as db:
                job = db.get(self.Job, job_id)
                job.status, job.stage = status, stage
                job.error_message = "模拟连接失败" if status == "failed" else None
                db.commit()
            response = client.get(f"/report-jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], status)
            self.assertEqual(response.json()["stage"], stage)
        self.assertEqual(client.get('/report-jobs/999999').status_code, 404)
        self.publish.assert_not_called()

    def test_publish_failure_records_error_and_next_submission_can_retry(self):
        from kombu.exceptions import OperationalError
        self.publish.side_effect = OperationalError("模拟Redis不可用")
        response = client.post(self.url)
        self.assertEqual(response.status_code, 503, response.text)
        job_id = response.json()["detail"]["job_id"]
        failed = client.get(f"/report-jobs/{job_id}").json()
        self.assertEqual(failed["status"], "failed")
        self.assertIsNotNone(failed["finished_at"])
        self.assertIn("Redis", failed["error_message"])
        self.publish.side_effect = None
        retry = client.post(self.url)
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertEqual(len(self._jobs()), 2)

    def test_ambiguous_publish_failure_does_not_overwrite_running_job(self):
        from kombu.exceptions import OperationalError
        def received_then_failed(*, args, retry):
            with TestingSessionLocal() as db:
                job = db.get(self.Job, args[0])
                job.status, job.stage = "running", "generating"
                db.commit()
            raise OperationalError("消息已收到，但响应连接断开")
        self.publish.side_effect = received_then_failed
        response = client.post(self.url)
        self.assertEqual(response.status_code, 503, response.text)
        job_id = response.json()["detail"]["job_id"]
        queried = client.get(f"/report-jobs/{job_id}").json()
        self.assertEqual(queried["status"], "running")
        self.assertIsNone(queried["error_message"])
        self.assertEqual(client.post(self.url).status_code, 409)

    def test_rejected_creation_rolls_back_transaction(self):
        from research_library.repositories.report_job_crud import create_report_job
        self._seed_job("queued")
        with TestingSessionLocal() as db:
            with self.assertRaisesRegex(ValueError, "尚未结束"):
                create_report_job(db, self.task_id)
            self.assertFalse(db.in_transaction())

    def test_job_claim_is_once_only(self):
        from research_library.repositories.report_job_crud import claim_report_job
        job_id = self._seed_job("queued")
        with TestingSessionLocal() as db:
            first = claim_report_job(db, job_id)
            self.assertEqual(first.status, "running")
            self.assertIsNotNone(first.started_at)
        with TestingSessionLocal() as db:
            self.assertIsNone(claim_report_job(db, job_id))
            self.assertIsNone(claim_report_job(db, 999999))
