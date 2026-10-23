"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestRAGService(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def test_rag_service_builds_prompt_and_sources(self):
        paper = SimpleNamespace(
            id=1,
            title="RAG分块方法研究",
        )

        first_chunk = SimpleNamespace(
            id=10,
            page_number=3,
            text="固定长度分块按照预定字符数量切分文档。",
        )

        second_chunk = SimpleNamespace(
            id=11,
            page_number=5,
            text="递归分块会按照段落和句子层级进行切分。",
        )

        matches = [
            (first_chunk, paper, 0.91),
            (second_chunk, paper, 0.85),
        ]

        with (
            patch(
                'research_library.retrieval.rag_service.query_rewriter.'
                "rewrite_query_for_search",
                return_value="RAG chunking methods",
            ) as mock_rewrite,
            patch(
                'research_library.retrieval.rag_service.semantic_search.'
                "search_task_chunks_semantic",
                return_value=matches,
            ) as mock_search,
            patch(
                'research_library.retrieval.rag_service.generate_text',
                return_value=(
                    "常见方法包括固定长度分块[资料1]"
                    "和递归分块[资料2]。"
                ),
            ) as mock_generate,
        ):
            with TestingSessionLocal() as db:
                # 此测试只验证原 RAG 的提示词和引用；缓存入口由 test_exact_cache 覆盖。
                result = rag_service._generate_research_answer(
                    db=db,
                    task_id=1,
                    question="RAG有哪些分块方法？",
                    top_k=2,
                )

        self.assertEqual(
            result.question,
            "RAG有哪些分块方法？",
        )

        self.assertEqual(
            result.answer,
            "常见方法包括固定长度分块[资料1]"
            "和递归分块[资料2]。",
        )

        self.assertEqual(len(result.sources), 2)

        self.assertEqual(
            result.sources[0].paper_title,
            "RAG分块方法研究",
        )

        self.assertEqual(
            result.sources[0].page_number,
            3,
        )

        self.assertEqual(
            result.sources[0].score,
            0.91,
        )

        mock_rewrite.assert_called_once_with(
            "RAG有哪些分块方法？"
        )

        mock_search.assert_called_once()

        search_arguments = mock_search.call_args.kwargs

        self.assertEqual(
            search_arguments["task_id"],
            1,
        )

        self.assertEqual(
            search_arguments["question"],
            "RAG chunking methods",
        )

        self.assertEqual(
            search_arguments["top_k"],
            2,
        )

        mock_generate.assert_called_once()

        generate_arguments = mock_generate.call_args.kwargs
        system_prompt = generate_arguments["system_prompt"]
        user_prompt = generate_arguments["user_prompt"]

        self.assertIn(
            "不得编造",
            system_prompt,
        )

        self.assertIn(
            "[资料1]",
            user_prompt,
        )

        self.assertIn(
            "固定长度分块",
            user_prompt,
        )

        self.assertIn(
            "[资料2]",
            user_prompt,
        )

        self.assertIn(
            "递归分块",
            user_prompt,
        )

    def test_rag_answer_api_returns_answer_and_sources(self):
        task_response = client.post(
            "/research-tasks",
            json={
                "title": "RAG调研任务",
                "question": "RAG有哪些分块方法？",
            },
        )

        self.assertEqual(
            task_response.status_code,
            201,
        )

        task_id = task_response.json()["id"]

        fake_result = rag_service.RAGResult(
            question="RAG有哪些分块方法？",
            answer="常见方法包括固定长度分块[资料1]。",
            sources=[
                rag_service.RAGSource(
                    paper_id=3,
                    paper_title="RAG分块方法研究",
                    chunk_id=18,
                    page_number=5,
                    score=0.91,
                )
            ],
        )

        with patch(
            'research_library.api.tasks.rag_service.answer_research_question',
            return_value=fake_result,
        ) as mock_rag:
            response = client.post(
                f"/research-tasks/{task_id}/answer",
                json={
                    "question": "RAG有哪些分块方法？",
                    "top_k": 5,
                },
            )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.json(),
            {
                "question": "RAG有哪些分块方法？",
                "answer": "常见方法包括固定长度分块[资料1]。",
                "sources": [
                    {
                        "paper_id": 3,
                        "paper_title": "RAG分块方法研究",
                        "chunk_id": 18,
                        "page_number": 5,
                        "score": 0.91,
                    }
                ],
            },
        )

        mock_rag.assert_called_once()

        rag_arguments = mock_rag.call_args.kwargs

        self.assertEqual(
            rag_arguments["task_id"],
            task_id,
        )

        self.assertEqual(
            rag_arguments["question"],
            "RAG有哪些分块方法？",
        )

        self.assertEqual(
            rag_arguments["top_k"],
            5,
        )
