"""检查记忆分类、来源及输入输出结构，不连接数据库。"""
from datetime import datetime
from types import SimpleNamespace
import unittest

from pydantic import ValidationError
from research_library.memory.schemas import MemoryCreate, MemoryResponse, MemoryUpdate


class TestMemorySchemas(unittest.TestCase):
    def make(self, **changes):
        return MemoryCreate(**{
            "kind": "preference", "content": "中文回答。",
            "origin": "user_statement", **changes,
        })

    def test_defaults_and_whitespace(self):
        record = self.make(content="  中文回答。 \n")
        self.assertEqual(record.content, "中文回答。")
        self.assertEqual(record.memory_type, "semantic")
        self.assertIsNone(record.research_task_id)
        self.assertIsNone(record.source_id)

    def test_supported_classifications(self):
        for kind in ("preference", "constraint", "experience"):
            with self.subTest(kind=kind):
                self.assertEqual(self.make(kind=kind).memory_type, "semantic")
        self.assertEqual(self.make(kind="event", memory_type="episodic").memory_type, "episodic")

    def test_wrong_classification_or_unknown_literals_are_rejected(self):
        for changes in (
            {"kind": "event"}, {"memory_type": "episodic"},
            {"kind": "unknown"}, {"origin": "unknown"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make(**changes)

    def test_source_requires_type_and_id_together(self):
        self.assertEqual(self.make(source_type="investigation", source_id=3).source_id, 3)
        for changes in ({"source_type": "investigation"}, {"source_id": 3},
                        {"source_type": " ", "source_id": 3},
                        {"source_type": "investigation", "source_id": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make(**changes)

    def test_content_boundaries(self):
        self.assertEqual(len(self.make(content="字" * 2000).content), 2000)
        for content in ("", " \n ", "字" * 2001):
            with self.subTest(length=len(content)), self.assertRaises(ValidationError):
                self.make(content=content)

    def test_task_ids_and_extra_owner_input(self):
        self.assertEqual(self.make(research_task_id=6).research_task_id, 6)
        for changes in ({"research_task_id": 0}, {"research_task_id": -1}, {"owner_id": "other-user"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make(**changes)

    def test_update_only_accepts_valid_content(self):
        self.assertEqual(MemoryUpdate(content=" 新正文 ").content, "新正文")
        for changes in ({"content": " "}, {"content": "字" * 2001},
                        {"content": "正文", "research_task_id": 6}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                MemoryUpdate(**changes)

    def test_response_reads_attributes_without_exposing_embedding(self):
        now = datetime.now()
        record = SimpleNamespace(
            id=1, owner_id="local-user", research_task_id=None,
            memory_type="semantic", kind="preference", content="中文回答。",
            origin="user_statement", source_type=None, source_id=None,
            created_at=now, updated_at=now, embedding=[0.1, 0.2], embedding_model="test",
        )
        response = MemoryResponse.model_validate(record)
        self.assertEqual(response.id, 1)
        self.assertEqual(response.created_at, now)
        self.assertNotIn("embedding", response.model_dump())
        self.assertNotIn("embedding_model", response.model_dump())
