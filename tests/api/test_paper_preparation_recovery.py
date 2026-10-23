"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestPaperPreparationRecovery(PaperPreparationJobFixture, unittest.TestCase):
    """恢复工具只修改隔离数据库；验证历史保留、交互取消和重新提交。"""

    def setUp(self):
        super().setUp()
        import scripts.recover_paper_preparation_job as script
        self.script = script
        self.enterContext(patch('scripts.recover_paper_preparation_job.SessionLocal', TestingSessionLocal))
        import research_library.services.paper_pipeline as paper_pipeline
        with TestingSessionLocal() as db:
            paper_pipeline.prepare_paper_document(db, db.get(PaperDocument, 12))
        self.embed.reset_mock()

    def _snapshot_job(self, job_id=3):
        with TestingSessionLocal() as db:
            job = db.get(self.Job, job_id)
            return {c.name: getattr(job, c.name) for c in self.Job.__table__.columns}

    def _snapshot_data(self):
        from sqlalchemy import select
        with TestingSessionLocal() as db:
            data = {model.__tablename__: [
                {c.name: getattr(row, c.name) for c in model.__table__.columns}
                for row in db.scalars(select(model))]
                for model in (Paper, PaperDocument, self.Page, PaperChunk, PaperChunkEmbedding)}
        return json.dumps(data, sort_keys=True, default=str), self.pdf_path.read_bytes()

    def _recover(self, job_id=3):
        with TestingSessionLocal() as db:
            return self.jobs.recover_interrupted_preparation_job(db, job_id)

    def _run_script(self, answers):
        with patch("builtins.input", side_effect=answers) as inputs, patch("builtins.print") as output:
            self.script.main()
        return inputs, output

    def test_queued_and_running_recover_without_changing_input_or_prepared_data(self):
        data_before = self._snapshot_data()
        for job_id, status in ((3, "queued"), (4, "running")):
            with self.subTest(status=status):
                self._seed(job_id=job_id, status=status)
                before = self._snapshot_job(job_id)
                self.assertTrue(self._recover(job_id))
                after = self._snapshot_job(job_id)
                self.assertEqual((after["status"], after["stage"]), ("failed", "interrupted"))
                self.assertIn("所有Worker停止", after["error_message"])
                self.assertIsNotNone(after["finished_at"])
                for name in ("id", "paper_id", "document_id", "result", "created_at", "started_at"):
                    self.assertEqual(after[name], before[name])
                self.assertEqual(self._snapshot_data(), data_before)
        self.publish.assert_not_called()
        self.embed.assert_not_called()

    def test_terminal_jobs_and_existing_results_are_unchanged(self):
        for job_id, status in ((3, "succeeded"), (4, "failed")):
            with self.subTest(status=status):
                self._seed(job_id=job_id, status=status)
                with TestingSessionLocal() as db:
                    job = db.get(self.Job, job_id)
                    job.result = {"retained": "已有结果"}
                    job.error_message = "已有说明"
                    db.commit()
                before = self._snapshot_job(job_id)
                self.assertFalse(self._recover(job_id))
                self.assertEqual(self._snapshot_job(job_id), before)

    def test_repeated_recovery_preserves_first_recovery_record(self):
        self._seed(status="running")
        self.assertTrue(self._recover())
        before = self._snapshot_job()
        self.assertFalse(self._recover())
        self.assertEqual(self._snapshot_job(), before)

    def test_missing_job_returns_false_without_creating_record(self):
        self.assertFalse(self._recover(999999))
        self.assertEqual(self._count(), 0)

    def test_only_requested_job_changes_and_report_jobs_are_untouched(self):
        from research_library.db.model import ReportJob
        self._seed(status="running")
        self._seed(job_id=4)
        self._seed(job_id=5, paper_id=3, document_id=13, status="running")
        before = {n: self._snapshot_job(n) for n in (4, 5)}
        with TestingSessionLocal() as db:
            db.add(ResearchTask(id=10, title="报告任务", question="报告问题", status="pending"))
            db.flush()
            db.add(ReportJob(id=3, task_id=10, status="running", stage="generating", analysis_snapshot={}))
            db.commit()
        self.assertTrue(self._recover(3))
        for number, previous in before.items():
            self.assertEqual(self._snapshot_job(number), previous)
        with TestingSessionLocal() as db:
            report = db.get(ReportJob, 3)
            self.assertEqual((report.status, report.stage), ("running", "generating"))

    def test_commit_failure_rolls_back_and_keeps_job_running(self):
        self._seed(status="running")
        before = self._snapshot_job()
        with TestingSessionLocal() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("模拟恢复提交失败")):
                with self.assertRaisesRegex(RuntimeError, "提交失败"):
                    self.jobs.recover_interrupted_preparation_job(db, 3)
            self.assertFalse(db.in_transaction())
        self.assertEqual(self._snapshot_job(), before)

    def test_invalid_script_input_never_opens_database(self):
        for answer in ("abc", "1.5", "", "0", "-3"):
            with self.subTest(answer=answer), patch.object(self.script, "SessionLocal") as sessions:
                inputs, output = self._run_script([answer])
                sessions.assert_not_called()
                self.assertEqual(inputs.call_count, 1)
                self.assertTrue(output.called)

    def test_script_missing_job_does_not_ask_confirmation(self):
        inputs, output = self._run_script(["999999"])
        self.assertEqual(inputs.call_count, 1)
        output.assert_any_call("没有找到这个论文准备作业。")
        self.assertEqual(self._count(), 0)

    def test_script_terminal_job_is_preserved_without_confirmation(self):
        for job_id, status in ((3, "succeeded"), (4, "failed")):
            with self.subTest(status=status):
                self._seed(job_id=job_id, status=status)
                before = self._snapshot_job(job_id)
                inputs, output = self._run_script([str(job_id)])
                self.assertEqual(inputs.call_count, 1)
                output.assert_any_call("这个作业已经结束，不需要恢复。")
                self.assertEqual(self._snapshot_job(job_id), before)

    def test_cancelled_or_incorrect_confirmation_keeps_job_unchanged(self):
        self._seed(status="running")
        before = self._snapshot_job()
        for answer in ("", "取消", "确认"):
            with self.subTest(answer=answer):
                inputs, output = self._run_script(["3", answer])
                self.assertEqual(inputs.call_count, 2)
                output.assert_any_call("已取消恢复。")
                self.assertEqual(self._snapshot_job(), before)

    def test_script_confirmation_displays_distinct_job_paper_and_document_ids(self):
        self._seed(status="running")
        inputs, output = self._run_script([" 3 ", " 确认恢复 "])
        self.assertIn("所有Celery Worker已停止", inputs.call_args_list[1].args[0])
        for line in ("作业编号：3", "论文编号：9", "PDF文档编号：12", "旧作业已标记为失败。"):
            output.assert_any_call(line)
        self.assertEqual(self._get()["stage"], "interrupted")
        self.publish.assert_not_called()

    def test_state_change_after_display_does_not_overwrite_success(self):
        self._seed(status="running")
        before = []
        def answer(prompt):
            if "请输入" in prompt:
                return "3"
            with TestingSessionLocal() as db:
                job = db.get(self.Job, 3)
                job.status, job.stage = "succeeded", "finished"
                db.commit()
            before.append(self._snapshot_job())
            return "确认恢复"
        inputs, output = self._run_script(answer)
        output.assert_any_call("作业状态已经变化，本次没有修改记录。")
        self.assertEqual(self._snapshot_job(), before[0])

    def test_recovery_unblocks_submission_and_old_message_cannot_run(self):
        self._seed(status="running")
        data_before = self._snapshot_data()
        self.assertEqual(client.post(self.url).status_code, 409)
        self.assertTrue(self._recover())
        old = self._snapshot_job()
        with patch('research_library.workers.paper_preparation_worker.paper_pipeline.prepare_paper_document') as prepare:
            self.worker.run_preparation_job.run(3)
            prepare.assert_not_called()
        self.assertEqual(self._snapshot_job(), old)
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        new_id = response.json()["id"]
        self.assertNotEqual(new_id, 3)
        self.publish.assert_called_once_with(args=[new_id], retry=False)
        with patch('research_library.services.paper_pipeline.extract_pdf_text', side_effect=AssertionError("已有页面应复用")):
            self.worker.run_preparation_job.run(new_id)
        job = self._get(new_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["indexed_count"], 0)
        self.embed.assert_not_called()
        self.assertEqual(self._snapshot_data(), data_before)
        self.assertEqual(self._snapshot_job(), old)
