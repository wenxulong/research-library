"""真实PDF集成测试：版面、表格、图片OCR及后续分块；不访问业务数据。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pymupdf

from research_library.retrieval.pdf_parser import extract_pdf_text
from research_library.retrieval.text_chunker import chunk_pages


class TestPDFLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "layout.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_textbox((50, 70, 280, 650), "LEFT_START\n" + "Left column research context.\n" * 12 + "LEFT_END", fontsize=12)
            page.insert_textbox((320, 70, 550, 650), "RIGHT_START\n" + "Right column experiment results.\n" * 12 + "RIGHT_END", fontsize=12)
            page = doc.new_page(width=600, height=800)
            page.insert_text((60, 70), "Table 1: cache performance", fontsize=14)
            xs, ys = [60, 220, 380, 540], [100, 145, 190, 235]
            for x in xs:
                page.draw_line((x, 100), (x, 235))
            for y in ys:
                page.draw_line((60, y), (540, y))
            for r, row in enumerate([["Method", "Hit rate", "Error rate"], ["Alpha", "80%", "5%"], ["Beta", "85%", "3%"]]):
                for c, value in enumerate(row):
                    page.insert_text((xs[c] + 12, ys[r] + 28), value, fontsize=12)
            with pymupdf.open() as source:
                page = source.new_page(width=600, height=800)
                page.insert_text((60, 100), "OCR SAMPLE: cache accuracy is 95 percent.", fontsize=18)
                page.insert_text((60, 150), "This page contains only an image.", fontsize=18)
                page.insert_text((60, 210), "Semantic caching reduces repeated requests.", fontsize=18)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                scan = doc.new_page(width=600, height=800)
                scan.insert_image(scan.rect, stream=pix.tobytes("png"))
            doc.new_page(width=600, height=800)
            doc.save(cls.path)
        cls.result = extract_pdf_text(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_original_page_numbers_including_blank_page(self):
        self.assertEqual(self.result.page_count, 4)
        self.assertEqual([p.page_number for p in self.result.pages], [1, 2, 3, 4])
        self.assertEqual(self.result.pages[3].text, "")

    def test_two_column_reading_order(self):
        text = self.result.pages[0].text
        positions = [text.index(marker) for marker in ("LEFT_START", "LEFT_END", "RIGHT_START", "RIGHT_END")]
        self.assertEqual(positions, sorted(positions))

    def test_table_header_and_values_stay_associated(self):
        text = self.result.pages[1].text
        rows = [line for line in text.splitlines() if line.startswith("|")]
        cells = [[cell.strip() for cell in row.strip("|").split("|")] for row in rows]
        self.assertEqual(cells[0], ["Method", "Hit rate", "Error rate"])
        self.assertIn(["Alpha", "80%", "5%"], cells)
        self.assertIn(["Beta", "85%", "3%"], cells)
        self.assertEqual(sum("Alpha" in row for row in rows), 1)

    def test_real_ocr_on_image_only_page(self):
        with pymupdf.open(self.path) as doc:
            self.assertEqual(doc[2].get_text().strip(), "")
        text = self.result.pages[2].text.lower()
        self.assertIn("cache accuracy is 95 percent", text)
        self.assertIn("only an image", text)

    def test_parsed_table_survives_chunking(self):
        chunks = chunk_pages(self.result.pages)
        table = next(c for c in chunks if "|Alpha|" in c.text)
        self.assertEqual(table.page_number, 2)
        self.assertIn("|Method|Hit rate|Error rate|", table.text)
        self.assertIn("|Alpha|80%|5%|", table.text)
        self.assertIn("|Beta|85%|3%|", table.text)

    def test_parser_enables_ocr_and_rejects_wrong_page_mapping(self):
        with patch("research_library.retrieval.pdf_parser.pymupdf4llm.to_markdown", return_value=[]) as convert:
            with self.assertRaisesRegex(ValueError, "页码"):
                extract_pdf_text(self.path)
            self.assertTrue(convert.call_args.kwargs["use_ocr"])
            self.assertTrue(convert.call_args.kwargs["page_chunks"])

    def test_encrypted_pdf_is_rejected(self):
        path = Path(self.temp.name) / "encrypted.pdf"
        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="reader")
        with self.assertRaisesRegex(ValueError, "加密"):
            extract_pdf_text(path)


if __name__ == "__main__":
    unittest.main()
