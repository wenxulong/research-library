"""定义科研资料库的 FastAPI 接口，并协调数据库和文件服务。"""
import io

from fastapi import Depends, FastAPI, HTTPException, File, UploadFile
from sqlalchemy.orm import Session
from research_report_export import build_research_report_zip

import research_question_agent
import crud
import task_crud
import document_crud
import page_crud
import chunk_crud
import paper_pipeline
import analysis_adoption
import analysis_restore
import revision_agent
import comparison_service
import comparison_agent
import comparison_review
import comparison_workflow
import comparison_evidence_workflow
import comparison_report_builder
import research_question_crud
import research_plan_service
import experiment_import_service
import experiment_statistics_service
import experiment_chart_service
import experiment_analysis_agent
import paper_draft_service
import paper_draft_agent
import paper_draft_crud

from model import ExperimentImport
from model import PaperDraft
from model import ResearchQuestionSet
from paper_draft_export import build_paper_draft_zip
from pdf_parser import ExtractedPage
from text_chunker import chunk_pages
from pdf_parser import extract_pdf_text
from document_service import delete_pdf, save_pdf
from database import get_db
import review_agent
from schemas import (
    PaperCreate,
    PaperResponse,
    ResearchTaskCreate,
    ResearchTaskPaperCreate,
    ResearchTaskPaperResponse,
    ResearchTaskResponse,
    ResearchTaskStatusUpdate,
    PaperDocumentResponse, PaperPageResponse,
    PaperChunkResponse,
    ResearchTaskChunkMatch,
    ResearchTaskSemanticChunkMatch,
    PaperEmbeddingIndexResponse,
    RAGQuestionRequest,
    RAGSourceResponse,
    RAGAnswerResponse,
    PaperPrepareResponse,
    AnalysisSourceResponse,
    ResearchTaskAnalysisResponse,
    ResearchTaskReviewResponse,
    ResearchTaskRevisionResponse,
    ResearchTaskCheckedRevisionResponse,
    EvidenceNoteAdoptRequest,
    EvidenceNoteAdoptResponse,
    AnalysisVersionSummaryResponse,
    AnalysisVersionDetailResponse,
    AnalysisRestoreRequest,
    AnalysisRestoreResponse,
    ComparisonContextResponse,
    ComparisonReportResponse,
    CheckedComparisonReportResponse,
    ReportJobSummaryResponse,
    PaperPreparationJobSummaryResponse,
    ResearchQuestionsResponse,
    SavedResearchQuestionsResponse,
    ResearchQuestionSelectRequest,
    ResearchConditions,
    ResearchPlanResponse,
    ExperimentImportResponse,
    ExperimentStatisticsResponse,
    ExperimentAnalysisRequest,
    ExperimentAnalysisResponse,
    PaperDraftRequest,
    PaperDraftResponse,
    PaperDraftMaterials,
    SavedPaperDraftResponse,
    PaperDraftContent,
    PaperDraftExpandRequest,
    PaperDraftExpandResponse,
)

from urllib.error import URLError
from fastapi import Query

import semantic_search
import rag_service

import embedding_indexer
from embedding_service import EMBEDDING_MODEL

from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import paper_analysis_agent

from kombu.exceptions import OperationalError
from redis.exceptions import RedisError

import report_job_crud

from report_worker import run_report_job
from schemas import ReportJobResponse
from fastapi.responses import Response

from report_export import build_report_markdown

import paper_preparation_job_crud

from paper_preparation_worker import run_preparation_job
from schemas import PaperPreparationJobResponse

from fastapi.responses import JSONResponse
from paper_operation_guard import PaperOperationBusyError, protect_paper_mutation

from health_api import router as health_router

app = FastAPI(title="个人科研资料库")
app.include_router(health_router)

@app.exception_handler(task_crud.TaskStateConflictError)
@app.exception_handler(PaperOperationBusyError)
async def handle_paper_operation_busy(request, error):
    """论文操作冲突时返回409，并保留可查询的作业编号。"""
    return JSONResponse(status_code=409, content={"detail": str(error)})


FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"
FRONTEND_FILE = FRONTEND_DIST / "index.html"
app.mount("/app/assets", StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False), name="frontend-assets")

@app.get("/")
def read_root():
    """返回服务运行状态。"""
    return {"message": "个人科研资料库 API 正在运行"}


@app.get("/papers", response_model=list[PaperResponse])
def read_papers(db: Session = Depends(get_db)):
    """返回全部论文。"""
    return crud.get_papers(db)


@app.get("/papers/{paper_id}", response_model=PaperResponse)
def read_paper(paper_id: int, db: Session = Depends(get_db)):
    """返回指定论文，不存在时返回 404。"""
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )
    return paper


@app.post("/papers", status_code=201, response_model=PaperResponse)
def create_paper(paper_data: PaperCreate, db: Session = Depends(get_db)):
    """创建并返回一篇论文。"""
    return crud.create_paper(db, paper_data)


@app.put("/papers/{paper_id}", response_model=PaperResponse)
def update_paper(
    paper_id: int,
    paper_data: PaperCreate,
    db: Session = Depends(get_db),
):
    """修改论文并返回最新记录。"""
    paper = crud.get_paper_by_id(db, paper_id)
    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    return crud.update_paper(db, paper, paper_data)


@app.delete("/papers/{paper_id}")
@protect_paper_mutation
def delete_paper(paper_id: int, db: Session = Depends(get_db)):
    """删除指定论文及需要同步清理的关联资源。"""
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    document = document_crud.get_document_by_paper_id(
        db,
        paper_id,
    )

    document_path = None

    if document is not None:
        document_path = document.file_path

    deleted_paper = PaperResponse.model_validate(paper)

    crud.delete_paper(db, paper)

    if document_path is not None:
        delete_pdf(document_path)

    return {
        "message": "论文删除成功",
        "deleted_paper": deleted_paper,
    }

@app.get(
    "/research-tasks",
    response_model=list[ResearchTaskResponse],
)
def read_research_tasks(
    db: Session = Depends(get_db),
):
    """返回全部研究任务。"""
    return task_crud.get_tasks(db)


@app.get(
    "/research-tasks/{task_id}",
    response_model=ResearchTaskResponse,
)
def read_research_task(
    task_id: int,
    db: Session = Depends(get_db),
):
    """返回指定研究任务，不存在时返回 404。"""
    task = task_crud.get_task_by_id(db, task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    return task

@app.post(
    "/research-tasks",
    status_code=201,
    response_model=ResearchTaskResponse,
)
def create_research_task(
    task_data: ResearchTaskCreate,
    db: Session = Depends(get_db),
):
    """创建新的研究任务。"""
    return task_crud.create_task(db, task_data)

@app.patch(
    "/research-tasks/{task_id}/status",
    response_model=ResearchTaskResponse,
)
def update_research_task_status(
    task_id: int,
    task_data: ResearchTaskStatusUpdate,
    db: Session = Depends(get_db),
):
    """更新研究任务状态及结果。"""
    task = task_crud.get_task_by_id(db, task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    return task_crud.update_task_status(
        db,
        task,
        task_data,
    )

@app.delete("/research-tasks/{task_id}")
def delete_research_task(
    task_id: int,
    db: Session = Depends(get_db),
):
    """删除指定研究任务并返回删除前的数据。"""
    task = task_crud.get_task_by_id(db, task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    deleted_task = ResearchTaskResponse.model_validate(task)

    try:
        task_crud.delete_task(db, task)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return {
        "message": "研究任务删除成功",
        "deleted_task": deleted_task,
    }

@app.post(
    "/research-tasks/{task_id}/papers/{paper_id}",
    status_code=201,
    response_model=ResearchTaskPaperResponse,
)
def add_paper_to_research_task(
    task_id: int,
    paper_id: int,
    link_data: ResearchTaskPaperCreate,
    db: Session = Depends(get_db),
):
    """将论文加入研究任务，并阻止重复关联。"""
    task = task_crud.get_task_by_id(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )
    paper = crud.get_paper_by_id(db, paper_id)
    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )
    existing_link = task_crud.get_task_paper_link(
        db,
        task_id,
        paper_id,
    )
    if existing_link is not None:
        raise HTTPException(
            status_code=409,
            detail="这篇论文已经加入该研究任务",
        )
    return task_crud.add_paper_to_task(
        db,
        task,
        paper,
        link_data,
    )

@app.get(
    "/research-tasks/{task_id}/papers",
    response_model=list[ResearchTaskPaperResponse],
)
def read_research_task_papers(
    task_id: int,
    db: Session = Depends(get_db),
):
    """返回指定研究任务关联的论文。"""
    task = task_crud.get_task_by_id(db, task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    return task_crud.get_task_paper_links(
        db,
        task_id,
    )

@app.delete(
    "/research-tasks/{task_id}/papers/{paper_id}",
)
def remove_paper_from_research_task(
    task_id: int,
    paper_id: int,
    db: Session = Depends(get_db),
):
    """移除研究任务与论文之间的关联。"""
    task = task_crud.get_task_by_id(db, task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    link = task_crud.get_task_paper_link(
        db,
        task_id,
        paper_id,
    )

    if link is None:
        raise HTTPException(
            status_code=404,
            detail="这篇论文没有加入该研究任务",
        )

    deleted_link = ResearchTaskPaperResponse.model_validate(link)

    task_crud.remove_paper_from_task(
        db,
        link,
    )

    return {
        "message": "论文已从研究任务中移除",
        "deleted_link": deleted_link,
    }

@app.post(
    "/papers/{paper_id}/document",
    status_code=201,
    response_model=PaperDocumentResponse,
)
async def upload_paper_document(
    paper_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """验证并保存论文 PDF 及其元数据。"""
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    existing_document = document_crud.get_document_by_paper_id(
        db,
        paper_id,
    )

    if existing_document is not None:
        raise HTTPException(
            status_code=409,
            detail="这篇论文已经上传了PDF",
        )

    try:
        saved_pdf = await save_pdf(file)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error
    finally:
        await file.close()

    try:
        document = document_crud.create_document(
            db=db,
            paper=paper,
            original_name=saved_pdf.original_name,
            stored_name=saved_pdf.stored_name,
            file_path=saved_pdf.file_path,
            content_type=saved_pdf.content_type,
            file_size=saved_pdf.file_size,
        )
    except Exception:
        delete_pdf(saved_pdf.file_path)
        raise

    return document


@app.get(
    "/papers/{paper_id}/document",
    response_model=PaperDocumentResponse,
)
def read_paper_document(
    paper_id: int,
    db: Session = Depends(get_db),
):
    """返回指定论文的 PDF 元数据。"""
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    document = document_crud.get_document_by_paper_id(
        db,
        paper_id,
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="这篇论文还没有上传PDF",
        )

    return document


@app.delete("/papers/{paper_id}/document")
@protect_paper_mutation
def delete_paper_document(
    paper_id: int,
    db: Session = Depends(get_db),
):
    """删除 PDF 元数据和对应磁盘文件。"""
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    document = document_crud.get_document_by_paper_id(
        db,
        paper_id,
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="这篇论文还没有上传PDF",
        )

    deleted_document = PaperDocumentResponse.model_validate(document)
    file_path = document.file_path

    document_crud.delete_document(
        db,
        document,
    )

    delete_pdf(file_path)

    return {
        "message": "PDF删除成功",
        "deleted_document": deleted_document,
    }

@app.post(
    "/papers/{paper_id}/document/extract",
    response_model=list[PaperPageResponse],
)

@protect_paper_mutation
def extract_paper_document(
        paper_id: int,
        db: Session = Depends(get_db),
):
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    document = document_crud.get_document_by_paper_id(db, paper_id)

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="这篇论文还没有上传PDF",
        )

    try:
        extracted_pdf = extract_pdf_text(document.file_path)

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="PDF文件在磁盘上不存在",
        )from error

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        )from error

    return page_crud.replace_document_pages(
        db,
        document,
        extracted_pdf,
    )

@app.get(
    "/papers/{paper_id}/pages",
    response_model=list[PaperPageResponse],
)
def read_paper_pages(
    paper_id: int,
    db: Session = Depends(get_db),
):
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    document = document_crud.get_document_by_paper_id(db, paper_id)

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="这篇论文还没有上传PDF",
        )

    return page_crud.get_pages_by_document_id(
        db,
        document.id,
    )

@app.post(
    "/papers/{paper_id}/chunks",
    response_model=list[PaperChunkResponse],
)
@protect_paper_mutation
def create_paper_chunks(
    paper_id: int,
    chunk_size: int = 500,
    overlap: int = 100,
    db: Session = Depends(get_db),
):
    """根据已提取的页面正文生成并保存文本块。"""
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    document = document_crud.get_document_by_paper_id(db, paper_id)

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="这篇论文还没有上传PDF",
        )

    saved_pages = page_crud.get_pages_by_document_id(
        db,
        document.id,
    )

    if not saved_pages:
        raise HTTPException(
            status_code=409,
            detail="请先提取PDF正文",
        )

    pages = [
        ExtractedPage(
            page_number=page.page_number,
            text=page.text,
        )
        for page in saved_pages
    ]

    try:
        text_chunks = chunk_pages(
            pages,
            chunk_size=chunk_size,
            overlap=overlap,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    if not text_chunks:
        raise HTTPException(
            status_code=422,
            detail="PDF没有可分块的文字",
        )

    return chunk_crud.replace_document_chunks(
        db,
        document,
        text_chunks,
    )

@app.get(
    "/papers/{paper_id}/chunks",
    response_model=list[PaperChunkResponse],
)
def read_paper_chunks(
    paper_id: int,
    db: Session = Depends(get_db),
):
    """查询一篇论文已经保存的文本块。"""
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    document = document_crud.get_document_by_paper_id(db, paper_id)

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="这篇论文还没有上传PDF",
        )

    return chunk_crud.get_chunks_by_document_id(
        db,
        document.id,
    )

@app.get(
    "/papers/{paper_id}/chunks/search",
    response_model=list[PaperChunkResponse],
)
def search_paper_chunks(
    paper_id: int,
    q: str,
    db: Session = Depends(get_db),
):
    """在指定论文的文本块中查找关键词。"""
    keyword = q.strip()

    if not keyword:
        raise HTTPException(
            status_code=400,
            detail="关键词不能为空",
        )

    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    document = document_crud.get_document_by_paper_id(db, paper_id)

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="这篇论文还没有上传PDF",
        )

    return chunk_crud.search_chunks_by_document_id(
        db,
        document.id,
        keyword,
    )

@app.get(
    "/research-tasks/{task_id}/chunks/search",
    response_model=list[ResearchTaskChunkMatch],
)
def search_research_task_chunks(
    task_id: int,
    q: str,
    db: Session = Depends(get_db),
):
    """在研究任务关联的论文中搜索正文。"""
    keyword = q.strip()

    if not keyword:
        raise HTTPException(
            status_code=400,
            detail="关键词不能为空",
        )

    task = task_crud.get_task_by_id(db, task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    matches = task_crud.search_task_chunks(
        db,
        task_id,
        keyword,
    )

    results = []

    for chunk, paper in matches:
        results.append(
            ResearchTaskChunkMatch(
                paper_id=paper.id,
                paper_title=paper.title,
                chunk_id=chunk.id,
                page_number=chunk.page_number,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
            )
        )

    return results

@app.get(
    "/research-tasks/{task_id}/chunks/semantic-search",
    response_model=list[ResearchTaskSemanticChunkMatch],
)
def search_research_task_chunks_semantically(
        task_id: int,
        q: str,
        top_k: int = Query(default=3, ge=1, le=20),
        db: Session = Depends(get_db),
):
    question = q.strip()
    if not question:
        raise HTTPException(
            status_code=400,
            detail="问题不能为空",
        )

    task = task_crud.get_task_by_id(db, task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    try:
        matches = semantic_search.search_task_chunks_semantic(
            db,
            task_id,
            question,
            top_k,
        )
    except URLError as exc:
        raise HTTPException(
            status_code=503,
            detail="无法连接向量模型，请确认 Ollama 已启动且模型已下载",
        ) from exc

    results = []

    for chunk, paper, score in matches:
        results.append(
            ResearchTaskSemanticChunkMatch(
                paper_id=paper.id,
                paper_title=paper.title,
                chunk_id=chunk.id,
                page_number=chunk.page_number,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                score=score,
            )
        )

    return results

@app.post(
    "/papers/{paper_id}/chunks/embeddings",
    response_model=PaperEmbeddingIndexResponse,
)
@protect_paper_mutation
def index_paper_embeddings(
    paper_id: int,
    db: Session = Depends(get_db),
):
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    document = document_crud.get_document_by_paper_id(db, paper_id)

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="这篇论文还没有上传PDF",
        )

    chunks = chunk_crud.get_chunks_by_document_id(db, document.id)

    if not chunks:
        raise HTTPException(
            status_code=409,
            detail="请先为PDF生成文本块",
        )

    try:
        indexed_count = embedding_indexer.index_document_chunks(
            db,
            document.id,
        )
    except URLError as exc:
        raise HTTPException(
            status_code=503,
            detail="无法连接向量模型，请确认 Ollama 已启动且模型已下载",
        ) from exc

    return PaperEmbeddingIndexResponse(
        paper_id=paper_id,
        document_id=document.id,
        model_name=EMBEDDING_MODEL,
        total_count=len(chunks),
        indexed_count=indexed_count,
    )

@app.post(
    "/research-tasks/{task_id}/answer",
    response_model=RAGAnswerResponse,
)
def answer_research_task_question(
    task_id: int,
    question_data: RAGQuestionRequest,
    db: Session = Depends(get_db),
):
    task = task_crud.get_task_by_id(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    try:
        result = rag_service.answer_research_question(
            db=db,
            task_id=task_id,
            question=question_data.question,
            top_k=question_data.top_k,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except URLError as error:
        raise HTTPException(
            status_code=503,
            detail="无法连接向量模型，请确认Ollama已经启动",
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    response_sources = []

    for source in result.sources:
        response_sources.append(
            RAGSourceResponse(
                paper_id=source.paper_id,
                paper_title=source.paper_title,
                chunk_id=source.chunk_id,
                page_number=source.page_number,
                score=source.score,
            )
        )

    return RAGAnswerResponse(
        question=result.question,
        answer=result.answer,
        sources=response_sources,
    )

@app.post(
    "/papers/{paper_id}/prepare",
    response_model=PaperPrepareResponse,
)
@protect_paper_mutation
def prepare_paper(
    paper_id: int,
    chunk_size: int = Query(default=500, ge=1, le=5000),
    overlap: int = Query(default=100, ge=0, le=4999),
    db: Session = Depends(get_db),
):
    """一键完成PDF正文提取、文本分块和向量生成。"""

    paper = crud.get_paper_by_id(
        db,
        paper_id,
    )

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )

    document = document_crud.get_document_by_paper_id(
        db,
        paper_id,
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="这篇论文还没有上传PDF",
        )

    try:
        result = paper_pipeline.prepare_paper_document(
            db=db,
            document=document,
            chunk_size=chunk_size,
            overlap=overlap,
        )

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="PDF文件在磁盘上不存在",
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except URLError as error:
        raise HTTPException(
            status_code=503,
            detail="无法连接Ollama向量服务",
        ) from error

    return PaperPrepareResponse(
        paper_id=paper.id,
        document_id=document.id,
        model_name=EMBEDDING_MODEL,
        page_count=result.page_count,
        chunk_count=result.chunk_count,
        indexed_count=result.indexed_count,
    )

@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
def read_frontend():
    """返回Vue构建页面；不把未经编译的源码交给浏览器。"""
    if not FRONTEND_FILE.is_file():
        raise HTTPException(status_code=503, detail="前端尚未构建，请在frontend目录执行npm run build")
    return FileResponse(FRONTEND_FILE, headers={"Cache-Control": "no-cache"})

@app.post(
    "/research-tasks/{task_id}/analysis",
    response_model=ResearchTaskAnalysisResponse,
)
def analysis_research_task_endpoint(
        task_id: int,
        db: Session = Depends(get_db),
):
    task = task_crud.get_task_by_id(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    expected_input = task_crud.task_input_snapshot(db, task)

    try:
        result = (
            paper_analysis_agent
            .analyze_research_task(
                db=db,
            task_id=task_id,
            question=task.question,
            )
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except URLError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "无法连接向量模型，"
                "请确认Ollama已经启动"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    response_sources = []
    for source in result.sources:
        response_sources.append(
            AnalysisSourceResponse(
                paper_id=source.paper_id,
                paper_title=source.paper_title,
                chunk_id=source.chunk_id,
                page_number=source.page_number,
                score=source.score,
            )
        )

    response = ResearchTaskAnalysisResponse(
        task_id=task_id,
        question=result.question,
        summary=result.summary,
        methods=result.methods,
        experiments=result.experiments,
        conclusions=result.conclusions,
        limitations=result.limitations,
        evidence_notes=result.evidence_notes,
        sources=response_sources,
        entities=result.entities,
    )

    task_crud.save_task_analysis(
        db=db,
        task=task,
        analysis=response,
        expected_input=expected_input,
    )

    return response

@app.get(
    "/research-tasks/{task_id}/analysis",
    response_model=ResearchTaskAnalysisResponse,
)
def read_research_task_analysis(
        task_id: int,
        db: Session = Depends(get_db),
):
    task = task_crud.get_task_by_id(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    task_crud.ensure_current_analysis(task)

    if not task.result:
        raise HTTPException(
            status_code=404,
            detail="这个研究任务还没有保存分析结果",
        )

    try:
        analysis = (
            ResearchTaskAnalysisResponse
            .model_validate_json(task.result)
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=409,
            detail="已保存内容不是结构化分析结果，请重新执行分析",
        ) from error

    if analysis.task_id != task_id:
        raise HTTPException(
            status_code=409,
            detail="已保存分析结果的任务编号不匹配，请重新执行分析",
        )

    return analysis

@app.post(
    "/research-tasks/{task_id}/review",
    response_model=ResearchTaskReviewResponse,
)
def review_research_task_endpoint(
    task_id: int,
    db: Session = Depends(get_db),
):
    """执行审校，并保存最新审校结果。"""

    analysis = read_research_task_analysis(
        task_id=task_id,
        db=db,
    )

    task = task_crud.get_task_by_id(db, task_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    expected_analysis = task.result

    try:
        result = review_agent.review_research_task(
            db=db,
            analysis=analysis,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    task_crud.save_task_review(
        db=db,
        task=task,
        review=result,
        expected_analysis=expected_analysis,
    )

    return result

@app.get(
    "/research-tasks/{task_id}/review",
    response_model=ResearchTaskReviewResponse,
)
def read_research_task_review(
        task_id: int,
        db: Session = Depends(get_db),
):
    task = task_crud.get_task_by_id(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {task_id} 的研究任务",
        )

    if not task.review_result:
        raise HTTPException(
            status_code=404,
            detail="这个研究任务还没有保存审校结果，请先执行审校",
        )

    try:
        review = ResearchTaskReviewResponse.model_validate_json(
            task.review_result,
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=409,
            detail="已保存的审校结果格式不正确，请重新执行审校",
        ) from error

    if review.task_id != task_id:
        raise HTTPException(
            status_code=409,
            detail="已保存审校结果的任务编号不匹配，请重新执行审校",
        )

    return review

@app.post(
    "/research-tasks/{task_id}/revisions",
    response_model=ResearchTaskRevisionResponse,
)
def revise_research_task_endpoint(
    task_id: int,
    db: Session = Depends(get_db),
):
    analysis = read_research_task_analysis(
        task_id=task_id,
        db=db,
    )
    review = read_research_task_review(
        task_id=task_id,
        db=db,
    )

    try:
        result = revision_agent.revise_research_task(
            db=db,
            analysis=analysis,
            review=review,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    return result

@app.post(
    "/research-tasks/{task_id}/revisions/review",
    response_model=ResearchTaskCheckedRevisionResponse,
)
def revise_and_review_research_task_endpoint(
    task_id: int,
    db: Session = Depends(get_db),
):
    """生成修改建议，并返回修改后的审校结果。"""

    analysis = read_research_task_analysis(
        task_id=task_id,
        db=db,
    )

    review = read_research_task_review(
        task_id=task_id,
        db=db,
    )

    try:
        result = revision_agent.revise_and_review_research_task(
            db=db,
            analysis=analysis,
            review=review,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    return result

@app.post(
    "/research-tasks/{task_id}/analysis/adopt-evidence-note",
    response_model=EvidenceNoteAdoptResponse,
)
def adopt_evidence_note_endpoint(
        task_id: int,
        adoption: EvidenceNoteAdoptRequest,
        db: Session = Depends(get_db),
):
    try:
        response = analysis_adoption.adopt_evidence_note(
            db=db,
            task_id=task_id,
            adoption=adoption,
        )
        return response

    except LookupError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

@app.get(
    "/research-tasks/{task_id}/analysis/versions",
    response_model=list[AnalysisVersionSummaryResponse],
)
def get_research_task_versions(
    task_id: int,
    db: Session = Depends(get_db),
):
    task = task_crud.get_task_by_id(db=db, task_id=task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这个研究任务",
        )
    return task_crud.get_analysis_versions(db=db, task_id=task_id,)

@app.get(
    "/research-tasks/{task_id}/analysis/versions/{version_id}",
    response_model=AnalysisVersionDetailResponse,
)
def read_analysis_version(
    task_id: int,
    version_id: int,
    db: Session = Depends(get_db),
):
    """返回一次修改前后的分析及原审校结果。"""

    version = task_crud.get_analysis_version(
        db=db,
        task_id=task_id,
        version_id=version_id,
    )

    if version is None:
        raise HTTPException(
            status_code=404,
            detail="这个研究任务下没有找到指定版本",
        )

    try:
        previous_analysis = (
            ResearchTaskAnalysisResponse.model_validate_json(
                version.previous_analysis
            )
        )

        updated_analysis = (
            ResearchTaskAnalysisResponse.model_validate_json(
                version.updated_analysis
            )
        )

        previous_review = None

        if version.previous_review is not None:
            previous_review = (
                ResearchTaskReviewResponse.model_validate_json(
                    version.previous_review
                )
            )

    except ValidationError as error:
        raise HTTPException(
            status_code=409,
            detail="这个版本保存的数据格式不正确",
        ) from error

    return AnalysisVersionDetailResponse(
        id=version.id,
        task_id=version.task_id,
        reason=version.reason,
        created_at=version.created_at,
        previous_analysis=previous_analysis,
        updated_analysis=updated_analysis,
        previous_review=previous_review,
    )

@app.post(
    "/research-tasks/{task_id}/analysis/versions/{version_id}/restore",
    response_model=AnalysisRestoreResponse,
)
def restore_analysis_version_endpoint(
    task_id: int,
    version_id: int,
    restore: AnalysisRestoreRequest,
    db: Session = Depends(get_db),
):
    """恢复某条历史记录的修改前分析。"""

    try:
        response = analysis_restore.restore_analysis_version(
            db=db,
            task_id=task_id,
            version_id=version_id,
            restore=restore,
        )

        return response

    except LookupError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

@app.get(
    "/research-tasks/{task_id}/comparison-context",
    response_model=ComparisonContextResponse,
)
def read_comparison_context(
        task_id: int,
        db: Session = Depends(get_db),
):
    analysis = read_research_task_analysis(task_id=task_id, db=db)
    try:
        return comparison_service.build_comparison_context(
            db=db,
            analysis=analysis,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

@app.post(
    "/research-tasks/{task_id}/comparison-report",
    response_model=ComparisonReportResponse,
)
def generate_comparison_report_endpoint(
    task_id: int,
    db: Session = Depends(get_db),
):
    """根据已保存分析的引用原文生成论文对比报告预览。"""

    analysis = read_research_task_analysis(
        task_id=task_id,
        db=db,
    )

    try:
        return comparison_agent.generate_comparison_report(
            db=db,
            analysis=analysis,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

@app.post(
    "/research-tasks/{task_id}/comparison-report/review",
    response_model=ResearchTaskReviewResponse,
)
def review_comparison_report_endpoint(
    task_id: int,
    report: ComparisonReportResponse,
    db: Session = Depends(get_db),
):
    """审校提交的对比报告，返回预览，不覆盖分析的审校结果。"""

    analysis = read_research_task_analysis(task_id=task_id, db=db)
    try:
        return comparison_review.review_comparison_report(
            db=db,
            analysis=analysis,
            report=report,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post(
    "/research-tasks/{task_id}/comparison-report/checked",
    response_model=CheckedComparisonReportResponse,
)
def checked_comparison_report_endpoint(
    task_id: int,
    report: ComparisonReportResponse | None = None,
    db: Session = Depends(get_db),
):
    """可提交旧报告修正，也可不传报告直接生成、审校并有限修正。"""
    analysis = read_research_task_analysis(task_id=task_id, db=db)
    try:
        return comparison_workflow.run_checked_comparison(db, analysis, report)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

@app.post(
    "/research-tasks/{task_id}/comparison-report/supplemented",
    response_model=CheckedComparisonReportResponse,
)
def supplemented_comparison_report_endpoint(
    task_id: int,
    report: ComparisonReportResponse | None = None,
    db: Session = Depends(get_db),
):
    """生成或接收原始报告，完成补证、修正和复审，返回报告预览。"""

    analysis = read_research_task_analysis(
        task_id=task_id,
        db=db,
    )

    try:
        if report is None:
            report = comparison_agent.generate_comparison_report(
                db=db,
                analysis=analysis,
            )

        processing_result = (
            comparison_evidence_workflow.process_comparison_evidence(
                db=db,
                analysis=analysis,
                report=report,
            )
        )

        checked_report = (
            comparison_report_builder.assemble_comparison_report(
                analysis=analysis,
                result=processing_result,
            )
        )

        return checked_report

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

@app.post(
    "/research-tasks/{task_id}/report-jobs",
    status_code=202,
    response_model=ReportJobResponse,
)
def submit_report_job(
    task_id: int,
    db: Session = Depends(get_db),
):
    """创建报告作业，并向Celery队列提交作业编号。"""

    try:
        job = report_job_crud.create_report_job(
            db=db,
            task_id=task_id,
        )
    except LookupError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    try:
        run_report_job.apply_async(
            args=[job.id],
            retry=False,
        )
    except (OperationalError, RedisError) as error:
        report_job_crud.mark_dispatch_failed(
            db=db,
            job_id=job.id,
        )

        raise HTTPException(
            status_code=503,
            detail={
                "message": "作业消息发送失败，请根据作业编号查询状态",
                "job_id": job.id,
            },
        ) from error

    return job

@app.get(
    "/research-tasks/{task_id}/report-jobs",
    response_model=list[ReportJobSummaryResponse],
)
def read_research_task_report_jobs(
    task_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """查询指定研究任务的报告作业历史。"""

    task = task_crud.get_task_by_id(
        db=db,
        task_id=task_id,
    )

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这个研究任务",
        )

    return report_job_crud.get_report_jobs_by_task(
        db=db,
        task_id=task_id,
        limit=limit,
        offset=offset,
    )

@app.get(
    "/report-jobs/{job_id}",
    response_model=ReportJobResponse,
)
def read_report_job(
    job_id: int,
    db: Session = Depends(get_db),
):
    """查询一次报告作业的状态、结果和失败原因。"""

    job = report_job_crud.get_report_job(
        db=db,
        job_id=job_id,
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这个报告作业",
        )

    return job

@app.get(
    "/report-jobs/{job_id}/export",
    response_class=Response,
)
def export_report_job(
    job_id: int,
    db: Session = Depends(get_db),
) -> Response:
    """下载已经生成的报告，未通过审校的报告会标记为待核验。"""

    job = report_job_crud.get_report_job(
        db=db,
        job_id=job_id,
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这个报告作业",
        )

    if job.status != "succeeded":
        raise HTTPException(
            status_code=409,
            detail="作业尚未成功完成，不能导出报告",
        )

    if job.result is None:
        raise HTTPException(
            status_code=500,
            detail="作业已完成，但没有保存报告结果",
        )

    try:
        checked_report = CheckedComparisonReportResponse.model_validate(
            job.result
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=500,
            detail="保存的报告格式不正确，无法导出",
        ) from error

    if (
            checked_report.task_id != job.task_id
            or checked_report.report.task_id != job.task_id
    ):
        raise HTTPException(
            status_code=500,
            detail="报告与作业所属的研究任务不一致",
        )

    markdown = build_report_markdown(
        checked_report=checked_report,
        job_id=job.id,
    )

    filename = f"report-task-{job.task_id}-job-{job.id}.md"

    return Response(
        content=markdown,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )

@app.post(
    "/papers/{paper_id}/preparation-jobs",
    status_code=202,
    response_model=PaperPreparationJobResponse,
)
def submit_paper_preparation_job(
    paper_id: int,
    db: Session = Depends(get_db),
):
    """创建论文准备作业，并把作业编号提交给Celery。"""

    try:
        job = paper_preparation_job_crud.create_preparation_job(
            db=db,
            paper_id=paper_id,
        )

    except LookupError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    try:
        run_preparation_job.apply_async(
            args=[job.id],
            retry=False,
        )

    except (OperationalError, RedisError) as error:
        paper_preparation_job_crud.mark_preparation_dispatch_failed(
            db=db,
            job_id=job.id,
        )

        raise HTTPException(
            status_code=503,
            detail={
                "message": "作业消息发送失败，请根据作业编号查询状态",
                "job_id": job.id,
            },
        ) from error

    return job

@app.get(
    "/paper-preparation-jobs/{job_id}",
    response_model=PaperPreparationJobResponse,
)
def read_paper_preparation_job(
    job_id: int,
    db: Session = Depends(get_db),
):
    """查询论文准备作业的当前阶段、结果和错误。"""

    job = paper_preparation_job_crud.get_preparation_job(
        db=db,
        job_id=job_id,
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这个论文准备作业",
        )

    return job

@app.get(
    "/papers/{paper_id}/preparation-jobs",
    response_model=list[PaperPreparationJobSummaryResponse],
)
def read_paper_preparation_jobs(
    paper_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """查询指定论文的准备作业历史。"""

    paper = crud.get_paper_by_id(
        db=db,
        paper_id=paper_id,
    )

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这篇论文",
        )

    return paper_preparation_job_crud.get_preparation_jobs_by_paper(
        db=db,
        paper_id=paper_id,
        limit=limit,
        offset=offset,
    )

@app.post(
    "/report-jobs/{job_id}/research-questions",
    response_model=SavedResearchQuestionsResponse,
)
def generate_research_questions_endpoint(
    job_id: int,
    db: Session = Depends(get_db),
):
    """根据一份已通过审校的报告生成候选研究问题。"""

    job = report_job_crud.get_report_job(db, job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这个报告作业",
        )

    if job.status != "succeeded" or not job.result:
        raise HTTPException(
            status_code=409,
            detail="报告尚未生成成功",
        )

    try:
        checked = CheckedComparisonReportResponse.model_validate(
            job.result,
        )

        if not checked.passed:
            raise ValueError("请先使用已通过审校的对比报告")

        result = research_question_agent.generate_research_questions(
            db=db,
            report_job_id=job.id,
            report=checked.report,
        )

        return research_question_crud.save_research_questions(
            db=db,
            result=result,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

@app.get(
    "/research-question-sets/{set_id}",
    response_model=SavedResearchQuestionsResponse,
)
def read_research_question_set(
    set_id: int,
    db: Session = Depends(get_db),
):
    """读取一组已保存的候选问题及选择结果。"""

    record = db.get(ResearchQuestionSet, set_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这组候选研究问题",
        )
    return record

@app.patch(
    "/research-question-sets/{set_id}/selection",
    response_model=SavedResearchQuestionsResponse,
)
def select_research_question_endpoint(
    set_id: int,
    selection: ResearchQuestionSelectRequest,
    db: Session = Depends(get_db),
):
    """选择候选问题，或修改最终确认的问题表述。"""
    record = db.get(ResearchQuestionSet, set_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这组候选研究问题",
        )

    try:
        return research_question_crud.select_research_question(
            db=db,
            record=record,
            selection=selection,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

@app.post(
    "/research-question-sets/{set_id}/plan",
    response_model=ResearchPlanResponse,
)
def generate_research_plan_endpoint(
    set_id: int,
    conditions: ResearchConditions,
    db: Session = Depends(get_db),
):
    """为用户选定的问题生成研究方案和实验计划。"""

    record = db.get(ResearchQuestionSet, set_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这组候选研究问题",
        )

    try:
        return research_plan_service.generate_research_plan(
            db=db,
            record=record,
            conditions=conditions,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

@app.get(
    "/research-question-sets/{set_id}/plan",
    response_model=ResearchPlanResponse,
)
def read_research_plan_endpoint(
    set_id: int,
    db: Session = Depends(get_db),
):
    """读取当前保存的研究方案和实验计划。"""
    record = db.get(ResearchQuestionSet, set_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这组候选研究问题",
        )
    if record.plan is None:
        raise HTTPException(
            status_code=404,
            detail="尚未生成研究计划，或选择问题后需要重新生成",
        )

    return ResearchPlanResponse.model_validate(record.plan)

@app.post(
    "/research-question-sets/{set_id}/experiment-imports",
    response_model=ExperimentImportResponse,
    status_code=201,
)
async def import_experiment_results_endpoint(
    set_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传CSV，保存实验结果和对应计划。"""

    record = db.get(ResearchQuestionSet, set_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这组候选研究问题",
        )

    try:
        content = await file.read()

        raw_csv = content.decode("utf-8-sig")

        return experiment_import_service.import_experiment_results(
            db=db,
            record=record,
            filename=file.filename or "results.csv",
            raw_csv=raw_csv,
        )

    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail="请上传UTF-8编码的CSV文件",
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    finally:
        await file.close()

@app.get(
    "/experiment-imports/{import_id}",
    response_model=ExperimentImportResponse,
)
def read_experiment_import(
    import_id: int,
    db: Session = Depends(get_db),
):
    """读取一批已导入的实验结果。"""

    imported = db.get(ExperimentImport, import_id)

    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    return imported

@app.get(
    "/experiment-imports/{import_id}/statistics",
    response_model=ExperimentStatisticsResponse,
)
def read_experiment_statistics(
    import_id: int,
    metric: str = Query(min_length=1),
    db: Session = Depends(get_db),
):
    """读取一次导入中指定指标的分组统计。"""

    imported = db.get(ExperimentImport, import_id)

    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        return experiment_statistics_service.summarize_experiment_results(
            imported=imported,
            metric=metric,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

@app.get(
    "/experiment-imports/{import_id}/chart",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def download_experiment_chart(
    import_id: int,
    metric: str = Query(min_length=1),
    dataset: str = Query(min_length=1),
    scenario: str = Query(min_length=1),
    db: Session = Depends(get_db),
):
    """下载指定指标、数据集和场景的方法对比图。"""

    imported = db.get(ExperimentImport, import_id)

    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        statistics = (
            experiment_statistics_service
            .summarize_experiment_results(
                imported=imported,
                metric=metric,
            )
        )

        image_bytes = experiment_chart_service.build_experiment_chart(
            statistics=statistics,
            dataset=dataset,
            scenario=scenario,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": (
                f'attachment; filename="experiment-{import_id}.png"'
            ),
        },
    )

@app.post(
    "/experiment-imports/{import_id}/analysis",
    response_model=ExperimentAnalysisResponse,
)
def analyze_experiment_results(
    import_id: int,
    request: ExperimentAnalysisRequest,
    db: Session = Depends(get_db),
):
    """根据一次实验导入记录生成多指标分析。"""
    imported = db.get(ExperimentImport, import_id)
    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        return experiment_analysis_agent.generate_experiment_analysis(
            imported=imported,
            request=request,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

@app.post(
    "/experiment-imports/{import_id}/research-report/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/zip": {},
            },
        },
    },
)
def export_research_report(
    import_id: int,
    request: ExperimentAnalysisRequest,
    db: Session = Depends(get_db),
):
    """生成实验分析并导出完整研究报告压缩包。"""
    imported = db.get(ExperimentImport, import_id)
    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        analysis = experiment_analysis_agent.generate_experiment_analysis(
            imported=imported,
            request=request,
        )

        archive_bytes = build_research_report_zip(
            imported=imported,
            analysis=analysis,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="research-report-{import_id}.zip"'
            ),
        },
    )

@app.post(
    "/experiment-imports/{import_id}/paper-draft",
    response_model=SavedPaperDraftResponse,
)
def create_paper_draft(
    import_id: int,
    request: PaperDraftRequest,
    db: Session = Depends(get_db),
):
    """生成论文初稿，并保存正文与写作材料。"""

    imported = db.get(
        ExperimentImport,
        import_id,
    )

    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        materials = paper_draft_service.build_paper_draft_materials(
            db=db,
            imported=imported,
            request=request,
        )

        draft = paper_draft_agent.generate_paper_draft(
            materials=materials,
        )

        record = paper_draft_crud.save_paper_draft(
            db=db,
            draft=draft,
            materials=materials,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    return paper_draft_crud.to_saved_paper_draft(record)

@app.post(
    "/experiment-imports/{import_id}/paper-draft/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/zip": {},
            },
        },
    },
)
def export_paper_draft(
    import_id: int,
    request: PaperDraftRequest,
    db: Session = Depends(get_db),
):
    """生成论文初稿，并下载正文、材料和图表压缩包。"""

    imported = db.get(
        ExperimentImport,
        import_id,
    )

    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        materials = paper_draft_service.build_paper_draft_materials(
            db=db,
            imported=imported,
            request=request,
        )

        draft = paper_draft_agent.generate_paper_draft(
            materials=materials,
        )

        archive_bytes = build_paper_draft_zip(
            db=db,
            draft=draft,
            materials=materials,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="paper-draft-{import_id}.zip"'
            ),
        },
    )

@app.get(
    "/paper-drafts/{draft_id}",
    response_model=SavedPaperDraftResponse,
)
def read_paper_draft(
    draft_id: int,
    db: Session = Depends(get_db),
):
    """读取已保存的论文初稿，不调用模型。"""

    record = db.get(
        PaperDraft,
        draft_id,
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这份论文初稿",
        )

    return paper_draft_crud.to_saved_paper_draft(record)

@app.get(
    "/paper-drafts/{draft_id}/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/zip": {},
            },
        },
    },
)
def export_saved_paper_draft(
    draft_id: int,
    db: Session = Depends(get_db),
):
    """使用已保存的正文和材料导出，不重新生成初稿。"""

    record = db.get(
        PaperDraft,
        draft_id,
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这份论文初稿",
        )

    draft = PaperDraftResponse.model_validate(
        record.draft_data,
    )

    materials = PaperDraftMaterials.model_validate(
        record.materials_snapshot,
    )

    try:
        archive_bytes = build_paper_draft_zip(
            db=db,
            draft=draft,
            materials=materials,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="paper-draft-{draft_id}.zip"'
            ),
        },
    )

@app.patch(
    "/paper-drafts/{draft_id}",
    response_model=SavedPaperDraftResponse,
)
def update_saved_paper_draft(
    draft_id: int,
    content: PaperDraftContent,
    db: Session = Depends(get_db),
):
    """保存人工修改的论文正文，不调用模型。"""

    record = db.get(
        PaperDraft,
        draft_id,
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这份论文初稿",
        )

    try:
        updated_record = paper_draft_crud.update_paper_draft(
            db=db,
            record=record,
            content=content,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    return paper_draft_crud.to_saved_paper_draft(
        updated_record,
    )

@app.post(
    "/paper-drafts/{draft_id}/expand",
    response_model=PaperDraftExpandResponse,
)
def expand_saved_paper_draft(
    draft_id: int,
    request: PaperDraftExpandRequest,
    db: Session = Depends(get_db),
):
    """生成章节扩写建议，不修改已经保存的初稿。"""

    record = db.get(
        PaperDraft,
        draft_id,
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这份论文初稿",
        )

    draft = PaperDraftResponse.model_validate(
        record.draft_data,
    )

    materials = PaperDraftMaterials.model_validate(
        record.materials_snapshot,
    )

    try:
        suggestion = paper_draft_agent.expand_paper_draft_section(
            draft=draft,
            materials=materials,
            request=request,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error

    return PaperDraftExpandResponse(
        draft_id=record.id,
        section=request.section,
        paragraphs=suggestion.paragraphs,
    )
