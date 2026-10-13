"""封装研究任务及其论文关联的数据库操作。"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from model import (
    Paper,
    PaperChunk,
    PaperDocument,
    ResearchTask,
    ResearchTaskPaper,
    PaperChunkEmbedding,
    ResearchTaskAnalysisVersion,
    ReportJob,
)
from schemas import ResearchTaskCreate, ResearchTaskStatusUpdate, ResearchTaskPaperCreate, ResearchTaskAnalysisResponse
from schemas import ResearchTaskReviewResponse

class TaskStateConflictError(ValueError):
    """输入版本过期或任务正在运行，拒绝覆盖当前数据。"""


def lock_task_for_update(db: Session, task_id: int) -> ResearchTask:
    """锁定并刷新任务，避免会话缓存及MySQL快照隐藏并发变化。"""
    task = db.scalar(select(ResearchTask).where(ResearchTask.id == task_id)
                     .with_for_update().execution_options(populate_existing=True))
    if task is None:
        raise TaskStateConflictError("研究任务已被删除，请刷新页面")
    return task


def ensure_no_active_report(db: Session, task_id: int) -> None:
    job_id = db.scalar(select(ReportJob.id).where(
        ReportJob.task_id == task_id,
        ReportJob.status.in_(["queued", "running"]),
    ).limit(1).with_for_update())
    if job_id is not None:
        raise TaskStateConflictError(f"报告作业{job_id}尚未结束，暂时不能调整关联论文")


def task_input_snapshot(db: Session, task: ResearchTask) -> tuple:
    """分析生成前保存输入；保存结果时必须仍对应同一份任务资料。"""
    paper_ids = tuple(db.scalars(select(ResearchTaskPaper.paper_id).where(
        ResearchTaskPaper.task_id == task.id).order_by(ResearchTaskPaper.paper_id)))
    return (task.result, task.review_result, task.status, task.question, paper_ids)


def invalidate_analysis(task: ResearchTask) -> None:
    """保留旧分析供历史核对，禁止把它当作当前资料的分析继续使用。"""
    if task.result:
        task.status = "stale"
    task.review_result = None


def ensure_current_analysis(task: ResearchTask) -> None:
    if task.status == "stale":
        raise TaskStateConflictError("关联论文已变化，旧分析已过期，请重新生成分析")


def get_tasks(db: Session) -> list[ResearchTask]:
    """查询并返回全部研究任务。"""
    statement = select(ResearchTask).order_by(ResearchTask.id)
    tasks = db.scalars(statement).all()
    return list(tasks)

def get_task_by_id(
    db: Session,
    task_id: int,
) -> ResearchTask | None:
    """根据编号查询研究任务，未找到时返回 None。"""
    task = db.get(ResearchTask, task_id)
    return task


def create_task(
    db: Session,
    task_data: ResearchTaskCreate,
) -> ResearchTask:
    """创建并返回一个研究任务。"""
    new_task = ResearchTask(
        title=task_data.title,
        question=task_data.question,
        status="pending",
        result=None,
    )

    db.add(new_task)
    db.commit()
    db.refresh(new_task)

    return new_task


def update_task_status(
    db: Session,
    task: ResearchTask,
    task_data: ResearchTaskStatusUpdate,
) -> ResearchTask:
    """更新任务状态；结果变化时清除旧审校。"""

    if task.result != task_data.result:
        task.review_result = None

    task.status = task_data.status
    task.result = task_data.result

    db.commit()
    db.refresh(task)

    return task


def delete_task(
    db: Session,
    task: ResearchTask,
) -> None:
    """拒绝删除未结束作业的任务；删除任务时同步清理已结束作业。"""
    task_id = task.id

    try:
        # 与创建作业采用相同的任务行锁，防止检查后又创建新作业。
        locked_task = db.get(
            ResearchTask,
            task_id,
            with_for_update=True,
            populate_existing=True,
        )

        if locked_task is None:
            raise LookupError("没有找到这个研究任务")

        # 锁定并读取最新作业状态，避免MySQL事务快照读到旧状态。
        jobs = db.execute(
            select(ReportJob.id, ReportJob.status)
            .where(ReportJob.task_id == task_id)
            .with_for_update()
        ).all()

        for job in jobs:
            if job.status not in ("succeeded", "failed"):
                raise ValueError(
                    f"作业{job.id}尚未结束，不能删除研究任务；"
                    "中断的作业请先恢复为失败"
                )

        # 先删除子表记录，再删除任务；所有修改在同一事务中提交。
        db.execute(
            delete(ReportJob)
            .where(ReportJob.task_id == task_id)
            .execution_options(synchronize_session=False)
        )
        db.delete(locked_task)
        db.commit()

    except Exception:
        db.rollback()
        raise

def get_task_paper_links(
    db: Session,
    task_id: int,
) -> list[ResearchTaskPaper]:
    """查询研究任务关联的全部论文记录。"""
    statement = (
        select(ResearchTaskPaper)
        .where(ResearchTaskPaper.task_id == task_id)
        .order_by(ResearchTaskPaper.id)
    )
    links = db.scalars(statement).all()
    return list(links)

def get_task_paper_link(
    db: Session,
    task_id: int,
    paper_id: int,
) -> ResearchTaskPaper | None:
    """查询指定任务与论文之间的关联记录。"""
    statement = select(ResearchTaskPaper).where(
        ResearchTaskPaper.task_id == task_id,
        ResearchTaskPaper.paper_id == paper_id,
    )
    link = db.scalar(statement)
    return link

def add_paper_to_task(
    db: Session,
    task: ResearchTask,
    paper: Paper,
    link_data: ResearchTaskPaperCreate,
) -> ResearchTaskPaper:
    """建立研究任务与论文的关联。"""
    try:
        current = lock_task_for_update(db, task.id)
        ensure_no_active_report(db, current.id)
        if get_task_paper_link(db, current.id, paper.id) is not None:
            raise TaskStateConflictError("这篇论文已经加入该研究任务")
        new_link = ResearchTaskPaper(task_id=current.id, paper_id=paper.id, note=link_data.note)
        db.add(new_link)
        invalidate_analysis(current)
        db.commit()
        db.refresh(new_link)
        return new_link
    except Exception:
        db.rollback()
        raise


def remove_paper_from_task(db: Session, link: ResearchTaskPaper) -> None:
    """与报告提交共用任务行锁；关联变化后标记分析过期。"""
    try:
        task = lock_task_for_update(db, link.task_id)
        ensure_no_active_report(db, task.id)
        db.delete(link)
        invalidate_analysis(task)
        db.commit()
    except Exception:
        db.rollback()
        raise


def search_task_chunks(
    db: Session,
    task_id: int,
    keyword: str,
) -> list[tuple[PaperChunk, Paper]]:
    statement = (
        select(PaperChunk, Paper)
        .select_from(PaperChunk)
        .join(
            PaperDocument,
            PaperChunk.document_id == PaperDocument.id,
        )
        .join(
            Paper,
            PaperDocument.paper_id == Paper.id,
        )
        .join(
            ResearchTaskPaper,
            ResearchTaskPaper.paper_id == Paper.id,
        )
        .where(
            ResearchTaskPaper.task_id == task_id,
            PaperChunk.text.contains(keyword, autoescape=True),
        )
        .order_by(
            Paper.id,
            PaperChunk.page_number,
            PaperChunk.chunk_index,
        )
    )

    return [
        (chunk, paper)
        for chunk, paper in db.execute(statement)
    ]

def get_task_chunks(
        db: Session,
        task_id: int,
) -> list[tuple[PaperChunk, Paper]]:
    statement = (
        select(PaperChunk, Paper)
        .select_from(PaperChunk)
        .join(
            PaperDocument,
            PaperChunk.document_id == PaperDocument.id,
        )
        .join(
            Paper,
            PaperDocument.paper_id == Paper.id,
        )
        .join(
            ResearchTaskPaper,
            ResearchTaskPaper.paper_id == Paper.id,
        )
        .where(ResearchTaskPaper.task_id == task_id)
        .order_by(
            Paper.id,
            PaperChunk.page_number,
            PaperChunk.chunk_index,
        )
    )
    return [(chunk, paper) for chunk, paper in db.execute(statement)]

def get_task_chunk_embeddings(
    db: Session,
    task_id: int,
    model_name: str,
) -> list[tuple[PaperChunk, Paper, PaperChunkEmbedding]]:
    statement = (
        select(
            PaperChunk,
            Paper,
            PaperChunkEmbedding,
        )
        .select_from(PaperChunk)
        .join(
            PaperChunkEmbedding,
            PaperChunkEmbedding.chunk_id == PaperChunk.id,
        )
        .join(
            PaperDocument,
            PaperChunk.document_id == PaperDocument.id,
        )
        .join(
            Paper,
            PaperDocument.paper_id == Paper.id,
        )
        .join(
            ResearchTaskPaper,
            ResearchTaskPaper.paper_id == Paper.id,
        )
        .where(
            ResearchTaskPaper.task_id == task_id,
            PaperChunkEmbedding.model_name == model_name,
        )
        .order_by(
            Paper.id,
            PaperChunk.page_number,
            PaperChunk.chunk_index,
        )
    )

    return [
        (chunk, paper, embedding)
        for chunk, paper, embedding in db.execute(statement)
    ]

def save_task_analysis(
    db: Session,
    task: ResearchTask,
    analysis: ResearchTaskAnalysisResponse,
    expected_input: tuple | None = None,
) -> None:
    """保存最新分析，并清除旧分析对应的审校结果。"""

    try:
        if expected_input is not None:
            task = lock_task_for_update(db, task.id)
            # 用锁定读检查最新关联，而不是复用最初事务的一致性快照。
            papers = tuple(db.scalars(select(ResearchTaskPaper.paper_id).where(
                ResearchTaskPaper.task_id == task.id).order_by(ResearchTaskPaper.paper_id).with_for_update()))
            current = (task.result, task.review_result, task.status, task.question, papers)
            if current != expected_input:
                raise TaskStateConflictError("分析期间任务已变化，请刷新后重新分析")
        task.result = analysis.model_dump_json()
        task.review_result = None
        task.status = "completed"
        db.commit()
    except Exception:
        db.rollback()
        raise

def save_task_review(
    db: Session,
    task: ResearchTask,
    review: ResearchTaskReviewResponse,
    expected_analysis: str,
) -> None:
    """审校只允许保存到它实际检查的分析版本上。"""
    try:
        task = lock_task_for_update(db, task.id)
        ensure_current_analysis(task)
        if task.result != expected_analysis:
            raise TaskStateConflictError("审校期间分析已变化，旧审校未保存，请重新审校")
        task.review_result = review.model_dump_json()
        db.commit()
    except Exception:
        db.rollback()
        raise

def get_analysis_versions(
    db: Session,
    task_id: int,
) -> list[ResearchTaskAnalysisVersion]:
    """查询任务的全部版本，最新记录排在前面。"""
    statement = (
        select(ResearchTaskAnalysisVersion)
        .where(ResearchTaskAnalysisVersion.task_id == task_id)
        .order_by(ResearchTaskAnalysisVersion.id.desc())
    )
    versions = db.scalars(statement).all()
    return list(versions)


def get_analysis_version(
    db: Session,
    task_id: int,
    version_id: int,
) -> ResearchTaskAnalysisVersion | None:
    """查询指定任务下的一条版本，防止跨任务返回记录。"""
    statement = (
        select(ResearchTaskAnalysisVersion)
        .where(
            ResearchTaskAnalysisVersion.task_id == task_id,
            ResearchTaskAnalysisVersion.id == version_id,
        )
    )
    return db.scalar(statement)
