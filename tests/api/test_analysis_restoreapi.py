"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestAnalysisRestoreAPI(unittest.TestCase):
    """恢复接口读写独立测试数据库，验证历史保留和事务回滚。"""

    def setUp(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        with TestingSessionLocal() as db:
            task = ResearchTask(title="恢复测试", question="研究问题", status="completed")
            other = ResearchTask(title="其他任务", question="其他问题", status="completed")
            db.add_all([task, other])
            db.flush()
            self.task_id, self.other_id = task.id, other.id
            before = ResearchTaskAnalysisResponse(
                task_id=task.id, question=task.question, summary="历史概述", methods=["历史方法"],
                experiments=[], conclusions=[], limitations=["历史边界"], evidence_notes=[], sources=[],
            )
            current = before.model_copy(deep=True)
            current.summary = "当前概述"
            current.methods = ["当前方法"]
            current.limitations = []
            current.evidence_notes = ["当前证据备注"]
            old_review = ResearchTaskReviewResponse(task_id=task.id, items=[
                ReviewItemResponse(field="summary", claim=before.summary, verdict="insufficient_evidence",
                                   reason="历史审校", source_numbers=[]),
            ])
            current_review = ResearchTaskReviewResponse(task_id=task.id, items=[
                ReviewItemResponse(field="summary", claim=current.summary, verdict="insufficient_evidence",
                                   reason="当前审校", source_numbers=[]),
            ])
            task.result = current.model_dump_json()
            task.review_result = current_review.model_dump_json()
            version = ResearchTaskAnalysisVersion(
                task_id=task.id, previous_analysis=before.model_dump_json(),
                updated_analysis=current.model_dump_json(), previous_review=old_review.model_dump_json(),
                reason="原始修改记录",
            )
            other_analysis = before.model_copy(update={"task_id": other.id, "question": other.question})
            other_version = ResearchTaskAnalysisVersion(
                task_id=other.id, previous_analysis=other_analysis.model_dump_json(),
                updated_analysis=other_analysis.model_dump_json(), previous_review=None, reason="其他任务的历史",
            )
            db.add_all([version, other_version])
            db.flush()
            self.version_id, self.other_version_id = version.id, other_version.id
            self.before, self.current = before.model_dump(), current.model_dump()
            self.current_json, self.current_review_json = task.result, task.review_result
            self.historical_json = version.previous_analysis
            db.commit()
        self.payload = {"base_analysis": self.current}
        self.url = f"/research-tasks/{self.task_id}/analysis/versions/{self.version_id}/restore"

    def _snapshot(self):
        from sqlalchemy import select
        from research_library.db.model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            tasks = tuple((task.id, task.title, task.question, task.result, task.review_result, task.status)
                          for task in db.scalars(select(ResearchTask).order_by(ResearchTask.id)))
            versions = tuple((version.id, version.task_id, version.previous_analysis, version.updated_analysis,
                              version.previous_review, version.reason, version.created_at)
                             for version in db.scalars(select(ResearchTaskAnalysisVersion).order_by(ResearchTaskAnalysisVersion.id)))
            return tasks, versions

    def _post(self, payload=None, task_id=None, version_id=None):
        """恢复不应调用大模型，即使误接模型调用也能在测试中发现。"""
        task_id = self.task_id if task_id is None else task_id
        version_id = self.version_id if version_id is None else version_id
        with (
            patch('research_library.agents.paper_analysis_agent.generate_text') as analyze,
            patch('research_library.agents.review_agent.generate_text') as review,
            patch('research_library.agents.revision_agent.generate_text') as revise,
        ):
            response = client.post(f"/research-tasks/{task_id}/analysis/versions/{version_id}/restore",
                                   json=self.payload if payload is None else payload)
        analyze.assert_not_called()
        review.assert_not_called()
        revise.assert_not_called()
        return response

    def _assert_rejected_without_changes(self, status_code, **kwargs):
        before = self._snapshot()
        response = self._post(**kwargs)
        self.assertEqual(response.status_code, status_code, response.text)
        self.assertEqual(self._snapshot(), before)
        return response

    def test_restore_saves_new_history_clears_review_and_preserves_old_records(self):
        from sqlalchemy import select
        from research_library.db.model import ResearchTaskAnalysisVersion
        old_versions = self._snapshot()[1]
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["task_id"], self.task_id)
        self.assertEqual(result["restored_from_version_id"], self.version_id)
        self.assertNotIn(result["version_id"], [self.version_id, self.other_version_id])
        self.assertEqual(result["analysis"], self.before)
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual(json.loads(task.result), self.before)
            self.assertIsNone(task.review_result)
            self.assertEqual(task.status, "completed")
            history = db.get(ResearchTaskAnalysisVersion, result["version_id"])
            self.assertEqual(history.previous_analysis, self.current_json)
            self.assertEqual(json.loads(history.updated_analysis), self.before)
            # 本次历史保存的是恢复前的当前审校，而不是所选旧版本的历史审校。
            self.assertEqual(history.previous_review, self.current_review_json)
            self.assertEqual(history.reason, f"恢复版本 {self.version_id} 的修改前分析")
            self.assertIsNotNone(history.created_at)
            self.assertEqual(len(db.scalars(select(ResearchTaskAnalysisVersion)).all()), 3)
        self.assertEqual(self._snapshot()[1][:2], old_versions)
        saved = client.get(f"/research-tasks/{self.task_id}/analysis")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), self.before)
        self.assertEqual(client.get(f"/research-tasks/{self.task_id}/review").status_code, 404)
        versions = client.get(f"/research-tasks/{self.task_id}/analysis/versions")
        self.assertEqual(versions.status_code, 200)
        self.assertEqual([row["id"] for row in versions.json()], [result["version_id"], self.version_id])
        detail = client.get(f"/research-tasks/{self.task_id}/analysis/versions/{result['version_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["previous_analysis"], self.current)
        self.assertEqual(detail.json()["updated_analysis"], self.before)
        self.assertEqual(detail.json()["previous_review"], json.loads(self.current_review_json))

    def test_restore_without_current_review_saves_null_in_history(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).review_result = None
            db.commit()
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        with TestingSessionLocal() as db:
            history = db.get(ResearchTaskAnalysisVersion, response.json()["version_id"])
            self.assertIsNone(history.previous_review)
            self.assertIsNone(db.get(ResearchTask, self.task_id).review_result)

    def test_stale_base_analysis_is_rejected_without_overwriting_new_content(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            current = json.loads(task.result)
            current["summary"] = "其他操作更新的内容"
            task.result = json.dumps(current)
            db.commit()
        response = self._assert_rejected_without_changes(409)
        self.assertIn("当前分析已变化", response.json()["detail"])

    def test_cannot_restore_version_owned_by_other_task(self):
        self._assert_rejected_without_changes(404, version_id=self.other_version_id)

    def test_missing_task_or_version_is_rejected_without_changes(self):
        for arguments in ({"task_id": 999999}, {"version_id": 999999}):
            with self.subTest(arguments=arguments):
                self._assert_rejected_without_changes(404, **arguments)

    def test_equal_target_is_rejected_without_creating_history(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            task.result = self.historical_json
            db.commit()
        self._assert_rejected_without_changes(409, payload={"base_analysis": self.before})

    def test_repeated_restore_does_not_create_another_history(self):
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        # 第二次用旧请求会过期，用最新正文再次恢复同一目标则无需恢复。
        self._assert_rejected_without_changes(409)
        self._assert_rejected_without_changes(409, payload={"base_analysis": response.json()["analysis"]})

    def test_malformed_current_or_target_json_is_rejected_without_changes(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        for target in ("current", "historical"):
            with self.subTest(target=target):
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    version = db.get(ResearchTaskAnalysisVersion, self.version_id)
                    task.result = self.current_json
                    version.previous_analysis = self.historical_json
                    if target == "current":
                        task.result = "这不是JSON"
                    else:
                        version.previous_analysis = "这不是JSON"
                    db.commit()
                self._assert_rejected_without_changes(409)

    def test_embedded_task_id_mismatch_is_rejected_without_changes(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        for target in ("current", "historical"):
            with self.subTest(target=target):
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    version = db.get(ResearchTaskAnalysisVersion, self.version_id)
                    task.result = self.current_json
                    version.previous_analysis = self.historical_json
                    if target == "current":
                        data = json.loads(task.result)
                        data["task_id"] = self.other_id
                        task.result = json.dumps(data)
                    else:
                        data = json.loads(version.previous_analysis)
                        data["task_id"] = self.other_id
                        version.previous_analysis = json.dumps(data)
                    db.commit()
                self._assert_rejected_without_changes(409)

    def test_legacy_target_defaults_notes_without_rewriting_original_history(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        legacy = dict(self.before)
        legacy.pop("evidence_notes")
        legacy_json = json.dumps(legacy)
        with TestingSessionLocal() as db:
            db.get(ResearchTaskAnalysisVersion, self.version_id).previous_analysis = legacy_json
            db.commit()
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["analysis"], self.before)
        with TestingSessionLocal() as db:
            self.assertEqual(db.get(ResearchTaskAnalysisVersion, self.version_id).previous_analysis, legacy_json)

    def test_missing_current_analysis_is_rejected_without_changes(self):
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).result = None
            db.commit()
        self._assert_rejected_without_changes(409)

    def test_invalid_request_returns_422_without_changes(self):
        for payload in ({}, {"base_analysis": {"summary": "缺少其他字段"}}):
            with self.subTest(payload=payload):
                self._assert_rejected_without_changes(422, payload=payload)

    def test_commit_failure_rolls_back_new_history_and_current_analysis(self):
        from sqlalchemy.exc import SQLAlchemyError
        before = self._snapshot()
        failure_client = TestClient(app, raise_server_exceptions=False)
        # 在 history 已经 flush、任务待更新之后，模拟实际提交失败。
        with patch("sqlalchemy.orm.Session.commit", side_effect=SQLAlchemyError("模拟数据库提交失败")) as commit:
            response = failure_client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 500, response.text)
        commit.assert_called_once()
        self.assertEqual(self._snapshot(), before)
        # 回滚后同样的请求还能正常执行，不留下半条历史或失效会话。
        retry = self._post()
        self.assertEqual(retry.status_code, 200, retry.text)

    def test_locked_query_refreshes_preloaded_task_before_checking_request(self):
        import research_library.services.analysis_restore as analysis_restore
        from research_library.schemas import AnalysisRestoreRequest
        with TestingSessionLocal() as cached_db:
            stale_task = cached_db.get(ResearchTask, self.task_id)
            self.assertEqual(stale_task.result, self.current_json)
            with TestingSessionLocal() as update_db:
                current = dict(self.current, summary="另一会话更新的概述")
                update_db.get(ResearchTask, self.task_id).result = json.dumps(current)
                update_db.commit()
            before = self._snapshot()
            with self.assertRaisesRegex(ValueError, "当前分析已变化"):
                analysis_restore.restore_analysis_version(
                    cached_db, self.task_id, self.version_id,
                    AnalysisRestoreRequest.model_validate(self.payload),
                )
            self.assertEqual(self._snapshot(), before)
