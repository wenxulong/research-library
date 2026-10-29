from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
import research_library.services.paper_draft_service as paper_draft_service
import research_library.agents.paper_draft_agent as paper_draft_agent
import research_library.repositories.paper_draft_crud as paper_draft_crud
import research_library.agents.paper_writing_skill as paper_writing_skill
from research_library.db.model import ExperimentImport
from research_library.db.model import PaperDraft
from research_library.services.paper_draft_export import build_paper_draft_zip
from research_library.db.database import get_db
from research_library.schemas import PaperDraftRequest, PaperDraftResponse, PaperDraftMaterials, SavedPaperDraftResponse, PaperDraftContent, PaperDraftExpandRequest, PaperDraftExpandResponse, PaperDraftSkillRequest, PaperDraftSkillResponse
from fastapi.responses import Response

from fastapi import APIRouter

router = APIRouter()

@router.post(
    "/experiment-imports/{import_id}/paper-draft",
    response_model=SavedPaperDraftResponse,
)
def create_paper_draft(
    import_id: int,
    request: PaperDraftRequest,
    db: Session = Depends(get_db),
):
    """生成论文初稿，并保存正文与写作材料。"""

    imported = db.get(
        ExperimentImport,
        import_id,
    )

    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        materials = paper_draft_service.build_paper_draft_materials(
            db=db,
            imported=imported,
            request=request,
        )

        draft = paper_draft_agent.generate_paper_draft(
            db=db,
            materials=materials,
        )

        record = paper_draft_crud.save_paper_draft(
            db=db,
            draft=draft,
            materials=materials,
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

    return paper_draft_crud.to_saved_paper_draft(record)


@router.post(
    "/experiment-imports/{import_id}/paper-draft/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/zip": {},
            },
        },
    },
)
def export_paper_draft(
    import_id: int,
    request: PaperDraftRequest,
    db: Session = Depends(get_db),
):
    """生成论文初稿，并下载正文、材料和图表压缩包。"""

    imported = db.get(
        ExperimentImport,
        import_id,
    )

    if imported is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这次实验结果导入",
        )

    try:
        materials = paper_draft_service.build_paper_draft_materials(
            db=db,
            imported=imported,
            request=request,
        )

        draft = paper_draft_agent.generate_paper_draft(
            db=db,
            materials=materials,
        )

        archive_bytes = build_paper_draft_zip(
            db=db,
            draft=draft,
            materials=materials,
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

    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="paper-draft-{import_id}.zip"'
            ),
        },
    )


@router.get(
    "/paper-drafts/{draft_id}",
    response_model=SavedPaperDraftResponse,
)
def read_paper_draft(
    draft_id: int,
    db: Session = Depends(get_db),
):
    """读取已保存的论文初稿，不调用模型。"""

    record = db.get(
        PaperDraft,
        draft_id,
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这份论文初稿",
        )

    return paper_draft_crud.to_saved_paper_draft(record)


@router.get(
    "/paper-drafts/{draft_id}/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/zip": {},
            },
        },
    },
)
def export_saved_paper_draft(
    draft_id: int,
    db: Session = Depends(get_db),
):
    """使用已保存的正文和材料导出，不重新生成初稿。"""

    record = db.get(
        PaperDraft,
        draft_id,
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这份论文初稿",
        )

    draft = PaperDraftResponse.model_validate(
        record.draft_data,
    )

    materials = PaperDraftMaterials.model_validate(
        record.materials_snapshot,
    )

    try:
        archive_bytes = build_paper_draft_zip(
            db=db,
            draft=draft,
            materials=materials,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="paper-draft-{draft_id}.zip"'
            ),
        },
    )


@router.patch(
    "/paper-drafts/{draft_id}",
    response_model=SavedPaperDraftResponse,
)
def update_saved_paper_draft(
    draft_id: int,
    content: PaperDraftContent,
    db: Session = Depends(get_db),
):
    """保存人工修改的论文正文，不调用模型。"""

    record = db.get(
        PaperDraft,
        draft_id,
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这份论文初稿",
        )

    try:
        updated_record = paper_draft_crud.update_paper_draft(
            db=db,
            record=record,
            content=content,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    return paper_draft_crud.to_saved_paper_draft(
        updated_record,
    )


@router.post(
    "/paper-drafts/{draft_id}/expand",
    response_model=PaperDraftExpandResponse,
)
def expand_saved_paper_draft(
    draft_id: int,
    request: PaperDraftExpandRequest,
    db: Session = Depends(get_db),
):
    """生成章节扩写建议，不修改已经保存的初稿。"""

    record = db.get(
        PaperDraft,
        draft_id,
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这份论文初稿",
        )

    draft = PaperDraftResponse.model_validate(
        record.draft_data,
    )

    materials = PaperDraftMaterials.model_validate(
        record.materials_snapshot,
    )

    try:
        suggestion = paper_draft_agent.expand_paper_draft_section(
            db=db,
            draft=draft,
            materials=materials,
            request=request,
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

    return PaperDraftExpandResponse(
        draft_id=record.id,
        section=request.section,
        paragraphs=suggestion.paragraphs,
    )


@router.post(
    "/paper-drafts/{draft_id}/skill",
    response_model=PaperDraftSkillResponse,
)
def run_saved_paper_draft_skill(
    draft_id: int,
    request: PaperDraftSkillRequest,
    db: Session = Depends(get_db),
):
    """根据自然语言要求执行当前初稿的写作Skill。"""
    record = db.get(
        PaperDraft,
        draft_id,
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这份论文初稿",
        )
    draft = PaperDraftResponse.model_validate(
        record.draft_data,
    )
    materials = PaperDraftMaterials.model_validate(
        record.materials_snapshot,
    )
    try:
        return paper_writing_skill.run_paper_writing_skill(
            db=db,
            draft_id=record.id,
            draft=draft,
            materials=materials,
            instruction=request.instruction,
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
