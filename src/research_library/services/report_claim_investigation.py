from copy import deepcopy

from sqlalchemy.orm import Session

from research_library.agents.evidence_react import run_evidence_react
from research_library.db.model import PaperChunk, ReportJob
from research_library.services.report_claim_service import get_report_claim_context
from research_library.agents.review_agent import review_claim
from research_library.services.review_service import ReviewClaim, ReviewEvidence
from research_library.repositories.report_claim_investigation_crud import save_investigation
from research_library.schemas import CheckedComparisonReportResponse, ComparisonSourceResponse, ReportClaimEvidenceResponse, ReportClaimInvestigationResponse, SavedReportClaimInvestigationResponse

def prepare_investigation_evidence(
    db: Session,
    sources: list[ComparisonSourceResponse],
    retrieved: list[dict],
) -> list[ReportClaimEvidenceResponse]:
    """复用报告已有编号，为新原文分配候选编号。"""

    source_by_chunk_id = {
        source.chunk_id: source for source in sources
    }

    next_number = max(
        (source.source_number for source in sources),
        default=0,
    ) + 1

    evidence = []
    seen_chunk_ids = set()

    for item in retrieved:
        chunk_id = item["chunk_id"]
        if chunk_id in seen_chunk_ids:
            continue

        chunk = db.get(PaperChunk, chunk_id)

        if chunk is None or chunk.document is None:
            raise ValueError(f"文本块{chunk_id}的原文已经不存在")

        source = source_by_chunk_id.get(chunk_id)
        if source is None:
            paper = chunk.document.paper

            source = ComparisonSourceResponse(
                source_number=next_number,
                paper_id=paper.id,
                paper_title=paper.title,
                chunk_id=chunk.id,
                page_number=chunk.page_number,
                score=item.get("score"),
            )

            next_number += 1

        if (
            source.paper_id != chunk.document.paper_id
            or source.page_number != chunk.page_number
        ):
            raise ValueError(f"文本块{chunk_id}与报告来源不对应")

        evidence.append(
            ReportClaimEvidenceResponse(
                **source.model_dump(),
                text=chunk.text,
            )
        )

        seen_chunk_ids.add(chunk_id)

    return evidence

def build_investigation_claim(
    field: str,
    text: str,
    evidence: list[ReportClaimEvidenceResponse],
) -> ReviewClaim:
    """把候选证据转换成现有审校函数需要的结构。"""

    review_evidence = []

    for item in evidence:
        review_evidence.append(
            ReviewEvidence(
                source_number=item.source_number,
                paper_title=item.paper_title,
                chunk_id=item.chunk_id,
                page_number=item.page_number,
                text=item.text,
                paper_id=item.paper_id,
            )
        )

    return ReviewClaim(
        field=field,
        claim=text,
        source_numbers=[
            item.source_number
            for item in evidence
        ],
        evidence=review_evidence,
    )

def investigation_report_claim(
    db: Session,
    job_id: int,
    claim_id: str,
    instruction: str,
) -> ReportClaimInvestigationResponse:
    """自主查证后复审，只返回候选结果，不修改报告。"""

    instruction = instruction.strip()

    context = get_report_claim_context(
        db=db,
        job_id=job_id,
        claim_id=claim_id,
    )

    job = db.get(ReportJob, job_id)

    checked = CheckedComparisonReportResponse.model_validate(
        job.result,
    )

    react_result = run_evidence_react(
        db=db,
        context=context,
        instruction=instruction,
    )

    evidence = prepare_investigation_evidence(
        db=db,
        sources=checked.report.sources,
        retrieved=react_result["evidence"],
    )

    claim = build_investigation_claim(
        field=context.field,
        text=context.text,
        evidence=evidence,
    )

    review = review_claim(claim)

    review = review.model_copy(
        update={"claim_id": context.claim_id},
    )

    original_chunk_ids = {
        item.chunk_id
        for item in context.evidence
    }

    added_chunk_ids = [
        item.chunk_id
        for item in evidence
        if item.chunk_id not in original_chunk_ids
    ]

    return ReportClaimInvestigationResponse(
        job_id=context.job_id,
        task_id=context.task_id,
        claim_id=context.claim_id,
        instruction=instruction,
        status=react_result["status"],
        summary=react_result["summary"],
        previous_review=context.review,
        review=review,
        evidence=evidence,
        added_chunk_ids=added_chunk_ids,
        steps=react_result["steps"],
    )


def create_saved_investigation(
    db: Session,
    job_id: int,
    claim_id: str,
    instruction: str,
) -> SavedReportClaimInvestigationResponse:
    """执行查证并保存独立候选记录，不改变正式报告。"""
    job = db.get(ReportJob, job_id)
    if job is None:
        raise LookupError("没有找到这个报告作业")
    if job.status != "succeeded" or job.result is None:
        raise ValueError("这个作业尚未成功生成报告")

    base_report = deepcopy(job.result)
    result = investigation_report_claim(
        db=db, job_id=job_id, claim_id=claim_id, instruction=instruction,
    )
    record = save_investigation(db=db, base_report=base_report, result=result)
    return SavedReportClaimInvestigationResponse.model_validate(record)
