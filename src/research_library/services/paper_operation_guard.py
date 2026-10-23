from contextlib import contextmanager, ExitStack, nullcontext
from functools import wraps
from hashlib import sha256
from inspect import signature
from threading import Lock

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from research_library.db.model import PaperPreparationJob, ReportJob, ResearchTaskPaper

class PaperOperationBusyError(ValueError):
    """论文正在处理，当前操作不能执行。"""

_sqlite_locks = {}
_sqlite_registry_lock = Lock()

@contextmanager
def paper_operation_lock(
    db: Session,
    paper_id: int,
    connection=None,
):
    """让同一篇论文的受保护操作互斥，期间提交事务不会解除保护。"""

    engine = db.get_bind().engine

    if engine.dialect.name == "sqlite":
        key = (id(engine), paper_id)

        with _sqlite_registry_lock:
            lock = _sqlite_locks.setdefault(key, Lock())

        if not lock.acquire(blocking=False):
            raise PaperOperationBusyError(
                "这篇论文正在执行其他操作，请稍后重试"
            )

        try:
            yield
        finally:
            lock.release()

        return

    if engine.dialect.name != "mysql":
        raise RuntimeError("当前操作锁只支持MySQL和SQLite")

    identity = f"{engine.url.database}:{paper_id}"
    digest = sha256(identity.encode("utf-8")).hexdigest()[:40]
    lock_name = f"research-paper:{digest}"

    connection_context = nullcontext(connection) if connection is not None else engine.connect()
    with connection_context as connection:
        try:
            acquired = connection.scalar(
                text("SELECT GET_LOCK(:lock_name, 0)"),
                {"lock_name": lock_name},
            )
        except Exception:
            # 取锁结果不明确时，废弃连接，避免将可能持锁的连接交回池中。
            connection.invalidate()
            raise

        if acquired == 0:
            raise PaperOperationBusyError(
                "这篇论文正在执行其他操作，请稍后重试"
            )

        if acquired != 1:
            connection.invalidate()
            raise RuntimeError("无法取得论文操作锁")

        try:
            yield

        finally:
            try:
                released = connection.scalar(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": lock_name},
                )

                if released != 1:
                    raise RuntimeError("无法正常释放论文操作锁")

            except Exception:
                connection.invalidate()
                raise

@contextmanager
def paper_operation_locks(db: Session, paper_ids: list[int]):
    """多篇论文按编号取锁，MySQL共用一个连接，避免耗尽连接池。"""
    engine = db.get_bind().engine
    connection_context = engine.connect() if engine.dialect.name == "mysql" else nullcontext(None)
    with connection_context as connection, ExitStack() as locks:
        for paper_id in sorted(set(paper_ids)):
            locks.enter_context(paper_operation_lock(db, paper_id, connection=connection))
        yield


def ensure_no_active_preparation(
    db: Session,
    paper_id: int,
) -> None:
    """有等待或运行中的准备作业时，拒绝修改论文准备数据。"""

    active_job_id = db.scalar(
        select(PaperPreparationJob.id)
        .where(
            PaperPreparationJob.paper_id == paper_id,
            PaperPreparationJob.status.in_(["queued", "running"]),
        )
        .limit(1)
        .with_for_update()
    )

    if active_job_id is not None:
        raise PaperOperationBusyError(
            f"论文准备作业{active_job_id}尚未结束，暂时不能修改或删除"
        )

def ensure_no_report_using_paper(db: Session, paper_id: int) -> None:
    """所有关联论文都可能参与报告补证，运行期间统一保护。"""
    job_id = db.scalar(select(ReportJob.id).join(
        ResearchTaskPaper, ResearchTaskPaper.task_id == ReportJob.task_id,
    ).where(ResearchTaskPaper.paper_id == paper_id,
            ReportJob.status.in_(["queued", "running"])).limit(1).with_for_update())
    if job_id is not None:
        raise PaperOperationBusyError(f"报告作业{job_id}正在使用这篇论文，暂时不能重建或删除资料")


def protect_paper_mutation(function):
    """保护修改论文准备数据的同步接口。"""

    function_signature = signature(function)

    @wraps(function)
    def wrapper(*args, **kwargs):
        arguments = function_signature.bind(*args, **kwargs)

        db = arguments.arguments["db"]
        paper_id = arguments.arguments["paper_id"]

        with paper_operation_lock(db, paper_id):
            ensure_no_active_preparation(db, paper_id)
            ensure_no_report_using_paper(db, paper_id)

            return function(*args, **kwargs)

    return wrapper
