from research_library.api.tasks import read_research_task_analysis
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
import research_library.repositories.task_crud as task_crud
import research_library.services.comparison_service as comparison_service
import research_library.agents.comparison_agent as comparison_agent
import research_library.services.comparison_review as comparison_review
import research_library.services.comparison_workflow as comparison_workflow
import research_library.services.comparison_evidence_workflow as comparison_evidence_workflow
import research_library.services.comparison_report_builder as comparison_report_builder
from research_library.db.database import get_db
from research_library.schemas import ResearchTaskReviewResponse, ComparisonContextResponse, ComparisonReportResponse, CheckedComparisonReportResponse, ReportJobSummaryResponse
from fastapi import Query
from pydantic import ValidationError
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
import research_library.repositories.report_job_crud as report_job_crud
from research_library.workers.report_worker import run_report_job
from research_library.schemas import ReportJobResponse
from fastapi.responses import Response
from research_library.services.report_export import build_report_markdown

from fastapi import APIRouter

router = APIRouter()

@router.get(
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


@router.post(
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


@router.post(
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


@router.post(
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


@router.post(
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


@router.post(
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


@router.get(
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


@router.get(
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


@router.get(
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
