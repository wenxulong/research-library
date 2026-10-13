from sqlalchemy.orm import Session

from review_service import prepare_review_claims
from schemas import (
    ComparisonContextResponse,
    ResearchTaskAnalysisResponse,
)

def build_comparison_context(
    db: Session,
    analysis: ResearchTaskAnalysisResponse,
) -> ComparisonContextResponse:

    claims = prepare_review_claims(
        db=db,
        analysis=analysis,
    )

    paper_blocks: dict[int, list[str]] = {}
    used_numbers: set[int] = set()

    for claim in claims:
        for evidence in claim.evidence:
            number = evidence.source_number
            if number in used_numbers:
                continue

            source =  analysis.sources[number-1]
            paper_id = source.paper_id

            if paper_id not in paper_blocks:
                paper_blocks[paper_id] = []

            block = (
                f"[资料{number}]\n"
                f"论文编号：{paper_id}\n"
                f"论文标题：{evidence.paper_title}\n"
                f"页码：{evidence.page_number}\n"
                f"原文：{evidence.text}"
            )
            paper_blocks[paper_id].append(block)
            used_numbers.add(number)
    if len(paper_blocks) < 2:
        raise ValueError(
            "当前分析引用的原文不足两篇论文，"
            "请关联并处理多篇论文后重新生成分析"
        )

    sections: list[str] = []

    for paper_id in sorted(paper_blocks):
        blocks = paper_blocks[paper_id]

        section = (
                f"=== 论文 {paper_id} ===\n"
                + "\n\n".join(blocks)
        )

        sections.append(section)

    return ComparisonContextResponse(
        task_id=analysis.task_id,
        question=analysis.question,
        paper_ids=sorted(paper_blocks),
        source_numbers=sorted(used_numbers),
        context="\n\n".join(sections),
    )