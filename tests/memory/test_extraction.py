"""自动提取及日志的模拟联调；无外部API请求、无正式数据库写入。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import select, func

from research_library.core import model_usage
from research_library.db.model import AgentMemory, ReportClaimInvestigation, ReportJob
from research_library.memory import extractor as extraction
from research_library.memory.extractor import MemoryExtractor
from research_library.memory.manager import MemoryManager
from research_library.memory.repository import MemoryRepository
from research_library.memory.schemas import MemoryCreate, MemoryExtractionResponse
from research_library.schemas import SavedReportClaimInvestigationResponse
from research_library.services import report_claim_investigation as service
from tests.agents.test_evidence_investigation import InvestigationFixture
from tests.fixtures.main import TestingSessionLocal


class TestMemoryExtraction(InvestigationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("research_library.retrieval.embedding_service.embed_texts",
                                side_effect=lambda texts: [[1.0, 0.0] for _ in texts]))
        directory = self.enterContext(TemporaryDirectory())
        self.log = Path(directory) / "llm_usage.jsonl"
        self.enterContext(patch.object(model_usage, "settings", SimpleNamespace(log_dir=Path(directory))))
        self.enterContext(patch.object(service, "record_memory_extraction", side_effect=model_usage.record_memory_extraction))

    def rows(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def save(self, db):
        with patch.object(service, "run_evidence_react", return_value=self.react_result()), \
             patch.object(service, "review_claim", return_value=self.context.review):
            return self.run_saved(db)

    def saved_input(self):
        return SavedReportClaimInvestigationResponse(
            id=7, job_id=self.job_id, claim_id=self.context.claim_id,
            created_at="2026-10-29T10:00:00",
            result={"job_id": self.job_id, "task_id": self.task_id,
                    "claim_id": self.context.claim_id, "instruction": "核对条件",
                    "status": "limit_reached", "summary": "证据仍不足",
                    "previous_review": self.context.review, "review": self.context.review,
                    "evidence": [], "added_chunk_ids": [], "steps": [
                        {"tool_name": "search_paper_evidence", "arguments": '{"query":"条件"}',
                         "observation": {"ok": True, "new_evidence_count": 1,
                                         "evidence": [{"text": "不应重复发送的原文"}]}},
                        {"tool_name": "remember_memory", "arguments": "不应重复提取的记忆", "observation": {}},
                    ]})

    def test_completed_logs_counts_sources_and_preserves_report(self):
        candidates = MemoryExtractionResponse(memories=[
            {"kind": "event", "content": "本次仍缺少实验条件。"},
            {"kind": "experience", "content": "核对负载条件。"},
        ])
        with TestingSessionLocal() as db:
            old = MemoryManager(MemoryRepository(db, "local-user")).remember(MemoryCreate(
                research_task_id=self.task_id, kind="experience", content="核对负载条件。",
                origin="model_summary", source_type="report_job", source_id=self.job_id))
            def extract(saved):
                self.assertIsNotNone(db.get(ReportClaimInvestigation, saved.id))
                scope = model_usage._usage_context.get()
                self.assertEqual(scope["stage"], "memory_extract")
                self.assertEqual(scope["investigation_id"], saved.id)
                return candidates
            with patch.object(MemoryExtractor, "extract", side_effect=extract):
                saved = self.save(db)
            memories = db.scalars(select(AgentMemory)).all()
            self.assertEqual(len(memories), 2)
            event = next(m for m in memories if m.kind == "event")
            self.assertEqual((event.source_type, event.source_id), ("investigation", saved.id))
            self.assertEqual(event.memory_type, "episodic")
            self.assertEqual(db.get(AgentMemory, old.memory.id).source_type, "report_job")
            self.assertEqual(db.get(ReportJob, self.job_id).result, self.base_report)
        start, end = self.rows()
        self.assertEqual((start["status"], end["status"]), ("started", "completed"))
        self.assertEqual((end["created_count"], end["reused_count"]), (1, 1))
        self.assertEqual(len(end["memory_ids"]), 2)
        self.assertEqual(start["trace_id"], end["trace_id"])
        self.assertEqual(end["investigation_id"], saved.id)
        self.assertEqual(end["report_job_id"], self.job_id)
        self.assertGreaterEqual(end["elapsed_seconds"], 0)
        for text in ("核对负载条件。", "本次仍缺少实验条件。"):
            self.assertNotIn(text, self.log.read_text())
        self.assertIsNone(model_usage._usage_context.get())

    def test_empty_candidates_are_logged_without_memory_write(self):
        with TestingSessionLocal() as db:
            saved = self.save(db)
            self.assertIsNotNone(db.get(ReportClaimInvestigation, saved.id))
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentMemory)), 0)
        end = self.rows()[-1]
        self.assertEqual(end["status"], "empty")
        self.assertEqual((end["created_count"], end["reused_count"], end["memory_ids"]), (0, 0, []))

    def test_extraction_failure_keeps_saved_investigation_and_hides_error_body(self):
        with TestingSessionLocal() as db, \
             patch.object(MemoryExtractor, "extract", side_effect=RuntimeError("保密的模型回复或密钥")), \
             self.assertLogs(service.logger, level="WARNING") as captured:
            saved = self.save(db)
            self.assertIsNotNone(db.get(ReportClaimInvestigation, saved.id))
            self.assertEqual(db.get(ReportJob, self.job_id).result, self.base_report)
        end = self.rows()[-1]
        self.assertEqual(end["status"], "failed")
        self.assertEqual(end["error_type"], "RuntimeError")
        self.assertIsNone(end["created_count"])
        self.assertNotIn("保密的模型回复或密钥", self.log.read_text() + str(captured.output))

    def test_partial_save_failure_does_not_claim_zero_writes(self):
        candidates = MemoryExtractionResponse(memories=[
            {"kind": "event", "content": "第一条已保存。"},
            {"kind": "experience", "content": "第二条写入失败。"},
        ])
        real_remember = MemoryManager.remember
        def remember(manager, data):
            if data.kind == "experience":
                raise RuntimeError("模拟第二条保存失败")
            return real_remember(manager, data)
        with TestingSessionLocal() as db, \
             patch.object(MemoryExtractor, "extract", return_value=candidates), \
             patch.object(MemoryManager, "remember", autospec=True, side_effect=remember), \
             self.assertLogs(service.logger, level="WARNING"):
            saved = self.save(db)
            self.assertIsNotNone(db.get(ReportClaimInvestigation, saved.id))
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentMemory)), 1)
        end = self.rows()[-1]
        self.assertEqual(end["status"], "failed")
        self.assertIsNone(end["memory_ids"])
        self.assertIsNone(end["created_count"])

    def test_no_extraction_when_investigation_itself_fails(self):
        with TestingSessionLocal() as db, \
             patch.object(service, "investigation_report_claim", side_effect=RuntimeError("查证失败")), \
             patch.object(MemoryExtractor, "extract") as extract:
            with self.assertRaisesRegex(RuntimeError, "查证失败"):
                self.run_saved(db)
            extract.assert_not_called()
        self.assertFalse(self.log.exists())

    def test_real_parser_retries_invalid_json_and_uses_final_review_without_raw_evidence(self):
        with patch.object(extraction, "generate_text", side_effect=[
            '{"memories":[{"kind":"unknown","content":"错误"}]}',
            '{"memories":[{"kind":"experience","content":"核查适用条件。"}]}',
        ]) as generate:
            result = self.memory_extract_impl(MemoryExtractor(), self.saved_input())
        self.assertEqual(result.memories[0].content, "核查适用条件。")
        self.assertEqual(generate.call_count, 2)
        payload = json.loads(generate.call_args_list[0].kwargs["user_prompt"])
        self.assertEqual(payload["execution_status"], "limit_reached")
        self.assertEqual(len(payload["research_steps"]), 1)
        self.assertNotIn("不应重复发送的原文", generate.call_args_list[0].kwargs["user_prompt"])
        self.assertNotIn("不应重复提取的记忆", generate.call_args_list[0].kwargs["user_prompt"])
        self.assertIn("上一次输出未通过", generate.call_args.kwargs["user_prompt"])

    def test_parse_rejects_too_many_candidates_and_empty_content(self):
        parser = MemoryExtractor()
        for payload in ({"memories": [{"kind": "event", "content": "事件"}] * 4},
                        {"memories": [{"kind": "experience", "content": " "}]}):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                parser._parse(json.dumps(payload))
        self.assertEqual(parser._parse('{"memories":[]}').memories, [])

    def test_log_io_failure_does_not_discard_saved_result(self):
        with TestingSessionLocal() as db, \
             patch.object(Path, "open", side_effect=OSError("日志目录不可写")), \
             self.assertLogs("research_library.core.model_usage", level="WARNING"):
            saved = self.save(db)
            self.assertIsNotNone(db.get(ReportClaimInvestigation, saved.id))
