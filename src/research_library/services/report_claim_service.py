from sqlalchemy.orm import Session
from sqlalchemy import select

from research_library.db.model import PaperChunk, ReportJob, ResearchTaskPaper
from research_library.schemas import CheckedComparisonReportResponse, ReportClaimContextResponse, ReportClaimEvidenceResponse, ComparisonStatementResponse

def get_report_claim_context(
    db: Session,
    job_id: int,
    claim_id: str,
) -> ReportClaimContextResponse:
    """读取一条最终陈述、审校意见和已有原文。"""

    if not claim_id.strip():
        raise ValueError("陈述编号不能为空，旧报告需要重新生成后才能查证")

    job = db.get(ReportJob, job_id)

    if job is None:
        raise LookupError("没有找到这个报告作业")

    if job.status != "succeeded" or job.result is None:
        raise ValueError("这个作业尚未成功生成报告")

    checked = CheckedComparisonReportResponse.model_validate(
        job.result,
    )

    groups = {
        "method_comparisons": checked.report.method_comparisons,
        "experiment_comparisons": checked.report.experiment_comparisons,
        "conclusion_comparisons": checked.report.conclusion_comparisons,
        "evidence_notes": checked.report.evidence_notes,
    }

    for field, statements in groups.items():
        for statement in statements:
            if statement.claim_id == claim_id:
                return build_claim_context(
                    db=db,
                    job=job,
                    checked=checked,
                    field=field,
                    statement=statement,
                )

    raise LookupError("没有找到这条陈述，请确认报告包含陈述编号")

def build_claim_context(
    db: Session,
    job: ReportJob,
    checked: CheckedComparisonReportResponse,
    field: str,
    statement: ComparisonStatementResponse,
) -> ReportClaimContextResponse:
    """为已定位的陈述组装审校结果和原文证据。"""

    review = next(
        (
            item
            for item in checked.review.items
            if item.claim_id == statement.claim_id
        ),
        None,
    )

    if review is None:
        raise ValueError("这条陈述缺少对应的最终审校结果")

    if review.field != field or review.claim != statement.text:
        raise ValueError("陈述与最终审校结果不一致")

    sources = {
        source.source_number: source
        for source in checked.report.sources
    }

    evidence = []

    for number in statement.source_numbers:
        source = sources.get(number)

        if source is None:
            raise ValueError(f"报告中不存在资料{number}")

        chunk = db.get(PaperChunk, source.chunk_id)

        if chunk is None or chunk.document is None:
            raise ValueError(f"资料{number}对应的原文已经不存在")

        if (
            chunk.document.paper_id != source.paper_id
            or chunk.page_number != source.page_number
        ):
            raise ValueError(f"资料{number}与当前原文不对应")

        evidence.append(
            ReportClaimEvidenceResponse(
                **source.model_dump(),
                text=chunk.text,
            )
        )

    paper_ids = list(
        db.scalars(
            select(ResearchTaskPaper.paper_id)
            .where(ResearchTaskPaper.task_id == job.task_id)
            .order_by(ResearchTaskPaper.paper_id)
        ).all()
    )

    return ReportClaimContextResponse(
        job_id=job.id,
        task_id=job.task_id,
        question=checked.report.question,
        claim_id=statement.claim_id,
        field=field,
        text=statement.text,
        source_numbers=statement.source_numbers,
        review=review,
        evidence=evidence,
        paper_ids=paper_ids,
    )
