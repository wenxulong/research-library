from sqlalchemy.orm import Session
from sqlalchemy import func, select, update

from model import Paper, PaperDocument, PaperPreparationJob
from paper_operation_guard import paper_operation_lock, ensure_no_report_using_paper

def create_preparation_job(
    db: Session,
    paper_id: int,
) -> PaperPreparationJob:
    """创建准备作业，与论文数据修改共用操作锁。"""

    with paper_operation_lock(db, paper_id):
        try:
            ensure_no_report_using_paper(db, paper_id)
            paper = db.get(
                Paper,
                paper_id,
                with_for_update=True,
            )

            if paper is None:
                raise LookupError("没有找到这篇论文")

            document = db.scalar(
                select(PaperDocument).where(
                    PaperDocument.paper_id == paper_id
                )
            )

            if document is None:
                raise LookupError("这篇论文还没有上传PDF")

            active_job = db.scalar(
                select(PaperPreparationJob)
                .where(
                    PaperPreparationJob.paper_id == paper_id,
                    PaperPreparationJob.status.in_(
                        ["queued", "running"]
                    ),
                )
                .with_for_update()
            )

            if active_job is not None:
                raise ValueError(
                    f"准备作业{active_job.id}尚未结束，请先查询该作业"
                )

            job = PaperPreparationJob(
                paper_id=paper.id,
                document_id=document.id,
                status="queued",
                stage="waiting",
            )

            db.add(job)
            db.commit()
            db.refresh(job)

            return job

        except Exception:
            db.rollback()
            raise

def get_preparation_job(
    db: Session,
    job_id: int,
) -> PaperPreparationJob | None:
    """按编号查询论文准备作业。"""

    return db.get(PaperPreparationJob, job_id)

def claim_preparation_job(
    db: Session,
    job_id: int,
) -> PaperPreparationJob | None:
    """只有queued作业可以被领取，避免重复消息重复执行。"""

    statement = (
        update(PaperPreparationJob)
        .where(
            PaperPreparationJob.id == job_id,
            PaperPreparationJob.status == "queued",
        )
        .values(
            status="running",
            stage="extracting",
            started_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )

    try:
        result = db.execute(statement)
        db.commit()

    except Exception:
        db.rollback()
        raise

    if result.rowcount != 1:
        return None

    return get_preparation_job(db, job_id)

def mark_preparation_dispatch_failed(
    db: Session,
    job_id: int,
) -> None:
    """投递失败时，只修改仍处于queued状态的作业。"""

    statement = (
        update(PaperPreparationJob)
        .where(
            PaperPreparationJob.id == job_id,
            PaperPreparationJob.status == "queued",
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


def recover_interrupted_preparation_job(
    db: Session,
    job_id: int,
) -> bool:
    """仅在API和本项目所有Worker已停止后，手动结束指定未完成作业。"""

    statement = (
        update(PaperPreparationJob)
        .where(
            PaperPreparationJob.id == job_id,
            PaperPreparationJob.status.in_(["queued", "running"]),
        )
        .values(
            status="failed",
            stage="interrupted",
            error_message=(
                "已确认API和所有Worker停止，"
                "手动恢复此作业，请重新提交论文准备。"
            ),
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
