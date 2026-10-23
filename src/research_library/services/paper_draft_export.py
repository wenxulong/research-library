from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

from sqlalchemy.orm import Session

import research_library.services.experiment_chart_service as experiment_chart_service
from research_library.db.model import Paper
from research_library.schemas import PaperDraftMaterials, PaperDraftResponse

PAPER_SECTIONS = (
    ("introduction", "1 引言"),
    ("related_work", "2 相关工作"),
    ("methods", "3 方法"),
    ("experiments", "4 实验设置与结果"),
    ("discussion", "5 讨论与局限"),
    ("conclusion", "6 结论"),
)

def build_paper_draft_markdown(
    db: Session,
    draft: PaperDraftResponse,
    materials: PaperDraftMaterials,
) -> str:
    """把论文初稿转换成带引用和图片链接的Markdown。"""

    paper_ids = list(
        dict.fromkeys(
            source.paper_id
            for source in draft.evidence
        )
    )

    paper_numbers = {
        paper_id: number
        for number, paper_id in enumerate(
            paper_ids,
            start=1,
        )
    }

    source_to_reference = {
        source.source_number: paper_numbers[source.paper_id]
        for source in draft.evidence
    }

    parts = [
        f"# {draft.content.title}",
        "## 摘要",
        draft.content.abstract,
    ]

    for field, title in PAPER_SECTIONS:
        parts.append(f"## {title}")

        paragraphs = getattr(
            draft.content,
            field,
        )

        for paragraph in paragraphs:
            reference_numbers = list(
                dict.fromkeys(
                    source_to_reference[number]
                    for number in paragraph.source_numbers
                )
            )

            citations = "".join(
                f"[{number}](#ref-{number})"
                for number in reference_numbers
            )

            parts.append(
                paragraph.text + citations
            )

        if field == "experiments":
            for number, statistics in enumerate(
                materials.analysis.statistics,
                start=1,
            ):
                parts.append(
                    f"### 指标：{statistics.metric}"
                )

                parts.append(
                    f"![指标{number}对比图]"
                    f"(figures/metric-{number}.png)"
                )

                parts.append(
                    "柱高为均值，误差棒为样本标准差，"
                    "n为有效记录数；不足两条有效记录时不绘制误差棒。"
                )

    parts.append("## 参考文献")

    if not paper_ids:
        parts.append("本初稿没有登记文献引用，相关工作需要核对补充。")

    for paper_id in paper_ids:
        paper = db.get(
            Paper,
            paper_id,
        )

        if paper is None:
            raise ValueError(
                f"参考文献对应的论文{paper_id}已不存在"
            )

        number = paper_numbers[paper_id]

        parts.append(
            f'<a id="ref-{number}"></a>'
        )

        parts.append(
            f"[{number}] {paper.author}. "
            f"{paper.title}. {paper.year}."
        )

    parts.append(
        "参考文献说明：以上条目使用资料库登记的作者、标题和年份；"
        "期刊、会议、卷期、页码和DOI等信息尚未补齐，投稿前需要核对。"
    )

    if draft.missing_information:
        parts.append("## 待补充信息")

        for item in draft.missing_information:
            parts.append(f"- {item}")

    parts.append(
        "初稿说明：本文由系统辅助生成，"
        "正文陈述、实验数字和引用支持关系需要作者核对。"
    )

    return "\n\n".join(parts) + "\n"

def build_paper_draft_zip(
    db: Session,
    draft: PaperDraftResponse,
    materials: PaperDraftMaterials,
) -> bytes:
    """打包论文初稿、引用证据、写作材料和实验图表。"""

    markdown = build_paper_draft_markdown(
        db=db,
        draft=draft,
        materials=materials,
    )

    with BytesIO() as buffer:
        with ZipFile(
                buffer,
                mode="w",
                compression=ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "paper.md",
                markdown,
            )

            archive.writestr(
                "draft.json",
                draft.model_dump_json(indent=2),
            )

            archive.writestr(
                "materials.json",
                materials.model_dump_json(indent=2),
            )

            for number, statistics in enumerate(
                    materials.analysis.statistics,
                    start=1,
            ):
                image_bytes = experiment_chart_service.build_experiment_chart(
                    statistics=statistics,
                    dataset=materials.analysis.dataset,
                    scenario=materials.analysis.scenario,
                )

                archive.writestr(
                    f"figures/metric-{number}.png",
                    image_bytes,
                )

        return buffer.getvalue()
