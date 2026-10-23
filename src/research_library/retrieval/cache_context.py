import hashlib
import json
import re

from sqlalchemy.orm import Session

from research_library.retrieval.cache_models import CacheEntry, QuestionFeatures
from research_library.core.config import settings

import research_library.repositories.task_crud as task_crud

from pydantic import ValidationError

from research_library.schemas import ResearchEntityResponse, ResearchTaskAnalysisResponse

def load_task_entities(
    db: Session,
    task_id: int,
) -> list[ResearchEntityResponse]:
    """读取研究任务已保存的实体目录。"""

    task = task_crud.get_task_by_id(
        db,
        task_id,
    )

    if task is None:
        return []

    if not task.result:
        return []

    if task.status == "stale":
        return []

    try:
        analysis = (
            ResearchTaskAnalysisResponse
            .model_validate_json(task.result)
        )

    except ValidationError:
        return []

    return analysis.entities

def match_entities(
    question: str,
    catalog: list[ResearchEntityResponse],
) -> list[str]:
    """根据实体目录，识别问题中的实体名称和别名。"""

    text = question.casefold()

    matched = set()
    for entity in catalog:
        names = [
            entity.canonical_name,
            *entity.aliases,
        ]

        for alias in names:
            name = alias.strip().casefold()

            if not name:
                continue

            pattern = re.escape(name)

            if name.isascii():
                pattern = (
                        r"(?<![a-z0-9-])"
                        + pattern
                        + r"(?![a-z0-9-])"
                )

            if re.search(pattern, text):
                entity_key = (
                    f"{entity.entity_type}:"
                    f"{entity.canonical_name.casefold()}"
                )

                matched.add(entity_key)

                break

    return sorted(matched)

def extract_features(
    question: str,
    catalog: list[ResearchEntityResponse],
) -> QuestionFeatures:
    """提取问题的意图、实体、数字和否定条件。"""
    text = question.casefold()

    patterns = [
        ("comparison", ["比较", "对比", "区别", "compare", "difference"]),
        ("limitation", ["局限", "不足", "缺点", "limitation", "weakness"]),
        ("metrics", ["指标", "metric"]),
        ("experiment", ["实验", "数据集", "experiment", "dataset"]),
        ("method", ["如何", "怎么", "机制", "原理", "how", "mechanism"]),
        ("definition", ["是什么", "定义", "what is", "definition"]),
    ]

    intent = "other"

    for name, keywords in patterns:
        if any(word in text for word in keywords):
            intent = name
            break

    entities = match_entities(
        question,
        catalog,
    )

    numbers = sorted(
        set(
            re.findall(
                r"\d+(?:\.\d+)?",
                text,
            )
        )
    )

    negative = any(
        word in text
        for word in [
            "不能",
            "不可以",
            "不支持",
            "不适用",
            "无法",
            "cannot",
            "not ",
        ]
    )

    return QuestionFeatures(
        intent=intent,
        entities=entities,
        numbers=numbers,
        negative=negative,
    )

def can_reuse_features(
    cached_features: QuestionFeatures,
    current_features: QuestionFeatures,
) -> bool:
    """检查缓存问题与当前问题的基础知识条件是否一致。"""

    if (
            not cached_features.entities
            or not current_features.entities
    ):
        return False

    if (
            cached_features.intent == "other"
            or current_features.intent == "other"
    ):
        return False

    return (
            cached_features.intent == current_features.intent
            and cached_features.entities == current_features.entities
            and cached_features.numbers == current_features.numbers
            and cached_features.negative == current_features.negative
    )

def build_context(
    db: Session,
    task_id: int,
    top_k: int,
) -> tuple[str, dict, list[ResearchEntityResponse]]:
    """计算缓存范围，并返回来源索引和实体目录。"""
    links = task_crud.get_task_paper_links(db, task_id)

    chunks = task_crud.get_task_chunks(db, task_id)

    catalog = load_task_entities(db, task_id)

    scope = {
        "version": "knowledge-cache-v2",
        "task_id": task_id,
        "top_k": top_k,
        "models": [
            settings.embedding_model,
            settings.ollama_base_url,
            settings.deepseek_model,
            settings.deepseek_base_url,
        ],
        "papers": sorted(
            (link.paper_id, link.paper.title)
            for link in links
        ),
        "chunks": [
            (
                chunk.id,
                chunk.document_id,
                chunk.page_number,
                chunk.text,
            )
            for chunk, paper in chunks
        ],
        "entities": [
            entity.model_dump()
            for entity in catalog
        ],
    }

    scope_json = json.dumps(
        scope,
        ensure_ascii=False,
        sort_keys=True,
    )

    digest = hashlib.sha256(
        scope_json.encode("utf-8")
    ).hexdigest()

    source_map = {
        chunk.id: (
            paper.id,
            chunk.page_number,
            paper.title,
        )
        for chunk, paper in chunks
    }

    return digest, source_map, catalog

def has_valid_sources(
    entry: CacheEntry,
    source_map: dict[int, tuple[int, int, str]],
) -> bool:
    """检查缓存答案的所有引用是否对应当前任务的资料。"""

    if not entry.sources:
        return False
    for source in entry.sources:
        current_source = source_map.get(source.chunk_id)

        if current_source is None:
            return False

        cached_source = (
            source.paper_id,
            source.page_number,
            source.paper_title,
        )

        if current_source != cached_source:
            return False

    return True
