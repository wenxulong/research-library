from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
import research_library.agents.research_question_agent as research_question_agent
import research_library.repositories.research_question_crud as research_question_crud
import research_library.agents.research_plan_service as research_plan_service
from research_library.db.model import ResearchQuestionSet
from research_library.db.database import get_db
from research_library.schemas import CheckedComparisonReportResponse, SavedResearchQuestionsResponse, ResearchQuestionSelectRequest, ResearchConditions, ResearchPlanResponse
import research_library.repositories.report_job_crud as report_job_crud

from fastapi import APIRouter

router = APIRouter()

@router.post(
    "/report-jobs/{job_id}/research-questions",
    response_model=SavedResearchQuestionsResponse,
)
def generate_research_questions_endpoint(
    job_id: int,
    db: Session = Depends(get_db),
):
    """根据一份已通过审校的报告生成候选研究问题。"""

    job = report_job_crud.get_report_job(db, job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这个报告作业",
        )

    if job.status != "succeeded" or not job.result:
        raise HTTPException(
            status_code=409,
            detail="报告尚未生成成功",
        )

    try:
        checked = CheckedComparisonReportResponse.model_validate(
            job.result,
        )

        if not checked.passed:
            raise ValueError("请先使用已通过审校的对比报告")

        result = research_question_agent.generate_research_questions(
            db=db,
            report_job_id=job.id,
            report=checked.report,
        )

        return research_question_crud.save_research_questions(
            db=db,
            result=result,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error


@router.get(
    "/research-question-sets/{set_id}",
    response_model=SavedResearchQuestionsResponse,
)
def read_research_question_set(
    set_id: int,
    db: Session = Depends(get_db),
):
    """读取一组已保存的候选问题及选择结果。"""

    record = db.get(ResearchQuestionSet, set_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这组候选研究问题",
        )
    return record


@router.patch(
    "/research-question-sets/{set_id}/selection",
    response_model=SavedResearchQuestionsResponse,
)
def select_research_question_endpoint(
    set_id: int,
    selection: ResearchQuestionSelectRequest,
    db: Session = Depends(get_db),
):
    """选择候选问题，或修改最终确认的问题表述。"""
    record = db.get(ResearchQuestionSet, set_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这组候选研究问题",
        )

    try:
        return research_question_crud.select_research_question(
            db=db,
            record=record,
            selection=selection,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error


@router.post(
    "/research-question-sets/{set_id}/plan",
    response_model=ResearchPlanResponse,
)
def generate_research_plan_endpoint(
    set_id: int,
    conditions: ResearchConditions,
    db: Session = Depends(get_db),
):
    """为用户选定的问题生成研究方案和实验计划。"""

    record = db.get(ResearchQuestionSet, set_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这组候选研究问题",
        )

    try:
        return research_plan_service.generate_research_plan(
            db=db,
            record=record,
            conditions=conditions,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error


@router.get(
    "/research-question-sets/{set_id}/plan",
    response_model=ResearchPlanResponse,
)
def read_research_plan_endpoint(
    set_id: int,
    db: Session = Depends(get_db),
):
    """读取当前保存的研究方案和实验计划。"""
    record = db.get(ResearchQuestionSet, set_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这组候选研究问题",
        )
    if record.plan is None:
        raise HTTPException(
            status_code=404,
            detail="尚未生成研究计划，或选择问题后需要重新生成",
        )

    return ResearchPlanResponse.model_validate(record.plan)
