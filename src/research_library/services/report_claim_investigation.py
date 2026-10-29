import logging
from time import perf_counter
from copy import deepcopy
from uuid import uuid4

from research_library.core.model_usage import model_usage_scope, record_investigation_saved, record_memory_extraction

from sqlalchemy.orm import Session

from research_library.agents.evidence_react import run_evidence_react
from research_library.db.model import PaperChunk, ReportJob
from research_library.services.report_claim_service import get_report_claim_context
from research_library.agents.review_agent import review_claim
from research_library.services.review_service import ReviewClaim, ReviewEvidence
from research_library.repositories.report_claim_investigation_crud import save_investigation
from research_library.schemas import CheckedComparisonReportResponse, ComparisonSourceResponse, ReportClaimEvidenceResponse, ReportClaimInvestigationResponse, SavedReportClaimInvestigationResponse
from research_library.memory.manager import MemoryManager
from research_library.memory.repository import MemoryRepository
from research_library.memory.schemas import MemoryContext
from research_library.schemas import ReportClaimContextResponse

logger = logging.getLogger(__name__)

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
    primary_by_content = {}

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

        content_key = (
            chunk.document.paper_id,
            chunk.text.replace("\r\n", "\n").replace("\r", "\n").strip(),
        )
        primary_id = primary_by_content.setdefault(content_key, chunk.id)
        evidence.append(
            ReportClaimEvidenceResponse(
                **source.model_dump(),
                text=chunk.text,
                duplicate_of_chunk_id=primary_id if primary_id != chunk.id else None,
            )
        )

        seen_chunk_ids.add(chunk_id)

    return evidence

def build_investigation_claim(
    field: str,
    text: str,
    evidence: list[ReportClaimEvidenceResponse],
    instruction: str = "",
    investigation_summary: str = "",
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
                duplicate_of_chunk_id=item.duplicate_of_chunk_id,
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
        instruction=instruction,
        investigation_summary=investigation_summary,
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

    memory_context = load_investigation_memory(
        db=db,
        context=context,
        instruction=instruction,
    )

    react_result = run_evidence_react(
        db=db,
        context=context,
        instruction=instruction,
        memory_context=memory_context,
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
        instruction=instruction,
        investigation_summary=react_result["summary"],
    )

    with model_usage_scope(
        stage="review", evidence_count=len(evidence),
        evidence_chars=sum(len(item.text) for item in evidence if item.duplicate_of_chunk_id is None),
        source_record_count=len(evidence),
    ):
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
    with model_usage_scope(
        trace_id=uuid4().hex, task_id=job.task_id,
        report_job_id=job_id, claim_id=claim_id,
    ):
        result = investigation_report_claim(
            db=db, job_id=job_id, claim_id=claim_id, instruction=instruction,
        )
        record = save_investigation(db=db, base_report=base_report, result=result)
        record_investigation_saved(record.id)
        saved = SavedReportClaimInvestigationResponse.model_validate(record)

        # 调查已提交；附加记忆提取失败不能把它误报为查证失败。
        with model_usage_scope(stage="memory_extract", investigation_id=saved.id):
            started_at = perf_counter()
            record_memory_extraction("started", started_at=started_at)
            try:
                repository = MemoryRepository(db=db, owner_id="local-user")
                manager = MemoryManager(repository=repository)
                writes = manager.learn_from_run(investigation=saved)
            except Exception as error:
                db.rollback()
                record_memory_extraction("failed", started_at=started_at, error=error)
                logger.warning(
                    "调查%s的记忆提取或保存失败（%s），调查记录已保留",
                    saved.id, type(error).__name__,
                )
            else:
                record_memory_extraction(
                    "completed" if writes else "empty",
                    started_at=started_at,
                    memory_ids=[item.memory.id for item in writes],
                    created_count=sum(item.created for item in writes),
                    reused_count=sum(not item.created for item in writes),
                )
    return saved

def load_investigation_memory(
    db: Session,
    context: ReportClaimContextResponse,
    instruction: str,
) -> MemoryContext:
    """读取与当前查证相关的共享记忆。"""

    repository = MemoryRepository(
        db=db,
        owner_id="local-user",
    )

    manager = MemoryManager(
        repository=repository,
        max_chars=4000,
    )

    query = (
        f"研究问题：{context.question}\n"
        f"待核查陈述：{context.text}\n"
        f"本次查证要求：{instruction}"
    )

    return manager.recall(
        query=query,
        task_id=context.task_id,
        limit=5,
        min_score=0.3,
    )