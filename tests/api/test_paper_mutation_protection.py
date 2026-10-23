"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestPaperMutationProtection(PaperPreparationJobFixture, unittest.TestCase):
    """验证六个修改入口的保护，读取入口保持可用；不做真实联调。"""

    def setUp(self):
        super().setUp()
        import research_library.services.paper_pipeline as paper_pipeline
        with TestingSessionLocal() as db:
            paper_pipeline.prepare_paper_document(db, db.get(PaperDocument, 12))
        self.embed.reset_mock()

    def _snapshot_preparation(self):
        from sqlalchemy import select
        models = (Paper, PaperDocument, self.Page, PaperChunk, PaperChunkEmbedding, self.Job)
        with TestingSessionLocal() as db:
            rows = {model.__tablename__: [
                {c.name: getattr(row, c.name) for c in model.__table__.columns}
                for row in db.scalars(select(model))] for model in models}
        return json.dumps(rows, sort_keys=True, default=str), self.pdf_path.read_bytes()

    def test_all_six_mutations_block_queued_and_running_jobs_without_changes(self):
        self._seed()
        operations = [
            ("delete", "/papers/9"), ("delete", "/papers/9/document"),
            ("post", "/papers/9/document/extract"), ("post", "/papers/9/chunks"),
            ("post", "/papers/9/chunks/embeddings"), ("post", "/papers/9/prepare"),
        ]
        for status in ("queued", "running"):
            with TestingSessionLocal() as db:
                db.get(self.Job, 3).status = status
                db.commit()
            before = self._snapshot_preparation()
            for method, url in operations:
                with self.subTest(status=status, url=url):
                    response = getattr(client, method)(url)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn("作业3", response.json()["detail"])
                    self.assertEqual(self._snapshot_preparation(), before)
        self.embed.assert_not_called()
        self.publish.assert_not_called()

    def test_reads_remain_available_during_preparation(self):
        self._seed(status="running")
        for url in ("/papers/9", "/papers/9/document", "/papers/9/pages",
                    "/papers/9/chunks", "/paper-preparation-jobs/3"):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.text)
        self.publish.assert_not_called()

    def test_other_paper_prepare_is_not_blocked(self):
        self._seed(status="running")
        response = client.post("/papers/3/prepare")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["paper_id"], 3)
        self.assertEqual(self._get()["status"], "running")

    def test_terminal_jobs_allow_synchronous_prepare(self):
        self._seed()
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                with TestingSessionLocal() as db:
                    db.get(self.Job, 3).status = status
                    db.commit()
                response = client.post("/papers/9/prepare")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["indexed_count"], 0)
                self.assertEqual(self._get()["status"], status)

    def test_terminal_jobs_allow_pdf_delete_and_preserve_job_history(self):
        self._seed(status="succeeded")
        response = client.delete("/papers/9/document")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.pdf_path.exists())
        self.assertEqual(self._get()["status"], "succeeded")
        with TestingSessionLocal() as db:
            self.assertIsNone(db.get(PaperDocument, 12))

    def test_terminal_jobs_allow_paper_delete_and_preserve_job_history(self):
        self._seed(status="failed")
        response = client.delete("/papers/9")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._get()["status"], "failed")
        with TestingSessionLocal() as db:
            self.assertIsNone(db.get(Paper, 9))

    def test_existing_missing_resource_errors_are_preserved(self):
        for url in ("/papers/999999/prepare", "/papers/11/prepare"):
            with self.subTest(url=url):
                response = client.post(url)
                self.assertEqual(response.status_code, 404, response.text)

    def test_lock_stays_held_across_stage_commits_and_blocks_job_submission(self):
        import research_library.services.paper_pipeline as paper_pipeline
        actual_prepare = paper_pipeline.prepare_paper_document
        observed = []
        def prepare_with_checks(*, db, document, chunk_size, overlap):
            def at_stage(stage):
                db.commit()
                submission = client.post(self.url)
                deletion = client.delete("/papers/9/document")
                observed.append((stage, submission.status_code, deletion.status_code))
                self.assertEqual(self._count(), 0)
                self.assertTrue(self.pdf_path.exists())
            return actual_prepare(db=db, document=document, chunk_size=chunk_size,
                                  overlap=overlap, on_stage=at_stage)
        with patch('research_library.api.papers.paper_pipeline.prepare_paper_document', side_effect=prepare_with_checks):
            response = client.post("/papers/9/prepare")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(observed, [(stage, 409, 409) for stage in ("extracting", "chunking", "indexing")])
        self.publish.assert_not_called()
        # 原操作结束后，同一篇论文可以取得锁并提交。
        self.assertEqual(client.post(self.url).status_code, 202)

    def test_lock_is_released_after_business_exception(self):
        with patch('research_library.api.papers.paper_pipeline.prepare_paper_document', side_effect=ValueError("模拟正文错误")):
            response = client.post("/papers/9/prepare")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(client.post(self.url).status_code, 202)

    def test_sqlite_lock_blocks_same_paper_across_sessions_but_allows_other_paper(self):
        from research_library.services.paper_operation_guard import paper_operation_lock, PaperOperationBusyError
        with TestingSessionLocal() as first, TestingSessionLocal() as second:
            with paper_operation_lock(first, 9):
                first.commit()
                with self.assertRaises(PaperOperationBusyError):
                    with paper_operation_lock(second, 9):
                        self.fail("同一论文不能同时取得锁")
                with paper_operation_lock(second, 3):
                    pass
            with paper_operation_lock(second, 9):
                pass

    def test_sqlite_thread_conflict_returns_409_and_release_restores_submission(self):
        from threading import Event, Thread
        from research_library.services.paper_operation_guard import paper_operation_lock
        entered, release = Event(), Event()
        errors = []
        def holder():
            try:
                with TestingSessionLocal() as db, paper_operation_lock(db, 9):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("测试锁未及时释放")
            except Exception as error:
                errors.append(error)
                entered.set()
        thread = Thread(target=holder)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(client.post(self.url).status_code, 409)
            self.assertEqual(client.delete("/papers/9/document").status_code, 409)
            self.assertEqual(self._count(), 0)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(client.post(self.url).status_code, 202)

    def test_background_worker_still_runs_without_using_mutation_entry(self):
        self._seed()
        self.worker.run_preparation_job.run(3)
        self.assertEqual(self._get()["status"], "succeeded")
