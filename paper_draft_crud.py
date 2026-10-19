from sqlalchemy.orm import Session

from model import PaperDraft
from paper_draft_agent import parse_paper_draft
from schemas import (
    PaperDraftContent,
    PaperDraftMaterials,
    PaperDraftResponse,
    PaperDraftRequest,
    SavedPaperDraftResponse,
)

def save_paper_draft(
    db: Session,
    draft: PaperDraftResponse,
    materials: PaperDraftMaterials,
) -> PaperDraft:
    """保存初稿正文和本次写作使用的材料快照。"""

    record = PaperDraft(
        import_id=draft.import_id,
        draft_data=draft.model_dump(mode="json"),
        materials_snapshot=materials.model_dump(mode="json"),
    )

    db.add(record)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(record)

    return record

def to_saved_paper_draft(
    record: PaperDraft,
) -> SavedPaperDraftResponse:
    """把数据库记录转换成接口返回的初稿对象。"""

    materials = PaperDraftMaterials.model_validate(record.materials_snapshot)
    return SavedPaperDraftResponse(
        id=record.id,
        created_at=record.created_at,
        writing_request=PaperDraftRequest(
            metrics=[item.metric for item in materials.analysis.statistics],
            dataset=materials.analysis.dataset,
            scenario=materials.analysis.scenario,
            implementation_details=materials.implementation_details,
            actual_experiment_setup=materials.actual_experiment_setup,
        ),
        **record.draft_data,
    )

def update_paper_draft(
    db: Session,
    record: PaperDraft,
    content: PaperDraftContent,
) -> PaperDraft:
    """检查修改后的正文，并更新当前初稿记录。"""

    materials = PaperDraftMaterials.model_validate(
        record.materials_snapshot,
    )

    try:
        updated_draft = parse_paper_draft(
            text=content.model_dump_json(),
            materials=materials,
        )
    except RuntimeError as error:
        raise ValueError(str(error)) from error

    record.draft_data = updated_draft.model_dump(
        mode="json",
    )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(record)

    return record
