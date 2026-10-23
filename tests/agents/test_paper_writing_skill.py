"""写作Skill：隔离数据库和模型，验证决策、工具执行及确认保存。"""
import copy
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import tests.fixtures.main as fixtures
from research_library.db.model import PaperDraft
import research_library.agents.paper_writing_skill as paper_writing_skill
from tests.services.test_paper_draft import DraftFixture


class TestPaperWritingSkill(DraftFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        response = fixtures.client.post(self.base, json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        self.saved = response.json()
        self.url = f"/paper-drafts/{self.saved['id']}/skill"
        self.writer.reset_mock()
        self.analysis_model.reset_mock()
        self.paragraphs = [{'text': '扩写后正文', 'source_numbers': [19]}]
        self.writer.return_value = json.dumps({'paragraphs': self.paragraphs})
        self.decision = {'action': 'expand_section', 'section': 'introduction', 'instruction': '补充研究背景'}
        self.router = self.enterContext(patch('research_library.agents.paper_writing_skill.generate_text', return_value=json.dumps(self.decision)))

    def run_skill(self, instruction='帮我扩写引言，补充研究背景'):
        return fixtures.client.post(self.url, json={'instruction': instruction})

    def read(self):
        return fixtures.client.get(f"/paper-drafts/{self.saved['id']}").json()

    def test_parser_returns_ask_user_and_accepts_code_blocks(self):
        decision = paper_writing_skill.parse_skill_decision('```json\n{"action":"ask_user"}\n```')
        self.assertEqual(decision.action, 'ask_user')
        self.assertIsNone(decision.section)

    def test_parser_rejects_invalid_action_section_or_incomplete_execution(self):
        bad = [
            {}, {'action': 'delete_paper'},
            {'action': 'expand_section', 'section': 'abstract', 'instruction': '扩写'},
            {'action': 'expand_section', 'instruction': '扩写'},
            {'action': 'expand_section', 'section': 'methods', 'instruction': '  '},
        ]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                paper_writing_skill.parse_skill_decision(json.dumps(payload))

    def test_each_section_calls_existing_tool_without_saving_or_reanalyzing(self):
        for field in ['introduction', 'related_work', 'methods', 'experiments', 'discussion', 'conclusion']:
            with self.subTest(field=field):
                self.router.return_value = json.dumps({**self.decision, 'section': field})
                response = self.run_skill()
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result['status'], 'suggested')
                self.assertEqual(result['suggestion'], {'draft_id': self.saved['id'], 'section': field, 'paragraphs': self.paragraphs})
                self.assertEqual(self.read(), self.saved)
        self.assertEqual(self.router.call_count, 6)
        self.assertEqual(self.writer.call_count, 6)
        self.analysis_model.assert_not_called()

    def test_ambiguous_request_asks_user_without_writing(self):
        for message in ['请说明需要扩写哪一章', '']:
            self.router.return_value = json.dumps({'action': 'ask_user', 'message': message})
            response = self.run_skill('帮我写长一点')
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result['status'], 'needs_clarification')
            self.assertIsNone(result['suggestion'])
            self.assertTrue(result['message'])
        self.writer.assert_not_called()
        self.assertEqual(self.read(), self.saved)

    def test_loaded_skill_is_passed_to_both_decision_and_writer(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'SKILL.md'
            instructions = '---\nname: paper-section-expand\ndescription: test\n---\n测试专用章节写作指引'
            path.write_text(instructions, encoding='utf-8')
            with patch.object(paper_writing_skill, 'SKILL_PATH', path):
                response = self.run_skill('扩写引言的背景')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(instructions, self.router.call_args.kwargs['system_prompt'])
        self.assertIn(instructions, self.writer.call_args.kwargs['system_prompt'])
        payload = json.loads(self.router.call_args.kwargs['user_prompt'])
        self.assertEqual(payload['current_draft_id'], self.saved['id'])
        self.assertEqual(payload['user_instruction'], '扩写引言的背景')
        self.assertNotIn('materials', payload)  # 识别要求不需要整份原文。
        self.assertEqual(self.read(), self.saved)

    def test_bad_decision_can_retry_before_calling_tool(self):
        self.router.side_effect = ['{}', json.dumps(self.decision)]
        response = self.run_skill()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.router.call_count, 2)
        self.writer.assert_called_once()

    def test_invalid_decision_never_executes_tool(self):
        self.router.return_value = '{"action":"delete_paper"}'
        response = self.run_skill()
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.router.call_count, 2)
        self.writer.assert_not_called()
        self.assertEqual(self.read(), self.saved)

    def test_unknown_citation_rejected_by_existing_writer(self):
        self.writer.return_value = json.dumps({'paragraphs': [{'text': '无效引用', 'source_numbers': [999]}]})
        response = self.run_skill()
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.writer.call_count, 2)
        self.assertEqual(self.read(), self.saved)

    def test_confirmation_uses_existing_patch_and_download(self):
        with fixtures.TestingSessionLocal() as db:
            materials = copy.deepcopy(db.get(PaperDraft, self.saved['id']).materials_snapshot)
        response = self.run_skill()
        self.assertEqual(response.status_code, 200, response.text)
        suggestion = response.json()['suggestion']
        content = copy.deepcopy(self.saved['content'])
        content[suggestion['section']] = suggestion['paragraphs']
        updated = fixtures.client.patch(f"/paper-drafts/{self.saved['id']}", json=content)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()['content'], content)
        self.assertEqual(updated.json()['id'], self.saved['id'])
        self.assertEqual(updated.json()['created_at'], self.saved['created_at'])
        self.router.reset_mock(); self.writer.reset_mock()
        exported = fixtures.client.get(f"/paper-drafts/{self.saved['id']}/export")
        self.assertEqual(exported.status_code, 200)
        with ZipFile(BytesIO(exported.content)) as archive:
            self.assertEqual(json.loads(archive.read('draft.json'))['content'], content)
            self.assertEqual(json.loads(archive.read('materials.json')), materials)
            self.assertIn('扩写后正文', archive.read('paper.md').decode())
        self.router.assert_not_called(); self.writer.assert_not_called()

    def test_missing_draft_or_invalid_input_never_calls_models(self):
        response = fixtures.client.post('/paper-drafts/999/skill', json={'instruction': '扩写引言'})
        self.assertEqual(response.status_code, 404)
        for payload in [{}, {'instruction': ''}, {'instruction': 12}, {'instruction': 'a' * 2001}]:
            self.assertEqual(fixtures.client.post(self.url, json=payload).status_code, 422)
        self.router.assert_not_called(); self.writer.assert_not_called()

    def test_model_failure_preserves_original(self):
        self.router.side_effect = RuntimeError('模拟模型连接失败')
        response = self.run_skill()
        self.assertEqual(response.status_code, 502)
        self.writer.assert_not_called()
        self.assertEqual(self.read(), self.saved)


if __name__ == '__main__':
    unittest.main()
