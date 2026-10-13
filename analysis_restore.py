"""恢复历史分析，并记录本次恢复前后的内容。"""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import task_crud

from model import ResearchTask, ResearchTaskAnalysisVersion
from schemas import (
    AnalysisRestoreRequest,
    AnalysisRestoreResponse,
    ResearchTaskAnalysisResponse,
)


def restore_analysis_version(
    db: Session,
    task_id: int,
    version_id: int,
    restore: AnalysisRestoreRequest,
) -> AnalysisRestoreResponse:
    """恢复指定历史记录的 previous_analysis，并清除当前审校。"""

    try:
        statement = (
            select(ResearchTask)
            .where(ResearchTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        task = db.scalar(statement)

        if task is None:
            raise LookupError("没有找到这个研究任务")

        task_crud.ensure_current_analysis(task)

        version = task_crud.get_analysis_version(
            db=db,
            task_id=task_id,
            version_id=version_id,
        )

        if version is None:
            raise LookupError("这个任务下没有找到指定版本")

        if not task.result:
            raise ValueError("当前任务没有分析结果，本接口暂不支持这种恢复")

        try:
            current_analysis = (
                ResearchTaskAnalysisResponse.model_validate_json(
                    task.result
                )
            )

            restored_analysis = (
                ResearchTaskAnalysisResponse.model_validate_json(
                    version.previous_analysis
                )
            )

        except ValidationError as error:
            raise ValueError("当前分析或历史分析格式不正确") from error

        if (
            current_analysis.task_id != task_id
            or restored_analysis.task_id != task_id
        ):
            raise ValueError("分析内容中的任务编号不匹配")

        if (
            current_analysis.model_dump()
            != restore.base_analysis.model_dump()
        ):
            raise ValueError("当前分析已变化，请刷新后重新操作")

        if (
            current_analysis.model_dump()
            == restored_analysis.model_dump()
        ):
            raise ValueError("当前分析已经与目标内容相同，无需恢复")

        history = ResearchTaskAnalysisVersion(
            task_id=task_id,
            previous_analysis=task.result,
            updated_analysis=restored_analysis.model_dump_json(),
            previous_review=task.review_result,
            reason=f"恢复版本 {version_id} 的修改前分析",
        )

        db.add(history)
        db.flush()

        response = AnalysisRestoreResponse(
            task_id=task_id,
            restored_from_version_id=version_id,
            version_id=history.id,
            analysis=restored_analysis,
        )

        task_crud.save_task_analysis(
            db=db,
            task=task,
            analysis=restored_analysis,
        )

        return response

    except Exception:
        db.rollback()
        raise