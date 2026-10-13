from dataclasses import dataclass

from sqlalchemy.orm import Session

from comparison_review import prepare_comparison_claims
from evidence_supplement import build_evidence_registry
from evidence_review_workflow import (
    EvidenceReviewResult,
    EvidenceRevisionResult,
    review_claim_with_supplement,
    revise_supplemented_claim,
)
from review_service import ReviewEvidence
from schemas import (
    ComparisonReportResponse,
    ResearchTaskAnalysisResponse,
)

@dataclass(frozen=True)
class ComparisonClaimResult:
    """保存一条报告陈述的补证审校结果及可选的修正结果。"""

    review_result: EvidenceReviewResult
    revision_result: EvidenceRevisionResult | None

@dataclass(frozen=True)
class ComparisonEvidenceResult:
    """保存整份报告的逐条处理结果，以及共享证据登记表。"""

    task_id: int
    items: list[ComparisonClaimResult]
    evidence_by_chunk_id: dict[int, ReviewEvidence]

def process_comparison_evidence(
    db: Session,
    analysis: ResearchTaskAnalysisResponse,
    report: ComparisonReportResponse,
) -> ComparisonEvidenceResult:
    """逐条执行审校、必要的补证，以及一次修正和复审。"""

    claims = prepare_comparison_claims(
        db=db,
        analysis=analysis,
        report=report,
    )

    evidence_by_chunk_id = build_evidence_registry(
        db=db,
        analysis=analysis,
    )

    items: list[ComparisonClaimResult] = []

    for claim in claims:
        review_result = review_claim_with_supplement(
            db=db,
            claim=claim,
            evidence_by_chunk_id=evidence_by_chunk_id,
        )

        revision_result = None

        if (
            review_result.final_review.verdict != "supported"
            and review_result.claim.evidence
        ):
            revision_result = revise_supplemented_claim(
                review_result=review_result,
            )

        items.append(
            ComparisonClaimResult(
                review_result=review_result,
                revision_result=revision_result,
            )
        )

    return ComparisonEvidenceResult(
        task_id=analysis.task_id,
        items=items,
        evidence_by_chunk_id=evidence_by_chunk_id,
    )