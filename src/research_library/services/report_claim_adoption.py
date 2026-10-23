"""用户采用候选证据与审校；不调用模型，不改写陈述事实内容。"""
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from research_library.agents.comparison_agent import parse_comparison_response
from research_library.db.model import ReportClaimInvestigation, ReportJob
from research_library.schemas import CheckedComparisonReportResponse, ComparisonSourceResponse, ReportClaimInvestigationResponse, ReportClaimAdoptionResponse

FIELDS = ("method_comparisons", "experiment_comparisons",
          "conclusion_comparisons", "evidence_notes")


def merge_investigation(checked, candidate):
    """以未变化的报告为基础合并证据，旧编号保留，冲突不静默重编号。"""
    if candidate.task_id != checked.task_id:
        raise ValueError("候选结果与报告所属任务不一致")
    review = candidate.review
    if review.field not in FIELDS or review.claim_id != candidate.claim_id:
        raise ValueError("候选陈述编号或字段不一致")
    statement = next((s for s in getattr(checked.report, review.field)
                      if s.claim_id == candidate.claim_id), None)
    if statement is None or statement.text != review.claim:
        raise ValueError("候选审校与当前陈述不一致")
    if not any(r.claim_id == candidate.claim_id for r in checked.review.items):
        raise ValueError("没有对应的最终审校记录")

    sources = {s.source_number: s for s in checked.report.sources}
    chunks = {s.chunk_id: s.source_number for s in sources.values()}
    available = set()
    for evidence in candidate.evidence:
        source = ComparisonSourceResponse.model_validate(evidence.model_dump())
        number = source.source_number
        if number in sources and sources[number] != source:
            raise ValueError("候选资料编号冲突，请重新查证")
        if source.chunk_id in chunks and chunks[source.chunk_id] != number:
            raise ValueError("同一原文使用了不同资料编号，请重新查证")
        sources[number] = source
        chunks[source.chunk_id] = number
        available.add(number)
    if not set(review.source_numbers).issubset(available):
        raise ValueError("复审引用了未提供的候选原文")

    # 只有完全支持的复审，才把新引用补到正文。
    # 反对、部分支持和证据不足的依据保存在review中，避免被读成支持引用。
    if review.verdict == "supported":
        for number in review.source_numbers:
            if number not in statement.source_numbers:
                statement.text += f"[资料{number}]"
                statement.source_numbers.append(number)
    try:
        checked.report = parse_comparison_response(
            checked.report.model_dump_json(), checked.task_id,
            checked.report.question, sorted(sources.values(), key=lambda s: s.source_number),
        )
    except RuntimeError as error:
        raise ValueError(str(error)) from error
    checked.review.items = [
        review.model_copy(update={"claim": statement.text})
        if item.claim_id == candidate.claim_id else item
        for item in checked.review.items
    ]
    checked.passed = bool(checked.review.items) and all(
        item.verdict == "supported" for item in checked.review.items)
    return checked


def adopt_investigation(db: Session, investigation_id: int) -> ReportClaimAdoptionResponse:
    """锁定同份报告，原子更新报告与采用标记，支持重复点击。"""
    try:
        record = db.get(ReportClaimInvestigation, investigation_id)
        if record is None:
            raise LookupError("没有找到这条查证记录")
        job = db.scalar(select(ReportJob).where(ReportJob.id == record.job_id)
                        .with_for_update().execution_options(populate_existing=True))
        record = db.scalar(select(ReportClaimInvestigation)
                           .where(ReportClaimInvestigation.id == investigation_id)
                           .with_for_update().execution_options(populate_existing=True))
        if job is None or record is None:
            raise LookupError("报告或查证记录已经不存在")
        if job.status != "succeeded" or job.result is None:
            raise ValueError("报告当前状态不允许采用结果")
        already_adopted = record.adopted_at is not None
        checked = CheckedComparisonReportResponse.model_validate(job.result)
        if not already_adopted:
            if job.result != record.base_report:
                raise ValueError("正式报告在查证后已变化，请刷新报告并重新查证")
            candidate = ReportClaimInvestigationResponse.model_validate(record.result)
            if candidate.job_id != job.id or candidate.claim_id != record.claim_id:
                raise ValueError("候选结果归属不一致")
            checked = merge_investigation(checked, candidate)
            job.result = checked.model_dump(mode="json")
            record.adopted_at = func.now()
        db.commit()
        return ReportClaimAdoptionResponse(
            investigation_id=investigation_id, job_id=job.id,
            already_adopted=already_adopted, checked_report=checked,
        )
    except Exception:
        db.rollback()
        raise
