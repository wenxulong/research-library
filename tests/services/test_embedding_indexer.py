"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestEmbeddingIndexer(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def test_generate_and_reuse_chunk_embeddings(self):
        with TestingSessionLocal() as db:
            paper = Paper(
                title="向量测试论文",
                author="测试作者",
                year=2026,
                notes="",
            )

            document = PaperDocument(
                paper=paper,
                original_name="test.pdf",
                stored_name="test-embedding.pdf",
                file_path="/tmp/test-embedding.pdf",
                content_type="application/pdf",
                file_size=100,
            )

            first_chunk = PaperChunk(
                document=document,
                page_number=1,
                chunk_index=1,
                text="第一段论文正文",
                char_count=8,
            )

            second_chunk = PaperChunk(
                document=document,
                page_number=1,
                chunk_index=2,
                text="第二段论文正文",
                char_count=8,
            )

            db.add_all([
                paper,
                document,
                first_chunk,
                second_chunk,
            ])
            db.commit()
            db.refresh(document)

            fake_vectors = [
                [1.0, 0.0],
                [0.0, 1.0],
            ]

            with patch(
                'research_library.retrieval.embedding_indexer.embed_texts',
                return_value=fake_vectors,
            ) as mock_embed:
                indexed_count = (
                    embedding_indexer.index_document_chunks(
                        db,
                        document.id,
                    )
                )

            self.assertEqual(indexed_count, 2)
            mock_embed.assert_called_once_with(
                [
                    "第一段论文正文",
                    "第二段论文正文",
                ]
            )

            saved_embeddings = (
                db.query(PaperChunkEmbedding)
                .order_by(PaperChunkEmbedding.chunk_id)
                .all()
            )

            self.assertEqual(len(saved_embeddings), 2)
            self.assertEqual(
                saved_embeddings[0].vector,
                [1.0, 0.0],
            )
            self.assertEqual(
                saved_embeddings[1].vector,
                [0.0, 1.0],
            )

            with patch(
                'research_library.retrieval.embedding_indexer.embed_texts',
            ) as mock_embed_again:
                second_count = (
                    embedding_indexer.index_document_chunks(
                        db,
                        document.id,
                    )
                )

            self.assertEqual(second_count, 0)
            mock_embed_again.assert_not_called()

    def test_embedding_api_generates_vectors(self):
        paper_response = client.post(
            "/papers",
            json={
                "title": "API向量测试论文",
                "author": "测试作者",
                "year": 2026,
                "notes": "",
            },
        )

        self.assertEqual(paper_response.status_code, 201)
        paper_id = paper_response.json()["id"]

        with TestingSessionLocal() as db:
            paper = db.get(Paper, paper_id)

            document = PaperDocument(
                paper=paper,
                original_name="api-test.pdf",
                stored_name="api-test-embedding.pdf",
                file_path="/tmp/api-test-embedding.pdf",
                content_type="application/pdf",
                file_size=100,
            )

            first_chunk = PaperChunk(
                document=document,
                page_number=1,
                chunk_index=1,
                text="向量检索可以寻找语义相关内容",
                char_count=14,
            )

            second_chunk = PaperChunk(
                document=document,
                page_number=2,
                chunk_index=1,
                text="大语言模型可以生成研究总结",
                char_count=13,
            )

            db.add_all([
                document,
                first_chunk,
                second_chunk,
            ])
            db.commit()
            db.refresh(document)

            document_id = document.id

        fake_vectors = [
            [1.0, 0.0],
            [0.5, 0.5],
        ]

        with patch(
                'research_library.retrieval.embedding_indexer.embed_texts',
                return_value=fake_vectors,
        ) as mock_embed:
            response = client.post(
                f"/papers/{paper_id}/chunks/embeddings"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "paper_id": paper_id,
                "document_id": document_id,
                "model_name": embedding_indexer.EMBEDDING_MODEL,
                "total_count": 2,
                "indexed_count": 2,
            },
        )

        mock_embed.assert_called_once_with(
            [
                "向量检索可以寻找语义相关内容",
                "大语言模型可以生成研究总结",
            ]
        )

        with TestingSessionLocal() as db:
            saved_embeddings = (
                db.query(PaperChunkEmbedding)
                .order_by(PaperChunkEmbedding.chunk_id)
                .all()
            )

            self.assertEqual(len(saved_embeddings), 2)
            self.assertEqual(
                saved_embeddings[0].vector,
                [1.0, 0.0],
            )
            self.assertEqual(
                saved_embeddings[1].vector,
                [0.5, 0.5],
            )
