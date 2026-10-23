from dataclasses import dataclass

from sqlalchemy.orm import Session

import research_library.repositories.chunk_crud as chunk_crud
import research_library.retrieval.embedding_indexer as embedding_indexer
import research_library.repositories.page_crud as page_crud
from collections.abc import Callable

from research_library.db.model import PaperDocument
from research_library.retrieval.pdf_parser import ExtractedPage, extract_pdf_text
from research_library.retrieval.text_chunker import chunk_pages


@dataclass(frozen=True)
class PaperPreparationResult:
    page_count: int
    chunk_count: int
    indexed_count: int


def prepare_paper_document(
    db: Session,
    document: PaperDocument,
    chunk_size: int = 500,
    overlap: int = 100,
    on_stage: Callable[[str], None] | None = None
) -> PaperPreparationResult:
    if on_stage is not None:
        on_stage("extracting")

    pages = page_crud.get_pages_by_document_id(
        db,
        document.id,
    )

    if not pages:
        extracted_pdf = extract_pdf_text(document.file_path)

        if not extracted_pdf.full_text.strip():
            raise ValueError("没有从PDF中提取到正文")

        pages = page_crud.replace_document_pages(
            db,
            document,
            extracted_pdf,
        )
    if on_stage is not None:
        on_stage("chunking")

    chunks = chunk_crud.get_chunks_by_document_id(
        db,
        document.id,
    )

    if not chunks:
        extracted_pages = [
            ExtractedPage(
                page_number=page.page_number,
                text=page.text,
            )
            for page in pages
        ]

        text_chunks = chunk_pages(
            extracted_pages,
            chunk_size=chunk_size,
            overlap=overlap,
        )

        if not text_chunks:
            raise ValueError("PDF正文无法生成文本块")

        chunks = chunk_crud.replace_document_chunks(
            db,
            document,
            text_chunks,
        )
        
    if on_stage is not None:
        on_stage("indexing")

    indexed_count = embedding_indexer.index_document_chunks(
        db,
        document.id,
    )

    return PaperPreparationResult(
        page_count=len(pages),
        chunk_count=len(chunks),
        indexed_count=indexed_count,
    )
