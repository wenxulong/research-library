from sqlalchemy.orm import Session

from model import ResearchQuestionSet
from schemas import (
    ResearchQuestionsResponse,
    ResearchQuestionSelectRequest,
)

def save_research_questions(
    db: Session,
    result: ResearchQuestionsResponse,
) ->ResearchQuestionSet:
    """保存一次生成的候选问题，初始状态为尚未选择。"""

    payload = result.model_dump(mode="json")

    record = ResearchQuestionSet(
        task_id=result.task_id,
        report_job_id=result.report_job_id,
        candidates=payload["candidates"],
        sources=payload["sources"],
    )

    try:
        db.add(record)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(record)

    return record

def select_research_question(
    db: Session,
    record: ResearchQuestionSet,
    selection: ResearchQuestionSelectRequest,
) -> ResearchQuestionSet:
    """选择一个候选问题，保存用户最终确认的表述。"""

    number = selection.candidate_number

    if number > len(record.candidates):
        raise ValueError("候选问题序号超出范围")

    candidate = record.candidates[number - 1]

    question = (
        selection.research_question
        if selection.research_question is not None
        else candidate["research_question"]
    )

    question =question.strip()

    if not question:
        raise ValueError("研究问题不能为空")

    if (
        record.selected_candidate_number != number
        or record.selected_question != question
    ):
        record.plan = None

    record.selected_candidate_number = number
    record.selected_question = question

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(record)

    return record
