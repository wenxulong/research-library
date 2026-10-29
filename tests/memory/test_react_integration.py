"""查证入口、记忆查询、下一轮观察和独立候选保存的完整模拟联调。"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from openai.types.chat import ChatCompletionMessage
from sqlalchemy import select, func

from research_library.agents import evidence_react
from research_library.db.model import AgentMemory, ReportJob
from research_library.memory.repository import MemoryRepository
from research_library.memory.schemas import MemoryCreate, MemoryContext
from research_library.retrieval import embedding_service
from research_library.services import report_claim_investigation as service
from tests.agents.test_evidence_investigation import InvestigationFixture
from tests.fixtures.main import TestingSessionLocal


def search_message():
    return ChatCompletionMessage(role="assistant", content=None, tool_calls=[{
        "id": "memory-call", "type": "function",
        "function": {"name": "search_memories", "arguments": '{"query":"负载条件经验","limit":3}'},
    }])


class TestMemoryReactIntegration(InvestigationFixture, unittest.TestCase):
    def add_memories(self, db):
        repository = MemoryRepository(db, "local-user")
        preference = repository.add(MemoryCreate(
            kind="preference", content="默认使用中文", origin="user_statement"))
        experience = repository.add(MemoryCreate(
            research_task_id=self.task_id, kind="experience", content="历史经验：核对负载条件。",
            origin="model_summary"))
        return preference.id, experience.id

    def test_real_service_reads_memory_runs_tool_and_saves_candidate_without_changing_report(self):
        snapshots = []
        replies = [search_message(), ChatCompletionMessage(role="assistant", content="查证仍需核对论文负载条件。")]

        def generate(**kwargs):
            snapshots.append(deepcopy(kwargs))
            return replies.pop(0)

        with TestingSessionLocal() as db:
            preference_id, experience_id = self.add_memories(db)
            before = db.scalar(select(func.count()).select_from(AgentMemory))
            with patch.object(embedding_service, "embed_texts", side_effect=lambda texts: [[1.0, 0.0] for _ in texts]), \
                 patch.object(evidence_react, "generate_tool_message", side_effect=generate), \
                 patch.object(service, "review_claim", return_value=self.context.review) as review:
                saved = self.run_saved(db)
            self.assertEqual(saved.result.steps[0]["tool_name"], "search_memories")
            observation = saved.result.steps[0]["observation"]
            self.assertEqual(observation["memory_ids"], [experience_id])
            self.assertNotIn("evidence", observation)
            self.assertNotIn("new_evidence_count", observation)
            self.assertEqual(db.get(ReportJob, self.job_id).result, self.base_report)
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentMemory)), before)
            self.assertEqual({item.chunk_id for item in saved.result.evidence},
                             {item.chunk_id for item in self.context.evidence})
            self.assertTrue(all(item.text != "历史经验：核对负载条件。" for item in review.call_args.args[0].evidence))

        initial_payload = json.loads(snapshots[0]["messages"][1]["content"])
        ids = {row["memory_id"] for row in initial_payload["memory_context"]["memories"]}
        self.assertEqual(ids, {preference_id, experience_id})
        self.assertEqual(snapshots[0]["tool_choice"], "auto")
        self.assertIn("search_memories", {t["function"]["name"] for t in snapshots[0]["tools"]})
        feedback = snapshots[1]["messages"][-1]
        self.assertEqual((feedback["role"], feedback["tool_call_id"]), ("tool", "memory-call"))
        self.assertEqual(json.loads(feedback["content"])["memory_ids"], [experience_id])

    def test_last_round_memory_observation_is_summarized_with_tools_disabled(self):
        snapshots = []
        replies = [search_message(), ChatCompletionMessage(role="assistant", content="根据原文，证据仍不足。")]

        def generate(**kwargs):
            snapshots.append(deepcopy(kwargs))
            return replies.pop(0)

        with TestingSessionLocal() as db:
            self.add_memories(db)
            with patch.object(embedding_service, "embed_texts", side_effect=lambda texts: [[1.0, 0.0] for _ in texts]), \
                 patch.object(evidence_react, "generate_tool_message", side_effect=generate):
                result = evidence_react.run_evidence_react(db, self.context, "检查", max_rounds=1)
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(snapshots[0]["tool_choice"], "auto")
        self.assertEqual(snapshots[1]["tool_choice"], "none")
        self.assertEqual(snapshots[1]["tools"], snapshots[0]["tools"])
        observation = json.loads(snapshots[1]["messages"][-2]["content"])
        self.assertEqual(observation["result_type"], "memory")
        self.assertNotIn("evidence", observation)

    def test_optional_memory_is_separate_from_paper_evidence_and_old_callers_still_work(self):
        without_memory = evidence_react.build_evidence_messages(self.context, "检查")
        self.assertIsNone(json.loads(without_memory[1]["content"])["memory_context"])
        context = MemoryContext(content=json.dumps({"memories": [{"memory_id": 999, "content": "历史经验"}]}),
                                memory_ids=[999], omitted_ids=[])
        with_memory = evidence_react.build_evidence_messages(self.context, "检查", memory_context=context)
        payload = json.loads(with_memory[1]["content"])
        self.assertEqual(payload["memory_context"]["memories"][0]["memory_id"], 999)
        self.assertEqual(payload["existing_evidence"], json.loads(without_memory[1]["content"])["existing_evidence"])
