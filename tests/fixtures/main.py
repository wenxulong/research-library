from research_library.core.config import PROJECT_ROOT


"""测试 API、文件上传和 PDF 解析功能。"""


import unittest


import json


import pymupdf


from fastapi.testclient import TestClient


from sqlalchemy import create_engine


from sqlalchemy.orm import sessionmaker


from sqlalchemy.pool import StaticPool


from urllib.error import URLError


from research_library.main import app


from research_library.db.database import Base, get_db


from research_library.db.model import Paper, PaperChunk, PaperChunkEmbedding, PaperDocument, ResearchTask


import research_library.retrieval.embedding_indexer as embedding_indexer


from pathlib import Path


from tempfile import TemporaryDirectory


from research_library.retrieval.pdf_parser import extract_pdf_text


from research_library.retrieval.pdf_parser import ExtractedPage


from research_library.retrieval.text_chunker import chunk_pages


import research_library.services.document_service as document_service


from types import SimpleNamespace


from unittest.mock import Mock, patch


import research_library.retrieval.semantic_search as semantic_search


import research_library.retrieval.query_rewriter as query_rewriter


import research_library.agents.paper_analysis_agent as paper_analysis_agent


import research_library.retrieval.rag_service as rag_service


import research_library.services.review_service as review_service


import research_library.agents.review_agent as review_agent


import research_library.agents.revision_agent as revision_agent


import research_library.services.field_splitter as field_splitter


from research_library.services.review_service import ReviewClaim, ReviewEvidence


from research_library.schemas import ResearchTaskAnalysisResponse, AnalysisSourceResponse, ReviewItemResponse, ResearchTaskReviewResponse


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


class ComparisonDatabaseFixture:
    """两个论文、五份来源的共用环境；实际引用只使用资料1、3、5。"""

    def setUp(self):
        from research_library.db.model import ResearchTaskPaper
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
        from research_library.db.model import ResearchTaskAnalysisVersion
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
        with patch('research_library.agents.comparison_agent.generate_text', return_value=json.dumps(self.answer) if answer is None else answer) as generate:
            response = client.get(url) if endpoint == "comparison-context" else client.post(url)
        self.assertEqual(response.status_code, status_code, response.text)
        self.assertEqual(self._snapshot(), before)
        return response, generate


class PaperPreparationJobFixture:
    """后台论文准备：实际接口、PDF解析、分块和数据库，模拟队列及向量服务。"""

    def setUp(self):
        from research_library.db.model import PaperPreparationJob, PaperPage
        import research_library.workers.paper_preparation_worker as worker
        import research_library.repositories.paper_preparation_job_crud as jobs
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
        self.publish = self.enterContext(patch('research_library.api.papers.run_preparation_job.apply_async'))
        self.enterContext(patch('research_library.workers.paper_preparation_worker.SessionLocal', TestingSessionLocal))
        self.embed = self.enterContext(patch('research_library.retrieval.embedding_indexer.embed_texts',
            side_effect=lambda texts: [[0.1, 0.2, 0.3] for _ in texts]))
        # 测试禁止访问真实模型或真实Ollama。
        for target in ('research_library.core.llm_service.generate_text', 'research_library.retrieval.embedding_service.urlopen'):
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
