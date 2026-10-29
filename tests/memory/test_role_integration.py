"""规划、实验分析、初稿、扩写的真实HTTP链路；独立数据库和模拟模型。"""
from copy import deepcopy
from io import BytesIO
import json
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from research_library.db.model import ExperimentImport, ResearchTask
from research_library.memory.repository import MemoryRepository
from research_library.memory.schemas import MemoryCreate
from research_library.retrieval import embedding_service
import tests.fixtures.main as fixtures
from tests.services.test_paper_draft import DraftFixture


class RoleMemoryFixture(DraftFixture):
    def setUp(self):
        super().setUp()
        self.embed = self.enterContext(patch.object(
            embedding_service, "embed_texts", side_effect=lambda texts: [[1.0, 0.0] for _ in texts]))
        self.generate_plan.reset_mock()
        self.analysis_model.reset_mock()
        self.writer.reset_mock()

    def seed(self):
        with fixtures.TestingSessionLocal() as db:
            repository = MemoryRepository(db, "local-user")
            records = {}
            for kind, text in (("preference", "写作偏好标记：术语首次出现解释"),
                               ("constraint", "历史约束标记：只有旧设备"),
                               ("experience", "历史经验标记：检查负载和质量损失")):
                record = repository.add(MemoryCreate(
                    research_task_id=self.task_id, kind=kind, content=text, origin="model_summary"))
                records[kind] = record.id
            other_task = ResearchTask(title="其他任务", question="其他问题")
            db.add(other_task)
            db.commit()
            for repository, task_id, text in (
                (repository, other_task.id, "其他任务标记"),
                (MemoryRepository(db, "other-user"), self.task_id, "其他用户标记"),
            ):
                repository.add(MemoryCreate(research_task_id=task_id, kind="experience",
                                            content=text, origin="model_summary"))
            self.set_id = db.get(ExperimentImport, self.import_id).question_set_id
        return records

    def assert_scope(self, prompt):
        self.assertIn("历史经验标记", prompt)
        self.assertNotIn("其他任务标记", prompt)
        self.assertNotIn("其他用户标记", prompt)

    def saved_draft(self):
        response = fixtures.client.post(self.base, json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class TestRoleMemoryIntegration(RoleMemoryFixture, unittest.TestCase):
    def test_planning_reads_constraints_and_experience_but_keeps_current_conditions(self):
        self.seed()
        plan = self.plan(self.set_id, equipment="新设备：四卡服务器")
        prompt = self.generate_plan.call_args.kwargs["user_prompt"]
        self.assert_scope(prompt)
        self.assertIn("历史约束标记", prompt)
        self.assertNotIn("写作偏好标记", prompt)
        self.assertIn("新设备：四卡服务器", prompt)
        self.assertEqual(plan["conditions"]["equipment"], "新设备：四卡服务器")
        self.assertIn("本次填写的实际研究条件优先", self.generate_plan.call_args.kwargs["system_prompt"])

    def test_analysis_uses_experience_without_changing_statistics_or_snapshot(self):
        self.seed()
        before = fixtures.client.get(f"/experiment-imports/{self.import_id}").json()
        response = fixtures.client.post(f"/experiment-imports/{self.import_id}/analysis", json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        prompt = self.analysis_model.call_args.kwargs["user_prompt"]
        self.assert_scope(prompt)
        self.assertNotIn("写作偏好标记", prompt)
        self.assertNotIn("历史约束标记", prompt)
        supplied = json.loads(prompt.split("程序计算的统计结果：\n")[1].split("\n\n辅助历史记忆")[0])
        self.assertEqual(supplied, response.json()["statistics"])
        self.assertEqual(supplied[0]["groups"][0]["mean"], 0.09)
        self.assertEqual(fixtures.client.get(f"/experiment-imports/{self.import_id}").json(), before)
        self.assertIn("历史异常不代表本次也存在相同异常", self.analysis_model.call_args.kwargs["system_prompt"])

    def test_draft_includes_writing_preferences_and_experience_without_new_citations(self):
        self.seed()
        saved = self.saved_draft()
        prompt = self.writer.call_args.kwargs["user_prompt"]
        self.assert_scope(prompt)
        memory_part = prompt.split("辅助写作记忆：\n")[1].split("\n\n输出JSON Schema：")[0]
        self.assertIn("写作偏好标记", memory_part)
        self.assertNotIn("历史约束标记", memory_part)
        self.assertEqual(saved["content"], self.payload)
        self.assertEqual([item["source_number"] for item in saved["evidence"]], [10, 13, 16])
        self.assertIn("不得写入source_numbers", self.writer.call_args.kwargs["system_prompt"])

    def test_empty_memory_works_through_saved_draft_and_download(self):
        saved = self.saved_draft()
        self.assertIn("没有与本次写作相关的历史记忆", self.writer.call_args.kwargs["user_prompt"])
        self.embed.assert_not_called()
        response = fixtures.client.get(f"/paper-drafts/{saved['id']}/export")
        self.assertEqual(response.status_code, 200, response.text)
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(json.loads(archive.read("draft.json"))["content"], saved["content"])

    def test_direct_expansion_loads_fresh_memory_and_waits_for_user_save(self):
        saved = self.saved_draft()
        records = self.seed()
        self.writer.return_value = json.dumps({"paragraphs": [{"text": "扩写正文", "source_numbers": [19]}]})
        response = fixtures.client.post(f"/paper-drafts/{saved['id']}/expand", json={
            "section": "introduction", "instruction": "详细说明背景"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = json.loads(self.writer.call_args.kwargs["user_prompt"])
        ids = {m["memory_id"] for m in payload["writing_memory"]["memories"]}
        self.assertEqual(ids, {records["preference"], records["experience"]})
        self.assertEqual(payload["instruction"], "详细说明背景")
        self.assertEqual(fixtures.client.get(f"/paper-drafts/{saved['id']}").json(), saved)

    def test_skill_route_keeps_router_small_and_passes_memory_to_writer(self):
        saved = self.saved_draft()
        self.seed()
        self.writer.return_value = json.dumps({"paragraphs": [{"text": "扩写正文", "source_numbers": [19]}]})
        with patch("research_library.agents.paper_writing_skill.generate_text", return_value=json.dumps({
            "action": "expand_section", "section": "methods", "instruction": "补充方法步骤"})) as router:
            response = fixtures.client.post(f"/paper-drafts/{saved['id']}/skill", json={"instruction": "扩写方法"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("writing_memory", json.loads(router.call_args.kwargs["user_prompt"]))
        writer_payload = json.loads(self.writer.call_args.kwargs["user_prompt"])
        self.assertEqual(writer_payload["section"], "methods")
        self.assert_scope(json.dumps(writer_payload["writing_memory"], ensure_ascii=False))
        self.assertEqual(fixtures.client.get(f"/paper-drafts/{saved['id']}").json(), saved)

    def test_memory_id_cannot_be_used_as_a_paper_citation(self):
        self.seed()
        bad = deepcopy(self.payload)
        bad["methods"][0]["source_numbers"] = [999999]
        self.writer.return_value = json.dumps(bad)
        response = fixtures.client.post(self.base, json=self.request)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.writer.call_count, 2)

    def test_report_export_analysis_receives_memory_and_produces_valid_zip(self):
        self.seed()
        response = fixtures.client.post(f"/experiment-imports/{self.import_id}/research-report/export", json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_scope(self.analysis_model.call_args.kwargs["user_prompt"])
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())

    def test_direct_draft_export_path_receives_memory(self):
        self.seed()
        response = fixtures.client.post(self.base + "/export", json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_scope(self.writer.call_args.kwargs["user_prompt"])
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())

    def test_necessary_memory_overflow_does_not_call_planning_model(self):
        self.seed()
        with fixtures.TestingSessionLocal() as db:
            repository = MemoryRepository(db, "local-user")
            for content in ("甲" * 2000, "乙" * 2000):
                repository.add(MemoryCreate(research_task_id=self.task_id, kind="constraint",
                                            content=content, origin="user_statement"))
        response = fixtures.client.post(f"/research-question-sets/{self.set_id}/plan", json=self.conditions)
        self.assertEqual(response.status_code, 409, response.text)
        self.generate_plan.assert_not_called()
        self.embed.assert_not_called()

    def test_oversized_preferences_return_conflict_for_expansion_and_skill(self):
        saved = self.saved_draft()
        with fixtures.TestingSessionLocal() as db:
            repository = MemoryRepository(db, "local-user")
            for content in ("甲" * 2000, "乙" * 2000):
                repository.add(MemoryCreate(kind="preference", content=content, origin="user_statement"))
        self.writer.reset_mock()
        response = fixtures.client.post(f"/paper-drafts/{saved['id']}/expand", json={
            "section": "introduction", "instruction": "扩写背景"})
        self.assertEqual(response.status_code, 409, response.text)
        with patch("research_library.agents.paper_writing_skill.generate_text", return_value=json.dumps({
            "action": "expand_section", "section": "introduction", "instruction": "扩写背景"})):
            response = fixtures.client.post(f"/paper-drafts/{saved['id']}/skill", json={"instruction": "扩写引言"})
        self.assertEqual(response.status_code, 409, response.text)
        self.writer.assert_not_called()
        self.assertEqual(fixtures.client.get(f"/paper-drafts/{saved['id']}").json(), saved)
