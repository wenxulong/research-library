"""前三阶段持久化链路：隔离数据库，实际HTTP请求，仅替换模型调用。"""
import json
import unittest
from unittest.mock import patch

import tests.fixtures.main as fixtures
from research_library.db.model import ReportJob, ResearchQuestionSet
from research_library.schemas import CheckedComparisonReportResponse, ComparisonReportResponse, ComparisonSourceResponse, ResearchTaskReviewResponse, ReviewItemResponse


class ResearchPlanningFixture(fixtures.ComparisonDatabaseFixture):
    """研究问题与计划的共用隔离环境，不包含测试用例。"""
    def setUp(self):
        super().setUp()
        report = ComparisonReportResponse(
            task_id=self.task_id, question=self.analysis.question, paper_ids=self.paper_ids,
            method_comparisons=[], experiment_comparisons=[], conclusion_comparisons=[], evidence_notes=[],
            sources=[ComparisonSourceResponse(source_number=n, **source.model_dump())
                     for n, source in enumerate(self.analysis.sources, 1)],
        )
        checked = CheckedComparisonReportResponse(
            task_id=self.task_id, report=report, passed=True, correction_rounds=0,
            initial_review=ResearchTaskReviewResponse(task_id=self.task_id, items=[]),
            review=ResearchTaskReviewResponse(task_id=self.task_id, items=[]),
        )
        with fixtures.TestingSessionLocal() as db:
            db.add(ReportJob(id=17, task_id=self.task_id, status="succeeded", stage="finished",
                            analysis_snapshot=self.analysis.model_dump(mode="json"),
                            result=checked.model_dump(mode="json")))
            db.commit()
        self.candidates = [dict(
            observation="当前资料描述了语义缓存。", research_question="分布变化时方法表现如何？",
            research_value="明确适用范围。", source_numbers=[1],
            verification_plan="设置领域切换请求序列。", uncertainties=["需要补充检索"],
        ), dict(
            observation="当前资料描述了完全匹配。", research_question="如何权衡缓存调用预算？",
            research_value="明确成本权衡。", source_numbers=[3],
            verification_plan="设置不同预算。", uncertainties=[],
        )]
        self.generate_questions = self.enterContext(patch(
            'research_library.agents.research_question_agent.generate_text', return_value=json.dumps({"candidates": self.candidates}),
        ))
        self.enterContext(patch('research_library.agents.research_question_agent.review_agent.review_claim', side_effect=lambda claim:
            ReviewItemResponse(field=claim.field, claim=claim.claim, verdict="supported",
                               reason="测试依据支持观察。", source_numbers=claim.source_numbers)))
        self.conditions = dict(equipment="Mac，无独立GPU", data_resources="已有论文，数据待准备",
                               budget="100元", time_budget="四周", existing_implementation="已有RAG与缓存")
        self.content = dict(
            objective="评估领域变化时的缓存行为。", hypothesis="知识检查可能降低误复用率，待验证。",
            method_steps=["复用现有缓存接口"], baselines=["固定阈值语义缓存"],
            data_plan="准备请求序列，划分调参与测试数据。",
            metrics=["错误复用率=错误复用次数/全部复用次数，越低越好。"],
            main_experiments=["固定预算比较稳定分布与领域切换，重复运行。"],
            ablation_experiments=["移除实体检查"], execution_steps=["准备数据", "运行实验"],
            result_columns=["method", "dataset", "scenario", "seed", "run_id", "error_reuse_rate"],
            risks=["标注成本尚未确认"],
        )
        self.generate_plan = self.enterContext(patch(
            'research_library.agents.research_plan_service.generate_text', return_value=json.dumps(self.content),
        ))

    def create_set(self):
        response = fixtures.client.post("/report-jobs/17/research-questions")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def select(self, set_id, **selection):
        response = fixtures.client.patch(f"/research-question-sets/{set_id}/selection", json=selection)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def plan(self, set_id, **updates):
        response = fixtures.client.post(f"/research-question-sets/{set_id}/plan", json={**self.conditions, **updates})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class TestResearchPlanning(ResearchPlanningFixture, unittest.TestCase):
    def test_generated_candidates_are_saved_and_can_be_read_without_model_call(self):
        data = self.create_set()
        self.assertIsInstance(data["id"], int)
        self.assertIsNotNone(data["created_at"])
        self.assertIsNone(data["selected_question"])
        response = fixtures.client.get(f"/research-question-sets/{data['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), data)
        self.generate_questions.assert_called_once()

    def test_selecting_or_editing_question_preserves_original_candidates(self):
        data = self.create_set()
        selected = self.select(data["id"], candidate_number=2)
        self.assertEqual(selected["selected_question"], self.candidates[1]["research_question"])
        edited = self.select(data["id"], candidate_number=2, research_question="  用户缩小后的问题  ")
        self.assertEqual(edited["selected_question"], "用户缩小后的问题")
        self.assertEqual(edited["candidates"], data["candidates"])
        self.assertEqual(fixtures.client.get(f"/research-question-sets/{data['id']}").json(), edited)

    def test_invalid_selection_does_not_change_saved_record(self):
        data = self.create_set()
        for payload, status in (({"candidate_number": 0}, 422), ({"candidate_number": 3}, 409),
                                ({"candidate_number": 1, "research_question": "   "}, 409)):
            with self.subTest(payload=payload):
                response = fixtures.client.patch(f"/research-question-sets/{data['id']}/selection", json=payload)
                self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(fixtures.client.get(f"/research-question-sets/{data['id']}").json(), data)

    def test_plan_requires_selection_and_does_not_call_model(self):
        data = self.create_set()
        response = fixtures.client.post(f"/research-question-sets/{data['id']}/plan", json=self.conditions)
        self.assertEqual(response.status_code, 409)
        self.generate_plan.assert_not_called()

    def test_generate_read_plan_roundtrip_preserves_question_conditions_and_basis(self):
        data = self.create_set()
        self.select(data["id"], candidate_number=2, research_question="用户确认的研究问题")
        plan = self.plan(data["id"])
        self.assertEqual(plan["research_question"], "用户确认的研究问题")
        self.assertEqual(plan["conditions"], self.conditions)
        self.assertEqual(plan["content"], self.content)
        self.assertEqual(plan["basis"]["research_question"], self.candidates[1]["research_question"])
        self.assertEqual(plan["sources"], data["sources"])
        self.assertEqual(plan["question_set_id"], data["id"])
        self.assertIn("Mac，无独立GPU", self.generate_plan.call_args.kwargs["user_prompt"])
        response = fixtures.client.get(f"/research-question-sets/{data['id']}/plan")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), plan)
        self.generate_plan.assert_called_once()
        with fixtures.TestingSessionLocal() as db:
            self.assertEqual(db.get(ResearchQuestionSet, data["id"]).plan, plan)

    def test_absent_plan_returns_404(self):
        data = self.create_set()
        self.assertEqual(fixtures.client.get(f"/research-question-sets/{data['id']}/plan").status_code, 404)

    def test_changing_selection_or_wording_clears_plan(self):
        for selection in ({"candidate_number": 2}, {"candidate_number": 1, "research_question": "新的问题"}):
            with self.subTest(selection=selection):
                data = self.create_set()
                self.select(data["id"], candidate_number=1)
                self.plan(data["id"])
                self.select(data["id"], **selection)
                self.assertEqual(fixtures.client.get(f"/research-question-sets/{data['id']}/plan").status_code, 404)

    def test_saving_identical_selection_keeps_plan(self):
        data = self.create_set()
        self.select(data["id"], candidate_number=1)
        plan = self.plan(data["id"])
        self.select(data["id"], candidate_number=1)
        self.assertEqual(fixtures.client.get(f"/research-question-sets/{data['id']}/plan").json(), plan)

    def test_regeneration_keeps_latest_plan_and_conditions(self):
        data = self.create_set()
        self.select(data["id"], candidate_number=1)
        self.plan(data["id"])
        self.generate_plan.return_value = json.dumps({**self.content, "objective": "更新后的目标"})
        latest = self.plan(data["id"], budget="50元")
        self.assertEqual(latest["conditions"]["budget"], "50元")
        self.assertEqual(latest["content"]["objective"], "更新后的目标")
        self.assertEqual(fixtures.client.get(f"/research-question-sets/{data['id']}/plan").json(), latest)

    def test_invalid_output_returns_502_and_keeps_previous_plan(self):
        data = self.create_set()
        self.select(data["id"], candidate_number=1)
        previous = self.plan(data["id"])
        self.generate_plan.reset_mock()
        self.generate_plan.return_value = "不是JSON"
        response = fixtures.client.post(f"/research-question-sets/{data['id']}/plan", json=self.conditions)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.generate_plan.call_count, 2)
        self.assertEqual(fixtures.client.get(f"/research-question-sets/{data['id']}/plan").json(), previous)

    def test_missing_records_return_404_without_model_calls(self):
        responses = [fixtures.client.get("/research-question-sets/999"),
                     fixtures.client.patch("/research-question-sets/999/selection", json={"candidate_number": 1}),
                     fixtures.client.post("/research-question-sets/999/plan", json=self.conditions),
                     fixtures.client.get("/research-question-sets/999/plan")]
        self.assertTrue(all(response.status_code == 404 for response in responses))
        self.generate_questions.assert_not_called()
        self.generate_plan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
