"""研究报告压缩包测试：使用内存数据库和模拟模型回答。"""
from io import BytesIO
import json
import re
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

import test_main as fixtures
from model import ResearchQuestionSet
from research_report_export import render_list
from test_research_planning import ResearchPlanningFixture


class TestResearchReportExport(ResearchPlanningFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.set_id = self.create_set()["id"]
        self.select(self.set_id, candidate_number=1)
        self.saved_plan = self.plan(self.set_id)
        # 使用不连续的证据编号，确认导出不会重新编号。
        mapping = {source["source_number"]: 10 + index * 3
                   for index, source in enumerate(self.saved_plan["sources"])}
        for source in self.saved_plan["sources"]:
            source["source_number"] = mapping[source["source_number"]]
        basis = self.saved_plan["basis"]
        basis["source_numbers"] = [mapping[number] for number in basis["source_numbers"]]
        if basis["evidence_review"]:
            basis["evidence_review"]["source_numbers"] = basis["source_numbers"]
        with fixtures.TestingSessionLocal() as db:
            db.get(ResearchQuestionSet, self.set_id).plan = self.saved_plan
            db.commit()
        self.csv = ("method,dataset,scenario,seed,run_id,error_reuse_rate,latency_ms\n"
                    "baseline,demo,normal,1,1,0.08,120\n"
                    "baseline,demo,normal,2,2,0.10,140\n"
                    "ours,demo,normal,1,3,0.04,160\n"
                    "baseline,demo,shift,1,4,0.99,999\n")
        response = fixtures.client.post(f"/research-question-sets/{self.set_id}/experiment-imports",
            files={"file": ("results.csv", self.csv.encode(), "text/csv")})
        self.assertEqual(response.status_code, 201, response.text)
        self.import_id = response.json()["id"]
        self.url = f"/experiment-imports/{self.import_id}/research-report/export"
        self.request = dict(metrics=["error_reuse_rate", "latency_ms"], dataset="demo", scenario="normal")
        self.content = dict(summary="测试分析总结", differences=["方法存在差异"],
                            tradeoffs=["需同时考虑延迟"], limitations=["重复次数少"],
                            next_experiments=["增加重复实验"])
        self.generate = self.enterContext(patch("experiment_analysis_agent.generate_text",
                                               return_value=json.dumps(self.content)))

    def test_zip_contains_both_metrics_valid_images_snapshots_and_source_links(self):
        response = fixtures.client.post(self.url, json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn(f"research-report-{self.import_id}.zip", response.headers["content-disposition"])
        self.generate.assert_called_once()
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {
                "report.md", "results.csv", "analysis.json", "plan.json",
                "figures/metric-1.png", "figures/metric-2.png",
            })
            markdown = archive.read("report.md").decode("utf-8")
            for metric in self.request["metrics"]:
                self.assertIn(f"### 指标：{metric}", markdown)
            self.assertEqual(markdown.count("## 五、实验分析"), 1)
            self.assertEqual(markdown.count("## 六、文献资料来源"), 1)
            self.assertIn(self.content["summary"], markdown)
            for filename in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown):
                with Image.open(BytesIO(archive.read(filename))) as image:
                    self.assertEqual(image.size, (1200, 750))
                    image.verify()
            self.assertEqual(archive.read("results.csv").decode(), self.csv)
            self.assertEqual(json.loads(archive.read("plan.json")), self.saved_plan)
            analysis = json.loads(archive.read("analysis.json"))
            self.assertEqual([item["metric"] for item in analysis["statistics"]], self.request["metrics"])
            self.assertEqual(analysis["statistics"][0]["groups"][0]["mean"], 0.09)
            self.assertEqual(analysis["statistics"][1]["groups"][0]["mean"], 130)
            for number in self.saved_plan["basis"]["source_numbers"]:
                self.assertIn(f"[资料{number}](#source-{number})", markdown)
                self.assertIn(f'<a id="source-{number}"></a>', markdown)

    def test_invalid_record_metric_or_request_does_not_call_model(self):
        self.assertEqual(fixtures.client.post("/experiment-imports/999/research-report/export",
            json=self.request).status_code, 404)
        self.assertEqual(fixtures.client.post(self.url,
            json={**self.request, "metrics": ["absent"]}).status_code, 409)
        self.assertEqual(fixtures.client.post(self.url,
            json={**self.request, "metrics": []}).status_code, 422)
        self.generate.assert_not_called()

    def test_invalid_model_result_is_502_instead_of_zip(self):
        self.generate.return_value = "{}"
        response = fixtures.client.post(self.url, json=self.request)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.generate.call_count, 2)


class TestReportList(unittest.TestCase):
    def test_empty_and_nonempty_lists(self):
        self.assertEqual(render_list([]), "未提供。")
        self.assertEqual(render_list(["准备数据", "运行实验"]), "- 准备数据\n- 运行实验")


if __name__ == "__main__":
    unittest.main()
