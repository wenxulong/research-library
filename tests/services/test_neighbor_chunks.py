"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestNeighborChunks(unittest.TestCase):
    """用不连续且乱序的数据库编号验证原文邻接关系。"""

    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        with TestingSessionLocal() as db:
            papers = [Paper(title=f"邻接论文{i}", author="测试", year=2026, notes="") for i in (1, 2)]
            db.add_all(papers)
            db.flush()
            docs = [PaperDocument(paper_id=p.id, original_name=f"neighbor-{p.id}.pdf",
                                  stored_name=f"neighbor-{p.id}.pdf", file_path="/unused/neighbor.pdf",
                                  content_type="application/pdf", file_size=1) for p in papers]
            db.add_all(docs)
            db.flush()
            self.document_id = docs[0].id
            for chunk_id, page, index in ((90, 1, 0), (7, 1, 1), (55, 2, 0), (130, 2, 1), (9, 3, 0)):
                db.add(PaperChunk(id=chunk_id, document_id=docs[0].id, page_number=page,
                                 chunk_index=index, text=f"原文{chunk_id}", char_count=4))
            db.add(PaperChunk(id=8, document_id=docs[1].id, page_number=1,
                             chunk_index=0, text="另一篇论文", char_count=5))
            db.commit()

    def _read(self, chunk_id, before=1, after=1):
        import research_library.repositories.chunk_crud as chunk_crud
        with TestingSessionLocal() as db:
            chunks = chunk_crud.get_neighbor_chunks(db, chunk_id, before, after)
            return [(chunk.id, chunk.document_id, chunk.page_number, chunk.chunk_index, chunk.text) for chunk in chunks]

    def test_default_returns_previous_current_next_in_document_order(self):
        result = self._read(55)
        self.assertEqual([row[0] for row in result], [7, 55, 130])
        self.assertTrue(all(row[1] == self.document_id for row in result))
        self.assertEqual(result[1][4], "原文55")

    def test_neighbors_cross_pages_without_using_database_id_arithmetic(self):
        self.assertEqual([row[0] for row in self._read(7)], [90, 7, 55])
        self.assertEqual([row[0] for row in self._read(130)], [55, 130, 9])

    def test_first_and_last_chunk_do_not_wrap_around(self):
        self.assertEqual([row[0] for row in self._read(90)], [90, 7])
        self.assertEqual([row[0] for row in self._read(9)], [130, 9])

    def test_zero_and_maximum_windows(self):
        self.assertEqual([row[0] for row in self._read(55, 0, 0)], [55])
        self.assertEqual([row[0] for row in self._read(55, 2, 2)], [90, 7, 55, 130, 9])

    def test_invalid_counts_fail_before_database_access(self):
        import research_library.repositories.chunk_crud as chunk_crud
        for before, after in ((-1, 1), (1, -1), (3, 1), (1, 3), (True, 1), (1, False), (1.5, 1), (1, "1")):
            with self.subTest(before=before, after=after):
                db = Mock()
                with self.assertRaises(ValueError):
                    chunk_crud.get_neighbor_chunks(db, 55, before, after)
                db.get.assert_not_called()

    def test_missing_chunk_reports_error(self):
        with self.assertRaisesRegex(ValueError, "没有找到"):
            self._read(9999)

    def test_changed_document_does_not_return_unrelated_chunks(self):
        with patch('research_library.repositories.chunk_crud.get_chunks_by_document_id', return_value=[]):
            with self.assertRaisesRegex(ValueError, "已经发生变化"):
                self._read(55)
