"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestPDFParser(unittest.TestCase):
    """测试 PDF 正文提取和异常处理。"""
    def setUp(self):
        """在每个测试前准备独立测试环境。"""
        self.temporary_directory = TemporaryDirectory()
        self.test_directory = Path(self.temporary_directory.name)

    def tearDown(self):
        """在每个测试后恢复配置并清理临时资源。"""
        self.temporary_directory.cleanup()

    def _create_test_pdf(self) -> Path:
        """创建一份包含两页文字的真实测试 PDF。"""
        file_path = self.test_directory / "parser-test.pdf"

        with pymupdf.open() as document:
            first_page = document.new_page()
            first_page.insert_text(
                (72, 72),
                "First page content",
            )

            second_page = document.new_page()
            second_page.insert_text(
                (72, 72),
                "Second page content",
            )

            document.save(file_path)

        return file_path

    def test_extract_pdf_text(self):
        """验证该场景的实际结果与预期一致。"""
        file_path = self._create_test_pdf()

        result = extract_pdf_text(file_path)

        self.assertEqual(result.page_count, 2)
        self.assertEqual(len(result.pages), 2)
        self.assertEqual(result.pages[0].page_number, 1)
        self.assertIn("First page content", result.pages[0].text)
        self.assertEqual(result.pages[1].page_number, 2)
        self.assertIn("Second page content", result.pages[1].text)
        self.assertEqual(
            result.full_text,
            "\n\n".join(page.text for page in result.pages),
        )

    def test_extract_missing_pdf(self):
        """验证该场景的实际结果与预期一致。"""
        missing_path = self.test_directory / "missing.pdf"

        with self.assertRaises(FileNotFoundError):
            extract_pdf_text(missing_path)

    def test_extract_invalid_pdf(self):
        """验证该场景的实际结果与预期一致。"""
        file_path = self.test_directory / "invalid.pdf"

        file_path.write_text(
            "这不是一个真正的PDF",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ValueError,
            "无法解析PDF文件",
        ):
            extract_pdf_text(file_path)
