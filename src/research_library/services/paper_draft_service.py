from fastapi import FastAPI
from sqlalchemy.orm import Session

import research_library.agents.experiment_analysis_agent as experiment_analysis_agent
from research_library.db.model import ExperimentImport, PaperChunk
from research_library.schemas import PaperDraftEvidence, PaperDraftMaterials, PaperDraftRequest, ResearchPlanResponse

def build_paper_draft_materials(
    db: Session,
    imported: ExperimentImport,
    request: PaperDraftRequest,
) -> PaperDraftMaterials:
    """组装论文写作材料，暂不生成论文正文。"""

    plan = ResearchPlanResponse.model_validate(
        imported.plan_snapshot,
    )

    evidence = []

    for source in plan.sources:
        chunk = db.get(
            PaperChunk,
            source.chunk_id,
        )

        if chunk is None or chunk.document is None:
            raise ValueError(
                f"资料{source.source_number}对应的原文或PDF已不存在"
            )

        if (
                chunk.document.paper_id != source.paper_id
                or chunk.page_number != source.page_number
        ):
            raise ValueError(
                f"资料{source.source_number}与当前论文原文不对应"
            )
        evidence.append(
            PaperDraftEvidence(
                **source.model_dump(),
                text=chunk.text,
            )
        )

    analysis = experiment_analysis_agent.generate_experiment_analysis(
        imported=imported,
        request=request,
    )

    implementation_details = request.implementation_details.strip()
    actual_experiment_setup = request.actual_experiment_setup.strip()

    missing_information = []

    if not implementation_details:
        missing_information.append(
            "未提供实际实现说明，方法章节需要标记待补充。"
        )

    if not actual_experiment_setup:
        missing_information.append(
            "未提供实际实验设置，不能把计划条件写成实际运行条件。"
        )

    return PaperDraftMaterials(
        plan=plan,
        analysis=analysis,
        evidence=evidence,
        implementation_details=implementation_details,
        actual_experiment_setup=actual_experiment_setup,
        missing_information=missing_information,
    )
