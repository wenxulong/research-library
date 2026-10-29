"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestSemanticSearch(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def test_more_similar_chunk_comes_first(self):
        paper = SimpleNamespace(id=1, title="测试论文")

        first_chunk = SimpleNamespace(
            id=1, page_number=1,
            text="不相关的内容",
        )
        second_chunk = SimpleNamespace(
            id=2, page_number=2,
            text="与问题相关的内容",
        )

        first_embedding = SimpleNamespace(
            vector=[0.0, 1.0],
        )
        second_embedding = SimpleNamespace(
            vector=[1.0, 0.0],
        )

        candidates = [
            (first_chunk, paper, first_embedding),
            (second_chunk, paper, second_embedding),
        ]

        with (
            patch(
                'research_library.retrieval.semantic_search.task_crud.get_task_chunk_embeddings',
                return_value=candidates,
            ),
            patch(
                'research_library.retrieval.semantic_search.embed_texts',
                return_value=[[1.0, 0.0]],
            ),
        ):
            with TestingSessionLocal() as db:
                results = semantic_search.search_task_chunks_semantic(
                    db,
                    task_id=1,
                    question="研究问题",
                    top_k=1,
                )

        self.assertEqual(len(results), 1)
        self.assertIs(results[0][0], second_chunk)
        self.assertEqual(results[0][2], 1.0)

    def test_api_returns_paper_page_and_score(self):
        task_response = client.post(
            "/research-tasks",
            json={
                "title": "测试任务",
                "question": "研究问题",
            },
        )
        self.assertEqual(task_response.status_code, 201)
        task_id = task_response.json()["id"]

        chunk = SimpleNamespace(
            id=8,
            page_number=2,
            chunk_index=1,
            text="与问题相关的正文",
        )
        paper = SimpleNamespace(id=5, title="测试论文")

        with patch(
            'research_library.api.tasks.semantic_search.search_task_chunks_semantic',
            return_value=[(chunk, paper, 0.91)],
        ):
            response = client.get(
                f"/research-tasks/{task_id}/chunks/semantic-search",
                params={"q": "研究问题", "top_k": 1},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "paper_id": 5,
                    "paper_title": "测试论文",
                    "chunk_id": 8,
                    "page_number": 2,
                    "chunk_index": 1,
                    "text": "与问题相关的正文",
                    "score": 0.91,
                }
            ],
        )

    def test_reference_page_is_lowered_during_reranking(self):
        paper = SimpleNamespace(
            id=3,
            title="语义缓存论文",
        )

        reference_heading = SimpleNamespace(
            id=124,
            page_number=10,
            text="REFERENCES [1] First paper",
        )

        reference_chunk = SimpleNamespace(
            id=127,
            page_number=10,
            text=(
                "Online cost-aware framework "
                "to reduce LLM calls"
            ),
        )

        method_chunk = SimpleNamespace(
            id=14,
            page_number=1,
            text=(
                "Our semantic caching method uses "
                "online adaptation to reduce cost"
            ),
        )

        heading_embedding = SimpleNamespace(
            vector=[0.10, 0.994987],
        )

        reference_embedding = SimpleNamespace(
            vector=[0.90, 0.435889],
        )

        method_embedding = SimpleNamespace(
            vector=[0.85, 0.526783],
        )

        candidates = [
            (
                reference_heading,
                paper,
                heading_embedding,
            ),
            (
                reference_chunk,
                paper,
                reference_embedding,
            ),
            (
                method_chunk,
                paper,
                method_embedding,
            ),
        ]

        with (
            patch(
                'research_library.retrieval.semantic_search.task_crud.'
                "get_task_chunk_embeddings",
                return_value=candidates,
            ),
            patch(
                'research_library.retrieval.semantic_search.embed_texts',
                return_value=[[1.0, 0.0]],
            ),
        ):
            with TestingSessionLocal() as db:
                results = (
                    semantic_search
                    .search_task_chunks_semantic(
                        db=db,
                        task_id=1,
                        question=(
                            "semantic caching method "
                            "online adaptation cost"
                        ),
                        top_k=1,
                    )
                )

        self.assertEqual(
            len(results),
            1,
        )

        self.assertIs(
            results[0][0],
            method_chunk,
        )

    def test_find_reference_pages_marks_whole_page(self):
        paper = SimpleNamespace(
            id=3,
            title="语义缓存论文",
        )

        heading_chunk = SimpleNamespace(
            id=124,
            page_number=10,
            text="REFERENCES [1] First paper",
        )

        later_chunk = SimpleNamespace(
            id=127,
            page_number=10,
            text="An online framework to reduce LLM calls",
        )

        method_chunk = SimpleNamespace(
            id=14,
            page_number=1,
            text="Our semantic caching method",
        )

        fake_embedding = SimpleNamespace(
            vector=[1.0, 0.0],
        )

        candidates = [
            (
                heading_chunk,
                paper,
                fake_embedding,
            ),
            (
                later_chunk,
                paper,
                fake_embedding,
            ),
            (
                method_chunk,
                paper,
                fake_embedding,
            ),
        ]

        reference_pages = (
            semantic_search.find_reference_pages(
                candidates
            )
        )

        self.assertEqual(
            reference_pages,
            {
                (3, 10),
            },
        )

        self.assertIn(
            (
                paper.id,
                later_chunk.page_number,
            ),
            reference_pages,
        )

        self.assertNotIn(
            (
                paper.id,
                method_chunk.page_number,
            ),
            reference_pages,
        )
