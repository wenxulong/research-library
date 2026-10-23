import json
from dataclasses import dataclass, field
from research_library.core.citation_utils import extract_source_numbers

from sqlalchemy.orm import Session
from pydantic import ValidationError
from research_library.schemas import ResearchEntityResponse

import research_library.retrieval.query_rewriter as query_rewriter
import research_library.retrieval.semantic_search as semantic_search
import research_library.repositories.task_crud as task_crud

from research_library.core.llm_service import generate_text
from research_library.db.model import Paper, PaperChunk

ANALYSIS_QUERIES = [
    (
        "core methodology proposed algorithm "
        "system model theoretical framework"
    ),
    (
        "experimental setup datasets baselines "
        "evaluation metrics experimental results"
    ),
    (
        "main findings conclusions performance "
        "improvements contributions"
    ),
    (
        "limitations assumptions weaknesses "
        "future work"
    ),
]

@dataclass(frozen=True)
class AnalysisSource:
    paper_id: int
    paper_title: str
    chunk_id: int
    page_number: int
    score: float

@dataclass(frozen=True)
class PaperAnalysisResult:
    question: str
    summary: str
    methods: list[str]
    experiments: list[str]
    conclusions: list[str]
    limitations: list[str]
    sources: list[AnalysisSource]
    evidence_notes: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)

def collect_analysis_matches(
    db: Session,
    task_id: int,
    question: str,
    top_k_per_query: int = 3,
) -> list[tuple[PaperChunk, Paper, float]]:
    """逐篇检索研究问题和各分析维度，合并并去除重复文本块。"""

    paper_links = task_crud.get_task_paper_links(db, task_id)

    if not paper_links:
        raise ValueError("当前研究任务还没有关联论文")

    rewritten_query = query_rewriter.rewrite_query_for_search(question)
    search_queries = [rewritten_query, *ANALYSIS_QUERIES]
    matches_by_chunk_id: dict[int, tuple[PaperChunk, Paper, float]] = {}

    for link in paper_links:
        for search_query in search_queries:
            matches = semantic_search.search_task_chunks_semantic(
                db=db,
                task_id=task_id,
                question=search_query,
                top_k=top_k_per_query,
                paper_id=link.paper_id,
            )

            if not matches:
                raise ValueError(
                    f"论文 {link.paper_id} 没有当前模型的可检索向量，"
                    "请先完成文本分块和向量索引"
                )

            for chunk, paper, score in matches:
                saved_match = matches_by_chunk_id.get(chunk.id)
                if saved_match is None or score > saved_match[2]:
                    matches_by_chunk_id[chunk.id] = (chunk, paper, score)

    return list(matches_by_chunk_id.values())


def build_analysis_context(
    matches: list[
        tuple[PaperChunk, Paper, float]
    ],
) -> tuple[str, list[AnalysisSource]]:
    """将检索结果转换成提示词上下文和来源列表。"""

    context_parts: list[str] = []
    sources: list[AnalysisSource] = []

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
            AnalysisSource(
                paper_id=paper.id,
                paper_title=paper.title,
                chunk_id=chunk.id,
                page_number=chunk.page_number,
                score=score,
            )
        )

    context = "\n\n".join(context_parts)

    return context, sources


def remove_json_code_block(text: str) -> str:
    """删除大模型可能添加的JSON代码块标记。"""

    text = text.strip()

    if not text.startswith("```"):
        return text

    lines = text.splitlines()

    if lines and lines[0].startswith("```"):
        lines = lines[1:]

    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()


def parse_analysis_response(
    response_text: str,
) -> dict:
    """解析并检查大模型返回的结构化分析结果。"""

    cleaned_text = remove_json_code_block(
        response_text
    )

    try:
        data = json.loads(cleaned_text)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "论文分析Agent没有返回正确的JSON"
        ) from error

    if not isinstance(data, dict):
        raise RuntimeError(
            "论文分析Agent返回的数据不是JSON对象"
        )

    summary = data.get("summary")

    if not isinstance(summary, str):
        raise RuntimeError(
            "论文分析结果缺少summary"
        )

    # 旧分析可能没有该字段；保持读取兼容。
    data.setdefault("evidence_notes", [])

    list_fields = [
        "methods",
        "experiments",
        "conclusions",
        "limitations",
        "evidence_notes",
    ]

    for field_name in list_fields:
        field_value = data.get(field_name)

        if not isinstance(field_value, list):
            raise RuntimeError(
                f"论文分析结果中的{field_name}"
                "必须是列表"
            )

        if not all(
            isinstance(item, str)
            for item in field_value
        ):
            raise RuntimeError(
                f"论文分析结果中的{field_name}"
                "只能包含字符串"
            )

    # 实体是对象列表，与上面的陈述字符串列表分别解析。
    entities = data.setdefault("entities", [])
    if not isinstance(entities, list):
        raise RuntimeError("论文分析结果中的entities必须是列表")
    try:
        data["entities"] = [
            ResearchEntityResponse.model_validate(item).model_dump()
            for item in entities
        ]
    except ValidationError as error:
        raise RuntimeError("论文分析结果中的实体格式不正确") from error

    return data


def validate_analysis_citations(
    analysis_data: dict,
    source_count: int,
) -> None:
    """检查所有分析字段中的资料编号是否存在；不判断结论真实性。"""

    fields = {
        "summary": [analysis_data["summary"]],
        "methods": analysis_data["methods"],
        "experiments": analysis_data["experiments"],
        "conclusions": analysis_data["conclusions"],
        "limitations": analysis_data["limitations"],
        "evidence_notes": analysis_data.get("evidence_notes", []),
    }

    for field_name, texts in fields.items():
        for text in texts:
            try:
                extract_source_numbers(text, source_count)
            except ValueError as error:
                raise RuntimeError(
                    f"论文分析结果中的{field_name}{error}"
                ) from error

    # 这里是分析资料编号，不是数据库的论文编号。
    for entity in analysis_data.get("entities", []):
        for number in entity["source_numbers"]:
            if not 1 <= number <= source_count:
                raise RuntimeError(f"实体引用了不存在的资料编号：{number}")


def analyze_research_task(
    db: Session,
    task_id: int,
    question: str,
) -> PaperAnalysisResult:
    """检索研究任务中的论文并生成结构化分析。"""

    question = question.strip()

    if not question:
        raise ValueError("研究问题不能为空")

    matches = collect_analysis_matches(
        db=db,
        task_id=task_id,
        question=question,
    )

    if not matches:
        raise ValueError(
            "没有找到可用于论文分析的资料"
        )

    context, sources = build_analysis_context(
        matches
    )

    system_prompt = (
        "你是科研工作流中的论文分析Agent。"
        "请只根据用户提供的论文资料进行分析。"
        "不得使用资料以外的信息，不得编造。"
        "每条具体结论必须标注资料编号，"
        "例如[资料1]。"
        "每个编号必须来自提供的资料，多个引用请分别写成[资料1][资料2]。"
        "如果资料不足，请明确写资料不足。"
        "必须区分本文提出的方法与相关工作，不能把已有方法的不足写成本文的局限性。"
        "limitations只描述本文的假设、评估范围或作者明确指出的限制；"
        "evidence_notes记录当前引用原文不足、未检索到的信息或引文核对说明，不能放入limitations。"
        "未检索到的信息只能写当前资料未提供，不能断言整篇论文不存在。"
        "conclusions只描述本文主要发现，不要把未来工作当作已经得到的结论。"
        "同时从提供的论文资料中抽取有明确名称的专业实体。"
        "实体可以包括算法、模型、数据集、评价指标、方法或其他专业概念。"
        "每条实体使用canonical_name作为统一名称，"
        "aliases只填写原文明确出现的别名、缩写或全称，不要猜测。"
        "source_numbers填写能够支持该实体的资料编号，不是论文编号。"
        "同一实体尽量合并，不要重复返回；未出现的实体不要补充。"
        "你必须只返回一个合法JSON对象，"
        "不要使用Markdown代码块，不要添加解释文字。"
    )

    user_prompt = (
        f"研究问题：\n{question}\n\n"
        f"论文资料：\n{context}\n\n"
        "请返回以下JSON结构：\n"
        "{\n"
        '  "summary": "论文整体概述",\n'
        '  "methods": ["核心方法1", "核心方法2"],\n'
        '  "experiments": ["实验信息1", "实验信息2"],\n'
        '  "conclusions": ["主要结论1", "主要结论2"],\n'
        '  "limitations": ["论文自身的局限性1"],\n'
        '  "evidence_notes": ["当前资料不足说明1"],\n'
        '  "entities": [\n'
        '    {\n'
        '      "canonical_name": "原文中的实体名称",\n'
        '      "entity_type": "algorithm",\n'
        '      "aliases": [],\n'
        '      "source_numbers": [1]\n'
        '    }\n'
        '  ]\n'
        "}\n"
        "methods、experiments、conclusions、limitations和evidence_notes"
        "数组中的每项必须是字符串，并在有资料支持的内容后标注资料编号。"
        "entities数组中的每项必须是上述格式的对象；没有可提取实体时返回空数组。"
    )

    response_text = generate_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    analysis_data = parse_analysis_response(
        response_text
    )

    validate_analysis_citations(
        analysis_data,
        source_count=len(sources),
    )

    return PaperAnalysisResult(
        question=question,
        summary=analysis_data["summary"],
        methods=analysis_data["methods"],
        experiments=analysis_data["experiments"],
        conclusions=analysis_data["conclusions"],
        limitations=analysis_data["limitations"],
        evidence_notes=analysis_data["evidence_notes"],
        sources=sources,
        entities=analysis_data["entities"],
    )
