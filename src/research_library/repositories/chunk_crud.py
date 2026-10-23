from typing import List

from sqlalchemy.orm import Session

from research_library.db.model import PaperDocument, PaperChunk
from research_library.retrieval.text_chunker import TextChunk

def get_chunks_by_document_id(
    db: Session,
    document_id: int,
) -> List[PaperChunk]:
    return (
        db.query(PaperChunk)
        .filter(PaperChunk.document_id == document_id)
        .order_by(PaperChunk.page_number, PaperChunk.chunk_index)
        .all()
    )

def replace_document_chunks(
    db: Session,
    document: PaperDocument,
    text_chunks: List[TextChunk],
) -> List[PaperChunk]:
    db.query(PaperChunk).filter(
        PaperChunk.document_id == document.id
    ).delete(synchronize_session=False)

    saved_chunks = []

    for text_chunk in text_chunks:
        saved_chunk = PaperChunk(
            document=document,
            page_number=text_chunk.page_number,
            chunk_index=text_chunk.chunk_index,
            text=text_chunk.text,
            char_count=len(text_chunk.text),
        )

        db.add(saved_chunk)
        saved_chunks.append(saved_chunk)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    for saved_chunk in saved_chunks:
        db.refresh(saved_chunk)

    return saved_chunks

def search_chunks_by_document_id(
    db: Session,
    document_id: int,
    keyword: str,
) -> List[PaperChunk]:
    return (
        db.query(PaperChunk)
        .filter(
            PaperChunk.document_id == document_id,
            PaperChunk.text.contains(keyword, autoescape=True),
        )
        .order_by(PaperChunk.page_number, PaperChunk.chunk_index)
        .all()
    )

def get_neighbor_chunks(
    db: Session,
    chunk_id: int,
    before: int=1,
    after: int=1,
) -> List[PaperChunk]:

    if type(before) is not int or type(after) is not int:
        raise ValueError("前后文本块数量必须是整数")

    if not 0 <= before <= 2 or not 0 <= after <= 2:
        raise ValueError("前后最多各补充2个文本块")

    target_chunk = db.get(PaperChunk, chunk_id)

    if target_chunk is None:
        raise ValueError(f"没有找到编号为 {chunk_id} 的文本块")

    chunks = get_chunks_by_document_id(
        db=db,
        document_id=target_chunk.document_id,
    )

    for index, chunk in enumerate(chunks):
        if chunk.id == target_chunk.id:
            start = max(0, index - before)
            end = min(len(chunks), index + after + 1)

            return chunks[start:end]

    raise ValueError("文档文本块已经发生变化，请重新查询")
