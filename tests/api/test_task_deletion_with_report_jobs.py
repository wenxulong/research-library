"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestTaskDeletionWithReportJobs(unittest.TestCase):
    """开启SQLite真实外键约束，验证后台作业和任务的删除规则。"""

    def setUp(self):
        from sqlalchemy import event
        from research_library.db.model import ReportJob
        self.Job = ReportJob
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        def get_test_db():
            with self.sessions() as db:
                yield db
        self.enterContext(patch.dict(app.dependency_overrides, {get_db: get_test_db}))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.task = self.client.post("/research-tasks", json={"title": "删除规则测试", "question": "测试问题"}).json()

    def _job(self, status, task_id=None):
        with self.sessions() as db:
            job = self.Job(task_id=self.task["id"] if task_id is None else task_id,
                           status=status, stage="test", analysis_snapshot={})
            db.add(job)
            db.commit()
            return job.id

    def test_active_jobs_reject_deletion_without_changing_records(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                job_id = self._job(status)
                response = self.client.delete(f'/research-tasks/{self.task["id"]}')
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn(str(job_id), response.json()["detail"])
                with self.sessions() as db:
                    self.assertIsNotNone(db.get(ResearchTask, self.task["id"]))
                    self.assertEqual(db.get(self.Job, job_id).status, status)
                    db.delete(db.get(self.Job, job_id))
                    db.commit()

    def test_terminal_jobs_are_deleted_before_parent_with_foreign_keys_enabled(self):
        jobs = [self._job("succeeded"), self._job("failed")]
        response = self.client.delete(f'/research-tasks/{self.task["id"]}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["deleted_task"], self.task)
        with self.sessions() as db:
            self.assertIsNone(db.get(ResearchTask, self.task["id"]))
            for job_id in jobs:
                self.assertIsNone(db.get(self.Job, job_id))

    def test_active_job_prevents_partial_deletion_of_terminal_history(self):
        ended = self._job("failed")
        active = self._job("running")
        response = self.client.delete(f'/research-tasks/{self.task["id"]}')
        self.assertEqual(response.status_code, 409)
        with self.sessions() as db:
            self.assertIsNotNone(db.get(self.Job, ended))
            self.assertIsNotNone(db.get(self.Job, active))

    def test_unknown_job_status_is_not_silently_deleted(self):
        job_id = self._job("unknown")
        self.assertEqual(self.client.delete(f'/research-tasks/{self.task["id"]}').status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.get(self.Job, job_id).status, "unknown")

    def test_deleting_task_preserves_paper_and_other_task_jobs(self):
        from research_library.db.model import ResearchTaskPaper
        other = self.client.post("/research-tasks", json={"title": "其他任务", "question": "保留"}).json()
        other_job = self._job("running", other["id"])
        with self.sessions() as db:
            paper = Paper(title="共享论文", author="作者", year=2026, notes="")
            db.add(paper)
            db.flush()
            paper_id = paper.id
            db.add(ResearchTaskPaper(task_id=self.task["id"], paper_id=paper_id, note=""))
            db.commit()
        self._job("failed")
        self.assertEqual(self.client.delete(f'/research-tasks/{self.task["id"]}').status_code, 200)
        with self.sessions() as db:
            self.assertIsNotNone(db.get(Paper, paper_id))
            self.assertIsNotNone(db.get(ResearchTask, other["id"]))
            self.assertEqual(db.get(self.Job, other_job).status, "running")
            self.assertEqual(db.query(ResearchTaskPaper).filter_by(task_id=self.task["id"]).count(), 0)

    def test_commit_failure_rolls_back_task_and_job_deletion(self):
        from research_library.repositories.task_crud import delete_task
        job_id = self._job("failed")
        with self.sessions() as db:
            task = db.get(ResearchTask, self.task["id"])
            with patch.object(db, "commit", side_effect=RuntimeError("模拟提交失败")):
                with self.assertRaisesRegex(RuntimeError, "模拟提交失败"):
                    delete_task(db, task)
            self.assertFalse(db.in_transaction())
        with self.sessions() as db:
            self.assertIsNotNone(db.get(ResearchTask, self.task["id"]))
            self.assertIsNotNone(db.get(self.Job, job_id))

    def test_rejection_releases_transaction(self):
        from research_library.repositories.task_crud import delete_task
        self._job("queued")
        with self.sessions() as db:
            with self.assertRaises(ValueError):
                delete_task(db, db.get(ResearchTask, self.task["id"]))
            self.assertFalse(db.in_transaction())
