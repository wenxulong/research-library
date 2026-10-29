"""定义项目使用的 SQLAlchemy 数据库模型及关系。"""

from datetime import datetime
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    Index,
    JSON
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from research_library.db.database import Base

class Paper(Base):
    """保存论文基本信息及其关联关系。"""
    __tablename__ = 'papers'
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    author: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    notes: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
        default="",
    )
    task_links: Mapped[list["ResearchTaskPaper"]] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
    )
    document: Mapped["PaperDocument"] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
        uselist=False,
    )

class ResearchTask(Base):
    """保存科研任务的问题、状态和执行结果。"""
    __tablename__ = 'research_tasks'

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )
    result: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    review_result: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    paper_links: Mapped[list["ResearchTaskPaper"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    analysis_versions: Mapped[list["ResearchTaskAnalysisVersion"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    comparison_report: Mapped["ResearchTaskComparisonReport | None"] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        uselist=False,
    )

class ResearchTaskPaper(Base):
    """连接研究任务和论文，并保存关联备注。"""
    __tablename__ = 'research_task_papers'

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "paper_id",
            name="uq_research_task_paper",
        ),
    )
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey(
            "research_tasks.id",
            ondelete="CASCADE",),
        nullable=False,
    )
    paper_id: Mapped[int] = mapped_column(
        ForeignKey(
            "papers.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    note: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )

    task: Mapped["ResearchTask"] = relationship(
        back_populates="paper_links",
    )

    paper: Mapped["Paper"] = relationship(
        back_populates="task_links",
    )

class PaperDocument(Base):
    """保存论文 PDF 的文件元数据。"""
    __tablename__ = 'paper_documents'

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    paper_id: Mapped[int] = mapped_column(
        ForeignKey(
            "papers.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
    )

    original_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    stored_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
    )

    file_path: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
    )

    content_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    file_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    paper: Mapped["Paper"] = relationship(
        back_populates="document",
    )

    pages: Mapped[list["PaperPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="PaperPage.page_number",
    )

    chunks: Mapped[list["PaperChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by=lambda: (PaperChunk.page_number, PaperChunk.chunk_index),
    )

class PaperPage(Base):
    """保存 PDF 单页正文及字符数。"""
    __tablename__ = "paper_pages"

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "page_number",
            name="uq_document_page_number",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey(
            "paper_documents.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    page_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    char_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    document: Mapped["PaperDocument"] = relationship(
        back_populates="pages",
    )

class PaperChunk(Base):
    __tablename__ = "paper_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "page_number",
            "chunk_index",
            name="uq_document_page_number",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey("paper_documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    page_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    char_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    document: Mapped["PaperDocument"] = relationship(
        back_populates="chunks",
    )

class PaperChunkEmbedding(Base):
    """保存一个论文文本块的向量。"""

    __tablename__ = "paper_chunk_embeddings"

    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("paper_chunks.id", ondelete="CASCADE"),
        primary_key=True,
    )

    model_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    vector: Mapped[list[float]] = mapped_column(
        JSON,
        nullable=False,
    )

class ResearchTaskAnalysisVersion(Base):
    """记录一次采纳操作前后的分析，保留原审校结果。"""

    __tablename__ = "research_task_analysis_versions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    task_id: Mapped[int] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    previous_analysis: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    updated_analysis: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    previous_review: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    task: Mapped["ResearchTask"] = relationship(
        back_populates="analysis_versions",
    )

class ResearchTaskComparisonReport(Base):
    """保存研究任务最新的已审校对比报告。"""

    __tablename__ = "research_task_comparison_reports"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    task_id: Mapped[int] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    analysis_snapshot: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    checked_report_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    saved_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    task: Mapped["ResearchTask"] = relationship(
        back_populates="comparison_report",
    )

class ReportJob(Base):
    """记录一次对比报告生成作业的状态、输入和结果。"""

    __tablename__ = "report_jobs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    task_id: Mapped[int] = mapped_column(
        ForeignKey("research_tasks.id"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="queued",
    )

    stage: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="waiting",
    )

    analysis_snapshot: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    result: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

class PaperPreparationJob(Base):
    """记录一次论文准备后台作业。"""

    __tablename__ = "paper_preparation_jobs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    paper_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="queued",
    )

    stage: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="waiting",
    )

    result: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

class ResearchQuestionSet(Base):
    """保存一组候选问题及用户的选择结果。"""

    __tablename__ = "research_question_sets"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    task_id: Mapped[int] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    report_job_id: Mapped[int] = mapped_column(
        ForeignKey("report_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    candidates: Mapped[list[dict]] = mapped_column(
        JSON,
        nullable=False,
    )

    sources: Mapped[list[dict]] = mapped_column(
        JSON,
        nullable=False,
    )

    selected_candidate_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    selected_question: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

    plan: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

class ExperimentImport(Base):
    """保存一次实验结果导入及其对应的研究计划。"""

    __tablename__ = "experiment_imports"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    question_set_id: Mapped[int] = mapped_column(
        ForeignKey("research_question_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    raw_csv: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    plan_snapshot: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    rows: Mapped[list[dict]] = mapped_column(
        JSON,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

class PaperDraft(Base):
    """保存已生成的论文初稿及其写作材料。"""

    __tablename__ = "paper_drafts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    import_id: Mapped[int] = mapped_column(
        ForeignKey(
            "experiment_imports.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    draft_data: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    materials_snapshot: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

class ReportClaimInvestigation(Base):
    """保存一次查证候选结果及用户采用时间。"""

    __tablename__ = "report_claim_investigations"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    job_id: Mapped[int] = mapped_column(
        ForeignKey("report_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    claim_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    base_report: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    adopted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    result: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

class AgentMemory(Base):
    """保存用户级或研究任务级的长期记忆。"""

    __tablename__ = "agent_memories"

    __table_args__ = (
        Index(
            "ix_agent_memories_owner_task",
            "owner_id",
            "research_task_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    owner_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    research_task_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "research_tasks.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    memory_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    origin: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    source_type: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )

    source_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    embedding: Mapped[list[float] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    embedding_model: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )