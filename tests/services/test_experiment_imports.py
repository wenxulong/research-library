"""CSV结构检查与导入持久化；不访问正式数据库或真实模型。"""
import unittest

from sqlalchemy import func, select

import tests.fixtures.main as fixtures
from research_library.services.experiment_import_service import parse_experiment_csv
from research_library.db.model import ExperimentImport
from tests.services.test_research_planning import ResearchPlanningFixture


class TestExperimentCSV(unittest.TestCase):
    def test_parses_quotes_commas_newlines_and_additional_columns(self):
        text = ' method ,score,notes\r\n baseline , 0.8 ,"含逗号,的备注"\r\n\r\nours,0.9,"跨\n行"\r\n'
        rows = parse_experiment_csv(text, ["method", "score"])
        self.assertEqual(rows, [
            {"method": "baseline", "score": "0.8", "notes": "含逗号,的备注"},
            {"method": "ours", "score": "0.9", "notes": "跨\n行"},
        ])

    def test_missing_values_and_failed_runs_are_not_filled_or_removed(self):
        rows = parse_experiment_csv('method,score,status\nours,,failed\n', ["method", "score"])
        self.assertEqual(rows, [{"method": "ours", "score": "", "status": "failed"}])

    def test_invalid_csv_is_rejected_with_specific_reason(self):
        cases = [
            ("", "文件为空"),
            ("method,score\n", "只有表头"),
            ("method,score,score\na,1,2\n", "重复列名"),
            ("method,,score\na,b,1\n", "列名不能为空"),
            ("method\na\n", "缺少研究计划要求的列"),
            ("method,score\na\n", "列数与表头不一致"),
            ("method,score\na,1,extra\n", "列数与表头不一致"),
            ('"method,score', "表头格式不正确"),
            ('method,score\n"unclosed,1', "内容格式不正确"),
        ]
        for text, reason in cases:
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, reason):
                    parse_experiment_csv(text, ["method", "score"])


class TestExperimentImportAPI(ResearchPlanningFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.set_id = self.create_set()["id"]
        self.select(self.set_id, candidate_number=1)
        self.saved_plan = self.plan(self.set_id)
        self.csv = (
            'method,dataset,scenario,seed,run_id,error_reuse_rate,status\n'
            'semantic,demo,shift,42,1,0.08,success\n'
            'knowledge,demo,shift,42,2,,failed\n'
        )
        # 上传与查询本身不应再调用任何模型。
        self.generate_questions.reset_mock()
        self.generate_plan.reset_mock()

    def upload(self, content=None, set_id=None, filename="results.csv"):
        content = self.csv.encode() if content is None else content
        set_id = self.set_id if set_id is None else set_id
        return fixtures.client.post(
            f"/research-question-sets/{set_id}/experiment-imports",
            files={"file": (filename, content, "text/csv")},
        )

    def count_imports(self):
        with fixtures.TestingSessionLocal() as db:
            return db.scalar(select(func.count()).select_from(ExperimentImport))

    def test_upload_and_get_preserve_raw_data_rows_and_plan_snapshot(self):
        response = self.upload(filename="folder/results.csv")
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["question_set_id"], self.set_id)
        self.assertEqual(data["filename"], "results.csv")
        self.assertEqual(data["raw_csv"], self.csv)
        self.assertEqual(data["plan_snapshot"], self.saved_plan)
        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual(data["rows"][0]["error_reuse_rate"], "0.08")
        self.assertEqual(data["rows"][1]["error_reuse_rate"], "")
        self.assertEqual(data["rows"][1]["status"], "failed")
        self.assertIsNotNone(data["created_at"])
        read = fixtures.client.get(f"/experiment-imports/{data['id']}")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json(), data)
        with fixtures.TestingSessionLocal() as db:
            self.assertEqual(db.get(ExperimentImport, data["id"]).rows, data["rows"])
        self.generate_questions.assert_not_called()
        self.generate_plan.assert_not_called()

    def test_utf8_bom_is_supported(self):
        response = self.upload(self.csv.encode("utf-8-sig"))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["raw_csv"], self.csv)
        self.assertIn("method", response.json()["rows"][0])

    def test_invalid_encoding_is_400_and_not_saved(self):
        response = self.upload(b'\xff\xfeinvalid')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("UTF-8", response.json()["detail"])
        self.assertEqual(self.count_imports(), 0)

    def test_invalid_csv_is_409_and_not_saved(self):
        for content in (b"", b"method\na\n", self.csv.splitlines()[0].encode()):
            with self.subTest(content=content):
                response = self.upload(content)
                self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.count_imports(), 0)

    def test_missing_record_is_404_and_not_saved(self):
        self.assertEqual(self.upload(set_id=999).status_code, 404)
        self.assertEqual(fixtures.client.get("/experiment-imports/999").status_code, 404)
        self.assertEqual(self.count_imports(), 0)

    def test_missing_plan_is_409_and_not_saved(self):
        self.select(self.set_id, candidate_number=2)
        response = self.upload()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("请先生成研究计划", response.json()["detail"])
        self.assertEqual(self.count_imports(), 0)

    def test_repeated_imports_create_distinct_records(self):
        first = self.upload().json()
        updated_csv = self.csv.replace("0.08", "0.07")
        response = self.upload(updated_csv.encode())
        self.assertEqual(response.status_code, 201, response.text)
        second = response.json()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.count_imports(), 2)
        self.assertEqual(fixtures.client.get(f"/experiment-imports/{first['id']}").json(), first)

    def test_regenerated_plan_does_not_change_old_import_snapshot(self):
        first = self.upload().json()
        latest_plan = self.plan(self.set_id, budget="50元")
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        second = response.json()
        self.assertEqual(first["plan_snapshot"]["conditions"]["budget"], "100元")
        self.assertEqual(second["plan_snapshot"], latest_plan)
        self.assertEqual(fixtures.client.get(f"/experiment-imports/{first['id']}").json(), first)

    def test_changing_selected_question_keeps_previous_imports(self):
        first = self.upload().json()
        self.select(self.set_id, candidate_number=2)
        read = fixtures.client.get(f"/experiment-imports/{first['id']}")
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json(), first)
        self.assertEqual(self.count_imports(), 1)


if __name__ == "__main__":
    unittest.main()
