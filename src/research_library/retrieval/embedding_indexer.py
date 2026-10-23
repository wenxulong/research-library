from sqlalchemy import select
from sqlalchemy.orm import Session

from research_library.repositories.chunk_crud import get_chunks_by_document_id
from research_library.retrieval.embedding_service import EMBEDDING_MODEL, embed_texts
from research_library.db.model import PaperChunk, PaperChunkEmbedding

def index_document_chunks(db: Session, document_id: int) -> int:
    chunks = get_chunks_by_document_id(db, document_id)
    if not chunks:
        return 0
    
    statement = (
        select(PaperChunkEmbedding)
        .join(PaperChunk)
        .where(PaperChunk.document_id == document_id)
    )

    saved = {
        record.chunk_id: record
        for record in db.scalars(statement).all()
    }

    pending = [
        chunk
        for chunk in chunks
        if chunk.id not in saved
           or saved[chunk.id].model_name != EMBEDDING_MODEL
    ]

    if not pending:
        return 0

    try:
        for start in range(0, len(pending), 16):
            batch = pending[start:start + 16]
            vectors = embed_texts([chunk.text for chunk in batch])

            for chunk, vector in zip(batch, vectors):
                record = saved.get(chunk.id)

                if record is None:
                    db.add(
                        PaperChunkEmbedding(
                            chunk_id=chunk.id,
                            model_name=EMBEDDING_MODEL,
                            vector=vector,
                        )
                    )
                else:
                    record.model_name = EMBEDDING_MODEL
                    record.vector = vector

        db.commit()
    except Exception:
        db.rollback()
        raise

    return len(pending)
