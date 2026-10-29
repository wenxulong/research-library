import re

from sqlalchemy.orm import Session

import research_library.repositories.task_crud as task_crud

from research_library.retrieval.embedding_service import EMBEDDING_MODEL, embed_texts
from research_library.db.model import Paper, PaperChunk
from research_library.retrieval.vector_utils import cosine_similarity
from research_library.retrieval.bm25_search import calculate_bm25_scores


SEMANTIC_WEIGHT = 0.85
KEYWORD_WEIGHT = 0.15
REFERENCE_PENALTY = 0.12
MAX_CHUNKS_PER_PAGE = 2
RRF_CONSTANT = 60
REFERENCE_SCORE_FACTOR = 0.8


def tokenize_text(text: str) -> set[str]:
    """把中英文文本转换成可用于关键词匹配的词语集合。"""

    lowered_text = text.lower()

    english_words = re.findall(
        r"[a-z0-9]+",
        lowered_text,
    )

    chinese_characters = re.findall(
        r"[\u4e00-\u9fff]",
        lowered_text,
    )

    chinese_text = "".join(chinese_characters)

    chinese_pairs = {
        chinese_text[index:index + 2]
        for index in range(len(chinese_text) - 1)
    }

    return set(english_words) | chinese_pairs


def calculate_keyword_overlap(
    question: str,
    chunk_text: str,
) -> float:
    """计算问题关键词在文本块中的覆盖比例。"""

    question_tokens = tokenize_text(question)

    if not question_tokens:
        return 0.0

    chunk_tokens = tokenize_text(chunk_text)

    common_tokens = question_tokens & chunk_tokens

    return len(common_tokens) / len(question_tokens)


def has_reference_heading(text: str) -> bool:
    """识别行首的参考文献标题，避免匹配正文中提到 references 的句子。"""
    return re.search(
        r"^\s*(?:\d+(?:\.\d+)*[.)]?\s+)?"
        r"(?:references|参考文献)\s*(?:$|\[\d+\])",
        text[:200],
        flags=re.IGNORECASE | re.MULTILINE,
    ) is not None


def looks_like_reference_chunk(text: str) -> bool:
    """判断文本块是否很像论文末尾的参考文献。"""

    lowered_text = text.lower()
    if has_reference_heading(text):
        return True

    # 文内引用数量不能区分正文和参考文献，改为检查行首条目结构。
    entries = re.findall(
        r"^\s*\[\d+\]\s+\S[^\n]*",
        text,
        flags=re.MULTILINE,
    )
    if len(entries) >= 3:
        return True
    return len(entries) >= 2 and (
        lowered_text.count("et al.") >= 2
        or lowered_text.count("doi:") >= 2
    )

def find_reference_pages(
    candidates: list[
        tuple[PaperChunk, Paper, object]
    ],
) -> set[tuple[int, int]]:
    """找出包含参考文献标题的论文页面。"""

    reference_pages: set[tuple[int, int]] = set()

    for chunk, paper, embedding in candidates:
        if has_reference_heading(chunk.text):
            page_key = (
                paper.id,
                chunk.page_number,
            )

            reference_pages.add(page_key)

    return reference_pages

def calculate_rerank_score(
    question: str,
    chunk_text: str,
    semantic_score: float,
    is_reference_page: bool = False,
) -> float:
    """综合语义、关键词和参考文献信息计算排序分数。"""

    keyword_score = calculate_keyword_overlap(
        question,
        chunk_text,
    )

    rerank_score = (
        semantic_score * SEMANTIC_WEIGHT
        + keyword_score * KEYWORD_WEIGHT
    )

    is_reference_chunk = looks_like_reference_chunk(
        chunk_text
    )

    if is_reference_chunk or is_reference_page:
        rerank_score -= REFERENCE_PENALTY

    return rerank_score

def select_diverse_matches(
    reranked_matches: list[
        tuple[PaperChunk, Paper, float, float]
    ],
    top_k: int,
) -> list[tuple[PaperChunk, Paper, float]]:
    """限制同一篇论文同一页返回的文本块数量。"""

    selected = []
    deferred = []
    page_counts: dict[tuple[int, int | None], int] = {}

    for chunk, paper, semantic_score, rerank_score in reranked_matches:
        page_key = (
            paper.id,
            getattr(chunk, "page_number", None),
        )

        current_count = page_counts.get(page_key, 0)

        if current_count >= MAX_CHUNKS_PER_PAGE:
            deferred.append(
                (
                    chunk,
                    paper,
                    semantic_score,
                )
            )
            continue

        selected.append(
            (
                chunk,
                paper,
                semantic_score,
            )
        )

        page_counts[page_key] = current_count + 1

        if len(selected) == top_k:
            return selected

    remaining_count = top_k - len(selected)

    if remaining_count > 0:
        selected.extend(
            deferred[:remaining_count]
        )

    return selected


def search_texts(
    question: str,
    texts: list[str],
    top_k: int = 3,
) -> list[tuple[str, float]]:
    """对一组普通文本执行简单向量搜索。"""

    if not question.strip():
        raise ValueError("问题不能为空")

    if top_k < 1:
        raise ValueError("top_k 必须大于 0")

    if not texts:
        return []

    vectors = embed_texts(
        [
            question,
            *texts,
        ]
    )

    question_vector = vectors[0]
    text_vectors = vectors[1:]

    matches = []

    for text, vector in zip(
        texts,
        text_vectors,
    ):
        score = cosine_similarity(
            question_vector,
            vector,
        )

        matches.append(
            (
                text,
                score,
            )
        )

    matches.sort(
        key=lambda item: item[1],
        reverse=True,
    )

    return matches[:top_k]


def fuse_recall_results(
    semantic_recall: list[tuple[PaperChunk, Paper, float]],
    bm25_recall: list[tuple[PaperChunk, Paper, float]],
) -> list[tuple[PaperChunk, Paper, float, float]]:
    """等权RRF融合两路排名；第三项仍保留原始语义相似度。"""
    matches_by_chunk_id = {}
    fusion_scores = {}
    for recalled_matches in (semantic_recall, bm25_recall):
        for rank, match in enumerate(recalled_matches, start=1):
            chunk, paper, semantic_score = match
            matches_by_chunk_id.setdefault(chunk.id, match)
            contribution = 0.5 / (RRF_CONSTANT + rank)
            fusion_scores[chunk.id] = fusion_scores.get(chunk.id, 0.0) + contribution

    return [
        (chunk, paper, semantic_score, fusion_scores[chunk_id])
        for chunk_id, (chunk, paper, semantic_score) in matches_by_chunk_id.items()
    ]


def search_task_chunks_semantic(
    db: Session,
    task_id: int,
    question: str,
    top_k: int = 3,
    paper_id: int | None = None,
) -> list[tuple[PaperChunk, Paper, float]]:
    """在同一任务论文范围内双路召回，按融合分数排序，返回原始语义分数。"""
    question = question.strip()
    if not question:
        raise ValueError("问题不能为空")
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")

    # 两路共享已向量化文本块集合；单篇限定必须在两路评分之前执行。
    candidates = task_crud.get_task_chunk_embeddings(db, task_id, EMBEDDING_MODEL)
    if paper_id is not None:
        candidates = [
            (chunk, paper, embedding)
            for chunk, paper, embedding in candidates
            if paper.id == paper_id
        ]
    if not candidates:
        return []

    reference_pages = find_reference_pages(candidates)
    question_vector = embed_texts([question])[0]
    all_matches = [
        (chunk, paper, cosine_similarity(question_vector, embedding.vector))
        for chunk, paper, embedding in candidates
    ]
    candidate_count = max(top_k * 5, 20)
    semantic_recall = sorted(
        all_matches, key=lambda item: item[2], reverse=True,
    )[:candidate_count]

    # BM25对全部限定候选独立评分，不能只处理向量一路已经召回的文本。
    bm25_scores = calculate_bm25_scores(
        question=question,
        texts=[chunk.text for chunk, paper, embedding in candidates],
    )
    bm25_indices = sorted(
        (index for index, score in enumerate(bm25_scores) if score > 0),
        key=lambda index: bm25_scores[index],
        reverse=True,
    )[:candidate_count]
    bm25_recall = [all_matches[index] for index in bm25_indices]

    fused_matches = fuse_recall_results(semantic_recall, bm25_recall)
    reranked_matches = []
    for chunk, paper, semantic_score, fusion_score in fused_matches:
        page_key = (paper.id, chunk.page_number)
        if page_key in reference_pages or looks_like_reference_chunk(chunk.text):
            # RRF分数较小，使用比例降权，不复用旧的固定减分值。
            fusion_score *= REFERENCE_SCORE_FACTOR
        reranked_matches.append((chunk, paper, semantic_score, fusion_score))

    reranked_matches.sort(key=lambda item: item[3], reverse=True)
    return select_diverse_matches(reranked_matches, top_k)


if __name__ == "__main__":
    results = search_texts(
        question="怎样通过向量搜索论文？",
        texts=[
            "使用文本向量和相似度检索相关论文内容。",
            "这篇文章讨论水稻育种方法。",
            "研究篮球运动员的训练计划。",
        ],
        top_k=2,
    )

    for text, score in results:
        print(
            f"相似度：{score:.3f}，内容：{text}"
        )
