"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestReviewService(unittest.TestCase):
    """验证审校证据定位和引用检查，不访问真实数据库或大模型。"""

    def setUp(self):
        self.chunk = SimpleNamespace(
            id=14, document_id=2, page_number=1, text="方法正文",
        )
        self.document = SimpleNamespace(id=2, paper_id=3)
        self.analysis = SimpleNamespace(
            summary="概述[资料1]",
            methods=["方法[资料1]"],
            experiments=[], conclusions=[], limitations=[],
            sources=[SimpleNamespace(
                chunk_id=14, paper_id=3, page_number=1, paper_title="测试论文",
            )],
        )
        self.db = Mock()
        self.db.get.side_effect = [self.chunk, self.document]

    def test_evidence_keeps_chunk_id_instead_of_document_id(self):
        evidence = review_service.load_review_evidence(self.db, self.analysis, 1)
        self.assertEqual(evidence.chunk_id, 14)
        self.assertEqual(evidence.text, "方法正文")
        self.assertEqual(
            self.db.get.call_args_list,
            [unittest.mock.call(PaperChunk, 14), unittest.mock.call(PaperDocument, 2)],
        )

    def test_prepare_claims_reuses_evidence_for_same_source(self):
        claims = review_service.prepare_review_claims(self.db, self.analysis)
        self.assertEqual([claim.field for claim in claims], ["summary", "methods"])
        self.assertEqual(claims[0].source_numbers, [1])
        self.assertEqual(claims[0].evidence[0].chunk_id, 14)
        self.assertIs(claims[0].evidence[0], claims[1].evidence[0])
        self.assertEqual(self.db.get.call_count, 2)

    def test_source_numbers_keep_order_and_remove_duplicates(self):
        self.assertEqual(
            review_service.extract_source_numbers("[资料 2][资料1][资料2]", 2),
            [2, 1],
        )
        self.assertEqual(review_service.extract_source_numbers("当前资料不足", 2), [])

    def test_review_rejects_invalid_citations(self):
        for text in ("[资料1", "[资料", "[资料1][资料2", "[资料一]", "[资料1,2]"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "引用格式错误"):
                    review_service.extract_source_numbers(text, 2)
        for text in ("[资料0]", "[资料3]"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "引用不存在"):
                    review_service.extract_source_numbers(text, 2)

    def test_review_rejects_missing_or_changed_evidence(self):
        cases = [
            ([None], "文本块已经不存在"),
            ([self.chunk, None], "PDF记录已经不存在"),
            ([self.chunk, SimpleNamespace(paper_id=99)], "信息不一致"),
            ([SimpleNamespace(document_id=2, page_number=9), self.document], "信息不一致"),
        ]
        for records, message in cases:
            with self.subTest(message=message, records=records):
                db = Mock()
                db.get.side_effect = records
                with self.assertRaisesRegex(ValueError, message):
                    review_service.load_review_evidence(db, self.analysis, 1)
