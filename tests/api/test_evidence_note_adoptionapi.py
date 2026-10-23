"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestEvidenceNoteAdoptionAPI(unittest.TestCase):
    """通过真实接口读写测试数据库，只模拟大模型的返回内容。"""

    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

        self.old_claim = "论文没有进行生产环境验证。[资料1]"
        self.new_claim = "当前引用原文未提供生产环境验证信息。[资料1]"
        self.evidence_text = "实验采用离线请求轨迹，在单台机器上测量缓存命中率。"

        with TestingSessionLocal() as db:
            paper = Paper(title="采纳测试论文", author="测试作者", year=2025, notes="")
            task = ResearchTask(title="采纳测试", question="有哪些评估边界？", status="completed")
            db.add_all([paper, task])
            db.flush()

            document = PaperDocument(
                paper_id=paper.id, original_name="adoption.pdf", stored_name="adoption.pdf",
                file_path="/unused/adoption.pdf", content_type="application/pdf", file_size=10,
            )
            db.add(document)
            db.flush()

            first_chunk = PaperChunk(
                document_id=document.id, page_number=1, chunk_index=0,
                text=self.evidence_text, char_count=len(self.evidence_text),
            )
            second_chunk = PaperChunk(
                document_id=document.id, page_number=2, chunk_index=0,
                text="实验测量了缓存命中率。", char_count=12,
            )
            db.add_all([first_chunk, second_chunk])
            db.flush()
            self.task_id = task.id
            self.chunk_id = first_chunk.id

            analysis = ResearchTaskAnalysisResponse(
                task_id=task.id, question=task.question,
                summary="研究离线缓存评估。[资料1]",
                methods=[], experiments=["实验测量缓存命中率。[资料2]"],
                conclusions=[], limitations=[self.old_claim],
                evidence_notes=["已有证据备注。[资料2]"],
                sources=[
                    AnalysisSourceResponse(paper_id=paper.id, paper_title=paper.title,
                                           chunk_id=first_chunk.id, page_number=1, score=0.9),
                    AnalysisSourceResponse(paper_id=paper.id, paper_title=paper.title,
                                           chunk_id=second_chunk.id, page_number=2, score=0.8),
                ],
            )
            review = ResearchTaskReviewResponse(task_id=task.id, items=[
                ReviewItemResponse(field="limitations", claim=self.old_claim,
                                   verdict="partially_supported", reason="只能说明当前原文范围。",
                                   source_numbers=[1]),
            ])
            task.result = analysis.model_dump_json()
            task.review_result = review.model_dump_json()
            self.original = (task.result, task.review_result, task.status)
            db.commit()

        self.payload = {
            "base_analysis": analysis.model_dump(),
            "revision": {
                "field": "limitations", "original_claim": self.old_claim,
                "revised_claim": self.new_claim, "reason": "限定为当前引用原文。",
                "source_numbers": [1],
            },
        }
        self.support = json.dumps({
            "verdict": "supported", "reason": "只描述提供片段的信息范围。", "source_numbers": [1],
        })
        self.category = json.dumps({
            "verdict": "appropriate", "reason": "适合证据备注。", "suggested_field": None,
        })
        self.url = f"/research-tasks/{self.task_id}/analysis/adopt-evidence-note"

    def _post(self, support=None, category=None, payload=None, url=None):
        """替换模型调用，其余取证、检查、接口和保存逻辑照常执行。"""
        with (
            patch('research_library.agents.review_agent.generate_text', return_value=self.support if support is None else support) as review,
            patch('research_library.agents.revision_agent.generate_text', return_value=self.category if category is None else category) as classify,
        ):
            response = client.post(self.url if url is None else url,
                                   json=self.payload if payload is None else payload)
        return response, review, classify

    def _assert_unchanged(self, expected=None):
        """失败时，任务应保持原样，也不能留下半条版本记录。"""
        from sqlalchemy import select
        from research_library.db.model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual((task.result, task.review_result, task.status),
                             self.original if expected is None else expected)
            versions = db.scalars(select(ResearchTaskAnalysisVersion)).all()
            self.assertEqual(versions, [])

    def test_success_moves_text_preserves_sources_and_saves_history(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        response, review, classify = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        analysis = result["analysis"]
        self.assertEqual(result["task_id"], self.task_id)
        self.assertEqual(analysis["limitations"], [])
        self.assertEqual(analysis["evidence_notes"], ["已有证据备注。[资料2]", self.new_claim])
        for field in ("summary", "methods", "experiments", "conclusions", "sources", "question", "task_id"):
            self.assertEqual(analysis[field], self.payload["base_analysis"][field])
        review.assert_called_once()
        classify.assert_called_once()
        prompt = review.call_args.kwargs["user_prompt"]
        self.assertIn(self.new_claim, prompt)
        self.assertIn(self.evidence_text, prompt)
        self.assertNotIn("实验测量了缓存命中率。", prompt)
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            version = db.get(ResearchTaskAnalysisVersion, result["version_id"])
            self.assertEqual(json.loads(task.result), analysis)
            self.assertIsNone(task.review_result)
            self.assertEqual(task.status, "completed")
            self.assertEqual(version.task_id, self.task_id)
            self.assertEqual(version.previous_analysis, self.original[0])
            self.assertEqual(version.previous_review, self.original[1])
            self.assertEqual(json.loads(version.updated_analysis), analysis)
            self.assertIn("limitations", version.reason)
            self.assertIsNotNone(version.created_at)
        saved = client.get(f"/research-tasks/{self.task_id}/analysis")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), analysis)
        versions = client.get(f"/research-tasks/{self.task_id}/analysis/versions")
        self.assertEqual(versions.status_code, 200, versions.text)
        self.assertEqual([row["id"] for row in versions.json()], [result["version_id"]])
        detail = client.get(f"/research-tasks/{self.task_id}/analysis/versions/{result['version_id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["previous_analysis"], json.loads(self.original[0]))
        self.assertEqual(detail.json()["updated_analysis"], analysis)
        self.assertEqual(detail.json()["previous_review"], json.loads(self.original[1]))
        self.assertEqual(client.get(f"/research-tasks/{self.task_id}/review").status_code, 404)

    def test_success_without_previous_review(self):
        from research_library.db.model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).review_result = None
            db.commit()
        response, _, _ = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        with TestingSessionLocal() as db:
            version = db.get(ResearchTaskAnalysisVersion, response.json()["version_id"])
            self.assertIsNone(version.previous_review)
            self.assertIsNone(db.get(ResearchTask, self.task_id).review_result)

    def test_partial_support_cannot_be_adopted(self):
        answer = json.dumps(dict(verdict="partially_supported", reason="只有部分内容有依据。", source_numbers=[1]))
        response, _, _ = self._post(support=answer)
        self.assertEqual(response.status_code, 409, response.text)
        self._assert_unchanged()

    def test_wrong_category_cannot_be_adopted(self):
        answer = json.dumps(dict(verdict="misplaced", reason="属于实验。", suggested_field="experiments"))
        response, _, _ = self._post(category=answer)
        self.assertEqual(response.status_code, 409, response.text)
        self._assert_unchanged()

    def test_stale_analysis_is_rejected_before_model_calls(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            analysis = json.loads(task.result)
            analysis["summary"] = "其他操作更新的概述。[资料1]"
            task.result = json.dumps(analysis)
            expected = (task.result, task.review_result, task.status)
            db.commit()
        response, review, classify = self._post()
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged(expected)

    def test_duplicate_adoption_creates_only_one_version(self):
        from sqlalchemy import select
        from research_library.db.model import ResearchTaskAnalysisVersion
        first, _, _ = self._post()
        self.assertEqual(first.status_code, 200, first.text)
        second, review, classify = self._post()
        self.assertEqual(second.status_code, 409, second.text)
        review.assert_not_called()
        classify.assert_not_called()
        with TestingSessionLocal() as db:
            self.assertEqual(len(db.scalars(select(ResearchTaskAnalysisVersion)).all()), 1)
            self.assertEqual(json.loads(db.get(ResearchTask, self.task_id).result), first.json()["analysis"])

    def test_nonexistent_or_duplicate_original_claim_is_rejected(self):
        for claims in ([], [self.old_claim, self.old_claim]):
            with self.subTest(claims=claims):
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    analysis = json.loads(self.original[0])
                    analysis["limitations"] = claims
                    task.result = json.dumps(analysis)
                    expected = (task.result, task.review_result, task.status)
                    db.commit()
                payload = dict(self.payload, base_analysis=analysis)
                response, review, classify = self._post(payload=payload)
                self.assertEqual(response.status_code, 409, response.text)
                review.assert_not_called()
                classify.assert_not_called()
                self._assert_unchanged(expected)

    def test_existing_note_is_not_added_again(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            analysis = json.loads(task.result)
            analysis["evidence_notes"].append(self.new_claim)
            task.result = json.dumps(analysis)
            expected = (task.result, task.review_result, task.status)
            db.commit()
        response, review, classify = self._post(payload=dict(self.payload, base_analysis=analysis))
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged(expected)

    def test_summary_is_not_supported_by_this_endpoint(self):
        revision = dict(self.payload["revision"], field="summary",
                        original_claim=self.payload["base_analysis"]["summary"])
        response, review, classify = self._post(payload=dict(self.payload, revision=revision))
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged()

    def test_new_citation_is_rejected_before_model_calls(self):
        revision = dict(self.payload["revision"], revised_claim="当前资料不足。[资料2]", source_numbers=[2])
        response, review, classify = self._post(payload=dict(self.payload, revision=revision))
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged()

    def test_missing_evidence_prevents_adoption(self):
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id))
            db.commit()
        response, review, classify = self._post()
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged()

    def test_malformed_model_output_does_not_change_database(self):
        for answers in (("not JSON", self.category), (self.support, "not JSON")):
            with self.subTest(answers=answers):
                response, _, _ = self._post(support=answers[0], category=answers[1])
                self.assertEqual(response.status_code, 502, response.text)
                self._assert_unchanged()

    def test_model_connection_failure_does_not_change_database(self):
        with patch('research_library.agents.review_agent.generate_text', side_effect=RuntimeError("模拟模型连接失败")):
            response = client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 502, response.text)
        self._assert_unchanged()

    def test_failed_commit_rolls_back_flushed_version_and_analysis(self):
        with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("模拟提交失败")) as commit:
            response, _, _ = self._post()
        self.assertEqual(response.status_code, 502, response.text)
        commit.assert_called_once()
        self._assert_unchanged()

    def test_analysis_changed_during_check_is_not_overwritten(self):
        original_move = revision_agent.move_revision_to_evidence_notes
        expected = []

        def change_analysis(*args, **kwargs):
            checked = original_move(*args, **kwargs)
            with TestingSessionLocal() as db:
                task = db.get(ResearchTask, self.task_id)
                analysis = json.loads(task.result)
                analysis["summary"] = "检查期间保存的另一份分析。[资料1]"
                task.result = json.dumps(analysis)
                expected.append((task.result, task.review_result, task.status))
                db.commit()
            return checked

        with patch('research_library.agents.revision_agent.move_revision_to_evidence_notes', side_effect=change_analysis):
            response, _, _ = self._post()
        self.assertEqual(response.status_code, 409, response.text)
        self._assert_unchanged(expected[0])

    def test_missing_task_and_missing_analysis_do_not_call_models(self):
        response, review, classify = self._post(
            url="/research-tasks/999999/analysis/adopt-evidence-note")
        self.assertEqual(response.status_code, 404, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            task.result = None
            expected = (task.result, task.review_result, task.status)
            db.commit()
        response, review, classify = self._post()
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged(expected)

    def test_invalid_request_returns_422_without_changing_database(self):
        response, review, classify = self._post(payload={"base_analysis": self.payload["base_analysis"]})
        self.assertEqual(response.status_code, 422, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged()

    def test_updated_analysis_can_be_reviewed_again(self):
        response, _, _ = self._post()
        self.assertEqual(response.status_code, 200, response.text)

        def review_current_text(*, system_prompt, user_prompt, json_mode=False):
            number = 2 if "[资料2]" in user_prompt else 1
            return json.dumps(dict(verdict="supported", reason="测试当前正文的审校流程。", source_numbers=[number]))

        with patch('research_library.agents.review_agent.generate_text', side_effect=review_current_text) as review:
            reviewed = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(review.call_count, 4)
        notes = [item for item in reviewed.json()["items"] if item["field"] == "evidence_notes"]
        self.assertEqual([item["claim"] for item in notes], ["已有证据备注。[资料2]", self.new_claim])
        self.assertNotIn(self.old_claim, [item["claim"] for item in reviewed.json()["items"]])
        saved = client.get(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), reviewed.json())

    def test_adopt_then_restore_returns_original_analysis_and_preserves_both_versions(self):
        from sqlalchemy import select
        from research_library.db.model import ResearchTaskAnalysisVersion
        adopted, _, _ = self._post()
        self.assertEqual(adopted.status_code, 200, adopted.text)
        adopted_data = adopted.json()
        with patch('research_library.agents.review_agent.generate_text') as review, patch('research_library.agents.revision_agent.generate_text') as revise:
            restored = client.post(
                f"/research-tasks/{self.task_id}/analysis/versions/{adopted_data['version_id']}/restore",
                json={"base_analysis": adopted_data["analysis"]},
            )
        self.assertEqual(restored.status_code, 200, restored.text)
        review.assert_not_called()
        revise.assert_not_called()
        data = restored.json()
        self.assertEqual(data["analysis"], self.payload["base_analysis"])
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual(json.loads(task.result), self.payload["base_analysis"])
            self.assertIsNone(task.review_result)
            original_version = db.get(ResearchTaskAnalysisVersion, adopted_data["version_id"])
            self.assertEqual(original_version.previous_analysis, self.original[0])
            self.assertEqual(original_version.previous_review, self.original[1])
            new_version = db.get(ResearchTaskAnalysisVersion, data["version_id"])
            self.assertEqual(json.loads(new_version.previous_analysis), adopted_data["analysis"])
            self.assertEqual(json.loads(new_version.updated_analysis), self.payload["base_analysis"])
            self.assertIsNone(new_version.previous_review)
            self.assertEqual(len(db.scalars(select(ResearchTaskAnalysisVersion)).all()), 2)
        detail = client.get(f"/research-tasks/{self.task_id}/analysis/versions/{data['version_id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["updated_analysis"], self.payload["base_analysis"])
