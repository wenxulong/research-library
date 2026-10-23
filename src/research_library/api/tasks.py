from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
import research_library.repositories.crud as crud
import research_library.repositories.task_crud as task_crud
import research_library.services.analysis_adoption as analysis_adoption
import research_library.services.analysis_restore as analysis_restore
import research_library.agents.revision_agent as revision_agent
from research_library.db.database import get_db
import research_library.agents.review_agent as review_agent
from research_library.schemas import ResearchTaskCreate, ResearchTaskPaperCreate, ResearchTaskPaperResponse, ResearchTaskResponse, ResearchTaskStatusUpdate, ResearchTaskChunkMatch, ResearchTaskSemanticChunkMatch, RAGQuestionRequest, RAGSourceResponse, RAGAnswerResponse, AnalysisSourceResponse, ResearchTaskAnalysisResponse, ResearchTaskReviewResponse, ResearchTaskRevisionResponse, ResearchTaskCheckedRevisionResponse, EvidenceNoteAdoptRequest, EvidenceNoteAdoptResponse, AnalysisVersionSummaryResponse, AnalysisVersionDetailResponse, AnalysisRestoreRequest, AnalysisRestoreResponse
from urllib.error import URLError
from fastapi import Query
import research_library.retrieval.semantic_search as semantic_search
import research_library.retrieval.rag_service as rag_service
from pydantic import ValidationError
import research_library.agents.paper_analysis_agent as paper_analysis_agent

from fastapi import APIRouter

router = APIRouter()

@router.get(
    "/research-tasks",
    response_model=list[ResearchTaskResponse],
)
def read_research_tasks(
    db: Session = Depends(get_db),
):
    """返回全部研究任务。"""
    return task_crud.get_tasks(db)


@router.get(
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


@router.post(
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


@router.patch(
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


@router.delete("/research-tasks/{task_id}")
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


@router.post(
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


@router.get(
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


@router.delete(
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


@router.get(
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


@router.get(
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


@router.post(
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


@router.post(
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


@router.get(
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


@router.post(
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


@router.get(
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


@router.post(
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


@router.post(
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


@router.post(
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


@router.get(
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


@router.get(
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


@router.post(
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
