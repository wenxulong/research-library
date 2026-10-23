from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

import research_library.services.experiment_chart_service as experiment_chart_service
from research_library.db.model import ExperimentImport
from research_library.schemas import ExperimentAnalysisResponse, ResearchPlanResponse

def render_list(items: list[str]) -> str:
    """把字符串列表转换成Markdown项目列表。"""

    if not items:
        return "未提供。"

    return "\n".join(
        f"- {item}"
        for item in items
    )

def build_research_report_markdown(
    imported: ExperimentImport,
    plan: ResearchPlanResponse,
    analysis: ExperimentAnalysisResponse,
) -> str:
    """把研究计划和实验分析组装成Markdown报告。"""

    parts = [
        "# 研究实验报告",
        f"- 研究任务编号：{plan.task_id}",
        f"- 实验导入编号：{imported.id}",
        f"- 数据集：{analysis.dataset}",
        f"- 场景：{analysis.scenario}",
        "## 一、研究问题",
        plan.research_question,
        "## 二、文献观察与研究动机",
        plan.basis.observation,
    ]

    citations = "、".join(
        f"[资料{number}](#source-{number})"
        for number in plan.basis.source_numbers
    )

    parts.append(f"上述文献观察对应的资料：{citations}")

    if plan.basis.evidence_review is not None:
        parts.append(
            "观察审校结果："
            f"{plan.basis.evidence_review.verdict}。"
            f"{plan.basis.evidence_review.reason}"
        )

    parts.extend([
        "## 三、研究方案与实验计划",
        "### 研究目标",
        plan.content.objective,
        "### 待验证假设",
        plan.content.hypothesis,
        "### 研究条件",
        plan.conditions.model_dump_json(indent=2),
    ])

    plan_sections = [
        ("方法步骤", plan.content.method_steps),
        ("对比基线", plan.content.baselines),
        ("指标定义", plan.content.metrics),
        ("主实验计划", plan.content.main_experiments),
        ("消融实验计划", plan.content.ablation_experiments),
        ("执行步骤", plan.content.execution_steps),
        ("计划中的风险", plan.content.risks),
    ]

    for title, items in plan_sections:
        parts.append(f"### {title}")
        parts.append(render_list(items))

    parts.extend([
        "### 数据准备计划",
        plan.content.data_plan,
        "说明：本节记录的是实验计划，不代表所有计划均已实际完成。",
    ])

    parts.append("## 四、实验图表与统计依据")

    for number, statistics in enumerate(
        analysis.statistics,
        start=1,
    ):
        parts.append(f"### 指标：{statistics.metric}")

        parts.append(
            f"![指标{number}对比图](figures/metric-{number}.png)"
        )

        parts.append(
            "柱高表示均值，误差棒表示样本标准差；"
            "n表示有效记录数。"
            "不足两条有效记录时不绘制误差棒。"
        )

        parts.append(
            "```json\n"
            + statistics.model_dump_json(indent=2)
            + "\n```"
        )

    parts.extend([
        "## 五、实验分析",
        "### 总体总结",
        analysis.content.summary,
    ])

    analysis_sections = [
        ("方法差异", analysis.content.differences),
        ("多指标权衡", analysis.content.tradeoffs),
        ("结果限制", analysis.content.limitations),
        ("后续实验建议", analysis.content.next_experiments),
    ]

    for title, items in analysis_sections:
        parts.append(f"### {title}")
        parts.append(render_list(items))

    parts.append("## 六、文献资料来源")

    for source in sorted(
            plan.sources,
            key=lambda item: item.source_number,
    ):
        parts.append(
            f'<a id="source-{source.source_number}"></a>'
        )

        parts.append(
            f"### 资料{source.source_number}：{source.paper_title}"
        )

        parts.append(
            f"- 论文编号：{source.paper_id}\n"
            f"- PDF页码：{source.page_number}\n"
            f"- 文本块编号：{source.chunk_id}"
        )

    parts.append(
        "引用说明：文献资料用于追溯研究动机和计划依据；"
        "本次实验数值来自results.csv及程序统计。"
        "模型生成的分析仍需作者核对，不能作为文献已经证明的结论。"
    )

    return "\n\n".join(parts) + "\n"

def build_research_report_zip(
    imported: ExperimentImport,
    analysis: ExperimentAnalysisResponse,
) -> bytes:
    """打包研究报告、图表、实验数据和计划快照。"""

    plan = ResearchPlanResponse.model_validate(imported.plan_snapshot)

    markdown = build_research_report_markdown(
        imported=imported,
        plan=plan,
        analysis=analysis,
    )

    with BytesIO() as buffer:
        with ZipFile(
                buffer,
                mode="w",
                compression=ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "report.md",
                markdown,
            )

            archive.writestr(
                "results.csv",
                imported.raw_csv,
            )

            archive.writestr(
                "analysis.json",
                analysis.model_dump_json(indent=2),
            )

            archive.writestr(
                "plan.json",
                plan.model_dump_json(indent=2),
            )

            for number, statistics in enumerate(
                    analysis.statistics,
                    start=1,
            ):
                image_bytes = experiment_chart_service.build_experiment_chart(
                    statistics=statistics,
                    dataset=analysis.dataset,
                    scenario=analysis.scenario,
                )

                archive.writestr(
                    f"figures/metric-{number}.png",
                    image_bytes,
                )

        return buffer.getvalue()
