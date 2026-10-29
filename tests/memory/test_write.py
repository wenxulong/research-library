"""主动保存、精确查重、来源绑定及后续检索；独立SQLite，无模型请求。"""
import unittest
from unittest.mock import patch

from sqlalchemy import select, func
from research_library.agents import evidence_react
from research_library.db.model import AgentMemory
from research_library.memory.manager import MemoryManager
from research_library.memory.repository import MemoryRepository
from research_library.memory.schemas import MemoryCreate
from tests.memory.test_retrieval_manager import MemoryFixture
from tests.memory.test_tools import tool_call


class TestMemoryWrite(MemoryFixture, unittest.TestCase):
    def data(self, **changes):
        return MemoryCreate(**{
            "kind": "experience", "content": "核对负载条件。",
            "origin": "model_summary", **changes,
        })

    def count(self):
        return self.db.scalar(select(func.count()).select_from(AgentMemory))

    def test_new_memory_is_persisted_without_embedding_request(self):
        result = self.manager.remember(self.data())
        self.assertTrue(result.created)
        self.assertEqual(result.memory.owner_id, "local-user")
        self.assertEqual(self.repository.get(result.memory.id).content, "核对负载条件。")
        self.assertIsNone(self.repository.get(result.memory.id).embedding)
        self.embed.assert_not_called()

    def test_duplicate_content_reuses_id_and_original_source_without_commit(self):
        first = self.manager.remember(self.data(source_type="report_job", source_id=21))
        with patch.object(self.db, "commit", wraps=self.db.commit) as commit:
            duplicate = self.manager.remember(self.data(
                content="  核对负载条件。  ", source_type="report_job", source_id=22))
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.memory.id, first.memory.id)
        self.assertEqual(duplicate.memory.source_id, 21)
        self.assertEqual(self.count(), 1)
        commit.assert_not_called()

    def test_scope_kind_origin_and_content_are_not_merged(self):
        task_a, task_b = self.task("A"), self.task("B")
        first = self.manager.remember(self.data())
        variants = (
            {"research_task_id": task_a}, {"research_task_id": task_b},
            {"kind": "event", "memory_type": "episodic"},
            {"origin": "user_statement"}, {"content": "另一种经验。"},
        )
        ids = {first.memory.id}
        for changes in variants:
            with self.subTest(changes=changes):
                result = self.manager.remember(self.data(**changes))
                self.assertTrue(result.created)
                ids.add(result.memory.id)
        self.assertEqual(len(ids), 6)
        other = MemoryManager(MemoryRepository(self.db, "other-user"))
        other_result = other.remember(self.data())
        self.assertTrue(other_result.created)
        self.assertNotIn(other_result.memory.id, ids)

    def test_missing_task_does_not_save(self):
        with self.assertRaisesRegex(ValueError, "研究任务不存在"):
            self.manager.remember(self.data(research_task_id=999999))
        self.assertEqual(self.count(), 0)

    def test_write_tool_binds_task_source_owner_and_model_summary(self):
        task_id = self.task()
        for kind, memory_type in (("event", "episodic"), ("experience", "semantic")):
            with self.subTest(kind=kind):
                result = evidence_react.execute_evidence_call(
                    self.db, task_id,
                    tool_call({"kind": kind, "content": "需要核对实验条件。"}, name="remember_memory"),
                    report_job_id=21,
                )
                self.assertTrue(result["ok"])
                self.assertTrue(result["created"])
                self.assertEqual(result["result_type"], "memory_write")
                self.assertNotIn("evidence", result)
                saved = self.repository.get(result["memory_id"])
                self.assertEqual((saved.owner_id, saved.research_task_id), ("local-user", task_id))
                self.assertEqual((saved.memory_type, saved.origin), (memory_type, "model_summary"))
                self.assertEqual((saved.source_type, saved.source_id), ("report_job", 21))
        self.embed.assert_not_called()

    def test_old_callers_can_omit_source(self):
        task_id = self.task()
        result = evidence_react.execute_evidence_call(
            self.db, task_id, tool_call({"kind": "experience", "content": "保留经验。"}, name="remember_memory"))
        self.assertTrue(result["ok"])
        self.assertIsNone(result["source_type"])
        self.assertIsNone(result["source_id"])

    def test_invalid_parameters_cannot_override_scope_or_origin(self):
        task_id = self.task()
        valid = {"kind": "experience", "content": "一条经验。"}
        invalid = (
            {**valid, "kind": "preference"}, {**valid, "content": " "},
            {**valid, "content": "长" * 2001}, {**valid, "owner_id": "other"},
            {**valid, "research_task_id": 999}, {**valid, "origin": "user_statement"},
            {**valid, "source_id": 999}, {**valid, "memory_type": "episodic"},
        )
        for arguments in invalid:
            with self.subTest(keys=list(arguments)):
                result = evidence_react.execute_evidence_call(
                    self.db, task_id, tool_call(arguments, name="remember_memory"))
                self.assertFalse(result["ok"])
        self.assertEqual(self.count(), 0)
        self.embed.assert_not_called()

    def test_save_failure_is_not_reported_as_success_and_rolls_back(self):
        task_id = self.task()
        with patch.object(self.db, "commit", side_effect=RuntimeError("模拟写入失败")):
            with self.assertRaisesRegex(RuntimeError, "模拟写入失败"):
                evidence_react.execute_evidence_call(
                    self.db, task_id, tool_call({"kind": "experience", "content": "不应保存。"}, name="remember_memory"))
        self.assertEqual(self.count(), 0)
        self.assertTrue(self.manager.remember(self.data()).created)

    def test_saved_memory_is_indexed_and_found_on_next_search(self):
        task_id = self.task()
        result = evidence_react.execute_evidence_call(
            self.db, task_id, tool_call({"kind": "experience", "content": "核对负载条件。"}, name="remember_memory"))
        self.embed.assert_not_called()
        self.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        context = self.manager.search("负载", task_id)
        self.assertEqual(context.memory_ids, [result["memory_id"]])
        self.assertEqual(self.embed.call_count, 2)

    def test_save_schema_exposes_only_kind_and_content(self):
        definitions = {item["function"]["name"]: item["function"] for item in evidence_react.EVIDENCE_TOOLS}
        schema = definitions["remember_memory"]["parameters"]
        self.assertEqual(set(schema["properties"]), {"kind", "content"})
        self.assertFalse(schema["additionalProperties"])
