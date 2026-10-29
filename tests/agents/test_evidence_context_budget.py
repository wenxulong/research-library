"""去重、预算及实际工具消息链路；不调用模型服务。"""
import json
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch
from openai.types.chat import ChatCompletionMessage
from research_library.agents import evidence_react
from tests.agents.test_evidence_investigation import InvestigationFixture


class TestEvidenceContextBudget(unittest.TestCase):
    def test_duplicate_text_is_not_resent_and_input_is_not_mutated(self):
        budget = evidence_react.EvidenceContextBudget(100)
        item = {'chunk_id': 1, 'paper_title': '论文', 'text': '完整原文'}
        result = budget.compact([item, item])
        self.assertEqual(result[0], item)
        self.assertNotIn('text', result[1])
        self.assertEqual(result[1]['context_status'], 'already_provided')
        self.assertEqual(budget.remaining_chars, 96)
        self.assertEqual(item['text'], '完整原文')

    def test_budget_keeps_whole_chunks_and_can_still_fit_smaller_chunks(self):
        budget = evidence_react.EvidenceContextBudget(5)
        result = budget.compact([
            {'chunk_id': 1, 'text': '123456'},
            {'chunk_id': 2, 'text': '12345'},
            {'chunk_id': 3, 'text': 'x'},
        ])
        self.assertEqual(result[0]['context_status'], 'context_budget_exceeded')
        self.assertNotIn('text', result[0])
        self.assertEqual(result[1]['text'], '12345')
        self.assertEqual(result[2]['context_status'], 'context_budget_exceeded')
        self.assertEqual(list(budget.evidence_by_chunk_id), [2])
        self.assertEqual(budget.remaining_chars, 0)


class TestEvidenceBudgetLoop(InvestigationFixture, unittest.TestCase):
    def test_initial_and_tool_evidence_share_budget_across_parallel_calls(self):
        calls = [{'id': f'call_{n}', 'type': 'function', 'function': {
            'name': 'search_paper_evidence', 'arguments': '{"query":"实验条件"}'}}
            for n in (1, 2)]
        replies = [ChatCompletionMessage(role='assistant', content=None, tool_calls=calls),
                   ChatCompletionMessage(role='assistant', content='尚有证据缺口')]
        snapshots = []
        def generate(**kwargs):
            snapshots.append(deepcopy(kwargs['messages']))
            return replies.pop(0)
        original = self.context.evidence[0].model_dump(mode='json')
        new = {'chunk_id': self.new_chunk, 'text': '唯一新增原文', 'page_number': 3}
        initial_chars = sum(len(item.text) for item in self.context.evidence)
        # 初始额度为总预算的一半；此处让全部初始证据可容纳。
        budget_limit = initial_chars * 2 + len(new['text'])
        omitted = {'chunk_id': 999999, 'text': 'X' * (budget_limit + 1)}
        with patch.object(evidence_react, 'generate_tool_message', side_effect=generate), \
             patch.object(evidence_react, 'execute_research_tool', return_value=[original, new, omitted]):
            result = evidence_react.run_evidence_react(
                Mock(), self.context, '查证', max_evidence_chars=budget_limit)
        tools = [json.loads(m['content']) for m in snapshots[1] if m['role'] == 'tool']
        self.assertEqual([o['new_evidence_count'] for o in tools], [1, 0])
        self.assertEqual(tools[0]['evidence'][0]['context_status'], 'already_provided')
        self.assertEqual(tools[1]['evidence'][1]['context_status'], 'already_provided')
        self.assertEqual(tools[0]['evidence'][2]['context_status'], 'context_budget_exceeded')
        self.assertEqual(tools[1]['remaining_evidence_chars'], initial_chars)
        final_context = json.dumps(snapshots[1], ensure_ascii=False)
        self.assertEqual(final_context.count(new['text']), 1)
        self.assertNotIn(omitted['text'], final_context)
        self.assertNotIn(999999, [item['chunk_id'] for item in result['evidence']])
        self.assertEqual(result['steps'][0]['observation'], tools[0])
        self.assertEqual(result['status'], 'finished')

    def test_nonpositive_budget_rejected_before_model_call(self):
        with patch.object(evidence_react, 'generate_tool_message') as generate:
            with self.assertRaises(ValueError):
                evidence_react.run_evidence_react(Mock(), self.context, '查证', max_evidence_chars=0)
        generate.assert_not_called()
