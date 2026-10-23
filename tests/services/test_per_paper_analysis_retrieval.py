"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestPerPaperAnalysisRetrieval(unittest.TestCase):
    """验证逐篇检索名额、任务范围、去重和缺失向量的处理。"""

    def _candidates(self):
        # 论文 8 的全部候选得分更高，用来暴露先取 top_k 再过滤的错误。
        result = []
        for index in range(25):
            result.append((
                SimpleNamespace(id=100 + index, page_number=index + 1, text="方法实验结论"),
                SimpleNamespace(id=8, title="论文8"),
                SimpleNamespace(vector=[1.0, 0.0]),
            ))
        result.append((
            SimpleNamespace(id=3, page_number=1, text="方法实验结论"),
            SimpleNamespace(id=3, title="论文3"),
            SimpleNamespace(vector=[0.6, 0.8]),
        ))
        return result

    def test_filter_before_recall_keeps_lower_scoring_target_paper(self):
        with (
            patch('research_library.retrieval.semantic_search.task_crud.get_task_chunk_embeddings', return_value=self._candidates()) as load,
            patch('research_library.retrieval.semantic_search.embed_texts', return_value=[[1.0, 0.0]]),
        ):
            result = semantic_search.search_task_chunks_semantic(
                Mock(), task_id=6, question="方法", top_k=1, paper_id=3,
            )
        self.assertEqual([paper.id for _, paper, _ in result], [3])
        self.assertAlmostEqual(result[0][2], 0.6)
        self.assertEqual(load.call_args.args[1], 6)

    def test_omitting_paper_filter_keeps_global_search(self):
        with (
            patch('research_library.retrieval.semantic_search.task_crud.get_task_chunk_embeddings', return_value=self._candidates()),
            patch('research_library.retrieval.semantic_search.embed_texts', return_value=[[1.0, 0.0]]),
        ):
            result = semantic_search.search_task_chunks_semantic(
                Mock(), task_id=6, question="方法", top_k=1,
            )
        self.assertEqual(result[0][1].id, 8)

    def test_unlinked_paper_returns_empty_without_embedding_call(self):
        with (
            patch('research_library.retrieval.semantic_search.task_crud.get_task_chunk_embeddings', return_value=self._candidates()),
            patch('research_library.retrieval.semantic_search.embed_texts') as embed,
        ):
            result = semantic_search.search_task_chunks_semantic(
                Mock(), task_id=6, question="方法", paper_id=999,
            )
        self.assertEqual(result, [])
        embed.assert_not_called()

    def test_all_queries_run_for_each_paper_and_duplicates_keep_best_score(self):
        links = [SimpleNamespace(paper_id=3), SimpleNamespace(paper_id=8)]
        papers = {pid: SimpleNamespace(id=pid, title=f"论文{pid}") for pid in (3, 8)}
        chunks = {pid: SimpleNamespace(id=pid * 10, page_number=1, text="方法原文") for pid in (3, 8)}
        counts = {3: 0, 8: 0}
        def search(**kwargs):
            pid = kwargs["paper_id"]
            counts[pid] += 1
            return [(chunks[pid], papers[pid], 0.1 * counts[pid])]
        with (
            patch('research_library.agents.paper_analysis_agent.task_crud.get_task_paper_links', return_value=links),
            patch('research_library.agents.paper_analysis_agent.query_rewriter.rewrite_query_for_search', return_value="改写问题") as rewrite,
            patch('research_library.agents.paper_analysis_agent.semantic_search.search_task_chunks_semantic', side_effect=search) as retrieve,
        ):
            matches = paper_analysis_agent.collect_analysis_matches(Mock(), 6, "比较两篇论文", top_k_per_query=2)
        self.assertEqual(counts, {3: 5, 8: 5})
        self.assertEqual(len(matches), 2)
        self.assertEqual({p.id for _, p, _ in matches}, {3, 8})
        self.assertTrue(all(abs(score - 0.5) < 1e-9 for _, _, score in matches))
        rewrite.assert_called_once_with("比较两篇论文")
        expected_queries = ["改写问题", *paper_analysis_agent.ANALYSIS_QUERIES]
        for pid in (3, 8):
            calls = [call.kwargs for call in retrieve.call_args_list if call.kwargs["paper_id"] == pid]
            self.assertEqual([call["question"] for call in calls], expected_queries)
            self.assertTrue(all(call["task_id"] == 6 and call["top_k"] == 2 for call in calls))
        _, sources = paper_analysis_agent.build_analysis_context(matches)
        self.assertEqual({source.paper_id for source in sources}, {3, 8})

    def test_no_linked_papers_fails_before_model_and_search(self):
        with (
            patch('research_library.agents.paper_analysis_agent.task_crud.get_task_paper_links', return_value=[]),
            patch('research_library.agents.paper_analysis_agent.query_rewriter.rewrite_query_for_search') as rewrite,
            patch('research_library.agents.paper_analysis_agent.semantic_search.search_task_chunks_semantic') as retrieve,
        ):
            with self.assertRaisesRegex(ValueError, "还没有关联论文"):
                paper_analysis_agent.collect_analysis_matches(Mock(), 6, "研究问题")
        rewrite.assert_not_called()
        retrieve.assert_not_called()

    def test_missing_vectors_for_one_paper_fails_instead_of_skipping_it(self):
        with (
            patch('research_library.agents.paper_analysis_agent.task_crud.get_task_paper_links', return_value=[SimpleNamespace(paper_id=3), SimpleNamespace(paper_id=8)]),
            patch('research_library.agents.paper_analysis_agent.query_rewriter.rewrite_query_for_search', return_value="研究问题"),
            patch('research_library.agents.paper_analysis_agent.semantic_search.search_task_chunks_semantic', side_effect=lambda **kw: [] if kw["paper_id"] == 8 else [(SimpleNamespace(id=1), SimpleNamespace(id=3), 0.9)]),
        ):
            with self.assertRaisesRegex(ValueError, "论文 8.*可检索向量"):
                paper_analysis_agent.collect_analysis_matches(Mock(), 6, "研究问题")
