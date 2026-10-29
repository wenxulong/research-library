"""记忆工具参数和执行器；禁止外部模型与向量服务请求。"""
import json
import unittest
from types import SimpleNamespace
from urllib.error import URLError
from unittest.mock import Mock

from research_library.agents import evidence_react
from research_library.tools import memory_tools
from tests.memory.test_retrieval_manager import MemoryFixture


def tool_call(arguments, name="search_memories"):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class TestMemoryTools(MemoryFixture, unittest.TestCase):
    def test_real_dispatch_searches_and_returns_memory_not_evidence(self):
        task_id = self.task()
        memory = self.add(content="核对负载条件", task_id=task_id, vector=[1.0, 0.0])
        self.add(content="用户偏好", kind="preference")
        self.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        result = evidence_react.execute_evidence_call(
            self.db, task_id, tool_call({"query": "成本条件", "limit": 3}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["result_type"], "memory")
        self.assertEqual(result["memory_ids"], [memory.id])
        self.assertEqual(result["memory_context"]["memories"][0]["content"], "核对负载条件")
        self.assertNotIn("evidence", result)
        self.assertNotIn("chunk_id", result["memory_context"]["memories"][0])

    def test_empty_history_is_a_successful_empty_result(self):
        result = evidence_react.execute_evidence_call(self.db, 1, tool_call({"query": "经验"}))
        self.assertTrue(result["ok"])
        self.assertIsNone(result["memory_context"])
        self.assertEqual(result["memory_ids"], [])
        self.embed.assert_not_called()

    def test_invalid_or_scope_overriding_arguments_are_not_executed(self):
        for args in ({"query": " "}, {"query": "经验", "limit": "3"},
                     {"query": "经验", "limit": 6},
                     {"query": "经验", "task_id": 999},
                     {"query": "经验", "owner_id": "other"}):
            with self.subTest(args=args):
                result = evidence_react.execute_evidence_call(self.db, 1, tool_call(args))
                self.assertFalse(result["ok"])
        self.embed.assert_not_called()

    def test_embedding_connection_error_is_returned_to_model(self):
        self.add()
        self.embed.side_effect = URLError("模拟Ollama不可用")
        result = evidence_react.execute_evidence_call(self.db, 1, tool_call({"query": "经验"}))
        self.assertFalse(result["ok"])
        self.assertIn("Ollama", result["error"])

    def test_tool_registration_has_no_user_or_task_parameters(self):
        definitions = {item["function"]["name"]: item for item in evidence_react.EVIDENCE_TOOLS}
        self.assertEqual(set(definitions), {"search_paper_evidence", "read_neighbor_chunks", "search_memories", "remember_memory"})
        schema = definitions["search_memories"]["function"]["parameters"]
        self.assertEqual(set(schema["properties"]), {"query", "limit"})
        self.assertFalse(schema["additionalProperties"])
