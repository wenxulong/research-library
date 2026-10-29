"""按句段切分正文，按完整行切分 Markdown 表格。"""

import re
from dataclasses import dataclass

from research_library.retrieval.pdf_parser import ExtractedPage


@dataclass(frozen=True)
class TextChunk:
    page_number: int
    chunk_index: int
    text: str


TABLE_SEPARATOR = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$"
)
TABLE_CAPTION = re.compile(r"^\s*(?:#{1,6}\s*)?(?:Table\s+\d+|表\s*\d+)", re.IGNORECASE)
PROSE_BOUNDARIES = (
    re.compile(r"\n[ \t]*\n+"),
    re.compile(r'''[。！？]+[”’」』]*|[.!?]+["’”']*(?=\s|$)'''),
)


def is_table_start(lines: list[str], index: int) -> bool:
    """表头后紧跟 Markdown 分隔行，才视为表格。"""
    return (
        index + 1 < len(lines)
        and "|" in lines[index]
        and "|" in lines[index + 1]
        and TABLE_SEPARATOR.fullmatch(lines[index + 1]) is not None
    )


def split_prose(text: str, chunk_size: int, overlap: int) -> list[str]:
    """在长度上限内优先选择段落、句末边界，保留字符重叠。"""
    text = text.strip()
    parts: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(start + chunk_size, len(text))
        end = hard_end
        if hard_end < len(text):
            # 块长必须大于 overlap，保证下一轮起点向后移动。
            min_end = start + max(chunk_size // 2, overlap + 1)
            for pattern in PROSE_BOUNDARIES:
                boundary = None
                # 对完整正文匹配，避免把切分窗口末尾误认成英文句末。
                for match in pattern.finditer(text, min_end - 1):
                    if match.end() > hard_end:
                        break
                    if match.end() >= min_end:
                        boundary = match.end()
                if boundary is not None:
                    end = boundary
                    break
        parts.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return parts


def split_table(lines: list[str], max_chars: int, caption: str = "") -> list[str]:
    """每块重复表头；不截断数据行，不在表格内应用字符重叠。"""
    header = "\n".join(([caption] if caption else []) + lines[:2])
    if len(header) > max_chars:
        raise ValueError("表格表头过长，请检查PDF表格解析结果")
    parts: list[str] = []
    rows: list[str] = []
    size = len(header)
    for row in lines[2:]:
        if len(header) + 1 + len(row) > max_chars:
            raise ValueError("表格单行过长，请检查解析结果或调整表格长度限制")
        if rows and size + 1 + len(row) > max_chars:
            parts.append("\n".join([header, *rows]))
            rows = []
            size = len(header)
        rows.append(row)
        size += 1 + len(row)
    if rows or not parts:
        parts.append("\n".join([header, *rows]))
    return parts


def chunk_pages(
    pages: list[ExtractedPage],
    chunk_size: int = 500,
    overlap: int = 100,
    table_chunk_size: int = 2000,
) -> list[TextChunk]:
    """逐页分块，保持页码、原文先后顺序和每页从1开始的序号。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于 0，且小于 chunk_size")
    if table_chunk_size <= 0:
        raise ValueError("table_chunk_size 必须大于 0")

    chunks: list[TextChunk] = []
    for page in pages:
        lines = page.text.strip().splitlines()
        parts: list[str] = []
        prose: list[str] = []
        index = 0
        while index < len(lines):
            if not is_table_start(lines, index):
                prose.append(lines[index])
                index += 1
                continue
            # 仅将明确标记为 Table/表 的紧邻标题随表头重复。
            while prose and not prose[-1].strip():
                prose.pop()
            caption = prose.pop() if prose and TABLE_CAPTION.match(prose[-1]) else ""
            parts.extend(split_prose("\n".join(prose), chunk_size, overlap))
            prose = []
            table = lines[index:index + 2]
            index += 2
            while index < len(lines) and lines[index].strip():
                # 解析器输出的表格行具有首尾管道符，避免吞掉普通正文。
                row = lines[index].strip()
                if not (row.startswith("|") and row.endswith("|")):
                    break
                if is_table_start(lines, index):
                    break
                table.append(lines[index])
                index += 1
            parts.extend(split_table(table, table_chunk_size, caption))
        parts.extend(split_prose("\n".join(prose), chunk_size, overlap))
        chunks.extend(
            TextChunk(page.page_number, number, text)
            for number, text in enumerate(parts, start=1)
        )
    return chunks
