from dataclasses import dataclass

from sqlalchemy.orm import Session

from evidence_supplement import supplement_claim_evidence
from review_agent import review_claim
from review_service import ReviewClaim, ReviewEvidence
from schemas import AnalysisRevisionResponse, ReviewItemResponse
from revision_agent import revise_claim

@dataclass(frozen=True)
class EvidenceReviewResult:
    """保存一条陈述补证前后的审校结果。"""

    claim: ReviewClaim
    initial_review: ReviewItemResponse
    final_review: ReviewItemResponse
    added_source_numbers: list[int]

@dataclass(frozen=True)
class EvidenceRevisionResult:
    """保存修正内容、修正后的待审校对象和审校结果。"""

    revision: AnalysisRevisionResponse
    revised_claim: ReviewClaim
    review: ReviewItemResponse

def review_claim_with_supplement(
    db: Session,
    claim: ReviewClaim,
    evidence_by_chunk_id: dict[int, ReviewEvidence],
) -> EvidenceReviewResult:

    initial_review = review_claim(claim)

    current_claim =claim
    final_review = initial_review
    added_source_numbers: list[int] = []

    should_supplement = initial_review.verdict in {
        "insufficient_evidence",
        "partially_supported",
    }

    if should_supplement and claim.evidence:
        expanded_claim = supplement_claim_evidence(
            db=db,
            claim=claim,
            evidence_by_chunk_id=evidence_by_chunk_id,
            max_new_chunks=2,
        )

        original_chunk_ids = {
            evidence.chunk_id for evidence in claim.evidence
        }

        for evidence in expanded_claim.evidence:
            if evidence.chunk_id not in original_chunk_ids:
                added_source_numbers.append(evidence.source_number)

        if added_source_numbers:
            current_claim = expanded_claim
            final_review = review_claim(current_claim)

    return EvidenceReviewResult(
        claim=current_claim,
        initial_review=initial_review,
        final_review=final_review,
        added_source_numbers=added_source_numbers,
    )

def revise_supplemented_claim(
    review_result: EvidenceReviewResult,
) -> EvidenceRevisionResult:

    claim = review_result.claim

    evidence_by_number = {
        evidence.source_number: evidence
        for evidence in claim.evidence
    }

    max_source_number = max(evidence_by_number, default=0)

    revision = revise_claim(
        claim=claim,
        review_item=review_result.final_review,
        source_count=max_source_number,
    )

    revised_evidence = [
        evidence_by_number[number]
        for number in revision.source_numbers
    ]

    revised_claim = ReviewClaim(
        field=claim.field,
        claim=revision.revised_claim,
        source_numbers=revision.source_numbers,
        evidence=revised_evidence,
    )

    revised_review = review_claim(revised_claim)

    return EvidenceRevisionResult(
        revision=revision,
        revised_claim=revised_claim,
        review=revised_review,
    )