"""使用临时文件SQLite验证真实提交、范围隔离、回滚与删除。"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from research_library.db.database import Base
from research_library.db.model import AgentMemory, ResearchTask
from research_library.memory.repository import MemoryRepository
from research_library.memory.schemas import MemoryCreate, MemoryResponse, MemoryUpdate


class TestMemoryRepository(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(TemporaryDirectory())
        self.url = "sqlite:///" + str(Path(self.directory) / "memories.db")
        self.engine = create_engine(self.url)
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.db = self.enterContext(Session(self.engine, expire_on_commit=False))
        self.repository = MemoryRepository(self.db, "user-a")

    def add_memory(self, **changes):
        return self.repository.add(MemoryCreate(**{
            "kind": "preference", "content": "中文回答。",
            "origin": "user_statement", **changes,
        }))

    def create_task(self, title):
        task = ResearchTask(title=title, question="测试问题")
        self.db.add(task)
        self.db.commit()
        return task.id

    def test_saved_record_survives_a_new_engine(self):
        record = self.add_memory(content="  中文回答。  ")
        fresh_engine = create_engine(self.url)
        try:
            with Session(fresh_engine) as db:
                saved = MemoryRepository(db, "user-a").get(record.id)
                response = MemoryResponse.model_validate(saved)
                self.assertEqual(response.content, "中文回答。")
                self.assertEqual(response.owner_id, "user-a")
                self.assertIsNotNone(response.created_at)
                self.assertIsNotNone(response.updated_at)
        finally:
            fresh_engine.dispose()

    def test_task_scope_includes_global_but_excludes_other_task(self):
        task_a = self.create_task("任务A")
        task_b = self.create_task("任务B")
        global_record = self.add_memory()
        scoped_record = self.add_memory(research_task_id=task_a, kind="constraint")
        self.add_memory(research_task_id=task_b, kind="constraint")
        self.assertEqual(
            [row.id for row in self.repository.list_candidates(task_a)],
            [scoped_record.id, global_record.id],
        )
        self.assertEqual([row.id for row in self.repository.list_candidates()], [global_record.id])

    def test_other_owner_cannot_read_update_or_delete(self):
        record = self.add_memory()
        other = MemoryRepository(self.db, "user-b")
        self.assertIsNone(other.get(record.id))
        self.assertEqual(other.list_candidates(), [])
        self.assertIsNone(other.update(record.id, MemoryUpdate(content="其他正文")))
        self.assertFalse(other.delete(record.id))
        self.db.refresh(record)
        self.assertEqual(record.content, "中文回答。")

    def test_limit_returns_newest_records(self):
        self.add_memory(content="第一条")
        latest = self.add_memory(content="第二条")
        self.assertEqual([row.id for row in self.repository.list_candidates(limit=1)], [latest.id])

    def test_invalid_owner_task_and_limit(self):
        self.assertEqual(MemoryRepository(self.db, " user-a ").owner_id, "user-a")
        for owner in (" ", "x" * 101):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                MemoryRepository(self.db, owner)
        for kwargs in ({"task_id": 0}, {"task_id": -1}, {"limit": 0}, {"limit": 101}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.repository.list_candidates(**kwargs)

    def test_missing_task_is_rejected_without_saving(self):
        with self.assertRaisesRegex(ValueError, "研究任务不存在"):
            self.add_memory(research_task_id=999999)
        self.assertEqual(self.repository.list_candidates(), [])

    def test_event_source_is_persisted(self):
        record = self.add_memory(memory_type="episodic", kind="event",
                                 origin="tool_observation", source_type="investigation", source_id=3)
        self.db.expire_all()
        saved = self.repository.get(record.id)
        self.assertEqual((saved.memory_type, saved.kind, saved.source_type, saved.source_id),
                         ("episodic", "event", "investigation", 3))

    def test_changed_content_clears_vector_but_preserves_scope_and_source(self):
        task_id = self.create_task("任务")
        record = self.add_memory(research_task_id=task_id, source_type="investigation", source_id=3)
        created_at = record.created_at
        record.embedding = [0.1, 0.2]
        record.embedding_model = "test-model"
        self.db.commit()
        updated = self.repository.update(record.id, MemoryUpdate(content=" 新正文 "))
        self.db.refresh(updated)
        self.assertEqual(updated.content, "新正文")
        self.assertIsNone(updated.embedding)
        self.assertIsNone(updated.embedding_model)
        self.assertEqual((updated.owner_id, updated.research_task_id, updated.source_id), ("user-a", task_id, 3))
        self.assertEqual(updated.created_at, created_at)

    def test_unchanged_content_keeps_vector_without_commit(self):
        record = self.add_memory()
        record.embedding = [0.1]
        record.embedding_model = "test-model"
        self.db.commit()
        with patch.object(self.db, "commit", wraps=self.db.commit) as commit:
            updated = self.repository.update(record.id, MemoryUpdate(content="中文回答。"))
        commit.assert_not_called()
        self.assertEqual(updated.embedding, [0.1])
        self.assertEqual(updated.embedding_model, "test-model")

    def test_delete_and_missing_record(self):
        record = self.add_memory()
        self.assertTrue(self.repository.delete(record.id))
        with Session(self.engine) as db:
            self.assertIsNone(db.get(AgentMemory, record.id))
        self.assertFalse(self.repository.delete(record.id))
        self.assertIsNone(self.repository.update(record.id, MemoryUpdate(content="新正文")))

    def test_task_delete_cascades_only_its_memory(self):
        task_id = self.create_task("任务")
        global_record = self.add_memory()
        scoped_record = self.add_memory(research_task_id=task_id)
        self.db.delete(self.db.get(ResearchTask, task_id))
        self.db.commit()
        self.assertIsNone(self.repository.get(scoped_record.id))
        self.assertIsNotNone(self.repository.get(global_record.id))

    def test_failed_writes_rollback_and_session_remains_usable(self):
        for operation in ("add", "update", "delete"):
            with self.subTest(operation=operation):
                record = self.add_memory(content="原正文")
                memory_id = record.id
                with patch.object(self.db, "commit", side_effect=RuntimeError("模拟提交失败")):
                    with self.assertRaisesRegex(RuntimeError, "模拟提交失败"):
                        if operation == "add":
                            self.add_memory(content="不应保存")
                        elif operation == "update":
                            self.repository.update(memory_id, MemoryUpdate(content="不应保存"))
                        else:
                            self.repository.delete(memory_id)
                self.assertEqual(self.repository.get(memory_id).content, "原正文")
                self.assertEqual(self.db.scalars(select(AgentMemory).where(AgentMemory.content == "不应保存")).all(), [])
                self.assertTrue(self.repository.delete(memory_id))
