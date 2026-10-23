"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestPaperAnalysisAgent(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def _citation_data(self, text):
        return {
            "summary": text,
            "methods": [],
            "experiments": [],
            "conclusions": [],
            "limitations": ["当前资料不足以分析局限性。"],
        }

    def test_citations_accept_valid_numbers_and_insufficient_sources(self):
        data = self._citation_data("语义缓存[资料1][资料 2]。")
        paper_analysis_agent.validate_analysis_citations(data, 2)

    def test_citations_reject_out_of_range_numbers_in_every_field(self):
        for field_name in (
            "summary", "methods", "experiments", "conclusions", "limitations"
        ):
            for marker in ("[资料0]", "[资料3]"):
                with self.subTest(field=field_name, marker=marker):
                    data = self._citation_data("概述[资料1]。")
                    data[field_name] = marker if field_name == "summary" else [marker]
                    with self.assertRaisesRegex(RuntimeError, "引用不存在"):
                        paper_analysis_agent.validate_analysis_citations(data, 2)

    def test_citations_reject_malformed_numbers(self):
        for marker in ("[资料-1]", "[资料一]", "[资料1,2]", "[资料]"):
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(RuntimeError, "引用格式错误"):
                    paper_analysis_agent.validate_analysis_citations(
                        self._citation_data(marker), 2
                    )

    def test_citations_reject_unclosed_markers_in_every_field(self):
        for field in ("summary", "methods", "experiments", "conclusions", "limitations"):
            for text in ("结论[资料1", "结论[资料", "[资料1][资料2"):
                with self.subTest(field=field, text=text):
                    data = self._citation_data("概述[资料1]")
                    data[field] = text if field == "summary" else [text]
                    with self.assertRaisesRegex(RuntimeError, "引用格式错误"):
                        paper_analysis_agent.validate_analysis_citations(data, 2)

    def test_analyze_rejects_nonexistent_citation_before_return(self):
        chunk = SimpleNamespace(id=10, page_number=1, text="论文正文")
        paper = SimpleNamespace(id=1, title="测试论文")
        response_text = json.dumps(self._citation_data("错误引用[资料2]"))
        with (
            patch(
                'research_library.agents.paper_analysis_agent.collect_analysis_matches',
                return_value=[(chunk, paper, 0.9)],
            ),
            patch('research_library.agents.paper_analysis_agent.generate_text', return_value=response_text),
        ):
            with TestingSessionLocal() as db:
                with self.assertRaisesRegex(RuntimeError, "引用不存在"):
                    paper_analysis_agent.analyze_research_task(db, 1, "研究问题")

    def test_parse_analysis_response_removes_code_block(self):
        response_text = """```json
{
  "summary": "论文研究语义缓存。",
  "methods": ["提出在线学习算法[资料1]"],
  "experiments": ["使用真实查询数据[资料2]"],
  "conclusions": ["方法可以降低服务成本[资料3]"],
  "limitations": ["资料没有说明完整局限性"]
}
```"""

        result = (
            paper_analysis_agent
            .parse_analysis_response(
                response_text
            )
        )

        self.assertEqual(
            result["summary"],
            "论文研究语义缓存。",
        )
        self.assertEqual(
            result["methods"],
            ["提出在线学习算法[资料1]"],
        )
        self.assertEqual(
            result["experiments"],
            ["使用真实查询数据[资料2]"],
        )
        self.assertEqual(
            result["conclusions"],
            ["方法可以降低服务成本[资料3]"],
        )
        self.assertEqual(
            result["limitations"],
            ["资料没有说明完整局限性"],
        )

    def test_parse_analysis_response_rejects_invalid_json(self):
        with self.assertRaises(RuntimeError):
            (
                paper_analysis_agent
                .parse_analysis_response(
                    "这不是正确的JSON"
                )
            )

    def test_collect_analysis_matches_removes_duplicates(self):
        paper = SimpleNamespace(
            id=1,
            title="语义缓存论文",
        )
        first_chunk = SimpleNamespace(
            id=10,
            page_number=1,
            text="论文核心方法",
        )
        second_chunk = SimpleNamespace(
            id=20,
            page_number=3,
            text="论文实验结果",
        )

        search_results = [
            [(first_chunk, paper, 0.70)],
            [
                (first_chunk, paper, 0.85),
                (second_chunk, paper, 0.60),
            ],
            [(first_chunk, paper, 0.75)],
            [(second_chunk, paper, 0.65)],
            [(first_chunk, paper, 0.80)],
        ]

        with (
            patch(
                'research_library.agents.paper_analysis_agent.task_crud.get_task_paper_links',
                return_value=[SimpleNamespace(paper_id=1)],
            ),
            patch(
                'research_library.agents.paper_analysis_agent.'
                'query_rewriter.'
                "rewrite_query_for_search",
                return_value="semantic caching method",
            ) as mock_rewrite,
            patch(
                'research_library.agents.paper_analysis_agent.'
                'semantic_search.'
                "search_task_chunks_semantic",
                side_effect=search_results,
            ) as mock_search,
        ):
            with TestingSessionLocal() as db:
                matches = (
                    paper_analysis_agent
                    .collect_analysis_matches(
                        db=db,
                        task_id=1,
                        question="论文使用了什么方法？",
                    )
                )

        self.assertEqual(len(matches), 2)
        self.assertIs(matches[0][0], first_chunk)
        self.assertEqual(matches[0][2], 0.85)
        self.assertIs(matches[1][0], second_chunk)

        mock_rewrite.assert_called_once_with(
            "论文使用了什么方法？"
        )
        self.assertEqual(mock_search.call_count, 5)

    def test_analyze_research_task_returns_structure(self):
        paper = SimpleNamespace(
            id=1,
            title="语义缓存论文",
        )
        chunk = SimpleNamespace(
            id=10,
            page_number=3,
            text=(
                "The system uses semantic caching "
                "to reduce LLM serving cost."
            ),
        )
        matches = [
            (chunk, paper, 0.91),
        ]

        model_response = """{
  "summary": "论文研究语义缓存[资料1]。",
  "methods": ["使用语义缓存降低服务成本[资料1]。"],
  "experiments": ["当前资料没有提供完整实验信息。"],
  "conclusions": ["语义缓存可以减少LLM调用[资料1]。"],
  "limitations": ["当前资料不足以分析完整局限性。"]
}"""

        with (
            patch(
                'research_library.agents.paper_analysis_agent.'
                "collect_analysis_matches",
                return_value=matches,
            ) as mock_collect,
            patch(
                'research_library.agents.paper_analysis_agent.generate_text',
                return_value=model_response,
            ) as mock_generate,
        ):
            with TestingSessionLocal() as db:
                result = (
                    paper_analysis_agent
                    .analyze_research_task(
                        db=db,
                        task_id=1,
                        question=(
                            "语义缓存如何降低"
                            "大模型服务成本？"
                        ),
                    )
                )

        self.assertEqual(
            result.question,
            "语义缓存如何降低大模型服务成本？",
        )
        self.assertEqual(
            result.summary,
            "论文研究语义缓存[资料1]。",
        )
        self.assertEqual(
            result.methods,
            ["使用语义缓存降低服务成本[资料1]。"],
        )
        self.assertEqual(
            result.experiments,
            ["当前资料没有提供完整实验信息。"],
        )
        self.assertEqual(
            result.conclusions,
            ["语义缓存可以减少LLM调用[资料1]。"],
        )
        self.assertEqual(
            result.limitations,
            ["当前资料不足以分析完整局限性。"],
        )
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0].paper_id, 1)
        self.assertEqual(result.sources[0].chunk_id, 10)
        self.assertEqual(result.sources[0].page_number, 3)
        self.assertEqual(result.sources[0].score, 0.91)

        mock_collect.assert_called_once()
        collect_arguments = mock_collect.call_args.kwargs
        self.assertEqual(collect_arguments["task_id"], 1)

        mock_generate.assert_called_once()
        generate_arguments = mock_generate.call_args.kwargs
        self.assertIn(
            "论文分析Agent",
            generate_arguments["system_prompt"],
        )
        self.assertIn(
            "[资料1]",
            generate_arguments["user_prompt"],
        )
        self.assertIn(
            "semantic caching",
            generate_arguments["user_prompt"],
        )
