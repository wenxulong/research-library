"""自主查证闭环与独立存储；模型/向量请求全部模拟。"""
import json
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from openai.types.chat import ChatCompletionMessage

import tests.fixtures.main as fixtures
import research_library.agents.evidence_react as evidence_react
import research_library.core.llm_service as llm_service
import research_library.services.report_claim_investigation as service
from research_library.repositories.report_claim_investigation_crud import get_investigation, save_investigation
from research_library.db.model import ReportJob, ReportClaimInvestigation
from research_library.services.report_claim_service import get_report_claim_context
from research_library.schemas import CheckedComparisonReportResponse, SavedReportClaimInvestigationResponse


class InvestigationFixture(fixtures.ComparisonDatabaseFixture):
    def setUp(self):
        super().setUp()
        from research_library.agents.comparison_agent import parse_comparison_response
        from research_library.schemas import ComparisonSourceResponse, ResearchTaskReviewResponse, ReviewItemResponse
        sources = [ComparisonSourceResponse(source_number=n, **s.model_dump())
                   for n, s in enumerate(self.analysis.sources, 1)]
        report = parse_comparison_response(json.dumps(self.answer), self.task_id,
                                           self.analysis.question, sources)
        reviews = []
        for field in ('method_comparisons', 'experiment_comparisons',
                      'conclusion_comparisons', 'evidence_notes'):
            for i, statement in enumerate(getattr(report, field)):
                statement.claim_id = f'{field}-{i}'
                reviews.append(ReviewItemResponse(claim_id=statement.claim_id, field=field,
                    claim=statement.text, verdict='partially_supported', reason='条件不完整',
                    source_numbers=statement.source_numbers))
        review = ResearchTaskReviewResponse(task_id=self.task_id, items=reviews)
        checked = CheckedComparisonReportResponse(task_id=self.task_id, report=report,
            review=review, initial_review=review, passed=False, correction_rounds=0)
        self.base_report = checked.model_dump(mode='json')
        with fixtures.TestingSessionLocal() as db:
            job = ReportJob(task_id=self.task_id, status='succeeded', stage='finished',
                            analysis_snapshot={}, result=self.base_report)
            db.add(job)
            db.commit()
            self.job_id = job.id
            self.context = get_report_claim_context(db, job.id, report.method_comparisons[0].claim_id)
        self.new_chunk = self.analysis.sources[4].chunk_id

    def react_result(self, status='finished'):
        return {'status': status, 'summary': '候选总结不能冒充原文', 'steps': [],
                'evidence': [e.model_dump(mode='json') for e in self.context.evidence]
                    + [{'chunk_id': self.new_chunk, 'text': '伪造文字', 'score': None}]*2}

    def run_saved(self, db):
        return service.create_saved_investigation(db, self.job_id,
                                                   self.context.claim_id, '核对条件')


class TestEvidenceInvestigation(InvestigationFixture, unittest.TestCase):
    def test_tool_model_wrapper_accepts_messages_keyword(self):
        client = Mock()
        message = ChatCompletionMessage(role='assistant', content='完成')
        client.chat.completions.create.return_value.choices = [
            Mock(finish_reason='stop', message=message)]
        with patch('research_library.core.llm_service.OpenAI') as factory, patch('research_library.core.llm_service.get_deepseek_api_key', return_value='test'):
            factory.return_value.__enter__.return_value = client
            result = llm_service.generate_tool_message(messages=[{'role':'user','content':'测试'}], tools=[])
        self.assertIs(result, message)
        self.assertEqual(client.chat.completions.create.call_args.kwargs['messages'][0]['content'], '测试')

    def test_react_sends_tool_observation_back_and_deduplicates(self):
        message = ChatCompletionMessage(role='assistant', content=None, tool_calls=[{
            'id':'call_1', 'type':'function', 'function':{
                'name':'search_paper_evidence', 'arguments':'{"query":"实验条件"}'}}])
        replies = [message, ChatCompletionMessage(role='assistant', content='资料仍不足')]
        snapshots = []
        def generate(**kwargs):
            snapshots.append(deepcopy(kwargs['messages']))
            return replies.pop(0)
        with patch('research_library.agents.evidence_react.generate_tool_message', side_effect=generate), \
             patch('research_library.agents.evidence_react.execute_research_tool', return_value=[{'chunk_id':self.new_chunk}]*2):
            result = evidence_react.run_evidence_react(Mock(), self.context, '核对条件')
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(snapshots[1][-1]['role'], 'tool')
        self.assertEqual(snapshots[1][-1]['tool_call_id'], 'call_1')
        self.assertEqual(len(result['evidence']), 3)

    def test_tool_errors_and_round_limit_are_not_success(self):
        message = ChatCompletionMessage(role='assistant', content=None, tool_calls=[{
            'id':'bad', 'type':'function', 'function':{'name':'unknown','arguments':'not-json'}}])
        with patch('research_library.agents.evidence_react.generate_tool_message', return_value=message):
            result = evidence_react.run_evidence_react(Mock(), self.context, '查证', max_rounds=1)
        self.assertEqual(result['status'], 'limit_reached')
        self.assertFalse(result['steps'][0]['observation']['ok'])

    def test_save_read_reuses_report_numbers_and_real_text_without_modifying_report(self):
        def review(claim):
            self.assertEqual(claim.claim, self.context.text)
            self.assertEqual(claim.source_numbers, [1, 3, 5])
            self.assertEqual(claim.evidence[-1].text, self.raw_texts[4])
            return self.context.review.model_copy(update={'verdict':'supported'})
        with fixtures.TestingSessionLocal() as db, \
             patch.object(service, 'run_evidence_react', return_value=self.react_result()), \
             patch.object(service, 'review_claim', side_effect=review):
            saved = self.run_saved(db)
            record = get_investigation(db, saved.id)
            self.assertEqual(record.base_report, self.base_report)
            self.assertEqual(db.get(ReportJob, self.job_id).result, self.base_report)
            self.assertEqual(saved.result.added_chunk_ids, [self.new_chunk])
            self.assertEqual(saved.result.review.claim_id, self.context.claim_id)
            self.assertEqual(SavedReportClaimInvestigationResponse.model_validate(record), saved)

    def test_new_source_number_uses_whole_report_maximum(self):
        with fixtures.TestingSessionLocal() as db:
            sources = CheckedComparisonReportResponse.model_validate(self.base_report).report.sources
            sources[-1].source_number = 23
            result = service.prepare_investigation_evidence(db, sources, [
                {'chunk_id': self.analysis.sources[1].chunk_id}]*2)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].source_number, 2)
            # 原文仍在数据库，但不在来源登记表，才分配新编号。
            result = service.prepare_investigation_evidence(db, sources[:1]+sources[2:], [
                {'chunk_id': self.analysis.sources[1].chunk_id}]*2)
            self.assertEqual(result[0].source_number, 24)

    def test_generation_failure_does_not_save_candidate(self):
        with fixtures.TestingSessionLocal() as db, \
             patch.object(service, 'run_evidence_react', side_effect=RuntimeError('模拟失败')):
            with self.assertRaises(RuntimeError):
                self.run_saved(db)
            self.assertEqual(db.query(ReportClaimInvestigation).count(), 0)
            self.assertEqual(db.get(ReportJob, self.job_id).result, self.base_report)

    def test_limit_result_can_be_saved_without_claiming_completion(self):
        with fixtures.TestingSessionLocal() as db, \
             patch.object(service, 'run_evidence_react', return_value=self.react_result('limit_reached')), \
             patch.object(service, 'review_claim', return_value=self.context.review):
            self.assertEqual(self.run_saved(db).result.status, 'limit_reached')

    def test_missing_and_unfinished_jobs_do_not_call_models(self):
        with fixtures.TestingSessionLocal() as db, patch.object(service, 'run_evidence_react') as generate:
            with self.assertRaises(LookupError):
                service.create_saved_investigation(db, 9999, 'x', '查证')
            db.get(ReportJob, self.job_id).status = 'running'
            db.flush()
            with self.assertRaises(ValueError):
                self.run_saved(db)
            with self.assertRaises(LookupError):
                get_investigation(db, 9999)
            generate.assert_not_called()

    def test_commit_failure_rolls_back(self):
        db = Mock()
        db.commit.side_effect = RuntimeError('模拟提交失败')
        result = Mock(job_id=self.job_id, claim_id=self.context.claim_id)
        result.model_dump.return_value = {}
        with self.assertRaises(RuntimeError):
            save_investigation(db, self.base_report, result)
        db.rollback.assert_called_once()
