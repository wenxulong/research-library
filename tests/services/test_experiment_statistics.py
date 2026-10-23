"""实验统计和 PNG 下载测试，使用隔离数据库，不调用真实模型。"""
from io import BytesIO
import unittest
import warnings
from unittest.mock import patch

from matplotlib.figure import Figure
from matplotlib import font_manager
from PIL import Image

import tests.fixtures.main as fixtures
import research_library.services.experiment_chart_service as experiment_chart_service
from research_library.services.experiment_statistics_service import summarize_experiment_results
from research_library.db.model import ExperimentImport
from tests.services.test_research_planning import ResearchPlanningFixture


def statistics_for(rows, metric="score"):
    return summarize_experiment_results(ExperimentImport(id=7, rows=rows), metric)


def row(value, method="baseline", status="success"):
    return dict(method=method, dataset="demo", scenario="normal", score=value, status=status)


class TestExperimentStatistics(unittest.TestCase):
    def test_counts_are_exclusive_and_mean_uses_only_valid_successes(self):
        stats = statistics_for([
            row("2"), row("4"), row("99", status="failed"), row(""),
            row("oops"), row("nan"), row("inf"), row("8", status="pending"),
        ])
        group = stats.groups[0]
        self.assertEqual((group.total_count, group.valid_count, group.failed_count,
                          group.missing_count, group.invalid_count, group.unknown_status_count),
                         (8, 2, 1, 1, 3, 1))
        self.assertEqual(group.mean, 3)
        self.assertAlmostEqual(group.stddev, 2 ** 0.5)
        self.assertTrue(stats.status_available)

    def test_without_status_single_valid_value_has_no_standard_deviation(self):
        item = row("0")
        del item["status"]
        stats = statistics_for([item])
        self.assertFalse(stats.status_available)
        self.assertEqual(stats.groups[0].mean, 0)
        self.assertIsNone(stats.groups[0].stddev)

    def test_metric_selection_and_dataset_scenario_grouping(self):
        rows = [row("1"), row("2"), row("3"), row("4")]
        rows[1]["method"] = "ours"
        rows[2]["dataset"] = "other"
        rows[3]["scenario"] = "shift"
        for item in rows:
            item["latency_ms"] = "120"
        stats = statistics_for(rows, "latency_ms")
        self.assertEqual(stats.metric, "latency_ms")
        self.assertEqual(len(stats.groups), 4)
        self.assertTrue(all(group.mean == 120 for group in stats.groups))

    def test_no_valid_values_are_none_not_zero(self):
        group = statistics_for([row(""), row("10", status="failed")]).groups[0]
        self.assertIsNone(group.mean)
        self.assertIsNone(group.stddev)

    def test_missing_metric_or_group_labels_are_rejected(self):
        for rows, metric in [([], "score"), ([row("1")], ""),
                             ([row("1")], "absent"), ([row("1", method=" ")], "score")]:
            with self.subTest(rows=rows, metric=metric), self.assertRaises(ValueError):
                statistics_for(rows, metric)


class TestExperimentChart(unittest.TestCase):
    def test_png_heights_error_bars_and_omitted_methods(self):
        stats = statistics_for([row("2"), row("4"), row("0", method="single"),
                                row("", method="missing")])
        figure = Figure(figsize=(8, 5), dpi=150)
        with patch.object(experiment_chart_service, "Figure", return_value=figure):
            content = experiment_chart_service.build_experiment_chart(stats, "demo", "normal")
        self.assertTrue(content.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(BytesIO(content)) as image:
            self.assertEqual(image.size, (1200, 750))
            image.verify()
        ax = figure.axes[0]
        self.assertEqual([bar.get_height() for bar in ax.patches], [3, 0])
        self.assertEqual(len(ax.collections), 1)  # 只有两次有效运行的方法有误差棒。
        self.assertEqual(ax.get_ylabel(), "score")
        self.assertIn("missing", figure.texts[0].get_text())
        self.assertIn("n=2", ax.get_xticklabels()[0].get_text())

    def test_missing_selection_or_all_invalid_values_are_rejected(self):
        stats = statistics_for([row("")])
        for dataset, scenario in [("absent", "normal"), ("demo", "shift"), ("demo", "normal")]:
            with self.subTest(dataset=dataset, scenario=scenario), self.assertRaises(ValueError):
                experiment_chart_service.build_experiment_chart(stats, dataset, scenario)

    def test_chinese_labels_use_available_system_font(self):
        candidates = {"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "SimHei", "Arial Unicode MS"}
        if not candidates.intersection(font.name for font in font_manager.fontManager.ttflist):
            self.skipTest("系统未安装中文字体")
        stats = statistics_for([row("1", method="普通语义缓存")])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            experiment_chart_service.build_experiment_chart(stats, "demo", "normal")
        self.assertFalse([warning for warning in caught if "Glyph" in str(warning.message)])


class TestExperimentStatisticsAPI(ResearchPlanningFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        set_id = self.create_set()["id"]
        self.select(set_id, candidate_number=1)
        self.plan(set_id)
        csv = ("method,dataset,scenario,seed,run_id,error_reuse_rate,latency_ms\n"
               "baseline,demo,normal,1,1,0.08,120\n"
               "baseline,demo,normal,2,2,0.10,140\n")
        response = fixtures.client.post(
            f"/research-question-sets/{set_id}/experiment-imports",
            files={"file": ("results.csv", csv.encode(), "text/csv")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.import_id = response.json()["id"]
        self.generate_questions.reset_mock()
        self.generate_plan.reset_mock()

    def test_import_statistics_and_two_metric_downloads(self):
        base = f"/experiment-imports/{self.import_id}"
        for metric, expected in [("error_reuse_rate", 0.09), ("latency_ms", 130)]:
            stats = fixtures.client.get(base + "/statistics", params={"metric": metric})
            self.assertEqual(stats.status_code, 200, stats.text)
            self.assertAlmostEqual(stats.json()["groups"][0]["mean"], expected)
            chart = fixtures.client.get(base + "/chart", params=dict(metric=metric, dataset="demo", scenario="normal"))
            self.assertEqual(chart.status_code, 200)
            self.assertEqual(chart.headers["content-type"], "image/png")
            self.assertIn(f"experiment-{self.import_id}.png", chart.headers["content-disposition"])
            with Image.open(BytesIO(chart.content)) as image:
                image.verify()
        self.generate_questions.assert_not_called()
        self.generate_plan.assert_not_called()

    def test_not_found_bad_metric_and_missing_query_parameters(self):
        for suffix in ["statistics", "chart"]:
            params = dict(metric="absent", dataset="demo", scenario="normal")
            self.assertEqual(fixtures.client.get(f"/experiment-imports/999/{suffix}", params=params).status_code, 404)
            url = f"/experiment-imports/{self.import_id}/{suffix}"
            self.assertEqual(fixtures.client.get(url, params=params).status_code, 409)
            self.assertEqual(fixtures.client.get(url).status_code, 422)
        response = fixtures.client.get(f"/experiment-imports/{self.import_id}/chart",
            params=dict(metric="error_reuse_rate", dataset="absent", scenario="normal"))
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
