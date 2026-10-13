from dataclasses import dataclass
from sqlalchemy.orm import Session

import semantic_search
import query_rewriter

from llm_service import generate_text

@dataclass(frozen=True)
class RAGSource:
    paper_id: int
    paper_title: str
    chunk_id: int
    page_number: int
    score: float

@dataclass(frozen=True)
class RAGResult:
    question: str
    answer: str
    sources: list[RAGSource]

def answer_research_question(
        db: Session,
        task_id: int,
        question: str,
        top_k: int = 5,
) -> RAGResult:
    question = question.strip()

    if not question:
        raise ValueError("问题不能为空")

    if top_k < 1:
        raise ValueError("top_k 必须大于0")

    search_query = query_rewriter.rewrite_query_for_search(
        question
    )

    matches = semantic_search.search_task_chunks_semantic(
        db=db,
        task_id=task_id,
        question=search_query,
        top_k=top_k,
    )

    if not matches:
        raise ValueError(
            "没有找到可用的论文资料，请先生成论文文本块和向量"
        )

    context_parts: list[str] = []
    sources: list[RAGSource] = []

    for number, (chunk, paper, score) in enumerate(
            matches,
            start=1,
    ):
        source_label = f"[资料{number}]"

        context_parts.append(
            "\n".join(
                [
                    source_label,
                    f"论文标题：{paper.title}",
                    f"页码：{chunk.page_number}",
                    f"正文：{chunk.text}",
                ]
            )
        )

        sources.append(
            RAGSource(
                paper_id=paper.id,
                paper_title=paper.title,
                chunk_id=chunk.id,
                page_number=chunk.page_number,
                score=score,
            )
        )

    context = "\n\n".join(context_parts)

    system_prompt = (
        "你是一名严谨的科研助手。"
        "请只根据用户提供的论文资料回答问题。"
        "每个主要结论后面必须标注对应的资料编号，"
        "例如[资料1]或[资料2]。"
        "如果资料不足以回答问题，请明确说明资料不足。"
        "不得编造论文中没有出现的信息。"
    )

    user_prompt = (
        f"研究问题：\n{question}\n\n"
        f"可使用的论文资料：\n{context}\n\n"
        "请根据以上资料回答研究问题，"
        "并在相关结论后标注资料编号。"
    )

    answer = generate_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    return RAGResult(
        question=question,
        answer=answer,
        sources=sources,
    )