"""模拟完整ReAct保存、查询和重复保存，不调用真实LLM或Ollama。"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from openai.types.chat import ChatCompletionMessage
from sqlalchemy import select, func

from research_library.agents import evidence_react
from research_library.db.model import AgentMemory, ReportJob
from research_library.memory.repository import MemoryRepository
from research_library.retrieval import embedding_service
from research_library.services import report_claim_investigation as service
from tests.agents.test_evidence_investigation import InvestigationFixture
from tests.fixtures.main import TestingSessionLocal


def call_message(name, arguments, call_id):
    return ChatCompletionMessage(role="assistant", content=None, tool_calls=[{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }])


class TestMemoryWriteReact(InvestigationFixture, unittest.TestCase):
    def test_write_search_reuse_and_review_preserve_paper_evidence_and_report(self):
        arguments = {"kind": "experience", "content": "本次仍需核对实验负载条件。"}
        replies = [
            call_message("remember_memory", arguments, "save-1"),
            call_message("search_memories", {"query": "负载条件"}, "search-1"),
            call_message("remember_memory", arguments, "save-2"),
            ChatCompletionMessage(role="assistant", content="已记录查证经验，论文证据仍待补充。"),
        ]
        snapshots = []

        def generate(**kwargs):
            snapshots.append(deepcopy(kwargs))
            return replies.pop(0)

        with TestingSessionLocal() as db:
            with patch.object(embedding_service, "embed_texts", side_effect=lambda texts: [[1.0, 0.0] for _ in texts]), \
                 patch.object(evidence_react, "generate_tool_message", side_effect=generate), \
                 patch.object(service, "review_claim", return_value=self.context.review) as review:
                saved = self.run_saved(db)
            observations = [step["observation"] for step in saved.result.steps]
            first, search, second = observations
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(first["memory_id"], second["memory_id"])
            self.assertEqual(search["memory_ids"], [first["memory_id"]])
            for observation in observations:
                self.assertTrue(observation["ok"])
                self.assertNotIn("evidence", observation)
                self.assertNotIn("new_evidence_count", observation)
            record = MemoryRepository(db, "local-user").get(first["memory_id"])
            self.assertEqual((record.source_type, record.source_id), ("report_job", self.job_id))
            self.assertEqual(record.research_task_id, self.task_id)
            self.assertEqual(record.origin, "model_summary")
            self.assertIsNotNone(record.embedding)
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentMemory)), 1)
            self.assertEqual(db.get(ReportJob, self.job_id).result, self.base_report)
            self.assertEqual({item.chunk_id for item in saved.result.evidence},
                             {item.chunk_id for item in self.context.evidence})
            self.assertTrue(all(item.text != arguments["content"] for item in review.call_args.args[0].evidence))
        self.assertEqual(len(snapshots), 4)
        for index, expected in ((1, True), (3, False)):
            feedback = snapshots[index]["messages"][-1]
            self.assertEqual(feedback["role"], "tool")
            self.assertEqual(json.loads(feedback["content"])["created"], expected)
        self.assertTrue(all(snapshot["tool_choice"] == "auto" for snapshot in snapshots))

    def test_write_at_round_limit_is_observed_by_summary_with_tools_disabled(self):
        snapshots = []
        replies = [
            call_message("remember_memory", {"kind": "event", "content": "查证尚未完成。"}, "save-last"),
            ChatCompletionMessage(role="assistant", content="已达上限，当前结论仍未核实。"),
        ]
        def generate(**kwargs):
            snapshots.append(deepcopy(kwargs))
            return replies.pop(0)
        with TestingSessionLocal() as db, \
             patch.object(evidence_react, "generate_tool_message", side_effect=generate):
            result = evidence_react.run_evidence_react(db, self.context, "核对", max_rounds=1)
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(snapshots[-1]["tool_choice"], "none")
        feedback = snapshots[-1]["messages"][-2]
        self.assertEqual(feedback["tool_call_id"], "save-last")
        self.assertEqual(json.loads(feedback["content"])["source_id"], self.job_id)
