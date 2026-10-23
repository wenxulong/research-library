"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestResearchTaskRevisionAPI(unittest.TestCase):
    """真实SQLite取证与接口调用，仅模拟DeepSeek返回。"""

    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        with TestingSessionLocal() as db:
            paper = Paper(title="修订测试论文", author="作者", year=2025, notes="")
            task = ResearchTask(title="修订测试", question="如何降低成本？", status="completed")
            db.add_all([paper, task])
            db.flush()
            document = PaperDocument(paper_id=paper.id, original_name="test.pdf", stored_name="test.pdf",
                                     file_path="/unused/test.pdf", content_type="application/pdf", file_size=10)
            db.add(document)
            db.flush()
            chunk = PaperChunk(document_id=document.id, page_number=5, chunk_index=0, text="数据集A成本降低20%。", char_count=13)
            db.add(chunk)
            db.flush()
            self.task_id, self.chunk_id = task.id, chunk.id
            analysis = ResearchTaskAnalysisResponse(task_id=task.id, question=task.question,
                summary="数据集A上成本降低。[资料1]", methods=[], experiments=[],
                conclusions=["所有数据集成本降低。[资料1]"], limitations=["当前资料不足。"],
                sources=[AnalysisSourceResponse(paper_id=paper.id, paper_title=paper.title, chunk_id=chunk.id, page_number=5, score=0.9)])
            review = ResearchTaskReviewResponse(task_id=task.id, items=[
                ReviewItemResponse(field="summary", claim=analysis.summary, verdict="supported", reason="支持", source_numbers=[1]),
                ReviewItemResponse(field="conclusions", claim=analysis.conclusions[0], verdict="partially_supported", reason="只能支持数据集A。", source_numbers=[1]),
                ReviewItemResponse(field="limitations", claim=analysis.limitations[0], verdict="insufficient_evidence", reason="无原文", source_numbers=[]),
            ])
            task.result, task.review_result = analysis.model_dump_json(), review.model_dump_json()
            self.original = (task.result, task.review_result, task.status)
            db.commit()
        self.answer = json.dumps(dict(revised_claim="数据集A成本降低20%。[资料1]", reason="限定到数据集A。"))

    def test_success_preserves_analysis_and_review(self):
        with patch('research_library.agents.revision_agent.generate_text', return_value=self.answer) as generate:
            response = client.post(f"/research-tasks/{self.task_id}/revisions")
        self.assertEqual(response.status_code, 200, response.text)
        generate.assert_called_once()
        self.assertIn("数据集A成本降低20%。", generate.call_args.kwargs["user_prompt"])
        data = response.json()
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["source_numbers"], [1])
        self.assertEqual(len(data["skipped_items"]), 1)
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual((task.result, task.review_result, task.status), self.original)

    def test_missing_task_returns_404(self):
        with patch('research_library.agents.revision_agent.generate_text') as generate:
            response = client.post("/research-tasks/99999/revisions")
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()

    def test_missing_saved_inputs_return_404(self):
        for field in ("result", "review_result"):
            with self.subTest(field=field):
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    task.result, task.review_result = self.original[:2]
                    setattr(task, field, None)
                    db.commit()
                with patch('research_library.agents.revision_agent.generate_text') as generate:
                    response = client.post(f"/research-tasks/{self.task_id}/revisions")
                self.assertEqual(response.status_code, 404)
                generate.assert_not_called()

    def test_stale_later_review_item_returns_409_without_model_call(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            review = json.loads(task.review_result)
            review["items"][2]["claim"] = "旧分析中的陈述"
            task.review_result = json.dumps(review)
            db.commit()
        with patch('research_library.agents.revision_agent.generate_text', return_value=self.answer) as generate:
            response = client.post(f"/research-tasks/{self.task_id}/revisions")
        self.assertEqual(response.status_code, 409)
        generate.assert_not_called()

    def test_deleted_evidence_returns_409(self):
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id))
            db.commit()
        with patch('research_library.agents.revision_agent.generate_text') as generate:
            response = client.post(f"/research-tasks/{self.task_id}/revisions")
        self.assertEqual(response.status_code, 409)
        generate.assert_not_called()

    def test_model_errors_return_502_and_preserve_saved_inputs(self):
        for answer in (RuntimeError("模型连接失败"), "not JSON", json.dumps(dict(revised_claim="错误引用。[资料2]", reason="原因"))):
            with self.subTest(answer=answer):
                options = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
                with patch('research_library.agents.revision_agent.generate_text', **options):
                    response = client.post(f"/research-tasks/{self.task_id}/revisions")
                self.assertEqual(response.status_code, 502)
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    self.assertEqual((task.result, task.review_result, task.status), self.original)


    def test_combined_revision_and_recheck_preserves_saved_inputs(self):
        checked = json.dumps(dict(verdict="supported", reason="修改后的范围与原文一致。", source_numbers=[1]))
        with (
            patch('research_library.agents.revision_agent.generate_text', side_effect=[self.answer, json.dumps(dict(verdict="appropriate", reason="适合结论字段。", suggested_field=None))]) as revise,
            patch('research_library.agents.review_agent.generate_text', return_value=checked) as recheck,
        ):
            response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(revise.call_count, 2)
        recheck.assert_called_once()
        data = response.json()
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(len(data["skipped_items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["review"]["claim"], item["revision"]["revised_claim"])
        self.assertEqual(item["review"]["verdict"], "supported")
        self.assertEqual(item["field_check"]["verdict"], "appropriate")
        self.assertIn(item["revision"]["revised_claim"], recheck.call_args.kwargs["user_prompt"])
        self.assertIn("数据集A成本降低20%。", recheck.call_args.kwargs["user_prompt"])
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual((task.result, task.review_result, task.status), self.original)

    def test_combined_returns_remaining_partial_support_without_retry_loop(self):
        checked = json.dumps(dict(verdict="partially_supported", reason="仍有部分表述无依据。", source_numbers=[1]))
        with (
            patch('research_library.agents.revision_agent.generate_text', side_effect=[self.answer, json.dumps(dict(verdict="appropriate", reason="适合结论字段。", suggested_field=None))]) as revise,
            patch('research_library.agents.review_agent.generate_text', return_value=checked) as recheck,
        ):
            response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["review"]["verdict"], "partially_supported")
        self.assertEqual(revise.call_count, 2)
        recheck.assert_called_once()

    def test_combined_recheck_errors_return_502_and_preserve_saved_inputs(self):
        for answer in (RuntimeError("再次审校连接失败"), "invalid JSON",
                       json.dumps(dict(verdict="supported", reason="错误引用", source_numbers=[2]))):
            with self.subTest(answer=answer):
                options = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
                with (
                    patch('research_library.agents.revision_agent.generate_text', return_value=self.answer),
                    patch('research_library.agents.review_agent.generate_text', **options),
                ):
                    response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
                self.assertEqual(response.status_code, 502)
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    self.assertEqual((task.result, task.review_result, task.status), self.original)

    def test_combined_rejects_stale_review_before_any_model_call(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            data = json.loads(task.review_result)
            data["items"][2]["claim"] = "旧的陈述"
            task.review_result = json.dumps(data)
            db.commit()
        with patch('research_library.agents.revision_agent.generate_text') as revise, patch('research_library.agents.review_agent.generate_text') as recheck:
            response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code, 409)
        revise.assert_not_called()
        recheck.assert_not_called()

    def test_combined_no_revision_needed_does_not_call_models(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            data = json.loads(task.review_result)
            data["items"][1]["verdict"] = "supported"
            task.review_result = json.dumps(data)
            db.commit()
        with patch('research_library.agents.revision_agent.generate_text') as revise, patch('research_library.agents.review_agent.generate_text') as recheck:
            response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(len(response.json()["skipped_items"]), 1)
        revise.assert_not_called()
        recheck.assert_not_called()


    def test_supported_evidence_can_still_have_misplaced_field(self):
        checked = json.dumps(dict(verdict="supported", reason="原文支持。", source_numbers=[1]))
        category = json.dumps(dict(verdict="misplaced", reason="这是具体实验测量结果。", suggested_field="experiments"))
        with (
            patch('research_library.agents.revision_agent.generate_text', side_effect=[self.answer, category]) as generate,
            patch('research_library.agents.review_agent.generate_text', return_value=checked),
        ):
            response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["review"]["verdict"], "supported")
        self.assertEqual(item["field_check"]["verdict"], "misplaced")
        self.assertEqual(item["field_check"]["suggested_field"], "experiments")
        self.assertEqual(generate.call_count, 2)
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual((task.result, task.review_result, task.status), self.original)

    def test_category_failure_returns_502_without_overwriting_saved_results(self):
        checked = json.dumps(dict(verdict="supported", reason="原文支持。", source_numbers=[1]))
        for answer in ("invalid JSON", RuntimeError("分类模型连接失败")):
            with self.subTest(answer=answer):
                with (
                    patch('research_library.agents.revision_agent.generate_text', side_effect=[self.answer, answer]),
                    patch('research_library.agents.review_agent.generate_text', return_value=checked),
                ):
                    response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
                self.assertEqual(response.status_code, 502)
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    self.assertEqual((task.result, task.review_result, task.status), self.original)


    def test_mixed_branch_splits_and_checks_each_part(self):
        mixed = json.dumps(dict(verdict="mixed", reason="含实验结果与边界说明。", suggested_field=None))
        appropriate = json.dumps(dict(verdict="appropriate", reason="分类合适。", suggested_field=None))
        supported = json.dumps(dict(verdict="supported", reason="原文支持。", source_numbers=[1]))
        insufficient = json.dumps(dict(verdict="insufficient_evidence", reason="原文不足以确认。", source_numbers=[1]))
        split_answer = json.dumps(dict(reason="分开结果和边界。", items=[
            dict(field="experiments", claim="数据集A成本下降20%。[资料1]"),
            dict(field="limitations", claim="当前原文不足以确认其他数据集的结果。[资料1]"),
        ]))
        with (
            patch('research_library.agents.revision_agent.generate_text', side_effect=[self.answer, mixed, appropriate, appropriate]) as generate,
            patch('research_library.agents.review_agent.generate_text', side_effect=[supported, supported, insufficient]) as check,
            patch('research_library.services.field_splitter.generate_text', return_value=split_answer) as splitter,
        ):
            response=client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code,200,response.text)
        item=response.json()["items"][0]
        self.assertEqual(item["field_check"]["verdict"],"mixed")
        self.assertEqual([i["item"]["field"] for i in item["split_checks"]],["experiments","limitations"])
        self.assertEqual([i["ready"] for i in item["split_checks"]],[True,False])
        self.assertEqual(item["split_result"]["source_claim"],item["revision"]["revised_claim"])
        self.assertEqual(generate.call_count,4)
        self.assertEqual(check.call_count,3)
        splitter.assert_called_once()
        with TestingSessionLocal() as db:
            task=db.get(ResearchTask,self.task_id)
            self.assertEqual((task.result,task.review_result,task.status),self.original)

    def test_invalid_split_returns_502_and_keeps_saved_inputs(self):
        mixed=json.dumps(dict(verdict="mixed",reason="混合内容",suggested_field=None))
        supported=json.dumps(dict(verdict="supported",reason="支持",source_numbers=[1]))
        with (
            patch('research_library.agents.revision_agent.generate_text',side_effect=[self.answer,mixed]),
            patch('research_library.agents.review_agent.generate_text',return_value=supported),
            patch('research_library.services.field_splitter.generate_text',return_value="invalid JSON"),
        ):
            response=client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code,502)
        with TestingSessionLocal() as db:
            task=db.get(ResearchTask,self.task_id)
            self.assertEqual((task.result,task.review_result,task.status),self.original)


    def test_evidence_note_field_move_is_preview_only(self):
        revised=json.dumps(dict(revised_claim="当前引用原文不足以确认所有数据集的结果。[资料1]",reason="限定为当前证据"))
        misplaced=json.dumps(dict(verdict="misplaced",reason="证据不足应作为备注",suggested_field="evidence_notes"))
        appropriate=json.dumps(dict(verdict="appropriate",reason="这是证据备注",suggested_field=None))
        supported=json.dumps(dict(verdict="supported",reason="仅说明当前片段的边界",source_numbers=[1]))
        with (
            patch('research_library.agents.revision_agent.generate_text',side_effect=[revised,misplaced,appropriate]),
            patch('research_library.agents.review_agent.generate_text',return_value=supported) as check,
        ):
            response=client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code,200,response.text)
        item=response.json()["items"][0]
        self.assertEqual(item["field_check"]["suggested_field"],"evidence_notes")
        self.assertEqual(item["field_move"]["item"]["field"],"evidence_notes")
        self.assertEqual(item["field_move"]["source_field"],"conclusions")
        self.assertTrue(item["field_move"]["ready"])
        self.assertEqual(check.call_count,2)
        with TestingSessionLocal() as db:
            task=db.get(ResearchTask,self.task_id)
            self.assertEqual((task.result,task.review_result,task.status),self.original)
