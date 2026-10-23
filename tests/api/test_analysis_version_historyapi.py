"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestAnalysisVersionHistoryAPI(unittest.TestCase):
    """用真实接口查询测试数据库，确认版本隔离、JSON转换和只读行为。"""

    def setUp(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        with TestingSessionLocal() as db:
            task = ResearchTask(title="版本查询测试", question="研究问题", status="completed")
            other = ResearchTask(title="其他任务", question="其他问题", status="completed")
            empty = ResearchTask(title="尚无版本的任务", question="问题", status="pending")
            db.add_all([task, other, empty])
            db.flush()
            self.task_id, self.other_id, self.empty_id = task.id, other.id, empty.id
            before = ResearchTaskAnalysisResponse(
                task_id=task.id, question=task.question, summary="旧概述", methods=[], experiments=[],
                conclusions=[], limitations=["当前资料未说明生产验证。"], evidence_notes=[], sources=[],
            )
            after = before.model_copy(deep=True)
            after.limitations = []
            after.evidence_notes = ["当前引用原文未说明生产验证。"]
            review = ResearchTaskReviewResponse(task_id=task.id, items=[
                ReviewItemResponse(field="limitations", claim=before.limitations[0],
                                   verdict="insufficient_evidence", reason="测试旧审校", source_numbers=[])
            ])
            task.result, task.review_result = after.model_dump_json(), None
            self.before, self.after, self.review = before.model_dump(), after.model_dump(), review.model_dump()
            self.saved_json = dict(previous_analysis=before.model_dump_json(),
                                   updated_analysis=after.model_dump_json(), previous_review=review.model_dump_json())
            old_version = ResearchTaskAnalysisVersion(task_id=task.id, reason="第一次修改", **self.saved_json)
            db.add(old_version)
            db.flush()
            self.old_version_id = old_version.id
            other_analysis = before.model_copy(update={"task_id": other.id, "question": other.question})
            other_version = ResearchTaskAnalysisVersion(
                task_id=other.id, previous_analysis=other_analysis.model_dump_json(),
                updated_analysis=other_analysis.model_dump_json(), previous_review=None, reason="其他任务的版本",
            )
            db.add(other_version)
            db.flush()
            self.other_version_id = other_version.id
            new_version = ResearchTaskAnalysisVersion(task_id=task.id, reason="第二次修改", **self.saved_json)
            db.add(new_version)
            db.flush()
            self.new_version_id = new_version.id
            db.commit()

    def _snapshot(self):
        """完整记录当前任务和版本内容，用于确认 GET 没有修改数据库。"""
        from sqlalchemy import select
        from research_library.db.model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            tasks = tuple((task.id, task.title, task.question, task.result, task.review_result, task.status)
                          for task in db.scalars(select(ResearchTask).order_by(ResearchTask.id)))
            versions = tuple((version.id, version.task_id, version.previous_analysis,
                              version.updated_analysis, version.previous_review, version.reason, version.created_at)
                             for version in db.scalars(select(ResearchTaskAnalysisVersion).order_by(ResearchTaskAnalysisVersion.id)))
            return tasks, versions

    def _get(self, version_id=None, task_id=None):
        """每次查询都检查数据库不变，且不调用任何分析、审校、修订模型。"""
        before = self._snapshot()
        task_id = self.task_id if task_id is None else task_id
        url = f"/research-tasks/{task_id}/analysis/versions"
        if version_id is not None:
            url += f"/{version_id}"
        with (
            patch('research_library.agents.paper_analysis_agent.generate_text') as analyze,
            patch('research_library.agents.review_agent.generate_text') as review,
            patch('research_library.agents.revision_agent.generate_text') as revise,
        ):
            response = client.get(url)
        analyze.assert_not_called()
        review.assert_not_called()
        revise.assert_not_called()
        self.assertEqual(self._snapshot(), before)
        return response

    def test_list_returns_latest_first_and_only_summary_fields(self):
        from datetime import datetime
        response = self._get()
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual([row["id"] for row in rows], [self.new_version_id, self.old_version_id])
        for row in rows:
            self.assertEqual(set(row), {"id", "task_id", "reason", "created_at"})
            self.assertEqual(row["task_id"], self.task_id)
            datetime.fromisoformat(row["created_at"])
        self.assertEqual(rows[0]["reason"], "第二次修改")

    def test_list_is_scoped_to_requested_task(self):
        response = self._get(task_id=self.other_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["id"] for row in response.json()], [self.other_version_id])
        self.assertEqual(response.json()[0]["task_id"], self.other_id)

    def test_existing_task_without_versions_returns_empty_list(self):
        response = self._get(task_id=self.empty_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])

    def test_nonexistent_task_returns_404_for_list_and_detail(self):
        for version_id in (None, self.old_version_id):
            with self.subTest(version_id=version_id):
                response = self._get(task_id=999999, version_id=version_id)
                self.assertEqual(response.status_code, 404, response.text)

    def test_detail_returns_structured_before_after_and_old_review(self):
        response = self._get(self.new_version_id)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["id"], self.new_version_id)
        self.assertEqual(data["task_id"], self.task_id)
        self.assertEqual(data["reason"], "第二次修改")
        self.assertTrue(data["created_at"])
        self.assertEqual(data["previous_analysis"], self.before)
        self.assertEqual(data["updated_analysis"], self.after)
        self.assertEqual(data["previous_review"], self.review)
        self.assertIsInstance(data["previous_analysis"], dict)
        self.assertIsInstance(data["updated_analysis"], dict)
        self.assertIsInstance(data["previous_review"], dict)

    def test_detail_allows_missing_old_review(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            db.get(ResearchTaskAnalysisVersion, self.new_version_id).previous_review = None
            db.commit()
        response = self._get(self.new_version_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["previous_review"])
        self.assertEqual(response.json()["previous_analysis"], self.before)

    def test_detail_reads_legacy_analysis_without_rewriting_stored_json(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        legacy_before = dict(self.before)
        legacy_after = dict(self.after)
        legacy_before.pop("evidence_notes")
        legacy_after.pop("evidence_notes")
        with TestingSessionLocal() as db:
            version = db.get(ResearchTaskAnalysisVersion, self.new_version_id)
            version.previous_analysis = json.dumps(legacy_before)
            version.updated_analysis = json.dumps(legacy_after)
            db.commit()
        response = self._get(self.new_version_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["previous_analysis"]["evidence_notes"], [])
        self.assertEqual(response.json()["updated_analysis"]["evidence_notes"], [])
        # _get 已确认默认值补齐只发生在响应中，数据库的旧 JSON 未被覆盖。

    def test_version_from_another_task_returns_404(self):
        response = self._get(self.other_version_id)
        self.assertEqual(response.status_code, 404, response.text)

    def test_nonexistent_version_returns_404(self):
        response = self._get(999999)
        self.assertEqual(response.status_code, 404, response.text)

    def test_malformed_saved_json_returns_409_without_modifying_history(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        for field in self.saved_json:
            with self.subTest(field=field):
                with TestingSessionLocal() as db:
                    version = db.get(ResearchTaskAnalysisVersion, self.new_version_id)
                    for original_field, value in self.saved_json.items():
                        setattr(version, original_field, value)
                    setattr(version, field, "这不是JSON")
                    db.commit()
                response = self._get(self.new_version_id)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"], "这个版本保存的数据格式不正确")

    def test_wrong_saved_field_types_return_409(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        bad_data = dict(self.before, methods="错误的字符串")
        with TestingSessionLocal() as db:
            version = db.get(ResearchTaskAnalysisVersion, self.new_version_id)
            version.previous_analysis = json.dumps(bad_data)
            db.commit()
        response = self._get(self.new_version_id)
        self.assertEqual(response.status_code, 409, response.text)

    def test_history_remains_readable_without_current_analysis_or_review(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            task.result, task.review_result = None, None
            db.commit()
        self.assertEqual(self._get().status_code, 200)
        response = self._get(self.new_version_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["previous_review"], self.review)

    def test_deleting_task_removes_its_versions_but_keeps_other_tasks_history(self):
        from sqlalchemy import select
        from research_library.db.model import ResearchTaskAnalysisVersion
        response = client.delete(f"/research-tasks/{self.task_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._get().status_code, 404)
        self.assertEqual(self._get(self.old_version_id).status_code, 404)
        with TestingSessionLocal() as db:
            versions = db.scalars(select(ResearchTaskAnalysisVersion)).all()
            self.assertEqual([version.id for version in versions], [self.other_version_id])
        self.assertEqual(self._get(self.other_version_id, task_id=self.other_id).status_code, 200)
