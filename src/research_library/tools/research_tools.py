from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from research_library.db.model import Paper, PaperChunk, ResearchTaskPaper
from research_library.repositories.chunk_crud import get_neighbor_chunks
from research_library.retrieval.semantic_search import search_task_chunks_semantic

class SearchEvidenceArguments(BaseModel):
    """搜索原文工具的参数。"""

    query: str = Field(
        min_length=1,
        description="需要查找的内容，应围绕陈述和证据缺口组织查询。",
    )
    top_k: int = Field(
        default=3,
        ge=1,
        le=5,
        description="返回的原文数量，默认3条，最多5条。",
    )


class NeighborChunksArguments(BaseModel):
    """读取相邻原文工具的参数。"""

    chunk_id: int = Field(
        ge=1,
        description="需要查看上下文的文本块ID，不是资料编号。",
    )

RESEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_paper_evidence",
            "description": (
                "在当前研究任务关联的论文中进行语义检索，"
                "查找支持或反对陈述的原文、实验条件和限制。"
                "返回论文信息、文本块ID、页码和原文。"
            ),
            "parameters": SearchEvidenceArguments.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_neighbor_chunks",
            "description": (
                "读取指定文本块及其前后各一个文本块。"
                "当原文缺少上下文、条件或解释时使用。"
                "只能读取当前研究任务关联论文中的文本块。"
            ),
            "parameters": NeighborChunksArguments.model_json_schema(),
        },
    },
]

def format_evidence(
    chunk: PaperChunk,
    paper: Paper,
    score: float | None = None,
) -> dict:
    """把数据库对象转换成工具可返回的证据信息。"""

    return {
        "chunk_id": chunk.id,
        "paper_id": paper.id,
        "paper_title": paper.title,
        "page_number": chunk.page_number,
        "text": chunk.text,
        "score": float(score) if score is not None else None,
    }

def search_paper_evidence(
    db: Session,
    task_id: int,
    query: str,
    top_k: int = 3,
) -> list[dict]:
    """在当前研究任务关联的论文中搜索证据。"""

    matches = search_task_chunks_semantic(
        db=db,
        task_id=task_id,
        question=query,
        top_k=top_k,
    )

    evidence = []

    for chunk, paper, score in matches:
        evidence.append(
            format_evidence(
                chunk=chunk,
                paper=paper,
                score=score,
            )
        )

    return evidence

def read_neighbor_chunks(
    db: Session,
    task_id: int,
    chunk_id: int,
) -> list[dict]:
    """读取当前任务中某个文本块及其相邻原文。"""

    chunk = db.get(PaperChunk, chunk_id)

    if chunk is None or chunk.document is None:
        raise ValueError("文本块或所属PDF记录不存在")

    paper = chunk.document.paper

    linked_id = db.scalar(
        select(ResearchTaskPaper.id).where(
            ResearchTaskPaper.task_id == task_id,
            ResearchTaskPaper.paper_id == paper.id,
        )
    )

    if linked_id is None:
        raise ValueError("这个文本块不属于当前研究任务的论文")

    neighbors = get_neighbor_chunks(
        db=db,
        chunk_id=chunk_id,
        before=1,
        after=1,
    )

    return [
        format_evidence(
            chunk=neighbor,
            paper=paper,
        )
        for neighbor in neighbors
    ]

def execute_research_tool(
    db: Session,
    task_id: int,
    name: str,
    arguments: dict,
) -> list[dict]:
    """根据工具名称校验参数，并执行对应函数。"""

    if name == "search_paper_evidence":
        parsed = SearchEvidenceArguments.model_validate(
            arguments,
            strict=True,
        )

        return search_paper_evidence(
            db=db,
            task_id=task_id,
            query=parsed.query,
            top_k=parsed.top_k,
        )

    if name == "read_neighbor_chunks":
        parsed = NeighborChunksArguments.model_validate(
            arguments,
            strict=True,
        )

        return read_neighbor_chunks(
            db=db,
            task_id=task_id,
            chunk_id=parsed.chunk_id,
        )

    raise ValueError(f"未知的研究工具：{name}")
