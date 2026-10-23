"""使用 PyMuPDF 按页提取 PDF 正文。"""

from dataclasses import dataclass
from pathlib import Path
import pymupdf

@dataclass(frozen=True)
class ExtractedPage:
    """表示从 PDF 单页提取出的页码和正文。"""
    page_number: int
    text: str

@dataclass(frozen=True)
class ExtractedPDF:
    """表示整份 PDF 的逐页正文和合并正文。"""
    page_count: int
    pages: list[ExtractedPage]
    full_text: str

def extract_pdf_text(file_path: str | Path) -> ExtractedPDF:
    """打开 PDF，按页提取正文并返回结构化结果。"""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f'没有找到PDF文件：{path}')

    try:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise ValueError("PDF文件已加密，无法提取正文")

            pages = []
            for page_index, page in enumerate(document):
                text = page.get_text("text").strip()
                pages.append(
                    ExtractedPage(
                        page_number=page_index + 1,
                        text=text,
                    )
                )

    except pymupdf.FileDataError as error:
        raise ValueError("无法解析PDF文件，文件可能已经损坏") from error

    full_text = "\n\n".join(
        page.text for page in pages
        if page.text
    )
    return ExtractedPDF(
        page_count=len(pages),
        pages=pages,
        full_text=full_text,
    )
