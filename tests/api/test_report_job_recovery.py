"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestReportJobRecovery(ComparisonDatabaseFixture, unittest.TestCase):
    """恢复中断作业及脚本交互；不访问真实MySQL或投递消息。"""

    def setUp(self):
        super().setUp()
        from research_library.db.model import ReportJob
        import scripts.recover_report_job as recover_report_job
        self.Job = ReportJob
        self.script = recover_report_job
        self.publish = self.enterContext(patch('research_library.api.reports.run_report_job.apply_async'))
        self.enterContext(patch('scripts.recover_report_job.SessionLocal', TestingSessionLocal))

    def _seed_job(self, status, task_id=None):
        with TestingSessionLocal() as db:
            job = self.Job(
                task_id=self.task_id if task_id is None else task_id,
                status=status, stage="generating" if status == "running" else "waiting",
                analysis_snapshot=self.analysis.model_dump(mode="json"),
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            return job.id

    def _snapshot_job(self, job_id):
        with TestingSessionLocal() as db:
            job = db.get(self.Job, job_id)
            return {column.name: getattr(job, column.name) for column in self.Job.__table__.columns}

    def _recover(self, job_id):
        from research_library.repositories.report_job_crud import recover_interrupted_job
        with TestingSessionLocal() as db:
            return recover_interrupted_job(db, job_id)

    def _run_script(self, answers):
        with patch("builtins.input", side_effect=answers) as inputs, patch("builtins.print") as output:
            self.script.main()
        return inputs, output

    def test_queued_and_running_recover_and_preserve_input(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                job_id = self._seed_job(status)
                before = self._snapshot_job(job_id)
                self.assertTrue(self._recover(job_id))
                after = self._snapshot_job(job_id)
                self.assertEqual(after["status"], "failed")
                self.assertEqual(after["stage"], "interrupted")
                self.assertIn("Worker停止", after["error_message"])
                self.assertIsNotNone(after["finished_at"])
                for name in ("id", "task_id", "analysis_snapshot", "result", "created_at", "started_at"):
                    self.assertEqual(after[name], before[name])
        self.publish.assert_not_called()

    def test_terminal_jobs_are_preserved(self):
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                job_id = self._seed_job(status)
                with TestingSessionLocal() as db:
                    job = db.get(self.Job, job_id)
                    job.result = {"retained": "已有结果"}
                    job.error_message = "已有说明"
                    db.commit()
                before = self._snapshot_job(job_id)
                self.assertFalse(self._recover(job_id))
                self.assertEqual(self._snapshot_job(job_id), before)

    def test_missing_job_returns_false(self):
        self.assertFalse(self._recover(999999))

    def test_repeated_recovery_does_not_overwrite_first_recovery(self):
        job_id = self._seed_job("running")
        self.assertTrue(self._recover(job_id))
        before = self._snapshot_job(job_id)
        self.assertFalse(self._recover(job_id))
        self.assertEqual(self._snapshot_job(job_id), before)

    def test_only_target_job_changes_and_research_tasks_are_unchanged(self):
        target = self._seed_job("running")
        same_task = self._seed_job("queued")
        with TestingSessionLocal() as db:
            other_task = db.query(ResearchTask).filter(ResearchTask.id != self.task_id).one().id
        other = self._seed_job("running", other_task)
        before = {job_id: self._snapshot_job(job_id) for job_id in (same_task, other)}
        tasks_before = self._snapshot()
        self.assertTrue(self._recover(target))
        self.assertEqual(self._snapshot(), tasks_before)
        for job_id, snapshot in before.items():
            self.assertEqual(self._snapshot_job(job_id), snapshot)

    def test_recovery_unblocks_resubmission_and_preserves_old_job(self):
        job_id = self._seed_job("running")
        url = f"/research-tasks/{self.task_id}/report-jobs"
        self.assertEqual(client.post(url).status_code, 409)
        self.publish.assert_not_called()
        self.assertTrue(self._recover(job_id))
        old = self._snapshot_job(job_id)
        response = client.post(url)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotEqual(response.json()["id"], job_id)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(self._snapshot_job(job_id), old)
        self.publish.assert_called_once_with(args=[response.json()["id"]], retry=False)

    def test_stale_queue_message_cannot_claim_recovered_job(self):
        from research_library.repositories.report_job_crud import claim_report_job
        job_id = self._seed_job("queued")
        self.assertTrue(self._recover(job_id))
        with TestingSessionLocal() as db:
            self.assertIsNone(claim_report_job(db, job_id))
        self.assertEqual(self._snapshot_job(job_id)["status"], "failed")

    def test_commit_failure_rolls_back_and_propagates(self):
        from research_library.repositories.report_job_crud import recover_interrupted_job
        job_id = self._seed_job("running")
        before = self._snapshot_job(job_id)
        with TestingSessionLocal() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("模拟提交失败")):
                with self.assertRaisesRegex(RuntimeError, "模拟提交失败"):
                    recover_interrupted_job(db, job_id)
            self.assertFalse(db.in_transaction())
        self.assertEqual(self._snapshot_job(job_id), before)

    def test_script_invalid_id_exits_without_database_access(self):
        with patch('scripts.recover_report_job.SessionLocal') as sessions:
            inputs, output = self._run_script(["不是数字"])
        sessions.assert_not_called()
        self.assertEqual(inputs.call_count, 1)
        output.assert_any_call("作业编号必须是整数。")

    def test_script_missing_job_exits_before_confirmation(self):
        inputs, output = self._run_script(["999999"])
        self.assertEqual(inputs.call_count, 1)
        output.assert_any_call("没有找到这个作业。")

    def test_script_terminal_job_exits_before_confirmation(self):
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                job_id = self._seed_job(status)
                before = self._snapshot_job(job_id)
                inputs, output = self._run_script([str(job_id)])
                self.assertEqual(inputs.call_count, 1)
                output.assert_any_call("这个作业已经结束，不需要恢复。")
                self.assertEqual(self._snapshot_job(job_id), before)

    def test_script_cancellation_does_not_modify_job(self):
        job_id = self._seed_job("running")
        before = self._snapshot_job(job_id)
        _, output = self._run_script([str(job_id), "取消"])
        output.assert_any_call("已取消恢复。")
        self.assertEqual(self._snapshot_job(job_id), before)

    def test_script_confirmation_recovers_job_without_publishing(self):
        job_id = self._seed_job("running")
        _, output = self._run_script([f" {job_id} ", " 确认恢复 "])
        output.assert_any_call("旧作业已标记为失败，可以重新提交研究任务。")
        self.assertEqual(self._snapshot_job(job_id)["status"], "failed")
        self.publish.assert_not_called()

    def test_script_handles_state_change_before_update(self):
        from research_library.repositories.report_job_crud import recover_interrupted_job
        job_id = self._seed_job("running")
        def complete_then_recover(db, selected_id):
            job = db.get(self.Job, selected_id)
            job.status = "succeeded"
            job.stage = "finished"
            db.commit()
            return recover_interrupted_job(db, selected_id)
        with patch('scripts.recover_report_job.recover_interrupted_job', side_effect=complete_then_recover):
            _, output = self._run_script([str(job_id), "确认恢复"])
        output.assert_any_call("作业状态已经变化，本次没有修改记录。")
        self.assertEqual(self._snapshot_job(job_id)["status"], "succeeded")

    def test_direct_script_execution_invokes_main(self):
        import runpy
        script_path = PROJECT_ROOT / "scripts" / "recover_report_job.py"
        with patch("builtins.input", return_value="不是数字") as inputs, patch("builtins.print") as output:
            runpy.run_path(str(script_path), run_name="__main__")
        inputs.assert_called_once()
        output.assert_any_call("作业编号必须是整数。")
