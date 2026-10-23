"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestPaperDocumentAPI(unittest.TestCase):
    """测试 PDF 上传、查询、校验和删除接口。"""
    def setUp(self):
        """在每个测试前准备独立测试环境。"""
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

        self.temporary_directory = TemporaryDirectory()
        self.original_upload_dir = document_service.UPLOAD_DIR

        document_service.UPLOAD_DIR = Path(
            self.temporary_directory.name
        )

    def tearDown(self):
        """在每个测试后恢复配置并清理临时资源。"""
        document_service.UPLOAD_DIR = self.original_upload_dir
        self.temporary_directory.cleanup()

    def _create_test_paper(self):
        """创建测试论文并返回响应数据。"""
        response = client.post(
            "/papers",
            json={
                "title": "PDF测试论文",
                "author": "测试作者",
                "year": 2025,
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 201)
        return response.json()

    def _upload_test_pdf(self, paper_id):
        """上传测试 PDF 并返回响应数据。"""
        response = client.post(
            f"/papers/{paper_id}/document",
            files={
                "file": (
                    "test-paper.pdf",
                    b"%PDF-1.4\nTest PDF content",
                    "application/pdf",
                ),
            },
        )

        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_upload_pdf(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()

        response = client.post(
            f"/papers/{paper['id']}/document",
            files={
                "file": (
                    "research.pdf",
                    b"%PDF-1.4\nTest PDF content",
                    "application/pdf",
                ),
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(result["paper_id"], paper["id"])
        self.assertEqual(result["original_name"], "research.pdf")
        self.assertEqual(result["content_type"], "application/pdf")
        self.assertGreater(result["file_size"], 0)

        saved_files = list(document_service.UPLOAD_DIR.iterdir())
        self.assertEqual(len(saved_files), 1)

    def test_read_pdf_information(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        uploaded_document = self._upload_test_pdf(paper["id"])

        response = client.get(
            f"/papers/{paper['id']}/document"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), uploaded_document)

    def test_reject_non_pdf_file(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()

        response = client.post(
            f"/papers/{paper['id']}/document",
            files={
                "file": (
                    "not-pdf.txt",
                    b"This is not a PDF",
                    "text/plain",
                ),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"detail": "只能上传PDF文件"},
        )

    def test_reject_duplicate_pdf(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        self._upload_test_pdf(paper["id"])

        response = client.post(
            f"/papers/{paper['id']}/document",
            files={
                "file": (
                    "second.pdf",
                    b"%PDF-1.4\nSecond PDF",
                    "application/pdf",
                ),
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"detail": "这篇论文已经上传了PDF"},
        )

    def test_upload_pdf_for_missing_paper(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.post(
            "/papers/999999/document",
            files={
                "file": (
                    "missing.pdf",
                    b"%PDF-1.4\nTest PDF",
                    "application/pdf",
                ),
            },
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_pdf(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        uploaded_document = self._upload_test_pdf(paper["id"])

        delete_response = client.delete(
            f"/papers/{paper['id']}/document"
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(
            delete_response.json(),
            {
                "message": "PDF删除成功",
                "deleted_document": uploaded_document,
            },
        )

        self.assertEqual(
            list(document_service.UPLOAD_DIR.iterdir()),
            [],
        )

        get_response = client.get(
            f"/papers/{paper['id']}/document"
        )

        self.assertEqual(get_response.status_code, 404)

    def test_extract_and_read_pages(self):
        """验证真实 PDF 可以提取、保存和查询。"""
        paper = self._create_test_paper()

        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "Research paper content")
            pdf_bytes = pdf.tobytes()

        upload_response = client.post(
            f"/papers/{paper['id']}/document",
            files={
                "file": (
                    "real.pdf",
                    pdf_bytes,
                    "application/pdf",
                ),
            },
        )
        self.assertEqual(upload_response.status_code, 201)

        extract_response = client.post(
            f"/papers/{paper['id']}/document/extract"
        )
        self.assertEqual(extract_response.status_code, 200)

        pages = extract_response.json()
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["page_number"], 1)
        self.assertEqual(pages[0]["text"], "Research paper content")
        self.assertEqual(pages[0]["char_count"], 22)

        read_response = client.get(
            f"/papers/{paper['id']}/pages"
        )
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(read_response.json(), pages)

        repeat_response = client.post(
            f"/papers/{paper['id']}/document/extract"
        )
        self.assertEqual(repeat_response.status_code, 200)
        self.assertEqual(len(repeat_response.json()), 1)

    def test_create_read_and_replace_chunks(self):
        """验证分块、查询、重新分块及页面更新后的清理。"""
        paper = self._create_test_paper()
        paper_id = paper["id"]

        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "ABCDEFGHIJ")
            pdf_bytes = pdf.tobytes()

        upload_response = client.post(
            f"/papers/{paper_id}/document",
            files={
                "file": (
                    "chunks-test.pdf",
                    pdf_bytes,
                    "application/pdf",
                ),
            },
        )
        self.assertEqual(upload_response.status_code, 201)

        before_extract = client.post(
            f"/papers/{paper_id}/chunks?chunk_size=6&overlap=2"
        )
        self.assertEqual(before_extract.status_code, 409)

        extract_response = client.post(
            f"/papers/{paper_id}/document/extract"
        )
        self.assertEqual(extract_response.status_code, 200)

        create_response = client.post(
            f"/papers/{paper_id}/chunks?chunk_size=6&overlap=2"
        )
        self.assertEqual(create_response.status_code, 200)

        chunks = create_response.json()
        self.assertEqual(
            [(chunk["page_number"], chunk["chunk_index"], chunk["text"])
             for chunk in chunks],
            [(1, 1, "ABCDEF"), (1, 2, "EFGHIJ")],
        )

        read_response = client.get(f"/papers/{paper_id}/chunks")
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(read_response.json(), chunks)

        replace_response = client.post(
            f"/papers/{paper_id}/chunks?chunk_size=4&overlap=1"
        )
        self.assertEqual(replace_response.status_code, 200)
        self.assertEqual(
            [chunk["text"] for chunk in replace_response.json()],
            ["ABCD", "DEFG", "GHIJ"],
        )

        extract_again = client.post(
            f"/papers/{paper_id}/document/extract"
        )
        self.assertEqual(extract_again.status_code, 200)

        after_extract = client.get(f"/papers/{paper_id}/chunks")
        self.assertEqual(after_extract.json(), [])

    def test_search_paper_chunks(self):
        """验证关键词搜索能找到文本块并处理空结果。"""
        paper = self._create_test_paper()
        paper_id = paper["id"]

        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "RAG retrieval methods")
            pdf_bytes = pdf.tobytes()

        upload = client.post(
            f"/papers/{paper_id}/document",
            files={
                "file": (
                    "search-test.pdf",
                    pdf_bytes,
                    "application/pdf",
                ),
            },
        )
        self.assertEqual(upload.status_code, 201)

        extract = client.post(
            f"/papers/{paper_id}/document/extract"
        )
        self.assertEqual(extract.status_code, 200)

        create = client.post(f"/papers/{paper_id}/chunks")
        self.assertEqual(create.status_code, 200)

        found = client.get(
            f"/papers/{paper_id}/chunks/search",
            params={"q": "RAG"},
        )
        self.assertEqual(found.status_code, 200)
        self.assertEqual(len(found.json()), 1)
        self.assertEqual(found.json()[0]["text"], "RAG retrieval methods")

        missing = client.get(
            f"/papers/{paper_id}/chunks/search",
            params={"q": "Python"},
        )
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.json(), [])

        blank = client.get(
            f"/papers/{paper_id}/chunks/search",
            params={"q": "   "},
        )
        self.assertEqual(blank.status_code, 400)

    def test_search_only_papers_linked_to_task(self):
        """任务搜索不能返回未加入任务的论文。"""
        task_response = client.post(
            "/research-tasks",
            json={
                "title": "RAG调研",
                "question": "RAG有哪些方法？",
            },
        )
        self.assertEqual(task_response.status_code, 201)
        task_id = task_response.json()["id"]

        paper_ids = []

        for title, text in [
            ("论文A", "RAG alpha"),
            ("论文B", "RAG beta"),
            ("论文C", "RAG excluded"),
        ]:
            paper_response = client.post(
                "/papers",
                json={
                    "title": title,
                    "author": "测试作者",
                    "year": 2025,
                    "notes": "",
                },
            )
            self.assertEqual(paper_response.status_code, 201)
            paper_id = paper_response.json()["id"]
            paper_ids.append(paper_id)

            with pymupdf.open() as pdf:
                page = pdf.new_page()
                page.insert_text((72, 72), text)
                pdf_bytes = pdf.tobytes()

            upload = client.post(
                f"/papers/{paper_id}/document",
                files={
                    "file": (
                        "test.pdf",
                        pdf_bytes,
                        "application/pdf",
                    ),
                },
            )
            self.assertEqual(upload.status_code, 201)

            extract = client.post(
                f"/papers/{paper_id}/document/extract"
            )
            self.assertEqual(extract.status_code, 200)

            create_chunks = client.post(
                f"/papers/{paper_id}/chunks"
            )
            self.assertEqual(create_chunks.status_code, 200)

        for paper_id in paper_ids[:2]:
            link = client.post(
                f"/research-tasks/{task_id}/papers/{paper_id}",
                json={"note": ""},
            )
            self.assertEqual(link.status_code, 201)

        search = client.get(
            f"/research-tasks/{task_id}/chunks/search",
            params={"q": "RAG"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(
            [item["paper_id"] for item in search.json()],
            paper_ids[:2],
        )
        self.assertEqual(
            [item["paper_title"] for item in search.json()],
            ["论文A", "论文B"],
        )
