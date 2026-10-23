import hashlib
import time

from dataclasses import asdict, dataclass
from sqlalchemy.orm import Session

from urllib.error import URLError

import research_library.retrieval.embedding_service as embedding_service

import research_library.retrieval.query_rewriter as query_rewriter
import research_library.retrieval.semantic_search as semantic_search
import research_library.retrieval.semantic_cache as semantic_cache

from research_library.retrieval.cache_context import build_context, can_reuse_features, extract_features, has_valid_sources
from research_library.retrieval.cache_models import CacheEntry
from research_library.core.llm_service import generate_text
from research_library.schemas import RAGSourceResponse

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

def _generate_research_answer(
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

def answer_research_question(
    db: Session,
    task_id: int,
    question: str,
    top_k: int = 5,
) -> RAGResult:
    """依次尝试完全匹配、语义匹配和原来的RAG生成。"""

    question = question.strip()

    if not question:
        raise ValueError("问题不能为空")

    if top_k < 1:
        raise ValueError("top_k 必须大于0")

    scope, source_map, catalog = build_context(
        db,
        task_id,
        top_k,
    )

    entry_id = hashlib.sha256(
        question.encode("utf-8")
    ).hexdigest()

    cached = semantic_cache.get_cache_entry(
        scope,
        entry_id,
    )

    if (
        cached is not None
        and has_valid_sources(cached, source_map)
    ):
        print("[问答缓存] 完全匹配命中")

        return RAGResult(
            question=question,
            answer=cached.answer,
            sources=[
                RAGSource(**source.model_dump())
                for source in cached.sources
            ],
        )

    features = extract_features(question, catalog)

    question_vector = None

    if features.entities and features.intent != "other":
        try:
            question_vector = embedding_service.embed_texts(
                [question]
            )[0]
        except (URLError, ValueError):
            print("[问答缓存] 问题向量生成失败，跳过语义缓存")

    if question_vector is not None:
        candidates = semantic_cache.find_semantic_entries(
            scope,
            question_vector,
        )

        for candidate, score in candidates:
            if not can_reuse_features(
                candidate.features,
                features,
            ):
                continue

            if not has_valid_sources(
                candidate,
                source_map,
            ):
                continue

            print(
                "[问答缓存] 语义命中，"
                f"相似度={score:.4f}，"
                f"原问题={candidate.question}"
            )

            return RAGResult(
                question=question,
                answer=candidate.answer,
                sources=[
                    RAGSource(**source.model_dump())
                    for source in candidate.sources
                ],
            )

    print("[问答缓存] 未命中，执行原来的RAG流程")

    started = time.perf_counter()

    result = _generate_research_answer(
        db=db,
        task_id=task_id,
        question=question,
        top_k=top_k,
    )

    generation_seconds = time.perf_counter() - started

    created_at = time.time()

    entry = CacheEntry(
        id=entry_id,
        question=question,
        answer=result.answer,
        sources=[
            RAGSourceResponse(**asdict(source))
            for source in result.sources
        ],
        features=features,
        created_at=created_at,
        expires_at=(
            created_at
            + semantic_cache.CACHE_TTL_SECONDS
        ),
        generation_seconds=generation_seconds,
    )

    saved = semantic_cache.save_cache_entry(
        scope,
        entry,
    )

    print("[问答缓存] 答案保存成功：", saved)

    if saved and question_vector is not None:
        vector_saved = semantic_cache.save_cache_vector(
            scope,
            entry.id,
            question_vector,
        )

        print("[问答缓存] 向量保存成功：", vector_saved)

    return result
