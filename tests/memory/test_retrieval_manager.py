"""记忆检索、向量复用和上下文预算；只使用SQLite与模拟向量。"""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from research_library.db.database import Base
from research_library.db.model import ResearchTask
from research_library.memory.manager import MemoryManager
from research_library.memory.repository import MemoryRepository
from research_library.memory.retrieval import MemoryRetriever
from research_library.memory.schemas import MemoryCreate, MemoryMatch, MemoryResponse, MemoryUpdate
from research_library.retrieval import embedding_service


class MemoryFixture:
    def setUp(self):
        super().setUp()
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = self.enterContext(Session(self.engine, expire_on_commit=False))
        self.repository = MemoryRepository(self.db, "local-user")
        self.retriever = MemoryRetriever(self.repository)
        self.manager = MemoryManager(self.repository)
        self.embed = self.enterContext(patch.object(
            embedding_service, "embed_texts", side_effect=AssertionError("未预期的embedding调用")))

    def add(self, content="查证经验", kind="experience", task_id=None, vector=None, model=None):
        record = self.repository.add(MemoryCreate(
            content=content, kind=kind, research_task_id=task_id,
            memory_type="episodic" if kind == "event" else "semantic", origin="model_summary"))
        if vector is not None:
            self.repository.save_embedding(record.id, vector, model or self.retriever.embedding_model)
        return record

    def task(self, title="研究任务"):
        record = ResearchTask(title=title, question="研究问题")
        self.db.add(record)
        self.db.commit()
        return record.id

    def match(self, record, score=0.8):
        return MemoryMatch(memory=MemoryResponse.model_validate(record), score=score)


class TestMemoryRetrievalManager(MemoryFixture, unittest.TestCase):
    def test_repository_vector_method_is_callable_and_owner_scoped(self):
        record = self.add()
        self.assertTrue(self.repository.save_embedding(record.id, [1.0, 0.0], "model"))
        other = MemoryRepository(self.db, "other")
        self.assertFalse(other.save_embedding(record.id, [0.0, 1.0], "other-model"))
        self.db.refresh(record)
        self.assertEqual((record.embedding, record.embedding_model), ([1.0, 0.0], "model"))
        self.assertFalse(self.repository.save_embedding(999999, [1.0], "model"))

    def test_index_batches_and_reuses_vectors(self):
        for index in range(17):
            self.add(content=f"经验{index}")
        self.add(content="表达偏好", kind="preference")
        self.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        self.assertEqual(self.retriever.index_memories(), 17)
        self.assertEqual([len(call.args[0]) for call in self.embed.call_args_list], [16, 1])
        self.embed.reset_mock()
        self.assertEqual(self.retriever.index_memories(), 0)
        self.embed.assert_not_called()

    def test_old_model_and_updated_content_are_reindexed(self):
        record = self.add(vector=[0.0, 1.0], model="old-model")
        self.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        self.assertEqual(self.retriever.index_memories(), 1)
        self.assertEqual(record.embedding_model, self.retriever.embedding_model)
        self.repository.update(record.id, MemoryUpdate(content="更新后的经验"))
        self.assertEqual(self.retriever.index_memories(), 1)
        self.assertEqual(self.embed.call_args.args[0], ["更新后的经验"])

    def test_search_filters_scope_threshold_and_sorts(self):
        task_a, task_b = self.task("A"), self.task("B")
        global_record = self.add(content="全局经验", vector=[0.8, 0.6])
        best = self.add(content="任务经验", task_id=task_a, vector=[1.0, 0.0])
        self.add(content="不相关", task_id=task_a, vector=[0.0, 1.0])
        self.add(content="其他任务", task_id=task_b, vector=[1.0, 0.0])
        MemoryRepository(self.db, "other").add(MemoryCreate(
            kind="experience", content="其他用户", origin="model_summary"))
        self.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        matches = self.retriever.search("查询", task_a, min_score=0.3)
        self.assertEqual([match.memory.id for match in matches], [best.id, global_record.id])
        self.assertAlmostEqual(matches[0].score, 1.0)
        self.embed.assert_called_once_with(["查询"])
        self.assertEqual(len(self.retriever.search("查询", task_a, limit=1)), 1)

    def test_retrieval_does_not_lose_older_record_after_100_new_memories(self):
        old = self.add(content="较早但相关", vector=[1.0, 0.0])
        for index in range(101):
            self.add(content=f"新记录{index}", vector=[0.0, 1.0])
        self.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        self.assertEqual([m.memory.id for m in self.retriever.search("问题")], [old.id])

    def test_no_history_does_not_request_embedding(self):
        preference = self.add(kind="preference", content="使用中文")
        context = self.manager.recall("问题")
        self.assertEqual(context.memory_ids, [preference.id])
        self.assertEqual(self.manager.search("问题").content, "")
        self.embed.assert_not_called()

    def test_invalid_queries_and_limits_stop_before_embedding(self):
        for query, limit in ((" ", 3), ("问题", 0)):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.retriever.search(query, limit=limit)
        self.embed.assert_not_called()

    def test_required_memories_are_scoped_without_embedding(self):
        task_a, task_b = self.task("A"), self.task("B")
        preference = self.add(kind="preference")
        constraint = self.add(kind="constraint", task_id=task_a)
        self.add(kind="constraint", task_id=task_b)
        self.add(kind="experience", task_id=task_a)
        self.assertEqual({r.id for r in self.retriever.get_required_memories(task_a)},
                         {preference.id, constraint.id})
        self.embed.assert_not_called()

    def test_budget_deduplicates_and_keeps_smaller_later_match(self):
        required = self.add(kind="constraint", content="研究约束")
        long_record = self.add(content="长" * 1000)
        short_record = self.add(content="短经验")
        required_response = MemoryResponse.model_validate(required)
        short_response = MemoryResponse.model_validate(short_record)
        self.manager.max_chars = len(self.manager._render([required_response, short_response]))
        context = self.manager.build_context(
            [required], [self.match(required), self.match(long_record), self.match(short_record)])
        self.assertEqual(context.memory_ids, [required.id, short_record.id])
        self.assertEqual(context.omitted_ids, [long_record.id])
        self.assertLessEqual(len(context.content), self.manager.max_chars)
        payload = json.loads(context.content)
        self.assertEqual([row["memory_id"] for row in payload["memories"]], context.memory_ids)
        self.assertNotIn("embedding", payload["memories"][0])

    def test_required_overflow_fails_before_search(self):
        self.add(kind="constraint", content="必须遵守的约束")
        self.add(content="待索引经验")
        manager = MemoryManager(self.repository, max_chars=1)
        with self.assertRaisesRegex(ValueError, "必要偏好和约束超过"):
            manager.recall("问题")
        self.embed.assert_not_called()

    def test_tool_search_does_not_repeat_required_memories(self):
        preference = self.add(kind="preference", content="使用中文")
        experience = self.add(vector=[1.0, 0.0])
        self.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        context = self.manager.search("问题")
        self.assertEqual(context.memory_ids, [experience.id])
        self.assertNotIn(preference.id, context.memory_ids)
