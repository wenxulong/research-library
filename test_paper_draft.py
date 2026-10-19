"""论文初稿、引用映射及ZIP下载的隔离回归测试。"""
from io import BytesIO
import json
import re
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

import test_main as fixtures
from model import ExperimentImport, ResearchQuestionSet
from paper_draft_agent import SECTION_FIELDS, generate_paper_draft, parse_paper_draft
from paper_draft_export import build_paper_draft_markdown, PAPER_SECTIONS
from paper_draft_service import build_paper_draft_materials
from schemas import PaperDraftRequest
from test_research_planning import ResearchPlanningFixture


class DraftFixture(ResearchPlanningFixture):
    def setUp(self):
        super().setUp()
        set_id = self.create_set()["id"]
        self.select(set_id, candidate_number=1)
        plan = self.plan(set_id)
        mapping = {source["source_number"]: 10 + n * 3 for n, source in enumerate(plan["sources"])}
        for source in plan["sources"]:
            source["source_number"] = mapping[source["source_number"]]
        plan["basis"]["source_numbers"] = [mapping[n] for n in plan["basis"]["source_numbers"]]
        if plan["basis"]["evidence_review"]:
            plan["basis"]["evidence_review"]["source_numbers"] = plan["basis"]["source_numbers"]
        with fixtures.TestingSessionLocal() as db:
            db.get(ResearchQuestionSet, set_id).plan = plan
            db.commit()
        csv = ("method,dataset,scenario,seed,run_id,error_reuse_rate,latency_ms\n"
               "baseline,demo,normal,1,1,0.08,120\n"
               "baseline,demo,normal,2,2,0.10,140\n"
               "ours,demo,normal,1,3,0.04,160\n"
               "baseline,demo,shift,1,4,0.99,999\n")
        upload = fixtures.client.post(f"/research-question-sets/{set_id}/experiment-imports",
            files={"file": ("results.csv", csv.encode(), "text/csv")})
        self.assertEqual(upload.status_code, 201, upload.text)
        self.import_id = upload.json()["id"]
        self.request = dict(metrics=["error_reuse_rate", "latency_ms"], dataset="demo", scenario="normal",
                            implementation_details="  已实现固定阈值缓存  ", actual_experiment_setup=" Mac，重复运行 ")
        self.analysis_content = dict(summary="测试总结", differences=["存在差异"], tradeoffs=["需考虑延迟"],
                                    limitations=["样本少"], next_experiments=["重复运行"])
        self.analysis_model = self.enterContext(patch("experiment_analysis_agent.generate_text",
                                                     return_value=json.dumps(self.analysis_content)))
        self.payload = dict(title="测试论文", abstract="测试摘要", **{
            field: [dict(text=f"{field}正文", source_numbers=[])] for field in SECTION_FIELDS})
        self.payload["introduction"][0]["source_numbers"] = [10, 13, 16]
        self.payload["related_work"][0]["source_numbers"] = [10]
        self.writer = self.enterContext(patch("paper_draft_agent.generate_text", return_value=json.dumps(self.payload)))
        self.base = f"/experiment-imports/{self.import_id}/paper-draft"

    def materials(self, **updates):
        with fixtures.TestingSessionLocal() as db:
            return build_paper_draft_materials(db, db.get(ExperimentImport, self.import_id),
                                               PaperDraftRequest(**{**self.request, **updates}))


class TestPaperDraftMaterials(DraftFixture, unittest.TestCase):
    def test_real_evidence_and_selected_statistics_are_assembled(self):
        materials = self.materials()
        self.assertTrue(all(source.text for source in materials.evidence))
        self.assertEqual([source.source_number for source in materials.evidence], [10, 13, 16, 19, 22])
        self.assertEqual(materials.analysis.statistics[0].groups[0].mean, 0.09)
        self.assertTrue(all(group.scenario == "normal" for stats in materials.analysis.statistics for group in stats.groups))
        self.assertEqual(materials.implementation_details, "已实现固定阈值缓存")
        self.assertEqual(materials.missing_information, [])

    def test_blank_actual_details_are_preserved_as_missing(self):
        materials = self.materials(implementation_details=" ", actual_experiment_setup="")
        self.assertEqual(len(materials.missing_information), 2)

    def test_missing_chunk_is_rejected_before_any_model_call(self):
        with fixtures.TestingSessionLocal() as db:
            imported = db.get(ExperimentImport, self.import_id)
            snapshot = json.loads(json.dumps(imported.plan_snapshot))
            snapshot["sources"][0]["chunk_id"] = 99999
            imported.plan_snapshot = snapshot
            with self.assertRaisesRegex(ValueError, "原文或PDF已不存在"):
                build_paper_draft_materials(db, imported, PaperDraftRequest(**self.request))
        self.analysis_model.assert_not_called()

    def test_mismatched_evidence_is_rejected(self):
        with fixtures.TestingSessionLocal() as db:
            imported = db.get(ExperimentImport, self.import_id)
            snapshot = json.loads(json.dumps(imported.plan_snapshot))
            snapshot["sources"][0]["page_number"] += 1
            imported.plan_snapshot = snapshot
            with self.assertRaisesRegex(ValueError, "不对应"):
                build_paper_draft_materials(db, imported, PaperDraftRequest(**self.request))
        self.analysis_model.assert_not_called()


class TestPaperDraftParsing(DraftFixture, unittest.TestCase):
    def test_code_blocks_and_used_evidence_filter(self):
        materials = self.materials()
        draft = parse_paper_draft("```json\n" + json.dumps(self.payload) + "\n```", materials)
        self.assertEqual([source.source_number for source in draft.evidence], [10, 13, 16])
        self.assertEqual(draft.import_id, self.import_id)

    def test_bad_json_or_missing_sections_are_rejected(self):
        materials = self.materials()
        for text in ["not json", "{}", json.dumps({**self.payload, "title": 12})]:
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                parse_paper_draft(text, materials)

    def test_unknown_source_is_rejected_in_each_section(self):
        materials = self.materials()
        for field in SECTION_FIELDS:
            payload = json.loads(json.dumps(self.payload))
            payload[field][0]["source_numbers"] = [999]
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "999"):
                parse_paper_draft(json.dumps(payload), materials)

    def test_bad_reference_retries_and_preserves_missing_information(self):
        materials = self.materials(implementation_details="", actual_experiment_setup="")
        bad = json.loads(json.dumps(self.payload))
        bad["introduction"][0]["source_numbers"] = [999]
        self.writer.side_effect = [json.dumps(bad), json.dumps(self.payload)]
        draft = generate_paper_draft(materials)
        self.assertEqual(self.writer.call_count, 2)
        self.assertEqual(draft.missing_information, materials.missing_information)
        self.assertIn(materials.evidence[0].text, self.writer.call_args.kwargs["user_prompt"])


class TestPaperDraftAPI(DraftFixture, unittest.TestCase):
    def test_generate_api_returns_all_sections_without_mutating_import(self):
        before = fixtures.client.get(f"/experiment-imports/{self.import_id}").json()
        response = fixtures.client.post(self.base, json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["content"], self.payload)
        self.analysis_model.assert_called_once()
        self.writer.assert_called_once()
        self.assertEqual(fixtures.client.get(f"/experiment-imports/{self.import_id}").json(), before)

    def test_export_zip_sections_references_images_and_materials_match(self):
        response = fixtures.client.post(self.base + "/export", json=self.request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn(f"paper-draft-{self.import_id}.zip", response.headers["content-disposition"])
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"paper.md", "draft.json", "materials.json",
                "figures/metric-1.png", "figures/metric-2.png"})
            markdown = archive.read("paper.md").decode()
            headings = ["## 摘要"] + [f"## {title}" for _, title in PAPER_SECTIONS] + ["## 参考文献"]
            offsets = [markdown.index(heading) for heading in headings]
            self.assertEqual(offsets, sorted(offsets))
            self.assertIn("introduction正文[1](#ref-1)[2](#ref-2)", markdown)
            self.assertEqual(markdown.count('<a id="ref-1"></a>'), 1)
            self.assertEqual(markdown.count('<a id="ref-2"></a>'), 1)
            self.assertNotIn('<a id="ref-3"></a>', markdown)
            self.assertEqual([source["source_number"] for source in json.loads(archive.read("draft.json"))["evidence"]], [10, 13, 16])
            stats = json.loads(archive.read("materials.json"))["analysis"]["statistics"]
            self.assertEqual([item["metric"] for item in stats], self.request["metrics"])
            for filename in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown):
                with Image.open(BytesIO(archive.read(filename))) as image:
                    self.assertEqual(image.size, (1200, 750))
                    image.verify()
        self.analysis_model.assert_called_once()
        self.writer.assert_called_once()

    def test_invalid_record_metric_and_request_do_not_call_models(self):
        for suffix in ["", "/export"]:
            self.assertEqual(fixtures.client.post("/experiment-imports/999/paper-draft" + suffix,
                json=self.request).status_code, 404)
            self.assertEqual(fixtures.client.post(self.base + suffix,
                json={**self.request, "metrics": ["absent"]}).status_code, 409)
            self.assertEqual(fixtures.client.post(self.base + suffix,
                json={**self.request, "metrics": []}).status_code, 422)
        self.analysis_model.assert_not_called()
        self.writer.assert_not_called()

    def test_invalid_writer_output_returns_502_after_two_attempts(self):
        self.writer.return_value = "{}"
        for suffix in ["", "/export"]:
            self.writer.reset_mock()
            response = fixtures.client.post(self.base + suffix, json=self.request)
            self.assertEqual(response.status_code, 502, response.text)
            self.assertEqual(self.writer.call_count, 2)

    def test_uncited_draft_and_missing_information_export_explicit_notes(self):
        materials = self.materials(implementation_details="", actual_experiment_setup="")
        for field in SECTION_FIELDS:
            self.payload[field][0]["source_numbers"] = []
        draft = parse_paper_draft(json.dumps(self.payload), materials)
        with fixtures.TestingSessionLocal() as db:
            markdown = build_paper_draft_markdown(db, draft, materials)
        self.assertIn("没有登记文献引用", markdown)
        self.assertIn("## 待补充信息", markdown)


if __name__ == "__main__":
    unittest.main()
