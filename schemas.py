"""定义 API 请求和响应使用的 Pydantic 数据模型。"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from datetime import datetime

class PaperCreate(BaseModel):
    """校验创建或修改论文时提交的数据。"""
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=1000, le=9999)
    notes: str = Field(default="", max_length=1000)


class PaperResponse(BaseModel):
    """定义论文接口的响应结构。"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    author: str
    year: int
    notes: str

class ResearchTaskCreate(BaseModel):
    """校验创建研究任务时提交的数据。"""
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)


class ResearchTaskStatusUpdate(BaseModel):
    """校验研究任务状态及结果的更新数据。"""
    status: Literal["pending", "running", "completed", "failed"]
    result: str | None = Field(default=None, max_length=10000)


class ResearchTaskResponse(BaseModel):
    """定义研究任务接口的响应结构。"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    question: str
    status: str
    result: str | None

class ResearchTaskPaperCreate(BaseModel):
    """校验任务与论文关联的备注数据。"""
    note: str = Field(default="", max_length=500)

class ResearchTaskPaperResponse(BaseModel):
    """定义任务与论文关联接口的响应结构。"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    paper_id: int
    note: str
    paper: PaperResponse

class PaperDocumentResponse(BaseModel):
    """定义 PDF 文件信息接口的响应结构。"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    paper_id: int
    original_name: str
    content_type: str
    file_size: int
    uploaded_at: datetime

class PaperPageResponse(BaseModel):
    """定义 PDF 页面正文接口的响应结构。"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    page_number: int
    text: str
    char_count: int

class PaperChunkResponse(BaseModel):
    """定义文本块接口返回的数据格式。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    page_number: int
    chunk_index: int
    text: str
    char_count: int

class ResearchTaskChunkMatch(BaseModel):
    """表示研究任务中找到的一条论文正文。"""

    paper_id: int
    paper_title: str
    chunk_id: int
    page_number: int
    chunk_index: int
    text: str

class ResearchTaskSemanticChunkMatch(ResearchTaskChunkMatch):
    """语义搜索结果：在原有文本块信息上增加相似度。"""

    score: float

class PaperEmbeddingIndexResponse(BaseModel):
    paper_id: int
    document_id: int
    model_name: str
    total_count: int
    indexed_count: int

class RAGQuestionRequest(BaseModel):
    """校验用户提交的RAG问题。"""
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)

class RAGSourceResponse(BaseModel):
    """表示RAG回答使用的一条论文来源。"""

    paper_id: int
    paper_title: str
    chunk_id: int
    page_number: int
    score: float

class RAGAnswerResponse(BaseModel):
    """定义RAG问答接口返回的数据。"""

    question: str
    answer: str
    sources: list[RAGSourceResponse]

class PaperPrepareResponse(BaseModel):
    """定义论文一键处理接口的返回格式。"""

    paper_id: int
    document_id: int
    model_name: str
    page_count: int
    chunk_count: int
    indexed_count: int

class AnalysisSourceResponse(BaseModel):
    """定义论文分析所使用的一条资料来源。"""

    paper_id: int
    paper_title: str
    chunk_id: int
    page_number: int
    score: float

class ResearchEntityResponse(BaseModel):
    """保存从论文资料中抽取的实体名称、别名和支持来源。"""

    canonical_name: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    source_numbers: list[int] = Field(min_length=1)


class ResearchTaskAnalysisResponse(BaseModel):
    """定义研究任务论文分析接口的返回格式。"""

    task_id: int
    question: str
    summary: str
    methods: list[str]
    experiments: list[str]
    conclusions: list[str]
    limitations: list[str]
    evidence_notes: list[str] = Field(default_factory=list)
    sources: list[AnalysisSourceResponse]
    entities: list[ResearchEntityResponse] = Field(default_factory=list)

class ReviewItemResponse(BaseModel):
    """定义一条分析陈述的审校结果。"""

    field: str
    claim: str
    verdict: Literal[
        "supported",
        "partially_supported",
        "insufficient_evidence",
        "contradicted",
    ]
    reason: str
    source_numbers: list[int]


class ResearchTaskReviewResponse(BaseModel):
    """定义研究任务的完整审校结果。"""

    task_id: int
    items: list[ReviewItemResponse]

class AnalysisRevisionResponse(BaseModel):
    """表示一条分析陈述的修订建议。"""

    field: str
    original_claim: str
    revised_claim: str
    reason: str
    source_numbers: list[int]

class ResearchTaskRevisionResponse(BaseModel):
    """表示一个研究任务的批量修订建议。"""

    task_id: int
    items: list[AnalysisRevisionResponse]
    skipped_items: list[ReviewItemResponse]

class AnalysisFieldCheckResponse(BaseModel):
    """表示陈述是否适合当前分析字段。"""

    verdict: Literal[
        "appropriate",
        "misplaced",
        "mixed",
        "uncertain",
    ]
    reason: str

class FieldCheckResponse(BaseModel):
    """表示陈述所属字段的分类检查结果。"""

    verdict: Literal[
        "appropriate",
        "misplaced",
        "mixed",
        "uncertain",
    ]
    reason: str
    suggested_field: Literal[
        "summary",
        "methods",
        "experiments",
        "conclusions",
        "limitations",
        "evidence_notes",
    ] | None = None

class AnalysisSplitItemResponse(BaseModel):
    """一条拆分陈述及其引用。"""

    field: Literal["summary", "methods", "experiments", "conclusions", "limitations", "evidence_notes"]
    claim: str
    source_numbers: list[int] = Field(default_factory=list)


class AnalysisSplitResponse(BaseModel):
    """保留原文本的拆分建议，不直接修改原分析。"""

    source_field: str
    source_claim: str
    reason: str
    items: list[AnalysisSplitItemResponse] = Field(min_length=2, max_length=8)


class CheckedSplitItemResponse(BaseModel):
    """拆分条目的证据与分类检查；ready只表示两种模型检查均通过。"""

    item: AnalysisSplitItemResponse
    source_field: str | None = None
    review: ReviewItemResponse
    field_check: FieldCheckResponse
    ready: bool


class CheckedRevisionItemResponse(BaseModel):
    """表示一条修改建议及其再次审校结果。"""

    revision: AnalysisRevisionResponse
    review: ReviewItemResponse
    field_check: FieldCheckResponse
    split_result: AnalysisSplitResponse | None = None
    split_checks: list[CheckedSplitItemResponse] = Field(default_factory=list)
    field_move: CheckedSplitItemResponse | None = None


class ResearchTaskCheckedRevisionResponse(BaseModel):
    """表示整个任务的修订及再次审校结果。"""

    task_id: int
    items: list[CheckedRevisionItemResponse]
    skipped_items: list[ReviewItemResponse]

class EvidenceNoteAdoptRequest(BaseModel):
    """提交原分析和准备采纳的修订建议。"""

    base_analysis: ResearchTaskAnalysisResponse
    revision: AnalysisRevisionResponse

class EvidenceNoteAdoptResponse(BaseModel):
    """返回采纳后的分析和对应的版本记录编号。"""

    task_id: int
    version_id: int
    analysis: ResearchTaskAnalysisResponse

class AnalysisVersionSummaryResponse(BaseModel):
    """版本列表中的一条简要记录。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    reason: str
    created_at: datetime


class AnalysisVersionDetailResponse(BaseModel):
    """一次修改前后的完整分析及原审校结果。"""

    id: int
    task_id: int
    reason: str
    created_at: datetime

    previous_analysis: ResearchTaskAnalysisResponse
    updated_analysis: ResearchTaskAnalysisResponse
    previous_review: ResearchTaskReviewResponse | None

class AnalysisRestoreRequest(BaseModel):
    """提交用户操作时看到的当前分析。"""

    base_analysis: ResearchTaskAnalysisResponse


class AnalysisRestoreResponse(BaseModel):
    """返回恢复后的分析和本次恢复产生的历史记录编号。"""

    task_id: int
    restored_from_version_id: int
    version_id: int
    analysis: ResearchTaskAnalysisResponse

class ComparisonContextResponse(BaseModel):
    """返回按论文整理的对比原文资料。"""

    task_id: int
    question: str
    paper_ids: list[int]
    source_numbers: list[int]
    context: str

class ComparisonStatementResponse(BaseModel):
    """报告中的一条对比陈述及其引用编号。"""

    text: str = Field(min_length=1)
    source_numbers: list[int] = Field(default_factory=list)

class ComparisonSourceResponse(AnalysisSourceResponse):
    """报告资料来源：保留资料编号，允许相邻补证没有相似度分数。"""

    source_number: int = Field(ge=1)
    score: float | None = None

class ComparisonReportResponse(BaseModel):
    """论文对比报告的返回格式。"""

    task_id: int
    question: str
    paper_ids: list[int]

    method_comparisons: list[ComparisonStatementResponse]
    experiment_comparisons: list[ComparisonStatementResponse]
    conclusion_comparisons: list[ComparisonStatementResponse]
    evidence_notes: list[ComparisonStatementResponse]

    sources: list[ComparisonSourceResponse]

class RejectedComparisonRevisionResponse(BaseModel):
    """记录未通过复审、未被采用的报告修订建议。"""

    revision: AnalysisRevisionResponse
    review: ReviewItemResponse


class CheckedComparisonReportResponse(BaseModel):
    """返回报告、修正前后的审校结果与是否通过；未通过不能当作已核验。"""

    task_id: int
    report: ComparisonReportResponse
    initial_review: ResearchTaskReviewResponse
    review: ResearchTaskReviewResponse
    passed: bool
    correction_rounds: int = Field(ge=0, le=2)
    rejected_revisions: list[RejectedComparisonRevisionResponse] = Field(
        default_factory=list,
    )

class SavedComparisonReportResponse(BaseModel):
    """定义已保存对比报告的接口返回格式。"""

    id: int
    task_id: int
    saved_at: datetime
    stale: bool
    checked_report: CheckedComparisonReportResponse

class ReportJobResponse(BaseModel):
    """返回报告作业的状态、时间、结果和错误信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    status: Literal["queued", "running", "succeeded", "failed"]
    stage: str

    result: CheckedComparisonReportResponse | None
    error_message: str | None

    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

class ReportJobSummaryResponse(BaseModel):
    """定义报告作业历史列表中每一条记录的格式。"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int

    status: Literal[
        "queued",
        "running",
        "succeeded",
        "failed",
    ]

    stage: str
    error_message: str | None

    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

class PaperPreparationJobResponse(BaseModel):
    """返回论文准备作业的状态、阶段和结果。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    paper_id: int
    document_id: int

    status: Literal["queued", "running", "succeeded", "failed"]
    stage: str

    result: PaperPrepareResponse | None
    error_message: str | None

    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

class PaperPreparationJobSummaryResponse(BaseModel):
    """定义论文准备作业历史中每条记录的返回格式。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    paper_id: int
    document_id: int

    status: Literal[
        "queued",
        "running",
        "succeeded",
        "failed",
    ]

    stage: str
    error_message: str | None

    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

class ResearchQuestionCandidate(BaseModel):
    """表示根据当前论文资料提出的候选研究问题。"""

    observation: str = Field(min_length=1)

    research_question: str = Field(min_length=1)

    research_value: str = Field(min_length=1)

    source_numbers: list[int] = Field(min_length=1)

    verification_plan: str = Field(min_length=1)

    uncertainties: list[str] = Field(default_factory=list)

    evidence_review: ReviewItemResponse | None = None

class ResearchQuestionsResponse(BaseModel):
    """返回一份报告所产生的候选研究问题。"""

    task_id: int

    report_job_id: int

    candidates: list[ResearchQuestionCandidate] = Field(
        min_length=1,
        max_length=3,
    )

    sources: list[ComparisonSourceResponse]

class SavedResearchQuestionsResponse(ResearchQuestionsResponse):
    """返回已保存的候选问题及选择结果。"""

    model_config = ConfigDict(from_attributes=True)

    id: int

    selected_candidate_number: int | None

    selected_question: str | None

    created_at: datetime

class ResearchQuestionSelectRequest(BaseModel):
    """用户选择一个候选问题，并可修改问题表述。"""

    candidate_number: int = Field(ge=1)

    research_question: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
    )

class ResearchConditions(BaseModel):
    """用户实际具备的研究与实验条件。"""

    equipment: str = Field(
        min_length=1,
    )

    data_resources: str = Field(
        min_length=1,
    )

    budget: str = Field(
        min_length=1,
    )

    time_budget: str = Field(
        min_length=1,
    )

    existing_implementation: str = ""

class ResearchPlanContent(BaseModel):
    """研究方案与待执行的实验计划。"""

    objective: str = Field(min_length=1)

    hypothesis: str = Field(min_length=1)

    method_steps: list[str] = Field(min_length=1)

    baselines: list[str] = Field(min_length=1)

    data_plan: str = Field(min_length=1)

    metrics: list[str] = Field(min_length=1)

    main_experiments: list[str] = Field(min_length=1)

    ablation_experiments: list[str] = Field(default_factory=list)

    execution_steps: list[str] = Field(min_length=1)

    result_columns: list[str] = Field(min_length=1)

    risks: list[str] = Field(min_length=1)

class ResearchPlanResponse(BaseModel):
    """保存计划内容，以及生成计划时使用的问题和条件。"""

    question_set_id: int

    task_id: int

    research_question: str

    conditions: ResearchConditions

    basis: ResearchQuestionCandidate

    sources: list[ComparisonSourceResponse]

    content: ResearchPlanContent

class ExperimentImportResponse(BaseModel):
    """返回一次导入的数据、原始文本与计划快照。"""

    model_config = ConfigDict(from_attributes=True)

    id: int

    question_set_id: int

    filename: str

    raw_csv: str

    plan_snapshot: ResearchPlanResponse

    rows: list[dict[str, str]]

    created_at: datetime

class ExperimentGroupStatistics(BaseModel):
    """同一方法、数据集和场景下的指标统计。"""

    method: str
    dataset: str
    scenario: str

    total_count: int
    valid_count: int
    failed_count: int
    missing_count: int
    invalid_count: int
    unknown_status_count: int

    mean: float | None
    stddev: float | None

class ExperimentStatisticsResponse(BaseModel):
    """返回指定实验导入记录的分组统计。"""

    import_id: int
    metric: str
    status_available: bool
    groups: list[ExperimentGroupStatistics]

class ExperimentAnalysisRequest(BaseModel):
    """指定需要分析的指标、数据集和场景。"""

    metrics: list[str] = Field(min_length=1)
    dataset: str = Field(min_length=1)
    scenario: str = Field(min_length=1)

class ExperimentAnalysisContent(BaseModel):
    """大模型生成的实验分析正文。"""

    summary: str = Field(min_length=1)
    differences: list[str] = Field(min_length=1)
    tradeoffs: list[str] = Field(min_length=1)
    limitations: list[str] = Field(min_length=1)
    next_experiments: list[str] = Field(min_length=1)

class ExperimentAnalysisResponse(BaseModel):
    """返回分析范围、统计依据和分析正文。"""

    import_id: int
    dataset: str
    scenario: str
    statistics: list[ExperimentStatisticsResponse]
    content: ExperimentAnalysisContent

class PaperDraftRequest(ExperimentAnalysisRequest):
    """选择实验分析范围，并补充实际研究情况。"""

    implementation_details: str = ""
    actual_experiment_setup: str = ""

class PaperDraftEvidence(ComparisonSourceResponse):
    """保留原资料编号，并附带论文原文。"""

    text: str

class PaperDraftMaterials(BaseModel):
    """提供给论文写作Agent的统一材料。"""

    plan: ResearchPlanResponse
    analysis: ExperimentAnalysisResponse
    evidence: list[PaperDraftEvidence]

    implementation_details: str
    actual_experiment_setup: str

    missing_information: list[str]

class PaperDraftParagraph(BaseModel):
    """论文中的一个段落及其文献证据编号。"""

    text: str = Field(min_length=1)
    source_numbers: list[int] = Field(default_factory=list)

class PaperDraftContent(BaseModel):
    """论文初稿的标题、摘要和六个正文部分。"""

    title: str = Field(min_length=1)
    abstract: str = Field(min_length=1)

    introduction: list[PaperDraftParagraph] = Field(min_length=1)
    related_work: list[PaperDraftParagraph] = Field(min_length=1)
    methods: list[PaperDraftParagraph] = Field(min_length=1)
    experiments: list[PaperDraftParagraph] = Field(min_length=1)
    discussion: list[PaperDraftParagraph] = Field(min_length=1)
    conclusion: list[PaperDraftParagraph] = Field(min_length=1)

class PaperDraftResponse(BaseModel):
    """返回初稿正文，以及正文实际引用的原文证据。"""

    import_id: int
    content: PaperDraftContent
    evidence: list[PaperDraftEvidence]
    missing_information: list[str]

class SavedPaperDraftResponse(PaperDraftResponse):
    """返回已保存的初稿及其数据库编号。"""

    id: int
    created_at: datetime
    writing_request: PaperDraftRequest

PaperDraftSectionName = Literal[
    "introduction",
    "related_work",
    "methods",
    "experiments",
    "discussion",
    "conclusion",
]

class PaperDraftExpandRequest(BaseModel):
    """指定需要扩写的章节和扩写要求。"""

    section: PaperDraftSectionName
    instruction: str = Field(min_length=1, max_length=2000)

class PaperDraftSectionContent(BaseModel):
    """模型生成的章节正文。"""

    paragraphs: list[PaperDraftParagraph] = Field(min_length=1)

class PaperDraftExpandResponse(PaperDraftSectionContent):
    """返回指定初稿的章节扩写建议，不代表已经保存。"""

    draft_id: int
    section: PaperDraftSectionName
