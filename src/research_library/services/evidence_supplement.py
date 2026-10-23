from sqlalchemy.orm import Session

from research_library.repositories.chunk_crud import get_neighbor_chunks
from research_library.services.review_service import ReviewClaim, ReviewEvidence, load_review_evidence
from research_library.schemas import ResearchTaskAnalysisResponse

def build_evidence_registry(
    db: Session,
    analysis: ResearchTaskAnalysisResponse,
) -> dict[int, ReviewEvidence]:

    evidence_by_chunk_id: dict[int, ReviewEvidence] = {}

    for number in range(1, len(analysis.sources) + 1):
        evidence = load_review_evidence(
            db=db,
            analysis=analysis,
            source_number=number,
        )
        if evidence.chunk_id in evidence_by_chunk_id:
            raise ValueError("原分析中存在重复文本块，请重新生成分析")

        evidence_by_chunk_id[evidence.chunk_id] = evidence

    return evidence_by_chunk_id

def supplement_claim_evidence(
    db: Session,
    claim: ReviewClaim,
    evidence_by_chunk_id: dict[int, ReviewEvidence],
    max_new_chunks: int = 4,
) -> ReviewClaim:
    """为一条陈述补充相邻原文，返回新的待审校对象。"""

    if type(max_new_chunks) is not int or not 0 <= max_new_chunks <= 4:
        raise ValueError("最多补充0到4个文本块，数量必须是整数")

    for evidence in claim.evidence:
        registered = evidence_by_chunk_id.get(evidence.chunk_id)

        if registered != evidence:
            raise ValueError("原证据与本次证据登记表不一致")

    expanded_evidence = list(claim.evidence)

    used_chunk_ids = {
        evidence.chunk_id
        for evidence in expanded_evidence
    }

    added_count = 0

    for original_evidence in claim.evidence:
        if added_count >= max_new_chunks:
            break

        neighbors = get_neighbor_chunks(
            db=db,
            chunk_id=original_evidence.chunk_id,
            before=1,
            after=1,
        )

        for chunk in neighbors:
            if added_count >= max_new_chunks:
                break

            if chunk.id in used_chunk_ids:
                continue

            evidence = evidence_by_chunk_id.get(chunk.id)

            if evidence is None:
                next_number = max(
                    item.source_number
                    for item in evidence_by_chunk_id.values()
                ) + 1

                evidence = ReviewEvidence(
                    source_number=next_number,
                    paper_title=original_evidence.paper_title,
                    chunk_id=chunk.id,
                    page_number=chunk.page_number,
                    text=chunk.text,
                    paper_id=original_evidence.paper_id,
                )

                evidence_by_chunk_id[chunk.id] = evidence

            expanded_evidence.append(evidence)
            used_chunk_ids.add(chunk.id)
            added_count += 1

    return ReviewClaim(
        field=claim.field,
        claim=claim.claim,
        source_numbers=[
            evidence.source_number
            for evidence in expanded_evidence
        ],
        evidence=expanded_evidence,
    )
