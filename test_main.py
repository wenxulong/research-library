"""测试 API、文件上传和 PDF 解析功能。"""

import unittest
import json

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from urllib.error import URLError

from api import app
from database import Base, get_db
from model import (
    Paper,
    PaperChunk,
    PaperChunkEmbedding,
    PaperDocument,
    ResearchTask,
)
import embedding_indexer
from pathlib import Path
from tempfile import TemporaryDirectory
from pdf_parser import extract_pdf_text

from pdf_parser import ExtractedPage
from text_chunker import chunk_pages

import document_service

from types import SimpleNamespace
from unittest.mock import Mock, patch

import semantic_search
import query_rewriter
import paper_analysis_agent
import rag_service
import review_service
import review_agent
import revision_agent
import field_splitter
from review_service import ReviewClaim, ReviewEvidence
from schemas import ResearchTaskAnalysisResponse, AnalysisSourceResponse, ReviewItemResponse, ResearchTaskReviewResponse

TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
)

Base.metadata.create_all(bind = test_engine)

def override_get_db():
    """让测试接口使用独立的 SQLite 内存数据库。"""
    with TestingSessionLocal() as db:
        yield db

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

class TestPaperAPI(unittest.TestCase):
    """测试论文增删改查接口及输入校验。"""
    def setUp(self):
        """在每个测试前准备独立测试环境。"""
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def _create_test_paper(self):
        """创建测试论文并返回响应数据。"""
        response = client.post(
            "/papers",
            json={
                "title": "测试论文",
                "author": "测试作者",
                "year": 2025,
                "notes": "这是一篇测试论文",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_read_root(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"message": "个人科研资料库 API 正在运行"},
        )

    def test_read_all_papers_is_empty(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get("/papers")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_create_paper(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.post(
            "/papers",
            json={
                "title": "大语言模型研究",
                "author": "张三",
                "year": 2025,
                "notes": "学习大语言模型应用",
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 201)
        self.assertIsInstance(result["id"], int)
        self.assertEqual(result["title"], "大语言模型研究")
        self.assertEqual(result["author"], "张三")
        self.assertEqual(result["year"], 2025)
        self.assertEqual(result["notes"], "学习大语言模型应用")

    def test_read_one_paper(self):
        """验证该场景的实际结果与预期一致。"""
        created_paper = self._create_test_paper()
        paper_id = created_paper["id"]

        response = client.get(f"/papers/{paper_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created_paper)

    def test_update_paper(self):
        """验证该场景的实际结果与预期一致。"""
        created_paper = self._create_test_paper()
        paper_id = created_paper["id"]

        response = client.put(
            f"/papers/{paper_id}",
            json={
                "title": "修改后的论文",
                "author": "修改后的作者",
                "year": 2026,
                "notes": "论文内容已经修改",
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["id"], paper_id)
        self.assertEqual(result["title"], "修改后的论文")
        self.assertEqual(result["author"], "修改后的作者")
        self.assertEqual(result["year"], 2026)
        self.assertEqual(result["notes"], "论文内容已经修改")

    def test_delete_paper(self):
        """验证该场景的实际结果与预期一致。"""
        created_paper = self._create_test_paper()
        paper_id = created_paper["id"]

        delete_response = client.delete(f"/papers/{paper_id}")

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(
            delete_response.json(),
            {
                "message": "论文删除成功",
                "deleted_paper": created_paper,
            },
        )

        get_response = client.get(f"/papers/{paper_id}")

        self.assertEqual(get_response.status_code, 404)

    def test_read_missing_paper(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get("/papers/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "没有找到编号为 999999 的论文"},
        )

    def test_read_paper_with_invalid_id(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get("/papers/abc")

        self.assertEqual(response.status_code, 422)

    def test_create_paper_with_invalid_data(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.post(
            "/papers",
            json={
                "title": "",
                "author": "测试作者",
                "year": 25,
                "notes": "错误的数据",
            },
        )

        self.assertEqual(response.status_code, 422)


class TestResearchTaskAPI(unittest.TestCase):
    """测试研究任务接口和状态更新规则。"""
    def setUp(self):
        """在每个测试前准备独立测试环境。"""
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def _create_test_task(self):
        """创建测试研究任务并返回响应数据。"""
        response = client.post(
            "/research-tasks",
            json={
                "title": "调研大模型智能体",
                "question": "主流大模型智能体有哪些技术路线？",
            },
        )

        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_create_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.post(
            "/research-tasks",
            json={
                "title": "RAG分块策略调研",
                "question": "不同分块策略如何影响RAG检索效果？",
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 201)
        self.assertIsInstance(result["id"], int)
        self.assertEqual(result["title"], "RAG分块策略调研")
        self.assertEqual(
            result["question"],
            "不同分块策略如何影响RAG检索效果？",
        )
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(result["result"])

    def test_read_all_research_tasks(self):
        """验证该场景的实际结果与预期一致。"""
        created_task = self._create_test_task()

        response = client.get("/research-tasks")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [created_task])

    def test_read_one_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        created_task = self._create_test_task()
        task_id = created_task["id"]

        response = client.get(f"/research-tasks/{task_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created_task)

    def test_read_missing_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get("/research-tasks/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "没有找到编号为 999999 的研究任务"},
        )

    def test_update_research_task_status(self):
        """验证该场景的实际结果与预期一致。"""
        created_task = self._create_test_task()
        task_id = created_task["id"]

        response = client.patch(
            f"/research-tasks/{task_id}/status",
            json={
                "status": "completed",
                "result": "已经完成技术路线调研。",
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["id"], task_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"], "已经完成技术路线调研。")

    def test_update_research_task_with_invalid_status(self):
        """验证该场景的实际结果与预期一致。"""
        created_task = self._create_test_task()
        task_id = created_task["id"]

        response = client.patch(
            f"/research-tasks/{task_id}/status",
            json={
                "status": "unknown",
                "result": None,
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_delete_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        created_task = self._create_test_task()
        task_id = created_task["id"]

        delete_response = client.delete(f"/research-tasks/{task_id}")

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(
            delete_response.json(),
            {
                "message": "研究任务删除成功",
                "deleted_task": created_task,
            },
        )

        get_response = client.get(f"/research-tasks/{task_id}")

        self.assertEqual(get_response.status_code, 404)


class TestResearchTaskPaperAPI(unittest.TestCase):
    """测试研究任务与论文的关联接口。"""
    def setUp(self):
        """在每个测试前准备独立测试环境。"""
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def _create_test_paper(self):
        """创建测试论文并返回响应数据。"""
        response = client.post(
            "/papers",
            json={
                "title": "RAG测试论文",
                "author": "测试作者",
                "year": 2025,
                "notes": "用于测试任务和论文的关联",
            },
        )

        self.assertEqual(response.status_code, 201)
        return response.json()

    def _create_test_task(self):
        """创建测试研究任务并返回响应数据。"""
        response = client.post(
            "/research-tasks",
            json={
                "title": "RAG分块策略调研",
                "question": "不同分块策略如何影响检索效果？",
            },
        )

        self.assertEqual(response.status_code, 201)
        return response.json()

    def _add_test_link(self, task_id, paper_id):
        """建立测试任务与论文的关联。"""
        response = client.post(
            f"/research-tasks/{task_id}/papers/{paper_id}",
            json={
                "note": "这是一篇核心参考论文",
            },
        )

        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_add_paper_to_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        task = self._create_test_task()

        response = client.post(
            f"/research-tasks/{task['id']}/papers/{paper['id']}",
            json={
                "note": "RAG领域的重要论文",
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 201)
        self.assertIsInstance(result["id"], int)
        self.assertEqual(result["task_id"], task["id"])
        self.assertEqual(result["paper_id"], paper["id"])
        self.assertEqual(result["note"], "RAG领域的重要论文")
        self.assertEqual(result["paper"], paper)

    def test_read_research_task_papers(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        task = self._create_test_task()
        link = self._add_test_link(task["id"], paper["id"])

        response = client.get(
            f"/research-tasks/{task['id']}/papers"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [link])

    def test_add_duplicate_paper_to_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        task = self._create_test_task()

        self._add_test_link(task["id"], paper["id"])

        response = client.post(
            f"/research-tasks/{task['id']}/papers/{paper['id']}",
            json={
                "note": "重复添加",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"detail": "这篇论文已经加入该研究任务"},
        )

    def test_add_paper_to_missing_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()

        response = client.post(
            f"/research-tasks/999999/papers/{paper['id']}",
            json={
                "note": "任务不存在",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "没有找到编号为 999999 的研究任务"},
        )

    def test_add_missing_paper_to_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        task = self._create_test_task()

        response = client.post(
            f"/research-tasks/{task['id']}/papers/999999",
            json={
                "note": "论文不存在",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "没有找到编号为 999999 的论文"},
        )

    def test_read_papers_for_missing_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get(
            "/research-tasks/999999/papers"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "没有找到编号为 999999 的研究任务"},
        )

    def test_remove_paper_from_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        task = self._create_test_task()
        link = self._add_test_link(task["id"], paper["id"])

        delete_response = client.delete(
            f"/research-tasks/{task['id']}/papers/{paper['id']}"
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(
            delete_response.json(),
            {
                "message": "论文已从研究任务中移除",
                "deleted_link": link,
            },
        )

        get_response = client.get(
            f"/research-tasks/{task['id']}/papers"
        )

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json(), [])

    def test_remove_missing_paper_link(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        task = self._create_test_task()

        response = client.delete(
            f"/research-tasks/{task['id']}/papers/{paper['id']}"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "这篇论文没有加入该研究任务"},
        )

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
        self.assertEqual(result.pages[0].text, "First page content")
        self.assertEqual(result.pages[1].page_number, 2)
        self.assertEqual(result.pages[1].text, "Second page content")
        self.assertEqual(
            result.full_text,
            "First page content\n\nSecond page content",
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

class TestTextChunker(unittest.TestCase):
    """测试逐页分块、重叠和参数校验。"""

    def test_chunk_pages_with_overlap(self):
        pages = [
            ExtractedPage(page_number=1, text="ABCDEFGHIJ"),
            ExtractedPage(page_number=2, text="   "),
            ExtractedPage(page_number=3, text="XYZ"),
        ]

        chunks = chunk_pages(pages, chunk_size=6, overlap=2)

        result = [
            (chunk.page_number, chunk.chunk_index, chunk.text)
            for chunk in chunks
        ]

        self.assertEqual(
            result,
            [
                (1, 1, "ABCDEF"),
                (1, 2, "EFGHIJ"),
                (3, 1, "XYZ"),
            ],
        )

    def test_reject_invalid_chunk_settings(self):
        pages = [ExtractedPage(page_number=1, text="ABCDE")]

        with self.assertRaises(ValueError):
            chunk_pages(pages, chunk_size=0)

        with self.assertRaises(ValueError):
            chunk_pages(pages, chunk_size=5, overlap=5)

class TestSemanticSearch(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def test_more_similar_chunk_comes_first(self):
        paper = SimpleNamespace(id=1, title="测试论文")

        first_chunk = SimpleNamespace(
            page_number=1,
            text="不相关的内容",
        )
        second_chunk = SimpleNamespace(
            page_number=2,
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
                "semantic_search.task_crud.get_task_chunk_embeddings",
                return_value=candidates,
            ),
            patch(
                "semantic_search.embed_texts",
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
            "api.semantic_search.search_task_chunks_semantic",
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
                "semantic_search.task_crud."
                "get_task_chunk_embeddings",
                return_value=candidates,
            ),
            patch(
                "semantic_search.embed_texts",
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
                "embedding_indexer.embed_texts",
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
                "embedding_indexer.embed_texts",
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
                "embedding_indexer.embed_texts",
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

class TestQueryRewriter(unittest.TestCase):
    def test_chinese_question_is_rewritten(self):
        with patch(
            "query_rewriter.generate_text",
            return_value=(
                "How does semantic caching reduce "
                "LLM serving costs?"
            ),
        ) as mock_generate:
            result = (
                query_rewriter
                .rewrite_query_for_search(
                    "语义缓存如何降低大模型服务成本？"
                )
            )

        self.assertIn(
            "语义缓存如何降低大模型服务成本？",
            result,
        )

        self.assertIn(
            "How does semantic caching reduce "
            "LLM serving costs?",
            result,
        )

        mock_generate.assert_called_once()

    def test_english_question_is_not_rewritten(self):
        question = (
            "How does semantic caching reduce "
            "LLM serving costs?"
        )

        with patch(
            "query_rewriter.generate_text",
        ) as mock_generate:
            result = (
                query_rewriter
                .rewrite_query_for_search(
                    question
                )
            )

        self.assertEqual(
            result,
            question,
        )

        mock_generate.assert_not_called()


class TestPaperAnalysisAgent(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def _citation_data(self, text):
        return {
            "summary": text,
            "methods": [],
            "experiments": [],
            "conclusions": [],
            "limitations": ["当前资料不足以分析局限性。"],
        }

    def test_citations_accept_valid_numbers_and_insufficient_sources(self):
        data = self._citation_data("语义缓存[资料1][资料 2]。")
        paper_analysis_agent.validate_analysis_citations(data, 2)

    def test_citations_reject_out_of_range_numbers_in_every_field(self):
        for field_name in (
            "summary", "methods", "experiments", "conclusions", "limitations"
        ):
            for marker in ("[资料0]", "[资料3]"):
                with self.subTest(field=field_name, marker=marker):
                    data = self._citation_data("概述[资料1]。")
                    data[field_name] = marker if field_name == "summary" else [marker]
                    with self.assertRaisesRegex(RuntimeError, "引用不存在"):
                        paper_analysis_agent.validate_analysis_citations(data, 2)

    def test_citations_reject_malformed_numbers(self):
        for marker in ("[资料-1]", "[资料一]", "[资料1,2]", "[资料]"):
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(RuntimeError, "引用格式错误"):
                    paper_analysis_agent.validate_analysis_citations(
                        self._citation_data(marker), 2
                    )

    def test_citations_reject_unclosed_markers_in_every_field(self):
        for field in ("summary", "methods", "experiments", "conclusions", "limitations"):
            for text in ("结论[资料1", "结论[资料", "[资料1][资料2"):
                with self.subTest(field=field, text=text):
                    data = self._citation_data("概述[资料1]")
                    data[field] = text if field == "summary" else [text]
                    with self.assertRaisesRegex(RuntimeError, "引用格式错误"):
                        paper_analysis_agent.validate_analysis_citations(data, 2)

    def test_analyze_rejects_nonexistent_citation_before_return(self):
        chunk = SimpleNamespace(id=10, page_number=1, text="论文正文")
        paper = SimpleNamespace(id=1, title="测试论文")
        response_text = json.dumps(self._citation_data("错误引用[资料2]"))
        with (
            patch(
                "paper_analysis_agent.collect_analysis_matches",
                return_value=[(chunk, paper, 0.9)],
            ),
            patch("paper_analysis_agent.generate_text", return_value=response_text),
        ):
            with TestingSessionLocal() as db:
                with self.assertRaisesRegex(RuntimeError, "引用不存在"):
                    paper_analysis_agent.analyze_research_task(db, 1, "研究问题")

    def test_parse_analysis_response_removes_code_block(self):
        response_text = """```json
{
  "summary": "论文研究语义缓存。",
  "methods": ["提出在线学习算法[资料1]"],
  "experiments": ["使用真实查询数据[资料2]"],
  "conclusions": ["方法可以降低服务成本[资料3]"],
  "limitations": ["资料没有说明完整局限性"]
}
```"""

        result = (
            paper_analysis_agent
            .parse_analysis_response(
                response_text
            )
        )

        self.assertEqual(
            result["summary"],
            "论文研究语义缓存。",
        )
        self.assertEqual(
            result["methods"],
            ["提出在线学习算法[资料1]"],
        )
        self.assertEqual(
            result["experiments"],
            ["使用真实查询数据[资料2]"],
        )
        self.assertEqual(
            result["conclusions"],
            ["方法可以降低服务成本[资料3]"],
        )
        self.assertEqual(
            result["limitations"],
            ["资料没有说明完整局限性"],
        )

    def test_parse_analysis_response_rejects_invalid_json(self):
        with self.assertRaises(RuntimeError):
            (
                paper_analysis_agent
                .parse_analysis_response(
                    "这不是正确的JSON"
                )
            )

    def test_collect_analysis_matches_removes_duplicates(self):
        paper = SimpleNamespace(
            id=1,
            title="语义缓存论文",
        )
        first_chunk = SimpleNamespace(
            id=10,
            page_number=1,
            text="论文核心方法",
        )
        second_chunk = SimpleNamespace(
            id=20,
            page_number=3,
            text="论文实验结果",
        )

        search_results = [
            [(first_chunk, paper, 0.70)],
            [
                (first_chunk, paper, 0.85),
                (second_chunk, paper, 0.60),
            ],
            [(first_chunk, paper, 0.75)],
            [(second_chunk, paper, 0.65)],
            [(first_chunk, paper, 0.80)],
        ]

        with (
            patch(
                "paper_analysis_agent.task_crud.get_task_paper_links",
                return_value=[SimpleNamespace(paper_id=1)],
            ),
            patch(
                "paper_analysis_agent."
                "query_rewriter."
                "rewrite_query_for_search",
                return_value="semantic caching method",
            ) as mock_rewrite,
            patch(
                "paper_analysis_agent."
                "semantic_search."
                "search_task_chunks_semantic",
                side_effect=search_results,
            ) as mock_search,
        ):
            with TestingSessionLocal() as db:
                matches = (
                    paper_analysis_agent
                    .collect_analysis_matches(
                        db=db,
                        task_id=1,
                        question="论文使用了什么方法？",
                    )
                )

        self.assertEqual(len(matches), 2)
        self.assertIs(matches[0][0], first_chunk)
        self.assertEqual(matches[0][2], 0.85)
        self.assertIs(matches[1][0], second_chunk)

        mock_rewrite.assert_called_once_with(
            "论文使用了什么方法？"
        )
        self.assertEqual(mock_search.call_count, 5)

    def test_analyze_research_task_returns_structure(self):
        paper = SimpleNamespace(
            id=1,
            title="语义缓存论文",
        )
        chunk = SimpleNamespace(
            id=10,
            page_number=3,
            text=(
                "The system uses semantic caching "
                "to reduce LLM serving cost."
            ),
        )
        matches = [
            (chunk, paper, 0.91),
        ]

        model_response = """{
  "summary": "论文研究语义缓存[资料1]。",
  "methods": ["使用语义缓存降低服务成本[资料1]。"],
  "experiments": ["当前资料没有提供完整实验信息。"],
  "conclusions": ["语义缓存可以减少LLM调用[资料1]。"],
  "limitations": ["当前资料不足以分析完整局限性。"]
}"""

        with (
            patch(
                "paper_analysis_agent."
                "collect_analysis_matches",
                return_value=matches,
            ) as mock_collect,
            patch(
                "paper_analysis_agent.generate_text",
                return_value=model_response,
            ) as mock_generate,
        ):
            with TestingSessionLocal() as db:
                result = (
                    paper_analysis_agent
                    .analyze_research_task(
                        db=db,
                        task_id=1,
                        question=(
                            "语义缓存如何降低"
                            "大模型服务成本？"
                        ),
                    )
                )

        self.assertEqual(
            result.question,
            "语义缓存如何降低大模型服务成本？",
        )
        self.assertEqual(
            result.summary,
            "论文研究语义缓存[资料1]。",
        )
        self.assertEqual(
            result.methods,
            ["使用语义缓存降低服务成本[资料1]。"],
        )
        self.assertEqual(
            result.experiments,
            ["当前资料没有提供完整实验信息。"],
        )
        self.assertEqual(
            result.conclusions,
            ["语义缓存可以减少LLM调用[资料1]。"],
        )
        self.assertEqual(
            result.limitations,
            ["当前资料不足以分析完整局限性。"],
        )
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0].paper_id, 1)
        self.assertEqual(result.sources[0].chunk_id, 10)
        self.assertEqual(result.sources[0].page_number, 3)
        self.assertEqual(result.sources[0].score, 0.91)

        mock_collect.assert_called_once()
        collect_arguments = mock_collect.call_args.kwargs
        self.assertEqual(collect_arguments["task_id"], 1)

        mock_generate.assert_called_once()
        generate_arguments = mock_generate.call_args.kwargs
        self.assertIn(
            "论文分析Agent",
            generate_arguments["system_prompt"],
        )
        self.assertIn(
            "[资料1]",
            generate_arguments["user_prompt"],
        )
        self.assertIn(
            "semantic caching",
            generate_arguments["user_prompt"],
        )


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
                "rag_service.query_rewriter."
                "rewrite_query_for_search",
                return_value="RAG chunking methods",
            ) as mock_rewrite,
            patch(
                "rag_service.semantic_search."
                "search_task_chunks_semantic",
                return_value=matches,
            ) as mock_search,
            patch(
                "rag_service.generate_text",
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
            "api.rag_service.answer_research_question",
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


class TestResearchTaskAnalysisAPI(unittest.TestCase):
    """验证论文分析接口的成功响应和错误处理。"""

    def setUp(self):
        # 每个测试开始前，删除测试数据库中的全部表。
        Base.metadata.drop_all(bind=test_engine)

        # 根据 model.py 中的模型重新创建表，保证测试相互独立。
        Base.metadata.create_all(bind=test_engine)

    def _create_task(self):
        """创建一个测试研究任务，并返回它的编号。"""

        # 使用测试客户端调用创建研究任务的真实接口。
        response = client.post(
            "/research-tasks",
            json={
                "title": "语义缓存论文分析",
                "question": "语义缓存如何降低大模型服务成本？",
            },
        )

        # 确认任务创建成功；201 表示新资源创建成功。
        self.assertEqual(response.status_code, 201)

        # 将响应转换成字典，再读取任务编号并返回。
        return response.json()["id"]

    def test_analysis_returns_structured_result(self):
        # 创建测试任务，取得数据库生成的编号。
        task_id = self._create_task()

        # 准备分析 Agent 应当返回的模拟结果。
        fake_result = paper_analysis_agent.PaperAnalysisResult(
            question="语义缓存如何降低大模型服务成本？",
            summary="论文研究语义缓存[资料1]。",
            methods=["通过复用已有响应降低调用成本[资料1]。"],
            experiments=["当前资料未提供完整实验信息。"],
            conclusions=["语义缓存能够减少重复调用[资料1]。"],
            limitations=["当前资料不足以分析完整局限性。"],
            sources=[
                paper_analysis_agent.AnalysisSource(
                    paper_id=3,
                    paper_title="Semantic Caching",
                    chunk_id=14,
                    page_number=1,
                    score=0.53,
                ),
            ],
        )

        # 暂时替换接口中调用的分析函数，避免真实调用模型。
        with patch(
            "api.paper_analysis_agent.analyze_research_task",
            return_value=fake_result,
        ) as mock_analyze:
            # 调用真实的分析接口；它使用任务中保存的问题。
            response = client.post(
                f"/research-tasks/{task_id}/analysis"
            )

        # 确认接口正常返回。
        self.assertEqual(response.status_code, 200)

        # 检查整个 JSON，确认所有字段和资料来源都正确转换。
        self.assertEqual(
            response.json(),
            {
                "task_id": task_id,
                "question": "语义缓存如何降低大模型服务成本？",
                "summary": "论文研究语义缓存[资料1]。",
                "methods": [
                    "通过复用已有响应降低调用成本[资料1]。",
                ],
                "experiments": [
                    "当前资料未提供完整实验信息。",
                ],
                "conclusions": [
                    "语义缓存能够减少重复调用[资料1]。",
                ],
                "limitations": [
                    "当前资料不足以分析完整局限性。",
                ],
                "evidence_notes": [],
                "entities": [],
                "sources": [
                    {
                        "paper_id": 3,
                        "paper_title": "Semantic Caching",
                        "chunk_id": 14,
                        "page_number": 1,
                        "score": 0.53,
                    },
                ],
            },
        )

        # 确认接口只调用了一次分析 Agent。
        mock_analyze.assert_called_once()

        # 读取接口传给分析 Agent 的关键字参数。
        arguments = mock_analyze.call_args.kwargs

        # 确认传入的是当前任务编号。
        self.assertEqual(arguments["task_id"], task_id)

        # 确认分析问题来自数据库中的任务记录。
        self.assertEqual(
            arguments["question"],
            "语义缓存如何降低大模型服务成本？",
        )

        # 使用新会话读取，确认 POST 已经真正提交结果。
        with TestingSessionLocal() as db:
            saved_task = db.get(ResearchTask, task_id)
            self.assertIsNotNone(saved_task)
            self.assertEqual(saved_task.status, "completed")
            saved_analysis = json.loads(saved_task.result)
            self.assertEqual(saved_analysis, response.json())

        # 读取已有分析时不应再次调用 Agent。
        with patch(
            "api.paper_analysis_agent.analyze_research_task"
        ) as mock_read_analyze:
            read_response = client.get(
                f"/research-tasks/{task_id}/analysis",
                follow_redirects=False,
            )

        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(read_response.json(), response.json())
        mock_read_analyze.assert_not_called()

    def test_read_analysis_returns_404_when_not_saved(self):
        task_id = self._create_task()

        with patch(
            "api.paper_analysis_agent.analyze_research_task"
        ) as mock_analyze:
            response = client.get(
                f"/research-tasks/{task_id}/analysis",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"],
            "这个研究任务还没有保存分析结果",
        )
        mock_analyze.assert_not_called()

    def test_read_analysis_rejects_plain_text_result(self):
        task_id = self._create_task()

        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, task_id)
            task.result = "这是以前保存的普通任务结果"
            db.commit()

        response = client.get(
            f"/research-tasks/{task_id}/analysis",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            "已保存内容不是结构化分析结果，请重新执行分析",
        )

    def test_analysis_returns_404_for_missing_task(self):
        # 设置模拟函数，用于检查任务不存在时是否调用了 Agent。
        with patch(
            "api.paper_analysis_agent.analyze_research_task"
        ) as mock_analyze:
            # 此测试没有创建任务，因此任务 999 不存在。
            response = client.post(
                "/research-tasks/999/analysis"
            )

        # 任务不存在，应返回 404。
        self.assertEqual(response.status_code, 404)

        # 检查错误说明。
        self.assertEqual(
            response.json()["detail"],
            "没有找到编号为 999 的研究任务",
        )

        # 不存在的任务不应该触发任何分析或模型调用。
        mock_analyze.assert_not_called()

    def test_analysis_returns_409_when_no_sources(self):
        # 创建存在的任务，确保代码能够进入分析阶段。
        task_id = self._create_task()

        # 模拟分析函数发现没有可用资料，主动抛出 ValueError。
        with patch(
            "api.paper_analysis_agent.analyze_research_task",
            side_effect=ValueError(
                "没有找到可用于论文分析的资料"
            ),
        ):
            response = client.post(
                f"/research-tasks/{task_id}/analysis"
            )

        # 验证 API 将 ValueError 转换成了 HTTP 409。
        self.assertEqual(response.status_code, 409)

        # 验证原始错误信息被正确返回。
        self.assertEqual(
            response.json()["detail"],
            "没有找到可用于论文分析的资料",
        )

    def test_analysis_returns_503_when_ollama_unavailable(self):
        # 创建一个测试任务。
        task_id = self._create_task()

        # 模拟调用 Ollama 时发生网络连接错误。
        with patch(
            "api.paper_analysis_agent.analyze_research_task",
            side_effect=URLError("连接被拒绝"),
        ):
            response = client.post(
                f"/research-tasks/{task_id}/analysis"
            )

        # 验证网络异常被转换为 HTTP 503。
        self.assertEqual(response.status_code, 503)

        # 验证接口返回的是你在 api.py 中定义的说明。
        self.assertEqual(
            response.json()["detail"],
            "无法连接向量模型，请确认Ollama已经启动",
        )

    def test_analysis_returns_502_when_model_result_invalid(self):
        # 创建一个测试任务。
        task_id = self._create_task()

        # 模拟 DeepSeek 返回内容无法解析为正确 JSON。
        with patch(
            "api.paper_analysis_agent.analyze_research_task",
            side_effect=RuntimeError(
                "论文分析Agent没有返回正确的JSON"
            ),
        ):
            response = client.post(
                f"/research-tasks/{task_id}/analysis"
            )

        # 验证运行错误被转换为 HTTP 502。
        self.assertEqual(response.status_code, 502)

        # 验证具体错误信息被保留。
        self.assertEqual(
            response.json()["detail"],
            "论文分析Agent没有返回正确的JSON",
        )

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


class TestReferenceDetection(unittest.TestCase):
    """确保文内引用不会触发参考文献降权。"""

    def test_body_with_citations_keeps_its_rerank_score(self):
        body = "Our method reduces serving cost. We compare [1], [2], and [3]."
        self.assertFalse(semantic_search.looks_like_reference_chunk(body))
        self.assertAlmostEqual(
            semantic_search.calculate_rerank_score("serving cost", body, 0.8),
            0.8 * semantic_search.SEMANTIC_WEIGHT + semantic_search.KEYWORD_WEIGHT,
        )

    def test_body_mentions_do_not_mark_whole_page_as_references(self):
        body = "Our method references prior work by Smith et al. and Lee et al."
        self.assertFalse(semantic_search.looks_like_reference_chunk(body))
        self.assertEqual(semantic_search.find_reference_pages([
            (SimpleNamespace(text=body, page_number=2), SimpleNamespace(id=3), None),
        ]), set())

    def test_reference_headings_and_numbered_entries_are_detected(self):
        for text in (
            "REFERENCES\n[1] First paper",
            "7 References\n[1] First paper",
            "参考文献\n[1] 第一篇论文",
            "[1] Smith. First paper. 2022.\n[2] Lee. Second paper. 2023.\n[3] Li. Third paper. 2024.",
        ):
            with self.subTest(text=text):
                self.assertTrue(semantic_search.looks_like_reference_chunk(text))


class TestReviewAgent(unittest.TestCase):
    """检查审校解析、真实证据提示词和逐条处理逻辑；模拟模型请求。"""

    def setUp(self):
        self.claim = ReviewClaim(
            field="conclusions", claim="所有数据集上成本都下降。[资料3]",
            source_numbers=[3], evidence=[ReviewEvidence(
                source_number=3, paper_title="测试论文", chunk_id=14,
                page_number=5, text="在数据集A上，服务成本降低了20%。",
            )],
        )
        self.data = dict(verdict="partially_supported", reason="只支持数据集A。", source_numbers=[3])

    def test_parse_valid_code_block_preserves_original_claim(self):
        data = dict(self.data, field="methods", claim="模型试图改写的陈述")
        result = review_agent.parse_review_response(
            "```json\n" + json.dumps(data) + "\n```", self.claim,
        )
        self.assertEqual(result.field, self.claim.field)
        self.assertEqual(result.claim, self.claim.claim)
        self.assertEqual(result.verdict, "partially_supported")
        self.assertEqual(result.source_numbers, [3])

    def test_reject_invalid_model_payloads(self):
        cases = [
            ("not json", "正确的JSON"),
            ("[]", "不是JSON对象"),
            (json.dumps(dict(self.data, verdict="maybe")), "字段或类型"),
            (json.dumps(dict(self.data, source_numbers=["3"])), "字段或类型"),
            (json.dumps(dict(self.data, source_numbers=[True])), "字段或类型"),
            (json.dumps(dict(self.data, reason="  ")), "审校理由"),
            (json.dumps(dict(self.data, source_numbers=[])), "没有提供资料编号"),
            (json.dumps({"verdict": "supported"}), "字段或类型"),
        ]
        for text, message in cases:
            with self.subTest(text=text):
                with self.assertRaisesRegex(RuntimeError, message):
                    review_agent.parse_review_response(text, self.claim)

    def test_reject_source_not_provided(self):
        with self.assertRaisesRegex(RuntimeError, "未提供的资料"):
            review_agent.parse_review_response(
                json.dumps(dict(self.data, source_numbers=[1])), self.claim,
            )

    def test_no_evidence_does_not_call_model(self):
        claim = ReviewClaim("limitations", "当前资料不足。", [], [])
        with patch("review_agent.generate_text") as generate:
            result = review_agent.review_claim(claim)
        self.assertEqual(result.verdict, "insufficient_evidence")
        self.assertEqual(result.source_numbers, [])
        generate.assert_not_called()

    def test_model_receives_original_evidence_and_number(self):
        with patch("review_agent.generate_text", return_value=json.dumps(self.data)) as generate:
            result = review_agent.review_claim(self.claim)
        generate.assert_called_once()
        prompt = generate.call_args.kwargs["user_prompt"]
        self.assertIn(self.claim.claim, prompt)
        self.assertIn("[资料3]", prompt)
        self.assertIn("页码：5", prompt)
        self.assertIn(self.claim.evidence[0].text, prompt)
        self.assertEqual(result.verdict, "partially_supported")

    def test_task_reviews_all_claims_in_order(self):
        second = ReviewClaim("limitations", "当前资料不足。", [], [])
        analysis = SimpleNamespace(task_id=7)
        db = Mock()
        with (
            patch("review_agent.prepare_review_claims", return_value=[self.claim, second]) as prepare,
            patch("review_agent.generate_text", return_value=json.dumps(self.data)) as generate,
        ):
            result = review_agent.review_research_task(db, analysis)
        prepare.assert_called_once_with(db=db, analysis=analysis)
        generate.assert_called_once()
        self.assertEqual(result.task_id, 7)
        self.assertEqual([i.field for i in result.items], ["conclusions", "limitations"])
        self.assertEqual(result.items[1].verdict, "insufficient_evidence")

    def test_model_failure_is_propagated(self):
        with patch("review_agent.generate_text", side_effect=RuntimeError("模型连接失败")):
            with self.assertRaisesRegex(RuntimeError, "模型连接失败"):
                review_agent.review_claim(self.claim)


class TestResearchTaskReviewAPI(unittest.TestCase):
    """使用SQLite测试完整取证和审校链路，仅替换DeepSeek请求。"""

    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        with TestingSessionLocal() as db:
            paper = Paper(title="测试论文", author="测试作者", year=2025, notes="")
            task = ResearchTask(title="审校测试", question="是否降低成本？")
            db.add_all([paper, task])
            db.flush()
            document = PaperDocument(
                paper_id=paper.id, original_name="test.pdf", stored_name="test.pdf",
                file_path="/unused/test.pdf", content_type="application/pdf", file_size=10,
            )
            db.add(document)
            db.flush()
            chunk = PaperChunk(document_id=document.id, page_number=5, chunk_index=0,
                               text="在数据集A上，服务成本降低了20%。", char_count=23)
            db.add(chunk)
            db.flush()
            self.task_id = task.id
            self.chunk_id = chunk.id
            analysis = ResearchTaskAnalysisResponse(
                task_id=task.id, question=task.question,
                summary="数据集A上成本下降。[资料1]", methods=[], experiments=[],
                conclusions=["所有数据集上成本下降。[资料1]"], limitations=["当前资料不足。"],
                sources=[AnalysisSourceResponse(
                    paper_id=paper.id, paper_title=paper.title, chunk_id=chunk.id,
                    page_number=5, score=0.9,
                )],
            )
            self.saved_result = analysis.model_dump_json()
            task.result = self.saved_result
            task.status = "completed"
            db.commit()

    def _set_result(self, result):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            task.result = result
            db.commit()

    def test_review_success_uses_database_evidence_and_preserves_analysis(self):
        answers = [
            json.dumps(dict(verdict="supported", reason="原文明确说明数据集A。", source_numbers=[1])),
            json.dumps(dict(verdict="partially_supported", reason="不能推广到所有数据集。", source_numbers=[1])),
        ]
        with patch("review_agent.generate_text", side_effect=answers) as generate:
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["task_id"], self.task_id)
        self.assertEqual([i["verdict"] for i in data["items"]],
                         ["supported", "partially_supported", "insufficient_evidence"])
        self.assertEqual(generate.call_count, 2)
        for call in generate.call_args_list:
            self.assertIn("在数据集A上，服务成本降低了20%。", call.kwargs["user_prompt"])
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual(task.result, self.saved_result)
            self.assertEqual(task.status, "completed")
            self.assertEqual(json.loads(task.review_result), data)

    def test_missing_task_returns_404(self):
        with patch("review_agent.generate_text") as generate:
            response = client.post("/research-tasks/99999/review")
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()

    def test_missing_analysis_returns_404(self):
        self._set_result(None)
        with patch("review_agent.generate_text") as generate:
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()

    def test_invalid_saved_analysis_returns_409(self):
        self._set_result("旧的纯文本结果")
        with patch("review_agent.generate_text") as generate:
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 409)
        generate.assert_not_called()

    def test_wrong_task_id_in_saved_analysis_returns_409(self):
        data = json.loads(self.saved_result)
        data["task_id"] = 99999
        self._set_result(json.dumps(data))
        with patch("review_agent.generate_text") as generate:
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 409)
        generate.assert_not_called()

    def test_deleted_chunk_returns_409(self):
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id))
            db.commit()
        with patch("review_agent.generate_text") as generate:
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 409)
        self.assertIn("文本块已经不存在", response.json()["detail"])
        generate.assert_not_called()

    def test_model_connection_failure_returns_502(self):
        with patch("review_agent.generate_text", side_effect=RuntimeError("模型连接失败")):
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "模型连接失败")

    def test_invalid_model_json_returns_502(self):
        with patch("review_agent.generate_text", return_value="invalid JSON"):
            response = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 502)
        self.assertIn("正确的JSON", response.json()["detail"])


    def _save_review_fixture(self):
        review = {
            "task_id": self.task_id,
            "items": [{"field": "summary", "claim": "数据集A上成本下降。[资料1]",
                       "verdict": "supported", "reason": "原文支持。", "source_numbers": [1]}],
        }
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).review_result = json.dumps(review, ensure_ascii=False)
            db.commit()
        return review

    def test_get_saved_review_returns_without_model_call(self):
        expected = self._save_review_fixture()
        with patch("review_agent.generate_text") as generate:
            response = client.get(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        generate.assert_not_called()

    def test_get_review_returns_404_when_not_saved(self):
        response = client.get(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 404)
        self.assertIn("还没有保存审校结果", response.json()["detail"])

    def test_get_review_returns_404_for_missing_task(self):
        self.assertEqual(client.get("/research-tasks/99999/review").status_code, 404)

    def test_get_review_rejects_corrupt_result(self):
        for value in ("not JSON", '{"task_id":1}', '{"task_id":1,"items":[{}]}'):
            with self.subTest(value=value):
                with TestingSessionLocal() as db:
                    db.get(ResearchTask, self.task_id).review_result = value
                    db.commit()
                response = client.get(f"/research-tasks/{self.task_id}/review")
                self.assertEqual(response.status_code, 409)
                self.assertIn("格式不正确", response.json()["detail"])

    def test_get_review_rejects_wrong_task_id(self):
        review = self._save_review_fixture()
        review["task_id"] = 99999
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).review_result = json.dumps(review)
            db.commit()
        response = client.get(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(response.status_code, 409)
        self.assertIn("任务编号不匹配", response.json()["detail"])

    def test_new_analysis_clears_previous_review(self):
        self._save_review_fixture()
        analysis = ResearchTaskAnalysisResponse.model_validate_json(self.saved_result)
        analysis.summary = "新的分析。[资料1]"
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            import task_crud
            task_crud.save_task_analysis(db, task, analysis)
        self.assertEqual(client.get(f"/research-tasks/{self.task_id}/review").status_code, 404)
        response = client.get(f"/research-tasks/{self.task_id}/analysis")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"], analysis.summary)

    def test_status_update_clears_review_only_when_result_changes(self):
        self._save_review_fixture()
        same = client.patch(f"/research-tasks/{self.task_id}/status", json={
            "status": "completed", "result": self.saved_result,
        })
        self.assertEqual(same.status_code, 200, same.text)
        self.assertEqual(client.get(f"/research-tasks/{self.task_id}/review").status_code, 200)
        changed = client.patch(f"/research-tasks/{self.task_id}/status", json={
            "status": "pending", "result": None,
        })
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(client.get(f"/research-tasks/{self.task_id}/review").status_code, 404)


class TestRevisionAgent(unittest.TestCase):
    """验证修订解析与批量处理，包括全部校验后才调用模型。"""

    def setUp(self):
        self.claim = ReviewClaim("conclusions", "所有数据集上成本下降。[资料3]", [3], [
            ReviewEvidence(3, "测试论文", 14, 5, "在数据集A上成本下降20%。"),
        ])
        self.item = ReviewItemResponse(field=self.claim.field, claim=self.claim.claim,
                                      verdict="partially_supported", reason="仅支持数据集A。", source_numbers=[3])
        self.answer = json.dumps(dict(revised_claim="数据集A上成本下降20%。[资料3]", reason="缩小结论范围。"))

    def test_valid_response_preserves_original_and_extracts_citations(self):
        result = revision_agent.parse_revision_response("```json\n" + self.answer + "\n```", self.claim, 3)
        self.assertEqual(result.original_claim, self.claim.claim)
        self.assertEqual(result.field, "conclusions")
        self.assertEqual(result.source_numbers, [3])

    def test_reject_invalid_model_payloads(self):
        cases = ["not JSON", "[]", "{}", json.dumps(dict(revised_claim=123, reason="原因")),
                 json.dumps(dict(revised_claim=" ", reason="原因")),
                 json.dumps(dict(revised_claim="修改后", reason=" "))]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    revision_agent.parse_revision_response(value, self.claim, 3)

    def test_reject_malformed_out_of_range_and_unprovided_citations(self):
        for citation in ("[资料3", "[资料四]", "[资料4]", "[资料1]"):
            with self.subTest(citation=citation):
                with self.assertRaises(RuntimeError):
                    revision_agent.parse_revision_response(
                        json.dumps(dict(revised_claim="修改后" + citation, reason="原因")), self.claim, 3,
                    )

    def test_evidence_insufficient_text_can_omit_citations(self):
        result = revision_agent.parse_revision_response(json.dumps(dict(
            revised_claim="当前引用原文不足以确认原陈述。", reason="原文不足。")), self.claim, 3)
        self.assertEqual(result.source_numbers, [])

    def test_single_revision_sends_claim_review_and_evidence(self):
        with patch("revision_agent.generate_text", return_value=self.answer) as generate:
            result = revision_agent.revise_claim(self.claim, self.item, 3)
        generate.assert_called_once()
        prompt = generate.call_args.kwargs["user_prompt"]
        for fragment in (self.claim.claim, self.item.reason, self.claim.evidence[0].text, "[资料3]"):
            self.assertIn(fragment, prompt)
        self.assertEqual(result.source_numbers, [3])

    def test_single_revision_rejects_ineligible_input_before_model_call(self):
        cases = [
            (self.claim, self.item.model_copy(update={"claim": "不同陈述"})),
            (self.claim, self.item.model_copy(update={"field": "methods"})),
            (self.claim, self.item.model_copy(update={"verdict": "supported"})),
            (ReviewClaim(self.claim.field, self.claim.claim, [3], []), self.item),
        ]
        with patch("revision_agent.generate_text") as generate:
            for claim, item in cases:
                with self.subTest(item=item):
                    with self.assertRaises(ValueError):
                        revision_agent.revise_claim(claim, item, 3)
        generate.assert_not_called()

    def _batch_fixture(self):
        supported = ReviewClaim("summary", "概述。[资料3]", [3], self.claim.evidence)
        missing = ReviewClaim("limitations", "当前资料不足。", [], [])
        claims = [supported, self.claim, missing]
        items = [
            ReviewItemResponse(field=supported.field, claim=supported.claim, verdict="supported", reason="支持", source_numbers=[3]),
            self.item,
            ReviewItemResponse(field=missing.field, claim=missing.claim, verdict="insufficient_evidence", reason="无证据", source_numbers=[]),
        ]
        return claims, ResearchTaskReviewResponse(task_id=1, items=items)

    def test_batch_skips_supported_and_reports_missing_evidence(self):
        claims, review = self._batch_fixture()
        with (
            patch("revision_agent.prepare_review_claims", return_value=claims),
            patch("revision_agent.generate_text", return_value=self.answer) as generate,
        ):
            result = revision_agent.revise_research_task(Mock(), SimpleNamespace(task_id=1, sources=[None]*3), review)
        generate.assert_called_once()
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].original_claim, self.claim.claim)
        self.assertEqual(result.skipped_items, [review.items[2]])

    def test_batch_checks_every_item_before_calling_model(self):
        for change in ("claim", "source_numbers"):
            claims, review = self._batch_fixture()
            if change == "claim":
                review.items[2] = review.items[2].model_copy(update={"claim": "已过期的陈述"})
            else:
                review.items[1] = review.items[1].model_copy(update={"source_numbers": [1]})
            with (
                self.subTest(change=change),
                patch("revision_agent.prepare_review_claims", return_value=claims),
                patch("revision_agent.generate_text", return_value=self.answer) as generate,
            ):
                with self.assertRaises(ValueError):
                    revision_agent.revise_research_task(Mock(), SimpleNamespace(task_id=1, sources=[None]*3), review)
                generate.assert_not_called()

    def test_batch_rejects_task_or_item_count_mismatch(self):
        claims, review = self._batch_fixture()
        for task_id, items in ((2, review.items), (1, review.items[:-1])):
            with (
                self.subTest(task_id=task_id),
                patch("revision_agent.prepare_review_claims", return_value=claims),
                patch("revision_agent.generate_text") as generate,
            ):
                with self.assertRaises(ValueError):
                    revision_agent.revise_research_task(Mock(), SimpleNamespace(task_id=task_id, sources=[None]*3),
                        ResearchTaskReviewResponse(task_id=1, items=items))
                generate.assert_not_called()

    def test_empty_batch_returns_empty_response(self):
        with patch("revision_agent.prepare_review_claims", return_value=[]):
            result = revision_agent.revise_research_task(Mock(), SimpleNamespace(task_id=1, sources=[]),
                                                        ResearchTaskReviewResponse(task_id=1, items=[]))
        self.assertIsNotNone(result)
        self.assertEqual(result.items, [])
        self.assertEqual(result.skipped_items, [])


class TestResearchTaskRevisionAPI(unittest.TestCase):
    """真实SQLite取证与接口调用，仅模拟DeepSeek返回。"""

    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        with TestingSessionLocal() as db:
            paper = Paper(title="修订测试论文", author="作者", year=2025, notes="")
            task = ResearchTask(title="修订测试", question="如何降低成本？", status="completed")
            db.add_all([paper, task])
            db.flush()
            document = PaperDocument(paper_id=paper.id, original_name="test.pdf", stored_name="test.pdf",
                                     file_path="/unused/test.pdf", content_type="application/pdf", file_size=10)
            db.add(document)
            db.flush()
            chunk = PaperChunk(document_id=document.id, page_number=5, chunk_index=0, text="数据集A成本降低20%。", char_count=13)
            db.add(chunk)
            db.flush()
            self.task_id, self.chunk_id = task.id, chunk.id
            analysis = ResearchTaskAnalysisResponse(task_id=task.id, question=task.question,
                summary="数据集A上成本降低。[资料1]", methods=[], experiments=[],
                conclusions=["所有数据集成本降低。[资料1]"], limitations=["当前资料不足。"],
                sources=[AnalysisSourceResponse(paper_id=paper.id, paper_title=paper.title, chunk_id=chunk.id, page_number=5, score=0.9)])
            review = ResearchTaskReviewResponse(task_id=task.id, items=[
                ReviewItemResponse(field="summary", claim=analysis.summary, verdict="supported", reason="支持", source_numbers=[1]),
                ReviewItemResponse(field="conclusions", claim=analysis.conclusions[0], verdict="partially_supported", reason="只能支持数据集A。", source_numbers=[1]),
                ReviewItemResponse(field="limitations", claim=analysis.limitations[0], verdict="insufficient_evidence", reason="无原文", source_numbers=[]),
            ])
            task.result, task.review_result = analysis.model_dump_json(), review.model_dump_json()
            self.original = (task.result, task.review_result, task.status)
            db.commit()
        self.answer = json.dumps(dict(revised_claim="数据集A成本降低20%。[资料1]", reason="限定到数据集A。"))

    def test_success_preserves_analysis_and_review(self):
        with patch("revision_agent.generate_text", return_value=self.answer) as generate:
            response = client.post(f"/research-tasks/{self.task_id}/revisions")
        self.assertEqual(response.status_code, 200, response.text)
        generate.assert_called_once()
        self.assertIn("数据集A成本降低20%。", generate.call_args.kwargs["user_prompt"])
        data = response.json()
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["source_numbers"], [1])
        self.assertEqual(len(data["skipped_items"]), 1)
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual((task.result, task.review_result, task.status), self.original)

    def test_missing_task_returns_404(self):
        with patch("revision_agent.generate_text") as generate:
            response = client.post("/research-tasks/99999/revisions")
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()

    def test_missing_saved_inputs_return_404(self):
        for field in ("result", "review_result"):
            with self.subTest(field=field):
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    task.result, task.review_result = self.original[:2]
                    setattr(task, field, None)
                    db.commit()
                with patch("revision_agent.generate_text") as generate:
                    response = client.post(f"/research-tasks/{self.task_id}/revisions")
                self.assertEqual(response.status_code, 404)
                generate.assert_not_called()

    def test_stale_later_review_item_returns_409_without_model_call(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            review = json.loads(task.review_result)
            review["items"][2]["claim"] = "旧分析中的陈述"
            task.review_result = json.dumps(review)
            db.commit()
        with patch("revision_agent.generate_text", return_value=self.answer) as generate:
            response = client.post(f"/research-tasks/{self.task_id}/revisions")
        self.assertEqual(response.status_code, 409)
        generate.assert_not_called()

    def test_deleted_evidence_returns_409(self):
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id))
            db.commit()
        with patch("revision_agent.generate_text") as generate:
            response = client.post(f"/research-tasks/{self.task_id}/revisions")
        self.assertEqual(response.status_code, 409)
        generate.assert_not_called()

    def test_model_errors_return_502_and_preserve_saved_inputs(self):
        for answer in (RuntimeError("模型连接失败"), "not JSON", json.dumps(dict(revised_claim="错误引用。[资料2]", reason="原因"))):
            with self.subTest(answer=answer):
                options = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
                with patch("revision_agent.generate_text", **options):
                    response = client.post(f"/research-tasks/{self.task_id}/revisions")
                self.assertEqual(response.status_code, 502)
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    self.assertEqual((task.result, task.review_result, task.status), self.original)


    def test_combined_revision_and_recheck_preserves_saved_inputs(self):
        checked = json.dumps(dict(verdict="supported", reason="修改后的范围与原文一致。", source_numbers=[1]))
        with (
            patch("revision_agent.generate_text", side_effect=[self.answer, json.dumps(dict(verdict="appropriate", reason="适合结论字段。", suggested_field=None))]) as revise,
            patch("review_agent.generate_text", return_value=checked) as recheck,
        ):
            response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(revise.call_count, 2)
        recheck.assert_called_once()
        data = response.json()
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(len(data["skipped_items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["review"]["claim"], item["revision"]["revised_claim"])
        self.assertEqual(item["review"]["verdict"], "supported")
        self.assertEqual(item["field_check"]["verdict"], "appropriate")
        self.assertIn(item["revision"]["revised_claim"], recheck.call_args.kwargs["user_prompt"])
        self.assertIn("数据集A成本降低20%。", recheck.call_args.kwargs["user_prompt"])
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual((task.result, task.review_result, task.status), self.original)

    def test_combined_returns_remaining_partial_support_without_retry_loop(self):
        checked = json.dumps(dict(verdict="partially_supported", reason="仍有部分表述无依据。", source_numbers=[1]))
        with (
            patch("revision_agent.generate_text", side_effect=[self.answer, json.dumps(dict(verdict="appropriate", reason="适合结论字段。", suggested_field=None))]) as revise,
            patch("review_agent.generate_text", return_value=checked) as recheck,
        ):
            response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["review"]["verdict"], "partially_supported")
        self.assertEqual(revise.call_count, 2)
        recheck.assert_called_once()

    def test_combined_recheck_errors_return_502_and_preserve_saved_inputs(self):
        for answer in (RuntimeError("再次审校连接失败"), "invalid JSON",
                       json.dumps(dict(verdict="supported", reason="错误引用", source_numbers=[2]))):
            with self.subTest(answer=answer):
                options = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
                with (
                    patch("revision_agent.generate_text", return_value=self.answer),
                    patch("review_agent.generate_text", **options),
                ):
                    response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
                self.assertEqual(response.status_code, 502)
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    self.assertEqual((task.result, task.review_result, task.status), self.original)

    def test_combined_rejects_stale_review_before_any_model_call(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            data = json.loads(task.review_result)
            data["items"][2]["claim"] = "旧的陈述"
            task.review_result = json.dumps(data)
            db.commit()
        with patch("revision_agent.generate_text") as revise, patch("review_agent.generate_text") as recheck:
            response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code, 409)
        revise.assert_not_called()
        recheck.assert_not_called()

    def test_combined_no_revision_needed_does_not_call_models(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            data = json.loads(task.review_result)
            data["items"][1]["verdict"] = "supported"
            task.review_result = json.dumps(data)
            db.commit()
        with patch("revision_agent.generate_text") as revise, patch("review_agent.generate_text") as recheck:
            response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(len(response.json()["skipped_items"]), 1)
        revise.assert_not_called()
        recheck.assert_not_called()


    def test_supported_evidence_can_still_have_misplaced_field(self):
        checked = json.dumps(dict(verdict="supported", reason="原文支持。", source_numbers=[1]))
        category = json.dumps(dict(verdict="misplaced", reason="这是具体实验测量结果。", suggested_field="experiments"))
        with (
            patch("revision_agent.generate_text", side_effect=[self.answer, category]) as generate,
            patch("review_agent.generate_text", return_value=checked),
        ):
            response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["review"]["verdict"], "supported")
        self.assertEqual(item["field_check"]["verdict"], "misplaced")
        self.assertEqual(item["field_check"]["suggested_field"], "experiments")
        self.assertEqual(generate.call_count, 2)
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual((task.result, task.review_result, task.status), self.original)

    def test_category_failure_returns_502_without_overwriting_saved_results(self):
        checked = json.dumps(dict(verdict="supported", reason="原文支持。", source_numbers=[1]))
        for answer in ("invalid JSON", RuntimeError("分类模型连接失败")):
            with self.subTest(answer=answer):
                with (
                    patch("revision_agent.generate_text", side_effect=[self.answer, answer]),
                    patch("review_agent.generate_text", return_value=checked),
                ):
                    response = client.post(f"/research-tasks/{self.task_id}/revisions/review")
                self.assertEqual(response.status_code, 502)
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    self.assertEqual((task.result, task.review_result, task.status), self.original)


    def test_mixed_branch_splits_and_checks_each_part(self):
        mixed = json.dumps(dict(verdict="mixed", reason="含实验结果与边界说明。", suggested_field=None))
        appropriate = json.dumps(dict(verdict="appropriate", reason="分类合适。", suggested_field=None))
        supported = json.dumps(dict(verdict="supported", reason="原文支持。", source_numbers=[1]))
        insufficient = json.dumps(dict(verdict="insufficient_evidence", reason="原文不足以确认。", source_numbers=[1]))
        split_answer = json.dumps(dict(reason="分开结果和边界。", items=[
            dict(field="experiments", claim="数据集A成本下降20%。[资料1]"),
            dict(field="limitations", claim="当前原文不足以确认其他数据集的结果。[资料1]"),
        ]))
        with (
            patch("revision_agent.generate_text", side_effect=[self.answer, mixed, appropriate, appropriate]) as generate,
            patch("review_agent.generate_text", side_effect=[supported, supported, insufficient]) as check,
            patch("field_splitter.generate_text", return_value=split_answer) as splitter,
        ):
            response=client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code,200,response.text)
        item=response.json()["items"][0]
        self.assertEqual(item["field_check"]["verdict"],"mixed")
        self.assertEqual([i["item"]["field"] for i in item["split_checks"]],["experiments","limitations"])
        self.assertEqual([i["ready"] for i in item["split_checks"]],[True,False])
        self.assertEqual(item["split_result"]["source_claim"],item["revision"]["revised_claim"])
        self.assertEqual(generate.call_count,4)
        self.assertEqual(check.call_count,3)
        splitter.assert_called_once()
        with TestingSessionLocal() as db:
            task=db.get(ResearchTask,self.task_id)
            self.assertEqual((task.result,task.review_result,task.status),self.original)

    def test_invalid_split_returns_502_and_keeps_saved_inputs(self):
        mixed=json.dumps(dict(verdict="mixed",reason="混合内容",suggested_field=None))
        supported=json.dumps(dict(verdict="supported",reason="支持",source_numbers=[1]))
        with (
            patch("revision_agent.generate_text",side_effect=[self.answer,mixed]),
            patch("review_agent.generate_text",return_value=supported),
            patch("field_splitter.generate_text",return_value="invalid JSON"),
        ):
            response=client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code,502)
        with TestingSessionLocal() as db:
            task=db.get(ResearchTask,self.task_id)
            self.assertEqual((task.result,task.review_result,task.status),self.original)


    def test_evidence_note_field_move_is_preview_only(self):
        revised=json.dumps(dict(revised_claim="当前引用原文不足以确认所有数据集的结果。[资料1]",reason="限定为当前证据"))
        misplaced=json.dumps(dict(verdict="misplaced",reason="证据不足应作为备注",suggested_field="evidence_notes"))
        appropriate=json.dumps(dict(verdict="appropriate",reason="这是证据备注",suggested_field=None))
        supported=json.dumps(dict(verdict="supported",reason="仅说明当前片段的边界",source_numbers=[1]))
        with (
            patch("revision_agent.generate_text",side_effect=[revised,misplaced,appropriate]),
            patch("review_agent.generate_text",return_value=supported) as check,
        ):
            response=client.post(f"/research-tasks/{self.task_id}/revisions/review")
        self.assertEqual(response.status_code,200,response.text)
        item=response.json()["items"][0]
        self.assertEqual(item["field_check"]["suggested_field"],"evidence_notes")
        self.assertEqual(item["field_move"]["item"]["field"],"evidence_notes")
        self.assertEqual(item["field_move"]["source_field"],"conclusions")
        self.assertTrue(item["field_move"]["ready"])
        self.assertEqual(check.call_count,2)
        with TestingSessionLocal() as db:
            task=db.get(ResearchTask,self.task_id)
            self.assertEqual((task.result,task.review_result,task.status),self.original)


class TestRevisionRecheck(unittest.TestCase):
    """确保再次审校使用修订后的文本及其实际引用的原文。"""

    def setUp(self):
        from schemas import AnalysisRevisionResponse
        self.claim = ReviewClaim("conclusions", "所有数据集成本下降。[资料3][资料5]", [3, 5], [
            ReviewEvidence(3, "论文", 14, 5, "数据集A成本下降20%。"),
            ReviewEvidence(5, "论文", 20, 6, "不应提供给本次审校的未引用段落。"),
        ])
        self.revision = AnalysisRevisionResponse(field="conclusions", original_claim=self.claim.claim,
            revised_claim="数据集A成本下降20%。[资料3]", reason="限定范围", source_numbers=[3])

    def test_recheck_uses_revised_text_and_only_its_cited_evidence(self):
        answer = json.dumps(dict(verdict="supported", reason="原文支持", source_numbers=[3]))
        with patch("review_agent.generate_text", return_value=answer) as generate:
            result = revision_agent.review_revision(self.claim, self.revision, 5)
        generate.assert_called_once()
        prompt = generate.call_args.kwargs["user_prompt"]
        self.assertIn(self.revision.revised_claim, prompt)
        self.assertIn("数据集A成本下降20%。", prompt)
        self.assertNotIn("不应提供给本次审校", prompt)
        self.assertNotIn("[资料5]", prompt)
        self.assertEqual(result.claim, self.revision.revised_claim)
        self.assertEqual(result.source_numbers, [3])

    def test_recheck_rejects_invalid_revision_before_model_call(self):
        cases = [
            {"field": "methods"}, {"original_claim": "另一条陈述"}, {"revised_claim": " "},
            {"source_numbers": [5]},
            {"revised_claim": "修改后[资料3", "source_numbers": [3]},
            {"revised_claim": "修改后[资料6]", "source_numbers": [6]},
            {"revised_claim": "修改后[资料1]", "source_numbers": [1]},
        ]
        with patch("review_agent.generate_text") as generate:
            for update in cases:
                with self.subTest(update=update):
                    with self.assertRaises(ValueError):
                        revision_agent.review_revision(self.claim, self.revision.model_copy(update=update), 5)
        generate.assert_not_called()

    def test_recheck_requires_actual_evidence_for_cited_number(self):
        claim = ReviewClaim(self.claim.field, self.claim.claim, [3, 5], [])
        with patch("review_agent.generate_text") as generate:
            with self.assertRaisesRegex(ValueError, "没有找到资料3"):
                revision_agent.review_revision(claim, self.revision, 5)
        generate.assert_not_called()

    def test_revision_without_citations_returns_insufficient_evidence_without_model_call(self):
        revision = self.revision.model_copy(update={"revised_claim": "当前原文不足以确认。", "source_numbers": []})
        with patch("review_agent.generate_text") as generate:
            result = revision_agent.review_revision(self.claim, revision, 5)
        self.assertEqual(result.verdict, "insufficient_evidence")
        self.assertEqual(result.claim, revision.revised_claim)
        generate.assert_not_called()

    def test_combined_returns_empty_response_when_no_drafts(self):
        from schemas import ResearchTaskRevisionResponse
        draft = ResearchTaskRevisionResponse(task_id=1, items=[], skipped_items=[])
        with (
            patch("revision_agent.revise_research_task", return_value=draft),
            patch("revision_agent.prepare_review_claims", return_value=[]),
            patch("review_agent.generate_text") as generate,
        ):
            result = revision_agent.revise_and_review_research_task(Mock(), SimpleNamespace(task_id=1, sources=[]), Mock())
        self.assertEqual(result.task_id, 1)
        self.assertEqual(result.items, [])
        generate.assert_not_called()

    def test_combined_rejects_draft_with_unknown_original_claim(self):
        from schemas import ResearchTaskRevisionResponse
        draft = ResearchTaskRevisionResponse(task_id=1, items=[self.revision], skipped_items=[])
        with (
            patch("revision_agent.revise_research_task", return_value=draft),
            patch("revision_agent.prepare_review_claims", return_value=[]),
            patch("review_agent.generate_text") as generate,
        ):
            with self.assertRaisesRegex(ValueError, "找不到对应的原陈述"):
                revision_agent.revise_and_review_research_task(Mock(), SimpleNamespace(task_id=1, sources=[None]*5), Mock())
        generate.assert_not_called()


class TestFieldClassification(unittest.TestCase):
    """检查分类模型输出规则与提示词，不访问真实模型。"""

    def setUp(self):
        from schemas import AnalysisRevisionResponse
        self.revision = AnalysisRevisionResponse(field="limitations", original_claim="原陈述",
            revised_claim="算法切换次数最少，运行时间最快。[资料9]", reason="修改理由", source_numbers=[9])

    def test_accept_valid_classification_verdicts(self):
        for verdict, suggested in (("appropriate", None), ("mixed", None), ("uncertain", None), ("misplaced", "experiments")):
            with self.subTest(verdict=verdict):
                answer = json.dumps(dict(verdict=verdict, reason="分类理由", suggested_field=suggested))
                with patch("revision_agent.generate_text", return_value="```json\n" + answer + "\n```"):
                    result = revision_agent.check_revision_field(self.revision)
                self.assertEqual(result.verdict, verdict)
                self.assertEqual(result.suggested_field, suggested)

    def test_reject_invalid_classification_payloads(self):
        cases = ["not JSON", "[]", "{}", json.dumps(dict(verdict="wrong", reason="原因")),
            json.dumps(dict(verdict="appropriate", reason=123)),
            json.dumps(dict(verdict="appropriate", reason=" ")),
            json.dumps(dict(verdict="misplaced", reason="原因", suggested_field="unknown"))]
        for answer in cases:
            with self.subTest(answer=answer), patch("revision_agent.generate_text", return_value=answer):
                with self.assertRaises(RuntimeError):
                    revision_agent.check_revision_field(self.revision)

    def test_reject_contradictory_suggested_field_rules(self):
        cases = [("misplaced", None), ("misplaced", "limitations"),
                 ("appropriate", "experiments"), ("mixed", "methods"), ("uncertain", "summary")]
        for verdict, suggested in cases:
            answer = json.dumps(dict(verdict=verdict, reason="分类理由", suggested_field=suggested))
            with self.subTest(verdict=verdict, suggested=suggested), patch("revision_agent.generate_text", return_value=answer):
                with self.assertRaises(RuntimeError):
                    revision_agent.check_revision_field(self.revision)

    def test_invalid_input_is_rejected_before_model_call(self):
        with patch("revision_agent.generate_text") as generate:
            for update in ({"field": "unknown"}, {"revised_claim": " "}):
                with self.subTest(update=update):
                    with self.assertRaises(ValueError):
                        revision_agent.check_revision_field(self.revision.model_copy(update=update))
        generate.assert_not_called()

    def test_prompt_checks_revised_text_and_field_without_changing_revision(self):
        before = self.revision.model_dump()
        answer = json.dumps(dict(verdict="misplaced", reason="实验结果", suggested_field="experiments"))
        with patch("revision_agent.generate_text", return_value=answer) as generate:
            revision_agent.check_revision_field(self.revision)
        generate.assert_called_once()
        arguments = generate.call_args.kwargs
        self.assertIn(self.revision.revised_claim, arguments["user_prompt"])
        self.assertIn("当前字段：limitations", arguments["user_prompt"])
        self.assertIn("不判断论文事实是否正确", arguments["system_prompt"])
        self.assertEqual(self.revision.model_dump(), before)


class TestFieldSplitter(unittest.TestCase):
    """校验拆分结构、字段、引用和原文本保留。"""

    def setUp(self):
        from schemas import AnalysisRevisionResponse, FieldCheckResponse
        self.revision=AnalysisRevisionResponse(field="limitations",original_claim="原陈述",
            revised_claim="采用阶段式切换。[资料3]实验显示成本降低。[资料5]",reason="修改",source_numbers=[3,5])
        self.category=FieldCheckResponse(verdict="mixed",reason="方法和实验混合",suggested_field=None)
        self.data=dict(reason="按主题拆分",items=[
            dict(field="methods",claim="采用阶段式切换。[资料3]"),
            dict(field="experiments",claim="实验显示成本降低。[资料5]"),
        ])

    def test_valid_split_preserves_original_and_derives_citations(self):
        data=dict(self.data,source_claim="模型改写原文",source_field="methods")
        data["items"][0]["source_numbers"]=[99]
        with patch("field_splitter.generate_text",return_value="```json\n"+json.dumps(data)+"\n```"):
            result=field_splitter.split_mixed_revision(self.revision,self.category,5)
        self.assertEqual(result.source_claim,self.revision.revised_claim)
        self.assertEqual(result.source_field,"limitations")
        self.assertEqual([i.source_numbers for i in result.items],[[3],[5]])

    def test_nonmixed_or_invalid_input_never_calls_model(self):
        cases=[(self.revision,self.category.model_copy(update={"verdict":"appropriate"})),
               (self.revision.model_copy(update={"revised_claim":" "}),self.category),
               (self.revision.model_copy(update={"source_numbers":[3]}),self.category)]
        with patch("field_splitter.generate_text") as generate:
            for revision,category in cases:
                with self.subTest(revision=revision):
                    with self.assertRaises(ValueError):
                        field_splitter.split_mixed_revision(revision,category,5)
        generate.assert_not_called()

    def test_reject_invalid_structure_or_item_count(self):
        cases=["not JSON","[]","{}",json.dumps(dict(self.data,items=self.data["items"][:1])),
               json.dumps(dict(self.data,items=self.data["items"]*5))]
        for answer in cases:
            with self.subTest(answer=answer),patch("field_splitter.generate_text",return_value=answer):
                with self.assertRaises(RuntimeError):
                    field_splitter.split_mixed_revision(self.revision,self.category,5)

    def test_reject_blank_claim_reason_and_invalid_field(self):
        cases=[dict(self.data,reason=" "),dict(self.data,items=[dict(field="methods",claim=" "),self.data["items"][1]]),
               dict(self.data,items=[dict(field="unknown",claim="正文"),self.data["items"][1]])]
        for data in cases:
            with self.subTest(data=data),patch("field_splitter.generate_text",return_value=json.dumps(data)):
                with self.assertRaises(RuntimeError):
                    field_splitter.split_mixed_revision(self.revision,self.category,5)

    def test_reject_new_out_of_range_or_unclosed_citations(self):
        for citation in ("[资料1]","[资料6]","[资料3"):
            data=dict(self.data,items=[dict(field="methods",claim="正文"+citation),self.data["items"][1]])
            with self.subTest(citation=citation),patch("field_splitter.generate_text",return_value=json.dumps(data)):
                with self.assertRaises(RuntimeError):
                    field_splitter.split_mixed_revision(self.revision,self.category,5)

    def test_reject_split_that_stays_in_one_field(self):
        data=dict(self.data,items=[dict(field="methods",claim="方法[资料3]"),dict(field="methods",claim="另一方法[资料5]")])
        with patch("field_splitter.generate_text",return_value=json.dumps(data)):
            with self.assertRaisesRegex(RuntimeError,"两个不同字段"):
                field_splitter.split_mixed_revision(self.revision,self.category,5)

    def test_prompt_uses_mixed_text_and_preserves_uncertainty_rules(self):
        with patch("field_splitter.generate_text",return_value=json.dumps(self.data)) as generate:
            field_splitter.split_mixed_revision(self.revision,self.category,5)
        self.assertIn(self.revision.revised_claim,generate.call_args.kwargs["user_prompt"])
        self.assertIn(self.category.reason,generate.call_args.kwargs["user_prompt"])
        self.assertIn("保留范围、限定条件和不确定性",generate.call_args.kwargs["system_prompt"])


    def test_reject_added_numbers_and_missing_citations(self):
        cases=[dict(self.data,items=[self.data["items"][0],dict(field="experiments",claim="成本减少90.91%。[资料5]")]),
               dict(self.data,items=[self.data["items"][0],dict(field="limitations",claim="当前原文不足。")])]
        for data in cases:
            with self.subTest(data=data),patch("field_splitter.generate_text",return_value=json.dumps(data)):
                with self.assertRaises(RuntimeError):
                    field_splitter.split_mixed_revision(self.revision,self.category,5)

    def test_split_retries_once_then_accepts_corrected_output(self):
        with patch("field_splitter.generate_text",side_effect=["invalid JSON",json.dumps(self.data)]) as generate:
            result=field_splitter.split_mixed_revision(self.revision,self.category,5)
        self.assertEqual(generate.call_count,2)
        self.assertEqual(len(result.items),2)
        self.assertIn("上次输出未通过检查",generate.call_args.kwargs["user_prompt"])

    def test_split_retry_is_bounded(self):
        with patch("field_splitter.generate_text",return_value="invalid JSON") as generate:
            with self.assertRaises(RuntimeError):
                field_splitter.split_mixed_revision(self.revision,self.category,5)
        self.assertEqual(generate.call_count,2)

    def test_original_numeric_values_and_citation_ids_are_allowed(self):
        revision=self.revision.model_copy(update={"revised_claim":"采用阶段式切换。[资料3]成本下降20%。[资料5]"})
        data=dict(self.data,items=[self.data["items"][0],dict(field="experiments",claim="成本下降20%。[资料5]")])
        with patch("field_splitter.generate_text",return_value=json.dumps(data)):
            result=field_splitter.split_mixed_revision(revision,self.category,5)
        self.assertIn("20%",result.items[1].claim)


class TestSplitVerification(unittest.TestCase):
    def setUp(self):
        from schemas import AnalysisRevisionResponse,FieldCheckResponse,AnalysisSplitResponse
        self.claim=ReviewClaim("limitations","原陈述[资料3]",[3],[ReviewEvidence(3,"论文",14,5,"方法原文")])
        self.revision=AnalysisRevisionResponse(field="limitations",original_claim=self.claim.claim,
            revised_claim="方法与边界[资料3]",reason="修改",source_numbers=[3])
        self.category=FieldCheckResponse(verdict="mixed",reason="混合",suggested_field=None)
        self.split=AnalysisSplitResponse(source_field="limitations",source_claim=self.revision.revised_claim,reason="拆分",items=[
            dict(field="methods",claim="方法[资料3]",source_numbers=[3]),
            dict(field="limitations",claim="当前证据不足",source_numbers=[]),
        ])

    def test_missing_evidence_is_rejected_before_recheck(self):
        claim=ReviewClaim(self.claim.field,self.claim.claim,[3],[])
        with patch("field_splitter.split_mixed_revision",return_value=self.split),patch("revision_agent.review_claim") as check:
            with self.assertRaisesRegex(ValueError,"原文证据不存在"):
                revision_agent.split_and_check_revision(claim,self.revision,self.category,3)
        check.assert_not_called()

    def test_no_citation_part_is_not_marked_ready(self):
        appropriate=json.dumps(dict(verdict="appropriate",reason="分类正确",suggested_field=None))
        supported=json.dumps(dict(verdict="supported",reason="原文支持",source_numbers=[3]))
        with (
            patch("field_splitter.split_mixed_revision",return_value=self.split),
            patch("review_agent.generate_text",return_value=supported) as generate,
            patch("revision_agent.generate_text",return_value=appropriate),
        ):
            result,checks=revision_agent.split_and_check_revision(self.claim,self.revision,self.category,3)
        generate.assert_called_once()
        self.assertEqual([i.ready for i in checks],[True,False])
        self.assertEqual(checks[1].review.verdict,"insufficient_evidence")


class TestEvidenceNotes(unittest.TestCase):
    """区分论文局限性和检索证据不足，并兼容旧分析数据。"""

    def _legacy_analysis(self):
        return dict(task_id=1,question="研究问题",summary="概述",methods=[],experiments=[],
                    conclusions=[],limitations=[],sources=[])

    def test_old_analysis_json_defaults_notes_to_empty_list(self):
        data=self._legacy_analysis()
        analysis=ResearchTaskAnalysisResponse.model_validate_json(json.dumps(data))
        self.assertEqual(analysis.evidence_notes,[])
        parsed=paper_analysis_agent.parse_analysis_response(json.dumps(data))
        self.assertEqual(parsed["evidence_notes"],[])

    def test_notes_type_and_citations_are_validated(self):
        data=self._legacy_analysis()
        data["evidence_notes"]="不是列表"
        with self.assertRaises(RuntimeError):
            paper_analysis_agent.parse_analysis_response(json.dumps(data))
        data["evidence_notes"]=["当前资料未提供。[资料2]"]
        with self.assertRaisesRegex(RuntimeError,"evidence_notes.*引用不存在"):
            paper_analysis_agent.validate_analysis_citations(data,1)

    def test_notes_are_prepared_for_review_with_original_evidence(self):
        analysis=ResearchTaskAnalysisResponse.model_validate(dict(self._legacy_analysis(),
            evidence_notes=["当前引用原文未明确说明。[资料1]"],
            sources=[dict(paper_id=3,paper_title="论文",chunk_id=14,page_number=5,score=0.9)]))
        db=Mock()
        db.get.side_effect=[SimpleNamespace(id=14,document_id=2,page_number=5,text="原文"),SimpleNamespace(paper_id=3)]
        claims=review_service.prepare_review_claims(db,analysis)
        self.assertEqual(claims[-1].field,"evidence_notes")
        self.assertEqual(claims[-1].evidence[0].text,"原文")

    def test_classifier_accepts_notes_and_suggests_them_for_meta_claims(self):
        from schemas import AnalysisRevisionResponse
        revision=AnalysisRevisionResponse(field="limitations",original_claim="原陈述",
            revised_claim="当前原文未提及生产环境验证。[资料1]",reason="备注",source_numbers=[1])
        misplaced=json.dumps(dict(verdict="misplaced",reason="这是检索备注",suggested_field="evidence_notes"))
        with patch("revision_agent.generate_text",return_value=misplaced):
            result=revision_agent.check_revision_field(revision)
        self.assertEqual(result.suggested_field,"evidence_notes")
        with patch("revision_agent.generate_text",return_value=json.dumps(dict(verdict="appropriate",reason="证据备注",suggested_field=None))) as generate:
            revision_agent.check_revision_field(revision.model_copy(update={"field":"evidence_notes"}))
        prompt=generate.call_args.kwargs["system_prompt"]
        self.assertIn("evidence_notes用于当前引用原文不足",prompt)
        self.assertIn("limitations只用于论文自身",prompt)

    def test_splitter_can_separate_methods_and_evidence_notes(self):
        from schemas import AnalysisRevisionResponse,FieldCheckResponse
        revision=AnalysisRevisionResponse(field="limitations",original_claim="原陈述",
            revised_claim="使用缓存。[资料1]当前引用原文未说明生产验证。[资料1]",reason="拆分",source_numbers=[1])
        category=FieldCheckResponse(verdict="mixed",reason="方法和证据备注混合")
        answer=json.dumps(dict(reason="分开方法和备注",items=[
            dict(field="methods",claim="使用缓存。[资料1]"),
            dict(field="evidence_notes",claim="当前引用原文未说明生产验证。[资料1]"),
        ]))
        with patch("field_splitter.generate_text",return_value=answer):
            result=field_splitter.split_mixed_revision(revision,category,1)
        self.assertEqual(result.items[1].field,"evidence_notes")
        self.assertEqual(result.items[1].source_numbers,[1])

    def test_move_preserves_text_citations_and_original_field(self):
        from schemas import AnalysisRevisionResponse
        revision=AnalysisRevisionResponse(field="limitations",original_claim="原陈述[资料1]",
            revised_claim="当前原文未说明。[资料1]",reason="修改",source_numbers=[1])
        claim=ReviewClaim("limitations",revision.original_claim,[1],[ReviewEvidence(1,"论文",14,5,"原文")])
        before=revision.model_dump()
        with (
            patch("review_agent.generate_text",return_value=json.dumps(dict(verdict="supported",reason="引用片段确实未说明",source_numbers=[1]))),
            patch("revision_agent.generate_text",return_value=json.dumps(dict(verdict="appropriate",reason="证据备注",suggested_field=None))),
        ):
            result=revision_agent.move_revision_to_evidence_notes(claim,revision,1)
        self.assertEqual(result.source_field,"limitations")
        self.assertEqual(result.item.field,"evidence_notes")
        self.assertEqual(result.item.claim,revision.revised_claim)
        self.assertEqual(result.review.field,"evidence_notes")
        self.assertTrue(result.ready)
        self.assertEqual(revision.model_dump(),before)

    def test_move_does_not_accept_unverified_note(self):
        from schemas import AnalysisRevisionResponse
        revision=AnalysisRevisionResponse(field="limitations",original_claim="原陈述[资料1]",
            revised_claim="当前原文未说明。[资料1]",reason="修改",source_numbers=[1])
        claim=ReviewClaim("limitations",revision.original_claim,[1],[ReviewEvidence(1,"论文",14,5,"原文")])
        with (
            patch("review_agent.generate_text",return_value=json.dumps(dict(verdict="insufficient_evidence",reason="证据不足",source_numbers=[1]))),
            patch("revision_agent.generate_text",return_value=json.dumps(dict(verdict="appropriate",reason="备注",suggested_field=None))),
        ):
            result=revision_agent.move_revision_to_evidence_notes(claim,revision,1)
        self.assertFalse(result.ready)

    def test_analysis_agent_returns_new_evidence_notes(self):
        data=dict(self._legacy_analysis(),summary="概述[资料1]",evidence_notes=["当前原文不足。[资料1]"])
        with (
            patch("paper_analysis_agent.collect_analysis_matches",return_value=[(SimpleNamespace(id=14,page_number=5,text="原文"),SimpleNamespace(id=3,title="论文"),0.9)]),
            patch("paper_analysis_agent.generate_text",return_value=json.dumps(data)),
        ):
            result=paper_analysis_agent.analyze_research_task(Mock(),1,"研究问题")
        self.assertEqual(result.evidence_notes,data["evidence_notes"])


class TestEvidenceNoteAdoptionAPI(unittest.TestCase):
    """通过真实接口读写测试数据库，只模拟大模型的返回内容。"""

    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

        self.old_claim = "论文没有进行生产环境验证。[资料1]"
        self.new_claim = "当前引用原文未提供生产环境验证信息。[资料1]"
        self.evidence_text = "实验采用离线请求轨迹，在单台机器上测量缓存命中率。"

        with TestingSessionLocal() as db:
            paper = Paper(title="采纳测试论文", author="测试作者", year=2025, notes="")
            task = ResearchTask(title="采纳测试", question="有哪些评估边界？", status="completed")
            db.add_all([paper, task])
            db.flush()

            document = PaperDocument(
                paper_id=paper.id, original_name="adoption.pdf", stored_name="adoption.pdf",
                file_path="/unused/adoption.pdf", content_type="application/pdf", file_size=10,
            )
            db.add(document)
            db.flush()

            first_chunk = PaperChunk(
                document_id=document.id, page_number=1, chunk_index=0,
                text=self.evidence_text, char_count=len(self.evidence_text),
            )
            second_chunk = PaperChunk(
                document_id=document.id, page_number=2, chunk_index=0,
                text="实验测量了缓存命中率。", char_count=12,
            )
            db.add_all([first_chunk, second_chunk])
            db.flush()
            self.task_id = task.id
            self.chunk_id = first_chunk.id

            analysis = ResearchTaskAnalysisResponse(
                task_id=task.id, question=task.question,
                summary="研究离线缓存评估。[资料1]",
                methods=[], experiments=["实验测量缓存命中率。[资料2]"],
                conclusions=[], limitations=[self.old_claim],
                evidence_notes=["已有证据备注。[资料2]"],
                sources=[
                    AnalysisSourceResponse(paper_id=paper.id, paper_title=paper.title,
                                           chunk_id=first_chunk.id, page_number=1, score=0.9),
                    AnalysisSourceResponse(paper_id=paper.id, paper_title=paper.title,
                                           chunk_id=second_chunk.id, page_number=2, score=0.8),
                ],
            )
            review = ResearchTaskReviewResponse(task_id=task.id, items=[
                ReviewItemResponse(field="limitations", claim=self.old_claim,
                                   verdict="partially_supported", reason="只能说明当前原文范围。",
                                   source_numbers=[1]),
            ])
            task.result = analysis.model_dump_json()
            task.review_result = review.model_dump_json()
            self.original = (task.result, task.review_result, task.status)
            db.commit()

        self.payload = {
            "base_analysis": analysis.model_dump(),
            "revision": {
                "field": "limitations", "original_claim": self.old_claim,
                "revised_claim": self.new_claim, "reason": "限定为当前引用原文。",
                "source_numbers": [1],
            },
        }
        self.support = json.dumps({
            "verdict": "supported", "reason": "只描述提供片段的信息范围。", "source_numbers": [1],
        })
        self.category = json.dumps({
            "verdict": "appropriate", "reason": "适合证据备注。", "suggested_field": None,
        })
        self.url = f"/research-tasks/{self.task_id}/analysis/adopt-evidence-note"

    def _post(self, support=None, category=None, payload=None, url=None):
        """替换模型调用，其余取证、检查、接口和保存逻辑照常执行。"""
        with (
            patch("review_agent.generate_text", return_value=self.support if support is None else support) as review,
            patch("revision_agent.generate_text", return_value=self.category if category is None else category) as classify,
        ):
            response = client.post(self.url if url is None else url,
                                   json=self.payload if payload is None else payload)
        return response, review, classify

    def _assert_unchanged(self, expected=None):
        """失败时，任务应保持原样，也不能留下半条版本记录。"""
        from sqlalchemy import select
        from model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual((task.result, task.review_result, task.status),
                             self.original if expected is None else expected)
            versions = db.scalars(select(ResearchTaskAnalysisVersion)).all()
            self.assertEqual(versions, [])

    def test_success_moves_text_preserves_sources_and_saves_history(self):
        from model import ResearchTaskAnalysisVersion
        response, review, classify = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        analysis = result["analysis"]
        self.assertEqual(result["task_id"], self.task_id)
        self.assertEqual(analysis["limitations"], [])
        self.assertEqual(analysis["evidence_notes"], ["已有证据备注。[资料2]", self.new_claim])
        for field in ("summary", "methods", "experiments", "conclusions", "sources", "question", "task_id"):
            self.assertEqual(analysis[field], self.payload["base_analysis"][field])
        review.assert_called_once()
        classify.assert_called_once()
        prompt = review.call_args.kwargs["user_prompt"]
        self.assertIn(self.new_claim, prompt)
        self.assertIn(self.evidence_text, prompt)
        self.assertNotIn("实验测量了缓存命中率。", prompt)
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            version = db.get(ResearchTaskAnalysisVersion, result["version_id"])
            self.assertEqual(json.loads(task.result), analysis)
            self.assertIsNone(task.review_result)
            self.assertEqual(task.status, "completed")
            self.assertEqual(version.task_id, self.task_id)
            self.assertEqual(version.previous_analysis, self.original[0])
            self.assertEqual(version.previous_review, self.original[1])
            self.assertEqual(json.loads(version.updated_analysis), analysis)
            self.assertIn("limitations", version.reason)
            self.assertIsNotNone(version.created_at)
        saved = client.get(f"/research-tasks/{self.task_id}/analysis")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), analysis)
        versions = client.get(f"/research-tasks/{self.task_id}/analysis/versions")
        self.assertEqual(versions.status_code, 200, versions.text)
        self.assertEqual([row["id"] for row in versions.json()], [result["version_id"]])
        detail = client.get(f"/research-tasks/{self.task_id}/analysis/versions/{result['version_id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["previous_analysis"], json.loads(self.original[0]))
        self.assertEqual(detail.json()["updated_analysis"], analysis)
        self.assertEqual(detail.json()["previous_review"], json.loads(self.original[1]))
        self.assertEqual(client.get(f"/research-tasks/{self.task_id}/review").status_code, 404)

    def test_success_without_previous_review(self):
        from model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).review_result = None
            db.commit()
        response, _, _ = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        with TestingSessionLocal() as db:
            version = db.get(ResearchTaskAnalysisVersion, response.json()["version_id"])
            self.assertIsNone(version.previous_review)
            self.assertIsNone(db.get(ResearchTask, self.task_id).review_result)

    def test_partial_support_cannot_be_adopted(self):
        answer = json.dumps(dict(verdict="partially_supported", reason="只有部分内容有依据。", source_numbers=[1]))
        response, _, _ = self._post(support=answer)
        self.assertEqual(response.status_code, 409, response.text)
        self._assert_unchanged()

    def test_wrong_category_cannot_be_adopted(self):
        answer = json.dumps(dict(verdict="misplaced", reason="属于实验。", suggested_field="experiments"))
        response, _, _ = self._post(category=answer)
        self.assertEqual(response.status_code, 409, response.text)
        self._assert_unchanged()

    def test_stale_analysis_is_rejected_before_model_calls(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            analysis = json.loads(task.result)
            analysis["summary"] = "其他操作更新的概述。[资料1]"
            task.result = json.dumps(analysis)
            expected = (task.result, task.review_result, task.status)
            db.commit()
        response, review, classify = self._post()
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged(expected)

    def test_duplicate_adoption_creates_only_one_version(self):
        from sqlalchemy import select
        from model import ResearchTaskAnalysisVersion
        first, _, _ = self._post()
        self.assertEqual(first.status_code, 200, first.text)
        second, review, classify = self._post()
        self.assertEqual(second.status_code, 409, second.text)
        review.assert_not_called()
        classify.assert_not_called()
        with TestingSessionLocal() as db:
            self.assertEqual(len(db.scalars(select(ResearchTaskAnalysisVersion)).all()), 1)
            self.assertEqual(json.loads(db.get(ResearchTask, self.task_id).result), first.json()["analysis"])

    def test_nonexistent_or_duplicate_original_claim_is_rejected(self):
        for claims in ([], [self.old_claim, self.old_claim]):
            with self.subTest(claims=claims):
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    analysis = json.loads(self.original[0])
                    analysis["limitations"] = claims
                    task.result = json.dumps(analysis)
                    expected = (task.result, task.review_result, task.status)
                    db.commit()
                payload = dict(self.payload, base_analysis=analysis)
                response, review, classify = self._post(payload=payload)
                self.assertEqual(response.status_code, 409, response.text)
                review.assert_not_called()
                classify.assert_not_called()
                self._assert_unchanged(expected)

    def test_existing_note_is_not_added_again(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            analysis = json.loads(task.result)
            analysis["evidence_notes"].append(self.new_claim)
            task.result = json.dumps(analysis)
            expected = (task.result, task.review_result, task.status)
            db.commit()
        response, review, classify = self._post(payload=dict(self.payload, base_analysis=analysis))
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged(expected)

    def test_summary_is_not_supported_by_this_endpoint(self):
        revision = dict(self.payload["revision"], field="summary",
                        original_claim=self.payload["base_analysis"]["summary"])
        response, review, classify = self._post(payload=dict(self.payload, revision=revision))
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged()

    def test_new_citation_is_rejected_before_model_calls(self):
        revision = dict(self.payload["revision"], revised_claim="当前资料不足。[资料2]", source_numbers=[2])
        response, review, classify = self._post(payload=dict(self.payload, revision=revision))
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged()

    def test_missing_evidence_prevents_adoption(self):
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id))
            db.commit()
        response, review, classify = self._post()
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged()

    def test_malformed_model_output_does_not_change_database(self):
        for answers in (("not JSON", self.category), (self.support, "not JSON")):
            with self.subTest(answers=answers):
                response, _, _ = self._post(support=answers[0], category=answers[1])
                self.assertEqual(response.status_code, 502, response.text)
                self._assert_unchanged()

    def test_model_connection_failure_does_not_change_database(self):
        with patch("review_agent.generate_text", side_effect=RuntimeError("模拟模型连接失败")):
            response = client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 502, response.text)
        self._assert_unchanged()

    def test_failed_commit_rolls_back_flushed_version_and_analysis(self):
        with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("模拟提交失败")) as commit:
            response, _, _ = self._post()
        self.assertEqual(response.status_code, 502, response.text)
        commit.assert_called_once()
        self._assert_unchanged()

    def test_analysis_changed_during_check_is_not_overwritten(self):
        original_move = revision_agent.move_revision_to_evidence_notes
        expected = []

        def change_analysis(*args, **kwargs):
            checked = original_move(*args, **kwargs)
            with TestingSessionLocal() as db:
                task = db.get(ResearchTask, self.task_id)
                analysis = json.loads(task.result)
                analysis["summary"] = "检查期间保存的另一份分析。[资料1]"
                task.result = json.dumps(analysis)
                expected.append((task.result, task.review_result, task.status))
                db.commit()
            return checked

        with patch("revision_agent.move_revision_to_evidence_notes", side_effect=change_analysis):
            response, _, _ = self._post()
        self.assertEqual(response.status_code, 409, response.text)
        self._assert_unchanged(expected[0])

    def test_missing_task_and_missing_analysis_do_not_call_models(self):
        response, review, classify = self._post(
            url="/research-tasks/999999/analysis/adopt-evidence-note")
        self.assertEqual(response.status_code, 404, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            task.result = None
            expected = (task.result, task.review_result, task.status)
            db.commit()
        response, review, classify = self._post()
        self.assertEqual(response.status_code, 409, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged(expected)

    def test_invalid_request_returns_422_without_changing_database(self):
        response, review, classify = self._post(payload={"base_analysis": self.payload["base_analysis"]})
        self.assertEqual(response.status_code, 422, response.text)
        review.assert_not_called()
        classify.assert_not_called()
        self._assert_unchanged()

    def test_updated_analysis_can_be_reviewed_again(self):
        response, _, _ = self._post()
        self.assertEqual(response.status_code, 200, response.text)

        def review_current_text(*, system_prompt, user_prompt, json_mode=False):
            number = 2 if "[资料2]" in user_prompt else 1
            return json.dumps(dict(verdict="supported", reason="测试当前正文的审校流程。", source_numbers=[number]))

        with patch("review_agent.generate_text", side_effect=review_current_text) as review:
            reviewed = client.post(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(review.call_count, 4)
        notes = [item for item in reviewed.json()["items"] if item["field"] == "evidence_notes"]
        self.assertEqual([item["claim"] for item in notes], ["已有证据备注。[资料2]", self.new_claim])
        self.assertNotIn(self.old_claim, [item["claim"] for item in reviewed.json()["items"]])
        saved = client.get(f"/research-tasks/{self.task_id}/review")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), reviewed.json())

    def test_adopt_then_restore_returns_original_analysis_and_preserves_both_versions(self):
        from sqlalchemy import select
        from model import ResearchTaskAnalysisVersion
        adopted, _, _ = self._post()
        self.assertEqual(adopted.status_code, 200, adopted.text)
        adopted_data = adopted.json()
        with patch("review_agent.generate_text") as review, patch("revision_agent.generate_text") as revise:
            restored = client.post(
                f"/research-tasks/{self.task_id}/analysis/versions/{adopted_data['version_id']}/restore",
                json={"base_analysis": adopted_data["analysis"]},
            )
        self.assertEqual(restored.status_code, 200, restored.text)
        review.assert_not_called()
        revise.assert_not_called()
        data = restored.json()
        self.assertEqual(data["analysis"], self.payload["base_analysis"])
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual(json.loads(task.result), self.payload["base_analysis"])
            self.assertIsNone(task.review_result)
            original_version = db.get(ResearchTaskAnalysisVersion, adopted_data["version_id"])
            self.assertEqual(original_version.previous_analysis, self.original[0])
            self.assertEqual(original_version.previous_review, self.original[1])
            new_version = db.get(ResearchTaskAnalysisVersion, data["version_id"])
            self.assertEqual(json.loads(new_version.previous_analysis), adopted_data["analysis"])
            self.assertEqual(json.loads(new_version.updated_analysis), self.payload["base_analysis"])
            self.assertIsNone(new_version.previous_review)
            self.assertEqual(len(db.scalars(select(ResearchTaskAnalysisVersion)).all()), 2)
        detail = client.get(f"/research-tasks/{self.task_id}/analysis/versions/{data['version_id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["updated_analysis"], self.payload["base_analysis"])


class TestAnalysisVersionHistoryAPI(unittest.TestCase):
    """用真实接口查询测试数据库，确认版本隔离、JSON转换和只读行为。"""

    def setUp(self):
        from model import ResearchTaskAnalysisVersion
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        with TestingSessionLocal() as db:
            task = ResearchTask(title="版本查询测试", question="研究问题", status="completed")
            other = ResearchTask(title="其他任务", question="其他问题", status="completed")
            empty = ResearchTask(title="尚无版本的任务", question="问题", status="pending")
            db.add_all([task, other, empty])
            db.flush()
            self.task_id, self.other_id, self.empty_id = task.id, other.id, empty.id
            before = ResearchTaskAnalysisResponse(
                task_id=task.id, question=task.question, summary="旧概述", methods=[], experiments=[],
                conclusions=[], limitations=["当前资料未说明生产验证。"], evidence_notes=[], sources=[],
            )
            after = before.model_copy(deep=True)
            after.limitations = []
            after.evidence_notes = ["当前引用原文未说明生产验证。"]
            review = ResearchTaskReviewResponse(task_id=task.id, items=[
                ReviewItemResponse(field="limitations", claim=before.limitations[0],
                                   verdict="insufficient_evidence", reason="测试旧审校", source_numbers=[])
            ])
            task.result, task.review_result = after.model_dump_json(), None
            self.before, self.after, self.review = before.model_dump(), after.model_dump(), review.model_dump()
            self.saved_json = dict(previous_analysis=before.model_dump_json(),
                                   updated_analysis=after.model_dump_json(), previous_review=review.model_dump_json())
            old_version = ResearchTaskAnalysisVersion(task_id=task.id, reason="第一次修改", **self.saved_json)
            db.add(old_version)
            db.flush()
            self.old_version_id = old_version.id
            other_analysis = before.model_copy(update={"task_id": other.id, "question": other.question})
            other_version = ResearchTaskAnalysisVersion(
                task_id=other.id, previous_analysis=other_analysis.model_dump_json(),
                updated_analysis=other_analysis.model_dump_json(), previous_review=None, reason="其他任务的版本",
            )
            db.add(other_version)
            db.flush()
            self.other_version_id = other_version.id
            new_version = ResearchTaskAnalysisVersion(task_id=task.id, reason="第二次修改", **self.saved_json)
            db.add(new_version)
            db.flush()
            self.new_version_id = new_version.id
            db.commit()

    def _snapshot(self):
        """完整记录当前任务和版本内容，用于确认 GET 没有修改数据库。"""
        from sqlalchemy import select
        from model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            tasks = tuple((task.id, task.title, task.question, task.result, task.review_result, task.status)
                          for task in db.scalars(select(ResearchTask).order_by(ResearchTask.id)))
            versions = tuple((version.id, version.task_id, version.previous_analysis,
                              version.updated_analysis, version.previous_review, version.reason, version.created_at)
                             for version in db.scalars(select(ResearchTaskAnalysisVersion).order_by(ResearchTaskAnalysisVersion.id)))
            return tasks, versions

    def _get(self, version_id=None, task_id=None):
        """每次查询都检查数据库不变，且不调用任何分析、审校、修订模型。"""
        before = self._snapshot()
        task_id = self.task_id if task_id is None else task_id
        url = f"/research-tasks/{task_id}/analysis/versions"
        if version_id is not None:
            url += f"/{version_id}"
        with (
            patch("paper_analysis_agent.generate_text") as analyze,
            patch("review_agent.generate_text") as review,
            patch("revision_agent.generate_text") as revise,
        ):
            response = client.get(url)
        analyze.assert_not_called()
        review.assert_not_called()
        revise.assert_not_called()
        self.assertEqual(self._snapshot(), before)
        return response

    def test_list_returns_latest_first_and_only_summary_fields(self):
        from datetime import datetime
        response = self._get()
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual([row["id"] for row in rows], [self.new_version_id, self.old_version_id])
        for row in rows:
            self.assertEqual(set(row), {"id", "task_id", "reason", "created_at"})
            self.assertEqual(row["task_id"], self.task_id)
            datetime.fromisoformat(row["created_at"])
        self.assertEqual(rows[0]["reason"], "第二次修改")

    def test_list_is_scoped_to_requested_task(self):
        response = self._get(task_id=self.other_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["id"] for row in response.json()], [self.other_version_id])
        self.assertEqual(response.json()[0]["task_id"], self.other_id)

    def test_existing_task_without_versions_returns_empty_list(self):
        response = self._get(task_id=self.empty_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])

    def test_nonexistent_task_returns_404_for_list_and_detail(self):
        for version_id in (None, self.old_version_id):
            with self.subTest(version_id=version_id):
                response = self._get(task_id=999999, version_id=version_id)
                self.assertEqual(response.status_code, 404, response.text)

    def test_detail_returns_structured_before_after_and_old_review(self):
        response = self._get(self.new_version_id)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["id"], self.new_version_id)
        self.assertEqual(data["task_id"], self.task_id)
        self.assertEqual(data["reason"], "第二次修改")
        self.assertTrue(data["created_at"])
        self.assertEqual(data["previous_analysis"], self.before)
        self.assertEqual(data["updated_analysis"], self.after)
        self.assertEqual(data["previous_review"], self.review)
        self.assertIsInstance(data["previous_analysis"], dict)
        self.assertIsInstance(data["updated_analysis"], dict)
        self.assertIsInstance(data["previous_review"], dict)

    def test_detail_allows_missing_old_review(self):
        from model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            db.get(ResearchTaskAnalysisVersion, self.new_version_id).previous_review = None
            db.commit()
        response = self._get(self.new_version_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["previous_review"])
        self.assertEqual(response.json()["previous_analysis"], self.before)

    def test_detail_reads_legacy_analysis_without_rewriting_stored_json(self):
        from model import ResearchTaskAnalysisVersion
        legacy_before = dict(self.before)
        legacy_after = dict(self.after)
        legacy_before.pop("evidence_notes")
        legacy_after.pop("evidence_notes")
        with TestingSessionLocal() as db:
            version = db.get(ResearchTaskAnalysisVersion, self.new_version_id)
            version.previous_analysis = json.dumps(legacy_before)
            version.updated_analysis = json.dumps(legacy_after)
            db.commit()
        response = self._get(self.new_version_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["previous_analysis"]["evidence_notes"], [])
        self.assertEqual(response.json()["updated_analysis"]["evidence_notes"], [])
        # _get 已确认默认值补齐只发生在响应中，数据库的旧 JSON 未被覆盖。

    def test_version_from_another_task_returns_404(self):
        response = self._get(self.other_version_id)
        self.assertEqual(response.status_code, 404, response.text)

    def test_nonexistent_version_returns_404(self):
        response = self._get(999999)
        self.assertEqual(response.status_code, 404, response.text)

    def test_malformed_saved_json_returns_409_without_modifying_history(self):
        from model import ResearchTaskAnalysisVersion
        for field in self.saved_json:
            with self.subTest(field=field):
                with TestingSessionLocal() as db:
                    version = db.get(ResearchTaskAnalysisVersion, self.new_version_id)
                    for original_field, value in self.saved_json.items():
                        setattr(version, original_field, value)
                    setattr(version, field, "这不是JSON")
                    db.commit()
                response = self._get(self.new_version_id)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"], "这个版本保存的数据格式不正确")

    def test_wrong_saved_field_types_return_409(self):
        from model import ResearchTaskAnalysisVersion
        bad_data = dict(self.before, methods="错误的字符串")
        with TestingSessionLocal() as db:
            version = db.get(ResearchTaskAnalysisVersion, self.new_version_id)
            version.previous_analysis = json.dumps(bad_data)
            db.commit()
        response = self._get(self.new_version_id)
        self.assertEqual(response.status_code, 409, response.text)

    def test_history_remains_readable_without_current_analysis_or_review(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            task.result, task.review_result = None, None
            db.commit()
        self.assertEqual(self._get().status_code, 200)
        response = self._get(self.new_version_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["previous_review"], self.review)

    def test_deleting_task_removes_its_versions_but_keeps_other_tasks_history(self):
        from sqlalchemy import select
        from model import ResearchTaskAnalysisVersion
        response = client.delete(f"/research-tasks/{self.task_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._get().status_code, 404)
        self.assertEqual(self._get(self.old_version_id).status_code, 404)
        with TestingSessionLocal() as db:
            versions = db.scalars(select(ResearchTaskAnalysisVersion)).all()
            self.assertEqual([version.id for version in versions], [self.other_version_id])
        self.assertEqual(self._get(self.other_version_id, task_id=self.other_id).status_code, 200)


class TestAnalysisRestoreAPI(unittest.TestCase):
    """恢复接口读写独立测试数据库，验证历史保留和事务回滚。"""

    def setUp(self):
        from model import ResearchTaskAnalysisVersion
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        with TestingSessionLocal() as db:
            task = ResearchTask(title="恢复测试", question="研究问题", status="completed")
            other = ResearchTask(title="其他任务", question="其他问题", status="completed")
            db.add_all([task, other])
            db.flush()
            self.task_id, self.other_id = task.id, other.id
            before = ResearchTaskAnalysisResponse(
                task_id=task.id, question=task.question, summary="历史概述", methods=["历史方法"],
                experiments=[], conclusions=[], limitations=["历史边界"], evidence_notes=[], sources=[],
            )
            current = before.model_copy(deep=True)
            current.summary = "当前概述"
            current.methods = ["当前方法"]
            current.limitations = []
            current.evidence_notes = ["当前证据备注"]
            old_review = ResearchTaskReviewResponse(task_id=task.id, items=[
                ReviewItemResponse(field="summary", claim=before.summary, verdict="insufficient_evidence",
                                   reason="历史审校", source_numbers=[]),
            ])
            current_review = ResearchTaskReviewResponse(task_id=task.id, items=[
                ReviewItemResponse(field="summary", claim=current.summary, verdict="insufficient_evidence",
                                   reason="当前审校", source_numbers=[]),
            ])
            task.result = current.model_dump_json()
            task.review_result = current_review.model_dump_json()
            version = ResearchTaskAnalysisVersion(
                task_id=task.id, previous_analysis=before.model_dump_json(),
                updated_analysis=current.model_dump_json(), previous_review=old_review.model_dump_json(),
                reason="原始修改记录",
            )
            other_analysis = before.model_copy(update={"task_id": other.id, "question": other.question})
            other_version = ResearchTaskAnalysisVersion(
                task_id=other.id, previous_analysis=other_analysis.model_dump_json(),
                updated_analysis=other_analysis.model_dump_json(), previous_review=None, reason="其他任务的历史",
            )
            db.add_all([version, other_version])
            db.flush()
            self.version_id, self.other_version_id = version.id, other_version.id
            self.before, self.current = before.model_dump(), current.model_dump()
            self.current_json, self.current_review_json = task.result, task.review_result
            self.historical_json = version.previous_analysis
            db.commit()
        self.payload = {"base_analysis": self.current}
        self.url = f"/research-tasks/{self.task_id}/analysis/versions/{self.version_id}/restore"

    def _snapshot(self):
        from sqlalchemy import select
        from model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            tasks = tuple((task.id, task.title, task.question, task.result, task.review_result, task.status)
                          for task in db.scalars(select(ResearchTask).order_by(ResearchTask.id)))
            versions = tuple((version.id, version.task_id, version.previous_analysis, version.updated_analysis,
                              version.previous_review, version.reason, version.created_at)
                             for version in db.scalars(select(ResearchTaskAnalysisVersion).order_by(ResearchTaskAnalysisVersion.id)))
            return tasks, versions

    def _post(self, payload=None, task_id=None, version_id=None):
        """恢复不应调用大模型，即使误接模型调用也能在测试中发现。"""
        task_id = self.task_id if task_id is None else task_id
        version_id = self.version_id if version_id is None else version_id
        with (
            patch("paper_analysis_agent.generate_text") as analyze,
            patch("review_agent.generate_text") as review,
            patch("revision_agent.generate_text") as revise,
        ):
            response = client.post(f"/research-tasks/{task_id}/analysis/versions/{version_id}/restore",
                                   json=self.payload if payload is None else payload)
        analyze.assert_not_called()
        review.assert_not_called()
        revise.assert_not_called()
        return response

    def _assert_rejected_without_changes(self, status_code, **kwargs):
        before = self._snapshot()
        response = self._post(**kwargs)
        self.assertEqual(response.status_code, status_code, response.text)
        self.assertEqual(self._snapshot(), before)
        return response

    def test_restore_saves_new_history_clears_review_and_preserves_old_records(self):
        from sqlalchemy import select
        from model import ResearchTaskAnalysisVersion
        old_versions = self._snapshot()[1]
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["task_id"], self.task_id)
        self.assertEqual(result["restored_from_version_id"], self.version_id)
        self.assertNotIn(result["version_id"], [self.version_id, self.other_version_id])
        self.assertEqual(result["analysis"], self.before)
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            self.assertEqual(json.loads(task.result), self.before)
            self.assertIsNone(task.review_result)
            self.assertEqual(task.status, "completed")
            history = db.get(ResearchTaskAnalysisVersion, result["version_id"])
            self.assertEqual(history.previous_analysis, self.current_json)
            self.assertEqual(json.loads(history.updated_analysis), self.before)
            # 本次历史保存的是恢复前的当前审校，而不是所选旧版本的历史审校。
            self.assertEqual(history.previous_review, self.current_review_json)
            self.assertEqual(history.reason, f"恢复版本 {self.version_id} 的修改前分析")
            self.assertIsNotNone(history.created_at)
            self.assertEqual(len(db.scalars(select(ResearchTaskAnalysisVersion)).all()), 3)
        self.assertEqual(self._snapshot()[1][:2], old_versions)
        saved = client.get(f"/research-tasks/{self.task_id}/analysis")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), self.before)
        self.assertEqual(client.get(f"/research-tasks/{self.task_id}/review").status_code, 404)
        versions = client.get(f"/research-tasks/{self.task_id}/analysis/versions")
        self.assertEqual(versions.status_code, 200)
        self.assertEqual([row["id"] for row in versions.json()], [result["version_id"], self.version_id])
        detail = client.get(f"/research-tasks/{self.task_id}/analysis/versions/{result['version_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["previous_analysis"], self.current)
        self.assertEqual(detail.json()["updated_analysis"], self.before)
        self.assertEqual(detail.json()["previous_review"], json.loads(self.current_review_json))

    def test_restore_without_current_review_saves_null_in_history(self):
        from model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).review_result = None
            db.commit()
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        with TestingSessionLocal() as db:
            history = db.get(ResearchTaskAnalysisVersion, response.json()["version_id"])
            self.assertIsNone(history.previous_review)
            self.assertIsNone(db.get(ResearchTask, self.task_id).review_result)

    def test_stale_base_analysis_is_rejected_without_overwriting_new_content(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            current = json.loads(task.result)
            current["summary"] = "其他操作更新的内容"
            task.result = json.dumps(current)
            db.commit()
        response = self._assert_rejected_without_changes(409)
        self.assertIn("当前分析已变化", response.json()["detail"])

    def test_cannot_restore_version_owned_by_other_task(self):
        self._assert_rejected_without_changes(404, version_id=self.other_version_id)

    def test_missing_task_or_version_is_rejected_without_changes(self):
        for arguments in ({"task_id": 999999}, {"version_id": 999999}):
            with self.subTest(arguments=arguments):
                self._assert_rejected_without_changes(404, **arguments)

    def test_equal_target_is_rejected_without_creating_history(self):
        with TestingSessionLocal() as db:
            task = db.get(ResearchTask, self.task_id)
            task.result = self.historical_json
            db.commit()
        self._assert_rejected_without_changes(409, payload={"base_analysis": self.before})

    def test_repeated_restore_does_not_create_another_history(self):
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        # 第二次用旧请求会过期，用最新正文再次恢复同一目标则无需恢复。
        self._assert_rejected_without_changes(409)
        self._assert_rejected_without_changes(409, payload={"base_analysis": response.json()["analysis"]})

    def test_malformed_current_or_target_json_is_rejected_without_changes(self):
        from model import ResearchTaskAnalysisVersion
        for target in ("current", "historical"):
            with self.subTest(target=target):
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    version = db.get(ResearchTaskAnalysisVersion, self.version_id)
                    task.result = self.current_json
                    version.previous_analysis = self.historical_json
                    if target == "current":
                        task.result = "这不是JSON"
                    else:
                        version.previous_analysis = "这不是JSON"
                    db.commit()
                self._assert_rejected_without_changes(409)

    def test_embedded_task_id_mismatch_is_rejected_without_changes(self):
        from model import ResearchTaskAnalysisVersion
        for target in ("current", "historical"):
            with self.subTest(target=target):
                with TestingSessionLocal() as db:
                    task = db.get(ResearchTask, self.task_id)
                    version = db.get(ResearchTaskAnalysisVersion, self.version_id)
                    task.result = self.current_json
                    version.previous_analysis = self.historical_json
                    if target == "current":
                        data = json.loads(task.result)
                        data["task_id"] = self.other_id
                        task.result = json.dumps(data)
                    else:
                        data = json.loads(version.previous_analysis)
                        data["task_id"] = self.other_id
                        version.previous_analysis = json.dumps(data)
                    db.commit()
                self._assert_rejected_without_changes(409)

    def test_legacy_target_defaults_notes_without_rewriting_original_history(self):
        from model import ResearchTaskAnalysisVersion
        legacy = dict(self.before)
        legacy.pop("evidence_notes")
        legacy_json = json.dumps(legacy)
        with TestingSessionLocal() as db:
            db.get(ResearchTaskAnalysisVersion, self.version_id).previous_analysis = legacy_json
            db.commit()
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["analysis"], self.before)
        with TestingSessionLocal() as db:
            self.assertEqual(db.get(ResearchTaskAnalysisVersion, self.version_id).previous_analysis, legacy_json)

    def test_missing_current_analysis_is_rejected_without_changes(self):
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).result = None
            db.commit()
        self._assert_rejected_without_changes(409)

    def test_invalid_request_returns_422_without_changes(self):
        for payload in ({}, {"base_analysis": {"summary": "缺少其他字段"}}):
            with self.subTest(payload=payload):
                self._assert_rejected_without_changes(422, payload=payload)

    def test_commit_failure_rolls_back_new_history_and_current_analysis(self):
        from sqlalchemy.exc import SQLAlchemyError
        before = self._snapshot()
        failure_client = TestClient(app, raise_server_exceptions=False)
        # 在 history 已经 flush、任务待更新之后，模拟实际提交失败。
        with patch("sqlalchemy.orm.Session.commit", side_effect=SQLAlchemyError("模拟数据库提交失败")) as commit:
            response = failure_client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 500, response.text)
        commit.assert_called_once()
        self.assertEqual(self._snapshot(), before)
        # 回滚后同样的请求还能正常执行，不留下半条历史或失效会话。
        retry = self._post()
        self.assertEqual(retry.status_code, 200, retry.text)

    def test_locked_query_refreshes_preloaded_task_before_checking_request(self):
        import analysis_restore
        from schemas import AnalysisRestoreRequest
        with TestingSessionLocal() as cached_db:
            stale_task = cached_db.get(ResearchTask, self.task_id)
            self.assertEqual(stale_task.result, self.current_json)
            with TestingSessionLocal() as update_db:
                current = dict(self.current, summary="另一会话更新的概述")
                update_db.get(ResearchTask, self.task_id).result = json.dumps(current)
                update_db.commit()
            before = self._snapshot()
            with self.assertRaisesRegex(ValueError, "当前分析已变化"):
                analysis_restore.restore_analysis_version(
                    cached_db, self.task_id, self.version_id,
                    AnalysisRestoreRequest.model_validate(self.payload),
                )
            self.assertEqual(self._snapshot(), before)


class ComparisonDatabaseFixture:
    """两个论文、五份来源的共用环境；实际引用只使用资料1、3、5。"""

    def setUp(self):
        from model import ResearchTaskPaper
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        self.raw_texts = [
            "论文A通过语义相似度查找缓存。",
            "未引用的论文A片段，不应该进入模型输入。",
            "论文B通过字符串完全匹配查找缓存。",
            "未引用的论文B片段，不应该进入模型输入。",
            "论文A实验使用离线请求轨迹。",
        ]
        with TestingSessionLocal() as db:
            first = Paper(title="论文A", author="作者A", year=2025, notes="")
            second = Paper(title="论文B", author="作者B", year=2025, notes="")
            task = ResearchTask(title="对比测试", question="两篇论文的匹配机制有什么不同？", status="completed")
            other = ResearchTask(title="其他任务", question="其他研究问题", status="pending")
            db.add_all([first, second, task, other])
            db.flush()
            self.task_id, self.paper_ids = task.id, [first.id, second.id]
            db.add_all([ResearchTaskPaper(task_id=task.id, paper_id=paper.id, note="")
                        for paper in (first, second)])
            documents = [PaperDocument(paper_id=paper.id, original_name=f"comparison-{paper.id}.pdf",
                                      stored_name=f"comparison-{paper.id}.pdf", file_path="/unused/comparison.pdf",
                                      content_type="application/pdf", file_size=1) for paper in (first, second)]
            db.add_all(documents)
            db.flush()
            owners = [(0, 1), (0, 2), (1, 1), (1, 2), (0, 3)]
            chunks = [PaperChunk(document_id=documents[owner].id, page_number=page, chunk_index=0,
                                 text=text, char_count=len(text))
                      for (owner, page), text in zip(owners, self.raw_texts)]
            db.add_all(chunks)
            db.flush()
            self.chunk_id = chunks[0].id
            papers = [first, second]
            sources = [AnalysisSourceResponse(paper_id=papers[owner].id, paper_title=papers[owner].title,
                       chunk_id=chunk.id, page_number=page, score=0.9)
                       for (owner, page), chunk in zip(owners, chunks)]
            self.analysis = ResearchTaskAnalysisResponse(
                task_id=task.id, question=task.question,
                summary="模型整理的概述，不能冒充论文原文。[资料1]",
                methods=["模型整理的方法。[资料1]"],
                experiments=["离线评估。[资料5]"], conclusions=["完全匹配机制。[资料3]"],
                limitations=[], evidence_notes=[], sources=sources,
            )
            task.result = self.analysis.model_dump_json()
            task.review_result = ResearchTaskReviewResponse(task_id=task.id, items=[]).model_dump_json()
            db.commit()
        self.answer = dict(
            method_comparisons=[dict(text="A采用语义匹配，B采用完全匹配。[资料1][资料3]")],
            experiment_comparisons=[], conclusion_comparisons=[],
            evidence_notes=[dict(text="当前A原文仅描述离线轨迹。[资料5]")],
        )

    def _snapshot(self):
        from sqlalchemy import select
        from model import ResearchTaskAnalysisVersion
        with TestingSessionLocal() as db:
            tasks = tuple((task.id, task.title, task.question, task.result, task.review_result, task.status)
                          for task in db.scalars(select(ResearchTask).order_by(ResearchTask.id)))
            versions = tuple((version.id, version.task_id, version.previous_analysis, version.updated_analysis,
                              version.previous_review, version.reason, version.created_at)
                             for version in db.scalars(select(ResearchTaskAnalysisVersion).order_by(ResearchTaskAnalysisVersion.id)))
            return tasks, versions

    def _save_analysis(self, data):
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).result = json.dumps(data)
            db.commit()

    def _request(self, endpoint, status_code, task_id=None, answer=None):
        """接口之外只替换模型网络调用；所有数据库读取和引用检查照常执行。"""
        before = self._snapshot()
        task_id = self.task_id if task_id is None else task_id
        url = f"/research-tasks/{task_id}/{endpoint}"
        with patch("comparison_agent.generate_text", return_value=json.dumps(self.answer) if answer is None else answer) as generate:
            response = client.get(url) if endpoint == "comparison-context" else client.post(url)
        self.assertEqual(response.status_code, status_code, response.text)
        self.assertEqual(self._snapshot(), before)
        return response, generate


class TestComparisonContextAPI(ComparisonDatabaseFixture, unittest.TestCase):
    """原文分组不调用模型，按论文去重，并保留不连续的原资料编号。"""

    def test_grouping_deduplicates_raw_evidence_and_preserves_original_numbers(self):
        response, generate = self._request("comparison-context", 200)
        generate.assert_not_called()
        data = response.json()
        self.assertEqual(data["task_id"], self.task_id)
        self.assertEqual(data["question"], self.analysis.question)
        self.assertEqual(data["paper_ids"], self.paper_ids)
        self.assertEqual(data["source_numbers"], [1, 3, 5])
        context = data["context"]
        for number, position in ((1, 0), (3, 2), (5, 4)):
            self.assertEqual(context.count(f"[资料{number}]"), 1)
            self.assertIn(self.raw_texts[position], context)
        self.assertNotIn("[资料2]", context)
        self.assertNotIn("[资料4]", context)
        self.assertNotIn(self.raw_texts[1], context)
        self.assertNotIn(self.raw_texts[3], context)
        self.assertNotIn("模型整理的概述", context)
        first_section, second_section = context.split(f"=== 论文 {self.paper_ids[1]} ===")
        self.assertIn("[资料1]", first_section)
        self.assertIn("[资料5]", first_section)
        self.assertNotIn("[资料3]", first_section)
        self.assertIn("[资料3]", second_section)
        # 分组展示顺序可以是1、5、3，但原编号不改变。
        self.assertLess(context.index("[资料5]"), context.index("[资料3]"))

    def test_two_linked_papers_but_only_one_cited_paper_cannot_generate_report(self):
        analysis = self.analysis.model_dump()
        analysis["conclusions"] = []
        self._save_analysis(analysis)
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint):
                _, generate = self._request(endpoint, 409)
                generate.assert_not_called()

    def test_sources_without_citations_are_not_treated_as_comparison_evidence(self):
        analysis = self.analysis.model_dump()
        analysis.update(summary="没有引用的概述", methods=[], experiments=[], conclusions=[])
        self._save_analysis(analysis)
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint):
                _, generate = self._request(endpoint, 409)
                generate.assert_not_called()

    def test_deleted_chunk_prevents_model_call(self):
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id))
            db.commit()
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint):
                _, generate = self._request(endpoint, 409)
                generate.assert_not_called()

    def test_wrong_source_paper_is_rejected_before_model_call(self):
        analysis = self.analysis.model_dump()
        analysis["sources"][0]["paper_id"] = self.paper_ids[1]
        self._save_analysis(analysis)
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint):
                _, generate = self._request(endpoint, 409)
                generate.assert_not_called()

    def test_missing_task_or_saved_analysis_returns_404_without_model_call(self):
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint, missing="task"):
                _, generate = self._request(endpoint, 404, task_id=999999)
                generate.assert_not_called()
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).result = None
            db.commit()
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint, missing="analysis"):
                _, generate = self._request(endpoint, 404)
                generate.assert_not_called()

    def test_invalid_saved_analysis_is_rejected_before_model_call(self):
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).result = "这不是正确的JSON"
            db.commit()
        for endpoint in ("comparison-context", "comparison-report"):
            with self.subTest(endpoint=endpoint):
                _, generate = self._request(endpoint, 409)
                generate.assert_not_called()


class TestComparisonReportParsing(unittest.TestCase):
    """独立测试模型返回内容的结构、编号及不同论文的引用检查。"""

    def setUp(self):
        from schemas import ComparisonSourceResponse
        self.sources = [
            ComparisonSourceResponse(source_number=number, paper_id=paper_id, paper_title=f"论文{paper_id}",
                                     chunk_id=number + 100, page_number=number, score=0.9)
            for number, paper_id in ((1, 11), (3, 22), (5, 11))
        ]
        self.data = dict(
            method_comparisons=[dict(text="A与B采用不同匹配机制。[资料1][资料3]", source_numbers=[5])],
            experiment_comparisons=[], conclusion_comparisons=[],
            evidence_notes=[dict(text="当前A片段未说明在线实验。[资料5]")],
        )

    def _parse(self, data=None, text=None, sources=None):
        import comparison_agent
        return comparison_agent.parse_comparison_response(
            json.dumps(self.data if data is None else data) if text is None else text,
            task_id=7, question="研究问题",
            sources=self.sources if sources is None else sources,
        )

    def _copy_data(self):
        return json.loads(json.dumps(self.data))

    def test_valid_report_uses_program_metadata_and_extracts_actual_citations(self):
        data = self._copy_data()
        data.update(task_id=999, question="模型编造的问题", paper_ids=[999], sources=[])
        report = self._parse(data=data)
        self.assertEqual(report.task_id, 7)
        self.assertEqual(report.question, "研究问题")
        self.assertEqual(report.paper_ids, [11, 22])
        self.assertEqual(report.sources, self.sources)
        self.assertEqual(report.method_comparisons[0].source_numbers, [1, 3])
        self.assertEqual(report.evidence_notes[0].source_numbers, [5])

    def test_json_code_block_is_accepted(self):
        report = self._parse(text="```json\n" + json.dumps(self.data) + "\n```")
        self.assertEqual(report.method_comparisons[0].source_numbers, [1, 3])

    def test_invalid_json_or_non_object_is_rejected(self):
        for text in ("not JSON", "[]", "null", "123"):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                self._parse(text=text)

    def test_missing_field_or_wrong_field_types_are_rejected(self):
        for name in ("method_comparisons", "experiment_comparisons", "conclusion_comparisons", "evidence_notes"):
            for value in (None, "错误字符串", ["不是正文对象"]):
                with self.subTest(name=name, value=value):
                    data = self._copy_data()
                    data[name] = value
                    with self.assertRaises(RuntimeError):
                        self._parse(data=data)
        data = self._copy_data()
        data.pop("evidence_notes")
        with self.assertRaises(RuntimeError):
            self._parse(data=data)

    def test_completely_empty_report_is_rejected(self):
        data = {name: [] for name in self.data}
        with self.assertRaisesRegex(RuntimeError, "空报告"):
            self._parse(data=data)

    def test_empty_or_whitespace_text_is_rejected(self):
        for text in ("", " \n\t "):
            with self.subTest(text=text):
                data = self._copy_data()
                data["method_comparisons"][0]["text"] = text
                with self.assertRaises(RuntimeError):
                    self._parse(data=data)

    def test_comparison_and_notes_both_require_citations(self):
        for field in ("method_comparisons", "evidence_notes"):
            with self.subTest(field=field):
                data = self._copy_data()
                data[field][0]["text"] = "没有引用的正文"
                with self.assertRaisesRegex(RuntimeError, "必须附上资料引用"):
                    self._parse(data=data)

    def test_bad_reference_syntax_or_out_of_range_number_is_rejected(self):
        for marker in ("[资料abc]", "[资料1", "[资料0]", "[资料9]"):
            with self.subTest(marker=marker):
                data = self._copy_data()
                data["method_comparisons"][0]["text"] = "正文" + marker
                with self.assertRaisesRegex(RuntimeError, "引用不合法"):
                    self._parse(data=data)

    def test_number_inside_range_but_not_provided_is_rejected(self):
        data = self._copy_data()
        data["method_comparisons"][0]["text"] = "错误引用。[资料1][资料2]"
        with self.assertRaisesRegex(RuntimeError, "本次未提供的资料：2"):
            self._parse(data=data)

    def test_two_sources_from_same_paper_do_not_count_as_two_papers(self):
        data = self._copy_data()
        data["method_comparisons"][0]["text"] = "同一论文的两份资料。[资料1][资料5]"
        with self.assertRaisesRegex(RuntimeError, "至少两篇不同论文"):
            self._parse(data=data)

    def test_all_comparison_categories_require_two_papers(self):
        for field in ("method_comparisons", "experiment_comparisons", "conclusion_comparisons"):
            with self.subTest(field=field):
                data = {name: [] for name in self.data}
                data[field] = [dict(text="单篇描述。[资料1]")]
                with self.assertRaisesRegex(RuntimeError, "至少两篇不同论文"):
                    self._parse(data=data)

    def test_notes_only_can_reference_one_paper_and_allow_empty_comparisons(self):
        data = {name: [] for name in self.data}
        data["evidence_notes"] = [dict(text="当前片段不足以比较。[资料1]")]
        report = self._parse(data=data)
        self.assertEqual(report.method_comparisons, [])
        self.assertEqual(report.evidence_notes[0].source_numbers, [1])

    def test_missing_sources_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "没有可用于检查报告引用的资料"):
            self._parse(sources=[])

    def test_citations_are_deduplicated_in_text_order_and_text_is_trimmed(self):
        data = self._copy_data()
        data["method_comparisons"][0]["text"] = " \n对比。[资料3][资料1][资料3] \n"
        report = self._parse(data=data)
        self.assertEqual(report.method_comparisons[0].source_numbers, [3, 1])
        self.assertEqual(report.method_comparisons[0].text, "对比。[资料3][资料1][资料3]")


class TestComparisonReportAPI(ComparisonDatabaseFixture, unittest.TestCase):
    """真实取证、提示词、解析和接口串联，仅替换DeepSeek网络请求。"""

    def test_success_reads_real_evidence_preserves_sources_and_does_not_save(self):
        response, generate = self._request("comparison-report", 200)
        generate.assert_called_once()
        data = response.json()
        self.assertEqual(data["task_id"], self.task_id)
        self.assertEqual(data["question"], self.analysis.question)
        self.assertEqual(data["paper_ids"], self.paper_ids)
        self.assertEqual(data["method_comparisons"][0]["source_numbers"], [1, 3])
        self.assertEqual(data["evidence_notes"][0]["source_numbers"], [5])
        self.assertEqual([source["source_number"] for source in data["sources"]], [1, 3, 5])
        for number, source in zip((1, 3, 5), data["sources"]):
            expected = self.analysis.sources[number - 1].model_dump()
            expected["source_number"] = number
            self.assertEqual(source, expected)
        prompt = generate.call_args.kwargs["user_prompt"]
        self.assertIn(self.analysis.question, prompt)
        for position in (0, 2, 4):
            self.assertIn(self.raw_texts[position], prompt)
        for position in (1, 3):
            self.assertNotIn(self.raw_texts[position], prompt)
        self.assertNotIn("模型整理的概述", prompt)
        self.assertEqual(prompt.count("[资料1]"), 1)
        rules = generate.call_args.kwargs["system_prompt"]
        self.assertIn("条件不同，不能直接判断某方法更好", rules)
        self.assertIn("保留原编号", rules)

    def test_model_error_returns_502_without_changing_analysis_or_history(self):
        before = self._snapshot()
        with patch("comparison_agent.generate_text", side_effect=RuntimeError("模拟模型连接失败")):
            response = client.post(f"/research-tasks/{self.task_id}/comparison-report")
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"], "模拟模型连接失败")
        self.assertEqual(self._snapshot(), before)

    def test_invalid_model_reports_return_502_without_saving(self):
        bad_number = json.loads(json.dumps(self.answer))
        bad_number["method_comparisons"][0]["text"] = "未提供的引用。[资料1][资料2]"
        same_paper = json.loads(json.dumps(self.answer))
        same_paper["method_comparisons"][0]["text"] = "只有一篇论文。[资料1][资料5]"
        for answer in ("not JSON", json.dumps(bad_number), json.dumps(same_paper)):
            with self.subTest(answer=answer):
                _, generate = self._request("comparison-report", 502, answer=answer)
                self.assertEqual(generate.call_count, 2)

    def test_empty_model_report_returns_502(self):
        answer = json.dumps({name: [] for name in self.answer})
        self._request("comparison-report", 502, answer=answer)

    def test_notes_only_report_is_returned_as_insufficient_comparison_evidence(self):
        data = {name: [] for name in self.answer}
        data["evidence_notes"] = [dict(text="当前资料不足以比较在线实验。[资料1][资料3]")]
        response, generate = self._request("comparison-report", 200, answer=json.dumps(data))
        generate.assert_called_once()
        self.assertEqual(response.json()["method_comparisons"], [])
        self.assertEqual(response.json()["evidence_notes"][0]["source_numbers"], [1, 3])

    def test_each_request_generates_a_new_preview_without_modifying_saved_analysis(self):
        second = json.loads(json.dumps(self.answer))
        second["method_comparisons"][0]["text"] = "第二次生成的机制对比。[资料1][资料3]"
        before = self._snapshot()
        with patch("comparison_agent.generate_text", side_effect=[json.dumps(self.answer), json.dumps(second)]) as generate:
            first = client.post(f"/research-tasks/{self.task_id}/comparison-report")
            another = client.post(f"/research-tasks/{self.task_id}/comparison-report")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(another.status_code, 200, another.text)
        self.assertEqual(generate.call_count, 2)
        self.assertNotEqual(first.json()["method_comparisons"], another.json()["method_comparisons"])
        self.assertEqual(self._snapshot(), before)


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
            patch("semantic_search.task_crud.get_task_chunk_embeddings", return_value=self._candidates()) as load,
            patch("semantic_search.embed_texts", return_value=[[1.0, 0.0]]),
        ):
            result = semantic_search.search_task_chunks_semantic(
                Mock(), task_id=6, question="方法", top_k=1, paper_id=3,
            )
        self.assertEqual([paper.id for _, paper, _ in result], [3])
        self.assertAlmostEqual(result[0][2], 0.6)
        self.assertEqual(load.call_args.args[1], 6)

    def test_omitting_paper_filter_keeps_global_search(self):
        with (
            patch("semantic_search.task_crud.get_task_chunk_embeddings", return_value=self._candidates()),
            patch("semantic_search.embed_texts", return_value=[[1.0, 0.0]]),
        ):
            result = semantic_search.search_task_chunks_semantic(
                Mock(), task_id=6, question="方法", top_k=1,
            )
        self.assertEqual(result[0][1].id, 8)

    def test_unlinked_paper_returns_empty_without_embedding_call(self):
        with (
            patch("semantic_search.task_crud.get_task_chunk_embeddings", return_value=self._candidates()),
            patch("semantic_search.embed_texts") as embed,
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
            patch("paper_analysis_agent.task_crud.get_task_paper_links", return_value=links),
            patch("paper_analysis_agent.query_rewriter.rewrite_query_for_search", return_value="改写问题") as rewrite,
            patch("paper_analysis_agent.semantic_search.search_task_chunks_semantic", side_effect=search) as retrieve,
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
            patch("paper_analysis_agent.task_crud.get_task_paper_links", return_value=[]),
            patch("paper_analysis_agent.query_rewriter.rewrite_query_for_search") as rewrite,
            patch("paper_analysis_agent.semantic_search.search_task_chunks_semantic") as retrieve,
        ):
            with self.assertRaisesRegex(ValueError, "还没有关联论文"):
                paper_analysis_agent.collect_analysis_matches(Mock(), 6, "研究问题")
        rewrite.assert_not_called()
        retrieve.assert_not_called()

    def test_missing_vectors_for_one_paper_fails_instead_of_skipping_it(self):
        with (
            patch("paper_analysis_agent.task_crud.get_task_paper_links", return_value=[SimpleNamespace(paper_id=3), SimpleNamespace(paper_id=8)]),
            patch("paper_analysis_agent.query_rewriter.rewrite_query_for_search", return_value="研究问题"),
            patch("paper_analysis_agent.semantic_search.search_task_chunks_semantic", side_effect=lambda **kw: [] if kw["paper_id"] == 8 else [(SimpleNamespace(id=1), SimpleNamespace(id=3), 0.9)]),
        ):
            with self.assertRaisesRegex(ValueError, "论文 8.*可检索向量"):
                paper_analysis_agent.collect_analysis_matches(Mock(), 6, "研究问题")


class TestComparisonReviewAPI(ComparisonDatabaseFixture, unittest.TestCase):
    """真实数据库和引用映射，只模拟审校模型网络请求。"""

    def _report_payload(self):
        with patch("comparison_agent.generate_text", return_value=json.dumps(self.answer)):
            response = client.post(f"/research-tasks/{self.task_id}/comparison-report")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _review(self, payload, model=None, task_id=None):
        # 两条陈述分别引用 1/3 和 5，编号有空缺，不可按报告列表位置取证。
        responses = [
            json.dumps(dict(verdict="supported", reason="原文分别支持两种匹配机制。", source_numbers=[1, 3])),
            json.dumps(dict(verdict="supported", reason="原文说明使用离线请求轨迹。", source_numbers=[5])),
        ]
        before = self._snapshot()
        with patch("review_agent.generate_text", side_effect=responses if model is None else model) as generate:
            response = client.post(
                f"/research-tasks/{self.task_id if task_id is None else task_id}/comparison-report/review",
                json=payload,
            )
        self.assertEqual(self._snapshot(), before)
        return response, generate

    def test_review_uses_actual_raw_text_and_original_sparse_numbers(self):
        response, generate = self._review(self._report_payload())
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertEqual([item["field"] for item in items], ["method_comparisons", "evidence_notes"])
        self.assertEqual([item["source_numbers"] for item in items], [[1, 3], [5]])
        self.assertEqual(generate.call_count, 2)
        first_prompt = generate.call_args_list[0].kwargs["user_prompt"]
        second_prompt = generate.call_args_list[1].kwargs["user_prompt"]
        self.assertIn(self.raw_texts[0], first_prompt)
        self.assertIn(self.raw_texts[2], first_prompt)
        self.assertIn(f"论文编号：{self.paper_ids[0]}", first_prompt)
        self.assertIn(f"论文编号：{self.paper_ids[1]}", first_prompt)
        self.assertNotIn(self.raw_texts[1], first_prompt)
        self.assertNotIn("模型整理的概述", first_prompt)
        self.assertIn(self.raw_texts[4], second_prompt)
        self.assertIn("[资料5]", second_prompt)
        self.assertNotIn(self.raw_texts[0], second_prompt)
        rules = generate.call_args_list[0].kwargs["system_prompt"]
        self.assertIn("数字、单位、参数名称", rules)
        self.assertIn("缓存容量和候选召回数Top-K是不同参数", rules)
        self.assertIn("不能用其他片段中的数字补全", rules)

    def test_source_numbers_are_derived_from_text_instead_of_trusting_request(self):
        payload = self._report_payload()
        payload["method_comparisons"][0]["source_numbers"] = [999]
        response, generate = self._review(payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["source_numbers"], [1, 3])
        self.assertNotIn("资料999", generate.call_args_list[0].kwargs["user_prompt"])

    def test_mismatched_identity_and_source_metadata_fail_before_model(self):
        base = self._report_payload()
        variants = []
        for key, value in (("task_id", 999), ("question", "其他问题"), ("paper_ids", [999])):
            payload = json.loads(json.dumps(base)); payload[key] = value; variants.append(payload)
        for key in ("chunk_id", "paper_id", "page_number", "source_number"):
            payload = json.loads(json.dumps(base)); payload["sources"][0][key] = 999; variants.append(payload)
        missing = json.loads(json.dumps(base)); missing["sources"].pop(); variants.append(missing)
        duplicate = json.loads(json.dumps(base)); duplicate["sources"].append(duplicate["sources"][0]); variants.append(duplicate)
        for payload in variants:
            with self.subTest(payload=payload):
                response, generate = self._review(payload)
                self.assertEqual(response.status_code, 409, response.text)
                generate.assert_not_called()

    def test_invalid_report_citations_and_empty_report_fail_before_model(self):
        base = self._report_payload()
        for text in ("对比。[资料2][资料3]", "对比。[资料1]", "对比。[资料99]", "对比。[资料", "没有引用"):
            payload = json.loads(json.dumps(base)); payload["method_comparisons"][0]["text"] = text
            response, generate = self._review(payload)
            self.assertEqual(response.status_code, 409, response.text)
            generate.assert_not_called()
        payload = json.loads(json.dumps(base))
        payload["method_comparisons"] = []; payload["evidence_notes"] = []
        response, generate = self._review(payload)
        self.assertEqual(response.status_code, 409, response.text)
        generate.assert_not_called()

    def test_deleted_chunk_fails_before_model(self):
        payload = self._report_payload()
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id)); db.commit()
        response, generate = self._review(payload)
        self.assertEqual(response.status_code, 409, response.text)
        generate.assert_not_called()

    def test_missing_task_or_analysis_returns_404_without_model(self):
        payload = self._report_payload()
        response, generate = self._review(payload, task_id=999)
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()
        with TestingSessionLocal() as db:
            db.get(ResearchTask, self.task_id).result = None; db.commit()
        response, generate = self._review(payload)
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()

    def test_model_failures_and_invalid_review_return_502(self):
        payload = self._report_payload()
        responses = [
            RuntimeError("模型服务不可用"),
            ["不是JSON", "仍不是JSON"],
            [json.dumps(dict(verdict="supported", reason="支持", source_numbers=[999]))] * 2,
        ]
        for model in responses:
            with self.subTest(model=model):
                response, _ = self._review(payload, model=model)
                self.assertEqual(response.status_code, 502, response.text)

    def test_partially_supported_verdict_and_reason_are_returned_without_rewriting(self):
        payload = self._report_payload()
        payload["method_comparisons"][0]["text"] = "A通过语义匹配且Top-K为500，B通过完全匹配。[资料1][资料3]"
        reason = "匹配机制有依据，但引用原文没有Top-K的数值。"
        model = [
            json.dumps(dict(verdict="partially_supported", reason=reason, source_numbers=[1, 3])),
            json.dumps(dict(verdict="supported", reason="离线轨迹有依据", source_numbers=[5])),
        ]
        response, _ = self._review(payload, model=model)
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["verdict"], "partially_supported")
        self.assertEqual(item["reason"], reason)
        self.assertEqual(item["claim"], payload["method_comparisons"][0]["text"])


class TestStructuredJSONOutput(unittest.TestCase):
    def test_invalid_json_retries_once_with_feedback_and_json_mode(self):
        from structured_output import generate_validated_json
        def parse(text):
            try:
                return json.loads(text)
            except ValueError as error:
                raise RuntimeError("JSON语法错误") from error
        generate = Mock(side_effect=["bad", '{"ok": true}'])
        result = generate_validated_json(generate, parse, "返回JSON", "问题")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(generate.call_count, 2)
        self.assertTrue(all(call.kwargs["json_mode"] for call in generate.call_args_list))
        self.assertIn("JSON语法错误", generate.call_args_list[1].kwargs["user_prompt"])

    def test_validation_failure_stops_after_two_attempts(self):
        from structured_output import generate_validated_json
        generate = Mock(return_value="invalid")
        with self.assertRaisesRegex(RuntimeError, "已尝试2次"):
            generate_validated_json(generate, Mock(side_effect=RuntimeError("字段错误")), "JSON", "问题")
        self.assertEqual(generate.call_count, 2)

    def test_network_error_is_not_retried_by_validation_layer(self):
        from structured_output import generate_validated_json
        generate = Mock(side_effect=RuntimeError("网络失败"))
        with self.assertRaisesRegex(RuntimeError, "网络失败"):
            generate_validated_json(generate, Mock(), "JSON", "问题")
        generate.assert_called_once()

    def test_empty_or_truncated_output_retries(self):
        from structured_output import generate_validated_json
        from llm_service import ModelOutputError
        for message in ("回答为空", "回答被截断"):
            generate = Mock(side_effect=[ModelOutputError(message), "{}"])
            self.assertEqual(generate_validated_json(generate, json.loads, "JSON", "问题"), {})
            self.assertEqual(generate.call_count, 2)

    def test_sdk_json_mode_sets_response_format_and_limits_tokens(self):
        import llm_service
        sdk = Mock()
        sdk.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content='{"ok": true}'),
        )])
        with patch("llm_service.OpenAI", return_value=sdk), patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-only"}):
            self.assertEqual(llm_service.generate_text("返回JSON", "问题", json_mode=True), '{"ok": true}')
            options = sdk.chat.completions.create.call_args.kwargs
            self.assertEqual(options["response_format"], {"type": "json_object"})
            self.assertEqual(options["max_tokens"], 8192)
            llm_service.generate_text("回答问题", "问题")
            self.assertNotIn("response_format", sdk.chat.completions.create.call_args.kwargs)

    def test_sdk_empty_and_truncated_outputs_are_retryable_errors(self):
        import llm_service
        sdk = Mock()
        with patch("llm_service.OpenAI", return_value=sdk), patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-only"}):
            for reason, content in (("length", '{"truncated":'), ("stop", "")):
                sdk.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason=reason, message=SimpleNamespace(content=content),
                )])
                with self.assertRaises(llm_service.ModelOutputError):
                    llm_service.generate_text("JSON", "问题", json_mode=True)

    def test_review_retries_bad_json_then_checks_citations(self):
        claim = ReviewClaim("experiments", "成本下降。[资料3]", [3], [ReviewEvidence(3, "论文", 1, 1, "成本下降。")])
        answer = json.dumps(dict(verdict="supported", reason="原文支持", source_numbers=[3]))
        with patch("review_agent.generate_text", side_effect=["bad", answer]) as generate:
            result = review_agent.review_claim(claim)
        self.assertEqual(result.verdict, "supported")
        self.assertEqual(generate.call_count, 2)


class TestCheckedComparisonWorkflow(ComparisonDatabaseFixture, unittest.TestCase):
    def _payload(self):
        with patch("comparison_agent.generate_text", return_value=json.dumps(self.answer)):
            response = client.post(f"/research-tasks/{self.task_id}/comparison-report")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _reviews(self, first_verdict="supported"):
        return [
            json.dumps(dict(verdict=first_verdict, reason="检查方法原文", source_numbers=[1, 3])),
            json.dumps(dict(verdict="supported", reason="离线轨迹原文支持", source_numbers=[5])),
        ]

    def _post(self, payload, reviews, corrections=None):
        before = self._snapshot()
        with (
            patch("review_agent.generate_text", side_effect=reviews) as reviewer,
            patch("comparison_workflow.generate_text", side_effect=corrections or []) as corrector,
            patch("comparison_agent.generate_text", return_value=json.dumps(self.answer)) as generator,
        ):
            response = client.post(f"/research-tasks/{self.task_id}/comparison-report/checked", json=payload)
        self.assertEqual(self._snapshot(), before)
        return response, reviewer, corrector, generator

    def test_supported_report_passes_without_correction(self):
        response, reviewer, corrector, generator = self._post(self._payload(), self._reviews())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["passed"])
        self.assertEqual(response.json()["correction_rounds"], 0)
        self.assertEqual(reviewer.call_count, 2)
        corrector.assert_not_called(); generator.assert_not_called()

    def test_no_report_generates_then_reviews(self):
        response, _, corrector, generator = self._post(None, self._reviews())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["passed"])
        generator.assert_called_once(); corrector.assert_not_called()

    def test_bad_claim_is_corrected_and_rechecked_before_passing(self):
        payload = self._payload()
        payload["method_comparisons"][0]["text"] = "A使用语义匹配且Top-K为500，B使用完全匹配。[资料1][资料3]"
        responses = self._reviews("partially_supported") + self._reviews()
        response, reviewer, corrector, _ = self._post(payload, responses, [json.dumps(self.answer)])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["passed"])
        self.assertEqual(result["correction_rounds"], 1)
        self.assertEqual(result["initial_review"]["items"][0]["verdict"], "partially_supported")
        self.assertNotIn("Top-K为500", result["report"]["method_comparisons"][0]["text"])
        self.assertEqual(reviewer.call_count, 4)
        prompt = corrector.call_args.kwargs["user_prompt"]
        self.assertIn(self.raw_texts[0], prompt)
        self.assertIn(self.raw_texts[2], prompt)
        self.assertIn("partially_supported", prompt)

    def test_repeated_unsupported_results_are_not_marked_passed(self):
        response, reviewer, corrector, _ = self._post(
            self._payload(), self._reviews("partially_supported") * 3,
            [json.dumps(self.answer)] * 2,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["passed"])
        self.assertEqual(response.json()["correction_rounds"], 2)
        self.assertEqual(corrector.call_count, 2)
        self.assertEqual(reviewer.call_count, 6)

    def test_invalid_correction_json_is_retried_and_rechecked(self):
        response, _, corrector, _ = self._post(
            self._payload(), self._reviews("partially_supported") + self._reviews(),
            ["bad JSON", json.dumps(self.answer)],
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["passed"])
        self.assertEqual(corrector.call_count, 2)

    def test_invalid_correction_is_rejected_without_rechecking_or_saving(self):
        data = json.loads(json.dumps(self.answer))
        data["method_comparisons"][0]["text"] = "捏造引用。[资料99]"
        response, reviewer, corrector, _ = self._post(
            self._payload(), self._reviews("partially_supported"), [json.dumps(data)] * 2,
        )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(corrector.call_count, 2)
        self.assertEqual(reviewer.call_count, 2)

    def test_correction_cannot_expand_empty_comparison_dimensions(self):
        data = json.loads(json.dumps(self.answer))
        data["experiment_comparisons"] = [dict(text="新增加的实验比较。[资料1][资料3]")]
        response, _, corrector, _ = self._post(
            self._payload(), self._reviews("partially_supported"), [json.dumps(data)] * 2,
        )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("不得新增", response.json()["detail"])
        self.assertEqual(corrector.call_count, 2)

    def test_correction_cannot_remove_already_supported_note(self):
        data = json.loads(json.dumps(self.answer)); data["evidence_notes"] = []
        response, _, _, _ = self._post(
            self._payload(), self._reviews("partially_supported"), [json.dumps(data)] * 2,
        )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("不得改写或删除已通过", response.json()["detail"])

    def test_stale_sources_fail_before_model_calls(self):
        payload = self._payload(); payload["sources"][0]["chunk_id"] = 999
        response, reviewer, corrector, generator = self._post(payload, [])
        self.assertEqual(response.status_code, 409, response.text)
        reviewer.assert_not_called(); corrector.assert_not_called(); generator.assert_not_called()

    def test_generation_bad_json_retries_then_reviews(self):
        before = self._snapshot()
        with (
            patch("comparison_agent.generate_text", side_effect=["invalid JSON", json.dumps(self.answer)]) as generate,
            patch("review_agent.generate_text", side_effect=self._reviews()),
        ):
            response = client.post(f"/research-tasks/{self.task_id}/comparison-report/checked")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["passed"])
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(self._snapshot(), before)


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
        import chunk_crud
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
        import chunk_crud
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
        with patch("chunk_crud.get_chunks_by_document_id", return_value=[]):
            with self.assertRaisesRegex(ValueError, "已经发生变化"):
                self._read(55)


class TestEvidenceSupplement(ComparisonDatabaseFixture, unittest.TestCase):
    """真实 SQLite 证据读取、相邻块补充及编号管理；模型调用单独模拟。"""

    def setUp(self):
        super().setUp()
        with TestingSessionLocal() as db:
            anchor = db.get(PaperChunk, self.analysis.sources[4].chunk_id)
            extra = PaperChunk(document_id=anchor.document_id, page_number=4, chunk_index=0,
                               text="候选召回数Top-K默认设置为10。", char_count=21)
            db.add(extra); db.flush()
            self.extra_id = extra.id
            db.commit()

    def _registry(self, db):
        from evidence_supplement import build_evidence_registry
        return build_evidence_registry(db, self.analysis)

    def _claim(self, registry, numbers=(5,)):
        evidence = [registry[self.analysis.sources[n - 1].chunk_id] for n in numbers]
        text = "候选召回数Top-K默认设置为500。" + "".join(f"[资料{n}]" for n in numbers)
        return ReviewClaim("experiments", text, list(numbers), evidence)

    def test_registry_preserves_all_original_numbers_and_real_text(self):
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            self.assertEqual(len(registry), 5)
            for number, source in enumerate(self.analysis.sources, 1):
                evidence = registry[source.chunk_id]
                self.assertEqual(evidence.source_number, number)
                self.assertEqual(evidence.text, self.raw_texts[number - 1])
                self.assertEqual(evidence.paper_id, source.paper_id)
                self.assertEqual(evidence.page_number, source.page_number)

    def test_registry_rejects_duplicate_and_missing_chunks(self):
        from evidence_supplement import build_evidence_registry
        duplicate = self.analysis.model_copy(deep=True)
        duplicate.sources.append(duplicate.sources[0])
        with TestingSessionLocal() as db:
            with self.assertRaisesRegex(ValueError, "重复文本块"):
                build_evidence_registry(db, duplicate)
            db.delete(db.get(PaperChunk, self.chunk_id)); db.commit()
            with self.assertRaisesRegex(ValueError, "不存在"):
                self._registry(db)

    def test_expansion_reuses_old_number_and_assigns_new_number_after_all_sources(self):
        from evidence_supplement import supplement_claim_evidence
        before = self._snapshot()
        original_analysis = self.analysis.model_dump()
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            claim = self._claim(registry)
            expanded = supplement_claim_evidence(db, claim, registry, max_new_chunks=2)
            self.assertEqual(expanded.source_numbers, [5, 2, 6])
            self.assertEqual(expanded.evidence[-1].chunk_id, self.extra_id)
            self.assertEqual(expanded.evidence[-1].text, "候选召回数Top-K默认设置为10。")
            self.assertEqual(expanded.evidence[-1].page_number, 4)
            self.assertTrue(all(e.paper_id == self.paper_ids[0] for e in expanded.evidence))
            self.assertEqual(claim.source_numbers, [5])
            self.assertEqual(len(claim.evidence), 1)
            self.assertEqual(expanded.claim, claim.claim)
            self.assertIsNot(expanded.evidence, claim.evidence)
        self.assertEqual(self.analysis.model_dump(), original_analysis)
        self.assertEqual(self._snapshot(), before)

    def test_shared_registry_reuses_new_number_across_claims(self):
        from evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            first = supplement_claim_evidence(db, self._claim(registry), registry)
            second = supplement_claim_evidence(db, self._claim(registry), registry)
            self.assertEqual(first.source_numbers, second.source_numbers)
            self.assertEqual(registry[self.extra_id].source_number, 6)
            self.assertEqual(len(registry), 6)

    def test_overlapping_neighbors_are_deduplicated(self):
        from evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            expanded = supplement_claim_evidence(db, self._claim(registry, (1, 5)), registry)
            self.assertEqual(expanded.source_numbers, [1, 5, 2, 6])
            ids = [e.chunk_id for e in expanded.evidence]
            self.assertEqual(len(ids), len(set(ids)))

    def test_budget_counts_existing_registry_neighbors_as_added_to_claim(self):
        from evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            expanded = supplement_claim_evidence(db, self._claim(registry), registry, max_new_chunks=1)
            self.assertEqual(expanded.source_numbers, [5, 2])
            self.assertNotIn(self.extra_id, registry)

    def test_zero_budget_and_empty_claim_do_not_query_neighbors(self):
        from evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db, patch("evidence_supplement.get_neighbor_chunks") as neighbors:
            registry = self._registry(db)
            claim = self._claim(registry)
            expanded = supplement_claim_evidence(db, claim, registry, max_new_chunks=0)
            self.assertEqual(expanded, claim)
            empty = ReviewClaim("experiments", "没有引用", [], [])
            self.assertEqual(supplement_claim_evidence(db, empty, {}), empty)
            neighbors.assert_not_called()

    def test_invalid_budget_fails_before_neighbor_query(self):
        from evidence_supplement import supplement_claim_evidence
        for count in (-1, 5, True, False, 1.5, "2", None):
            with self.subTest(count=count), patch("evidence_supplement.get_neighbor_chunks") as neighbors:
                with self.assertRaises(ValueError):
                    supplement_claim_evidence(Mock(), ReviewClaim("experiments", "", [], []), {}, count)
                neighbors.assert_not_called()

    def test_mismatched_evidence_rejected_before_registry_mutation(self):
        from dataclasses import replace
        from evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            snapshot = dict(registry)
            claim = self._claim(registry)
            forged = replace(claim.evidence[0], text="被修改的原文")
            bad_claim = replace(claim, evidence=[forged])
            with patch("evidence_supplement.get_neighbor_chunks") as neighbors:
                with self.assertRaisesRegex(ValueError, "不一致"):
                    supplement_claim_evidence(db, bad_claim, registry)
                neighbors.assert_not_called()
            self.assertEqual(registry, snapshot)

    def test_reviewer_receives_new_raw_text_and_accepts_new_source_number(self):
        from evidence_supplement import supplement_claim_evidence
        with TestingSessionLocal() as db:
            registry = self._registry(db)
            expanded = supplement_claim_evidence(db, self._claim(registry), registry)
            answer = json.dumps(dict(verdict="contradicted", reason="新原文默认值是10，不是500。", source_numbers=[6]))
            with patch("review_agent.generate_text", return_value=answer) as generate:
                result = review_agent.review_claim(expanded)
        self.assertEqual(result.verdict, "contradicted")
        self.assertEqual(result.source_numbers, [6])
        prompt = generate.call_args.kwargs["user_prompt"]
        self.assertIn("[资料6]", prompt)
        self.assertIn("候选召回数Top-K默认设置为10。", prompt)
        self.assertIn("页码：4", prompt)


class TestComparisonEvidenceAssembly(ComparisonDatabaseFixture, unittest.TestCase):
    """验证补证结果组装与完整内部链路；模型网络调用全部模拟。"""

    def setUp(self):
        super().setUp()
        import comparison_report_builder as builder
        from evidence_supplement import build_evidence_registry
        from comparison_evidence_workflow import ComparisonEvidenceResult
        self.builder = builder
        with TestingSessionLocal() as db:
            registry = build_evidence_registry(db, self.analysis)
        # 故意使用不连续编号23：不能用证据数量代替编号上限。
        self.new_evidence = ReviewEvidence(23, "论文A", 9001, 4,
                                          "候选召回数Top-K默认设置为10。", self.paper_ids[0])
        registry[9001] = self.new_evidence
        self.result = ComparisonEvidenceResult(self.task_id, [], dict(reversed(list(registry.items()))))
        self.original_text = "A采用语义匹配，B采用完全匹配。[资料1][资料3]"
        self.result.items.append(self._item(self.original_text, [1, 3]))
        # 所有测试必须显式模拟模型；意外外部请求直接失败。
        for target in ("review_agent.generate_text", "revision_agent.generate_text"):
            guard = patch(target, side_effect=AssertionError("测试禁止真实模型请求"))
            guard.start()
            self.addCleanup(guard.stop)

    def _claim(self, text, numbers, field="method_comparisons"):
        by_number = {e.source_number: e for e in self.result.evidence_by_chunk_id.values()}
        return ReviewClaim(field, text, numbers, [by_number[n] for n in numbers])

    def _review(self, claim, verdict="supported"):
        return ReviewItemResponse(field=claim.field, claim=claim.claim, verdict=verdict,
                                  reason="测试审校意见", source_numbers=claim.source_numbers)

    def _item(self, text, numbers, verdict="supported", field="method_comparisons"):
        from comparison_evidence_workflow import ComparisonClaimResult
        from evidence_review_workflow import EvidenceReviewResult
        claim = self._claim(text, numbers, field)
        review = self._review(claim, verdict)
        return ComparisonClaimResult(EvidenceReviewResult(claim, review, review, []), None)

    def _add_revision(self, verdict="supported", text=None, numbers=None):
        from comparison_evidence_workflow import ComparisonClaimResult
        from evidence_review_workflow import EvidenceRevisionResult
        from schemas import AnalysisRevisionResponse
        original = self.result.items[0]
        claim = self._claim(text or "A的Top-K为10，B使用完全匹配。[资料23][资料3]",
                            [23, 3] if numbers is None else numbers)
        revision = AnalysisRevisionResponse(field=claim.field, original_claim=self.original_text,
                                            revised_claim=claim.claim, reason="按新增原文修正",
                                            source_numbers=claim.source_numbers)
        self.result.items[0] = ComparisonClaimResult(
            original.review_result,
            EvidenceRevisionResult(revision, claim, self._review(claim, verdict)))

    def test_failed_revision_preserves_original_and_records_rejection(self):
        from dataclasses import asdict
        for verdict in ("contradicted", "partially_supported", "insufficient_evidence"):
            with self.subTest(verdict=verdict):
                self.result.items[0] = self._item(self.original_text, [1, 3], "partially_supported")
                self._add_revision(verdict=verdict)
                before = asdict(self.result)
                report = self.builder.assemble_comparison_report(self.analysis, self.result)
                self.assertEqual(report.report.method_comparisons[0].text, self.original_text)
                self.assertEqual(report.report.method_comparisons[0].source_numbers, [1, 3])
                self.assertEqual(report.review.items[0].verdict, "partially_supported")
                self.assertEqual(report.review.items[0].claim, self.original_text)
                self.assertFalse(report.passed)
                self.assertEqual(report.correction_rounds, 1)
                rejected = report.rejected_revisions[0]
                self.assertEqual(rejected.review.verdict, verdict)
                self.assertEqual(rejected.revision.revised_claim, self.result.items[0].revision_result.revised_claim.claim)
                self.assertEqual(asdict(self.result), before)
                # 经过JSON序列化，查询方仍能读取未采用的修订及原因。
                from schemas import CheckedComparisonReportResponse
                restored = CheckedComparisonReportResponse.model_validate_json(report.model_dump_json())
                self.assertEqual(restored.rejected_revisions, report.rejected_revisions)

    def test_accepted_revision_has_no_rejections(self):
        self._add_revision()
        report = self.builder.assemble_comparison_report(self.analysis, self.result)
        self.assertTrue(report.passed)
        self.assertEqual(report.rejected_revisions, [])

    def test_legacy_report_without_rejections_is_readable(self):
        from schemas import CheckedComparisonReportResponse
        report = self.builder.assemble_comparison_report(self.analysis, self.result)
        payload = report.model_dump()
        del payload["rejected_revisions"]
        self.assertEqual(CheckedComparisonReportResponse.model_validate(payload).rejected_revisions, [])

    def test_mismatched_rejected_revision_is_not_silently_accepted(self):
        from dataclasses import replace
        self._add_revision(verdict="contradicted")
        item = self.result.items[0]
        wrong = item.revision_result.review.model_copy(update={"claim": "不对应的陈述"})
        self.result.items[0] = replace(item, revision_result=replace(item.revision_result, review=wrong))
        with self.assertRaisesRegex(ValueError, "修订陈述与审校结果不一致"):
            self.builder.assemble_comparison_report(self.analysis, self.result)

    def test_sources_include_every_entry_sorted_without_renumbering(self):
        sources = self.builder.build_report_sources(self.analysis, self.result)
        self.assertEqual([s.source_number for s in sources], [1, 2, 3, 4, 5, 23])
        self.assertEqual(sources[-1].chunk_id, 9001)
        self.assertEqual([s.score for s in sources[:-1]], [0.9] * 5)
        self.assertIsNone(sources[-1].score)
        self.assertEqual(json.loads(sources[-1].model_dump_json())["score"], None)

    def test_empty_registry_returns_empty_list(self):
        from dataclasses import replace
        result = replace(self.result, evidence_by_chunk_id={})
        self.assertEqual(self.builder.build_report_sources(self.analysis, result), [])

    def test_source_without_paper_id_is_rejected_even_after_first_entry(self):
        from dataclasses import replace
        self.result.evidence_by_chunk_id[9001] = replace(self.new_evidence, paper_id=None)
        with self.assertRaisesRegex(ValueError, "缺少论文编号"):
            self.builder.build_report_sources(self.analysis, self.result)

    def test_supported_original_is_preserved_and_input_is_unchanged(self):
        from dataclasses import asdict
        before = asdict(self.result)
        analysis_before = self.analysis.model_dump()
        report = self.builder.assemble_comparison_report(self.analysis, self.result)
        self.assertEqual(report.report.method_comparisons[0].text, self.original_text)
        self.assertEqual(report.report.method_comparisons[0].source_numbers, [1, 3])
        self.assertTrue(report.passed)
        self.assertEqual(report.correction_rounds, 0)
        self.assertEqual(asdict(self.result), before)
        self.assertEqual(self.analysis.model_dump(), analysis_before)

    def test_revision_uses_new_text_and_matching_final_review(self):
        self._add_revision()
        report = self.builder.assemble_comparison_report(self.analysis, self.result)
        self.assertIn("Top-K为10", report.report.method_comparisons[0].text)
        self.assertEqual(report.report.method_comparisons[0].source_numbers, [23, 3])
        self.assertEqual(report.initial_review.items[0].claim, self.original_text)
        self.assertEqual(report.review.items[0].claim, report.report.method_comparisons[0].text)
        self.assertTrue(report.passed)
        self.assertEqual(report.correction_rounds, 1)

    def test_unresolved_original_and_revision_never_pass(self):
        for revised in (False, True):
            with self.subTest(revised=revised):
                self.result.items[0] = self._item(self.original_text, [1, 3], "insufficient_evidence")
                if revised:
                    self._add_revision(verdict="contradicted")
                report = self.builder.assemble_comparison_report(self.analysis, self.result)
                self.assertFalse(report.passed)
                self.assertEqual(report.correction_rounds, int(revised))

    def test_single_paper_comparison_is_rejected(self):
        self._add_revision(text="A的Top-K为10。[资料23]", numbers=[23])
        with self.assertRaisesRegex(RuntimeError, "至少两篇"):
            self.builder.assemble_comparison_report(self.analysis, self.result)

    def test_one_paper_evidence_note_is_allowed(self):
        self.result.items[:] = [self._item("原文给出的Top-K是10。[资料23]", [23], field="evidence_notes")]
        report = self.builder.assemble_comparison_report(self.analysis, self.result)
        self.assertTrue(report.passed)
        self.assertEqual(report.report.evidence_notes[0].source_numbers, [23])

    def test_unknown_citation_and_empty_report_are_rejected(self):
        self.result.items[:] = [self._item("错误引用。[资料99]", [1, 3])]
        with self.assertRaisesRegex(RuntimeError, "引用"):
            self.builder.assemble_comparison_report(self.analysis, self.result)
        self.result.items.clear()
        with self.assertRaisesRegex(RuntimeError, "空报告"):
            self.builder.assemble_comparison_report(self.analysis, self.result)

    def test_task_field_and_review_mismatches_are_rejected(self):
        from dataclasses import replace
        with self.assertRaisesRegex(ValueError, "研究任务不一致"):
            self.builder.assemble_comparison_report(self.analysis, replace(self.result, task_id=999))
        self.result.items[:] = [self._item(self.original_text, [1, 3], field="unknown")]
        with self.assertRaisesRegex(ValueError, "未知的报告字段"):
            self.builder.assemble_comparison_report(self.analysis, self.result)
        item = self._item(self.original_text, [1, 3])
        wrong = item.review_result.final_review.model_copy(update={"claim": "另一句话"})
        self.result.items[:] = [replace(item, review_result=replace(item.review_result, final_review=wrong))]
        with self.assertRaisesRegex(ValueError, "审校结果不一致"):
            self.builder.assemble_comparison_report(self.analysis, self.result)

    def test_no_new_neighbors_does_not_repeat_review(self):
        from evidence_review_workflow import review_claim_with_supplement
        claim = self._claim(self.original_text, [1, 3])
        for verdict in ("supported", "contradicted", "insufficient_evidence", "partially_supported"):
            with self.subTest(verdict=verdict), \
                 patch("evidence_review_workflow.review_claim", return_value=self._review(claim, verdict)) as review, \
                 patch("evidence_review_workflow.supplement_claim_evidence", return_value=claim) as supplement:
                result = review_claim_with_supplement(Mock(), claim, self.result.evidence_by_chunk_id)
                self.assertEqual(review.call_count, 1)
                self.assertEqual(result.added_source_numbers, [])
                self.assertEqual(supplement.call_count, int(verdict in ("insufficient_evidence", "partially_supported")))

    def test_revision_accepts_sparse_new_number_and_rechecks_only_cited_evidence(self):
        from evidence_review_workflow import revise_supplemented_claim, EvidenceReviewResult
        claim = self._claim("Top-K是500。[资料1]", [1, 3, 23])
        old_review = self._review(claim, "contradicted")
        output = json.dumps({"revised_claim": "Top-K默认是10。[资料23]", "reason": "根据新增原文修正"})
        review_output = json.dumps({"verdict": "supported", "reason": "原文是10", "source_numbers": [23]})
        with patch("revision_agent.generate_text", return_value=output) as revise, \
             patch("review_agent.generate_text", return_value=review_output) as review:
            result = revise_supplemented_claim(EvidenceReviewResult(claim, old_review, old_review, [23]))
        self.assertEqual(result.revised_claim.source_numbers, [23])
        self.assertEqual(result.revised_claim.evidence, [self.new_evidence])
        self.assertIn("[资料23]", revise.call_args.kwargs["user_prompt"])
        self.assertIn(self.new_evidence.text, review.call_args.kwargs["user_prompt"])
        self.assertNotIn("[资料1]", review.call_args.kwargs["user_prompt"])

    def test_full_internal_workflow_reads_db_supplements_revises_and_assembles(self):
        import comparison_agent
        from comparison_evidence_workflow import process_comparison_evidence
        from schemas import ComparisonSourceResponse
        with TestingSessionLocal() as db:
            anchor = db.get(PaperChunk, self.analysis.sources[4].chunk_id)
            extra = PaperChunk(document_id=anchor.document_id, page_number=4, chunk_index=0,
                               text="候选召回数Top-K默认设置为10。", char_count=21)
            db.add(extra); db.commit()
            extra_id = extra.id
        # 先查5的邻居，可获得旧资料2和新资料6；新编号不从报告中只出现的编号数开始。
        data = dict(method_comparisons=[], experiment_comparisons=[
            {"text": "A的Top-K是500，B使用完全匹配。[资料5][资料3]"}],
            conclusion_comparisons=[], evidence_notes=[])
        sources = [ComparisonSourceResponse(source_number=n, **self.analysis.sources[n-1].model_dump())
                   for n in (1, 3, 5)]
        report = comparison_agent.parse_comparison_response(json.dumps(data), self.task_id,
                                                            self.analysis.question, sources)
        before = self._snapshot()
        original_report = report.model_dump()
        responses = [
            dict(verdict="insufficient_evidence", reason="缺少默认值", source_numbers=[]),
            dict(verdict="contradicted", reason="新增原文是10", source_numbers=[6]),
            dict(verdict="supported", reason="修正后有依据", source_numbers=[6, 3]),
        ]
        revision = dict(revised_claim="A的Top-K是10，B使用完全匹配。[资料6][资料3]", reason="依据新原文")
        with TestingSessionLocal() as db, \
             patch("review_agent.generate_text", side_effect=[json.dumps(r) for r in responses]) as review, \
             patch("revision_agent.generate_text", return_value=json.dumps(revision)) as revise:
            processed = process_comparison_evidence(db, self.analysis, report)
            assembled = self.builder.assemble_comparison_report(self.analysis, processed)
        self.assertEqual(review.call_count, 3)
        revise.assert_called_once()
        self.assertEqual(processed.items[0].review_result.added_source_numbers, [2, 6])
        self.assertEqual(processed.evidence_by_chunk_id[extra_id].source_number, 6)
        self.assertTrue(assembled.passed)
        self.assertEqual(assembled.report.experiment_comparisons[0].source_numbers, [6, 3])
        self.assertIsNone(assembled.report.sources[-1].score)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(report.model_dump(), original_report)


class TestSupplementedComparisonAPI(ComparisonDatabaseFixture, unittest.TestCase):
    """HTTP入口测试：使用隔离数据库，模型输出模拟，真实执行内部流程。"""

    def setUp(self):
        super().setUp()
        self.url = f"/research-tasks/{self.task_id}/comparison-report/supplemented"

    def _review_json(self, verdict="supported"):
        return json.dumps(dict(verdict=verdict, reason="测试原文审校", source_numbers=[1, 3, 5]))

    def test_no_body_generates_report_and_returns_serializable_result(self):
        before = self._snapshot()
        # 每条回复只能引用当次提供的证据。
        responses = [dict(verdict="supported", reason="有原文支持", source_numbers=[1, 3]),
                     dict(verdict="supported", reason="有原文支持", source_numbers=[5])]
        with patch("comparison_agent.generate_text", return_value=json.dumps(self.answer)) as generate, \
             patch("review_agent.generate_text", side_effect=[json.dumps(r) for r in responses]), \
             patch("revision_agent.generate_text", side_effect=AssertionError("通过的陈述不应修正")):
            response = client.post(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["passed"])
        self.assertEqual(body["correction_rounds"], 0)
        self.assertEqual(len(body["review"]["items"]), 2)
        self.assertEqual(len(body["report"]["sources"]), 5)
        generate.assert_called_once()
        self.assertEqual(self._snapshot(), before)

    def test_submitted_report_skips_generation_and_false_verdict_survives_http(self):
        import comparison_agent
        from schemas import ComparisonSourceResponse
        sources = [ComparisonSourceResponse(source_number=n, **self.analysis.sources[n-1].model_dump())
                   for n in (1, 3, 5)]
        report = comparison_agent.parse_comparison_response(json.dumps(self.answer), self.task_id,
                                                            self.analysis.question, sources)
        from comparison_report_builder import assemble_comparison_report
        from comparison_evidence_workflow import ComparisonEvidenceResult, ComparisonClaimResult
        from evidence_review_workflow import EvidenceReviewResult
        from evidence_supplement import build_evidence_registry
        from comparison_review import prepare_comparison_claims
        with TestingSessionLocal() as db:
            claims = prepare_comparison_claims(db, self.analysis, report)
            registry = build_evidence_registry(db, self.analysis)
        items = []
        for claim in claims:
            review = ReviewItemResponse(field=claim.field, claim=claim.claim, verdict="insufficient_evidence",
                                        reason="仍未确认", source_numbers=[])
            items.append(ComparisonClaimResult(EvidenceReviewResult(claim, review, review, []), None))
        processed = ComparisonEvidenceResult(self.task_id, items, registry)
        with patch("comparison_agent.generate_text", side_effect=AssertionError("不应重新生成")), \
             patch("comparison_evidence_workflow.process_comparison_evidence", return_value=processed):
            response = client.post(self.url, json=report.model_dump())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["passed"])

    def test_missing_task_and_bad_body_do_not_call_model(self):
        with patch("comparison_agent.generate_text") as generate:
            self.assertEqual(client.post('/research-tasks/99999/comparison-report/supplemented').status_code, 404)
            self.assertEqual(client.post(self.url, json={"bad": True}).status_code, 422)
            generate.assert_not_called()

    def test_value_and_runtime_errors_become_http_errors(self):
        for error, status in ((ValueError("来源不一致"), 409), (RuntimeError("模型结果不符合规则"), 502)):
            with self.subTest(status=status), patch("comparison_agent.generate_comparison_report", side_effect=error):
                response = client.post(self.url)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["detail"], str(error))


class TestReportJobSubmission(ComparisonDatabaseFixture, unittest.TestCase):
    """作业提交/查询和重复控制；使用隔离数据库并禁止真实队列投递。"""

    def setUp(self):
        super().setUp()
        from model import ReportJob
        self.Job = ReportJob
        self.url = f"/research-tasks/{self.task_id}/report-jobs"
        self.publish = patch("api.run_report_job.apply_async").start()
        self.addCleanup(patch.stopall)

    def _jobs(self):
        from sqlalchemy import select
        with TestingSessionLocal() as db:
            return list(db.scalars(select(self.Job).order_by(self.Job.id)))

    def _seed_job(self, status, task_id=None):
        with TestingSessionLocal() as db:
            job = self.Job(task_id=self.task_id if task_id is None else task_id,
                           status=status, stage="waiting", analysis_snapshot=self.analysis.model_dump(mode="json"))
            db.add(job); db.commit(); db.refresh(job)
            return job.id

    def test_first_submission_returns_202_and_publishes_only_id(self):
        before = self._snapshot()
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["task_id"], self.task_id)
        self.assertIsNone(body["result"])
        self.assertNotIn("analysis_snapshot", body)
        self.publish.assert_called_once_with(args=[body["id"]], retry=False)
        jobs = self._jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].analysis_snapshot, self.analysis.model_dump(mode="json"))
        self.assertEqual(self._snapshot(), before)

    def test_repeated_submission_does_not_create_or_publish_second_job(self):
        first = client.post(self.url)
        second = client.post(self.url)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(second.status_code, 409, second.text)
        self.assertIn(str(first.json()["id"]), second.json()["detail"])
        self.assertEqual(len(self._jobs()), 1)
        self.assertEqual(self.publish.call_count, 1)

    def test_queued_and_running_jobs_both_block_submission(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                with TestingSessionLocal() as db:
                    db.query(self.Job).delete(); db.commit()
                existing = self._seed_job(status)
                self.publish.reset_mock()
                response = client.post(self.url)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn(str(existing), response.json()["detail"])
                self.assertEqual(len(self._jobs()), 1)
                self.publish.assert_not_called()

    def test_terminal_jobs_allow_new_submission_and_preserve_history(self):
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                with TestingSessionLocal() as db:
                    db.query(self.Job).delete(); db.commit()
                existing = self._seed_job(status)
                self.publish.reset_mock()
                response = client.post(self.url)
                self.assertEqual(response.status_code, 202, response.text)
                jobs = self._jobs()
                self.assertEqual(len(jobs), 2)
                self.assertEqual(jobs[0].id, existing)
                self.assertEqual(jobs[0].status, status)
                self.assertNotEqual(response.json()["id"], existing)
                self.publish.assert_called_once()

    def test_other_task_active_job_does_not_block_this_task(self):
        with TestingSessionLocal() as db:
            other = db.query(ResearchTask).filter(ResearchTask.id != self.task_id).one()
            other_id = other.id
        self._seed_job("running", other_id)
        self.assertEqual(client.post(self.url).status_code, 202)
        self.assertEqual(len(self._jobs()), 2)

    def test_missing_or_invalid_analysis_never_publishes(self):
        self.assertEqual(client.post('/research-tasks/999999/report-jobs').status_code, 404)
        for saved in (None, "不是JSON", self.analysis.model_copy(update={"question": "旧问题"}).model_dump_json()):
            with self.subTest(saved=saved):
                with TestingSessionLocal() as db:
                    db.get(ResearchTask, self.task_id).result = saved
                    db.commit()
                response = client.post(self.url)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self._jobs(), [])
        self.publish.assert_not_called()

    def test_invalid_raw_evidence_rejected_before_job_creation(self):
        with TestingSessionLocal() as db:
            db.delete(db.get(PaperChunk, self.chunk_id)); db.commit()
        response = client.post(self.url)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self._jobs(), [])
        self.publish.assert_not_called()

    def test_query_returns_current_state_without_publishing(self):
        job_id = self._seed_job("queued")
        for status, stage in (("queued", "waiting"), ("running", "generating"), ("failed", "failed")):
            with TestingSessionLocal() as db:
                job = db.get(self.Job, job_id)
                job.status, job.stage = status, stage
                job.error_message = "模拟连接失败" if status == "failed" else None
                db.commit()
            response = client.get(f"/report-jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], status)
            self.assertEqual(response.json()["stage"], stage)
        self.assertEqual(client.get('/report-jobs/999999').status_code, 404)
        self.publish.assert_not_called()

    def test_publish_failure_records_error_and_next_submission_can_retry(self):
        from kombu.exceptions import OperationalError
        self.publish.side_effect = OperationalError("模拟Redis不可用")
        response = client.post(self.url)
        self.assertEqual(response.status_code, 503, response.text)
        job_id = response.json()["detail"]["job_id"]
        failed = client.get(f"/report-jobs/{job_id}").json()
        self.assertEqual(failed["status"], "failed")
        self.assertIsNotNone(failed["finished_at"])
        self.assertIn("Redis", failed["error_message"])
        self.publish.side_effect = None
        retry = client.post(self.url)
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertEqual(len(self._jobs()), 2)

    def test_ambiguous_publish_failure_does_not_overwrite_running_job(self):
        from kombu.exceptions import OperationalError
        def received_then_failed(*, args, retry):
            with TestingSessionLocal() as db:
                job = db.get(self.Job, args[0])
                job.status, job.stage = "running", "generating"
                db.commit()
            raise OperationalError("消息已收到，但响应连接断开")
        self.publish.side_effect = received_then_failed
        response = client.post(self.url)
        self.assertEqual(response.status_code, 503, response.text)
        job_id = response.json()["detail"]["job_id"]
        queried = client.get(f"/report-jobs/{job_id}").json()
        self.assertEqual(queried["status"], "running")
        self.assertIsNone(queried["error_message"])
        self.assertEqual(client.post(self.url).status_code, 409)

    def test_rejected_creation_rolls_back_transaction(self):
        from report_job_crud import create_report_job
        self._seed_job("queued")
        with TestingSessionLocal() as db:
            with self.assertRaisesRegex(ValueError, "尚未结束"):
                create_report_job(db, self.task_id)
            self.assertFalse(db.in_transaction())

    def test_job_claim_is_once_only(self):
        from report_job_crud import claim_report_job
        job_id = self._seed_job("queued")
        with TestingSessionLocal() as db:
            first = claim_report_job(db, job_id)
            self.assertEqual(first.status, "running")
            self.assertIsNotNone(first.started_at)
        with TestingSessionLocal() as db:
            self.assertIsNone(claim_report_job(db, job_id))
            self.assertIsNone(claim_report_job(db, 999999))


class TestReportJobRecovery(ComparisonDatabaseFixture, unittest.TestCase):
    """恢复中断作业及脚本交互；不访问真实MySQL或投递消息。"""

    def setUp(self):
        super().setUp()
        from model import ReportJob
        import recover_report_job
        self.Job = ReportJob
        self.script = recover_report_job
        self.publish = self.enterContext(patch("api.run_report_job.apply_async"))
        self.enterContext(patch("recover_report_job.SessionLocal", TestingSessionLocal))

    def _seed_job(self, status, task_id=None):
        with TestingSessionLocal() as db:
            job = self.Job(
                task_id=self.task_id if task_id is None else task_id,
                status=status, stage="generating" if status == "running" else "waiting",
                analysis_snapshot=self.analysis.model_dump(mode="json"),
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            return job.id

    def _snapshot_job(self, job_id):
        with TestingSessionLocal() as db:
            job = db.get(self.Job, job_id)
            return {column.name: getattr(job, column.name) for column in self.Job.__table__.columns}

    def _recover(self, job_id):
        from report_job_crud import recover_interrupted_job
        with TestingSessionLocal() as db:
            return recover_interrupted_job(db, job_id)

    def _run_script(self, answers):
        with patch("builtins.input", side_effect=answers) as inputs, patch("builtins.print") as output:
            self.script.main()
        return inputs, output

    def test_queued_and_running_recover_and_preserve_input(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                job_id = self._seed_job(status)
                before = self._snapshot_job(job_id)
                self.assertTrue(self._recover(job_id))
                after = self._snapshot_job(job_id)
                self.assertEqual(after["status"], "failed")
                self.assertEqual(after["stage"], "interrupted")
                self.assertIn("Worker停止", after["error_message"])
                self.assertIsNotNone(after["finished_at"])
                for name in ("id", "task_id", "analysis_snapshot", "result", "created_at", "started_at"):
                    self.assertEqual(after[name], before[name])
        self.publish.assert_not_called()

    def test_terminal_jobs_are_preserved(self):
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                job_id = self._seed_job(status)
                with TestingSessionLocal() as db:
                    job = db.get(self.Job, job_id)
                    job.result = {"retained": "已有结果"}
                    job.error_message = "已有说明"
                    db.commit()
                before = self._snapshot_job(job_id)
                self.assertFalse(self._recover(job_id))
                self.assertEqual(self._snapshot_job(job_id), before)

    def test_missing_job_returns_false(self):
        self.assertFalse(self._recover(999999))

    def test_repeated_recovery_does_not_overwrite_first_recovery(self):
        job_id = self._seed_job("running")
        self.assertTrue(self._recover(job_id))
        before = self._snapshot_job(job_id)
        self.assertFalse(self._recover(job_id))
        self.assertEqual(self._snapshot_job(job_id), before)

    def test_only_target_job_changes_and_research_tasks_are_unchanged(self):
        target = self._seed_job("running")
        same_task = self._seed_job("queued")
        with TestingSessionLocal() as db:
            other_task = db.query(ResearchTask).filter(ResearchTask.id != self.task_id).one().id
        other = self._seed_job("running", other_task)
        before = {job_id: self._snapshot_job(job_id) for job_id in (same_task, other)}
        tasks_before = self._snapshot()
        self.assertTrue(self._recover(target))
        self.assertEqual(self._snapshot(), tasks_before)
        for job_id, snapshot in before.items():
            self.assertEqual(self._snapshot_job(job_id), snapshot)

    def test_recovery_unblocks_resubmission_and_preserves_old_job(self):
        job_id = self._seed_job("running")
        url = f"/research-tasks/{self.task_id}/report-jobs"
        self.assertEqual(client.post(url).status_code, 409)
        self.publish.assert_not_called()
        self.assertTrue(self._recover(job_id))
        old = self._snapshot_job(job_id)
        response = client.post(url)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotEqual(response.json()["id"], job_id)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(self._snapshot_job(job_id), old)
        self.publish.assert_called_once_with(args=[response.json()["id"]], retry=False)

    def test_stale_queue_message_cannot_claim_recovered_job(self):
        from report_job_crud import claim_report_job
        job_id = self._seed_job("queued")
        self.assertTrue(self._recover(job_id))
        with TestingSessionLocal() as db:
            self.assertIsNone(claim_report_job(db, job_id))
        self.assertEqual(self._snapshot_job(job_id)["status"], "failed")

    def test_commit_failure_rolls_back_and_propagates(self):
        from report_job_crud import recover_interrupted_job
        job_id = self._seed_job("running")
        before = self._snapshot_job(job_id)
        with TestingSessionLocal() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("模拟提交失败")):
                with self.assertRaisesRegex(RuntimeError, "模拟提交失败"):
                    recover_interrupted_job(db, job_id)
            self.assertFalse(db.in_transaction())
        self.assertEqual(self._snapshot_job(job_id), before)

    def test_script_invalid_id_exits_without_database_access(self):
        with patch("recover_report_job.SessionLocal") as sessions:
            inputs, output = self._run_script(["不是数字"])
        sessions.assert_not_called()
        self.assertEqual(inputs.call_count, 1)
        output.assert_any_call("作业编号必须是整数。")

    def test_script_missing_job_exits_before_confirmation(self):
        inputs, output = self._run_script(["999999"])
        self.assertEqual(inputs.call_count, 1)
        output.assert_any_call("没有找到这个作业。")

    def test_script_terminal_job_exits_before_confirmation(self):
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                job_id = self._seed_job(status)
                before = self._snapshot_job(job_id)
                inputs, output = self._run_script([str(job_id)])
                self.assertEqual(inputs.call_count, 1)
                output.assert_any_call("这个作业已经结束，不需要恢复。")
                self.assertEqual(self._snapshot_job(job_id), before)

    def test_script_cancellation_does_not_modify_job(self):
        job_id = self._seed_job("running")
        before = self._snapshot_job(job_id)
        _, output = self._run_script([str(job_id), "取消"])
        output.assert_any_call("已取消恢复。")
        self.assertEqual(self._snapshot_job(job_id), before)

    def test_script_confirmation_recovers_job_without_publishing(self):
        job_id = self._seed_job("running")
        _, output = self._run_script([f" {job_id} ", " 确认恢复 "])
        output.assert_any_call("旧作业已标记为失败，可以重新提交研究任务。")
        self.assertEqual(self._snapshot_job(job_id)["status"], "failed")
        self.publish.assert_not_called()

    def test_script_handles_state_change_before_update(self):
        from report_job_crud import recover_interrupted_job
        job_id = self._seed_job("running")
        def complete_then_recover(db, selected_id):
            job = db.get(self.Job, selected_id)
            job.status = "succeeded"
            job.stage = "finished"
            db.commit()
            return recover_interrupted_job(db, selected_id)
        with patch("recover_report_job.recover_interrupted_job", side_effect=complete_then_recover):
            _, output = self._run_script([str(job_id), "确认恢复"])
        output.assert_any_call("作业状态已经变化，本次没有修改记录。")
        self.assertEqual(self._snapshot_job(job_id)["status"], "succeeded")

    def test_direct_script_execution_invokes_main(self):
        import runpy
        script_path = Path(__file__).with_name("recover_report_job.py")
        with patch("builtins.input", return_value="不是数字") as inputs, patch("builtins.print") as output:
            runpy.run_path(str(script_path), run_name="__main__")
        inputs.assert_called_once()
        output.assert_any_call("作业编号必须是整数。")


class TestTaskDeletionWithReportJobs(unittest.TestCase):
    """开启SQLite真实外键约束，验证后台作业和任务的删除规则。"""

    def setUp(self):
        from sqlalchemy import event
        from model import ReportJob
        self.Job = ReportJob
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        def get_test_db():
            with self.sessions() as db:
                yield db
        self.enterContext(patch.dict(app.dependency_overrides, {get_db: get_test_db}))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.task = self.client.post("/research-tasks", json={"title": "删除规则测试", "question": "测试问题"}).json()

    def _job(self, status, task_id=None):
        with self.sessions() as db:
            job = self.Job(task_id=self.task["id"] if task_id is None else task_id,
                           status=status, stage="test", analysis_snapshot={})
            db.add(job)
            db.commit()
            return job.id

    def test_active_jobs_reject_deletion_without_changing_records(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                job_id = self._job(status)
                response = self.client.delete(f'/research-tasks/{self.task["id"]}')
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn(str(job_id), response.json()["detail"])
                with self.sessions() as db:
                    self.assertIsNotNone(db.get(ResearchTask, self.task["id"]))
                    self.assertEqual(db.get(self.Job, job_id).status, status)
                    db.delete(db.get(self.Job, job_id))
                    db.commit()

    def test_terminal_jobs_are_deleted_before_parent_with_foreign_keys_enabled(self):
        jobs = [self._job("succeeded"), self._job("failed")]
        response = self.client.delete(f'/research-tasks/{self.task["id"]}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["deleted_task"], self.task)
        with self.sessions() as db:
            self.assertIsNone(db.get(ResearchTask, self.task["id"]))
            for job_id in jobs:
                self.assertIsNone(db.get(self.Job, job_id))

    def test_active_job_prevents_partial_deletion_of_terminal_history(self):
        ended = self._job("failed")
        active = self._job("running")
        response = self.client.delete(f'/research-tasks/{self.task["id"]}')
        self.assertEqual(response.status_code, 409)
        with self.sessions() as db:
            self.assertIsNotNone(db.get(self.Job, ended))
            self.assertIsNotNone(db.get(self.Job, active))

    def test_unknown_job_status_is_not_silently_deleted(self):
        job_id = self._job("unknown")
        self.assertEqual(self.client.delete(f'/research-tasks/{self.task["id"]}').status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.get(self.Job, job_id).status, "unknown")

    def test_deleting_task_preserves_paper_and_other_task_jobs(self):
        from model import ResearchTaskPaper
        other = self.client.post("/research-tasks", json={"title": "其他任务", "question": "保留"}).json()
        other_job = self._job("running", other["id"])
        with self.sessions() as db:
            paper = Paper(title="共享论文", author="作者", year=2026, notes="")
            db.add(paper)
            db.flush()
            paper_id = paper.id
            db.add(ResearchTaskPaper(task_id=self.task["id"], paper_id=paper_id, note=""))
            db.commit()
        self._job("failed")
        self.assertEqual(self.client.delete(f'/research-tasks/{self.task["id"]}').status_code, 200)
        with self.sessions() as db:
            self.assertIsNotNone(db.get(Paper, paper_id))
            self.assertIsNotNone(db.get(ResearchTask, other["id"]))
            self.assertEqual(db.get(self.Job, other_job).status, "running")
            self.assertEqual(db.query(ResearchTaskPaper).filter_by(task_id=self.task["id"]).count(), 0)

    def test_commit_failure_rolls_back_task_and_job_deletion(self):
        from task_crud import delete_task
        job_id = self._job("failed")
        with self.sessions() as db:
            task = db.get(ResearchTask, self.task["id"])
            with patch.object(db, "commit", side_effect=RuntimeError("模拟提交失败")):
                with self.assertRaisesRegex(RuntimeError, "模拟提交失败"):
                    delete_task(db, task)
            self.assertFalse(db.in_transaction())
        with self.sessions() as db:
            self.assertIsNotNone(db.get(ResearchTask, self.task["id"]))
            self.assertIsNotNone(db.get(self.Job, job_id))

    def test_rejection_releases_transaction(self):
        from task_crud import delete_task
        self._job("queued")
        with self.sessions() as db:
            with self.assertRaises(ValueError):
                delete_task(db, db.get(ResearchTask, self.task["id"]))
            self.assertFalse(db.in_transaction())


class TestDatabaseInitialization(unittest.TestCase):
    """迁移初始化空库；不完整旧库拒绝登记版本。"""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        import init_db
        self.init = init_db
        self.enterContext(patch("init_db.engine", self.engine))
        self.enterContext(patch("builtins.print"))

    def test_fresh_database_creates_all_model_tables(self):
        from sqlalchemy import inspect
        self.init.init_db()
        self.assertEqual(set(inspect(self.engine).get_table_names()), set(Base.metadata.tables) | {"alembic_version"})
        self.assertIn("report_jobs", inspect(self.engine).get_table_names())
        self.assertIn("research_task_comparison_reports", inspect(self.engine).get_table_names())

    def test_missing_report_table_is_rejected_without_changing_existing_task(self):
        from model import ResearchTaskComparisonReport
        from sqlalchemy import inspect
        Base.metadata.create_all(self.engine)
        sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with sessions() as db:
            task = ResearchTask(title="保留原任务", question="原问题", status="completed", result="原分析")
            db.add(task)
            db.commit()
            task_id = task.id
        ResearchTaskComparisonReport.__table__.drop(self.engine)
        from db_migrations import SchemaMismatchError
        with self.assertRaises(SchemaMismatchError):
            self.init.init_db()
        self.assertNotIn("research_task_comparison_reports", inspect(self.engine).get_table_names())
        self.assertNotIn("alembic_version", inspect(self.engine).get_table_names())
        with sessions() as db:
            task = db.get(ResearchTask, task_id)
            self.assertEqual((task.title, task.question, task.result, task.status), ("保留原任务", "原问题", "原分析", "completed"))


class TestPaperIdentityPrompts(unittest.TestCase):
    """相同标题、乱序资料也必须保留真实论文编号，禁止真实模型请求。"""

    def setUp(self):
        self.evidence = [
            ReviewEvidence(23, "相同标题", 9001, 4, "低切换实验结果", 9),
            ReviewEvidence(3, "相同标题", 9002, 5, "法律医疗实验结果", 10),
        ]
        self.claim = ReviewClaim("evidence_notes", "论文归属待核对。[资料23][资料3]", [23, 3], self.evidence)
        self.review = ReviewItemResponse(field=self.claim.field, claim=self.claim.claim,
                                        verdict="contradicted", reason="论文归属错误", source_numbers=[23, 3])

    def _check_prompt(self, prompt):
        self.assertIn("[资料23]\n论文编号：9\n论文标题：相同标题\n页码：4\n原文：低切换实验结果", prompt)
        self.assertIn("[资料3]\n论文编号：10\n论文标题：相同标题\n页码：5\n原文：法律医疗实验结果", prompt)
        self.assertNotIn("论文编号：23", prompt)

    def test_review_prompt_preserves_real_paper_ids(self):
        from review_agent import review_claim
        with patch("review_agent.generate_text", return_value=json.dumps(dict(
            verdict="contradicted", reason="论文归属错误", source_numbers=[23, 3]))) as generate:
            review_claim(self.claim)
        self._check_prompt(generate.call_args.kwargs["user_prompt"])

    def test_revision_prompt_preserves_ids_and_forbids_swapping(self):
        from revision_agent import revise_claim
        with patch("revision_agent.generate_text", return_value=json.dumps(dict(
            revised_claim="论文9报告低切换，论文10报告法律医疗实验。[资料23][资料3]", reason="核对来源"))) as generate:
            revise_claim(self.claim, self.review, 23)
        self._check_prompt(generate.call_args.kwargs["user_prompt"])
        self.assertIn("不得交换不同论文的结果", generate.call_args.kwargs["system_prompt"])

    def test_legacy_evidence_does_not_invent_paper_id(self):
        from dataclasses import replace
        from review_service import format_review_evidence
        text = format_review_evidence([replace(self.evidence[0], paper_id=None)])
        self.assertIn("[资料23]", text)
        self.assertNotIn("论文编号：", text)
        self.assertIn("低切换实验结果", text)


class TestReportExport(ComparisonDatabaseFixture, unittest.TestCase):
    """验证Markdown下载：真实接口和隔离数据库，禁止模型调用及队列投递。"""

    def setUp(self):
        super().setUp()
        from model import ReportJob
        from schemas import CheckedComparisonReportResponse, ComparisonReportResponse, ComparisonSourceResponse
        self.Job = ReportJob
        self.claim = "A采用语义匹配，B采用完全匹配。[资料23][资料3]"
        report = ComparisonReportResponse(
            task_id=self.task_id, question=self.analysis.question, paper_ids=self.paper_ids,
            method_comparisons=[dict(text=self.claim, source_numbers=[23, 3])],
            experiment_comparisons=[], conclusion_comparisons=[], evidence_notes=[],
            # 故意乱序、不连续，导出只能排序，不能重新编号。
            sources=[ComparisonSourceResponse(source_number=n, **source.model_dump())
                     for n, source in ((23, self.analysis.sources[0]), (3, self.analysis.sources[2]))],
        )
        review = ResearchTaskReviewResponse(task_id=self.task_id, items=[
            ReviewItemResponse(field="method_comparisons", claim=self.claim,
                               verdict="supported", reason="双方原文支持该比较。", source_numbers=[23, 3])])
        self.checked = CheckedComparisonReportResponse(
            task_id=self.task_id, report=report, initial_review=review, review=review,
            passed=True, correction_rounds=0,
        )
        self.model_call = self.enterContext(patch(
            "llm_service.generate_text", side_effect=AssertionError("导出不能调用模型")))
        self.publish = self.enterContext(patch(
            "api.run_report_job.apply_async", side_effect=AssertionError("导出不能创建后台作业")))
        # Agent导入了各自的generate_text引用，也要防止意外调用。
        for target in ("comparison_agent.generate_text", "review_agent.generate_text", "revision_agent.generate_text"):
            self.enterContext(patch(target, side_effect=AssertionError("导出不能调用Agent模型")))

    def _seed_job(self, status="succeeded", payload=None):
        with TestingSessionLocal() as db:
            job = self.Job(task_id=self.task_id, status=status, stage="finished",
                           analysis_snapshot=self.analysis.model_dump(mode="json"),
                           result=self.checked.model_dump(mode="json") if payload is None else payload)
            db.add(job)
            db.commit()
            db.refresh(job)
            return job.id

    def _export(self, job_id):
        return client.get(f"/report-jobs/{job_id}/export")

    def test_success_download_has_utf8_content_and_attachment_filename(self):
        job_id = self._seed_job()
        response = self._export(job_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/markdown; charset=utf-8")
        self.assertEqual(response.headers["content-disposition"],
                         f'attachment; filename="report-task-{self.task_id}-job-{job_id}.md"')
        text = response.content.decode("utf-8")
        for expected in ("# 科研论文对比报告", self.analysis.question, self.claim,
                         "模型审校通过", "双方原文支持该比较。", "## 资料来源"):
            self.assertIn(expected, text)
        self.assertNotIn("## 未采用的修订", text)

    def test_sources_sort_without_renumbering_or_swapping_papers(self):
        response = self._export(self._seed_job())
        self.assertEqual(response.status_code, 200, response.text)
        sources = response.text.split("## 资料来源", 1)[1]
        self.assertLess(sources.index("### 资料3\n"), sources.index("### 资料23\n"))
        self.assertNotIn("### 资料1\n", sources)
        for number, source in ((23, self.analysis.sources[0]), (3, self.analysis.sources[2])):
            self.assertIn(f"### 资料{number}\n\n> 论文编号：{source.paper_id}\n"
                          f"> 论文标题：{source.paper_title}\n> 页码：{source.page_number}\n"
                          f"> 文本块编号：{source.chunk_id}", sources)

    def test_unapproved_report_downloads_with_pending_label_and_rejection_reason(self):
        from schemas import AnalysisRevisionResponse, RejectedComparisonRevisionResponse
        review = ReviewItemResponse(field="method_comparisons", claim=self.claim,
                                   verdict="partially_supported", reason="仍有证据不足。", source_numbers=[23, 3])
        bad_text = "未采用的错误归属。[资料23][资料3]"
        rejected = RejectedComparisonRevisionResponse(
            revision=AnalysisRevisionResponse(field="method_comparisons", original_claim=self.claim,
                revised_claim=bad_text, reason="尝试修正", source_numbers=[23, 3]),
            review=ReviewItemResponse(field="method_comparisons", claim=bad_text,
                verdict="contradicted", reason="修订交换了论文归属。", source_numbers=[23, 3]))
        self.checked = self.checked.model_copy(update={
            "passed": False, "correction_rounds": 1,
            "review": ResearchTaskReviewResponse(task_id=self.task_id, items=[review]),
            "rejected_revisions": [rejected],
        })
        response = self._export(self._seed_job())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("待核验：存在未通过审校的陈述", response.text)
        self.assertNotIn("模型审校通过", response.text)
        adopted, rejected_section = response.text.split("## 未采用的修订", 1)
        self.assertIn(self.claim, adopted)
        self.assertNotIn(bad_text, adopted)
        self.assertIn(bad_text, rejected_section)
        self.assertIn("修订交换了论文归属。", rejected_section)

    def test_empty_sections_keep_headings_and_explanation(self):
        response = self._export(self._seed_job())
        self.assertEqual(response.status_code, 200, response.text)
        for title in ("实验对比", "结论对比", "证据说明"):
            self.assertIn(f"## {title}\n\n本部分没有内容。", response.text)

    def test_multiline_question_preserves_chinese_lines(self):
        from report_export import build_report_markdown
        updated = self.checked.model_copy(deep=True)
        updated.report.question = "第一行研究问题\n第二行补充说明"
        text = build_report_markdown(updated, 16)
        self.assertIn("> 第一行研究问题\n> 第二行补充说明", text)

    def test_missing_job_returns_404(self):
        response = self._export(999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "没有找到这个报告作业")

    def test_unfinished_and_failed_jobs_return_409(self):
        for status in ("queued", "running", "failed"):
            with self.subTest(status=status):
                response = self._export(self._seed_job(status=status))
                self.assertEqual(response.status_code, 409, response.text)
                self.assertNotIn("content-disposition", response.headers)

    def test_completed_job_without_result_returns_500(self):
        job_id = self._seed_job()
        with TestingSessionLocal() as db:
            db.get(self.Job, job_id).result = None
            db.commit()
        response = self._export(job_id)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "作业已完成，但没有保存报告结果")

    def test_malformed_result_returns_500_without_download(self):
        for payload in ({}, {"task_id": self.task_id}, "不是正确的报告对象"):
            with self.subTest(payload=payload):
                response = self._export(self._seed_job(payload=payload))
                self.assertEqual(response.status_code, 500, response.text)
                self.assertEqual(response.json()["detail"], "保存的报告格式不正确，无法导出")
                self.assertNotIn("content-disposition", response.headers)

    def test_task_mismatch_at_either_level_returns_500(self):
        for outer in (True, False):
            with self.subTest(outer=outer):
                payload = self.checked.model_dump(mode="json")
                if outer:
                    payload["task_id"] = self.task_id + 100
                else:
                    payload["report"]["task_id"] = self.task_id + 100
                response = self._export(self._seed_job(payload=payload))
                self.assertEqual(response.status_code, 500, response.text)
                self.assertEqual(response.json()["detail"], "报告与作业所属的研究任务不一致")

    def test_legacy_report_without_rejections_still_downloads(self):
        payload = self.checked.model_dump(mode="json")
        del payload["rejected_revisions"]
        response = self._export(self._seed_job(payload=payload))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(self.claim, response.text)

    def test_repeated_export_does_not_change_database_or_invoke_generation(self):
        from sqlalchemy import select
        job_id = self._seed_job()
        before = self._snapshot()
        with TestingSessionLocal() as db:
            original = db.get(self.Job, job_id)
            job_before = (original.status, original.stage, original.result, original.analysis_snapshot,
                          original.error_message, original.created_at, original.finished_at)
            count = db.query(self.Job).count()
        first = self._export(job_id)
        second = self._export(job_id)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.content, second.content)
        self.assertEqual(self._snapshot(), before)
        with TestingSessionLocal() as db:
            current = db.get(self.Job, job_id)
            self.assertEqual((current.status, current.stage, current.result, current.analysis_snapshot,
                              current.error_message, current.created_at, current.finished_at), job_before)
            self.assertEqual(db.query(self.Job).count(), count)
        self.model_call.assert_not_called()
        self.publish.assert_not_called()


class PaperPreparationJobFixture:
    """后台论文准备：实际接口、PDF解析、分块和数据库，模拟队列及向量服务。"""

    def setUp(self):
        from model import PaperPreparationJob, PaperPage
        import paper_preparation_worker as worker
        import paper_preparation_job_crud as jobs
        self.Job, self.Page, self.worker, self.jobs = PaperPreparationJob, PaperPage, worker, jobs
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        self.temp = self.enterContext(TemporaryDirectory())
        self.pdf_path = Path(self.temp) / "paper.pdf"
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_textbox(pymupdf.Rect(40, 40, 550, 780),
                                "Semantic caching reduces language model serving costs. " * 12,
                                fontsize=10)
            pdf.save(self.pdf_path)
        with TestingSessionLocal() as db:
            db.add_all([
                Paper(id=9, title="测试论文A", author="作者A", year=2026, notes=""),
                Paper(id=3, title="测试论文B", author="作者B", year=2026, notes=""),
                Paper(id=11, title="未上传PDF", author="作者C", year=2026, notes=""),
            ])
            db.flush()
            for document_id, paper_id in ((12, 9), (13, 3)):
                db.add(PaperDocument(id=document_id, paper_id=paper_id, original_name="paper.pdf",
                    stored_name=f"paper-{document_id}.pdf", file_path=str(self.pdf_path),
                    content_type="application/pdf", file_size=self.pdf_path.stat().st_size))
            db.commit()
        self.publish = self.enterContext(patch("api.run_preparation_job.apply_async"))
        self.enterContext(patch("paper_preparation_worker.SessionLocal", TestingSessionLocal))
        self.embed = self.enterContext(patch("embedding_indexer.embed_texts",
            side_effect=lambda texts: [[0.1, 0.2, 0.3] for _ in texts]))
        # 测试禁止访问真实模型或真实Ollama。
        for target in ("llm_service.generate_text", "embedding_service.urlopen"):
            self.enterContext(patch(target, side_effect=AssertionError("测试禁止外部模型请求")))
        self.url = "/papers/9/preparation-jobs"

    def _seed(self, job_id=3, paper_id=9, document_id=12, status="queued"):
        with TestingSessionLocal() as db:
            db.add(self.Job(id=job_id, paper_id=paper_id, document_id=document_id,
                            status=status, stage="waiting"))
            db.commit()
        return job_id

    def _get(self, job_id=3):
        response = client.get(f"/paper-preparation-jobs/{job_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _count(self):
        with TestingSessionLocal() as db:
            return db.query(self.Job).count()

class TestPaperPreparationJobs(PaperPreparationJobFixture, unittest.TestCase):
    """验证作业提交、领取、执行、失败记录及数据复用。"""

    def test_submit_creates_snapshot_and_publishes_only_job_id(self):
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertNotEqual(job["id"], job["paper_id"])
        self.assertEqual((job["paper_id"], job["document_id"], job["status"], job["stage"]),
                         (9, 12, "queued", "waiting"))
        self.assertIsNone(job["result"])
        self.assertIsNone(job["started_at"])
        self.publish.assert_called_once_with(args=[job["id"]], retry=False)
        self.assertEqual(self._count(), 1)
        self.embed.assert_not_called()

    def test_queued_and_running_jobs_block_duplicate_submission(self):
        self._seed()
        for status in ("queued", "running"):
            with self.subTest(status=status):
                with TestingSessionLocal() as db:
                    db.get(self.Job, 3).status = status
                    db.commit()
                response = client.post(self.url)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("3", response.json()["detail"])
                self.assertEqual(self._count(), 1)
        self.publish.assert_not_called()

    def test_terminal_jobs_allow_new_submission_and_keep_history(self):
        self._seed(status="failed")
        self._seed(job_id=4, status="succeeded")
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self._count(), 3)
        self.assertEqual(self._get(3)["status"], "failed")
        self.assertEqual(self._get(4)["status"], "succeeded")

    def test_other_paper_active_job_does_not_block_submission(self):
        self._seed(job_id=9, paper_id=3, document_id=13)
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self._get(9)["status"], "queued")
        self.assertEqual(self._count(), 2)

    def test_missing_paper_or_pdf_returns_404_without_job_or_message(self):
        for paper_id in (999999, 11):
            with self.subTest(paper_id=paper_id):
                response = client.post(f"/papers/{paper_id}/preparation-jobs")
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(self._count(), 0)
        self.publish.assert_not_called()

    def test_query_does_not_publish_and_missing_job_returns_404(self):
        self._seed()
        self.assertEqual(self._get()["status"], "queued")
        self.assertEqual(client.get('/paper-preparation-jobs/999999').status_code, 404)
        self.publish.assert_not_called()
        self.embed.assert_not_called()

    def test_claim_uses_job_id_not_paper_id_and_leaves_other_job_untouched(self):
        # 编号交叉：作业3属于论文9，作业9属于论文3，不能混淆。
        self._seed()
        self._seed(job_id=9, paper_id=3, document_id=13)
        with TestingSessionLocal() as db:
            claimed = self.jobs.claim_preparation_job(db, 3)
            self.assertIsNotNone(claimed)
            self.assertEqual((claimed.id, claimed.paper_id), (3, 9))
            self.assertEqual((claimed.status, claimed.stage), ("running", "extracting"))
            self.assertIsNotNone(claimed.started_at)
        self.assertEqual(self._get(9)["status"], "queued")
        with TestingSessionLocal() as db:
            self.assertIsNone(self.jobs.claim_preparation_job(db, 3))
            self.assertIsNone(self.jobs.claim_preparation_job(db, 999999))

    def test_terminal_job_cannot_be_claimed(self):
        for job_id, status in ((3, "failed"), (4, "succeeded")):
            self._seed(job_id=job_id, status=status)
            with TestingSessionLocal() as db:
                self.assertIsNone(self.jobs.claim_preparation_job(db, job_id))
            self.assertEqual(self._get(job_id)["status"], status)

    def test_publish_failure_records_failed_job_and_can_resubmit(self):
        from kombu.exceptions import OperationalError
        from redis.exceptions import RedisError
        for error in (OperationalError("断开连接"), RedisError("Redis不可用")):
            with self.subTest(error=type(error).__name__):
                self.publish.side_effect = error
                response = client.post(self.url)
                self.assertEqual(response.status_code, 503, response.text)
                job = self._get(response.json()["detail"]["job_id"])
                self.assertEqual((job["status"], job["stage"]), ("failed", "failed"))
                self.assertIsNotNone(job["finished_at"])
                self.assertIn("Redis", job["error_message"])
        self.publish.side_effect = None
        self.assertEqual(client.post(self.url).status_code, 202)

    def test_ambiguous_publish_failure_does_not_overwrite_claimed_job(self):
        from kombu.exceptions import OperationalError
        def accepted_then_disconnected(*, args, retry):
            with TestingSessionLocal() as db:
                self.jobs.claim_preparation_job(db, args[0])
            raise OperationalError("已接收消息，但回复连接断开")
        self.publish.side_effect = accepted_then_disconnected
        response = client.post(self.url)
        self.assertEqual(response.status_code, 503, response.text)
        job = self._get(response.json()["detail"]["job_id"])
        self.assertEqual(job["status"], "running")
        self.assertIsNone(job["error_message"])
        self.assertEqual(client.post(self.url).status_code, 409)

    def test_worker_runs_actual_pipeline_saves_data_and_exposes_stages(self):
        import paper_pipeline
        self._seed()
        actual_prepare = paper_pipeline.prepare_paper_document
        observed = []
        def observe_prepare(*, db, document, on_stage):
            def observe_stage(stage):
                on_stage(stage)
                job = self._get()
                observed.append((job["status"], job["stage"]))
            return actual_prepare(db=db, document=document, on_stage=observe_stage)
        with patch("paper_preparation_worker.paper_pipeline.prepare_paper_document", side_effect=observe_prepare):
            self.worker.run_preparation_job.run(3)
        job = self._get()
        self.assertEqual(observed, [("running", s) for s in ("extracting", "chunking", "indexing")])
        self.assertEqual((job["status"], job["stage"]), ("succeeded", "finished"))
        self.assertIsNotNone(job["started_at"])
        self.assertIsNotNone(job["finished_at"])
        self.assertIsNone(job["error_message"])
        result = job["result"]
        self.assertEqual((result["paper_id"], result["document_id"]), (9, 12))
        with TestingSessionLocal() as db:
            pages = db.query(self.Page).filter_by(document_id=12).all()
            chunks = db.query(PaperChunk).filter_by(document_id=12).all()
            vectors = [db.get(PaperChunkEmbedding, c.id) for c in chunks]
            self.assertEqual(result["page_count"], len(pages))
            self.assertEqual(result["chunk_count"], len(chunks))
            self.assertEqual(result["indexed_count"], len(chunks))
            self.assertGreater(len(chunks), 0)
            self.assertTrue(all(v and v.vector == [0.1, 0.2, 0.3] for v in vectors))
        self.embed.assert_called()

    def test_worker_duplicate_message_does_not_repeat_processing(self):
        self._seed()
        self.worker.run_preparation_job.run(3)
        before = self._get()
        with patch("paper_preparation_worker.paper_pipeline.prepare_paper_document") as prepare:
            self.worker.run_preparation_job.run(3)
            self.worker.run_preparation_job.run(999999)
            prepare.assert_not_called()
        self.assertEqual(self._get(), before)

    def test_worker_refuses_missing_or_wrong_document(self):
        for job_id, document_id, message in ((3, 999999, "已经不存在"), (4, 13, "不一致")):
            with self.subTest(document_id=document_id):
                self._seed(job_id=job_id, document_id=document_id)
                with patch("paper_preparation_worker.paper_pipeline.prepare_paper_document") as prepare:
                    with self.assertRaisesRegex(ValueError, message):
                        self.worker.run_preparation_job.run(job_id)
                    prepare.assert_not_called()
                job = self._get(job_id)
                self.assertEqual(job["status"], "failed")
                self.assertIn(message, job["error_message"])
                self.assertIsNotNone(job["finished_at"])

    def test_missing_pdf_file_records_failure_without_vectors(self):
        self._seed()
        self.pdf_path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.worker.run_preparation_job.run(3)
        job = self._get()
        self.assertEqual((job["status"], job["stage"]), ("failed", "failed"))
        self.assertIsNone(job["result"])
        self.assertIn("没有找到PDF文件", job["error_message"])
        self.embed.assert_not_called()

    def test_vector_failure_then_retry_reuses_saved_pages_and_chunks(self):
        self._seed()
        self.embed.side_effect = URLError("Ollama不可用")
        with self.assertRaises(URLError):
            self.worker.run_preparation_job.run(3)
        self.assertEqual(self._get()["status"], "failed")
        with TestingSessionLocal() as db:
            old_pages = [p.id for p in db.query(self.Page).filter_by(document_id=12)]
            old_chunks = [c.id for c in db.query(PaperChunk).filter_by(document_id=12)]
            self.assertTrue(old_pages and old_chunks)
            self.assertEqual(db.query(PaperChunkEmbedding).count(), 0)
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        new_id = response.json()["id"]
        self.embed.side_effect = lambda texts: [[0.1, 0.2, 0.3] for _ in texts]
        with patch("paper_pipeline.extract_pdf_text", side_effect=AssertionError("重试应复用已有页面")):
            self.worker.run_preparation_job.run(new_id)
        self.assertEqual(self._get(new_id)["status"], "succeeded")
        with TestingSessionLocal() as db:
            self.assertEqual([p.id for p in db.query(self.Page).filter_by(document_id=12)], old_pages)
            self.assertEqual([c.id for c in db.query(PaperChunk).filter_by(document_id=12)], old_chunks)

    def test_new_job_reuses_existing_vectors_and_returns_zero_new_indexes(self):
        self._seed()
        self.worker.run_preparation_job.run(3)
        self.embed.reset_mock()
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        new_id = response.json()["id"]
        with patch("paper_pipeline.extract_pdf_text", side_effect=AssertionError("已有页面不能重复提取")):
            self.worker.run_preparation_job.run(new_id)
        job = self._get(new_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["indexed_count"], 0)
        self.embed.assert_not_called()

    def test_creation_commit_failure_rolls_back(self):
        with TestingSessionLocal() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("模拟提交失败")):
                with self.assertRaisesRegex(RuntimeError, "模拟提交失败"):
                    self.jobs.create_preparation_job(db, 9)
            self.assertFalse(db.in_transaction())
        self.assertEqual(self._count(), 0)

    def test_worker_failure_limits_error_length(self):
        self._seed()
        with patch("paper_preparation_worker.paper_pipeline.prepare_paper_document",
                   side_effect=RuntimeError("错" * 2500)):
            with self.assertRaises(RuntimeError):
                self.worker.run_preparation_job.run(3)
        self.assertEqual(len(self._get()["error_message"]), 2000)


class TestPaperMutationProtection(PaperPreparationJobFixture, unittest.TestCase):
    """验证六个修改入口的保护，读取入口保持可用；不做真实联调。"""

    def setUp(self):
        super().setUp()
        import paper_pipeline
        with TestingSessionLocal() as db:
            paper_pipeline.prepare_paper_document(db, db.get(PaperDocument, 12))
        self.embed.reset_mock()

    def _snapshot_preparation(self):
        from sqlalchemy import select
        models = (Paper, PaperDocument, self.Page, PaperChunk, PaperChunkEmbedding, self.Job)
        with TestingSessionLocal() as db:
            rows = {model.__tablename__: [
                {c.name: getattr(row, c.name) for c in model.__table__.columns}
                for row in db.scalars(select(model))] for model in models}
        return json.dumps(rows, sort_keys=True, default=str), self.pdf_path.read_bytes()

    def test_all_six_mutations_block_queued_and_running_jobs_without_changes(self):
        self._seed()
        operations = [
            ("delete", "/papers/9"), ("delete", "/papers/9/document"),
            ("post", "/papers/9/document/extract"), ("post", "/papers/9/chunks"),
            ("post", "/papers/9/chunks/embeddings"), ("post", "/papers/9/prepare"),
        ]
        for status in ("queued", "running"):
            with TestingSessionLocal() as db:
                db.get(self.Job, 3).status = status
                db.commit()
            before = self._snapshot_preparation()
            for method, url in operations:
                with self.subTest(status=status, url=url):
                    response = getattr(client, method)(url)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn("作业3", response.json()["detail"])
                    self.assertEqual(self._snapshot_preparation(), before)
        self.embed.assert_not_called()
        self.publish.assert_not_called()

    def test_reads_remain_available_during_preparation(self):
        self._seed(status="running")
        for url in ("/papers/9", "/papers/9/document", "/papers/9/pages",
                    "/papers/9/chunks", "/paper-preparation-jobs/3"):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.text)
        self.publish.assert_not_called()

    def test_other_paper_prepare_is_not_blocked(self):
        self._seed(status="running")
        response = client.post("/papers/3/prepare")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["paper_id"], 3)
        self.assertEqual(self._get()["status"], "running")

    def test_terminal_jobs_allow_synchronous_prepare(self):
        self._seed()
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                with TestingSessionLocal() as db:
                    db.get(self.Job, 3).status = status
                    db.commit()
                response = client.post("/papers/9/prepare")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["indexed_count"], 0)
                self.assertEqual(self._get()["status"], status)

    def test_terminal_jobs_allow_pdf_delete_and_preserve_job_history(self):
        self._seed(status="succeeded")
        response = client.delete("/papers/9/document")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.pdf_path.exists())
        self.assertEqual(self._get()["status"], "succeeded")
        with TestingSessionLocal() as db:
            self.assertIsNone(db.get(PaperDocument, 12))

    def test_terminal_jobs_allow_paper_delete_and_preserve_job_history(self):
        self._seed(status="failed")
        response = client.delete("/papers/9")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._get()["status"], "failed")
        with TestingSessionLocal() as db:
            self.assertIsNone(db.get(Paper, 9))

    def test_existing_missing_resource_errors_are_preserved(self):
        for url in ("/papers/999999/prepare", "/papers/11/prepare"):
            with self.subTest(url=url):
                response = client.post(url)
                self.assertEqual(response.status_code, 404, response.text)

    def test_lock_stays_held_across_stage_commits_and_blocks_job_submission(self):
        import paper_pipeline
        actual_prepare = paper_pipeline.prepare_paper_document
        observed = []
        def prepare_with_checks(*, db, document, chunk_size, overlap):
            def at_stage(stage):
                db.commit()
                submission = client.post(self.url)
                deletion = client.delete("/papers/9/document")
                observed.append((stage, submission.status_code, deletion.status_code))
                self.assertEqual(self._count(), 0)
                self.assertTrue(self.pdf_path.exists())
            return actual_prepare(db=db, document=document, chunk_size=chunk_size,
                                  overlap=overlap, on_stage=at_stage)
        with patch("api.paper_pipeline.prepare_paper_document", side_effect=prepare_with_checks):
            response = client.post("/papers/9/prepare")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(observed, [(stage, 409, 409) for stage in ("extracting", "chunking", "indexing")])
        self.publish.assert_not_called()
        # 原操作结束后，同一篇论文可以取得锁并提交。
        self.assertEqual(client.post(self.url).status_code, 202)

    def test_lock_is_released_after_business_exception(self):
        with patch("api.paper_pipeline.prepare_paper_document", side_effect=ValueError("模拟正文错误")):
            response = client.post("/papers/9/prepare")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(client.post(self.url).status_code, 202)

    def test_sqlite_lock_blocks_same_paper_across_sessions_but_allows_other_paper(self):
        from paper_operation_guard import paper_operation_lock, PaperOperationBusyError
        with TestingSessionLocal() as first, TestingSessionLocal() as second:
            with paper_operation_lock(first, 9):
                first.commit()
                with self.assertRaises(PaperOperationBusyError):
                    with paper_operation_lock(second, 9):
                        self.fail("同一论文不能同时取得锁")
                with paper_operation_lock(second, 3):
                    pass
            with paper_operation_lock(second, 9):
                pass

    def test_sqlite_thread_conflict_returns_409_and_release_restores_submission(self):
        from threading import Event, Thread
        from paper_operation_guard import paper_operation_lock
        entered, release = Event(), Event()
        errors = []
        def holder():
            try:
                with TestingSessionLocal() as db, paper_operation_lock(db, 9):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("测试锁未及时释放")
            except Exception as error:
                errors.append(error)
                entered.set()
        thread = Thread(target=holder)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(client.post(self.url).status_code, 409)
            self.assertEqual(client.delete("/papers/9/document").status_code, 409)
            self.assertEqual(self._count(), 0)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(client.post(self.url).status_code, 202)

    def test_background_worker_still_runs_without_using_mutation_entry(self):
        self._seed()
        self.worker.run_preparation_job.run(3)
        self.assertEqual(self._get()["status"], "succeeded")


class TestMySQLPaperLockProtocol(unittest.TestCase):
    """模拟MySQL连接协议，验证取锁和释放规则；不证明真实MySQL互斥。"""

    def _database(self, acquired=1, released=1, database="test_library"):
        from unittest.mock import MagicMock
        connection = MagicMock()
        connection.scalar.side_effect = [acquired, released]
        engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql"),
                                 url=SimpleNamespace(database=database))
        engine.engine = engine
        context = MagicMock()
        context.__enter__.return_value = connection
        engine.connect = Mock(return_value=context)
        db = Mock()
        db.get_bind.return_value = engine
        return db, connection

    def test_mysql_uses_same_connection_and_name_for_acquire_and_release(self):
        from paper_operation_guard import paper_operation_lock
        db, connection = self._database()
        with paper_operation_lock(db, 9):
            db.commit()
            self.assertEqual(connection.scalar.call_count, 1)
        calls = connection.scalar.call_args_list
        self.assertIn("GET_LOCK", str(calls[0].args[0]))
        self.assertIn("RELEASE_LOCK", str(calls[1].args[0]))
        self.assertEqual(calls[0].args[1], calls[1].args[1])
        self.assertLessEqual(len(calls[0].args[1]["lock_name"]), 64)
        connection.invalidate.assert_not_called()

    def test_same_database_and_paper_share_name_other_papers_have_other_names(self):
        from paper_operation_guard import paper_operation_lock
        names = []
        for database, paper_id in (("library", 9), ("library", 9), ("library", 10), ("other", 9)):
            db, connection = self._database(database=database)
            with paper_operation_lock(db, paper_id):
                pass
            names.append(connection.scalar.call_args_list[0].args[1]["lock_name"])
        self.assertEqual(names[0], names[1])
        self.assertNotEqual(names[0], names[2])
        self.assertNotEqual(names[0], names[3])

    def test_busy_or_unavailable_lock_does_not_execute_body(self):
        from paper_operation_guard import paper_operation_lock, PaperOperationBusyError
        for value, error_type in ((0, PaperOperationBusyError), (None, RuntimeError)):
            with self.subTest(value=value):
                db, connection = self._database(acquired=value)
                with self.assertRaises(error_type):
                    with paper_operation_lock(db, 9):
                        self.fail("未取得锁不能执行操作")
                self.assertEqual(connection.scalar.call_count, 1)

    def test_business_exception_still_releases_lock(self):
        from paper_operation_guard import paper_operation_lock
        db, connection = self._database()
        with self.assertRaisesRegex(ValueError, "业务失败"):
            with paper_operation_lock(db, 9):
                raise ValueError("业务失败")
        self.assertEqual(connection.scalar.call_count, 2)
        connection.invalidate.assert_not_called()

    def test_acquire_exception_invalidates_connection_without_executing_body(self):
        from paper_operation_guard import paper_operation_lock
        db, connection = self._database()
        connection.scalar.side_effect = RuntimeError("取得锁时连接中断")
        with self.assertRaisesRegex(RuntimeError, "连接中断"):
            with paper_operation_lock(db, 9):
                self.fail("取得锁失败不能执行操作")
        connection.invalidate.assert_called_once()

    def test_release_failure_invalidates_connection(self):
        from paper_operation_guard import paper_operation_lock
        db, connection = self._database(released=0)
        with self.assertRaisesRegex(RuntimeError, "释放"):
            with paper_operation_lock(db, 9):
                pass
        self.assertTrue(connection.invalidate.called)


class TestPaperPreparationRecovery(PaperPreparationJobFixture, unittest.TestCase):
    """恢复工具只修改隔离数据库；验证历史保留、交互取消和重新提交。"""

    def setUp(self):
        super().setUp()
        import recover_paper_preparation_job as script
        self.script = script
        self.enterContext(patch("recover_paper_preparation_job.SessionLocal", TestingSessionLocal))
        import paper_pipeline
        with TestingSessionLocal() as db:
            paper_pipeline.prepare_paper_document(db, db.get(PaperDocument, 12))
        self.embed.reset_mock()

    def _snapshot_job(self, job_id=3):
        with TestingSessionLocal() as db:
            job = db.get(self.Job, job_id)
            return {c.name: getattr(job, c.name) for c in self.Job.__table__.columns}

    def _snapshot_data(self):
        from sqlalchemy import select
        with TestingSessionLocal() as db:
            data = {model.__tablename__: [
                {c.name: getattr(row, c.name) for c in model.__table__.columns}
                for row in db.scalars(select(model))]
                for model in (Paper, PaperDocument, self.Page, PaperChunk, PaperChunkEmbedding)}
        return json.dumps(data, sort_keys=True, default=str), self.pdf_path.read_bytes()

    def _recover(self, job_id=3):
        with TestingSessionLocal() as db:
            return self.jobs.recover_interrupted_preparation_job(db, job_id)

    def _run_script(self, answers):
        with patch("builtins.input", side_effect=answers) as inputs, patch("builtins.print") as output:
            self.script.main()
        return inputs, output

    def test_queued_and_running_recover_without_changing_input_or_prepared_data(self):
        data_before = self._snapshot_data()
        for job_id, status in ((3, "queued"), (4, "running")):
            with self.subTest(status=status):
                self._seed(job_id=job_id, status=status)
                before = self._snapshot_job(job_id)
                self.assertTrue(self._recover(job_id))
                after = self._snapshot_job(job_id)
                self.assertEqual((after["status"], after["stage"]), ("failed", "interrupted"))
                self.assertIn("所有Worker停止", after["error_message"])
                self.assertIsNotNone(after["finished_at"])
                for name in ("id", "paper_id", "document_id", "result", "created_at", "started_at"):
                    self.assertEqual(after[name], before[name])
                self.assertEqual(self._snapshot_data(), data_before)
        self.publish.assert_not_called()
        self.embed.assert_not_called()

    def test_terminal_jobs_and_existing_results_are_unchanged(self):
        for job_id, status in ((3, "succeeded"), (4, "failed")):
            with self.subTest(status=status):
                self._seed(job_id=job_id, status=status)
                with TestingSessionLocal() as db:
                    job = db.get(self.Job, job_id)
                    job.result = {"retained": "已有结果"}
                    job.error_message = "已有说明"
                    db.commit()
                before = self._snapshot_job(job_id)
                self.assertFalse(self._recover(job_id))
                self.assertEqual(self._snapshot_job(job_id), before)

    def test_repeated_recovery_preserves_first_recovery_record(self):
        self._seed(status="running")
        self.assertTrue(self._recover())
        before = self._snapshot_job()
        self.assertFalse(self._recover())
        self.assertEqual(self._snapshot_job(), before)

    def test_missing_job_returns_false_without_creating_record(self):
        self.assertFalse(self._recover(999999))
        self.assertEqual(self._count(), 0)

    def test_only_requested_job_changes_and_report_jobs_are_untouched(self):
        from model import ReportJob
        self._seed(status="running")
        self._seed(job_id=4)
        self._seed(job_id=5, paper_id=3, document_id=13, status="running")
        before = {n: self._snapshot_job(n) for n in (4, 5)}
        with TestingSessionLocal() as db:
            db.add(ResearchTask(id=10, title="报告任务", question="报告问题", status="pending"))
            db.flush()
            db.add(ReportJob(id=3, task_id=10, status="running", stage="generating", analysis_snapshot={}))
            db.commit()
        self.assertTrue(self._recover(3))
        for number, previous in before.items():
            self.assertEqual(self._snapshot_job(number), previous)
        with TestingSessionLocal() as db:
            report = db.get(ReportJob, 3)
            self.assertEqual((report.status, report.stage), ("running", "generating"))

    def test_commit_failure_rolls_back_and_keeps_job_running(self):
        self._seed(status="running")
        before = self._snapshot_job()
        with TestingSessionLocal() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("模拟恢复提交失败")):
                with self.assertRaisesRegex(RuntimeError, "提交失败"):
                    self.jobs.recover_interrupted_preparation_job(db, 3)
            self.assertFalse(db.in_transaction())
        self.assertEqual(self._snapshot_job(), before)

    def test_invalid_script_input_never_opens_database(self):
        for answer in ("abc", "1.5", "", "0", "-3"):
            with self.subTest(answer=answer), patch.object(self.script, "SessionLocal") as sessions:
                inputs, output = self._run_script([answer])
                sessions.assert_not_called()
                self.assertEqual(inputs.call_count, 1)
                self.assertTrue(output.called)

    def test_script_missing_job_does_not_ask_confirmation(self):
        inputs, output = self._run_script(["999999"])
        self.assertEqual(inputs.call_count, 1)
        output.assert_any_call("没有找到这个论文准备作业。")
        self.assertEqual(self._count(), 0)

    def test_script_terminal_job_is_preserved_without_confirmation(self):
        for job_id, status in ((3, "succeeded"), (4, "failed")):
            with self.subTest(status=status):
                self._seed(job_id=job_id, status=status)
                before = self._snapshot_job(job_id)
                inputs, output = self._run_script([str(job_id)])
                self.assertEqual(inputs.call_count, 1)
                output.assert_any_call("这个作业已经结束，不需要恢复。")
                self.assertEqual(self._snapshot_job(job_id), before)

    def test_cancelled_or_incorrect_confirmation_keeps_job_unchanged(self):
        self._seed(status="running")
        before = self._snapshot_job()
        for answer in ("", "取消", "确认"):
            with self.subTest(answer=answer):
                inputs, output = self._run_script(["3", answer])
                self.assertEqual(inputs.call_count, 2)
                output.assert_any_call("已取消恢复。")
                self.assertEqual(self._snapshot_job(), before)

    def test_script_confirmation_displays_distinct_job_paper_and_document_ids(self):
        self._seed(status="running")
        inputs, output = self._run_script([" 3 ", " 确认恢复 "])
        self.assertIn("所有Celery Worker已停止", inputs.call_args_list[1].args[0])
        for line in ("作业编号：3", "论文编号：9", "PDF文档编号：12", "旧作业已标记为失败。"):
            output.assert_any_call(line)
        self.assertEqual(self._get()["stage"], "interrupted")
        self.publish.assert_not_called()

    def test_state_change_after_display_does_not_overwrite_success(self):
        self._seed(status="running")
        before = []
        def answer(prompt):
            if "请输入" in prompt:
                return "3"
            with TestingSessionLocal() as db:
                job = db.get(self.Job, 3)
                job.status, job.stage = "succeeded", "finished"
                db.commit()
            before.append(self._snapshot_job())
            return "确认恢复"
        inputs, output = self._run_script(answer)
        output.assert_any_call("作业状态已经变化，本次没有修改记录。")
        self.assertEqual(self._snapshot_job(), before[0])

    def test_recovery_unblocks_submission_and_old_message_cannot_run(self):
        self._seed(status="running")
        data_before = self._snapshot_data()
        self.assertEqual(client.post(self.url).status_code, 409)
        self.assertTrue(self._recover())
        old = self._snapshot_job()
        with patch("paper_preparation_worker.paper_pipeline.prepare_paper_document") as prepare:
            self.worker.run_preparation_job.run(3)
            prepare.assert_not_called()
        self.assertEqual(self._snapshot_job(), old)
        response = client.post(self.url)
        self.assertEqual(response.status_code, 202, response.text)
        new_id = response.json()["id"]
        self.assertNotEqual(new_id, 3)
        self.publish.assert_called_once_with(args=[new_id], retry=False)
        with patch("paper_pipeline.extract_pdf_text", side_effect=AssertionError("已有页面应复用")):
            self.worker.run_preparation_job.run(new_id)
        job = self._get(new_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["indexed_count"], 0)
        self.embed.assert_not_called()
        self.assertEqual(self._snapshot_data(), data_before)
        self.assertEqual(self._snapshot_job(), old)


if __name__ == "__main__":
    unittest.main()
