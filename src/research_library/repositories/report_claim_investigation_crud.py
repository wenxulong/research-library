"""查证候选记录的保存与读取，不更新正式报告。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from research_library.db.model import ReportClaimInvestigation
from research_library.schemas import ReportClaimInvestigationResponse


def save_investigation(
    db: Session,
    base_report: dict,
    result: ReportClaimInvestigationResponse,
) -> ReportClaimInvestigation:
    record = ReportClaimInvestigation(
        job_id=result.job_id,
        claim_id=result.claim_id,
        base_report=base_report,
        result=result.model_dump(mode="json"),
    )
    db.add(record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(record)
    return record


def get_investigation(db: Session, investigation_id: int) -> ReportClaimInvestigation:
    record = db.get(ReportClaimInvestigation, investigation_id)
    if record is None:
        raise LookupError("没有找到这条查证记录")
    return record


def list_investigations(db: Session, job_id: int, claim_id: str, limit: int = 10):
    """仅读取本报告、本陈述最近的候选记录，不重新查证。"""
    return list(db.scalars(
        select(ReportClaimInvestigation)
        .where(ReportClaimInvestigation.job_id == job_id,
               ReportClaimInvestigation.claim_id == claim_id)
        .order_by(ReportClaimInvestigation.id.desc()).limit(limit)
    ).all())
