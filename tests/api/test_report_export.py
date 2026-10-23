"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestReportExport(ComparisonDatabaseFixture, unittest.TestCase):
    """验证Markdown下载：真实接口和隔离数据库，禁止模型调用及队列投递。"""

    def setUp(self):
        super().setUp()
        from research_library.db.model import ReportJob
        from research_library.schemas import CheckedComparisonReportResponse, ComparisonReportResponse, ComparisonSourceResponse
        self.Job = ReportJob
        self.claim = "A采用语义匹配，B采用完全匹配。[资料23][资料3]"
        report = ComparisonReportResponse(
            task_id=self.task_id, question=self.analysis.question, paper_ids=self.paper_ids,
            method_comparisons=[dict(text=self.claim, source_numbers=[23, 3])],
            experiment_comparisons=[], conclusion_comparisons=[], evidence_notes=[],
            # 故意乱序、不连续，导出只能排序，不能重新编号。
            sources=[ComparisonSourceResponse(source_number=n, **source.model_dump())
                     for n, source in ((23, self.analysis.sources[0]), (3, self.analysis.sources[2]))],
        )
        review = ResearchTaskReviewResponse(task_id=self.task_id, items=[
            ReviewItemResponse(field="method_comparisons", claim=self.claim,
                               verdict="supported", reason="双方原文支持该比较。", source_numbers=[23, 3])])
        self.checked = CheckedComparisonReportResponse(
            task_id=self.task_id, report=report, initial_review=review, review=review,
            passed=True, correction_rounds=0,
        )
        self.model_call = self.enterContext(patch(
            'research_library.core.llm_service.generate_text', side_effect=AssertionError("导出不能调用模型")))
        self.publish = self.enterContext(patch(
            'research_library.api.reports.run_report_job.apply_async', side_effect=AssertionError("导出不能创建后台作业")))
        # Agent导入了各自的generate_text引用，也要防止意外调用。
        for target in ('research_library.agents.comparison_agent.generate_text', 'research_library.agents.review_agent.generate_text', 'research_library.agents.revision_agent.generate_text'):
            self.enterContext(patch(target, side_effect=AssertionError("导出不能调用Agent模型")))

    def _seed_job(self, status="succeeded", payload=None):
        with TestingSessionLocal() as db:
            job = self.Job(task_id=self.task_id, status=status, stage="finished",
                           analysis_snapshot=self.analysis.model_dump(mode="json"),
                           result=self.checked.model_dump(mode="json") if payload is None else payload)
            db.add(job)
            db.commit()
            db.refresh(job)
            return job.id

    def _export(self, job_id):
        return client.get(f"/report-jobs/{job_id}/export")

    def test_success_download_has_utf8_content_and_attachment_filename(self):
        job_id = self._seed_job()
        response = self._export(job_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/markdown; charset=utf-8")
        self.assertEqual(response.headers["content-disposition"],
                         f'attachment; filename="report-task-{self.task_id}-job-{job_id}.md"')
        text = response.content.decode("utf-8")
        for expected in ("# 科研论文对比报告", self.analysis.question, self.claim,
                         "模型审校通过", "双方原文支持该比较。", "## 资料来源"):
            self.assertIn(expected, text)
        self.assertNotIn("## 未采用的修订", text)

    def test_sources_sort_without_renumbering_or_swapping_papers(self):
        response = self._export(self._seed_job())
        self.assertEqual(response.status_code, 200, response.text)
        sources = response.text.split("## 资料来源", 1)[1]
        self.assertLess(sources.index("### 资料3\n"), sources.index("### 资料23\n"))
        self.assertNotIn("### 资料1\n", sources)
        for number, source in ((23, self.analysis.sources[0]), (3, self.analysis.sources[2])):
            self.assertIn(f"### 资料{number}\n\n> 论文编号：{source.paper_id}\n"
                          f"> 论文标题：{source.paper_title}\n> 页码：{source.page_number}\n"
                          f"> 文本块编号：{source.chunk_id}", sources)

    def test_unapproved_report_downloads_with_pending_label_and_rejection_reason(self):
        from research_library.schemas import AnalysisRevisionResponse, RejectedComparisonRevisionResponse
        review = ReviewItemResponse(field="method_comparisons", claim=self.claim,
                                   verdict="partially_supported", reason="仍有证据不足。", source_numbers=[23, 3])
        bad_text = "未采用的错误归属。[资料23][资料3]"
        rejected = RejectedComparisonRevisionResponse(
            revision=AnalysisRevisionResponse(field="method_comparisons", original_claim=self.claim,
                revised_claim=bad_text, reason="尝试修正", source_numbers=[23, 3]),
            review=ReviewItemResponse(field="method_comparisons", claim=bad_text,
                verdict="contradicted", reason="修订交换了论文归属。", source_numbers=[23, 3]))
        self.checked = self.checked.model_copy(update={
            "passed": False, "correction_rounds": 1,
            "review": ResearchTaskReviewResponse(task_id=self.task_id, items=[review]),
            "rejected_revisions": [rejected],
        })
        response = self._export(self._seed_job())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("待核验：存在未通过审校的陈述", response.text)
        self.assertNotIn("模型审校通过", response.text)
        adopted, rejected_section = response.text.split("## 未采用的修订", 1)
        self.assertIn(self.claim, adopted)
        self.assertNotIn(bad_text, adopted)
        self.assertIn(bad_text, rejected_section)
        self.assertIn("修订交换了论文归属。", rejected_section)

    def test_empty_sections_keep_headings_and_explanation(self):
        response = self._export(self._seed_job())
        self.assertEqual(response.status_code, 200, response.text)
        for title in ("实验对比", "结论对比", "证据说明"):
            self.assertIn(f"## {title}\n\n本部分没有内容。", response.text)

    def test_multiline_question_preserves_chinese_lines(self):
        from research_library.services.report_export import build_report_markdown
        updated = self.checked.model_copy(deep=True)
        updated.report.question = "第一行研究问题\n第二行补充说明"
        text = build_report_markdown(updated, 16)
        self.assertIn("> 第一行研究问题\n> 第二行补充说明", text)

    def test_missing_job_returns_404(self):
        response = self._export(999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "没有找到这个报告作业")

    def test_unfinished_and_failed_jobs_return_409(self):
        for status in ("queued", "running", "failed"):
            with self.subTest(status=status):
                response = self._export(self._seed_job(status=status))
                self.assertEqual(response.status_code, 409, response.text)
                self.assertNotIn("content-disposition", response.headers)

    def test_completed_job_without_result_returns_500(self):
        job_id = self._seed_job()
        with TestingSessionLocal() as db:
            db.get(self.Job, job_id).result = None
            db.commit()
        response = self._export(job_id)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "作业已完成，但没有保存报告结果")

    def test_malformed_result_returns_500_without_download(self):
        for payload in ({}, {"task_id": self.task_id}, "不是正确的报告对象"):
            with self.subTest(payload=payload):
                response = self._export(self._seed_job(payload=payload))
                self.assertEqual(response.status_code, 500, response.text)
                self.assertEqual(response.json()["detail"], "保存的报告格式不正确，无法导出")
                self.assertNotIn("content-disposition", response.headers)

    def test_task_mismatch_at_either_level_returns_500(self):
        for outer in (True, False):
            with self.subTest(outer=outer):
                payload = self.checked.model_dump(mode="json")
                if outer:
                    payload["task_id"] = self.task_id + 100
                else:
                    payload["report"]["task_id"] = self.task_id + 100
                response = self._export(self._seed_job(payload=payload))
                self.assertEqual(response.status_code, 500, response.text)
                self.assertEqual(response.json()["detail"], "报告与作业所属的研究任务不一致")

    def test_legacy_report_without_rejections_still_downloads(self):
        payload = self.checked.model_dump(mode="json")
        del payload["rejected_revisions"]
        response = self._export(self._seed_job(payload=payload))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(self.claim, response.text)

    def test_repeated_export_does_not_change_database_or_invoke_generation(self):
        from sqlalchemy import select
        job_id = self._seed_job()
        before = self._snapshot()
        with TestingSessionLocal() as db:
            original = db.get(self.Job, job_id)
            job_before = (original.status, original.stage, original.result, original.analysis_snapshot,
                          original.error_message, original.created_at, original.finished_at)
            count = db.query(self.Job).count()
        first = self._export(job_id)
        second = self._export(job_id)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.content, second.content)
        self.assertEqual(self._snapshot(), before)
        with TestingSessionLocal() as db:
            current = db.get(self.Job, job_id)
            self.assertEqual((current.status, current.stage, current.result, current.analysis_snapshot,
                              current.error_message, current.created_at, current.finished_at), job_before)
            self.assertEqual(db.query(self.Job).count(), count)
        self.model_call.assert_not_called()
        self.publish.assert_not_called()
