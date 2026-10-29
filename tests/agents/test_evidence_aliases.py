"""同文去重保留来源，最后一轮观察进入受限总结。"""
from unittest.mock import Mock, patch
import unittest
from openai.types.chat import ChatCompletionMessage
from tests.agents.test_evidence_investigation import InvestigationFixture
from tests.fixtures.main import TestingSessionLocal
from research_library.agents import evidence_react
from research_library.db.model import PaperChunk
from research_library.services.report_claim_investigation import prepare_investigation_evidence, build_investigation_claim
from research_library.services.review_service import format_review_evidence
from research_library.schemas import ComparisonSourceResponse, ReportClaimEvidenceResponse


class TestEvidenceAliasFlow(InvestigationFixture, unittest.TestCase):
    def test_initial_duplicate_keeps_number_page_and_maps_to_primary_in_review(self):
        with TestingSessionLocal() as db:
            original = self.context.evidence[0]
            chunk = db.get(PaperChunk, original.chunk_id)
            alias_chunk = PaperChunk(document_id=chunk.document_id, page_number=99, chunk_index=0,
                                     text=chunk.text, char_count=len(chunk.text))
            db.add(alias_chunk); db.commit()
            alias_source = ComparisonSourceResponse(
                source_number=23, chunk_id=alias_chunk.id, paper_id=original.paper_id,
                paper_title=original.paper_title, page_number=99, score=None)
            alias_evidence = ReportClaimEvidenceResponse(**alias_source.model_dump(), text=chunk.text)
            context = self.context.model_copy(update={
                'text': self.context.text + '[资料23]',
                'source_numbers': [*self.context.source_numbers, 23],
                'evidence': [*self.context.evidence, alias_evidence],
            })
            with patch.object(evidence_react, 'generate_tool_message', return_value=ChatCompletionMessage(
                role='assistant', content='两个编号对应相同正文，保留来源。')):
                result = evidence_react.run_evidence_react(db, context, '核对原引用')
            aliases = [e for e in result['evidence'] if e['chunk_id'] == alias_chunk.id]
            self.assertEqual(aliases[0]['duplicate_of_chunk_id'], original.chunk_id)
            self.assertNotIn('text', aliases[0])
            sources = [ComparisonSourceResponse.model_validate(e.model_dump()) for e in self.context.evidence]
            evidence = prepare_investigation_evidence(db, [*sources, alias_source], result['evidence'])
            alias = next(e for e in evidence if e.source_number == 23)
            self.assertEqual(alias.page_number, 99)
            self.assertEqual(alias.duplicate_of_chunk_id, original.chunk_id)
            claim = build_investigation_claim(context.field, context.text, evidence)
            self.assertIn(23, claim.source_numbers)
            formatted = format_review_evidence(claim.evidence)
            self.assertEqual(formatted.count('原文：' + original.text), 1)
            self.assertIn('[资料23]', formatted)
            self.assertIn(f'正文同[资料{original.source_number}]', formatted)
            self.assertIn('页码：99', formatted)

    def test_last_tool_result_is_seen_by_summary_and_no_more_tool_execution(self):
        request = ChatCompletionMessage(role='assistant', content=None, tool_calls=[{
            'id': 'last-call', 'type': 'function',
            'function': {'name': 'search_paper_evidence', 'arguments': '{"query":"条件"}'}}])
        replies = [request, ChatCompletionMessage(role='assistant', content='最后结果显示仍缺少实验条件。')]
        snapshots = []
        def generate(**kwargs):
            from copy import deepcopy
            snapshots.append(deepcopy(kwargs))
            return replies.pop(0)
        with patch.object(evidence_react, 'generate_tool_message', side_effect=generate), \
             patch.object(evidence_react, 'execute_research_tool', return_value=[{
                 'chunk_id': self.new_chunk, 'text': '最后一轮新原文'}]) as execute:
            result = evidence_react.run_evidence_react(Mock(), self.context, '核对', max_rounds=1)
        execute.assert_called_once()
        self.assertEqual(snapshots[1]['tool_choice'], 'none')
        self.assertIn('最后一轮新原文', snapshots[1]['messages'][-2]['content'])
        self.assertEqual(snapshots[1]['messages'][-2]['tool_call_id'], 'last-call')
        self.assertEqual(result['status'], 'limit_reached')
        self.assertIn('仍缺少实验条件', result['summary'])

    def test_limit_summary_cannot_execute_another_tool(self):
        request = ChatCompletionMessage(role="assistant", content=None, tool_calls=[{
            "id": "call", "type": "function",
            "function": {"name": "search_paper_evidence", "arguments": '{"query":"条件"}'}}])
        with patch.object(evidence_react, "generate_tool_message", side_effect=[request, request]), \
             patch.object(evidence_react, "execute_research_tool", return_value=[]) as execute:
            with self.assertRaisesRegex(RuntimeError, "总结未返回有效正文"):
                evidence_react.run_evidence_react(Mock(), self.context, "核对", max_rounds=1)
        execute.assert_called_once()
