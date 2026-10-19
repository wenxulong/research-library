import task_crud
from paper_operation_guard import paper_operation_locks, ensure_no_active_preparation
from pydantic import ValidationError
from sqlalchemy.orm import Session, load_only

from comparison_service import build_comparison_context
from model import ReportJob, ResearchTask, ResearchTaskPaper
from schemas import ResearchTaskAnalysisResponse
from sqlalchemy import func, select, update

def create_report_job(
    db: Session,
    task_id: int,
) -> ReportJob:
    """同一研究任务没有未结束作业时，才创建新的报告作业。"""

    try:
        task = db.get(
            ResearchTask,
            task_id,
            with_for_update=True,
            populate_existing=True,
        )

        if task is None:
            raise LookupError("没有找到这个研究任务")

        active_statement = (
            select(ReportJob)
            .where(
                ReportJob.task_id == task_id,
                ReportJob.status.in_(["queued", "running"]),
            )
            .order_by(ReportJob.id.desc())
            .limit(1)
            .with_for_update()
        )

        active_job = db.scalar(active_statement)

        if active_job is not None:
            raise ValueError(
                f"已有作业{active_job.id}尚未结束，"
                "请查询该作业，不要重复提交"
            )

        task_crud.ensure_current_analysis(task)

        if not task.result:
            raise ValueError("这个研究任务还没有保存分析结果")

        try:
            analysis = ResearchTaskAnalysisResponse.model_validate_json(
                task.result
            )
        except ValidationError as error:
            raise ValueError("保存的分析结果格式不正确") from error

        if (
            analysis.task_id != task.id
            or analysis.question != task.question
        ):
            raise ValueError("保存的分析与当前研究任务不一致")

        paper_ids = list(db.scalars(select(ResearchTaskPaper.paper_id).where(
            ResearchTaskPaper.task_id == task_id).order_by(ResearchTaskPaper.paper_id).with_for_update()))
        if not {source.paper_id for source in analysis.sources}.issubset(set(paper_ids)):
            raise ValueError("旧分析引用了已移出任务的论文，请重新生成分析")

        # 提交与删除/分块共用论文锁。queued写入后，后续变更会被作业状态阻止。
        with paper_operation_locks(db, paper_ids):
            for paper_id in paper_ids:
                ensure_no_active_preparation(db, paper_id)
            build_comparison_context(db=db, analysis=analysis)
            job = ReportJob(task_id=task.id, status="queued", stage="waiting",
                            analysis_snapshot=analysis.model_dump(mode="json"))
            db.add(job)
            db.commit()

    except Exception:
        db.rollback()
        raise

    db.refresh(job)

    return job

def get_report_job(
    db: Session,
    job_id: int,
) -> ReportJob | None:
    """根据作业编号查询记录，不存在时返回None。"""

    return db.get(ReportJob, job_id)

def get_report_jobs_by_task(
    db: Session,
    task_id: int,
    limit: int = 20,
    offset: int = 0,
) -> list[ReportJob]:
    """查询指定研究任务的报告作业，最新记录排在前面。"""
    statement = (
        select(ReportJob)
        .options(
            load_only(
                ReportJob.id,
                ReportJob.task_id,
                ReportJob.status,
                ReportJob.stage,
                ReportJob.error_message,
                ReportJob.created_at,
                ReportJob.started_at,
                ReportJob.finished_at,
            )
        )
        .where(ReportJob.task_id == task_id)
        .order_by(ReportJob.id.desc())
        .limit(limit)
        .offset(offset)
    )
    jobs = db.scalars(statement).all()

    return list(jobs)

def claim_report_job(
    db: Session,
    job_id: int,
) -> ReportJob | None:
    """将等待中的作业改为运行中；不能领取时返回None。"""

    statement = (
        update(ReportJob)
        .where(
            ReportJob.id == job_id,
            ReportJob.status == "queued",
        )
        .values(
            status="running",
            stage="generating",
            started_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )

    try:
        update_result = db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise

    if update_result.rowcount != 1:
        return None

    return get_report_job(db, job_id)

def mark_dispatch_failed(
    db: Session,
    job_id: int,
) -> None:
    """消息发送失败时，仅将仍在等待的作业标记为失败。"""

    statement = (
        update(ReportJob)
        .where(
            ReportJob.id == job_id,
            ReportJob.status == "queued",
        )
        .values(
            status="failed",
            stage="failed",
            error_message="作业消息发送失败，请检查Redis连接",
            finished_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )

    try:
        db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise

def recover_interrupted_job(
    db: Session,
    job_id: int,
) -> bool:
    """确认Worker停止后，将指定的未结束作业标记为失败。"""

    statement = (
        update(ReportJob)
        .where(
            ReportJob.id == job_id,
            ReportJob.status.in_(["queued", "running"]),
        )
        .values(
            status="failed",
            stage="interrupted",
            error_message="已确认Worker停止，手动恢复此作业，请重新提交。",
            finished_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )

    try:
        update_result = db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return update_result.rowcount == 1
