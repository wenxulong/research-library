"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestResearchTaskAnalysisAPI(unittest.TestCase):
    """验证论文分析接口的成功响应和错误处理。"""

    def setUp(self):
        # 每个测试开始前，删除测试数据库中的全部表。
        Base.metadata.drop_all(bind=test_engine)

        # 根据 model.py 中的模型重新创建表，保证测试相互独立。
        Base.metadata.create_all(bind=test_engine)

    def _create_task(self):
        """创建一个测试研究任务，并返回它的编号。"""

        # 使用测试客户端调用创建研究任务的真实接口。
        response = client.post(
            "/research-tasks",
            json={
                "title": "语义缓存论文分析",
                "question": "语义缓存如何降低大模型服务成本？",
            },
        )

        # 确认任务创建成功；201 表示新资源创建成功。
        self.assertEqual(response.status_code, 201)

        # 将响应转换成字典，再读取任务编号并返回。
        return response.json()["id"]

    def test_analysis_returns_structured_result(self):
        # 创建测试任务，取得数据库生成的编号。
        task_id = self._create_task()

        # 准备分析 Agent 应当返回的模拟结果。
        fake_result = paper_analysis_agent.PaperAnalysisResult(
            question="语义缓存如何降低大模型服务成本？",
            summary="论文研究语义缓存[资料1]。",
            methods=["通过复用已有响应降低调用成本[资料1]。"],
            experiments=["当前资料未提供完整实验信息。"],
            conclusions=["语义缓存能够减少重复调用[资料1]。"],
            limitations=["当前资料不足以分析完整局限性。"],
            sources=[
                paper_analysis_agent.AnalysisSource(
                    paper_id=3,
                    paper_title="Semantic Caching",
                    chunk_id=14,
                    page_number=1,
                    score=0.53,
                ),
            ],
        )

        # 暂时替换接口中调用的分析函数，避免真实调用模型。
        with patch(
            'research_library.api.tasks.paper_analysis_agent.analyze_research_task',
            return_value=fake_result,
        ) as mock_analyze:
            # 调用真实的分析接口；它使用任务中保存的问题。
            response = client.post(
                f"/research-tasks/{task_id}/analysis"
            )

        # 确认接口正常返回。
        self.assertEqual(response.status_code, 200)

        # 检查整个 JSON，确认所有字段和资料来源都正确转换。
        self.assertEqual(
            response.json(),
            {
                "task_id": task_id,
                "question": "语义缓存如何降低大模型服务成本？",
                "summary": "论文研究语义缓存[资料1]。",
                "methods": [
                    "通过复用已有响应降低调用成本[资料1]。",
                ],
                "experiments": [
                    "当前资料未提供完整实验信息。",
                ],
                "conclusions": [
                    "语义缓存能够减少重复调用[资料1]。",
                ],
                "limitations": [
                    "当前资料不足以分析完整局限性。",
                ],
                "evidence_notes": [],
                "entities": [],
                "sources": [
                    {
                        "paper_id": 3,
                        "paper_title": "Semantic Caching",
                        "chunk_id": 14,
                        "page_number": 1,
                        "score": 0.53,
                    },
                ],
            },
        )

        # 确认接口只调用了一次分析 Agent。
        mock_analyze.assert_called_once()

        # 读取接口传给分析 Agent 的关键字参数。
        arguments = mock_analyze.call_args.kwargs

        # 确认传入的是当前任务编号。
        self.assertEqual(arguments["task_id"], task_id)

        # 确认分析问题来自数据库中的任务记录。
        self.assertEqual(
            arguments["question"],
            "语义缓存如何降低大模型服务成本？",
        )

        # 使用新会话读取，确认 POST 已经真正提交结果。
        with TestingSessionLocal() as db:
            saved_task = db.get(ResearchTask, task_id)
            self.assertIsNotNone(saved_task)
            self.assertEqual(saved_task.status, "completed")
            saved_analysis = json.loads(saved_task.result)
            self.assertEqual(saved_analysis, response.json())

        # 读取已有分析时不应再次调用 Agent。
        with patch(
            'research_library.api.tasks.paper_analysis_agent.analyze_research_task'
        ) as mock_read_analyze:
            read_response = client.get(
                f"/research-tasks/{task_id}/analysis",
                follow_redirects=False,
            )

        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(read_response.json(), response.json())
        mock_read_analyze.assert_not_called()

    def test_read_analysis_returns_404_when_not_saved(self):
        task_id = self._create_task()

        with patch(
            'research_library.api.tasks.paper_analysis_agent.analyze_research_task'
        ) as mock_analyze:
            response = client.get(
                f"/research-tasks/{task_id}/analysis",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"],
            "这个研究任务还没有保存分析结果",
        )
        mock_analyze.assert_not_called()

    def test_read_analysis_rejects_plain_text_result(self):
        task_id = self._create_task()

        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, task_id)
            task.result = "这是以前保存的普通任务结果"
            db.commit()

        response = client.get(
            f"/research-tasks/{task_id}/analysis",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            "已保存内容不是结构化分析结果，请重新执行分析",
        )

    def test_analysis_returns_404_for_missing_task(self):
        # 设置模拟函数，用于检查任务不存在时是否调用了 Agent。
        with patch(
            'research_library.api.tasks.paper_analysis_agent.analyze_research_task'
        ) as mock_analyze:
            # 此测试没有创建任务，因此任务 999 不存在。
            response = client.post(
                "/research-tasks/999/analysis"
            )

        # 任务不存在，应返回 404。
        self.assertEqual(response.status_code, 404)

        # 检查错误说明。
        self.assertEqual(
            response.json()["detail"],
            "没有找到编号为 999 的研究任务",
        )

        # 不存在的任务不应该触发任何分析或模型调用。
        mock_analyze.assert_not_called()

    def test_analysis_returns_409_when_no_sources(self):
        # 创建存在的任务，确保代码能够进入分析阶段。
        task_id = self._create_task()

        # 模拟分析函数发现没有可用资料，主动抛出 ValueError。
        with patch(
            'research_library.api.tasks.paper_analysis_agent.analyze_research_task',
            side_effect=ValueError(
                "没有找到可用于论文分析的资料"
            ),
        ):
            response = client.post(
                f"/research-tasks/{task_id}/analysis"
            )

        # 验证 API 将 ValueError 转换成了 HTTP 409。
        self.assertEqual(response.status_code, 409)

        # 验证原始错误信息被正确返回。
        self.assertEqual(
            response.json()["detail"],
            "没有找到可用于论文分析的资料",
        )

    def test_analysis_returns_503_when_ollama_unavailable(self):
        # 创建一个测试任务。
        task_id = self._create_task()

        # 模拟调用 Ollama 时发生网络连接错误。
        with patch(
            'research_library.api.tasks.paper_analysis_agent.analyze_research_task',
            side_effect=URLError("连接被拒绝"),
        ):
            response = client.post(
                f"/research-tasks/{task_id}/analysis"
            )

        # 验证网络异常被转换为 HTTP 503。
        self.assertEqual(response.status_code, 503)

        # 验证接口返回的是你在 api.py 中定义的说明。
        self.assertEqual(
            response.json()["detail"],
            "无法连接向量模型，请确认Ollama已经启动",
        )

    def test_analysis_returns_502_when_model_result_invalid(self):
        # 创建一个测试任务。
        task_id = self._create_task()

        # 模拟 DeepSeek 返回内容无法解析为正确 JSON。
        with patch(
            'research_library.api.tasks.paper_analysis_agent.analyze_research_task',
            side_effect=RuntimeError(
                "论文分析Agent没有返回正确的JSON"
            ),
        ):
            response = client.post(
                f"/research-tasks/{task_id}/analysis"
            )

        # 验证运行错误被转换为 HTTP 502。
        self.assertEqual(response.status_code, 502)

        # 验证具体错误信息被保留。
        self.assertEqual(
            response.json()["detail"],
            "论文分析Agent没有返回正确的JSON",
        )
