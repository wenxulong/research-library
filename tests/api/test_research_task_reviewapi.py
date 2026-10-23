"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestResearchTaskReviewAPI(unittest.TestCase):
    """使用SQLite测试完整取证和审校链路，仅替换DeepSeek请求。"""

    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        with TestingSessionLocal() as db:
            paper = Paper(title="测试论文", author="测试作者", year=2025, notes="")
            task = ResearchTask(title="审校测试", question="是否降低成本？")
            db.add_all([paper, task])
            db.flush()
            document = PaperDocument(
                paper_id=paper.id, original_name="test.pdf", stored_name="test.pdf",
                file_path="/unused/test.pdf", content_type="application/pdf", file_size=10,
            )
            db.add(document)
            db.flush()
            chunk = PaperChunk(document_id=document.id, page_number=5, chunk_index=0,
                               text="在数据集A上，服务成本降低了20%。", char_count=23)
            db.add(chunk)
            db.flush()
            self.task_id = task.id
            self.chunk_id = chunk.id
            analysis = ResearchTaskAnalysisResponse(
                task_id=task.id, question=task.question,
                summary="数据集A上成本下降。[资料1]", methods=[], experiments=[],
                conclusions=["所有数据集上成本下降。[资料1]"], limitations=["当前资料不足。"],
                sources=[AnalysisSourceResponse(
                    paper_id=paper.id, paper_title=paper.title, chunk_id=chunk.id,
                    page_number=5, score=0.9,
                )],
            )
            self.saved_result = analysis.model_dump_json()
            task.result = self.saved_result
            task.status = "completed"
            db.commit()

    def _set_result(self, result):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            task.result = result
            db.commit()

    def test_review_success_uses_database_evidence_and_preserves_analysis(self):
        answers = [
            json.dumps(dict(verdict="supported", reason="原文明确说明数据集A。", source_numbers=[1])),
            json.dumps(dict(verdict="partially_supported", reason="不能推广到所有数据集。", source_numbers=[1])),
        ]
        with patch('research_library.agents.review_agent.generate_text', side_effect=answers) as generate:
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["task_id"], self.task_id)
        self.assertEqual([i["verdict"] for i in data["items"]],
                         ["supported", "partially_supported", "insufficient_evidence"])
        self.assertEqual(generate.call_count, 2)
        for call in generate.call_args_list:
            self.assertIn("在数据集A上，服务成本降低了20%。", call.kwargs["user_prompt"])
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual(task.result, self.saved_result)
            self.assertEqual(task.status, "completed")
            self.assertEqual(json.loads(task.review_result), data)

    def test_missing_task_returns_404(self):
        with patch('research_library.agents.review_agent.generate_text') as generate:
            response = client.post("/research-tasks/99999/review")
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()

    def test_missing_analysis_returns_404(self):
        self._set_result(None)
        with patch('research_library.agents.review_agent.generate_text') as generate:
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()

    def test_invalid_saved_analysis_returns_409(self):
        self._set_result("旧的纯文本结果")
        with patch('research_library.agents.review_agent.generate_text') as generate:
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 409)
        generate.assert_not_called()

    def test_wrong_task_id_in_saved_analysis_returns_409(self):
        data = json.loads(self.saved_result)
        data["task_id"] = 99999
        self._set_result(json.dumps(data))
        with patch('research_library.agents.review_agent.generate_text') as generate:
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 409)
        generate.assert_not_called()

    def test_deleted_chunk_returns_409(self):
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id))
            db.commit()
        with patch('research_library.agents.review_agent.generate_text') as generate:
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 409)
        self.assertIn("文本块已经不存在", response.json()["detail"])
        generate.assert_not_called()

    def test_model_connection_failure_returns_502(self):
        with patch('research_library.agents.review_agent.generate_text', side_effect=RuntimeError("模型连接失败")):
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "模型连接失败")

    def test_invalid_model_json_returns_502(self):
        with patch('research_library.agents.review_agent.generate_text', return_value="invalid JSON"):
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 502)
        self.assertIn("正确的JSON", response.json()["detail"])


    def _save_review_fixture(self):
        review = {
            "task_id": self.task_id,
            "items": [{"field": "summary", "claim": "数据集A上成本下降。[资料1]",
                       "verdict": "supported", "reason": "原文支持。", "source_numbers": [1]}],
        }
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).review_result = json.dumps(review, ensure_ascii=False)
            db.commit()
        return review

    def test_get_saved_review_returns_without_model_call(self):
        expected = self._save_review_fixture()
        # 旧记录没有陈述编号，读取时应补默认空值，不生成随机编号。
        expected["items"][0]["claim_id"] = ""
        with patch('research_library.agents.review_agent.generate_text') as generate:
            response = client.get(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        generate.assert_not_called()

    def test_get_review_returns_404_when_not_saved(self):
        response = client.get(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 404)
        self.assertIn("还没有保存审校结果", response.json()["detail"])

    def test_get_review_returns_404_for_missing_task(self):
        self.assertEqual(client.get("/research-tasks/99999/review").status_code, 404)

    def test_get_review_rejects_corrupt_result(self):
        for value in ("not JSON", '{"task_id":1}', '{"task_id":1,"items":[{}]}'):
            with self.subTest(value=value):
                with TestingSessionLocal() as db:
                    db.get(ResearchTask, self.task_id).review_result = value
                    db.commit()
                response = client.get(f"/research-tasks/{self.task_id}/review")
                self.assertEqual(response.status_code, 409)
                self.assertIn("格式不正确", response.json()["detail"])

    def test_get_review_rejects_wrong_task_id(self):
        review = self._save_review_fixture()
        review["task_id"] = 99999
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).review_result = json.dumps(review)
            db.commit()
        response = client.get(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 409)
        self.assertIn("任务编号不匹配", response.json()["detail"])

    def test_new_analysis_clears_previous_review(self):
        self._save_review_fixture()
        analysis = ResearchTaskAnalysisResponse.model_validate_json(self.saved_result)
        analysis.summary = "新的分析。[资料1]"
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            import research_library.repositories.task_crud as task_crud
            task_crud.save_task_analysis(db, task, analysis)
        self.assertEqual(client.get(f"/research-tasks/{self.task_id}/review").status_code, 404)
        response = client.get(f"/research-tasks/{self.task_id}/analysis")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"], analysis.summary)

    def test_status_update_clears_review_only_when_result_changes(self):
        self._save_review_fixture()
        same = client.patch(f"/research-tasks/{self.task_id}/status", json={
            "status": "completed", "result": self.saved_result,
        })
        self.assertEqual(same.status_code, 200, same.text)
        self.assertEqual(client.get(f"/research-tasks/{self.task_id}/review").status_code, 200)
        changed = client.patch(f"/research-tasks/{self.task_id}/status", json={
            "status": "pending", "result": None,
        })
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(client.get(f"/research-tasks/{self.task_id}/review").status_code, 404)
