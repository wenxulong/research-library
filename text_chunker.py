from dataclasses import dataclass
from pdf_parser import ExtractedPage
from schemas import PaperPageResponse


@dataclass(frozen=True)
class TextChunk:
    page_number: int
    chunk_index: int
    text: str

def chunk_pages(
    pages: list[ExtractedPage],
    chunk_size: int = 500,
    overlap: int = 100,
) -> list[TextChunk]:

    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")

    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于 0，且小于 chunk_size")

    chunks: list[TextChunk] = []

    for page in pages:
        text = page.text.strip()

        if not text:
            continue

        start = 0
        chunk_index = 1

        while start < len(text):
            end = min(start + chunk_size, len(text))

            chunks.append(
                TextChunk(
                    page_number=page.page_number,
                    chunk_index=chunk_index,
                    text=text[start:end],
                )
            )

            if end == len(text):
                break

            start = end - overlap
            chunk_index += 1

    return chunks