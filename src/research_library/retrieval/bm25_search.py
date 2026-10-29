"""面向任务论文文本块的轻量BM25评分，不依赖外部搜索服务。"""
import re
from collections import Counter
from math import log


def tokenize_for_bm25(text: str) -> list[str]:
    """保留词频、英文复合术语及连续中文内的双字组合。"""
    parts = re.findall(
        r"[a-z0-9]+(?:[-_][a-z0-9]+)*|[\u4e00-\u9fff]+",
        text.lower(),
    )
    tokens = []
    for part in parts:
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(
                    part[index:index + 2]
                    for index in range(len(part) - 1)
                )
        else:
            tokens.append(part)
            if "-" in part or "_" in part:
                tokens.extend(re.split(r"[-_]", part))
    return tokens


def calculate_bm25_scores(question: str, texts: list[str]) -> list[float]:
    """采用正值IDF形式评分；分数位置与输入texts严格对应。"""
    query_terms = set(tokenize_for_bm25(question))
    documents = [tokenize_for_bm25(text) for text in texts]
    scores = [0.0] * len(documents)
    if not query_terms or not documents:
        return scores

    average_length = sum(len(document) for document in documents) / len(documents)
    if average_length == 0:
        return scores

    term_counts = [Counter(document) for document in documents]
    document_frequency = Counter()
    for counts in term_counts:
        document_frequency.update(counts.keys())

    document_count = len(documents)
    k1 = 1.5
    b = 0.75
    for index, counts in enumerate(term_counts):
        document_length = len(documents[index])
        score = 0.0
        for term in query_terms:
            frequency = counts.get(term, 0)
            if frequency == 0:
                continue
            containing_count = document_frequency[term]
            inverse_frequency = log(
                1 + (document_count - containing_count + 0.5)
                / (containing_count + 0.5)
            )
            length_adjustment = 1 - b + b * document_length / average_length
            score += (
                inverse_frequency * frequency * (k1 + 1)
                / (frequency + k1 * length_adjustment)
            )
        scores[index] = score
    return scores
