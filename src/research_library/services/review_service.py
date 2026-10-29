from dataclasses import dataclass
from research_library.core.citation_utils import extract_source_numbers
from sqlalchemy.orm import Session
from research_library.db.model import PaperChunk, PaperDocument
from research_library.schemas import ResearchTaskAnalysisResponse

@dataclass(frozen=True)
class ReviewEvidence:
    source_number: int
    paper_title: str
    chunk_id: int
    page_number: int
    text: str
    paper_id: int | None = None
    duplicate_of_chunk_id: int | None = None

def format_review_evidence(evidence: list[ReviewEvidence]) -> str:
    """审校与修正共用同一证据格式，保留资料编号和真实论文编号。"""
    parts: list[str] = []
    by_chunk_id = {item.chunk_id: item for item in evidence}

    for item in evidence:
        paper_label = (
            f"论文编号：{item.paper_id}\n"
            if item.paper_id is not None else ""
        )
        primary = by_chunk_id.get(item.duplicate_of_chunk_id)
        body = (
            f"正文同[资料{primary.source_number}]（文本块{primary.chunk_id}），完整原文已提供。"
            if primary is not None else f"原文：{item.text}"
        )
        parts.append(
            f"[资料{item.source_number}]\n"
            f"{paper_label}"
            f"论文标题：{item.paper_title}\n"
            f"页码：{item.page_number}\n"
            f"文本块编号：{item.chunk_id}\n"
            + body
        )

    return "\n\n".join(parts)


@dataclass(frozen=True)
class ReviewClaim:
    field: str
    claim: str
    source_numbers: list[int]
    evidence: list[ReviewEvidence]
    instruction: str = ""
    investigation_summary: str = ""

def load_review_evidence(
        db: Session,
        analysis: ResearchTaskAnalysisResponse,
        source_number: int,
) -> ReviewEvidence:

    if not 1 <= source_number <= len(analysis.sources):
        raise ValueError("资料编号超出范围")

    source = analysis.sources[source_number - 1]
    chunk = db.get(PaperChunk, source.chunk_id)

    if chunk is None:
        raise ValueError(
            f"资料{source_number}对应的文本块已经不存在"
        )

    document = db.get(PaperDocument, chunk.document_id)
    if document is None:
        raise ValueError(
            f"资料{source_number}对应的PDF记录已经不存在"
        )

    if (
        document.paper_id != source.paper_id
        or chunk.page_number != source.page_number
    ):
        raise ValueError(
            f"资料{source_number}与当前文本块信息不一致，"
            "请重新生成论文分析"
        )

    return ReviewEvidence(
        source_number=source_number,
        paper_title=source.paper_title,
        chunk_id=chunk.id,
        page_number=chunk.page_number,
        text=chunk.text,
        paper_id=document.paper_id,
    )

def prepare_review_claims(
    db: Session,
    analysis: ResearchTaskAnalysisResponse,
) -> list[ReviewClaim]:
    """将分析内容转换成待审校陈述与真实证据。"""

    fields = {
        "summary": [analysis.summary],
        "methods": analysis.methods,
        "experiments": analysis.experiments,
        "conclusions": analysis.conclusions,
        "limitations": analysis.limitations,
        "evidence_notes": getattr(analysis, "evidence_notes", []),
    }

    claims: list[ReviewClaim] = []
    evidence_cache: dict[int, ReviewEvidence] = {}

    for field_name, texts in fields.items():
        for text in texts:
            source_numbers = extract_source_numbers(
                text,
                source_count=len(analysis.sources),
            )

            evidence: list[ReviewEvidence] = []

            for number in source_numbers:
                if number not in evidence_cache:
                    evidence_cache[number] = load_review_evidence(
                        db=db,
                        analysis=analysis,
                        source_number=number,
                    )

                evidence.append(
                    evidence_cache[number]
                )

            claims.append(
                ReviewClaim(
                    field=field_name,
                    claim=text,
                    source_numbers=source_numbers,
                    evidence=evidence,
                )
            )

    return claims
