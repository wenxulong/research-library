"""封装论文 PDF 元数据的数据库操作。"""

from sqlalchemy.orm import Session

from research_library.db.model import Paper, PaperDocument

def get_document_by_paper_id(
    db: Session,
    paper_id: int,
) -> PaperDocument | None:
    """根据论文编号查询 PDF 元数据。"""
    return db.query(PaperDocument).filter(
        PaperDocument.paper_id == paper_id
    ).first()

def create_document(
    db: Session,
    paper: Paper,
    original_name: str,
    stored_name: str,
    file_path: str,
    content_type: str,
    file_size: int,
) -> PaperDocument:
    """创建并返回 PDF 元数据记录。"""
    document = PaperDocument(
        paper=paper,
        original_name=original_name,
        stored_name=stored_name,
        file_path=file_path,
        content_type=content_type,
        file_size=file_size,
    )

    db.add(document)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(document)
    return document


def delete_document(
    db: Session,
    document: PaperDocument,
) -> None:
    """删除 PDF 元数据记录。"""
    db.delete(document)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
