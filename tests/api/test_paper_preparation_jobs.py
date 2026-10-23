"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestPaperPreparationJobs(PaperPreparationJobFixture, unittest.TestCase):
    """验证作业提交、领取、执行、失败记录及数据复用。"""

    def test_submit_creates_snapshot_and_publishes_only_job_id(self):
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertNotEqual(job["id"], job["paper_id"])
        self.assertEqual((job["paper_id"], job["document_id"], job["status"], job["stage"]),
                         (9, 12, "queued", "waiting"))
        self.assertIsNone(job["result"])
        self.assertIsNone(job["started_at"])
        self.publish.assert_called_once_with(args=[job["id"]], retry=False)
        self.assertEqual(self._count(), 1)
        self.embed.assert_not_called()

    def test_queued_and_running_jobs_block_duplicate_submission(self):
        self._seed()
        for status in ("queued", "running"):
            with self.subTest(status=status):
                with TestingSessionLocal() as db:
                    db.get(self.Job, 3).status = status
                    db.commit()
                response = client.post(self.url)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("3", response.json()["detail"])
                self.assertEqual(self._count(), 1)
        self.publish.assert_not_called()

    def test_terminal_jobs_allow_new_submission_and_keep_history(self):
        self._seed(status="failed")
        self._seed(job_id=4, status="succeeded")
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self._count(), 3)
        self.assertEqual(self._get(3)["status"], "failed")
        self.assertEqual(self._get(4)["status"], "succeeded")

    def test_other_paper_active_job_does_not_block_submission(self):
        self._seed(job_id=9, paper_id=3, document_id=13)
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self._get(9)["status"], "queued")
        self.assertEqual(self._count(), 2)

    def test_missing_paper_or_pdf_returns_404_without_job_or_message(self):
        for paper_id in (999999, 11):
            with self.subTest(paper_id=paper_id):
                response = client.post(f"/papers/{paper_id}/preparation-jobs")
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(self._count(), 0)
        self.publish.assert_not_called()

    def test_query_does_not_publish_and_missing_job_returns_404(self):
        self._seed()
        self.assertEqual(self._get()["status"], "queued")
        self.assertEqual(client.get('/paper-preparation-jobs/999999').status_code, 404)
        self.publish.assert_not_called()
        self.embed.assert_not_called()

    def test_claim_uses_job_id_not_paper_id_and_leaves_other_job_untouched(self):
        # 编号交叉：作业3属于论文9，作业9属于论文3，不能混淆。
        self._seed()
        self._seed(job_id=9, paper_id=3, document_id=13)
        with TestingSessionLocal() as db:
            claimed = self.jobs.claim_preparation_job(db, 3)
            self.assertIsNotNone(claimed)
            self.assertEqual((claimed.id, claimed.paper_id), (3, 9))
            self.assertEqual((claimed.status, claimed.stage), ("running", "extracting"))
            self.assertIsNotNone(claimed.started_at)
        self.assertEqual(self._get(9)["status"], "queued")
        with TestingSessionLocal() as db:
            self.assertIsNone(self.jobs.claim_preparation_job(db, 3))
            self.assertIsNone(self.jobs.claim_preparation_job(db, 999999))

    def test_terminal_job_cannot_be_claimed(self):
        for job_id, status in ((3, "failed"), (4, "succeeded")):
            self._seed(job_id=job_id, status=status)
            with TestingSessionLocal() as db:
                self.assertIsNone(self.jobs.claim_preparation_job(db, job_id))
            self.assertEqual(self._get(job_id)["status"], status)

    def test_publish_failure_records_failed_job_and_can_resubmit(self):
        from kombu.exceptions import OperationalError
        from redis.exceptions import RedisError
        for error in (OperationalError("断开连接"), RedisError("Redis不可用")):
            with self.subTest(error=type(error).__name__):
                self.publish.side_effect = error
                response = client.post(self.url)
                self.assertEqual(response.status_code, 503, response.text)
                job = self._get(response.json()["detail"]["job_id"])
                self.assertEqual((job["status"], job["stage"]), ("failed", "failed"))
                self.assertIsNotNone(job["finished_at"])
                self.assertIn("Redis", job["error_message"])
        self.publish.side_effect = None
        self.assertEqual(client.post(self.url).status_code, 202)

    def test_ambiguous_publish_failure_does_not_overwrite_claimed_job(self):
        from kombu.exceptions import OperationalError
        def accepted_then_disconnected(*, args, retry):
            with TestingSessionLocal() as db:
                self.jobs.claim_preparation_job(db, args[0])
            raise OperationalError("已接收消息，但回复连接断开")
        self.publish.side_effect = accepted_then_disconnected
        response = client.post(self.url)
        self.assertEqual(response.status_code, 503, response.text)
        job = self._get(response.json()["detail"]["job_id"])
        self.assertEqual(job["status"], "running")
        self.assertIsNone(job["error_message"])
        self.assertEqual(client.post(self.url).status_code, 409)

    def test_worker_runs_actual_pipeline_saves_data_and_exposes_stages(self):
        import research_library.services.paper_pipeline as paper_pipeline
        self._seed()
        actual_prepare = paper_pipeline.prepare_paper_document
        observed = []
        def observe_prepare(*, db, document, on_stage):
            def observe_stage(stage):
                on_stage(stage)
                job = self._get()
                observed.append((job["status"], job["stage"]))
            return actual_prepare(db=db, document=document, on_stage=observe_stage)
        with patch('research_library.workers.paper_preparation_worker.paper_pipeline.prepare_paper_document', side_effect=observe_prepare):
            self.worker.run_preparation_job.run(3)
        job = self._get()
        self.assertEqual(observed, [("running", s) for s in ("extracting", "chunking", "indexing")])
        self.assertEqual((job["status"], job["stage"]), ("succeeded", "finished"))
        self.assertIsNotNone(job["started_at"])
        self.assertIsNotNone(job["finished_at"])
        self.assertIsNone(job["error_message"])
        result = job["result"]
        self.assertEqual((result["paper_id"], result["document_id"]), (9, 12))
        with TestingSessionLocal() as db:
            pages = db.query(self.Page).filter_by(document_id=12).all()
            chunks = db.query(PaperChunk).filter_by(document_id=12).all()
            vectors = [db.get(PaperChunkEmbedding, c.id) for c in chunks]
            self.assertEqual(result["page_count"], len(pages))
            self.assertEqual(result["chunk_count"], len(chunks))
            self.assertEqual(result["indexed_count"], len(chunks))
            self.assertGreater(len(chunks), 0)
            self.assertTrue(all(v and v.vector == [0.1, 0.2, 0.3] for v in vectors))
        self.embed.assert_called()

    def test_worker_duplicate_message_does_not_repeat_processing(self):
        self._seed()
        self.worker.run_preparation_job.run(3)
        before = self._get()
        with patch('research_library.workers.paper_preparation_worker.paper_pipeline.prepare_paper_document') as prepare:
            self.worker.run_preparation_job.run(3)
            self.worker.run_preparation_job.run(999999)
            prepare.assert_not_called()
        self.assertEqual(self._get(), before)

    def test_worker_refuses_missing_or_wrong_document(self):
        for job_id, document_id, message in ((3, 999999, "已经不存在"), (4, 13, "不一致")):
            with self.subTest(document_id=document_id):
                self._seed(job_id=job_id, document_id=document_id)
                with patch('research_library.workers.paper_preparation_worker.paper_pipeline.prepare_paper_document') as prepare:
                    with self.assertRaisesRegex(ValueError, message):
                        self.worker.run_preparation_job.run(job_id)
                    prepare.assert_not_called()
                job = self._get(job_id)
                self.assertEqual(job["status"], "failed")
                self.assertIn(message, job["error_message"])
                self.assertIsNotNone(job["finished_at"])

    def test_missing_pdf_file_records_failure_without_vectors(self):
        self._seed()
        self.pdf_path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.worker.run_preparation_job.run(3)
        job = self._get()
        self.assertEqual((job["status"], job["stage"]), ("failed", "failed"))
        self.assertIsNone(job["result"])
        self.assertIn("没有找到PDF文件", job["error_message"])
        self.embed.assert_not_called()

    def test_vector_failure_then_retry_reuses_saved_pages_and_chunks(self):
        self._seed()
        self.embed.side_effect = URLError("Ollama不可用")
        with self.assertRaises(URLError):
            self.worker.run_preparation_job.run(3)
        self.assertEqual(self._get()["status"], "failed")
        with TestingSessionLocal() as db:
            old_pages = [p.id for p in db.query(self.Page).filter_by(document_id=12)]
            old_chunks = [c.id for c in db.query(PaperChunk).filter_by(document_id=12)]
            self.assertTrue(old_pages and old_chunks)
            self.assertEqual(db.query(PaperChunkEmbedding).count(), 0)
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        new_id = response.json()["id"]
        self.embed.side_effect = lambda texts: [[0.1, 0.2, 0.3] for _ in texts]
        with patch('research_library.services.paper_pipeline.extract_pdf_text', side_effect=AssertionError("重试应复用已有页面")):
            self.worker.run_preparation_job.run(new_id)
        self.assertEqual(self._get(new_id)["status"], "succeeded")
        with TestingSessionLocal() as db:
            self.assertEqual([p.id for p in db.query(self.Page).filter_by(document_id=12)], old_pages)
            self.assertEqual([c.id for c in db.query(PaperChunk).filter_by(document_id=12)], old_chunks)

    def test_new_job_reuses_existing_vectors_and_returns_zero_new_indexes(self):
        self._seed()
        self.worker.run_preparation_job.run(3)
        self.embed.reset_mock()
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        new_id = response.json()["id"]
        with patch('research_library.services.paper_pipeline.extract_pdf_text', side_effect=AssertionError("已有页面不能重复提取")):
            self.worker.run_preparation_job.run(new_id)
        job = self._get(new_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["indexed_count"], 0)
        self.embed.assert_not_called()

    def test_creation_commit_failure_rolls_back(self):
        with TestingSessionLocal() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("模拟提交失败")):
                with self.assertRaisesRegex(RuntimeError, "模拟提交失败"):
                    self.jobs.create_preparation_job(db, 9)
            self.assertFalse(db.in_transaction())
        self.assertEqual(self._count(), 0)

    def test_worker_failure_limits_error_length(self):
        self._seed()
        with patch('research_library.workers.paper_preparation_worker.paper_pipeline.prepare_paper_document',
                   side_effect=RuntimeError("错" * 2500)):
            with self.assertRaises(RuntimeError):
                self.worker.run_preparation_job.run(3)
        self.assertEqual(len(self._get()["error_message"]), 2000)
