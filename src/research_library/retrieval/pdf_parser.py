"""使用版面分析提取逐页 Markdown 正文、表格，并按需执行本地 OCR。"""

from dataclasses import dataclass
from pathlib import Path

import pymupdf
import pymupdf4llm


@dataclass(frozen=True)
class ExtractedPage:
    """原 PDF 页码和当前页的 Markdown 正文。"""
    page_number: int
    text: str


@dataclass(frozen=True)
class ExtractedPDF:
    """保留现有调用方使用的逐页、全文接口。"""
    page_count: int
    pages: list[ExtractedPage]
    full_text: str


def extract_pdf_text(file_path: str | Path) -> ExtractedPDF:
    """解析正文与表格；扫描页通过本地 OCR 引擎提取文字。"""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"没有找到PDF文件：{path}")

    try:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise ValueError("PDF文件已加密，无法提取正文")
            page_count = document.page_count
            results = pymupdf4llm.to_markdown(
                document,
                page_chunks=True,
                use_ocr=True,
                show_progress=False,
            )
            pages = [
                ExtractedPage(
                    page_number=int(result["metadata"]["page_number"]),
                    text=result["text"].strip(),
                )
                for result in results
            ]
    except pymupdf.FileDataError as error:
        raise ValueError("无法解析PDF文件，文件可能已经损坏") from error

    if [page.page_number for page in pages] != list(range(1, page_count + 1)):
        raise ValueError("PDF解析返回的页码与原文件不一致")

    return ExtractedPDF(
        page_count=page_count,
        pages=pages,
        full_text="\n\n".join(page.text for page in pages if page.text),
    )
