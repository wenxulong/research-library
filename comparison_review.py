"""从当前分析的真实引用原文逐条审校对比报告，不保存审校结果。"""

from sqlalchemy.orm import Session

import comparison_agent
import comparison_service
import review_agent
from review_service import ReviewClaim, load_review_evidence
from schemas import (
    ComparisonReportResponse,
    ComparisonSourceResponse,
    ResearchTaskAnalysisResponse,
    ResearchTaskReviewResponse,
)


def prepare_comparison_claims(
    db: Session,
    analysis: ResearchTaskAnalysisResponse,
    report: ComparisonReportResponse,
) -> list[ReviewClaim]:
    """校验对比报告，并准备每条陈述对应的真实原文。"""

    if report.task_id != analysis.task_id or report.question != analysis.question:
        raise ValueError("对比报告与当前研究任务不一致")

    context = comparison_service.build_comparison_context(db, analysis)
    expected_sources = [
        ComparisonSourceResponse(
            source_number=number,
            **analysis.sources[number - 1].model_dump(),
        )
        for number in context.source_numbers
    ]

    if report.sources != expected_sources or report.paper_ids != context.paper_ids:
        raise ValueError("对比报告的资料来源与当前分析不一致，请重新生成报告")

    # 重新从正文提取编号，不能直接相信请求里填写的 source_numbers。
    try:
        checked_report = comparison_agent.parse_comparison_response(
            response_text=report.model_dump_json(),
            task_id=analysis.task_id,
            question=analysis.question,
            sources=expected_sources,
        )
    except RuntimeError as error:
        raise ValueError(f"对比报告不符合引用要求：{error}") from error

    groups = {
        "method_comparisons": checked_report.method_comparisons,
        "experiment_comparisons": checked_report.experiment_comparisons,
        "conclusion_comparisons": checked_report.conclusion_comparisons,
        "evidence_notes": checked_report.evidence_notes,
    }

    # 先准备全部真实证据，再调用模型；证据缺失时不开始外部审校。
    evidence_cache = {}
    claims: list[ReviewClaim] = []
    for field_name, statements in groups.items():
        for statement in statements:
            evidence = []
            for number in statement.source_numbers:
                if number not in evidence_cache:
                    evidence_cache[number] = load_review_evidence(db, analysis, number)
                evidence.append(evidence_cache[number])
            claims.append(
                ReviewClaim(
                    field=field_name,
                    claim=statement.text,
                    source_numbers=statement.source_numbers,
                    evidence=evidence,
                )
            )

    return claims


def review_comparison_report(
    db: Session,
    analysis: ResearchTaskAnalysisResponse,
    report: ComparisonReportResponse,
) -> ResearchTaskReviewResponse:
    """准备报告陈述，再逐条进行普通审校。"""

    claims = prepare_comparison_claims(
        db=db,
        analysis=analysis,
        report=report,
    )

    items = [
        review_agent.review_claim(claim)
        for claim in claims
    ]

    return ResearchTaskReviewResponse(
        task_id=analysis.task_id,
        items=items,
    )