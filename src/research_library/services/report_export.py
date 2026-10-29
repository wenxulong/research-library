from research_library.schemas import CheckedComparisonReportResponse

def quote_text(text: str) -> str:
    """把一段文字转换成 Markdown 引用段落。"""

    lines = []
    for line in text.splitlines():
        lines.append(f"> {line}")

    return "\n".join(lines)

def render_sources(
    checked_report: CheckedComparisonReportResponse,
) -> str:
    """把报告的资料登记表整理成 Markdown。"""

    parts = ["## 资料来源"]

    sources = sorted(
        checked_report.report.sources,
        key=lambda source: source.source_number,
    )
    for source in sources:
        parts.append(
            f"### 资料{source.source_number}"
        )

        parts.append(
            quote_text(
                f"论文编号：{source.paper_id}\n"
                f"论文标题：{source.paper_title}\n"
                f"页码：{source.page_number}\n"
                f"文本块编号：{source.chunk_id}"
            )
        )

    return "\n\n".join(parts)

def render_investigation_review(review) -> str:
    """保留调查与复审的关系，不把分歧静默折叠成通过。"""
    labels = {
        "agrees": "复审核对了调查意见",
        "disagrees": "复审与调查意见存在分歧",
        "uncertain": "调查疑问尚未确认",
    }
    if review.investigation_assessment is None:
        return ""
    return f"**{labels[review.investigation_assessment]}：**\n\n{quote_text(review.investigation_reason)}"


def build_report_markdown(
    checked_report: CheckedComparisonReportResponse,
    job_id: int,
) -> str:
    """根据已经保存的报告生成 Markdown，不调用模型。"""

    report = checked_report.report

    review_status = (
        "模型审校通过"
        if checked_report.passed
        else "待核验：存在未通过审校的陈述"
    )

    parts = [
        "# 科研论文对比报告",
        f"- 研究任务编号：{checked_report.task_id}",
        f"- 报告作业编号：{job_id}",
        f"- 审校状态：{review_status}",
        f"- 修订轮数：{checked_report.correction_rounds}",
        "## 研究问题",
        quote_text(report.question),
    ]

    sections = [
        ("方法对比", report.method_comparisons),
        ("实验对比", report.experiment_comparisons),
        ("结论对比", report.conclusion_comparisons),
        ("证据说明", report.evidence_notes),
    ]

    reviews_by_claim_id = {item.claim_id: item for item in checked_report.review.items if item.claim_id}
    evidence_labels = {
        "supported": "支持判断的复审依据",
        "contradicted": "反对证据／矛盾核查依据（不作为支持引用）",
        "partially_supported": "适用条件与限制的核查依据",
        "insufficient_evidence": "证据缺口的核查资料（尚不能支持结论）",
    }
    for title, statements in sections:
        parts.append(f"## {title}")

        if not statements:
            parts.append("本部分没有内容。")
            continue

        for number, statement in enumerate(statements, start=1):
            parts.append(f"### 陈述{number}")
            parts.append(quote_text(statement.text))
            review = reviews_by_claim_id.get(statement.claim_id)
            if review is not None:
                parts.append(f"**当前审校：** {review.verdict}")
                parts.append(quote_text(review.reason))
                investigation_note = render_investigation_review(review)
                if investigation_note:
                    parts.append(investigation_note)
                references = " ".join(f"[资料{n}]" for n in review.source_numbers) or "无"
                parts.append(f"**{evidence_labels[review.verdict]}：** {references}")

    parts.append("## 最终审校结果")

    for number, item in enumerate(
            checked_report.review.items,
            start=1,
    ):
        parts.append(f"### 审校项{number}")
        parts.append(quote_text(item.claim))
        parts.append(f"**判断：** {item.verdict}")
        parts.append(quote_text(item.reason))
        investigation_note = render_investigation_review(item)
        if investigation_note:
            parts.append(investigation_note)

    if checked_report.rejected_revisions:
        parts.append("## 未采用的修订")

        for item in checked_report.rejected_revisions:
            parts.append(quote_text(item.revision.revised_claim))
            parts.append(f"**复审判断：** {item.review.verdict}")
            parts.append(quote_text(item.review.reason))

    parts.append(render_sources(checked_report))

    return "\n\n".join(parts) + "\n"
