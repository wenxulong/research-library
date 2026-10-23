from fastapi import Depends, HTTPException, File, UploadFile
from sqlalchemy.orm import Session
import research_library.repositories.crud as crud
import research_library.repositories.document_crud as document_crud
import research_library.repositories.page_crud as page_crud
import research_library.repositories.chunk_crud as chunk_crud
import research_library.services.paper_pipeline as paper_pipeline
from research_library.retrieval.pdf_parser import ExtractedPage
from research_library.retrieval.text_chunker import chunk_pages
from research_library.retrieval.pdf_parser import extract_pdf_text
from research_library.services.document_service import delete_pdf, save_pdf
from research_library.db.database import get_db
from research_library.schemas import PaperCreate, PaperResponse, PaperDocumentResponse, PaperPageResponse, PaperChunkResponse, PaperEmbeddingIndexResponse, PaperPrepareResponse, PaperPreparationJobSummaryResponse
from urllib.error import URLError
from fastapi import Query
import research_library.retrieval.embedding_indexer as embedding_indexer
from research_library.retrieval.embedding_service import EMBEDDING_MODEL
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
import research_library.repositories.paper_preparation_job_crud as paper_preparation_job_crud
from research_library.workers.paper_preparation_worker import run_preparation_job
from research_library.schemas import PaperPreparationJobResponse
from research_library.services.paper_operation_guard import protect_paper_mutation

from fastapi import APIRouter

router = APIRouter()

@router.get("/papers", response_model=list[PaperResponse])
def read_papers(db: Session = Depends(get_db)):
    """返回全部论文。"""
    return crud.get_papers(db)


@router.get("/papers/{paper_id}", response_model=PaperResponse)
def read_paper(paper_id: int, db: Session = Depends(get_db)):
    """返回指定论文，不存在时返回 404。"""
    paper = crud.get_paper_by_id(db, paper_id)

    if paper is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到编号为 {paper_id} 的论文",
        )
    return paper


@router.post("/papers", status_code=201, response_model=PaperResponse)
def create_paper(paper_data: PaperCreate, db: Session = Depends(get_db)):
    """创建并返回一篇论文。"""
    return crud.create_paper(db, paper_data)


@router.put("/papers/{paper_id}", response_model=PaperResponse)
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


@router.delete("/papers/{paper_id}")
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


@router.post(
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


@router.get(
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


@router.delete("/papers/{paper_id}/document")
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


@router.post(
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


@router.get(
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


@router.post(
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


@router.get(
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


@router.get(
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


@router.post(
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


@router.post(
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


@router.post(
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


@router.get(
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


@router.get(
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
