"""实验分析接口：隔离数据库，替换 DeepSeek，不发送正式数据。"""
import json
import unittest
from unittest.mock import patch

import test_main as fixtures
from experiment_analysis_agent import parse_experiment_analysis
from test_research_planning import ResearchPlanningFixture


CONTENT = dict(summary="测试结果总结", differences=["错误复用率存在差异"],
               tradeoffs=["需要结合延迟判断"], limitations=["重复次数有限"],
               next_experiments=["增加重复实验"])


class TestExperimentAnalysisParsing(unittest.TestCase):
    def test_code_block_is_parsed(self):
        result = parse_experiment_analysis("```json\n" + json.dumps(CONTENT) + "\n```")
        self.assertEqual(result.model_dump(), CONTENT)

    def test_invalid_json_or_fields_raise_runtime_error(self):
        for text in ["not json", '{}', json.dumps({**CONTENT, "summary": 12})]:
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                parse_experiment_analysis(text)


class TestExperimentAnalysisAPI(ResearchPlanningFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        set_id = self.create_set()["id"]
        self.select(set_id, candidate_number=1)
        self.saved_plan = self.plan(set_id)
        csv = ("method,dataset,scenario,seed,run_id,error_reuse_rate,latency_ms\n"
               "baseline,demo,normal,1,1,0.08,120\n"
               "baseline,demo,normal,2,2,0.10,140\n"
               "ours,demo,normal,1,3,0.04,160\n"
               "baseline,demo,shift,1,4,0.99,999\n")
        upload = fixtures.client.post(f"/research-question-sets/{set_id}/experiment-imports",
            files={"file": ("results.csv", csv.encode(), "text/csv")})
        self.assertEqual(upload.status_code, 201, upload.text)
        self.import_id = upload.json()["id"]
        self.url = f"/experiment-imports/{self.import_id}/analysis"
        self.request = dict(metrics=["error_reuse_rate", "latency_ms"], dataset="demo", scenario="normal")
        self.generate = self.enterContext(patch("experiment_analysis_agent.generate_text",
                                               return_value=json.dumps(CONTENT)))

    def test_all_metrics_reach_model_once_and_only_selected_groups_are_returned(self):
        response = fixtures.client.post(self.url, json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["import_id"], self.import_id)
        self.assertEqual(data["content"], CONTENT)
        self.assertEqual([item["metric"] for item in data["statistics"]], self.request["metrics"])
        self.assertEqual(data["statistics"][0]["groups"][0]["mean"], 0.09)
        self.assertEqual(data["statistics"][1]["groups"][0]["mean"], 130)
        self.assertTrue(all(group["scenario"] == "normal" for item in data["statistics"] for group in item["groups"]))
        self.generate.assert_called_once()
        prompt = self.generate.call_args.kwargs["user_prompt"]
        supplied = json.loads(prompt.split("程序计算的统计结果：\n")[1].split("\n\n输出JSON Schema：")[0])
        self.assertEqual(supplied, data["statistics"])
        self.assertIn(self.saved_plan["research_question"], prompt)

    def test_duplicate_metrics_are_removed(self):
        response = fixtures.client.post(self.url, json={**self.request,
            "metrics": [" latency_ms ", "latency_ms"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["statistics"]), 1)
        self.generate.assert_called_once()

    def test_invalid_analysis_range_does_not_call_model(self):
        for changes in [dict(metrics=["error_reuse_rate", "absent"]), dict(metrics=[" "]),
                        dict(dataset="absent"), dict(scenario=" ")]:
            with self.subTest(changes=changes):
                response = fixtures.client.post(self.url, json={**self.request, **changes})
                self.assertEqual(response.status_code, 409, response.text)
        self.generate.assert_not_called()

    def test_missing_record_or_missing_input(self):
        self.assertEqual(fixtures.client.post("/experiment-imports/999/analysis", json=self.request).status_code, 404)
        self.assertEqual(fixtures.client.post(self.url, json={**self.request, "metrics": []}).status_code, 422)
        self.generate.assert_not_called()

    def test_invalid_model_output_retries_then_returns_502(self):
        self.generate.return_value = "{}"
        response = fixtures.client.post(self.url, json=self.request)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.generate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
