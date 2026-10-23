from uuid import uuid4

from research_library.agents.comparison_agent import parse_comparison_response
from research_library.services.comparison_evidence_workflow import ComparisonEvidenceResult
from research_library.schemas import CheckedComparisonReportResponse, ComparisonSourceResponse, ResearchTaskAnalysisResponse, ResearchTaskReviewResponse, RejectedComparisonRevisionResponse
import json

def build_report_sources(
    analysis: ResearchTaskAnalysisResponse,
    result: ComparisonEvidenceResult,
) -> list[ComparisonSourceResponse]:

    original_scores = {
        source.chunk_id: source.score
        for source in analysis.sources
    }

    evidence_list = sorted(
        result.evidence_by_chunk_id.values(),
        key=lambda evidence: evidence.source_number,
    )

    sources: list[ComparisonSourceResponse] = []

    for evidence in evidence_list:
        if evidence.paper_id is None:
            raise ValueError("证据缺少论文编号，无法组装报告")

        sources.append(
            ComparisonSourceResponse(
                source_number=evidence.source_number,
                paper_id=evidence.paper_id,
                paper_title=evidence.paper_title,
                chunk_id=evidence.chunk_id,
                page_number=evidence.page_number,
                score=original_scores.get(evidence.chunk_id),
            )
        )

    # 全部证据处理完再返回，避免只保留第一条资料。
    return sources

def assemble_comparison_report(
        analysis: ResearchTaskAnalysisResponse,
        result: ComparisonEvidenceResult,
) ->CheckedComparisonReportResponse:
    if result.task_id != analysis.task_id:
        raise ValueError("处理结果与研究任务不一致")

    sources = build_report_sources(analysis, result)

    groups = {
        "method_comparisons": [],
        "experiment_comparisons": [],
        "conclusion_comparisons": [],
        "evidence_notes": [],
    }

    initial_reviews = []
    final_reviews = []
    has_revision = False
    rejected_revisions = []

    for item in result.items:
        initial_reviews.append(item.review_result.initial_review)

        # 默认保留补证后的原陈述及其审校；只有通过复审才采用修正版。
        claim = item.review_result.claim
        review = item.review_result.final_review

        if item.revision_result is not None:
            revision_result = item.revision_result
            revised_claim = revision_result.revised_claim
            revised_review = revision_result.review
            has_revision = True

            if (
                revised_review.field != revised_claim.field
                or revised_review.claim != revised_claim.claim
                or revision_result.revision.original_claim != claim.claim
                or revision_result.revision.field != claim.field
                or revised_claim.field != claim.field
                or revision_result.revision.revised_claim != revised_claim.claim
            ):
                raise ValueError("修订陈述与审校结果不一致")

            if revised_review.verdict == "supported":
                claim = revised_claim
                review = revised_review
            else:
                rejected_revisions.append(
                    RejectedComparisonRevisionResponse(
                        revision=revision_result.revision,
                        review=revised_review,
                    )
                )

        if claim.field not in groups:
            raise ValueError(f"未知的报告字段：{claim.field}")

        if review.field != claim.field or review.claim != claim.claim:
            raise ValueError("最终陈述与审校结果不一致")

        # 最终正文和对应审校共用编号；不修改原始审校对象。
        claim_id = uuid4().hex
        groups[claim.field].append({"claim_id": claim_id, "text": claim.claim})
        final_reviews.append(review.model_copy(update={"claim_id": claim_id}))

    report = parse_comparison_response(
        response_text=json.dumps(groups, ensure_ascii=False),
        task_id=analysis.task_id,
        question=analysis.question,
        sources=sources,
    )

    passed = bool(final_reviews) and all(
        review.verdict == "supported"
        for review in final_reviews
    )

    return CheckedComparisonReportResponse(
        task_id=analysis.task_id,
        report=report,
        initial_review=ResearchTaskReviewResponse(
            task_id=analysis.task_id,
            items=initial_reviews,
        ),
        review=ResearchTaskReviewResponse(
            task_id=analysis.task_id,
            items=final_reviews,
        ),
        passed=passed,
        correction_rounds=int(has_revision),
        rejected_revisions=rejected_revisions,
    )
