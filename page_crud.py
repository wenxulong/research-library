"""封装 PDF 页面正文的查询和批量保存操作。"""

from sqlalchemy.orm import Session
from model import PaperDocument, PaperPage, PaperChunk
from pdf_parser import ExtractedPDF

def get_pages_by_document_id(
    db: Session,
    document_id: int,
) -> list[PaperPage]:
    """查询指定 PDF 的全部页面并按页码排序。"""
    return (
        db.query(PaperPage)
        .filter(PaperPage.document_id == document_id)
        .order_by(PaperPage.page_number)
        .all()
    )


def replace_document_pages(
    db: Session,
    document: PaperDocument,
    extracted_pdf: ExtractedPDF,
) -> list[PaperPage]:
    """删除旧页面并保存最新的 PDF 解析结果。"""
    db.query(PaperChunk).filter(
        PaperChunk.document_id == document.id
    ).delete(synchronize_session=False)

    db.query(PaperPage).filter(
        PaperPage.document_id == document.id
    ).delete(synchronize_session=False)

    page_models = []

    for extracted_page in extracted_pdf.pages:
        page_model = PaperPage(
            document=document,
            page_number=extracted_page.page_number,
            text=extracted_page.text,
            char_count=len(extracted_page.text),
        )

        db.add(page_model)
        page_models.append(page_model)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    for page_model in page_models:
        db.refresh(page_model)

    return page_models