from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import revision_agent
import task_crud

from model import ResearchTask, ResearchTaskAnalysisVersion
from review_service import prepare_review_claims
from schemas import (
    EvidenceNoteAdoptRequest,
    EvidenceNoteAdoptResponse,
    ResearchTaskAnalysisResponse,
)

def adopt_evidence_note(
    db: Session,
    task_id: int,
    adoption: EvidenceNoteAdoptRequest,
) -> EvidenceNoteAdoptResponse:
    """重新检查建议，保存版本和新分析，清除旧审校。"""

    try:
        task = db.get(ResearchTask, task_id)

        if task is None:
            raise LookupError("没有找到这个研究任务")

        if not task.result:
            raise ValueError("这个任务还没有保存分析结果")

        task_crud.ensure_current_analysis(task)

        previous_analysis = task.result
        previous_review = task.review_result

        try:
            analysis = ResearchTaskAnalysisResponse.model_validate_json(
                previous_analysis
            )
        except ValidationError as error:
            raise ValueError("已保存的分析格式不正确，请重新分析") from error

        if analysis.task_id != task_id:
            raise ValueError("已保存分析的任务编号不匹配")

        if analysis.model_dump() != adoption.base_analysis.model_dump():
            raise ValueError("分析已发生变化，请重新查看分析并生成建议")

        revision = adoption.revision

        allowed_fields = {
            "methods",
            "experiments",
            "conclusions",
            "limitations",
        }

        if revision.field not in allowed_fields:
            raise ValueError("本接口只支持从四个列表字段移动到证据备注")

        original_items = getattr(analysis, revision.field)

        if original_items.count(revision.original_claim) != 1:
            raise ValueError("原陈述不存在或重复，无法确定要移动哪一条")

        if revision.revised_claim in analysis.evidence_notes:
            raise ValueError("证据备注中已经存在相同内容")

        claims = prepare_review_claims(
            db=db,
            analysis=analysis,
        )

        claim = next(
            item
            for item in claims
            if (
                item.field == revision.field
                and item.claim == revision.original_claim
            )
        )

        checked_move = revision_agent.move_revision_to_evidence_notes(
            claim=claim,
            revision=revision,
            source_count=len(analysis.sources),
        )

        if not checked_move.ready:
            raise ValueError("移动建议未同时通过证据审校和分类检查，不能采纳")

        updated_analysis = analysis.model_copy(deep=True)

        updated_items = getattr(
            updated_analysis,
            revision.field,
        )

        updated_items.remove(revision.original_claim)

        updated_analysis.evidence_notes.append(
            checked_move.item.claim
        )

        statement = (
            select(ResearchTask)
            .where(ResearchTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        current_task = db.scalar(statement)

        if current_task is None:
            raise LookupError("研究任务已经被删除")

        if (
            current_task.result != previous_analysis
            or current_task.review_result != previous_review
        ):
            raise ValueError("检查期间任务结果已变化，请重新生成建议")

        version = ResearchTaskAnalysisVersion(
            task_id=task_id,
            previous_analysis=previous_analysis,
            updated_analysis=updated_analysis.model_dump_json(),
            previous_review=previous_review,
            reason=(
                f"从 {revision.field} 移动到 evidence_notes："
                f"{revision.reason}"
            ),
        )

        db.add(version)
        db.flush()

        response = EvidenceNoteAdoptResponse(
            task_id=task_id,
            version_id=version.id,
            analysis=updated_analysis,
        )

        task_crud.save_task_analysis(
            db=db,
            task=current_task,
            analysis=updated_analysis,
        )

        return response

    except Exception:
        db.rollback()
        raise