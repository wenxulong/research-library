"""初稿持久化与无模型下载：隔离数据库，只替换外部模型。"""
from io import BytesIO
import json
import unittest
from zipfile import ZipFile
from sqlalchemy import func, select

import test_main as fixtures
from model import ExperimentImport, PaperDraft
from test_paper_draft import DraftFixture


class TestSavedPaperDraft(DraftFixture, unittest.TestCase):
    def generate(self):
        response = fixtures.client.post(self.base, json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_generation_persists_content_materials_and_request(self):
        saved = self.generate()
        self.assertIsInstance(saved['id'], int)
        self.assertTrue(saved['created_at'])
        self.assertEqual(saved['writing_request']['implementation_details'], '已实现固定阈值缓存')
        self.assertEqual(saved['writing_request']['metrics'], self.request['metrics'])
        with fixtures.TestingSessionLocal() as db:
            record = db.get(PaperDraft, saved['id'])
            self.assertEqual(record.import_id, self.import_id)
            self.assertEqual(record.draft_data['content'], saved['content'])
            self.assertEqual(record.materials_snapshot['analysis']['import_id'], self.import_id)

    def test_read_and_repeated_export_do_not_call_models_or_change_content(self):
        saved = self.generate()
        self.analysis_model.reset_mock(); self.writer.reset_mock()
        read = fixtures.client.get(f"/paper-drafts/{saved['id']}")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json(), saved)
        exports = []
        for _ in range(2):
            response = fixtures.client.get(f"/paper-drafts/{saved['id']}/export")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers['content-type'], 'application/zip')
            with ZipFile(BytesIO(response.content)) as archive:
                exports.append({name: archive.read(name) for name in archive.namelist()})
                self.assertEqual(json.loads(archive.read('draft.json'))['content'], saved['content'])
        self.assertEqual(exports[0], exports[1])
        self.analysis_model.assert_not_called(); self.writer.assert_not_called()

    def test_later_experiment_changes_do_not_change_saved_materials(self):
        saved = self.generate()
        with fixtures.TestingSessionLocal() as db:
            original = json.loads(json.dumps(db.get(PaperDraft, saved['id']).materials_snapshot))
            imported = db.get(ExperimentImport, self.import_id)
            imported.rows = [{'changed': 'later data'}]
            imported.plan_snapshot = {}
            db.commit()
        self.analysis_model.reset_mock(); self.writer.reset_mock()
        response = fixtures.client.get(f"/paper-drafts/{saved['id']}/export")
        self.assertEqual(response.status_code, 200, response.text)
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(json.loads(archive.read('materials.json')), original)
        self.analysis_model.assert_not_called(); self.writer.assert_not_called()

    def test_missing_draft_is_404_without_models(self):
        for suffix in ['', '/export']:
            self.assertEqual(fixtures.client.get('/paper-drafts/999' + suffix).status_code, 404)
        self.analysis_model.assert_not_called(); self.writer.assert_not_called()

    def test_repeated_generation_creates_separate_saved_records(self):
        first = self.generate(); second = self.generate()
        self.assertNotEqual(first['id'], second['id'])
        with fixtures.TestingSessionLocal() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(PaperDraft)), 2)
        self.assertEqual(fixtures.client.get(f"/paper-drafts/{first['id']}").json(), first)

    def test_failed_model_output_does_not_save_partial_record(self):
        self.writer.return_value = '{}'
        response = fixtures.client.post(self.base, json=self.request)
        self.assertEqual(response.status_code, 502, response.text)
        with fixtures.TestingSessionLocal() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(PaperDraft)), 0)


if __name__ == '__main__':
    unittest.main()
