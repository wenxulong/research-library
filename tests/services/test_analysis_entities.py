"""验证实体抽取的格式、来源和保存链路；使用隔离数据库与模拟模型。"""
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import research_library.agents.paper_analysis_agent as agent
import tests.fixtures.main as fixtures
from research_library.db.model import ResearchTask
from research_library.schemas import ResearchTaskAnalysisResponse

ENTITY = {
    'canonical_name': 'KCSC-B',
    'entity_type': 'algorithm',
    'aliases': ['知识约束缓存算法'],
    'source_numbers': [1],
}


def analysis_data(**updates):
    data = dict(summary='概述[资料1]', methods=['方法[资料1]'], experiments=[],
                conclusions=[], limitations=[], evidence_notes=[], entities=[ENTITY])
    data.update(updates)
    return data


class TestEntityAnalysisParsing(unittest.TestCase):
    def test_entity_objects_and_aliases_survive_parsing(self):
        data = agent.parse_analysis_response(json.dumps(analysis_data()))
        self.assertEqual(data['entities'], [ENTITY])
        agent.validate_analysis_citations(data, 1)

    def test_old_model_output_and_old_response_default_to_empty_entities(self):
        old = analysis_data()
        old.pop('entities')
        parsed = agent.parse_analysis_response(json.dumps(old))
        self.assertEqual(parsed['entities'], [])
        response = ResearchTaskAnalysisResponse(task_id=1, question='问题', sources=[], **old)
        self.assertEqual(response.entities, [])

    def test_wrong_entity_shape_is_rejected(self):
        for entities in ({'name': 'KCSC-B'}, ['KCSC-B'], [{'canonical_name': 'KCSC-B'}]):
            with self.subTest(entities=entities), self.assertRaises(RuntimeError):
                agent.parse_analysis_response(json.dumps(analysis_data(entities=entities)))

    def test_entity_source_number_must_belong_to_current_context(self):
        for number in (0, 2):
            data = analysis_data(entities=[{**ENTITY, 'source_numbers': [number]}])
            with self.subTest(number=number), self.assertRaisesRegex(RuntimeError, '资料编号'):
                agent.validate_analysis_citations(data, 1)

    def test_agent_requests_entities_in_same_model_call(self):
        chunk = SimpleNamespace(id=10, page_number=2, text='KCSC-B是一种知识约束算法。')
        paper = SimpleNamespace(id=3, title='论文')
        with patch('research_library.agents.paper_analysis_agent.collect_analysis_matches', return_value=[(chunk,paper,.9)]), \
             patch('research_library.agents.paper_analysis_agent.generate_text', return_value=json.dumps(analysis_data())) as generate:
            result = agent.analyze_research_task(None, 1, '算法是什么？')
        generate.assert_called_once()
        self.assertEqual(result.entities, [ENTITY])
        self.assertIn('aliases只填写原文明确出现', generate.call_args.kwargs['system_prompt'])
        self.assertIn('entities数组中的每项必须', generate.call_args.kwargs['user_prompt'])
        self.assertNotIn('数组中每一项都必须是字符串', generate.call_args.kwargs['user_prompt'])


class TestEntityAnalysisPersistence(fixtures.ComparisonDatabaseFixture, unittest.TestCase):
    def test_entities_roundtrip_through_api_and_saved_task_json(self):
        result = agent.PaperAnalysisResult(
            question=self.analysis.question, summary=self.analysis.summary,
            methods=self.analysis.methods, experiments=self.analysis.experiments,
            conclusions=self.analysis.conclusions, limitations=self.analysis.limitations,
            sources=[agent.AnalysisSource(**s.model_dump()) for s in self.analysis.sources],
            entities=[ENTITY],
        )
        with patch('research_library.api.tasks.paper_analysis_agent.analyze_research_task', return_value=result):
            response = fixtures.client.post(f'/research-tasks/{self.task_id}/analysis')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['entities'], [ENTITY])
        with fixtures.TestingSessionLocal() as db:
            saved = json.loads(db.get(ResearchTask, self.task_id).result)
            self.assertEqual(saved['entities'], [ENTITY])
        reread = fixtures.client.get(f'/research-tasks/{self.task_id}/analysis')
        self.assertEqual(reread.status_code, 200)
        self.assertEqual(reread.json()['entities'], [ENTITY])

    def test_reading_legacy_json_does_not_rewrite_old_database_record(self):
        old = self.analysis.model_dump()
        old.pop('entities')
        raw = json.dumps(old)
        with fixtures.TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).result = raw
            db.commit()
        response = fixtures.client.get(f'/research-tasks/{self.task_id}/analysis')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['entities'], [])
        with fixtures.TestingSessionLocal() as db:
            self.assertEqual(db.get(ResearchTask, self.task_id).result, raw)
