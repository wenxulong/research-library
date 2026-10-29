"""结构化分块测试；不连接数据库、向量服务或大模型。"""

import unittest

from research_library.retrieval.pdf_parser import ExtractedPage
from research_library.retrieval.text_chunker import chunk_pages, split_prose, split_table


class TestStructuredChunking(unittest.TestCase):
    def test_sentence_boundary_and_overlap(self):
        parts = split_prose("甲" * 11 + "。" + "乙" * 18, 20, 4)
        self.assertEqual(parts[0], "甲" * 11 + "。")
        self.assertEqual(parts[0][-4:], parts[1][:4])

    def test_paragraph_boundary_has_priority(self):
        parts = split_prose("甲" * 10 + "\n\n" + "乙" * 4 + "。" + "丙" * 20, 20, 4)
        self.assertEqual(parts[0], "甲" * 10 + "\n\n")

    def test_english_decimal_is_not_sentence_end(self):
        # 小数点恰好位于长度上限时，不应把切分窗口当作句末。
        parts = split_prose("AAAA. BBBBBBB 3.14 and more text", 15, 2)
        self.assertEqual(len(parts[0]), 15)

    def test_body_is_not_lost_with_high_overlap(self):
        text = "第一段正文。\n\n第二段结果。" * 12
        for overlap in (0, 5, 29):
            with self.subTest(overlap=overlap):
                parts = split_prose(text, 30, overlap)
                rebuilt = parts[0] + "".join(part[overlap:] for part in parts[1:])
                self.assertEqual(rebuilt, text)
                self.assertTrue(all(len(part) <= 30 for part in parts))

    def test_table_keeps_every_row_and_repeats_header(self):
        lines = ["| 方法 | 分数 |", "| --- | --- |", "| A | 1 |", "| B | 2 |", "| C | 3 |"]
        header = "\n".join(lines[:2])
        limit = len(header) + 1 + len(lines[2])
        parts = split_table(lines, limit)
        self.assertEqual(parts, [header + "\n" + row for row in lines[2:]])

    def test_prose_table_order_page_numbers_and_blank_page(self):
        table = "| Method | Score |\n| --- | --- |\n| A | 90 |"
        chunks = chunk_pages([
            ExtractedPage(3, "Before table.\n\n" + table + "\n\nFollowing text."),
            ExtractedPage(4, "  "),
            ExtractedPage(5, "Next page."),
        ])
        self.assertEqual([c.text for c in chunks], ["Before table.", table, "Following text.", "Next page."])
        self.assertEqual([(c.page_number, c.chunk_index) for c in chunks], [(3, 1), (3, 2), (3, 3), (5, 1)])

    def test_inline_pipe_does_not_turn_prose_into_table(self):
        text = "A | B is ordinary text.\nAnother sentence."
        self.assertEqual(chunk_pages([ExtractedPage(1, text)])[0].text, text)

    def test_two_adjacent_tables_are_not_merged(self):
        first = "| A | B |\n| --- | --- |\n| x | y |"
        second = "| C | D |\n| --- | --- |\n| z | w |"
        chunks = chunk_pages([ExtractedPage(1, first + "\n" + second)])
        self.assertEqual([c.text for c in chunks], [first, second])

    def test_single_column_markdown_table(self):
        table = "| Name |\n| --- |\n| Alice |"
        self.assertEqual(chunk_pages([ExtractedPage(1, table)])[0].text, table)

    def test_oversized_row_has_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "单行过长"):
            split_table(["| A | B |", "| --- | --- |", "| " + "x" * 100 + " | y |"], 50)

    def test_caption_is_repeated_with_long_table(self):
        text = "## Table 1: results\n\n| Method | Score |\n| --- | --- |\n| A | 1 |\n| B | 2 |"
        chunks = chunk_pages([ExtractedPage(1, text)], table_chunk_size=65)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(c.text.startswith("## Table 1: results\n| Method | Score |") for c in chunks))

    def test_invalid_table_size(self):
        with self.assertRaisesRegex(ValueError, "table_chunk_size"):
            chunk_pages([], table_chunk_size=0)


if __name__ == "__main__":
    unittest.main()
